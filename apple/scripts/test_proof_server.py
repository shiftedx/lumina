#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from proof_server import (
    install_initial_video_format_failure,
    install_metadata_refresh_metrics,
    install_requests_catalog_metrics,
)


class ProofServerTests(unittest.TestCase):
    def test_metadata_refresh_metrics_count_only_next_up_and_bypass_frontend(self) -> None:
        app = FastAPI()

        @app.get("/Shows/NextUp")
        def next_up() -> dict[str, list[object]]:
            return {"Items": []}

        @app.api_route("/{path:path}", methods=["GET", "POST"], name="frontend")
        def frontend(path: str) -> Response:  # noqa: ARG001
            return Response("frontend", media_type="text/plain")

        install_metadata_refresh_metrics(app)
        with TestClient(app) as client:
            self.assertEqual(client.get("/anything").text, "frontend")
            self.assertEqual(client.post("/anything").text, "frontend")
            self.assertEqual(client.get("/Shows/NextUp").status_code, 200)
            self.assertEqual(client.get("/Items").text, "frontend")
            self.assertEqual(client.get("/proof/metadata-refresh-metrics").json(), {"next_up": 1})
            self.assertEqual(client.post("/proof/metadata-refresh-metrics/reset").json(), {"next_up": 0})
            self.assertEqual(client.get("/proof/metadata-refresh-metrics").json(), {"next_up": 0})

    def test_requests_metrics_expose_only_aggregate_catalog_counts(self) -> None:
        app = FastAPI()

        @app.get("/Lumina/Requests/catalog/genres/{kind}")
        def genres(kind: str) -> dict[str, str]:
            return {"kind": kind}

        @app.get("/Lumina/Requests/catalog/title/{kind}/{identifier}")
        def detail(kind: str, identifier: str) -> dict[str, str]:
            return {"kind": kind, "identifier": identifier}

        @app.api_route("/{path:path}", methods=["GET", "POST"], name="frontend")
        def frontend(path: str) -> Response:  # noqa: ARG001
            return Response("frontend", media_type="text/plain")

        install_requests_catalog_metrics(app)
        with TestClient(app) as client:
            self.assertEqual(client.get("/anything").text, "frontend")
            self.assertEqual(client.post("/anything").text, "frontend")
            self.assertEqual(client.get("/Lumina/Requests/catalog/genres/movie").status_code, 200)
            self.assertEqual(client.get("/Lumina/Requests/catalog/genres/show").status_code, 200)
            self.assertEqual(client.get("/Lumina/Requests/catalog/title/movie/603").status_code, 200)
            self.assertEqual(client.get("/Lumina/Requests/catalog/search", params={"q": "secret"}).text, "frontend")

            self.assertEqual(client.get("/proof/requests-metrics").json(), {
                "genres_movie": 1, "genres_show": 1, "genres_anime": 0, "details": 1,
            })
            self.assertEqual(client.post("/proof/requests-metrics/reset").json(), {
                "genres_movie": 0, "genres_show": 0, "genres_anime": 0, "details": 0,
            })
            self.assertEqual(client.get("/proof/requests-metrics").json(), {
                "genres_movie": 0, "genres_show": 0, "genres_anime": 0, "details": 0,
            })

    def test_verified_public_item_faults_only_direct_stream_after_activation(self) -> None:
        app = FastAPI()
        public_id = "public-video-001"
        physical_id = "physical-library-item-001"
        original = b"original-direct-mp4"
        hls = b"#EXTM3U\n#EXT-X-VERSION:7\n"

        @app.get("/Videos/{item_id}/stream")
        def direct_stream(item_id: str) -> Response:
            return Response(original if item_id == public_id else b"other-direct-mp4", media_type="video/mp4")

        @app.get("/Videos/{item_id}/master.m3u8")
        def master(item_id: str) -> Response:  # noqa: ARG001
            return Response(hls, media_type="application/vnd.apple.mpegurl")

        activate = install_initial_video_format_failure(app)
        with TestClient(app) as client:
            before = client.get(f"/Videos/{public_id}/stream", headers={"Range": "bytes=0-3"})
            self.assertEqual(before.status_code, 200)
            self.assertEqual(before.content, original)
            self.assertFalse(client.get("/Lumina/Proof/initial-video-format-recovery").json()["faultedDirectStream"])

            activate(public_id)
            faulted = client.get(f"/Videos/{public_id}/stream", headers={"Range": "bytes=0-3"})
            self.assertEqual(faulted.status_code, 206)
            self.assertEqual(faulted.headers.get("content-type"), "video/mp4")
            self.assertEqual(faulted.headers.get("content-range"), "bytes 0-3/491520")
            self.assertEqual(faulted.content, b"not-")

            self.assertNotEqual(public_id, physical_id)
            self.assertEqual(client.get(f"/Videos/{physical_id}/stream").content, b"other-direct-mp4")
            self.assertEqual(client.get(f"/Videos/{public_id}/master.m3u8").content, hls)
            snapshot = client.get("/Lumina/Proof/initial-video-format-recovery").json()
            self.assertTrue(snapshot["faultedDirectStream"])


if __name__ == "__main__":
    unittest.main()
