"""Jellyfin serves backdrop indices 0-4 read-only."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app
from app.models import MediaTitle, TitleUpload, utcnow
from app.services.media_titles import jellyfin_id
from title_support import ALICE_TOKEN, MOVIE, POSTER, jellyfin_household, mediabrowser

HEADERS = mediabrowser(ALICE_TOKEN)
SHAS = ["a" * 64, "b" * 64, "c" * 64]
DATA = {sha: b"\xff\xd8\xff" + sha[:1].encode() * 8 for sha in SHAS}


def set_backdrops(*shas: str, extra: dict | None = None) -> None:
    with db_module.SessionLocal() as session:
        title = session.get(MediaTitle, MOVIE)
        images = {k: v for k, v in (title.images or {}).items() if not k.startswith("Backdrop")}
        for n, sha in enumerate(shas):
            images["Backdrop" if n == 0 else f"Backdrop.{n}"] = {"upload": sha, "tag": sha}
        title.images = {**images, **(extra or {})}
        session.commit()


@pytest.fixture
def jf(tmp_path):  # noqa: ANN001, ANN201
    jellyfin_household(tmp_path.resolve() / "media")
    with db_module.SessionLocal() as session:
        for sha, data in DATA.items():
            session.add(TitleUpload(sha256=sha, content_type="image/jpeg", width=200, height=300, data=data, created_at=utcnow()))
        session.commit()
    set_backdrops(*SHAS)
    client = TestClient(app, base_url="http://localhost")
    yield client
    client.close()


def dto(client) -> dict:  # noqa: ANN001
    return client.get(f"/Items/{jellyfin_id(MOVIE)}", headers=HEADERS).json()


def test_backdrop_image_tags_list_the_present_indices_0_to_4(jf) -> None:  # noqa: ANN001
    assert len(dto(jf)["BackdropImageTags"]) == 3 and len(set(dto(jf)["BackdropImageTags"])) == 3
    set_backdrops(SHAS[0])
    assert len(dto(jf)["BackdropImageTags"]) == 1
    set_backdrops()
    assert dto(jf).get("BackdropImageTags", []) == []
    set_backdrops(*SHAS, extra={"Backdrop.3": {"removed": True, "tag": "removed:x"}})
    assert len(dto(jf)["BackdropImageTags"]) == 3  # a tombstone is absent


def test_index_1_to_4_is_served_with_a_token_or_the_signed_tag_and_5_is_404(jf) -> None:  # noqa: ANN001
    tags = dto(jf)["BackdropImageTags"]
    path = f"/Items/{jellyfin_id(MOVIE)}/Images/Backdrop"
    assert jf.get(f"{path}/1", headers=HEADERS).content == DATA[SHAS[1]]
    assert jf.get(f"{path}/2", params={"tag": tags[2]}).content == DATA[SHAS[2]]
    assert jf.get(f"{path}/2", params={"tag": tags[1]}).status_code == 404  # another index's tag
    assert jf.get(f"{path}/1").status_code == 404  # neither
    assert jf.get(f"{path}/3", headers=HEADERS).status_code == 404  # empty slot
    assert jf.get(f"{path}/5", headers=HEADERS).status_code == 404


@pytest.mark.parametrize("kind", ["Primary", "Logo", "Thumb"])
def test_index_1_on_a_non_backdrop_type_is_404(jf, kind) -> None:  # noqa: ANN001
    assert jf.get(f"/Items/{jellyfin_id(MOVIE)}/Images/{kind}/1", headers=HEADERS).status_code == 404


def test_size_params_on_an_extra_backdrop_serve_the_original(jf) -> None:  # noqa: ANN001
    reply = jf.get(f"/Items/{jellyfin_id(MOVIE)}/Images/Backdrop/1", params={"maxWidth": 100}, headers={**HEADERS, "Accept": "image/webp"})
    assert reply.status_code == 200 and reply.content == DATA[SHAS[1]]


def test_tags_change_after_a_reorder(jf) -> None:  # noqa: ANN001
    before = dto(jf)["BackdropImageTags"]
    set_backdrops(SHAS[2], SHAS[0], SHAS[1])
    after = dto(jf)["BackdropImageTags"]
    assert len(after) == 3 and after != before and all(a != b for a, b in zip(after, before))


def test_the_primary_image_still_serves(jf) -> None:  # noqa: ANN001
    assert jf.get(f"/Items/{jellyfin_id(MOVIE)}/Images/Primary", headers=HEADERS).content == POSTER


def test_no_jellyfin_mutation_route_exists(jf) -> None:  # noqa: ANN001
    item = jellyfin_id(MOVIE)
    for path in (f"/Items/{item}/Images/Primary", f"/Items/{item}/Images/Backdrop/1", f"/Items/{item}"):
        for method in ("post", "put", "delete"):
            assert jf.request(method.upper(), path, headers=HEADERS, content=b"x").status_code in (404, 405)
