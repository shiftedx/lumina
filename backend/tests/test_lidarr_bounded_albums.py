from __future__ import annotations
import json
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
                state["active"] += 1; state["max_active"] = max(state["max_active"], state["active"])
                time.sleep(.01)
                try:
                    if aid == fail_artist: return httpx.Response(500)
                    return httpx.Response(200, json=albums_by_artist.get(aid, []))
                finally: state["active"] -= 1
            return httpx.Response(404)
        real_client = httpx.Client
        def mock_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(respond)
            return real_client(*args, **kwargs)
        connector = ServiceConnector(id="connector", kind="lidarr", name="Lidarr", base_url="http://lidarr.example", api_key="fixture-key", settings={})
        return connector, state, mock_client

    def observe(self, fixture):
        connector, state, mock_client = fixture
        with patch.object(connectors.httpx, "Client", side_effect=mock_client), patch.object(LidarrClient, "test", return_value={"version": "2.0"}), patch.object(LidarrClient, "paged", return_value=[]):
            return connectors.observe(connector), state

    def test_all_albums_retrieved_per_artist_with_two_request_window(self):
        fixture = self.setup_fixture()
        result, state = self.observe(fixture)
        self.assertEqual([row["id"] for row in result[1]["albums"]], [11, 22])
        self.assertEqual(result[1]["albums"][0]["title"], "One")
        album_requests = [r for r in state["requests"] if r[1].endswith("/album")]
        self.assertCountEqual([r[2] for r in album_requests], [{"artistId": "1"}, {"artistId": "2"}])
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

    def test_per_artist_reader_preserves_complete_album_rows(self):
        fixture = self.setup_fixture()
        _, _, mock_client = fixture
        connector = fixture[0]
        with patch.object(connectors.httpx, "Client", side_effect=mock_client):
            rows, byte_count = connectors._lidarr_album_rows(LidarrClient.of(connector), 1)
        self.assertEqual(rows, [{"id": 11, "artistId": 1, "title": "One", "extra": {"keep": True}}])
        self.assertGreater(byte_count, 0)

    def test_album_observation_deadline_stops_before_next_batch(self):
        fixture = self.setup_fixture(albums_by_artist={1: [], 2: [], 3: []})
        with patch.object(connectors, "LIDARR_ALBUM_OBSERVATION_SECONDS", 1), \
             patch.object(connectors.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(connectors.ConnectorError, "time limit"):
                self.observe(fixture)
        self.assertFalse(any(path.endswith("/album") for _, path, _ in fixture[1]["requests"]))

if __name__ == "__main__": unittest.main()
