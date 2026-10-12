"""Upload sniff, dimension caps, the bounded ffmpeg re-encode, the SQLite store and upload GC."""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import MediaTitle, TitleEdit, TitleUpload
from app.services import renditions
from app.services import title_images as ti
from art_support import png_header
from discovery_support import add_title

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x00" * 20


def jpeg_header(width: int, height: int) -> bytes:
    """SOI + SOF0: enough JPEG for sniff and renditions.image_size; nothing decodes it."""
    return b"\xff\xd8\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03" + b"\x00" * 24


@pytest.mark.parametrize("data, expected", [
    (JPEG, "image/jpeg"), (png_header(800, 600), "image/png"), (WEBP, "image/webp"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None), (b"<?xml version='1.0'?><svg/>", None),
    (b"GIF89a" + b"\x00" * 30, None), (b"\x00\x00\x00\x1cftypavif" + b"\x00" * 30, None),
    (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 30, None), (b"II*\x00" + b"\x00" * 30, None),   # TIFF
    (b"BM" + b"\x00" * 30, None), (b"\x00\x00\x01\x00" + b"\x00" * 30, None),                # BMP, ICO
    (b"%PDF-1.7" + b"\x00" * 30, None), (b"<html><body>", None), (b"", None),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24, None),   # PNG signature without IHDR
])
def test_sniff_judges_magic_bytes_only(data, expected) -> None:  # noqa: ANN001
    assert ti.sniff(data) == expected


@pytest.mark.parametrize("w, h, code", [(63, 800, 422), (800, 63, 422), (1, 1, 422), (8001, 100, 422), (100, 8001, 422), (7000, 6000, 422)])  # 42 MP last
def test_dimensions_are_checked_from_the_header(w, h, code) -> None:  # noqa: ANN001
    with pytest.raises(ti.UploadError) as raised:
        ti.check_dimensions(png_header(w, h))
    assert (raised.value.status, raised.value.detail) == (code, "image_dimensions")


def test_dimension_limits_are_inclusive() -> None:
    assert ti.check_dimensions(png_header(64, 64)) == (64, 64)
    assert ti.check_dimensions(png_header(8000, 5000)) == (8000, 5000)        # exactly 40 MP
    with pytest.raises(ti.UploadError):
        ti.check_dimensions(png_header(8000, 5001))


def test_an_unreadable_header_is_unsupported_not_a_crash() -> None:
    with pytest.raises(ti.UploadError) as raised:
        ti.check_dimensions(b"\xff\xd8\xff\xe0" + b"\x00" * 3)               # truncated JPEG
    assert (raised.value.status, raised.value.detail) == (415, "unsupported_image")


def test_an_animated_webp_is_refused_with_its_own_reason() -> None:
    # RIFF/WEBP, VP8X with the animation flag, a 128 x 96 canvas, then ANIM (as libwebp writes it).
    animated = b"RIFF\x84\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00\x02\x00\x00\x00\x7f\x00\x00\x5f\x00\x00ANIM" + b"\x00" * 16
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(animated, "Primary", ffmpeg="/nonexistent/ffmpeg")
    assert (raised.value.status, raised.value.detail) == (415, "animated_image")
    still = animated[:20] + b"\x00" + animated[21:]  # the same header without the flag is judged on its size as before
    assert ti.check_dimensions(still) == (128, 96)


def test_encode_refuses_before_spawning_anything() -> None:
    for data, status in ((b"<svg/>", 415), (png_header(10, 10), 422)):
        with pytest.raises(ti.UploadError) as raised:
            ti.encode(data, "Primary", ffmpeg="/nonexistent/ffmpeg")
        assert raised.value.status == status
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=None)
    assert (raised.value.status, raised.value.detail) == (503, "encoder_unavailable")


# ---- the re-encode, with the real ffmpeg ----

def _lavfi(ffmpeg: str, out, size: str, *extra: str) -> bytes:  # noqa: ANN001
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}",
                    "-frames:v", "1", *extra, str(out)], check=True, timeout=60)
    return out.read_bytes()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):  # noqa: ANN001, ANN201
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    root = tmp_path_factory.mktemp("upload-corpus")
    jpeg = _lavfi(FFMPEG, root / "a.jpg", "400x600")
    exif = b"Exif\x00\x00" + b"GPSLatitude 51.5N GPSLongitude 0.12W" + b"\x00" * 16
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    return {
        "jpeg": jpeg,
        "jpeg_exif": jpeg[:2] + app1 + jpeg[2:],
        "png": _lavfi(FFMPEG, root / "a.png", "400x600"),
        "png_rgba": _lavfi(FFMPEG, root / "rgba.png", "400x160", "-vf", "format=rgba,colorchannelmixer=aa=0.5", "-pix_fmt", "rgba"),
        "jpeg_5000x3000": _lavfi(FFMPEG, root / "big.jpg", "5000x3000"),
        "jpeg_200x300": _lavfi(FFMPEG, root / "small.jpg", "200x300"),
    }


@needs_ffmpeg
def test_re_encode_strips_metadata_and_trailing_bytes(corpus) -> None:  # noqa: ANN001
    assert b"GPSLatitude" in corpus["jpeg_exif"]
    polyglot = corpus["png"] + b"<html><script>alert(1)</script></html>"
    out = ti.encode(polyglot, "Primary", ffmpeg=FFMPEG)
    assert out.content_type == "image/jpeg" and ti.sniff(out.data) == "image/jpeg"
    assert b"<script>" not in out.data and b"GPSLatitude" not in ti.encode(corpus["jpeg_exif"], "Primary", ffmpeg=FFMPEG).data
    assert b"Exif" not in out.data[:64]
    assert (out.width, out.height) == (400, 600) and len(out.sha256) == 64


@needs_ffmpeg
def test_primary_and_backdrop_are_jpeg_and_logo_keeps_alpha(corpus) -> None:  # noqa: ANN001
    assert ti.encode(corpus["png_rgba"], "Backdrop", ffmpeg=FFMPEG).content_type == "image/jpeg"
    logo = ti.encode(corpus["png_rgba"], "Logo", ffmpeg=FFMPEG)
    assert logo.content_type == ("image/webp" if renditions.has_libwebp(FFMPEG) else "image/png")
    assert ti.sniff(logo.data) == logo.content_type


@needs_ffmpeg
def test_large_images_are_scaled_to_fit_3840_and_small_ones_are_not_upscaled(corpus) -> None:  # noqa: ANN001
    big = ti.encode(corpus["jpeg_5000x3000"], "Backdrop", ffmpeg=FFMPEG)
    assert max(big.width, big.height) == 3840 and renditions.image_size(big.data) == (big.width, big.height)
    assert ti.encode(corpus["jpeg_200x300"], "Primary", ffmpeg=FFMPEG).width == 200


@needs_ffmpeg
def test_output_is_resniffed_and_rechecked(monkeypatch, corpus) -> None:  # noqa: ANN001
    monkeypatch.setattr(ti, "_run", lambda *a, **k: b"<svg/>")       # a binary that "succeeds" with junk
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(corpus["png"], "Primary", ffmpeg=FFMPEG)
    assert raised.value.status == 415


# ---- the re-encode, with a fake binary ----

def fake_binary(tmp_path, body: str) -> str:  # noqa: ANN001
    script = tmp_path / "fake-encoder"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return str(script)


def test_every_failure_is_415_and_never_leaks_stderr(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "secret-path-do-not-leak"
    binary = fake_binary(tmp_path, f"cat >/dev/null; echo '{secret}' >&2; echo '{secret}'; exit 1")
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=binary)
    assert (raised.value.status, raised.value.detail) == (415, "unsupported_image")
    assert "secret" not in str(raised.value) and "secret" not in repr(raised.value)


def test_a_missing_binary_is_415(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=str(tmp_path / "missing"))
    assert raised.value.status == 415


def test_timeout_kills_the_encoder(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    pid_file = tmp_path / "pid"
    binary = fake_binary(tmp_path, f"echo $$ > '{pid_file}'; exec sleep 30")
    monkeypatch.setattr(ti, "ENCODE_TIMEOUT_SECONDS", 0.5)
    started = time.monotonic()
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=binary)
    assert raised.value.status == 415 and time.monotonic() - started < 5
    pid = int(pid_file.read_text())
    assert subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0  # gone (and reaped)


def test_output_over_4_mib_is_refused(tmp_path) -> None:  # noqa: ANN001
    binary = fake_binary(tmp_path, "cat >/dev/null; printf '\\377\\330\\377'; head -c 5242880 /dev/zero")
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=binary)
    assert (raised.value.status, raised.value.detail) == (415, "unsupported_image")


def test_a_valid_output_from_the_binary_is_accepted(tmp_path) -> None:  # noqa: ANN001
    out = ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=fake_binary(tmp_path, "exec cat"))
    assert (out.content_type, out.width, out.height, out.data) == ("image/jpeg", 200, 300, jpeg_header(200, 300))


def test_a_second_concurrent_encode_waits_then_503(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    slow = fake_binary(tmp_path, "sleep 1.5; exec cat")
    monkeypatch.setattr(ti, "BUSY_WAIT_SECONDS", 0.3)
    first: list = []
    worker = threading.Thread(target=lambda: first.append(ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=slow)))
    worker.start()
    while ti._ENCODER.acquire(blocking=False):  # noqa: SLF001  # still free: the first worker has not taken the slot yet
        ti._ENCODER.release()  # noqa: SLF001
        time.sleep(0.01)
    started = time.monotonic()
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=slow)
    waited = time.monotonic() - started
    assert (raised.value.status, raised.value.detail) == (503, "encoder_busy") and 0.25 <= waited < 1.2
    worker.join(10)
    assert first and first[0].width == 200
    assert ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=fake_binary(tmp_path, "exec cat")).width == 200  # released


# ---- store and GC ----

NOW = datetime(2026, 10, 2, 12, 0, 0)


def upload(session, sha: str, *, age: timedelta = timedelta(hours=2)) -> None:  # noqa: ANN001
    session.add(TitleUpload(sha256=sha, content_type="image/jpeg", width=200, height=300, data=b"x", created_at=NOW - age))


def remaining(session) -> set[str]:  # noqa: ANN001
    return set(session.scalars(select(TitleUpload.sha256)))


def test_save_is_idempotent_by_sha(db_factory) -> None:  # noqa: ANN001
    encoded = ti.Encoded("a" * 64, "image/jpeg", 200, 300, b"bytes")
    with db_factory.begin() as session:
        ti.save(session, encoded, "user-1")
    with db_factory.begin() as session:
        ti.save(session, encoded, "user-2")
        ti.save(session, encoded, "user-3")
    with db_factory() as session:
        rows = session.scalars(select(TitleUpload)).all()
        assert [(row.sha256, row.created_by, row.data, row.width) for row in rows] == [("a" * 64, "user-1", b"bytes", 200)]


def test_gc_keeps_uploads_referenced_by_a_title(db_factory) -> None:  # noqa: ANN001
    with db_factory.begin() as session:
        title = add_title(session, "t1", "movie", "Movie")
        title.images = {"Primary": {"upload": "p" * 64, "tag": "p" * 16}, "Backdrop.3": {"upload": "b" * 64, "tag": "b" * 16}}
        for sha in ("p" * 64, "b" * 64, "x" * 64):
            upload(session, sha)
    with db_factory.begin() as session:
        assert ti.gc_uploads(session, NOW) == 1
    with db_factory() as session:
        assert remaining(session) == {"p" * 64, "b" * 64}


def test_gc_keeps_uploads_referenced_by_recent_history(db_factory) -> None:  # noqa: ANN001
    with db_factory.begin() as session:
        for sha, days, field, side in (("r" * 64, 10, "images.Primary", "before"), ("a" * 64, 10, "images.Backdrop.2", "after"),
                                       ("o" * 64, 300, "images.Primary", "before"), ("n" * 64, 10, "overview", "before")):
            upload(session, sha)
            session.add(TitleEdit(batch_id="b", kind="image", title_id="t1", field=field, created_at=NOW - timedelta(days=days),
                                  **{side: {"upload": sha, "tag": sha[:16]}}))
    with db_factory.begin() as session:
        assert ti.gc_uploads(session, NOW) == 1
    with db_factory() as session:  # Any surviving history row (365-day retention) keeps its upload for undo
        assert remaining(session) == {"r" * 64, "a" * 64, "o" * 64}


def test_gc_deletes_unreferenced_uploads_older_than_an_hour_only(db_factory) -> None:  # noqa: ANN001
    with db_factory.begin() as session:
        upload(session, "o" * 64, age=timedelta(hours=2))
        upload(session, "y" * 64, age=timedelta(minutes=5))   # the upload-then-write race
    with db_factory.begin() as session:
        assert ti.gc_uploads(session, NOW) == 1
    with db_factory() as session:
        assert remaining(session) == {"y" * 64}


def test_gc_ignores_tombstones_and_other_entry_shapes(db_factory) -> None:  # noqa: ANN001
    with db_factory.begin() as session:
        title = add_title(session, "t1", "movie", "Movie")
        title.images = {"Primary": {"removed": True, "tag": "t" * 16}, "Backdrop": {"path": "a.jpg", "tag": "x"}, "Logo": {"tmdb": "/l.png"},
                        "Backdrop.1": None, "Backdrop.2": {"upload": 7}, "Backdrop.3": "u" * 64}
        add_title(session, "t2", "movie", "Other").images = None
        session.add(TitleEdit(batch_id="b", kind="image", title_id="t1", field="images.Primary", before=None, after={"removed": True}, created_at=NOW))
        upload(session, "u" * 64)
    with db_factory.begin() as session:
        assert ti.gc_uploads(session, NOW) == 1
    with db_factory() as session:
        assert remaining(session) == set()
        assert session.get(MediaTitle, "t1") is not None


# ---- A PNG reaches ffmpeg rebuilt from its critical chunks only (no iCCP/zTXt zlib bomb) ----

SIG = b"\x89PNG\r\n\x1a\n"
MEMORY_BUDGET = 512 * 1024 * 1024


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def tiny_png(*ancillary: bytes, width: int = 64, height: int = 64) -> tuple[bytes, bytes]:
    """(a PNG with ``ancillary`` chunks after IHDR and a split IDAT, the same PNG with only IHDR/PLTE/tRNS/IDAT/IEND)."""
    pixels = zlib.compress(bytes((width * 3 + 1) * height))
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    plte, trns = chunk(b"PLTE", b"\x00\x00\x00\xff\xff\xff"), chunk(b"tRNS", b"\x00\x01\x00\x02\x00\x03")
    idats = chunk(b"IDAT", pixels[:10]) + chunk(b"IDAT", pixels[10:])
    end = chunk(b"IEND", b"")
    full = SIG + ihdr + b"".join(ancillary) + plte + chunk(b"tEXt", b"Author\x00me") + trns + idats + chunk(b"tIME", b"\x07\xea\x0a\x02\x0c\x00\x00") + end
    return full, SIG + ihdr + plte + trns + idats + end


def zlib_bomb(kind: bytes, mib: int) -> bytes:
    """An iCCP or zTXt chunk whose payload inflates to ``mib`` MiB of zeros."""
    packer = zlib.compressobj(9)
    zeros = b"".join(packer.compress(bytes(64 << 20)) for _ in range(mib // 64)) + packer.flush()
    return chunk(kind, {b"iCCP": b"icc\x00\x00", b"zTXt": b"Comment\x00\x00"}[kind] + zeros)


@pytest.fixture(scope="module")
def bombs() -> dict[bytes, bytes]:
    return {kind: zlib_bomb(kind, 512) for kind in (b"iCCP", b"zTXt")}


def test_a_png_is_rebuilt_from_its_critical_chunks(bombs) -> None:  # noqa: ANN001
    ancillary = (bombs[b"iCCP"], bombs[b"zTXt"], chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x/>"), chunk(b"eXIf", b"MM\x00*GPS"))
    full, critical = tiny_png(*ancillary)
    assert ti.strip_png(full) == critical
    assert ti.strip_png(full + b"<html><script>alert(1)</script>") == critical   # bytes after IEND are dropped


@pytest.mark.parametrize("damage", ["crc", "ancillary_crc", "overrun", "no_iend", "no_idat", "bad_length", "truncated"])
def test_a_malformed_png_is_refused_before_ffmpeg(damage) -> None:  # noqa: ANN001
    full, _critical = tiny_png(chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"x")))
    at = full.index(b"zTXt") - 4
    data = {
        "crc": full[:-1] + b"\x00",                                            # IEND's CRC
        "ancillary_crc": full[:at + 12] + bytes([full[at + 12] ^ 1]) + full[at + 13:],
        "overrun": full[:at] + struct.pack(">I", 1 << 20) + full[at + 4:],
        "no_iend": full[:-12],
        "no_idat": SIG + full[8:33] + chunk(b"IEND", b""),
        "bad_length": full[:at] + b"\xff\xff\xff\xff" + full[at + 4:],
        "truncated": full[:8 + 25 + 6],                                       # IHDR, then half a chunk header
    }[damage]
    with pytest.raises(ti.UploadError) as raised:
        ti.strip_png(data)
    assert (raised.value.status, raised.value.detail) == (415, "unsupported_image")


def test_encode_never_hands_ffmpeg_a_compressed_ancillary_chunk(monkeypatch, bombs) -> None:  # noqa: ANN001
    fed: list[bytes] = []
    monkeypatch.setattr(ti, "_run", lambda argv, data, timeout: fed.append(data) or jpeg_header(64, 64))
    full, critical = tiny_png(bombs[b"zTXt"], bombs[b"iCCP"])
    ti.encode(full, "Primary", ffmpeg="/unused/ffmpeg")
    assert fed == [critical] and b"zTXt" not in fed[0] and b"iCCP" not in fed[0]
    jpeg = jpeg_header(200, 300)
    ti.encode(jpeg, "Primary", ffmpeg="/unused/ffmpeg")
    assert fed[-1] == jpeg                                                     # JPEG and WebP pass through unchanged


MEASURE = (
    "import resource, subprocess, sys\n"
    "done = subprocess.run(sys.argv[1:], stdin=sys.stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)\n"
    "sys.stderr.write(str(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss))\n"
    "sys.stdout.buffer.write(done.stdout)\n"
    "sys.exit(done.returncode)\n"
)


@needs_ffmpeg
@pytest.mark.parametrize("kind", [b"iCCP", b"zTXt"])
def test_a_png_zlib_bomb_encodes_inside_the_memory_budget(monkeypatch, corpus, bombs, kind) -> None:  # noqa: ANN001
    """The reviewer's case: a 400x600 PNG with a chunk inflating to 512 MiB measured ~1.8 GB in ffmpeg before the fix."""
    peaks: list[int] = []

    def measured(argv, data, timeout):  # noqa: ANN001, ANN202 - the real argv and bytes, ffmpeg's peak RSS read in isolation
        done = subprocess.run([sys.executable, "-c", MEASURE, *argv], input=data, capture_output=True, timeout=120)
        peaks.append(int(done.stderr) * (1 if sys.platform == "darwin" else 1024))
        if done.returncode != 0:
            raise ti.UploadError(415, "unsupported_image")
        return done.stdout

    monkeypatch.setattr(ti, "_run", measured)
    bomb = corpus["png"][:33] + bombs[kind] + corpus["png"][33:]
    assert len(bomb) < 600 * 1024
    out = ti.encode(bomb, "Primary", ffmpeg=FFMPEG)
    print(f"{kind.decode()} bomb: ffmpeg peak RSS {peaks[0] / 2**20:.0f} MiB")
    assert (out.width, out.height) == (400, 600) and peaks[0] < MEMORY_BUDGET


def test_the_encoder_child_gets_an_address_space_limit(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """RLIMIT_AS backstop (Linux prlimit): set on the child before a byte of input is fed to it."""
    limits: list = []
    monkeypatch.setattr(ti.resource, "prlimit", lambda pid, which, limit: limits.append((pid > 0, which, limit)), raising=False)
    ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=fake_binary(tmp_path, "exec cat"))
    assert limits == [(True, ti.resource.RLIMIT_AS, (ti.ENCODER_ADDRESS_SPACE, ti.ENCODER_ADDRESS_SPACE))]
    assert ti.ENCODER_ADDRESS_SPACE == 1280 * 1024 * 1024


@pytest.mark.skipif(not hasattr(__import__("resource"), "prlimit"), reason="RLIMIT_AS is enforced on Linux only")
def test_an_encoder_past_its_address_space_fails_cleanly(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(ti, "ENCODER_ADDRESS_SPACE", 64 * 1024 * 1024)
    hog = fake_binary(tmp_path, f"exec {sys.executable} -c 'import sys; sys.stdin.read(); b = bytearray(256 << 20)'")
    with pytest.raises(ti.UploadError) as raised:
        ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=hog)
    assert (raised.value.status, raised.value.detail) == (415, "unsupported_image")


# ---- Chunk cap, strip inside the encoder slot, prlimit denial ----

def many_idat_png(count: int) -> bytes:
    """A valid 64x64 PNG preceded by ``count`` empty IDAT chunks (built once, repeated by reference; ~12 B per chunk)."""
    full, _ = tiny_png()
    ihdr_end = 8 + 25
    return full[:ihdr_end] + chunk(b"IDAT", b"") * count + full[ihdr_end:]


def test_a_png_with_over_65536_chunks_is_refused_fast_and_bounded(monkeypatch) -> None:  # noqa: ANN001
    import tracemalloc

    data = many_idat_png(1_250_000)
    crcs = [0]

    class CountingZlib:  # "fast" = the walk stops at the cap: counted CRC checks, not a wall clock that load can blow
        @staticmethod
        def crc32(view) -> int:  # noqa: ANN001
            crcs[0] += 1
            return zlib.crc32(view)

    monkeypatch.setattr(ti, "zlib", CountingZlib)
    tracemalloc.start()
    with pytest.raises(ti.UploadError) as raised:
        ti.strip_png(data)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert (raised.value.status, raised.value.detail) == (415, "unsupported_image")
    assert peak < 32 * 1024 * 1024 and crcs[0] <= ti.MAX_PNG_CHUNKS


def test_a_png_at_the_chunk_cap_is_still_rebuilt() -> None:
    full, critical = tiny_png()
    assert ti.strip_png(many_idat_png(1000)) == critical[:33] + chunk(b"IDAT", b"") * 1000 + critical[33:]
    assert full  # the cap is on chunk count, not on this fixture


def test_uploads_serialize_the_png_parse(monkeypatch) -> None:  # noqa: ANN001
    active, peak, lock = [0], [0], threading.Lock()
    real = ti.strip_png

    def slow(data: bytes) -> bytes:
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with lock:
            active[0] -= 1
        return real(data)

    monkeypatch.setattr(ti, "strip_png", slow)
    monkeypatch.setattr(ti, "BUSY_WAIT_SECONDS", 5)
    monkeypatch.setattr(ti, "_run", lambda argv, data, timeout: jpeg_header(64, 64))
    full, _ = tiny_png()
    workers = [threading.Thread(target=lambda: ti.encode(full, "Primary", ffmpeg="/unused/ffmpeg")) for _ in range(3)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(10)
    assert peak[0] == 1


def test_a_denied_prlimit_is_logged_once_and_the_encode_continues(monkeypatch, tmp_path, caplog) -> None:  # noqa: ANN001
    def deny(pid, which, limit):  # noqa: ANN001, ANN202
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(ti.resource, "prlimit", deny, raising=False)
    monkeypatch.setattr(ti, "_PRLIMIT_WARNED", False, raising=False)
    binary = fake_binary(tmp_path, "exec cat")
    with caplog.at_level("WARNING", logger=ti.__name__):
        for _ in range(2):
            assert ti.encode(jpeg_header(200, 300), "Primary", ffmpeg=binary).width == 200
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and binary not in warnings[0].getMessage()
