#!/usr/bin/env python3
from __future__ import annotations

import http.client
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import real_media_range_proxy as proxy


class RangeProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix='lumina-range-proxy-')
        self.root = Path(self.temporary.name)
        self.media = self.root / 'media.bin'
        self.media.write_bytes(bytes(range(256)) * 8192)
        self.app_root = self.root / 'stub'
        (self.app_root / 'app' / 'services').mkdir(parents=True)
        (self.app_root / 'app' / '__init__.py').touch()
        (self.app_root / 'app' / 'services' / '__init__.py').touch()
        data = self.root / 'data'
        data.mkdir()
        (self.app_root / 'app' / 'config.py').write_text(
            'from pathlib import Path\nclass Settings: pass\n'
            f'settings = Settings(); settings.data_dir = Path({str(data)!r}); settings.database_filename = "state.sqlite3"\n'
        )
        (self.app_root / 'app' / 'services' / 'media_artifacts.py').write_text(
            'from pathlib import Path\ndef artifact_file(root, relative): return Path(root) / relative\n'
        )
        self.database = data / 'state.sqlite3'
        with sqlite3.connect(self.database) as connection:
            connection.execute('create table storage_roots (id integer primary key, path text not null)')
            connection.execute('create table media_artifacts (id text primary key, root_id integer, relative_path text not null)')
            connection.execute('insert into storage_roots values (1, ?)', (str(self.root),))
            connection.execute('insert into media_artifacts values (?, 1, ?)', ('fixture', self.media.name))
        self.environment = mock.patch.dict(os.environ, {'PYTHONPATH': str(self.app_root)}, clear=False)
        self.environment.start()
        self.identity = self.identity_for(self.media)
        self.proxy: proxy.RangeProxy | None = None
        self.thread: threading.Thread | None = None

    def tearDown(self) -> None:
        if self.proxy is not None:
            self.proxy.close()
        if self.thread is not None:
            self.thread.join(timeout=3)
            self.assertFalse(self.thread.is_alive())
        self.environment.stop()
        self.temporary.cleanup()

    def identity_for(self, path: Path) -> proxy.SourceIdentity:
        stat = path.stat()
        return proxy.SourceIdentity(
            artifactID='fixture', size=stat.st_size, mtimeNS=stat.st_mtime_ns, device=stat.st_dev, inode=stat.st_ino,
            etag='"fixture-v1"', duration=90, audioStreamIndex=1,
            expected={'width': 1920, 'height': 1080, 'videoCodec': 'h264', 'hdr': 'sdr', 'audioCodecs': ['aac']},
        )

    def factory(self, identity: proxy.SourceIdentity) -> list[str]:
        return [
            sys.executable, '-u', '-c', proxy.READER_SCRIPT, identity.artifactID,
            str(identity.size), str(identity.mtimeNS), str(identity.device), str(identity.inode), str(proxy.CHUNK_BYTES),
        ]

    def start(self, max_run_bytes: int | None = None, streaming_ranges: bool = False) -> str:
        self.proxy = proxy.start_proxy(
            self.identity, self.factory, self.root / 'manifest.json', max_run_bytes=max_run_bytes,
            streaming_ranges=streaming_ranges,
        )
        self.thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.thread.start()
        return f'127.0.0.1:{self.proxy.server_port}{self.proxy.route}'

    @staticmethod
    def request(authority: str, range_header: str | None = None) -> tuple[int, dict[str, str], bytes]:
        host, path = authority.split('/', 1)
        connection = http.client.HTTPConnection(host, timeout=5)
        headers = {'Range': range_header} if range_header else {}
        try:
            connection.request('GET', '/' + path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_framed_reader_serves_finite_and_open_tail_exact_bytes(self) -> None:
        authority = self.start()
        original = self.media.read_bytes()
        for requested in ('bytes=0-1048575', 'bytes=1048576-2097151', f'bytes={len(original) - 1024}-'):
            status, headers, body = self.request(authority, requested)
            self.assertEqual(status, 206)
            start, end = (int(part) for part in headers['Content-Range'].split(' ', 1)[1].split('/')[0].split('-'))
            self.assertEqual(body, original[start:end + 1])
            self.assertEqual(headers['ETag'], self.identity.etag)

    def test_rejects_unbounded_get_and_response_or_run_budget(self) -> None:
        authority = self.start(max_run_bytes=1024)
        self.assertEqual(self.request(authority)[0], 416)
        with mock.patch.object(proxy, 'MAX_RESPONSE_BYTES', 1024):
            self.assertEqual(self.request(authority, 'bytes=0-1024')[0], 416)
        self.assertEqual(self.request(authority, 'bytes=0-1023')[0], 206)
        self.assertEqual(self.request(authority, 'bytes=1024-1024')[0], 503)

    def test_invalid_run_budget_rejected_before_starting_source_readers(self) -> None:
        with mock.patch.object(proxy, 'ReaderPool') as readers:
            for limit in (0, -1, (1 << 30) + 1, True):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    proxy.RangeProxy(self.identity, self.factory, max_run_bytes=limit)
            readers.assert_not_called()

    def test_raw_player_streaming_ranges_still_enforce_source_budget(self) -> None:
        authority = self.start(max_run_bytes=self.identity.size, streaming_ranges=True)
        with mock.patch.object(proxy, 'MAX_RESPONSE_BYTES', 1024):
            status, _, body = self.request(authority, 'bytes=0-')
        self.assertEqual((status, body), (206, self.media.read_bytes()))
        assert self.proxy is not None
        self.assertEqual(self.proxy.budget, self.identity.size)
        # A subsequent request cannot read beyond the same origin's budget.
        self.assertEqual(self.request(authority, 'bytes=0-0')[0], 503)
        self.assertEqual(self.proxy.budget, self.identity.size)

    def test_streaming_budget_stops_mid_response_without_reading_extra(self) -> None:
        authority = self.start(max_run_bytes=proxy.CHUNK_BYTES, streaming_ranges=True)
        with self.assertRaises(http.client.IncompleteRead) as caught:
            self.request(authority, 'bytes=0-')
        self.assertEqual(caught.exception.partial, self.media.read_bytes()[:proxy.CHUNK_BYTES])
        assert self.proxy is not None
        self.assertEqual(self.proxy.budget, proxy.CHUNK_BYTES)

    def test_raw_player_can_stream_without_a_range(self) -> None:
        self.identity.expected['contentType'] = 'video/mp4'
        authority = self.start(streaming_ranges=True)
        status, headers, body = self.request(authority)
        self.assertEqual((status, body), (200, self.media.read_bytes()))
        self.assertEqual(int(headers['Content-Length']), self.identity.size)
        self.assertEqual(headers['Content-Type'], 'video/mp4')

    def test_frozen_source_cannot_inject_response_headers(self) -> None:
        config, state, manifest = (self.root / name for name in ('config.json', 'state.json', 'manifest.json'))
        proxy.write_private(config, {'sshHost': 'fixture-host', 'container': 'fixture-container', 'artifactID': 'fixture'})
        self.identity.expected['contentType'] = 'video/mp4\r\nX-Injected: yes'
        proxy.write_private(state, asdict(self.identity))
        arguments = ['proxy', '--config', str(config), '--source-state', str(state), '--manifest', str(manifest)]
        with mock.patch.object(sys, 'argv', arguments), mock.patch.object(sys, 'stderr', io.StringIO()), \
             mock.patch.object(proxy, 'ReaderPool') as readers, self.assertRaises(SystemExit) as caught:
            proxy.main()
        self.assertEqual(caught.exception.code, 2)
        readers.assert_not_called()
        self.assertFalse(manifest.exists())

    def test_disconnect_reuses_framed_reader_and_shutdown_closes_workers(self) -> None:
        authority = self.start()
        host, path = authority.split('/', 1)
        connection = http.client.HTTPConnection(host, timeout=5)
        connection.request('GET', '/' + path, headers={'Range': 'bytes=0-1048575'})
        response = connection.getresponse()
        self.assertEqual(response.status, 206)
        self.assertTrue(response.read(64))
        connection.close()
        time.sleep(0.1)
        status, _, body = self.request(authority, 'bytes=4096-4103')
        self.assertEqual((status, body), (206, self.media.read_bytes()[4096:4104]))
        assert self.proxy is not None
        readers = list(self.proxy.pool.readers)
        self.proxy.close()
        self.proxy = None
        self.assertTrue(all(reader.process is None for reader in readers))

    def test_stale_identity_refuses_workers_before_a_manifest_can_be_written(self) -> None:
        stale = proxy.SourceIdentity(
            artifactID=self.identity.artifactID, size=self.identity.size, mtimeNS=self.identity.mtimeNS + 1,
            device=self.identity.device, inode=self.identity.inode, etag=self.identity.etag,
            duration=self.identity.duration, audioStreamIndex=self.identity.audioStreamIndex, expected=self.identity.expected,
        )
        manifest = self.root / 'private-manifest.json'
        with self.assertRaises(RuntimeError):
            proxy.RangeProxy(stale, self.factory)
        self.assertFalse(manifest.exists())

    def test_manifest_failure_closes_unserved_workers_without_waiting_for_serve_forever(self) -> None:
        created: list[proxy.RangeProxy] = []
        actual = proxy.RangeProxy

        def capture(*arguments: object) -> proxy.RangeProxy:
            value = actual(*arguments)
            created.append(value)
            return value

        with mock.patch.object(proxy, 'RangeProxy', side_effect=capture), \
             mock.patch.object(proxy, 'write_private', side_effect=OSError('fixture write failure')):
            started = time.monotonic()
            with self.assertRaises(OSError):
                proxy.start_proxy(self.identity, self.factory, self.root / 'private-manifest.json')
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(created), 1)
        self.assertTrue(all(reader.process is None for reader in created[0].pool.readers))

    def test_shutdown_waits_for_failed_reader_restart_and_closes_its_new_process(self) -> None:
        self.proxy = proxy.RangeProxy(self.identity, self.factory)
        reader = self.proxy.pool.acquire()
        original_start = reader.start
        entered = threading.Event()
        continue_start = threading.Event()

        def delayed_start() -> None:
            entered.set()
            self.assertTrue(continue_start.wait(timeout=3))
            original_start()

        with mock.patch.object(reader, 'start', side_effect=delayed_start):
            release = threading.Thread(target=self.proxy.pool.release, args=(reader, False))
            release.start()
            self.assertTrue(entered.wait(timeout=3))
            closing = threading.Thread(target=self.proxy.pool.close)
            closing.start()
            continue_start.set()
            release.join(timeout=5)
            closing.join(timeout=5)
        self.assertFalse(release.is_alive())
        self.assertFalse(closing.is_alive())
        self.assertIsNone(reader.process)

    def test_private_config_requires_owner_only_permissions(self) -> None:
        config = self.root / 'config.json'
        config.write_text(json.dumps({'sshHost': 'fixture-host', 'container': 'fixture-container', 'artifactID': 'fixture'}))
        os.chmod(config, 0o600)
        self.assertEqual(proxy.PrivateConfig.load(config).artifactID, 'fixture')
        os.chmod(config, 0o644)
        with self.assertRaises(ValueError):
            proxy.PrivateConfig.load(config)


if __name__ == '__main__':
    unittest.main()
