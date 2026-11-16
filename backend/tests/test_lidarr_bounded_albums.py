from __future__ import annotations
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import threading
import time
import unittest
from unittest.mock import patch
import httpx
from app.models import ServiceConnector
from app.services import infrastructure_connectors as connectors
from app.services import lidarr
from app.services.lidarr import LidarrClient

class LidarrBoundedAlbumsTests(unittest.TestCase):
    def setup_fixture(self, albums_by_artist=None, *, artist_sets=None, fail_artist=None, oversized_unfiltered=False):
        albums_by_artist = ({1: [{"id": 11, "artistId": 1, "title": "One", "extra": {"keep": True}}], 2: [{"id": 22, "artistId": 2, "title": "Two"}]} if albums_by_artist is None else albums_by_artist)
        state = {"requests": [], "active": 0, "max_active": 0, "artists_calls": 0}
        artist_sets = artist_sets or [[{"id": i, "artistName": f"artist-{i}", "foreignArtistId": "abc", "monitored": True, "statistics": {"trackFileCount": 0}} for i in albums_by_artist]]
        def respond(request):
            path = request.url.path
            state["requests"].append((request.method, path, dict(request.url.params)))
            if path.endswith("/api/v1/artist"):
                idx = min(state["artists_calls"], len(artist_sets)-1); state["artists_calls"] += 1
                return httpx.Response(200, json=artist_sets[idx])
            if path.endswith("/api/v1/album"):
                if "artistId" not in request.url.params:
                    if oversized_unfiltered: return httpx.Response(200, content=b"[" + b" " * (16 * 1024 * 1024) + b"]")
                    return httpx.Response(200, json=[row for rows in albums_by_artist.values() for row in rows])
                aid = int(request.url.params["artistId"])
                if aid == fail_artist: return httpx.Response(500)
                return httpx.Response(200, json=albums_by_artist.get(aid, []))
            return httpx.Response(404)
        async def async_respond(request):
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            try:
                await asyncio.sleep(.01)
                return respond(request)
            finally:
                state["active"] -= 1
        real_client, real_async_client = httpx.Client, httpx.AsyncClient
        def mock_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(respond)
            return real_client(*args, **kwargs)
        def mock_async_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(async_respond)
            return real_async_client(*args, **kwargs)
        mock_client.async_client = mock_async_client
        connector = ServiceConnector(id="connector", kind="lidarr", name="Lidarr", base_url="http://lidarr.example", api_key="fixture-key", settings={})
        return connector, state, mock_client

    def observe(self, fixture):
        connector, state, mock_client = fixture
        with patch.object(connectors.httpx, "Client", side_effect=mock_client), patch.object(connectors.httpx, "AsyncClient", side_effect=mock_client.async_client), patch.object(LidarrClient, "test", return_value={"version": "2.0"}), patch.object(LidarrClient, "paged", return_value=[]):
            return connectors.observe(connector), state

    def test_all_albums_retrieved_per_artist_with_two_request_window(self):
        fixture = self.setup_fixture(albums_by_artist={
            artist_id: [{"id": artist_id * 11, "artistId": artist_id, "title": "One"}]
            for artist_id in range(1, 5)
        })
        result, state = self.observe(fixture)
        self.assertEqual([row["id"] for row in result[1]["albums"]], [11, 22, 33, 44])
        self.assertEqual(result[1]["albums"][0]["title"], "One")
        album_requests = [r for r in state["requests"] if r[1].endswith("/album")]
        self.assertCountEqual([r[2] for r in album_requests], [{"artistId": str(i)} for i in range(1, 5)])
        self.assertLessEqual(state["max_active"], 2)
        self.assertTrue(all(method == "GET" for method, _, _ in state["requests"]))

    def test_no_artists_returns_empty_albums(self):
        fixture = self.setup_fixture(albums_by_artist={}, artist_sets=[[]])
        result, state = self.observe(fixture)
        self.assertEqual(result[1]["albums"], [])
        self.assertFalse(any(path.endswith("/album") for _, path, _ in state["requests"]))

    def test_artist_added_after_empty_initial_list_fails(self):
        fixture = self.setup_fixture(albums_by_artist={}, artist_sets=[[], [{"id": 3, "artistName": "new", "statistics": {}}]])
        with self.assertRaises(connectors.ConnectorError): self.observe(fixture)

    def test_artist_set_change_fails_observation(self):
        rows = [{"id": 1, "artistName": "a", "statistics": {}}]
        fixture = self.setup_fixture(albums_by_artist={1: []}, artist_sets=[rows, [{"id": 2, "artistName": "b", "statistics": {}}]])
        with self.assertRaises(connectors.ConnectorError): self.observe(fixture)

    def test_failure_for_one_artist_fails_whole_observation(self):
        fixture = self.setup_fixture(fail_artist=2)
        with self.assertRaises(connectors.ConnectorError): self.observe(fixture)

    def test_invalid_duplicate_or_wrong_artist_album_rows_fail(self):
        for rowset in ([{"id": 1, "artistId": 1}, {"id": 1, "artistId": 1}], [{"id": 1, "artistId": 9}], [{"id": None, "artistId": 1}]):
            with self.subTest(rows=rowset):
                fixture = self.setup_fixture(albums_by_artist={1: rowset}, artist_sets=[[{"id": 1, "artistName": "a", "statistics": {}}]])
                with self.assertRaises(connectors.ConnectorError): self.observe(fixture)

    def test_large_unfiltered_collection_uses_small_filtered_reads(self):
        fixture = self.setup_fixture(oversized_unfiltered=True)
        _, state = self.observe(fixture)
        album_requests = [row for row in state["requests"] if row[1].endswith("/album")]
        self.assertTrue(album_requests)
        self.assertTrue(all("artistId" in row[2] for row in album_requests))

    def test_invalid_artist_id_fails(self):
        fixture = self.setup_fixture(albums_by_artist={}, artist_sets=[[{"id": None, "artistName": "a", "statistics": {}}]])
        with self.assertRaises(connectors.ConnectorError): self.observe(fixture)

    def test_aggregate_byte_and_record_limits_fail_closed(self):
        fixture = self.setup_fixture()
        with patch.object(lidarr, "MAX_PAGED_BYTES", 1):
            with self.assertRaises(connectors.ConnectorError): self.observe(fixture)
        fixture = self.setup_fixture()
        with patch.object(lidarr, "MAX_PAGED_RECORDS", 1):
            with self.assertRaises(connectors.ConnectorError): self.observe(fixture)

    def test_observation_preserves_complete_album_rows(self):
        expected = [{"id": 11, "artistId": 1, "title": "One", "extra": {"keep": True}}]
        connector, _, mock_client = self.setup_fixture(albums_by_artist={1: expected})
        with patch.object(connectors.httpx, "AsyncClient", side_effect=mock_client.async_client):
            rows = connectors._lidarr_albums(LidarrClient.of(connector), [{"id": 1}])
        self.assertEqual(rows, expected)

    def test_deadline_cancels_slow_streams_and_membership_rereads(self):
        for phase in ("album", "artist", "empty"):
            with self.subTest(phase=phase):
                state = {"artist_calls": 0, "album_ids": []}
                active: set[int] = set()
                lock = threading.Lock()
                started = threading.Event()
                closed = threading.Event()

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *_args):
                        pass

                    def do_GET(self):
                        url = urlsplit(self.path)
                        if url.path.endswith("/artist"):
                            state["artist_calls"] += 1
                            rows = [] if phase == "empty" else [{"id": i} for i in (1, 2, 3)]
                            slow = phase != "album" and state["artist_calls"] > 1
                        else:
                            artist_id = int(parse_qs(url.query)["artistId"][0])
                            state["album_ids"].append(artist_id)
                            rows = [{"id": artist_id, "artistId": artist_id}]
                            slow = phase == "album"
                        self.send_response(200)
                        self.end_headers()
                        if not slow:
                            self.wfile.write(json.dumps(rows).encode())
                            return
                        identity = threading.get_ident()
                        with lock:
                            active.add(identity)
                        started.set()
                        try:
                            self.wfile.write(b"[")
                            # Every chunk arrives well inside HTTPX's inactivity timeout.
                            for _ in range(100):
                                self.wfile.write(b" ")
                                self.wfile.flush()
                                time.sleep(.01)
                            self.wfile.write(b"]")
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        finally:
                            with lock:
                                active.discard(identity)
                                if not active:
                                    closed.set()

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                connector = ServiceConnector(kind="lidarr", base_url=f"http://127.0.0.1:{server.server_port}", api_key="fixture-key", settings={})
                try:
                    with patch.object(LidarrClient, "test", return_value={"version": "2.0"}), \
                         patch.object(LidarrClient, "paged", return_value=[]), \
                         patch.object(connectors, "LIDARR_ALBUM_OBSERVATION_SECONDS", .15):
                        before = time.monotonic()
                        with self.assertRaisesRegex(connectors.ConnectorError, "time limit"):
                            connectors.observe(connector)
                        elapsed = time.monotonic() - before
                    self.assertTrue(started.is_set())
                    self.assertLess(elapsed, .75)
                    self.assertTrue(closed.wait(.5), "Timed-out responses must release their sockets")
                    if phase == "album":
                        self.assertCountEqual(state["album_ids"], [1, 2])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

if __name__ == "__main__": unittest.main()
