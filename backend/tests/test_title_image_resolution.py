"""Art resolution for upload and removed entries and extra backdrops."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import MediaTitle, TitleUpload, User
from app.services import art_urls, titles
from support import make_user
from title_support import ALICE, MOVIE, ROOT, SERIES, seed_tree

SHA = "a" * 64
TID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def mk(images: dict) -> MediaTitle:
    return MediaTitle(id=TID, type="movie", key="k", name="Dune", images=images)


def source(entry: dict) -> str | None:
    return titles.title_image_source(mk({"Primary": entry}), "Primary")


def test_source_of_each_entry_shape() -> None:
    assert source({"upload": SHA, "tag": SHA}) == SHA
    assert source({"removed": True, "tag": "removed:abc"}) is None
    assert source({"tmdb": "/x.jpg", "tag": "tmdb:/x.jpg"}) == "tmdb:/x.jpg"
    assert source({"path": "a.jpg", "tag": "t"}) == "t" and source({"embedded": "a.mp3"}) == "a.mp3" and source({"tmdb": "/x.jpg"}) == "/x.jpg"


def test_image_key_and_url_for_extra_backdrops() -> None:
    assert titles.image_key("Backdrop", 0) == "Backdrop" and titles.image_key("Backdrop", 3) == "Backdrop.3"
    with pytest.raises(ValueError):
        titles.image_key("Primary", 1)
    with pytest.raises(ValueError):
        titles.image_key("Backdrop", 5)
    entry = {"upload": SHA, "tag": SHA}
    t = mk({"Backdrop": entry, "Backdrop.2": entry})
    tag = titles.image_tag(t, "Backdrop.2")
    assert titles.image_url(t, "Backdrop.2") == f"/api/titles/{TID}/images/Backdrop?index=2&tag={tag}"
    assert titles.image_url(t, "Backdrop") == f"/api/titles/{TID}/images/Backdrop?tag={titles.image_tag(t, 'Backdrop')}"


def test_tag_differs_per_key_and_per_source() -> None:
    t = mk({"Backdrop": {"upload": SHA, "tag": SHA}, "Backdrop.1": {"upload": SHA, "tag": SHA}})
    assert titles.image_tag(t, "Backdrop") != titles.image_tag(t, "Backdrop.1")
    other = mk({"Backdrop": {"upload": "b" * 64, "tag": "b" * 64}})
    assert titles.image_tag(t, "Backdrop") != titles.image_tag(other, "Backdrop")
    assert titles.image_tag(mk({}), "Backdrop") is None


def put_upload(db_factory, data: bytes = b"\x89PNGdata") -> None:  # noqa: ANN001
    with db_factory() as s:
        s.add(TitleUpload(sha256=SHA, content_type="image/png", width=2, height=3, data=data))
        s.commit()


def test_upload_bytes_come_from_the_row(db_factory) -> None:  # noqa: ANN001
    put_upload(db_factory)
    t = mk({"Primary": {"upload": SHA, "tag": SHA}})
    with db_factory() as s:
        assert titles.title_image_bytes(s, t, "Primary", None) == ("image/png", b"\x89PNGdata")


def test_missing_upload_and_removed_are_filenotfound(db_factory) -> None:  # noqa: ANN001
    with db_factory() as s:
        with pytest.raises(FileNotFoundError):
            titles.title_image_bytes(s, mk({"Primary": {"upload": SHA, "tag": SHA}}), "Primary", None)
        with pytest.raises(FileNotFoundError):
            titles.title_image_bytes(s, mk({"Primary": {"removed": True, "tag": "x"}}), "Primary", None)


def test_tmdb_entry_under_a_backdrop_n_key_uses_the_backdrop_size(monkeypatch, db_factory) -> None:  # noqa: ANN001
    seen = []
    monkeypatch.setattr(titles.tmdb, "load_image", lambda artwork, path, kind: seen.append(kind) or SimpleNamespace(content_type="image/jpeg", content=b"x"))
    with db_factory() as s:
        titles.title_image_bytes(s, mk({"Backdrop.2": {"tmdb": "/x.jpg"}}), "Backdrop.2", None)
    assert seen == ["Backdrop"]


def test_supported_accepts_upload_and_rejects_removed() -> None:
    assert art_urls.supported({"upload": SHA, "tag": SHA}) and not art_urls.supported({"removed": True, "tag": "x"})
    assert not art_urls.local({"upload": SHA, "tag": SHA})


@pytest.fixture
def served(db_factory, api_client, tmp_path):  # noqa: ANN001, ANN201
    with db_factory() as s:
        s.add(make_user(ALICE, username="alice"))
        seed_tree(s, tmp_path.resolve() / "media")
        s.commit()
        m = s.get(MediaTitle, MOVIE)
        e = {"upload": SHA, "tag": SHA}
        m.images = {"Primary": e, "Backdrop": e, "Backdrop.1": e, "Logo": {"removed": True, "tag": "x"}}
        s.commit()
        user = s.get(User, ALICE)
    put_upload(db_factory)
    return api_client(user=user, base_url="http://localhost")


def test_serve_route_index_matrix_and_headers(served) -> None:  # noqa: ANN001
    assert served.get(f"/api/titles/{MOVIE}/images/Backdrop", params={"index": 1}).status_code == 200
    assert served.get(f"/api/titles/{MOVIE}/images/Backdrop", params={"index": 5}).status_code == 422
    assert served.get(f"/api/titles/{MOVIE}/images/Backdrop", params={"index": 2}).status_code == 404
    assert served.get(f"/api/titles/{MOVIE}/images/Primary", params={"index": 1}).status_code == 404
    assert served.get(f"/api/titles/{MOVIE}/images/Logo").status_code == 404
    r = served.get(f"/api/titles/{MOVIE}/images/Primary")
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    assert "immutable" not in r.headers["cache-control"]
    older = "f" * 64
    tag = titles.image_tag(MediaTitle(id=MOVIE, type="movie", key="k", name="Dune", images={"Primary": {"upload": older, "tag": older}}), "Primary")  # same title, older source: stale
    assert "immutable" not in served.get(f"/api/titles/{MOVIE}/images/Primary", params={"tag": tag}).headers["cache-control"]


def test_fresh_tag_is_immutable_and_removed_has_no_art(served, db_factory) -> None:  # noqa: ANN001
    with db_factory() as s:
        m = s.get(MediaTitle, MOVIE)
        assert titles.image_url(m, "Logo") is None
        url = titles.image_url(m, "Primary")
    assert "immutable" in served.get(url).headers["cache-control"]
