"""Audit-only request observations and offline/rate-limit controls for budget tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from urllib.parse import parse_qsl, urlsplit

import pytest


class OfflineYoutubeDL:
    def __init__(self, options, policy=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, query, download=False):
        return {"entries": []}

    def sanitize_info(self, info):
        return info


@pytest.fixture(scope="session", autouse=True)
def offline_provider():
    # This dedicated benchmark subprocess must stay offline even when a
    # background extraction outlives an individual request/test fixture.
    from app.services import yt_dlp_service
    yt_dlp_service.PolicyYoutubeDL = OfflineYoutubeDL


@pytest.fixture(autouse=True)
def audit_requests(monkeypatch, request):
    from starlette.testclient import TestClient
    from app.services.rate_limit import RATE_LIMIT_RULES, RateLimitRule, rate_limiter

    rate_limiter.clear()
    for bucket, rule in list(RATE_LIMIT_RULES.items()):
        monkeypatch.setitem(RATE_LIMIT_RULES, bucket, RateLimitRule(1_000_000, rule.window_seconds))
    original = TestClient.request
    samples = []

    def measured(self, method, url, *args, **kwargs):
        started, cpu = time.perf_counter(), time.process_time()
        parsed = urlsplit(str(url))
        filters = {"type", "sort", "limit", "unwatched", "in_progress", "favorites", "genre", "year_from", "year_to", "resolution", "letter", "season", "cursor"}
        row = {"method": str(method), "path": parsed.path,
               "filters": {key: "<cursor>" if key == "cursor" else value for key, value in parse_qsl(parsed.query) if key in filters}}
        try:
            response = original(self, method, url, *args, **kwargs)
            row.update(status=response.status_code, decoded_response_bytes=len(response.content))
            return response
        except Exception as error:
            row.update(error=type(error).__name__)
            raise
        finally:
            row.update(wall_ms=(time.perf_counter() - started) * 1000, process_cpu_ms=(time.process_time() - cpu) * 1000)
            samples.append(row)

    monkeypatch.setattr(TestClient, "request", measured)
    try:
        yield
    finally:
        destination = Path(os.environ["AUDIT_TITLE_REQUESTS"])
        with destination.open("a") as output:
            output.write(json.dumps({"test": request.node.nodeid, "scope": "all requests including warmups/assertions; process CPU includes background threads",
                "controls": "rate-limit ceilings raised for computation timing; background YouTube search returns an empty offline fixture", "requests": samples}) + "\n")
        rate_limiter.clear()
