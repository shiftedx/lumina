#!/usr/bin/env python3
"""Loopback-only, range-bounded original-media qualification proxy.

The required JSON config is private (0600) and contains only ``sshHost``,
``container`` and ``artifactID``.  It is never logged.  The generated manifest
and optional frozen source state are private too; neither belongs in git.
"""
from __future__ import annotations

import argparse
import atexit
import http.server
import json
import os
import queue
import re
import select
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


CHUNK_BYTES = 1 << 20
MAX_RESPONSE_BYTES = 64 << 20
MAX_RUN_BYTES = 256 << 20
MAX_ALLOWED_RUN_BYTES = 1 << 30
WORKERS = 4
IO_TIMEOUT_SECONDS = 15
STATE_VERSION = 1


METADATA_SCRIPT = r'''
import json, sqlite3, sys
from app.config import settings
from app.services.media_artifacts import artifact_file
from app.services.media_response import MediaFileResponse

artifact_id = sys.argv[1]
connection = sqlite3.connect('file:' + str(settings.data_dir / settings.database_filename) + '?mode=ro', uri=True)
row = connection.execute("""
    select r.path, a.relative_path, a.probe
    from media_artifacts a join storage_roots r on r.id = a.root_id where a.id = ?
""", (artifact_id,)).fetchone()
if row is None:
    raise SystemExit(2)
path = artifact_file(row[0], row[1])
stat = path.stat()
probe = json.loads(row[2] or '{}')
streams = [value for value in probe.get('streams', []) if isinstance(value, dict)]
video = next((value for value in streams if value.get('type') == 'video'), None)
audio = [value for value in streams if value.get('type') == 'audio' and isinstance(value.get('index'), int)]
if video is None or not audio:
    raise SystemExit(3)
chosen = next((value for value in audio if value.get('default') is True), audio[0] if len(audio) == 1 else None)
if chosen is None:
    raise SystemExit(4)
codec = str(video.get('codec') or '').lower()
width, height = video.get('width'), video.get('height')
if not codec or not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
    raise SystemExit(5)
transfer = str(video.get('color_transfer') or '').lower()
if transfer == 'smpte2084':
    hdr = 'pq'
elif transfer == 'arib-std-b67':
    hdr = 'hlg'
else:
    hdr = 'sdr'
selected_audio_codec = str(chosen.get('codec') or '').lower()
if not selected_audio_codec:
    raise SystemExit(6)
audio_codecs = [selected_audio_codec]
audio_codecs.extend(str(value.get('codec') or '').lower() for value in audio if value is not chosen)
response = MediaFileResponse(path, stat_result=stat)
print(json.dumps({
    'version': 1, 'artifactID': artifact_id, 'size': stat.st_size, 'mtimeNS': stat.st_mtime_ns,
    'device': stat.st_dev, 'inode': stat.st_ino,
    'etag': response.headers['etag'],
    'duration': probe.get('duration'), 'audioStreamIndex': chosen['index'],
    'expected': {'width': width, 'height': height, 'videoCodec': codec, 'hdr': hdr,
                 'sourceDolbyVisionProfile': video.get('dv_profile'), 'audioCodecs': audio_codecs,
                 'contentType': response.headers['content-type']},
}))
'''


READER_SCRIPT = r'''
import os, sqlite3, sys
from app.config import settings
from app.services.media_artifacts import artifact_file

artifact_id, size, mtime_ns, device, inode, maximum = sys.argv[1:]
size, mtime_ns, device, inode, maximum = map(int, (size, mtime_ns, device, inode, maximum))
connection = sqlite3.connect('file:' + str(settings.data_dir / settings.database_filename) + '?mode=ro', uri=True)
row = connection.execute("""
    select r.path, a.relative_path from media_artifacts a join storage_roots r on r.id = a.root_id where a.id = ?
""", (artifact_id,)).fetchone()
if row is None:
    raise SystemExit(2)
path = artifact_file(row[0], row[1])

def expected(stat):
    return (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino) == (size, mtime_ns, device, inode)

if not expected(path.stat()):
    raise SystemExit(3)
output = sys.stdout.buffer
output.write(b'READY\n')
output.flush()
for raw in sys.stdin.buffer:
    try:
        offset, length = map(int, raw.strip().split())
        if offset < 0 or length < 1 or length > maximum or offset + length > size:
            raise ValueError
    except ValueError:
        output.write(b'ERROR\n'); output.flush(); continue
    try:
        with path.open('rb') as source:
            before = os.fstat(source.fileno())
            if not expected(before) or not expected(path.stat()):
                output.write(b'STALE\n'); output.flush(); continue
            source.seek(offset)
            data = source.read(length)
            after = os.fstat(source.fileno())
            current = path.stat()
        if len(data) != length or not expected(after) or not expected(current):
            output.write(b'STALE\n'); output.flush(); continue
        output.write(f'OK {length}\n'.encode())
        output.write(data)
        output.flush()
    except OSError:
        output.write(b'ERROR\n'); output.flush()
'''


@dataclass(frozen=True)
class SourceIdentity:
    artifactID: str
    size: int
    mtimeNS: int
    device: int
    inode: int
    etag: str
    duration: float | None
    audioStreamIndex: int
    expected: dict[str, Any]
    version: int = STATE_VERSION

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> 'SourceIdentity':
        required = ('artifactID', 'size', 'mtimeNS', 'device', 'inode', 'etag', 'audioStreamIndex', 'expected')
        if value.get('version', STATE_VERSION) != STATE_VERSION or any(key not in value for key in required):
            raise ValueError('invalid private source state')
        identity = cls(**{key: value.get(key, STATE_VERSION if key == 'version' else None)
                          for key in cls.__dataclass_fields__})
        if (not identity.artifactID or identity.size <= 0 or identity.mtimeNS < 0 or identity.device < 0
                or identity.inode < 0 or not re.fullmatch(r'"[^"\\]+"', identity.etag)
                or identity.audioStreamIndex < 0 or not isinstance(identity.expected, dict)):
            raise ValueError('invalid private source state')
        expected = identity.expected
        if not (isinstance(expected.get('width'), int) and expected['width'] > 0
                and isinstance(expected.get('height'), int) and expected['height'] > 0
                and isinstance(expected.get('videoCodec'), str) and expected['videoCodec']
                and isinstance(expected.get('hdr'), str) and expected['hdr']
                and isinstance(expected.get('audioCodecs'), list) and expected['audioCodecs']):
            raise ValueError('invalid private source expectations')
        content_type = expected.get('contentType', 'application/octet-stream')
        if not isinstance(content_type, str) or not re.fullmatch(r'[\w.+-]+/[\w.+-]+', content_type, flags=re.ASCII):
            raise ValueError('invalid private source content type')
        return identity


@dataclass(frozen=True)
class PrivateConfig:
    sshHost: str
    container: str
    artifactID: str

    @classmethod
    def load(cls, path: Path) -> 'PrivateConfig':
        require_private(path)
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('private config must be an object')
        config = cls(**{key: value.get(key) for key in ('sshHost', 'container', 'artifactID')})
        if not all(isinstance(part, str) and 0 < len(part) <= 256 and not part.startswith('-')
                   for part in asdict(config).values()):
            raise ValueError('invalid private config')
        return config


def require_private(path: Path) -> None:
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('private file must exist with mode 0600')


def write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, json.dumps(value, separators=(',', ':')).encode())
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def ssh_command(config: PrivateConfig, script: str, *arguments: object) -> list[str]:
    remote = shlex.join(['docker', 'exec', '-i', config.container, 'python', '-u', '-c', script, *map(str, arguments)])
    return [
        'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5',
        '-o', 'ServerAliveCountMax=2', '--', config.sshHost, remote,
    ]


def read_metadata(config: PrivateConfig) -> SourceIdentity:
    result = subprocess.run(ssh_command(config, METADATA_SCRIPT, config.artifactID), capture_output=True,
                            check=True, timeout=IO_TIMEOUT_SECONDS)
    return SourceIdentity.from_mapping(json.loads(result.stdout))


def terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


CommandFactory = Callable[[SourceIdentity], list[str]]


class PersistentReader:
    def __init__(self, identity: SourceIdentity, command_factory: CommandFactory) -> None:
        self.identity = identity
        self.command_factory = command_factory
        self.process: subprocess.Popen[bytes] | None = None
        self.buffer = bytearray()
        self.start()

    def start(self) -> None:
        self.close()
        self.process = subprocess.Popen(self.command_factory(self.identity), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
        try:
            if self.readline() != b'READY\n':
                raise RuntimeError('reader did not bind source identity')
        except BaseException:
            self.close()
            raise

    def _read_some(self, limit: int) -> bytes:
        if self.process is None or self.process.stdout is None:
            return b''
        descriptor = self.process.stdout.fileno()
        if not select.select([descriptor], [], [], IO_TIMEOUT_SECONDS)[0]:
            raise TimeoutError('reader timed out')
        return os.read(descriptor, limit)

    def readline(self) -> bytes:
        while b'\n' not in self.buffer:
            if len(self.buffer) >= 128:
                raise RuntimeError('reader header exceeds bound')
            data = self._read_some(64 * 1024)
            if not data:
                raise RuntimeError('reader ended before header')
            self.buffer.extend(data)
        ending = self.buffer.index(b'\n') + 1
        line = bytes(self.buffer[:ending])
        del self.buffer[:ending]
        return line

    def _write(self, data: bytes) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError('reader is closed')
        view = memoryview(data)
        descriptor = self.process.stdin.fileno()
        while view:
            if not select.select([], [descriptor], [], IO_TIMEOUT_SECONDS)[1]:
                raise TimeoutError('reader command timed out')
            view = view[os.write(descriptor, view):]

    def begin(self, offset: int, length: int) -> None:
        self._write(f'{offset} {length}\n'.encode())
        if self.readline() != f'OK {length}\n'.encode():
            raise RuntimeError('reader rejected source range')

    def chunks(self, length: int):
        remaining = length
        while remaining:
            if self.buffer:
                data = bytes(self.buffer[:min(64 * 1024, remaining)])
                del self.buffer[:len(data)]
            else:
                data = self._read_some(min(64 * 1024, remaining))
            if not data:
                raise RuntimeError('reader ended before framed body')
            remaining -= len(data)
            yield data

    def drain(self, length: int) -> int:
        return sum(len(data) for data in self.chunks(length))

    def close(self) -> None:
        if self.process is None:
            return
        process, self.process = self.process, None
        try:
            terminate(process)
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
            self.buffer.clear()


class ReaderPool:
    def __init__(self, identity: SourceIdentity, command_factory: CommandFactory) -> None:
        self.available: queue.Queue[PersistentReader] = queue.Queue(maxsize=WORKERS)
        self.readers: list[PersistentReader] = []
        self.stopping = False
        self.lock = threading.Lock()
        try:
            for _ in range(WORKERS):
                reader = PersistentReader(identity, command_factory)
                self.readers.append(reader)
                self.available.put(reader)
        except BaseException:
            self.close()
            raise

    def acquire(self) -> PersistentReader:
        with self.lock:
            if self.stopping:
                raise RuntimeError('reader pool is stopping')
        return self.available.get(timeout=IO_TIMEOUT_SECONDS)

    def release(self, reader: PersistentReader, healthy: bool) -> None:
        with self.lock:
            if self.stopping:
                reader.close()
                return
            if not healthy:
                reader.close()
                try:
                    reader.start()
                except BaseException:
                    reader.close()
            if reader.process is not None:
                self.available.put(reader)

    def close(self) -> None:
        with self.lock:
            self.stopping = True
            readers, self.readers = self.readers, []
        for reader in readers:
            reader.close()


class RangeProxy(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, identity: SourceIdentity, command_factory: CommandFactory,
                 max_run_bytes: int | None = None, streaming_ranges: bool = False) -> None:
        self.max_run_bytes = MAX_RUN_BYTES if max_run_bytes is None else max_run_bytes
        if type(self.max_run_bytes) is not int or not 1 <= self.max_run_bytes <= MAX_ALLOWED_RUN_BYTES:
            raise ValueError('run byte budget must be between 1 byte and 1 GiB')
        self.identity = identity
        self.streaming_ranges = streaming_ranges
        self.route = '/' + uuid.uuid4().hex
        self.pool = ReaderPool(identity, command_factory)
        self.slots = threading.BoundedSemaphore(WORKERS)
        self.budget = 0
        self.budget_exhausted = False
        self.budget_lock = threading.Lock()
        self.serving = False
        try:
            super().__init__(('127.0.0.1', 0), RangeHandler)
        except BaseException:
            self.pool.close()
            raise

    def serve_forever(self, *arguments: object, **keywords: object) -> None:
        self.serving = True
        try:
            super().serve_forever(*arguments, **keywords)
        finally:
            self.serving = False

    def reserve(self, length: int) -> bool:
        with self.budget_lock:
            if self.budget + length > self.max_run_bytes:
                if not self.budget_exhausted:
                    self.budget_exhausted = True
                    print('real-media range proxy read budget exhausted', file=sys.stderr, flush=True)
                return False
            self.budget += length
            return True

    def close(self) -> None:
        if self.serving:
            self.shutdown()
        self.server_close()
        self.pool.close()


class RangeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_: object) -> None:
        pass

    def do_HEAD(self) -> None:  # noqa: N802
        self.serve(False)

    def do_GET(self) -> None:  # noqa: N802
        self.serve(True)

    @property
    def proxy(self) -> RangeProxy:
        return self.server  # type: ignore[return-value]

    def fail_range(self) -> None:
        self.send_response(416)
        self.send_header('Content-Range', f'bytes */{self.proxy.identity.size}')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def parse_range(self, body: bool) -> tuple[int, int] | None:
        header = self.headers.get('Range')
        if header is None:
            return (0, self.proxy.identity.size - 1) if not body or self.proxy.streaming_ranges else None
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', header)
        if not match:
            return None
        start = int(match[1])
        end = self.proxy.identity.size - 1 if not match[2] else min(int(match[2]), self.proxy.identity.size - 1)
        if (start > end or start >= self.proxy.identity.size
                or (end - start + 1 > MAX_RESPONSE_BYTES and not self.proxy.streaming_ranges)):
            return None
        return start, end

    @staticmethod
    def slices(start: int, end: int):
        while start <= end:
            length = min(CHUNK_BYTES, end - start + 1)
            yield start, length
            start += length

    def serve(self, body: bool) -> None:
        if self.path.split('?', 1)[0] != self.proxy.route:
            self.send_error(404)
            return
        parsed = self.parse_range(body)
        if parsed is None:
            self.fail_range()
            return
        start, end = parsed
        length = end - start + 1
        slot = False
        reader: PersistentReader | None = None
        if body:
            slot = self.proxy.slots.acquire(timeout=IO_TIMEOUT_SECONDS)
            first_reservation = min(CHUNK_BYTES, length) if self.proxy.streaming_ranges else length
            if not slot or not self.proxy.reserve(first_reservation):
                if slot:
                    self.proxy.slots.release()
                self.send_error(503)
                return
            try:
                reader = self.proxy.pool.acquire()
            except (queue.Empty, RuntimeError):
                self.proxy.slots.release()
                self.send_error(503)
                return
        try:
            self.send_response(206 if self.headers.get('Range') else 200)
            self.send_header('Content-Type', self.proxy.identity.expected.get('contentType', 'application/octet-stream'))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('ETag', self.proxy.identity.etag)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(length))
            self.send_header('Connection', 'close')
            if self.headers.get('Range'):
                self.send_header('Content-Range', f'bytes {start}-{end}/{self.proxy.identity.size}')
            self.end_headers()
            if not body:
                return
            healthy = True
            try:
                self.connection.settimeout(IO_TIMEOUT_SECONDS)
                for offset, chunk_length in self.slices(start, end):
                    if self.proxy.streaming_ranges and offset != start and not self.proxy.reserve(chunk_length):
                        break
                    reader.begin(offset, chunk_length)
                    consumed = 0
                    try:
                        for data in reader.chunks(chunk_length):
                            consumed += len(data)
                            self.wfile.write(data)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, socket.timeout):
                        reader.drain(chunk_length - consumed)
                        break
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            except BaseException:
                healthy = False
            finally:
                if reader is not None:
                    self.proxy.pool.release(reader, healthy)
        finally:
            if slot:
                self.proxy.slots.release()


def reader_factory(config: PrivateConfig) -> CommandFactory:
    def command(identity: SourceIdentity) -> list[str]:
        return ssh_command(config, READER_SCRIPT, identity.artifactID, identity.size, identity.mtimeNS,
                           identity.device, identity.inode, CHUNK_BYTES)
    return command


def manifest(identity: SourceIdentity, url: str) -> dict[str, Any]:
    duration = max(float(identity.duration or 1), 1)
    return {
        'alias': 'real-media-qualified', 'proxyURL': url, 'usesClientDemux': True, 'strictPair': False,
        'startSeconds': 0, 'deepSeekSeconds': max(1, duration / 2), 'nearEndSeconds': max(0, duration - 30),
        'sourceSize': identity.size, 'sourceValidator': identity.etag, 'audioStreamIndex': identity.audioStreamIndex,
        'expected': identity.expected,
    }


def load_identity(config: PrivateConfig, source_state: Path | None) -> SourceIdentity:
    if source_state is None:
        return read_metadata(config)
    require_private(source_state)
    identity = SourceIdentity.from_mapping(json.loads(source_state.read_text(encoding='utf-8')))
    if identity.artifactID != config.artifactID:
        raise ValueError('source state does not match private config')
    return identity


def start_proxy(identity: SourceIdentity, command_factory: CommandFactory, manifest_path: Path,
                freeze_path: Path | None = None, max_run_bytes: int | None = None,
                streaming_ranges: bool = False) -> RangeProxy:
    proxy = RangeProxy(identity, command_factory, max_run_bytes, streaming_ranges)
    try:
        if freeze_path:
            write_private(freeze_path, asdict(identity))
        write_private(manifest_path, manifest(identity, f'http://127.0.0.1:{proxy.server_port}{proxy.route}'))
        return proxy
    except BaseException:
        proxy.close()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--freeze', type=Path)
    parser.add_argument('--source-state', type=Path)
    parser.add_argument('--max-run-bytes', type=int, default=MAX_RUN_BYTES,
                        help='total reserved response bytes for this origin (default: 256 MiB; maximum: 1 GiB)')
    parser.add_argument('--streaming-ranges', action='store_true',
                        help='admit raw-player HTTP reads one bounded frame at a time, including large or absent ranges')
    args = parser.parse_args()
    if args.freeze and args.source_state:
        parser.error('--freeze and --source-state are mutually exclusive')
    if not 1 <= args.max_run_bytes <= MAX_ALLOWED_RUN_BYTES:
        parser.error('--max-run-bytes must be between 1 byte and 1 GiB')
    try:
        config = PrivateConfig.load(args.config)
        identity = load_identity(config, args.source_state)
        proxy = start_proxy(identity, reader_factory(config), args.manifest, args.freeze,
                            args.max_run_bytes, args.streaming_ranges)
        atexit.register(proxy.close)
    except Exception:
        print('real-media range proxy startup failed', file=sys.stderr)
        raise SystemExit(2) from None

    def request_shutdown(*_: object) -> None:
        threading.Thread(target=proxy.close, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, request_shutdown)
        signal.signal(signal.SIGTERM, request_shutdown)
    print('real-media range proxy ready', flush=True)
    try:
        proxy.serve_forever()
    finally:
        proxy.close()
        print(f'real-media range proxy reservedBytes={proxy.budget} maxRunBytes={proxy.max_run_bytes}',
              file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
