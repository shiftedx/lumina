"""Gallery T1: the rendition pipeline, the background pass, the cache sweep and the on-demand path."""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

from app.config import settings
from app.services import art_urls, renditions
from app.services.artwork import ArtworkService
from art_support import FAKE_ACCENT, FAKE_DOMINANT, fake_calls, png_header, sample_images, use_fake_ffmpeg

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
REPO = Path(__file__).resolve().parents[2]
KEY = "ab" * 32


def uid(n: int) -> str:
    return str(uuid.UUID(int=n))


@pytest.fixture(autouse=True)
def fresh_urls(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(art_urls, "_secret", None)
    monkeypatch.setattr(art_urls, "_extension", "webp")
    monkeypatch.setattr(art_urls, "_pending", art_urls.OrderedDict())
    monkeypatch.setattr(art_urls, "on_enqueue", None)
    renditions.has_libwebp.cache_clear()


@pytest.fixture(scope="module")
def sample_root(tmp_path_factory) -> Path:  # noqa: ANN001
    """The 24 sample images, made once per module (about 3 s); request it only from needs_ffmpeg tests."""
    root = tmp_path_factory.mktemp("bench")
    sample_images(root / "art")
    return root


def _webp(chunk: bytes, body: bytes) -> bytes:
    return b"RIFF\x00\x00\x00\x00WEBP" + chunk + len(body).to_bytes(4, "little") + body


def test_image_size_reads_png_webp_and_jpeg_headers_without_decoding() -> None:
    assert renditions.image_size(png_header(1000, 1500)) == (1000, 1500)
    assert renditions.image_size(_webp(b"VP8X", b"\x00" * 4 + (1919).to_bytes(3, "little") + (1079).to_bytes(3, "little"))) == (1920, 1080)
    assert renditions.image_size(_webp(b"VP8L", b"\x2f" + (799 | (309 << 14)).to_bytes(4, "little"))) == (800, 310)
    assert renditions.image_size(_webp(b"VP8 ", b"\x00\x00\x00\x9d\x01\x2a" + (400).to_bytes(2, "little") + (225).to_bytes(2, "little"))) == (400, 225)
    jpeg = b"\xff\xd8" + b"\xff\xe0\x00\x04ab" + b"\xff\xc0\x00\x0b\x08" + (720).to_bytes(2, "big") + (1280).to_bytes(2, "big") + b"\x03"
    assert renditions.image_size(jpeg) == (1280, 720)
    for junk in (b"", b"not an image", b"\xff\xd8\x00\x00" + b"\x00" * 20, b"RIFF\x00\x00\x00\x00WEBPVP8Z" + b"\x00" * 20):
        assert renditions.image_size(junk) is None, junk


def test_colours_are_the_mean_and_the_most_vivid_pixel() -> None:
    grey = bytes([90, 90, 90]) * 64
    assert renditions.colours(grey) == ("#5a5a5a", "#5a5a5a")  # no pixel qualifies: accent = dominant
    one_red = bytes([128, 128, 128]) * 63 + bytes([200, 40, 40])
    assert renditions.colours(one_red) == (FAKE_DOMINANT, FAKE_ACCENT)
    too_dark = bytes([128, 128, 128]) * 63 + bytes([40, 0, 0])  # v < .25 never counts
    assert renditions.colours(too_dark)[1] == renditions.colours(too_dark)[0]


def test_render_makes_every_product_in_one_niced_single_thread_run(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    niced: list[tuple[int, int, int]] = []
    monkeypatch.setattr(renditions.os, "setpriority", lambda which, who, prio: niced.append((which, who, prio)))
    data = png_header(1000, 1500)
    rendered = renditions.render(data, "image/png", title_type="movie", image_type="Primary", timeout=10, ffmpeg=renditions.media_tool(None, "ffmpeg"))
    calls = fake_calls(log)
    assert len(calls) == 1 and calls[0]["stdin"] == len(data)
    argv = calls[0]["argv"]
    assert niced == [(os.PRIO_PROCESS, niced[0][1], 19)] and niced[0][1] > 0
    assert "-nostdin" not in argv
    for pair in (["-protocol_whitelist", "pipe"], ["-f", "png_pipe"], ["-max_pixels", "80000000"], ["-i", "pipe:0"], ["-filter_threads", "1"]):
        assert any(argv[i:i + 2] == pair for i in range(len(argv))), pair
    assert argv.count("-threads") == 1 + 5  # decoder plus each of the five outputs (240, 480, two previews, colours)
    outputs = [token for token in argv if "/.staging/" in token]
    assert all(Path(token).parent == rendered.staging for token in outputs) and len(outputs) == 5
    assert str(tmp_path) not in " ".join(token for token in argv if token not in outputs and not token.endswith("fake-ffmpeg"))
    assert sorted(rendered.files) == [240, 480] and all(path.is_file() for path in rendered.files.values())
    assert (rendered.ext, rendered.width, rendered.height) == ("webp", 1000, 1500)
    assert (rendered.preview, rendered.preview_type) == (b"p" * 300, "image/webp")  # q30 was 700 bytes, so q15
    assert (rendered.dominant, rendered.accent) == (FAKE_DOMINANT, FAKE_ACCENT)
    assert stat.S_IMODE(renditions.renditions_root().stat().st_mode) == 0o700


def test_logos_get_no_preview_or_colours_and_stills_no_colours(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    ffmpeg = renditions.media_tool(None, "ffmpeg")
    logo = renditions.render(png_header(800, 310), "image/png", title_type="movie", image_type="Logo", timeout=10, ffmpeg=ffmpeg)
    assert (sorted(logo.files), logo.preview, logo.dominant, logo.accent) == ([600], None, None, None)
    still = renditions.render(png_header(1280, 720), "image/png", title_type="episode", image_type="Primary", timeout=10, ffmpeg=ffmpeg)
    assert (sorted(still.files), still.preview is not None, still.dominant) == ([400], True, None)
    backdrop = renditions.render(png_header(1920, 1080), "image/png", title_type="series", image_type="Backdrop", timeout=10, ffmpeg=ffmpeg)
    assert (sorted(backdrop.files), backdrop.dominant) == ([960, 1920], FAKE_DOMINANT)


@pytest.mark.parametrize(("mode", "data", "content_type", "image_type", "ext", "reason"), [
    ("fail", png_header(1000, 1500), "image/png", "Primary", "webp", "ffmpeg_failed"),
    ("oversize", png_header(1000, 1500), "image/png", "Primary", "webp", "oversize_output"),
    ("hang", png_header(1000, 1500), "image/png", "Primary", "webp", "timeout"),
    ("ok", b"not an image at all", "image/png", "Primary", "webp", "unreadable"),
    ("ok", png_header(1000, 1500), "image/avif", "Primary", "webp", "unsupported_format"),
    ("ok", png_header(10_000, 9_000), "image/png", "Primary", "webp", "unsupported_format"),  # over -max_pixels
    ("ok", png_header(800, 310), "image/png", "Logo", "jpg", "unsupported_format"),  # JPEG would drop the alpha
])
def test_render_failures_have_stable_reasons_and_leave_no_staging(tmp_path, monkeypatch, mode, data, content_type, image_type, ext, reason) -> None:  # noqa: ANN001, PLR0913
    use_fake_ffmpeg(tmp_path, monkeypatch, mode)
    with pytest.raises(renditions.RenditionError) as raised:
        renditions.render(data, content_type, title_type="movie", image_type=image_type, timeout=0.5, ext=ext, ffmpeg=renditions.media_tool(None, "ffmpeg"))
    assert raised.value.reason == reason
    staging = renditions.staging_root()
    assert not staging.exists() or not any(staging.iterdir())


def test_install_moves_renditions_into_private_shards_and_rows_carry_the_facts(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    rendered = renditions.render(png_header(1000, 1500), "image/png", title_type="movie", image_type="Primary", timeout=10, ffmpeg=renditions.media_tool(None, "ffmpeg"))
    renditions.install(KEY, rendered)
    for width in (240, 480):
        target = art_urls.rendition_file(KEY, width, "webp")
        assert target.read_bytes() == b"r" * 64
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert not rendered.staging.exists()
    row = renditions.ready_row("t", "Primary", KEY, rendered)
    assert (row.state, row.source_key, row.width, row.height, row.preview_type, row.dominant, row.error) == ("ready", KEY, 1000, 1500, "image/webp", FAKE_DOMINANT, None)
    assert (renditions.failed_row("t", "Primary", KEY, "timeout").state, renditions.failed_row("t", "Primary", KEY, "unsupported_format").state) == ("failed", "unsupported")


def test_the_remote_artwork_evictor_leaves_renditions_alone(tmp_path) -> None:  # noqa: ANN001
    kept = art_urls.rendition_file(KEY, 240, "webp")
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"x")
    (renditions.staging_root() / "abc").mkdir(parents=True)
    ArtworkService(remote_fetcher=object(), cache_root=settings.data_dir / "artwork-cache")  # its constructor evicts
    assert kept.read_bytes() == b"x" and (renditions.staging_root() / "abc").is_dir()


@needs_ffmpeg
def test_real_ffmpeg_makes_every_product_and_never_upscales(sample_root, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    ffmpeg = shutil.which("ffmpeg")
    ext = "webp" if renditions.has_libwebp(ffmpeg) else "jpg"
    monkeypatch.setattr(art_urls, "_extension", ext)
    poster = (sample_root / "art" / "poster-0.jpg").read_bytes()
    rendered = renditions.render(poster, "image/jpeg", title_type="movie", image_type="Primary", timeout=20, ffmpeg=ffmpeg)
    assert rendered.ext == ext and (rendered.width, rendered.height) == (1000, 1500)
    assert renditions.image_size(rendered.files[240].read_bytes()) == (240, 360)
    assert rendered.preview is None or len(rendered.preview) <= 600
    assert rendered.dominant.startswith("#") and len(rendered.dominant) == 7
    renditions.discard(rendered)
    small = tmp_path / "small.jpg"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc2=size=200x300", "-frames:v", "1", str(small)], check=True, timeout=60)
    tiny = renditions.render(small.read_bytes(), "image/jpeg", title_type="movie", image_type="Primary", timeout=20, ffmpeg=ffmpeg)
    assert renditions.image_size(tiny.files[240].read_bytes())[0] == 200 and renditions.image_size(tiny.files[480].read_bytes())[0] == 200
    renditions.discard(tiny)


@needs_ffmpeg
def test_the_benchmark_reports_every_kind_and_an_estimate(sample_root) -> None:  # noqa: ANN001
    spec = importlib.util.spec_from_file_location("rendition_bench", REPO / "scripts" / "perf" / "rendition_bench.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    result = bench.bench(1, shutil.which("ffmpeg"), sample_root)
    assert set(result["kinds"]) == {"poster", "backdrop", "logo", "still"}
    assert result["estimate_hours"] > 0 and result["extension"] in ("webp", "jpg")
    assert set(result["kinds"]["backdrop"]["bytes_mean"]) == {"960", "1920"}


import hashlib
import threading
import time

from sqlalchemy import select

from app import db as db_module
from app.models import MediaTitle, StorageRoot, TitleArtwork
from app.services import library_import, tmdb
from app.services.titles import title_image_source
from art_support import ART_ROOT_ID, add_art_title, give_art, seed_art_root
from discovery_support import add_progress, add_title, add_version
from test_v1_scan import client_for, library, register_root, write  # noqa: F401  (library is a fixture)

A, B, C, D = uid(0xA1), uid(0xB1), uid(0xC1), uid(0xD1)


@pytest.fixture
def media(tmp_path) -> Path:  # noqa: ANN001
    root = tmp_path.resolve() / "media"
    seed_art_root(root)
    return root


def _row(title_id: str, image_type: str = "Primary") -> TitleArtwork | None:
    with db_module.SessionLocal() as session:
        return session.get(TitleArtwork, (title_id, image_type))


def test_run_once_prepares_one_image_installs_it_and_writes_a_ready_row(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    worker = renditions.ArtworkRenditions()
    assert worker.run_once() is True
    row = _row(A)
    assert (row.state, row.width, row.height, row.dominant, row.preview) == ("ready", 1000, 1500, FAKE_DOMINANT, b"p" * 300)
    assert art_urls.rendition_file(row.source_key, 240, "webp").read_bytes() == b"r" * 64
    assert worker.run_once() is False  # nothing left


def test_scan_orders_titles_then_watched_series_stills_then_the_rest(media) -> None:  # noqa: ANN001
    add_art_title(media, A, images=("Primary", "Backdrop", "Logo"), days=0)
    add_art_title(media, B, days=5)  # newer movie: its poster goes first
    with db_module.SessionLocal() as session:
        for series, season in ((uid(0x51), uid(0x511)), (uid(0x52), uid(0x521))):
            add_title(session, series, "series", "Show")
            add_title(session, season, "season", "Season 1", parent_id=series, index=1)
        session.commit()
    add_art_title(media, uid(0x5111), "episode", parent_id=uid(0x511))  # a series someone is watching
    add_art_title(media, uid(0x5211), "episode", parent_id=uid(0x521))
    add_art_title(media, C, "season", parent_id=uid(0x52))
    with db_module.SessionLocal() as session:
        add_version(session, "busy-v", uid(0x5111), kind="episode")
        add_progress(session, "u1", "busy-v")
        session.commit()
    with db_module.SessionLocal() as session:
        found = renditions.scan(session)
    assert found.order == [
        (B, "Primary"), (A, "Primary"), (A, "Backdrop"), (A, "Logo"),
        (uid(0x5111), "Primary"), (uid(0x5211), "Primary"), (C, "Primary"),
    ]
    assert (found.total, found.prepared) == (7, 0)


def test_on_demand_requests_then_rescanned_titles_jump_the_queue(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    for days, title_id in enumerate((A, B, C)):
        add_art_title(media, title_id, days=days)  # C is newest, so the plain order would start with it
    worker = renditions.ArtworkRenditions()
    seen: list[tuple[str, str]] = []
    real = worker._prepare
    monkeypatch.setattr(worker, "_prepare", lambda title_id, image_type: (seen.append((title_id, image_type)), real(title_id, image_type)))
    art_urls.enqueue([(A, "Primary")])
    worker.wake(priority_titles=[B])
    while worker.run_once():
        pass
    assert seen == [(A, "Primary"), (B, "Primary"), (B, "Backdrop"), (B, "Logo"), (C, "Primary")]
    assert {_row(title_id).state for title_id in (A, B, C)} == {"ready"}


def test_failures_are_recorded_and_not_retried_until_the_art_changes(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch, "fail")
    add_art_title(media, A)
    worker = renditions.ArtworkRenditions()
    assert worker.run_once() is True
    assert (_row(A).state, _row(A).error) == ("failed", "ffmpeg_failed")
    assert worker.run_once() is False
    use_fake_ffmpeg(tmp_path, monkeypatch, "ok")
    with db_module.SessionLocal() as session:
        give_art(session.get(MediaTitle, A), "Primary", path=f"{A}/primary.png", tag="changed")  # a rescan saw a new file
        session.commit()
    assert worker.run_once() is True and _row(A).state == "ready"


def test_an_offline_root_is_skipped_not_marked_failed(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    with db_module.SessionLocal() as session:
        session.get(StorageRoot, ART_ROOT_ID).observation = {"state": "offline"}
        session.commit()
    worker = renditions.ArtworkRenditions()
    assert worker.run_once() is True  # looked at, skipped
    assert _row(A) is None and fake_calls(log) == []
    assert worker.run_once() is False  # skipped until the next idle wait
    with db_module.SessionLocal() as session:
        session.get(StorageRoot, ART_ROOT_ID).observation = {"state": "available"}
        session.commit()
    worker._skipped.clear()  # what the loop does before each idle wait
    assert worker.run_once() is True and _row(A).state == "ready"


def test_an_unmounted_root_still_stored_as_available_is_skipped_not_marked_failed(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    unplugged = media.with_name("unplugged")
    media.rename(unplugged)  # the NAS drops off; nothing re-probed, so the stored observation still says available
    worker = renditions.ArtworkRenditions()
    assert worker.run_once() is True
    assert _row(A) is None and fake_calls(log) == []
    unplugged.rename(media)
    worker._skipped.clear()
    assert worker.run_once() is True and _row(A).state == "ready"


def test_a_missing_file_on_a_mounted_root_is_unreadable(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    (media / A / "primary.png").unlink()
    assert renditions.ArtworkRenditions().run_once() is True
    assert (_row(A).state, _row(A).error) == ("failed", "unreadable")


def test_stop_kills_a_running_ffmpeg_and_saves_nothing(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    log = use_fake_ffmpeg(tmp_path, monkeypatch, "hang")
    add_art_title(media, A)
    monkeypatch.setattr(library_import, "after_import_hooks", [])
    worker = renditions.ArtworkRenditions()
    worker.start()
    deadline = time.monotonic() + 10
    while not fake_calls(log) and time.monotonic() < deadline:
        time.sleep(0.05)
    began = time.monotonic()
    worker.stop()
    assert time.monotonic() - began < 5 and not worker.running
    assert _row(A) is None


def test_the_loop_backs_off_briefly_after_an_error(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(renditions, "ERROR_BACKOFF_SECONDS", 0.05)
    monkeypatch.setattr(renditions.sessions, "video_encodes", lambda: 0)
    worker = renditions.ArtworkRenditions()
    calls: list[int] = []

    def flaky() -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return False

    monkeypatch.setattr(worker, "run_once", flaky)
    monkeypatch.setattr(worker, "_housekeeping", lambda: None)
    worker._thread = threading.Thread(target=worker._loop, daemon=True)
    worker._thread.start()
    deadline = time.monotonic() + 5
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    worker._stop.set()
    worker._wake.set()
    worker._thread.join(5)
    assert len(calls) >= 2  # retried after the back-off, not after IDLE_SECONDS


def test_the_lifespan_starts_the_pass_off_the_event_loop(library, monkeypatch) -> None:  # noqa: ANN001
    import asyncio

    seen: list[bool] = []

    def record() -> None:
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)

    monkeypatch.setattr(renditions.renditions, "start", record)
    with client_for("admin"):
        pass
    assert seen == [False]  # ffmpeg -encoders and the staging sweep never block the loop


def test_tmdb_art_is_prepared_only_once_its_pinned_copy_is_on_disk(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    with db_module.SessionLocal() as session:
        add_title(session, A, "movie", "Remote").images = {"Primary": {"tmdb": "/abc123.jpg"}}
        session.commit()
    worker = renditions.ArtworkRenditions()
    assert worker.run_once() is True and _row(A) is None and fake_calls(log) == []  # never fetched from the network
    pinned = settings.data_dir / "metadata-art" / (hashlib.sha256(tmdb.image_url("/abc123.jpg", tmdb.IMAGE_SIZES["Primary"]).encode()).hexdigest() + ".jpg")
    pinned.parent.mkdir(parents=True)
    pinned.write_bytes(png_header(500, 750))
    worker._skipped.clear()
    assert worker.run_once() is True and _row(A).state == "ready"


def test_start_wires_the_hooks_clears_staging_and_uses_jpeg_without_libwebp(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch, "no_webp")
    monkeypatch.setattr(library_import, "after_import_hooks", [])
    leftover = renditions.staging_root() / "deadbeef"
    leftover.mkdir(parents=True)
    hour_ago = time.time() - renditions.STAGING_MAX_AGE_SECONDS - 60
    os.utime(leftover, (hour_ago, hour_ago))
    in_flight = renditions.staging_root() / "cafef00d"  # an on-demand render running right now
    in_flight.mkdir()
    worker = renditions.ArtworkRenditions()
    worker.start()
    try:
        assert art_urls.extension() == "jpg"
        assert worker.after_import in library_import.after_import_hooks and art_urls.on_enqueue is not None
        assert not leftover.exists() and in_flight.is_dir() and worker.running
    finally:
        worker.stop()
    assert not worker.running and art_urls.extension() == "webp" and art_urls.on_enqueue is None
    assert worker.after_import not in library_import.after_import_hooks


def test_the_pass_pauses_while_a_video_transcode_runs(media, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    monkeypatch.setattr(library_import, "after_import_hooks", [])
    monkeypatch.setattr(renditions, "PAUSE_POLL_SECONDS", 0.02)
    encodes = {"running": 1}
    monkeypatch.setattr(renditions.sessions, "video_encodes", lambda: encodes["running"])
    worker = renditions.ArtworkRenditions()
    worker.start()
    try:
        time.sleep(0.3)
        assert worker.paused and fake_calls(log) == []
        encodes["running"] = 0
        deadline = time.monotonic() + 10
        while not fake_calls(log) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(fake_calls(log)) == 1 and not worker.paused
    finally:
        worker.stop()


def test_an_import_wakes_the_pass_with_titles_whose_art_changed(library, monkeypatch) -> None:  # noqa: ANN001
    woken: list[set[str]] = []
    monkeypatch.setattr(renditions.renditions, "wake", lambda priority_titles=(): woken.append(set(priority_titles)))
    write(library / "Heat (1995)" / "Heat (1995).mkv", b"heat")
    write(library / "Heat (1995)" / "poster.jpg", png_header(1000, 1500))
    with client_for("admin") as admin:
        root_id = register_root(admin, library)
        assert admin.post("/api/admin/imports", json={"root_id": root_id}).status_code == 202
        with db_module.SessionLocal() as session:
            heat = session.scalars(select(MediaTitle).where(MediaTitle.type == "movie")).one()
        assert heat.images["Primary"]["path"].endswith("poster.jpg")
        assert woken == [{heat.id}]
        woken.clear()
        assert admin.post("/api/admin/imports", json={"root_id": root_id}).status_code == 202
        assert woken == []  # an unchanged rescan wakes nobody
        write(library / "Heat (1995)" / "poster.jpg", png_header(2000, 3000) + b"new")
        assert admin.post("/api/admin/imports", json={"root_id": root_id}).status_code == 202
        assert woken == [{heat.id}]


def test_use_host_format_picks_jpeg_only_when_ffmpeg_lacks_libwebp(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    assert renditions.use_host_format() == "webp" and art_urls.extension() == "webp"
    use_fake_ffmpeg(tmp_path, monkeypatch, "no_webp")
    renditions.has_libwebp.cache_clear()  # same fake path, different encoders: forget the cached probe
    assert renditions.use_host_format() == "jpg" and art_urls.extension() == "jpg"
    art_urls.set_extension("webp")
    assert renditions.use_host_format(renditions.media_tool(None, "ffmpeg")) == "jpg"  # scripts without the app database pass the binary


def test_the_webp_probe_runs_once_per_ffmpeg(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch, "no_webp")
    probes: list[list[str]] = []
    real_run = subprocess.run
    monkeypatch.setattr(renditions.subprocess, "run", lambda argv, **kw: (probes.append(argv), real_run(argv, **kw))[1])
    assert renditions.use_host_format() == "jpg"
    art_urls.set_extension("webp")
    assert renditions.use_host_format() == "jpg"
    assert len(probes) == 1


def test_local_art_without_a_storage_root_is_counted_and_logged_once(media, tmp_path, monkeypatch, caplog) -> None:  # noqa: ANN001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    with db_module.SessionLocal() as session:
        session.get(MediaTitle, A).root_id = None
        session.commit()
    with db_module.SessionLocal() as session:
        found = renditions.scan(session)
    assert (found.total, found.prepared, found.failure_reasons) == (1, 0, {"no_storage_root": 1})
    worker = renditions.ArtworkRenditions()
    with caplog.at_level("DEBUG", logger=renditions.__name__):
        assert worker.run_once() is True
        worker._skipped.clear()
        assert worker.run_once() is True
    assert _row(A) is None and fake_calls(log) == []
    assert [record.getMessage() for record in caplog.records if "no storage root" in record.getMessage()] == [f"Artwork for title {A} has a local path but no storage root; skipped."]


def test_the_artwork_pass_setting_keeps_the_pass_from_starting(library, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    use_fake_ffmpeg(tmp_path, monkeypatch, "no_webp")
    monkeypatch.setattr(settings, "artwork_pass", False)  # LUMINA_ARTWORK_PASS=off, as the realstack launcher sets it
    with client_for("admin"):
        assert not renditions.renditions.running
        assert art_urls.extension() == "jpg"  # on-demand renders still use the host's format
    monkeypatch.setattr(settings, "artwork_pass", True)
    with client_for("admin"):
        assert renditions.renditions.running
    assert not renditions.renditions.running


def _aged(path: Path, days: float) -> None:
    moment = time.time() - days * 86400
    os.utime(path, (moment, moment))


def test_the_sweep_drops_rows_of_deleted_titles_orphan_files_and_the_least_recently_used(media) -> None:  # noqa: ANN001
    add_art_title(media, A)
    add_art_title(media, B)
    with db_module.SessionLocal() as session:
        from art_support import artwork_row, write_rendition

        key_a = artwork_row(session, session.get(MediaTitle, A)).source_key
        key_b = artwork_row(session, session.get(MediaTitle, B)).source_key
        session.commit()
        session.delete(session.get(MediaTitle, B))
        session.commit()
    old = write_rendition(key_a, 240, b"a" * 100)
    new = write_rendition(key_a, 480, b"a" * 100)
    orphan = write_rendition("f" * 64, 240, b"o" * 100)
    gone = write_rendition(key_b, 240, b"b" * 100)
    for path, days in ((old, 3), (orphan, 1), (gone, 1)):
        _aged(path, days)
    renditions.ArtworkRenditions().sweep(cap=150)
    assert _row(B) is None
    assert (old.exists(), new.exists(), orphan.exists(), gone.exists()) == (False, True, False, False)


def test_an_evicted_rendition_puts_its_art_back_in_the_pass_order(media) -> None:  # noqa: ANN001
    from art_support import write_rendition

    add_art_title(media, A)
    add_art_title(media, B)
    with db_module.SessionLocal() as session:
        from art_support import artwork_row

        key_a = artwork_row(session, session.get(MediaTitle, A)).source_key
        key_b = artwork_row(session, session.get(MediaTitle, B)).source_key
        session.commit()
        assert renditions.scan(session).order == []
    cold = write_rendition(key_a, 240, b"a" * 100)
    warm = write_rendition(key_b, 240, b"b" * 100)
    _aged(cold, 3)
    renditions.ArtworkRenditions().sweep(cap=150)
    assert (cold.exists(), warm.exists()) == (False, True)
    assert _row(A) is None and _row(B) is not None  # A is prepared again rather than left a permanent miss
    with db_module.SessionLocal() as session:
        assert renditions.scan(session).order == [(A, "Primary")]


def test_the_rendition_cache_is_capped_at_5_gib() -> None:
    assert renditions.CACHE_CAP_BYTES == 5 * 1024**3


def test_a_rendition_installed_after_the_sweep_read_the_rows_survives_it(media, tmp_path) -> None:  # noqa: ANN001
    staging = tmp_path / "staged"
    staging.mkdir()
    staged = staging / "w240.webp"
    staged.write_bytes(b"r" * 64)
    _aged(staged, 1)  # rendered a while ago; its row is saved only after the install
    rendered = renditions.Rendered(ext="webp", files={240: staged}, width=1000, height=1500, preview=None, preview_type=None,
                                   dominant=None, accent=None, staging=staging)
    walk = renditions.rendition_files

    def install_mid_sweep():  # noqa: ANN202
        renditions.install("e" * 64, rendered)  # a request thread, after the live keys were read
        yield from walk()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(renditions, "rendition_files", install_mid_sweep)
        renditions.ArtworkRenditions().sweep()
    assert art_urls.rendition_file("e" * 64, 240, "webp").is_file()

def test_reading_a_rendition_bumps_its_lru_clock_at_most_daily(tmp_path) -> None:  # noqa: ANN001
    from art_support import write_rendition

    path = write_rendition(KEY, 240, b"x" * 10)
    _aged(path, 2)
    data, mtime = renditions.read_rendition(path)
    assert data == b"x" * 10 and time.time() - mtime < 60 and time.time() - path.stat().st_mtime < 60
    _aged(path, 0.5)
    before = path.stat().st_mtime
    assert renditions.read_rendition(path)[1] == before and path.stat().st_mtime == before
    assert renditions.read_rendition(tmp_path / "missing.webp") is None
    assert renditions.touch(path, before) == before
    assert renditions.touch(path, time.time() - 2 * 86400) > time.time() - 60


import threading

from app.main import artwork as app_artwork


def _drain() -> None:
    for future in list(renditions._inflight.values()):
        future.result(timeout=10)


def _key(title_id: str, image_type: str = "Primary") -> str:
    with db_module.SessionLocal() as session:
        return art_urls.source_key(title_id, image_type, title_image_source(session.get(MediaTitle, title_id), image_type))


@pytest.fixture
def fresh_serving(monkeypatch):  # noqa: ANN001, ANN201
    import collections

    monkeypatch.setattr(renditions, "_serving", collections.Counter())
    monkeypatch.setattr(renditions, "_slots", threading.BoundedSemaphore(renditions.ON_DEMAND_SLOTS))
    monkeypatch.setattr(renditions, "_inflight", {})  # a done-callback of an earlier test lands in the old ones
    yield
    _drain()


def test_a_miss_is_generated_within_budget_installed_and_recorded(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    key = _key(A)
    served = renditions.serve_miss(A, "Primary", key, 480, "webp", app_artwork)
    assert served == renditions.Served("image/webp", b"r" * 64)
    assert art_urls.rendition_file(key, 240, "webp").is_file() and _row(A).state == "ready"
    assert renditions.serving().generated == 1


def test_a_miss_for_art_that_is_gone_or_changed_is_not_found(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    assert renditions.serve_miss(A, "Primary", "0" * 64, 240, "webp", app_artwork) is None
    assert renditions.serve_miss(B, "Primary", _key(A), 240, "webp", app_artwork) is None
    assert renditions.serve_miss(A, "Primary", _key(A), 400, "webp", app_artwork) is None  # a still width on a poster
    assert fake_calls(log) == []  # a still width on a poster is refused before ffmpeg


def test_a_failed_generation_serves_the_original_uncached_and_is_not_retried(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    log = use_fake_ffmpeg(tmp_path, monkeypatch, "fail")
    add_art_title(media, A)
    original = (media / A / "primary.png").read_bytes()
    first = renditions.serve_miss(A, "Primary", _key(A), 240, "webp", app_artwork)
    assert first == renditions.Served("image/png", original, cacheable=False)
    assert (_row(A).state, _row(A).error) == ("failed", "ffmpeg_failed")
    assert renditions.serve_miss(A, "Primary", _key(A), 240, "webp", app_artwork) == first
    assert len(fake_calls(log)) == 1
    assert (renditions.serving().failures, renditions.serving().fallback_original) == (1, 2)


def test_over_budget_serves_a_small_original_enqueues_and_keeps_going(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    use_fake_ffmpeg(tmp_path, monkeypatch, "hang")
    monkeypatch.setattr(renditions, "ON_DEMAND_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(renditions, "ON_DEMAND_TIMEOUT_SECONDS", 1.0)
    add_art_title(media, A)
    served = renditions.serve_miss(A, "Primary", _key(A), 240, "webp", app_artwork)
    assert served.cacheable is False and served.content_type == "image/png"
    assert art_urls.take(5) == [(A, "Primary")]
    _drain()
    assert _row(A) is None  # an on-demand timeout is left to the pass, never recorded as failed


def test_over_budget_with_a_large_original_answers_preparing(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    use_fake_ffmpeg(tmp_path, monkeypatch, "hang")
    monkeypatch.setattr(renditions, "ON_DEMAND_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(renditions, "ON_DEMAND_TIMEOUT_SECONDS", 1.0)
    add_art_title(media, A)
    (media / A / "primary.png").write_bytes(png_header(1000, 1500) + b"\0" * (2 * 1024 * 1024))
    with pytest.raises(renditions.Preparing):
        renditions.serve_miss(A, "Primary", _key(A), 240, "webp", app_artwork)
    assert renditions.serving().fallback_unavailable == 1


def test_on_demand_generation_is_bounded_to_two_processes(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    log = use_fake_ffmpeg(tmp_path, monkeypatch, "hang")
    monkeypatch.setattr(renditions, "ON_DEMAND_BUDGET_SECONDS", 0.4)
    monkeypatch.setattr(renditions, "ON_DEMAND_TIMEOUT_SECONDS", 1.5)
    for title_id in (A, B, C):
        add_art_title(media, title_id)
    results = {}
    threads = [threading.Thread(target=lambda t=t: results.setdefault(t, renditions.serve_miss(t, "Primary", _key(t), 240, "webp", app_artwork))) for t in (A, B, C)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(results) == 3
    assert len(fake_calls(log)) == 2  # the third found no slot within 50 ms and fell back at once
    assert all(served.cacheable is False for served in results.values())


def test_concurrent_misses_for_one_image_share_one_generation(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    key = _key(A)
    results = []
    together = threading.Barrier(3)  # all three ask before the first run (>= 50 ms of fake ffmpeg) can finish

    def ask(width: int) -> None:
        together.wait()
        results.append(renditions.serve_miss(A, "Primary", key, width, "webp", app_artwork))

    threads = [threading.Thread(target=ask, args=(w,)) for w in (240, 480, 240)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(results) == 3
    assert len(fake_calls(log)) == 1 and all(served.cacheable for served in results)


def test_a_failed_install_still_serves_the_generated_bytes(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)

    def disk_full(key, rendered):  # noqa: ANN001, ANN202, ARG001
        renditions.discard(rendered)
        raise OSError("No space left on device")

    monkeypatch.setattr(renditions, "install", disk_full)
    served = renditions.serve_miss(A, "Primary", _key(A), 240, "webp", app_artwork)
    assert served == renditions.Served("image/webp", b"r" * 64)
    assert not art_urls.rendition_file(_key(A), 240, "webp").exists()


def test_an_offline_root_starts_no_on_demand_run_and_writes_no_row(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)
    with db_module.SessionLocal() as session:
        session.get(StorageRoot, ART_ROOT_ID).observation = {"state": "offline"}
        session.commit()
    renditions.serve_miss(A, "Primary", _key(A), 240, "webp", app_artwork)
    assert _row(A) is None and fake_calls(log) == []
    assert art_urls.take(5) == [(A, "Primary")]  # the pass tries again once the drive is back


def test_an_unexpected_error_after_rendering_falls_back_instead_of_raising(media, tmp_path, monkeypatch, fresh_serving) -> None:  # noqa: ANN001, ARG001
    use_fake_ffmpeg(tmp_path, monkeypatch)
    add_art_title(media, A)

    def broken(row):  # noqa: ANN001, ANN202, ARG001
        raise RuntimeError("database is gone")

    monkeypatch.setattr(renditions, "save", broken)
    served = renditions.serve_miss(A, "Primary", _key(A), 240, "webp", app_artwork)
    assert served.cacheable is False and renditions.serving().failures == 1


def test_the_original_of_a_title_deleted_mid_request_is_not_found(media) -> None:  # noqa: ANN001, ARG001
    assert renditions._original(D, "Primary", app_artwork, any_size=True) is None
