"""Gallery T1: Jellyfin clients' maxWidth/fillHeight/format get renditions, not originals."""
from __future__ import annotations

import collections
import threading

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import MediaTitle
from app.services import art_urls, renditions
from app.services.connected_apps import image_grants
from app.services.media_titles import jellyfin_id
from app.services.titles import title_image_source
from art_support import artwork_row, fake_calls, give_art, png_header, use_fake_ffmpeg, write_rendition
from title_support import ALICE_TOKEN, MOVIE, POSTER, SECRET_SERIES, SERIES, jellyfin_household, mediabrowser

WEBP = {"Accept": "image/webp,*/*", **mediabrowser(ALICE_TOKEN)}
INFUSE = {"Accept": "*/*", **mediabrowser(ALICE_TOKEN)}
PATH = f"/Items/{jellyfin_id(SERIES)}/Images/Primary"


@pytest.mark.parametrize(("params", "dims", "expected"), [
    ({}, None, None),
    ({"maxwidth": "300"}, None, 300),
    ({"fillwidth": "500", "width": "100"}, None, 500),
    ({"width": "100"}, None, 100),
    ({"fillheight": "450"}, (1000, 1500), 300),
    ({"maxheight": "451"}, (1000, 1500), 301),  # ceil
    ({"fillheight": "450"}, None, None),  # no known aspect: the original
    ({"maxwidth": "-5"}, None, None),
    ({"maxwidth": "4e3"}, None, None),
    ({"maxwidth": "999999"}, None, None),
])
def test_requested_width(params, dims, expected) -> None:  # noqa: ANN001
    assert renditions.requested_width(params, dims) == expected


@pytest.mark.parametrize(("widths", "requested", "source", "expected"), [
    ((240, 480), None, 1000, None),
    ((240, 480), 200, 1000, 240),
    ((240, 480), 241, 1000, 480),
    ((240, 480), 700, 1000, None),  # wider than every rendition and the source is wider still: the original
    ((240, 480), 700, 450, 480),  # the source is narrower than the largest rendition: that rendition is the source size
    ((240, 480), 700, None, None),
    ((), 200, 1000, None),
])
def test_pick_width(widths, requested, source, expected) -> None:  # noqa: ANN001
    assert renditions.pick_width(widths, requested, source) == expected


def test_pick_format_follows_format_accept_and_the_host(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(art_urls, "_extension", "webp")
    assert renditions.pick_format({}, "image/webp,*/*") == "webp"
    assert renditions.pick_format({"format": "webp"}, "image/webp") == "webp"
    assert renditions.pick_format({"format": "jpg"}, "image/webp") == "jpg"
    assert renditions.pick_format({}, "*/*") == "jpg"
    monkeypatch.setattr(art_urls, "_extension", "jpg")
    assert renditions.pick_format({}, "image/webp") == "jpg"


@pytest.fixture
def jf(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(art_urls, "_secret", None)
    monkeypatch.setattr(art_urls, "_extension", "webp")
    monkeypatch.setattr(art_urls, "_pending", art_urls.OrderedDict())
    monkeypatch.setattr(renditions, "_serving", collections.Counter())
    monkeypatch.setattr(renditions, "_slots", threading.BoundedSemaphore(renditions.ON_DEMAND_SLOTS))  # no leaked slot from another test
    monkeypatch.setattr(renditions, "_inflight", {})
    media = tmp_path.resolve() / "media"
    jellyfin_household(media)
    with db_module.SessionLocal() as session:
        series = session.get(MediaTitle, SERIES)
        artwork_row(session, series, "Primary")  # 1000 x 1500
        key = art_urls.source_key(SERIES, "Primary", title_image_source(series, "Primary"))
        session.commit()
    for width in (240, 480):
        for ext in ("webp", "jpg"):
            write_rendition(key, width, f"{ext}-{width}".encode(), ext)
    client = TestClient(app, base_url="http://localhost")
    yield client, media, key
    client.close()
    for future in list(renditions._inflight.values()):
        future.result(timeout=10)


def test_max_width_gets_the_smallest_rendition_at_least_that_wide(jf) -> None:  # noqa: ANN001
    client, _, _ = jf
    response = client.get(PATH, params={"maxWidth": 300}, headers=WEBP)
    assert (response.status_code, response.content, response.headers["content-type"]) == (200, b"webp-480", "image/webp")
    assert response.headers["cache-control"] == "private, max-age=31536000" and "Accept" in [v.strip() for v in response.headers["vary"].split(",")]
    assert client.get(PATH, params={"fillHeight": 300}, headers=WEBP).content == b"webp-240"  # 300 x 1000/1500 = 200
    assert client.get(PATH, params={"maxWidth": 300, "format": "jpg"}, headers=WEBP).content == b"jpg-480"


def test_no_size_or_too_wide_serves_the_original_as_before(jf) -> None:  # noqa: ANN001
    client, _, _ = jf
    assert client.get(PATH, headers=WEBP).content == POSTER
    assert client.get(PATH, params={"maxWidth": 2000}, headers=WEBP).content == POSTER  # the source (1000) is wider than 480


def test_a_signed_tag_without_a_token_still_gets_renditions_and_visibility_still_applies(jf) -> None:  # noqa: ANN001
    client, _, _ = jf
    tag = client.get(f"/Items/{jellyfin_id(SERIES)}", headers=mediabrowser(ALICE_TOKEN)).json()["ImageTags"]["Primary"]
    assert client.get(PATH, params={"tag": tag, "maxWidth": 200}, headers={"Accept": "image/webp"}).content == b"webp-240"
    image_grants.clear()  # the sign-in above grants this address its art for minutes
    assert client.get(PATH, params={"maxWidth": 200}, headers={"Accept": "image/webp"}).status_code == 404  # neither
    assert client.get(f"/Items/{jellyfin_id(SECRET_SERIES)}/Images/Primary", params={"maxWidth": 200}, headers=WEBP).status_code == 404


def test_logos_never_become_jpeg(jf) -> None:  # noqa: ANN001
    client, media, _ = jf
    (media / "Show" / "logo.png").write_bytes(png_header(800, 310))
    with db_module.SessionLocal() as session:
        give_art(session.get(MediaTitle, SERIES), "Logo", path="Show/logo.png")
        session.commit()
    response = client.get(f"/Items/{jellyfin_id(SERIES)}/Images/Logo", params={"maxWidth": 300}, headers=INFUSE)
    assert (response.status_code, response.content, response.headers["content-type"]) == (200, png_header(800, 310), "image/png")


def test_infuse_without_webp_gets_jpeg_made_once_then_from_disk(jf, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    client, media, key = jf
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    monkeypatch.setattr(renditions, "ON_DEMAND_BUDGET_SECONDS", 5.0)  # a fast fake run must not lose the 0.8 s race under load
    (media / "Show" / "poster.png").write_bytes(png_header(1000, 1500))  # a real header for the fake to "decode"
    for width in (240, 480):
        art_urls.rendition_file(key, width, "jpg").unlink()
    first = client.get(PATH, params={"maxWidth": 300}, headers=INFUSE)
    assert (first.status_code, first.content, first.headers["content-type"]) == (200, b"r" * 64, "image/jpeg")
    assert first.headers["cache-control"] == "private, max-age=31536000"
    assert client.get(PATH, params={"maxWidth": 300}, headers=INFUSE).content == b"r" * 64
    assert len(fake_calls(log)) == 1 and "-c:v" in fake_calls(log)[0]["argv"] and "mjpeg" in fake_calls(log)[0]["argv"]


def test_a_failed_generation_serves_the_original_uncached(jf, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    client, media, key = jf
    log = use_fake_ffmpeg(tmp_path, monkeypatch, "fail")
    poster = png_header(1000, 1500)  # a real header: image_size must succeed so ffmpeg is actually invoked (then fails)
    (media / "Show" / "poster.png").write_bytes(poster)
    for width in (240, 480):
        art_urls.rendition_file(key, width, "webp").unlink()
    response = client.get(PATH, params={"maxWidth": 300}, headers=WEBP)
    assert (response.status_code, response.content, response.headers["cache-control"]) == (200, poster, "no-store")
    assert len(fake_calls(log)) == 1  # a skipped (offline/settled) generation must not pass this by accident


def test_a_cold_miss_with_a_large_original_never_503s_and_queues_the_pass(jf, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    client, media, key = jf
    use_fake_ffmpeg(tmp_path, monkeypatch, "hang")
    monkeypatch.setattr(renditions, "ON_DEMAND_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(renditions, "ON_DEMAND_TIMEOUT_SECONDS", 1.0)
    large = png_header(1000, 1500) + b"\0" * (2 * 1024 * 1024 + 1)
    (media / "Show" / "poster.png").write_bytes(large)
    for width in (240, 480):
        art_urls.rendition_file(key, width, "webp").unlink()
    response = client.get(PATH, params={"maxWidth": 300}, headers=WEBP)
    assert (response.status_code, response.content, response.headers["cache-control"]) == (200, large, "no-store")
    assert art_urls.take(5) == [(SERIES, "Primary")]  # the background pass retries once ffmpeg is free


def test_a_height_only_request_with_no_known_aspect_serves_the_original_uncached(jf) -> None:  # noqa: ANN001
    client, _, _ = jf  # MOVIE has no title_artwork row (unlike SERIES in this fixture): dims are unknown
    response = client.get(f"/Items/{jellyfin_id(MOVIE)}/Images/Primary", params={"fillHeight": 450}, headers=WEBP)
    assert (response.status_code, response.content, response.headers["cache-control"]) == (200, POSTER, "no-store")
    assert client.get(f"/Items/{jellyfin_id(MOVIE)}/Images/Primary", headers=WEBP).headers["cache-control"] == "private, max-age=31536000"


def test_head_with_size_params_never_starts_generation(jf, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    client, _, key = jf
    log = use_fake_ffmpeg(tmp_path, monkeypatch)
    for width in (240, 480):
        art_urls.rendition_file(key, width, "webp").unlink()
    response = client.head(PATH, params={"maxWidth": 300}, headers=WEBP)
    assert response.status_code == 200
    assert fake_calls(log) == [] and art_urls.take(5) == []  # no generation, no enqueue
    assert client.get(PATH, params={"maxWidth": 300}, headers=WEBP).content == POSTER  # unaffected: still on-demand for GET
    disk_hit = client.head(PATH, params={"maxWidth": 300, "format": "jpg"}, headers=WEBP)  # jpg renditions were never unlinked
    assert disk_hit.status_code == 200 and fake_calls(log) == []  # served from disk, no generation either way
