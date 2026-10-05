"""Gallery T1: /api/art delivery: signed capability URLs, zero SQL on a hit, headers, fallbacks."""
from __future__ import annotations

import collections
import threading
import time
import uuid
from functools import partial

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app import db as db_module
from app.main import app
from app.models import MediaTitle
from app.routers import art
from app.services import art_urls, renditions
from app.services.titles import title_image_source
from art_support import add_art_title, art_url, fake_calls, png_header, seed_art_root, use_fake_ffmpeg, write_rendition

MOVIE = str(uuid.UUID(int=0xD0E))


@pytest.fixture
def poster(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(art_urls, "_secret", None)
    monkeypatch.setattr(art_urls, "_extension", "webp")
    monkeypatch.setattr(art_urls, "_pending", art_urls.OrderedDict())
    monkeypatch.setattr(renditions, "_serving", collections.Counter())
    art.byte_cache.clear()
    media = tmp_path.resolve() / "media"
    seed_art_root(media)
    add_art_title(media, MOVIE)
    with db_module.SessionLocal() as session:
        source = title_image_source(session.get(MediaTitle, MOVIE), "Primary")
    yield media, source, art_urls.source_key(MOVIE, "Primary", source)
    for future in list(renditions._inflight.values()):
        future.result(timeout=10)
    art.byte_cache.clear()


@pytest.fixture
def http():  # noqa: ANN201
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


@pytest.fixture
def statements():  # noqa: ANN201
    seen: list[str] = []

    def record(conn, cursor, statement, *args):  # noqa: ANN001, ANN002, ARG001
        seen.append(statement)

    event.listen(db_module.engine, "before_cursor_execute", record)
    yield seen
    event.remove(db_module.engine, "before_cursor_execute", record)


def test_a_hit_is_served_with_immutable_headers_and_zero_sql(poster, http, statements) -> None:  # noqa: ANN001
    _, source, key = poster
    write_rendition(key, 240, b"W" * 100)
    url = art_url(MOVIE, "Primary", source, 240)
    statements.clear()
    first = http.get(url)
    second = http.get(url)
    assert statements == []
    assert (first.status_code, first.content, second.content) == (200, b"W" * 100, b"W" * 100)
    assert first.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert first.headers["etag"] == f'"{key}-240.webp"'
    assert (first.headers["content-type"], first.headers["content-length"], first.headers["x-content-type-options"]) == ("image/webp", "100", "nosniff")
    assert (renditions.serving().hits_disk, renditions.serving().hits_memory) == (1, 1)


def test_a_scoped_signature_is_cached_privately_and_briefly(poster, http, monkeypatch) -> None:  # noqa: ANN001
    _, source, key = poster
    write_rendition(key, 240, b"W" * 100)
    monkeypatch.setattr(art, "_scope_sees", lambda scope, title_id: True)
    url = art_urls.rendition_template(MOVIE, "Primary", key, "deadbeef" + "kid").replace("{w}", "240")
    first = http.get(url)
    assert (first.status_code, first.headers["cache-control"]) == (200, "private, max-age=300")
    again = http.get(url, headers={"If-None-Match": first.headers["etag"]})
    assert (again.status_code, again.headers["cache-control"]) == (304, "private, max-age=300")


def test_if_none_match_is_304_and_head_sends_only_headers(poster, http) -> None:  # noqa: ANN001
    _, source, key = poster
    write_rendition(key, 480, b"W" * 10)
    url = art_url(MOVIE, "Primary", source, 480)
    unchanged = http.get(url, headers={"If-None-Match": f'W/"{key}-480.webp", "other"'})
    assert (unchanged.status_code, unchanged.content, unchanged.headers["etag"]) == (304, b"", f'"{key}-480.webp"')
    head = http.head(url)
    assert (head.status_code, head.content, head.headers["content-length"]) == (200, b"", "10")


def _tamper(url: str, part: str) -> str:
    prefix, sig, title_id, image_type, file = url.rsplit("/", 4)
    key, rest = file.split("-")
    changes = {
        "sig": (("A" if sig[0] != "A" else "B") + sig[1:], title_id, image_type, file),
        "sig-length": (sig + "A", title_id, image_type, file),
        "other-title": (sig, str(uuid.UUID(int=1)), image_type, file),
        "upper-title": (sig, title_id.upper(), image_type, file),
        "type": (sig, title_id, "Thumb", file),
        "backdrop-sig-reuse": (sig, title_id, "Backdrop", file.replace("-240.", "-960.")),
        "width": (sig, title_id, image_type, f"{key}-300.webp"),
        "padded-width": (sig, title_id, image_type, f"{key}-0240.webp"),
        "ext": (sig, title_id, image_type, f"{key}-240.png"),
        "key-case": (sig, title_id, image_type, f"{key.upper()}-{rest}"),
        "dots": (sig, title_id, image_type, "..-240.webp"),
        "trailing-newline": (sig, title_id, image_type, f"{file}%0A"),
    }
    return "/".join((prefix, *changes[part]))


@pytest.mark.parametrize("part", ["sig", "sig-length", "other-title", "upper-title", "type", "backdrop-sig-reuse", "width", "padded-width", "ext", "key-case", "dots", "trailing-newline"])
def test_every_tampered_part_is_404_before_any_sql(poster, http, statements, part) -> None:  # noqa: ANN001
    _, source, key = poster
    write_rendition(key, 240, b"W")
    statements.clear()
    response = http.get(_tamper(art_url(MOVIE, "Primary", source, 240), part))
    assert (response.status_code, response.json()) == (404, {"detail": "Image not found"})
    assert statements == []


def test_a_miss_for_art_that_changed_is_404(poster, http) -> None:  # noqa: ANN001
    assert http.get(art_url(MOVIE, "Primary", "an-older-tag", 240)).status_code == 404


def test_a_miss_is_generated_within_budget_from_the_titles_own_art_only(poster, http, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    _, source, key = poster
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    reads = []
    real = renditions.title_image_bytes
    monkeypatch.setattr(renditions, "title_image_bytes", lambda db, title, image_type, artwork: reads.append((title.id, image_type)) or real(db, title, image_type, artwork))
    url = art_url(MOVIE, "Primary", source, 240)
    started = time.perf_counter()
    response = http.get(url)
    assert time.perf_counter() - started < 0.8 + 0.5  # the budget, plus the fake's interpreter start-up
    assert (response.status_code, response.content, response.headers["etag"]) == (200, b"r" * 64, f'"{key}-240.webp"')
    assert reads == [(MOVIE, "Primary")] and len(fake_calls(log)) == 1
    assert http.get(url).content == b"r" * 64 and len(fake_calls(log)) == 1  # now a hit


def test_a_miss_over_budget_serves_a_small_original_or_503(poster, http, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    media, source, _ = poster
    use_fake_ffmpeg(tmp_path, monkeypatch, "hang")
    monkeypatch.setattr(renditions, "ON_DEMAND_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(renditions, "ON_DEMAND_TIMEOUT_SECONDS", 1.0)
    small = http.get(art_url(MOVIE, "Primary", source, 240))
    assert (small.status_code, small.content, small.headers["cache-control"]) == (200, png_header(1000, 1500), "no-store")
    assert small.headers["content-type"] == "image/png" and "etag" not in small.headers
    for future in list(renditions._inflight.values()):
        future.result(timeout=10)
    (media / MOVIE / "primary.png").write_bytes(png_header(1000, 1500) + b"\0" * (2 * 1024 * 1024))
    large = http.get(art_url(MOVIE, "Primary", source, 480))
    assert (large.status_code, large.headers["retry-after"], large.json()) == (503, "2", {"detail": "Image is being prepared"})


def test_the_byte_cache_is_a_bounded_lru() -> None:
    cache = art.ByteCache(limit=10)
    cache.put("a", b"1234", 1.0)
    cache.put("b", b"1234", 1.0)
    assert cache.get("a") == (b"1234", 1.0)  # a is now most recent
    cache.put("c", b"1234", 1.0)  # 12 > 10: evicts b, the least recent
    assert (cache.get("b"), cache.used) == (None, 8)
    cache.put("huge", b"x" * 11, 1.0)  # larger than the whole cache: never stored
    assert cache.get("huge") is None and cache.get("a") is not None


def test_art_reads_do_not_wait_on_the_default_thread_pool(poster) -> None:  # noqa: ANN001
    _, source, key = poster
    write_rendition(key, 240, b"W" * 100)
    url = art_url(MOVIE, "Primary", source, 240)

    async def scenario() -> float:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 1
        release = threading.Event()
        async with anyio.create_task_group() as group:
            group.start_soon(partial(anyio.to_thread.run_sync, release.wait))
            await anyio.sleep(0.05)  # the pool's only token is now held, like 40 blocked sync routes
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                    with anyio.fail_after(2):
                        started = time.perf_counter()
                        response = await client.get(url)
                        elapsed = time.perf_counter() - started
            finally:
                release.set()
        assert response.status_code == 200
        return elapsed

    # The 50 ms target is the reference-host budget (see the perf title budgets); here the guard is "not blocked at all".
    assert anyio.run(scenario) < 0.2


def test_a_still_width_on_a_poster_is_404_without_running_ffmpeg(poster, http, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    _, source, _ = poster
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    still = min(art_urls.ALLOWED_WIDTHS["Primary"] - set(art_urls.widths("movie", "Primary")))
    response = http.get(art_url(MOVIE, "Primary", source, still))
    assert (response.status_code, fake_calls(log)) == (404, [])


def test_a_failed_image_serves_its_original_uncached_at_any_size(poster, http, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    media, source, key = poster
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    large = png_header(1000, 1500) + b"\0" * (3 * 1024 * 1024)
    (media / MOVIE / "primary.png").write_bytes(large)
    renditions.save(renditions.failed_row(MOVIE, "Primary", key, "ffmpeg_failed"))
    response = http.get(art_url(MOVIE, "Primary", source, 240))
    assert (response.status_code, response.headers["cache-control"], len(response.content)) == (200, "no-store", len(large))
    assert fake_calls(log) == []


def test_misses_waiting_on_generation_do_not_hold_up_hits(poster, monkeypatch) -> None:  # noqa: ANN001
    _, source, key = poster
    write_rendition(key, 480, b"W" * 100)
    release = threading.Event()
    waiting = threading.Semaphore(0)

    def slow_miss(*args):  # noqa: ANN002, ANN202, ARG001
        waiting.release()
        release.wait(5)

    monkeypatch.setattr(renditions, "serve_miss", slow_miss)

    async def scenario() -> float:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            async with anyio.create_task_group() as group:
                for _ in range(art.MISS_THREADS):  # a cold scroll: every miss is waiting on generation
                    group.start_soon(client.get, art_url(MOVIE, "Primary", source, 240))
                try:
                    with anyio.fail_after(2):
                        for _ in range(art.MISS_THREADS):
                            await anyio.to_thread.run_sync(waiting.acquire)
                        started = time.perf_counter()
                        response = await client.get(art_url(MOVIE, "Primary", source, 480))
                        elapsed = time.perf_counter() - started
                finally:
                    release.set()
        assert response.status_code == 200
        return elapsed

    assert anyio.run(scenario) < 0.2

