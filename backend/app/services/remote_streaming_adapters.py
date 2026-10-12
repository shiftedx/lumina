from __future__ import annotations

import functools
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, time
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from yt_dlp.networking import Request
from yt_dlp.networking._helper import make_ssl_context
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils.networking import std_headers

from app.db import SessionLocal
from app.services.network_policy import PolicyYoutubeDL, PublicSourcePolicy, create_public_connection
from app.services.playback_log import log_playback
from app.services.remote_streaming import (
    ByteRange,
    HlsAsset,
    HlsPresentation,
    MediaTrack,
    PreparationSupersededError,
    UnsupportedPlaybackError,
    UpstreamTrackExpiredError,
    UpstreamMediaResponse,
)
from app.services.yt_dlp_service import YtDlpService

try:  # POSIX advisory locks.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised through the Windows adapter test.
    _fcntl = None

try:  # Windows byte-range locks.
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - the POSIX test runtime has no msvcrt.
    _msvcrt = None


def _try_lock_file(file) -> bool:  # noqa: ANN001
    if _fcntl is not None:
        try:
            _fcntl.flock(file.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
    if _msvcrt is None:
        raise RuntimeError("This platform does not provide a supported file-lock API.")
    file.seek(0, 2)
    if file.tell() == 0:
        file.write(b"\0")
        file.flush()
    file.seek(0)
    try:
        _msvcrt.locking(file.fileno(), _msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _unlock_file(file) -> None:  # noqa: ANN001
    if _fcntl is not None:
        _fcntl.flock(file.fileno(), _fcntl.LOCK_UN)
        return
    if _msvcrt is None:
        raise RuntimeError("This platform does not provide a supported file-lock API.")
    file.seek(0)
    _msvcrt.locking(file.fileno(), _msvcrt.LK_UNLCK, 1)


class YtDlpPlaybackResolver:
    """Re-resolve private upstream tracks without crossing the API boundary."""

    def resolve(self, source_url: str, owner_user_id: str) -> dict:
        with SessionLocal() as db:
            return YtDlpService(db).resolve_remote_playback(source_url)


def _open_public_url(
    policy: PublicSourcePolicy,
    url: str,
    *,
    headers: dict[str, str],
    byte_range: ByteRange | None,
    timeout_seconds: float | None,
    request_timeout_seconds: float,
    chunk_size: int,
    data: bytes | None = None,
) -> UpstreamMediaResponse:
    """Open one policy-validated URL through yt-dlp's public-only networking (a POST when ``data`` is given).

    The public-source policy is revalidated here and again inside the yt-dlp
    handler at connection time (DNS re-resolution and redirect revalidation),
    so no private destination is reachable even under DNS rebinding.
    """

    policy.validate_url(url)
    request_headers = dict(headers)
    if byte_range is not None:
        if byte_range.start is None:
            request_headers["Range"] = f"bytes=-{byte_range.end}"
        else:
            suffix = "" if byte_range.end is None else str(byte_range.end)
            request_headers["Range"] = f"bytes={byte_range.start}-{suffix}"
    ydl = PolicyYoutubeDL({"ignoreconfig": True, "quiet": True, "proxy": ""}, policy=policy)
    try:
        request_timeout = min(request_timeout_seconds, timeout_seconds) if timeout_seconds is not None else request_timeout_seconds
        response = ydl.urlopen(Request(url, data=data, headers=request_headers, extensions={"timeout": max(0.1, request_timeout)}))
    except HTTPError as exc:
        response = exc.response
    except BaseException:
        ydl.close()
        raise
    try:
        status_code = int(response.status)
        response_headers = {str(key): str(value) for key, value in response.headers.items()}
    except BaseException:
        try:
            response.close()
        finally:
            ydl.close()
        raise

    def close() -> None:
        try:
            response.close()
        finally:
            ydl.close()

    def chunks() -> Iterator[bytes]:
        while chunk := response.read(chunk_size):
            yield chunk

    return UpstreamMediaResponse(status_code=status_code, headers=response_headers, body=chunks(), close=close)


class PublicMediaReader:
    """Stream a resolved track through yt-dlp's public-only networking handler."""

    chunk_size = 64 * 1024
    # googlevideo paces each request (3-25 Mb/s while other connections ran at 100+), so a bounded span (a DASH
    # segment: a 21 MB 4K one took 5-8 s one chunk at a time) reads this many chunks at once, in order, each at least
    # piece_bytes so a segment costs about as many upstream requests as before. Longer or open spans (a progressive
    # file) stay sequential: their start needs only the first bytes, and read-ahead would buffer whole chunks.
    # A read-ahead chunk is buffered whole, at most parallel_pieces x the track's chunk size per response.
    # 2.5.0 shipped 4: in production googlevideo answered parallel pieces with 403s (read as expired tracks), which
    # re-resolved the stream and dropped quality mid-play. One piece at a time is 2.4.0's request pattern.
    parallel_pieces = 1
    piece_bytes = 4 * 1024 * 1024
    parallel_span_bytes = 64 * 1024 * 1024

    def __init__(self, policy: PublicSourcePolicy | None = None, request_timeout_seconds: float = 30) -> None:
        self._policy = policy or PublicSourcePolicy()
        self._request_timeout_seconds = request_timeout_seconds

    def open(self, track: MediaTrack, byte_range: ByteRange | None, timeout_seconds: float | None = None) -> UpstreamMediaResponse:
        step = track.chunk_size
        if not step or (byte_range is not None and byte_range.start is None):
            # Suffix ranges stay one request (browsers rarely send them); chunk them if they get throttled.
            return self._open_range(track, byte_range, timeout_seconds)
        end = byte_range.end if byte_range is not None and byte_range.end is not None else None
        if end is None and track.content_length:
            end = track.content_length - 1
        span = None if end is None else end - (byte_range.start if byte_range is not None else 0) + 1
        if span is None or span > self.parallel_span_bytes:
            return self._open_chunked(track, byte_range, timeout_seconds, step, 1)
        piece = min(step, max(self.piece_bytes, -(-span // self.parallel_pieces)))
        return self._open_chunked(track, byte_range, timeout_seconds, piece, self.parallel_pieces)

    def _open_range(self, track: MediaTrack, byte_range: ByteRange | None, timeout_seconds: float | None) -> UpstreamMediaResponse:
        return _open_public_url(
            self._policy,
            track.url,
            headers=dict(track.headers),
            byte_range=byte_range,
            timeout_seconds=timeout_seconds,
            request_timeout_seconds=self._request_timeout_seconds,
            chunk_size=self.chunk_size,
        )

    def _open_chunked(
        self, track: MediaTrack, byte_range: ByteRange | None, timeout_seconds: float | None, step: int, parallel: int
    ) -> UpstreamMediaResponse:
        """Serve one span as sequential ranged sub-requests of at most ``step`` bytes.

        Callers see the same response shape as a single request: 200 for a
        whole-track read, 206 with the combined Content-Range for a range read.
        """

        deadline = None if timeout_seconds is None else monotonic() + timeout_seconds
        start = 0 if byte_range is None else byte_range.start
        end = byte_range.end if byte_range is not None else None
        if end is None and track.content_length is not None:
            end = track.content_length - 1
        first_end = start + step - 1 if end is None else min(end, start + step - 1)
        first = self._open_range(track, ByteRange(start, first_end), timeout_seconds)
        if first.status_code != 206:
            return first  # Refused (4xx/416) or range ignored: callers handle it exactly as today.
        first_start, total = _content_range(first)
        if first_start != start or total is None:
            # No usable total, fall back to one unranged request (throttled but correct).
            first.close()
            return self._open_range(track, byte_range, timeout_seconds)
        # A full read trusts the upstream total over yt-dlp's filesize hint.
        end = total - 1 if byte_range is None or byte_range.end is None else min(byte_range.end, total - 1)
        headers = {
            key: value for key, value in first.headers.items() if key.lower() not in {"content-range", "content-length"}
        }
        headers["Content-Length"] = str(end - start + 1)
        if byte_range is not None:
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        pieces = [ByteRange(position, min(end, position + step - 1)) for position in range(min(first_end, end) + 1, end + 1, step)]
        pool = ThreadPoolExecutor(max_workers=parallel) if pieces else None

        def read_piece(piece: ByteRange) -> bytes:
            remaining = None if deadline is None else deadline - monotonic()
            if remaining is not None and remaining <= 0:
                raise UnsupportedPlaybackError("The upstream media read exceeded its deadline.")
            response = self._open_range(track, piece, remaining)
            try:
                if response.status_code in {401, 403, 404, 410}:
                    raise UpstreamTrackExpiredError("An upstream track expired during a ranged read.")
                if response.status_code != 206 or _content_range(response)[0] != piece.start:
                    raise UnsupportedPlaybackError("The upstream source did not honor a media range.")
                data = b"".join(response.body)
            finally:
                response.close()
            if len(data) != piece.end - piece.start + 1:
                raise UnsupportedPlaybackError("An upstream media range ended early.")
            return data

        def body() -> Iterator[bytes]:
            # The first chunk streams as it arrives while the next ones are already reading.
            ahead = [pool.submit(read_piece, piece) for piece in pieces[:parallel]] if pool else []
            received = 0
            for chunk in first.body:
                received += len(chunk)
                yield chunk
            first.close()
            if received != min(first_end, end) - start + 1:
                raise UnsupportedPlaybackError("An upstream media range ended early.")
            for index in range(len(pieces)):
                data = ahead[index].result()
                if index + parallel < len(pieces):
                    ahead.append(pool.submit(read_piece, pieces[index + parallel]))
                for offset in range(0, len(data), self.chunk_size):
                    yield data[offset : offset + self.chunk_size]

        def close() -> None:
            first.close()
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)

        return UpstreamMediaResponse(
            status_code=200 if byte_range is None else 206,
            headers=headers,
            body=body(),
            close=close,
        )


def _content_range(response: UpstreamMediaResponse) -> tuple[int | None, int | None]:
    """Return (start, total) from a ``bytes start-end/total`` Content-Range, or Nones."""

    value = next((v for k, v in response.headers.items() if k.lower() == "content-range"), "")
    try:
        interval, total = value.removeprefix("bytes ").split("/", 1)
        return int(interval.split("-", 1)[0]), (None if total == "*" else int(total))
    except ValueError:
        return None, None


class PublicRelayFetcher:
    """Fetch a policy-validated HLS relay address through public-only networking.

    Upstream request headers resolved for the source are applied here,
    server-side only, and never surface to the browser.

    Connections are kept alive per origin: a live player asks for a playlist and a segment every couple of seconds,
    and a fresh yt-dlp client plus TLS handshake per request cost ~170 ms each (Twitch playlist 213 ms vs 43 ms
    kept alive, segment first byte ~200 ms vs ~10 ms). Every new socket still connects only to an address the
    policy resolved as public, and every address (redirects included) is revalidated before it is requested.
    """

    chunk_size = 64 * 1024
    max_idle_per_origin = 6
    max_redirects = 5

    def __init__(self, policy: PublicSourcePolicy | None = None, request_timeout_seconds: float = 30) -> None:
        self._policy = policy or PublicSourcePolicy()
        self._request_timeout_seconds = request_timeout_seconds
        self._idle: dict[tuple[str, str, int], list[http.client.HTTPConnection]] = {}
        self._idle_lock = Lock()
        self._ssl_context: ssl.SSLContext | None = None

    def fetch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        byte_range: ByteRange | None = None,
        timeout_seconds: float | None = None,
    ) -> UpstreamMediaResponse:
        timeout = max(0.1, min(self._request_timeout_seconds, timeout_seconds) if timeout_seconds is not None else self._request_timeout_seconds)
        request_headers = {**std_headers, **(headers or {}), "Accept-Encoding": "identity"}  # bodies pass through undecoded
        if byte_range is not None:
            if byte_range.start is None:
                request_headers["Range"] = f"bytes=-{byte_range.end}"
            else:
                request_headers["Range"] = f"bytes={byte_range.start}-{'' if byte_range.end is None else byte_range.end}"
        for _redirect in range(self.max_redirects + 1):
            self._policy.validate_url(url)
            origin, target = self._origin(url)
            connection, response = self._request(origin, target, request_headers, timeout)
            location = response.getheader("Location")
            if response.status in {301, 302, 303, 307, 308} and location:
                response.read()
                self._finish(origin, connection, response)
                url = urljoin(url, location)
                continue
            return self._wrap(origin, connection, response)
        raise UnsupportedPlaybackError("The upstream source redirected too many times.")

    def preconnect(self, url: str) -> None:
        """Open (and pool) a connection to ``url``'s origin ahead of its first request, unless one is idle already."""
        self._policy.validate_url(url)
        origin, _target = self._origin(url)
        with self._idle_lock:
            if self._idle.get(origin):
                return
        connection = self._connect(origin, self._request_timeout_seconds)
        connection.connect()
        with self._idle_lock:
            pooled = self._idle.setdefault(origin, [])
            if len(pooled) < self.max_idle_per_origin:
                pooled.append(connection)
                return
        connection.close()

    @staticmethod
    def _origin(url: str) -> tuple[tuple[str, str, int], str]:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        origin = (scheme, parts.hostname or "", parts.port or (443 if scheme == "https" else 80))
        return origin, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

    def _request(self, origin, target, headers, timeout):  # noqa: ANN001, ANN202
        with self._idle_lock:
            pooled = self._idle.get(origin) or []
            connection = pooled.pop() if pooled else None
        if connection is not None:
            try:
                connection.sock.settimeout(timeout)
                connection.request("GET", target, headers=headers)
                return connection, connection.getresponse()
            except (OSError, http.client.HTTPException):
                connection.close()  # the origin closed an idle keep-alive: retry once on a new socket
        connection = self._connect(origin, timeout)
        try:
            connection.request("GET", target, headers=headers)
            return connection, connection.getresponse()
        except BaseException:
            connection.close()
            raise

    def _connect(self, origin, timeout) -> http.client.HTTPConnection:  # noqa: ANN001
        scheme, host, port = origin
        if scheme == "https":
            if self._ssl_context is None:
                self._ssl_context = make_ssl_context()
            connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=self._ssl_context)
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
        # The only way a socket opens: to an address the policy resolved as public, re-resolved here (no rebinding).
        connection._create_connection = functools.partial(create_public_connection, self._policy)  # noqa: SLF001
        return connection

    def _finish(self, origin, connection, response) -> None:  # noqa: ANN001
        """Pool the connection when its response was read to the end and the origin keeps it open; else close it."""
        if response.isclosed() and not response.will_close and connection.sock is not None:
            with self._idle_lock:
                pooled = self._idle.setdefault(origin, [])
                if len(pooled) < self.max_idle_per_origin:
                    pooled.append(connection)
                    return
        response.close()
        connection.close()

    def _wrap(self, origin, connection, response) -> UpstreamMediaResponse:  # noqa: ANN001
        closed = False

        def close() -> None:
            nonlocal closed
            if not closed:
                closed = True
                self._finish(origin, connection, response)

        def chunks() -> Iterator[bytes]:
            while chunk := response.read(self.chunk_size):
                yield chunk

        return UpstreamMediaResponse(
            status_code=response.status,
            headers={str(key): str(value) for key, value in response.getheaders()},
            body=chunks(),
            close=close,
        )


# A playlist is returned once it lists this many segments (or FFmpeg already finished).
_READY_SEGMENTS = 2
_PACKAGE_ASSET = re.compile(r"init\.mp4|segment-\d{5}\.m4s")


class FfmpegHlsPackager:
    """Incremental HLS packaging for split AVC tracks no larger than 1080p.

    Providers offer 1440p/2160p only as AV1/VP9 DASH tracks; those play through
    the DASH range relay, which transfers just the segments the player buffers.

    Both tracks stream through pipes into FFmpeg while they download, so the
    first playlist is ready after its first segments, not after whole tracks,
    and the inputs never touch disk. Pipes need fragmented (DASH-style) MP4
    inputs, as YouTube's split tracks are; a moov-at-end input fails closed.
    """

    def __init__(
        self,
        *,
        reader: PublicMediaReader,
        temp_root: Path,
        ffmpeg_path: str | None = None,
        timeout_seconds: float = 60,
        max_total_bytes: int = 2 * 1024 * 1024 * 1024,
        clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], float] = time,
        orphan_ttl_seconds: float = 24 * 60 * 60,
        process_token_factory: Callable[[], str] | None = None,
    ) -> None:
        if orphan_ttl_seconds <= 0:
            raise ValueError("The abandoned HLS package TTL must be positive.")
        self._reader = reader
        self._base_root = temp_root / "remote-streams"
        self._lease_root = self._base_root / ".leases"
        self._reclaim_abandoned_roots(wall_clock(), orphan_ttl_seconds)
        process_token = (process_token_factory or (lambda: secrets.token_hex(16)))()
        if not process_token or not process_token.isalnum():
            raise ValueError("The HLS process token must be non-empty and alphanumeric.")
        self._root = self._base_root / process_token
        self._lease_path = self._lease_root / f"{process_token}.lock"
        self._lease_file = None
        self._ffmpeg_path = ffmpeg_path
        self._timeout_seconds = timeout_seconds
        self._max_total_bytes = max_total_bytes
        self._clock = clock
        self._lock = Lock()
        self._stream_dirs: dict[str, set[Path]] = {}
        self._cancels: dict[Path, Event] = {}

    def can_package(self, video: MediaTrack, audio: MediaTrack) -> bool:
        """Return whether the remux fits its byte budget.

        Inputs stream through pipes, so the HLS output (about one copy of the
        inputs) is the peak disk use. Unknown sizes remain eligible and are
        bounded during preparation.
        """

        # The pass_fds pipes are POSIX-only; Windows hosts fall back to progressive/DASH renditions.
        if os.name != "posix" or video.height is None or not 0 < video.height <= 1080:
            return False
        video_size = video.content_length or video.estimated_content_length
        audio_size = audio.content_length or audio.estimated_content_length
        if video_size is None or audio_size is None:
            return True
        return video_size + audio_size <= self._max_total_bytes

    def _reclaim_abandoned_roots(self, now: float, orphan_ttl_seconds: float) -> None:
        if not self._base_root.is_dir():
            return
        cutoff = now - orphan_ttl_seconds
        for candidate in self._base_root.iterdir():
            if candidate == self._lease_root:
                continue
            lease = None
            lease_path = self._lease_root / f"{candidate.name}.lock"
            locked = False
            try:
                if not candidate.is_dir() or candidate.stat().st_mtime > cutoff:
                    continue
                self._lease_root.mkdir(parents=True, exist_ok=True)
                lease = lease_path.open("a+b")
                if not _try_lock_file(lease):
                    lease.close()
                    lease = None
                    continue
                locked = True
                shutil.rmtree(candidate)
            except OSError:
                continue
            finally:
                if lease is not None:
                    try:
                        if locked:
                            _unlock_file(lease)
                    finally:
                        lease.close()
                    if locked:
                        lease_path.unlink(missing_ok=True)
    def _ensure_process_root_locked(self) -> None:
        if self._lease_file is not None:
            return
        self._root.mkdir(parents=True, exist_ok=True)
        self._lease_root.mkdir(parents=True, exist_ok=True)
        lease = self._lease_path.open("a+b")
        try:
            if not _try_lock_file(lease):
                raise RuntimeError("The HLS process root is already owned by another process.")
        except BaseException:
            lease.close()
            raise
        self._lease_file = lease

    def _release_process_root_if_unused(self) -> None:
        with self._lock:
            if self._stream_dirs or self._lease_file is None:
                return
            lease = self._lease_file
            self._lease_file = None
            try:
                shutil.rmtree(self._root, ignore_errors=True)
            finally:
                try:
                    _unlock_file(lease)
                finally:
                    lease.close()
            self._lease_path.unlink(missing_ok=True)

    def prepare(self, stream_id: str, generation: int, video: MediaTrack, audio: MediaTrack) -> HlsPresentation:
        """Start packaging and return as soon as the first segments exist.

        The returned playlist grows (EVENT) until FFmpeg ends it with ENDLIST.
        ``done`` resolves when the upstream downloads stop; ``timeout_seconds``
        bounds how long the package may go without reading bytes or adding a
        segment.
        """
        if video.height is not None and video.height > 1080:
            raise UnsupportedPlaybackError(
                "High-resolution split playback uses the DASH range relay, not the HLS packager."
            )
        ffmpeg = self._ffmpeg_path or shutil.which("ffmpeg")
        if not ffmpeg:
            raise UnsupportedPlaybackError("Split playback requires FFmpeg on this Lumina server.")
        known_total = sum(track.content_length or 0 for track in (video, audio))
        if all(track.content_length is not None for track in (video, audio)) and known_total > self._max_total_bytes:
            raise UnsupportedPlaybackError("This source exceeds the aggregate budget for split playback.")
        identity = hashlib.sha256(stream_id.encode()).hexdigest()[:32]
        target = self._root / identity / str(generation)
        with self._lock:
            self._ensure_process_root_locked()
            target.mkdir(parents=True, exist_ok=True)
            self._stream_dirs.setdefault(stream_id, set()).add(target)
            cancel = self._cancels[target] = Event()
        manifest_path = target / "manifest.m3u8"
        pipes = [os.pipe(), os.pipe()]
        try:
            process = subprocess.Popen(
                _pipe_command(ffmpeg, pipes[0][0], pipes[1][0], target, manifest_path),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(pipes[0][0], pipes[1][0]),
            )
        except (OSError, ValueError) as exc:
            for read_end, write_end in pipes:
                os.close(read_end)
                os.close(write_end)
            with self._lock:
                self._cancels.pop(target, None)
            self._close_generation(stream_id, target)
            raise UnsupportedPlaybackError("Lumina could not prepare split audio and video for playback.") from exc
        used, budget_lock = [0], Lock()
        downloads: list[Future[None]] = [Future(), Future()]
        for track, (read_end, write_end), result in zip((video, audio), pipes, downloads):
            os.close(read_end)  # FFmpeg holds its own copy
            Thread(target=self._download, args=(track, write_end, used, budget_lock, cancel, result), daemon=True).start()
        ready, done = Event(), Future()
        failure: list[UnsupportedPlaybackError] = []
        started = monotonic()

        def supervise() -> None:
            segments, seen_bytes, last_progress = 0, 0, self._clock()
            try:
                while True:
                    if cancel.is_set():
                        raise PreparationSupersededError("A newer request replaced this split playback preparation.")
                    for result in downloads:
                        if result.done() and result.exception() is not None:
                            raise result.exception()
                    downloaded = all(result.done() for result in downloads)
                    if downloaded and not done.done():
                        done.set_result(None)
                    exited = process.poll() is not None
                    try:
                        manifest = manifest_path.read_bytes()
                    except FileNotFoundError:
                        manifest = b""
                    count = manifest.count(b"#EXTINF")
                    if count != segments and _tree_size(target) > self._max_total_bytes:
                        raise UnsupportedPlaybackError("This source exceeds the aggregate budget for split playback.")
                    if count != segments or used[0] != seen_bytes:
                        segments, seen_bytes, last_progress = count, used[0], self._clock()
                    if exited:
                        if not downloaded or b"#EXT-X-ENDLIST" not in manifest:
                            raise UnsupportedPlaybackError("Lumina could not prepare split audio and video for playback.")
                        _log_package("complete", stream_id, video, audio, started, segments)
                        return
                    if count >= _READY_SEGMENTS:
                        ready.set()
                    if self._clock() - last_progress > self._timeout_seconds:
                        raise UnsupportedPlaybackError("Split playback preparation stopped making progress.")
                    cancel.wait(0.1)
            except BaseException as exc:  # noqa: BLE001 - raised to the waiting request, or logged once playing
                if not isinstance(exc, UnsupportedPlaybackError):
                    exc = UnsupportedPlaybackError("Lumina could not prepare split audio and video for playback.")
                failure.append(exc)
                cancel.set()  # stops both downloads
                if ready.is_set():
                    _log_package("failed", stream_id, video, audio, started, segments, exc)
                self._close_generation(stream_id, target)
            finally:
                _terminate(process)
                with self._lock:
                    self._cancels.pop(target, None)
                if not done.done():
                    done.set_result(None)
                ready.set()

        Thread(target=supervise, daemon=True).start()
        ready.wait()
        if failure:
            raise failure[0]
        return HlsPresentation(manifest_path, _PackageAssets(target), done)

    def close(self, stream_id: str) -> None:
        with self._lock:
            directories = self._stream_dirs.pop(stream_id, set())
            for directory in directories:
                self._cancel_locked(directory)
        for directory in directories:
            shutil.rmtree(directory, ignore_errors=True)
            _remove_empty_parents(directory.parent, self._root)
        self._release_process_root_if_unused()

    def close_generation(self, stream_id: str, generation: int) -> None:
        with self._lock:
            directories = self._stream_dirs.get(stream_id, set())
            targets = {path for path in directories if path.name == str(generation)}
        for target in targets:
            self._close_generation(stream_id, target)

    def _cancel_locked(self, target: Path) -> None:
        """Signal an in-flight preparation of ``target`` to stop its reads and FFmpeg."""
        cancel = self._cancels.get(target)
        if cancel is not None:
            cancel.set()

    def _close_generation(self, stream_id: str, target: Path) -> None:
        with self._lock:
            self._cancel_locked(target)
            directories = self._stream_dirs.get(stream_id)
            if directories is not None:
                directories.discard(target)
                if not directories:
                    self._stream_dirs.pop(stream_id, None)
        shutil.rmtree(target, ignore_errors=True)
        _remove_empty_parents(target.parent, self._root)
        self._release_process_root_if_unused()

    def _download(
        self, track: MediaTrack, pipe: int, used: list[int], budget_lock: Lock, cancel: Event, result: Future[None]
    ) -> None:
        """Stream one track into FFmpeg's pipe within the shared byte budget; closing the pipe is its EOF."""
        output = os.fdopen(pipe, "wb", buffering=0)
        try:
            response = self._reader.open(track, None)
            try:
                if response.status_code in {401, 403, 404, 410}:
                    raise UpstreamTrackExpiredError(f"An upstream track expired while preparing playback (HTTP {response.status_code}).")
                if response.status_code >= 400:
                    raise UnsupportedPlaybackError(f"An upstream track was unavailable while preparing playback (HTTP {response.status_code}).")
                for chunk in response.body:
                    if cancel.is_set():
                        raise PreparationSupersededError("A newer request replaced this split playback preparation.")
                    with budget_lock:
                        used[0] += len(chunk)
                        over_budget = used[0] > self._max_total_bytes
                    if over_budget:
                        raise UnsupportedPlaybackError("This source exceeds the aggregate budget for split playback.")
                    output.write(chunk)  # blocks while FFmpeg is busy: FFmpeg paces the download
            finally:
                response.close()
        except BaseException as exc:  # noqa: BLE001 - handed to the supervisor (a killed FFmpeg shows up as a broken pipe)
            result.set_exception(exc)
        else:
            result.set_result(None)
        finally:
            try:
                output.close()
            except OSError:
                pass


class _PackageAssets(Mapping[str, HlsAsset]):
    """A growing package's served files: only FFmpeg's own init/segment names inside its directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def __getitem__(self, name: str) -> HlsAsset:
        path = self._root / name
        if not _PACKAGE_ASSET.fullmatch(name) or not path.is_file():
            raise KeyError(name)
        return HlsAsset(path, _hls_content_type(path))

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(path.name for path in self._root.glob("*") if _PACKAGE_ASSET.fullmatch(path.name)))

    def __len__(self) -> int:
        return sum(1 for _ in self)


def _pipe_command(ffmpeg: str, video_pipe: int, audio_pipe: int, target: Path, manifest_path: Path) -> list[str]:
    return [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", f"pipe:{video_pipe}", "-i", f"pipe:{audio_pipe}",
        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
        "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "event", "-hls_flags", "temp_file",
        "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(target / "segment-%05d.m4s"),
        str(manifest_path),
    ]


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()


def _tree_size(root: Path) -> int:
    total = 0
    for path in root.iterdir():
        try:
            total += path.stat().st_size
        except FileNotFoundError:  # a temp segment renamed mid-scan
            continue
    return total


def _log_package(outcome: str, stream_id: str, video: MediaTrack, audio: MediaTrack, started: float, segments: int, error: BaseException | None = None) -> None:
    if isinstance(error, PreparationSupersededError):
        outcome = "superseded"
    log_playback(
        "remote_stream.package", outcome, stream=stream_id[:8], video_format=video.format_id, audio_format=audio.format_id,
        segments=segments, duration_ms=round((monotonic() - started) * 1000), error=f'"{error}"' if error else None,
    )


def _hls_content_type(path: Path) -> str:
    if path.suffix == ".mp4":
        return "video/mp4"
    if path.suffix == ".m4s":
        return "video/iso.segment"
    return "application/octet-stream"


def _remove_empty_parents(path: Path, stop: Path) -> None:
    while path != stop.parent:
        try:
            path.rmdir()
        except OSError:
            return
        if path == stop:
            return
        path = path.parent


class _ChatSourceGone(Exception):
    """The chat page answered 401/403/404/410: chat ended or the source went private."""


_YOUTUBE_VIDEO_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/live/|/embed/|/shorts/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_LIVE_CHAT_PAGE = "https://www.youtube.com/live_chat?is_popout=1&v={}"
_LIVE_CHAT_API = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?prettyPrint=false&continuation="
_CHAT_CONTINUATION = re.compile(r'"(?:invalidation|timed|reload)ContinuationData":\{[^{}]*?"continuation":"([^"]+)"')
_CHAT_CLIENT_VERSION = re.compile(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"')
_CHAT_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36", "Accept-Language": "en"}


class YtDlpLiveChatFetcher:
    """Provider seam for a currently-live YouTube source's current chat.

    Bootstrap resolves the source through the same public-only network policy as
    every other extraction and reports whether the source exposes current chat.
    Page fetches flow through the guarded transport server-side; the browser
    never sees the innertube continuation, request headers, or cookies.

    Best-effort: when a live continuation cannot be resolved (a private source,
    chat disabled, or an extractor shape Lumina does not yet parse), bootstrap
    reports ``unavailable`` and the viewing session shows an honest "current
    chat unavailable" state while media keeps playing.
    """

    _MAX_PAGE_BYTES = 4 * 1024 * 1024

    def __init__(self, policy: PublicSourcePolicy | None = None, request_timeout_seconds: float = 20) -> None:
        self._policy = policy or PublicSourcePolicy()
        self._request_timeout_seconds = request_timeout_seconds

    def _read(self, url: str, *, headers: dict[str, str], data: bytes | None) -> bytes:
        response = _open_public_url(
            self._policy,
            url,
            headers=headers,
            byte_range=None,
            timeout_seconds=self._request_timeout_seconds,
            request_timeout_seconds=self._request_timeout_seconds,
            chunk_size=64 * 1024,
            data=data,
        )
        try:
            if response.status_code in {401, 403, 404, 410}:
                raise _ChatSourceGone()
            if response.status_code != 200:
                raise UnsupportedPlaybackError("The live chat source did not answer.")
            collected = bytearray()
            for chunk in response.body:
                collected.extend(chunk)
                if len(collected) > self._MAX_PAGE_BYTES:
                    raise UnsupportedPlaybackError("A live chat page exceeded its byte budget.")
        finally:
            response.close()
        return bytes(collected)

    def bootstrap(self, source_url, owner_user_id):  # noqa: ANN001
        # Imported lazily so the adapter module stays importable without the
        # live-chat service on the import path during unrelated tooling.
        from app.services.live_chat_seam import LiveChatBootstrap

        unavailable = LiveChatBootstrap(continuation=None, headers={}, status="unavailable")
        match = _YOUTUBE_VIDEO_ID.search(source_url or "")
        if match is None:
            return unavailable
        # The chat popout page carries the live-edge continuation and the web client version that
        # get_live_chat expects (yt-dlp's live_chat "subtitle" is only the watch page address).
        try:
            page = self._read(_LIVE_CHAT_PAGE.format(match.group(1)), headers=_CHAT_BROWSER_HEADERS, data=None)
        except Exception:
            return unavailable
        text = page.decode("utf-8", errors="replace")
        continuation = _CHAT_CONTINUATION.search(text)
        version = _CHAT_CLIENT_VERSION.search(text)
        if continuation is None or version is None:
            return unavailable
        headers = {**_CHAT_BROWSER_HEADERS, "Content-Type": "application/json", "X-YouTube-Client-Name": "1", "X-YouTube-Client-Version": version.group(1)}
        return LiveChatBootstrap(continuation=_LIVE_CHAT_API + quote(continuation.group(1), safe=""), headers=headers, status="available")

    def fetch_page(self, continuation, *, headers):  # noqa: ANN001
        from app.services.live_chat_seam import LiveChatPage

        token = parse_qs(urlsplit(continuation).query).get("continuation", [""])[0]
        if not token:
            return LiveChatPage(actions=[], continuation=None, status="ended")
        version = dict(headers or {}).get("X-YouTube-Client-Version", "")
        body = json.dumps({"context": {"client": {"clientName": "WEB", "clientVersion": version, "hl": "en"}}, "continuation": token}).encode()
        try:
            collected = self._read(continuation, headers=dict(headers or {}), data=body)
        except _ChatSourceGone:
            # The chat continuation expired or the source turned private:
            # a bounded terminal outcome, never a raise into media playback.
            return LiveChatPage(actions=[], continuation=None, status="ended")
        try:
            payload = json.loads(collected.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise UnsupportedPlaybackError("The live chat continuation page was not valid JSON.") from exc
        return _parse_live_chat_payload(payload, continuation)


def _parse_live_chat_payload(payload, source_continuation):  # noqa: ANN001
    """Tolerantly read innertube live-chat JSON into a bounded page.

    Returns the continuation actions and the next live-edge address. Any missing
    or unexpected shape resolves to an ended page rather than a raise, so a
    provider change degrades the chat rail without ever touching media.
    """

    from app.services.live_chat_seam import LiveChatPage

    if not isinstance(payload, dict):
        return LiveChatPage(actions=[], continuation=None, status="ended")
    contents = payload.get("continuationContents")
    live = contents.get("liveChatContinuation") if isinstance(contents, dict) else None
    if not isinstance(live, dict):
        return LiveChatPage(actions=[], continuation=None, status="ended")
    actions = live.get("actions")
    actions = [item for item in actions if isinstance(item, dict)] if isinstance(actions, list) else []
    next_continuation = _next_continuation_address(live.get("continuations"), source_continuation)
    if next_continuation is None:
        return LiveChatPage(actions=actions, continuation=None, status="ended")
    return LiveChatPage(actions=actions, continuation=next_continuation, status="active")


def _next_continuation_address(continuations, source_continuation):  # noqa: ANN001
    if not isinstance(continuations, list):
        return None
    for entry in continuations:
        if not isinstance(entry, dict):
            continue
        for data in entry.values():
            token = data.get("continuation") if isinstance(data, dict) else None
            if isinstance(token, str) and token:
                # Preserve the fetchable innertube address, swapping only the
                # continuation token so the next fetch stays at the live edge.
                return _swap_continuation_token(source_continuation, token)
    return None


def _swap_continuation_token(source_continuation, token) -> str | None:  # noqa: ANN001
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(source_continuation)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    replaced = False
    for key in ("continuation", "ctoken"):
        if key in query:
            query[key] = token
            replaced = True
    if not replaced:
        query["continuation"] = token
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
