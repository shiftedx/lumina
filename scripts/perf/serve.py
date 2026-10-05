"""Run the real backend against a seeded data dir, provider network stubbed, for load measurement.

    backend/.venv/bin/python scripts/perf/serve.py DATA_DIR PORT

Measurement-only differences from production: provider extraction returns a canned
flat result (no network; loopback-only socket guard as a backstop), per-member rate
limits are off so the load mix is not throttled, and every response carries an
``X-Perf-Queries`` header with its SQL statement count.
"""
from __future__ import annotations

import contextvars
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["LUMINA_DATA_DIR"] = str(Path(sys.argv[1]).resolve())
os.environ["LUMINA_ALLOWED_ORIGINS"] = f"http://127.0.0.1:{sys.argv[2]}"
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]

import test_safety  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import event  # noqa: E402

test_safety.LoopbackOnlyGuard().apply(lambda target, name, value: setattr(target, name, value))

from app import main  # noqa: E402
from app.db import engine  # noqa: E402
from app.services import yt_dlp_service  # noqa: E402


class CannedYoutubeDL:
    def __init__(self, options, policy=None):  # noqa: ANN001
        del options, policy

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc):  # noqa: ANN002
        return False

    def extract_info(self, query, download=False):  # noqa: ANN001
        return {"entries": [
            {"id": f"stub{n:05d}", "title": f"Stub result {n} for {query}", "url": f"https://www.youtube.com/watch?v=stub{n:05d}",
             "ie_key": "Youtube", "duration": 300 + n, "channel": f"Stub channel {n % 7}", "view_count": 1000 * n}
            for n in range(24)
        ]}

    def sanitize_info(self, info):  # noqa: ANN001
        return info


yt_dlp_service.PolicyYoutubeDL = CannedYoutubeDL
main.enforce_rate_limit = lambda *args, **kwargs: None

_queries: contextvars.ContextVar[list | None] = contextvars.ContextVar("perf_queries", default=None)


@event.listens_for(engine, "before_cursor_execute")
def _count(*_args) -> None:  # noqa: ANN002
    counter = _queries.get()
    if counter is not None:
        counter[0] += 1


class QueryCountMiddleware:
    """Pure ASGI so streaming responses are untouched; threadpool calls share the context's list."""

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        counter = [0]
        _queries.set(counter)

        async def counted_send(message):  # noqa: ANN001
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-perf-queries", str(counter[0]).encode()))
            await send(message)

        await self.app(scope, receive, counted_send)


main.app.add_middleware(QueryCountMiddleware)

if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning", timeout_keep_alive=30)
