"""Title image routes: permissions, slot matrix, stale tags, upload errors, backdrop shift/reorder, caches."""
from __future__ import annotations

import hashlib
import shutil
import subprocess

import pytest
from sqlalchemy import select

from app.models import AppSettings, MediaTitle, TitleEdit, TitleUpload, User
from app.services import art_urls, jellyfin as jf, media_titles, metadata_editor, title_images, tmdb, titles
from app.services.title_images import Encoded, UploadError
from support import make_user, seed_app_settings
from title_support import ALICE, BOB, MOVIE, S1E1, SEASON1, SERIES, seed_tree

ADMIN = "admin-1"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def t1_stub(monkeypatch):  # noqa: ANN001, ANN201
    """keep_source_value may be a NotImplementedError stub."""
    try:
        media_titles.keep_source_value(MediaTitle(), "x")
    except NotImplementedError:
        monkeypatch.setattr(media_titles, "keep_source_value", lambda title, field: None)


@pytest.fixture
def env(db_factory, tmp_path, api_client, monkeypatch):  # noqa: ANN001, ANN201
    root = tmp_path.resolve() / "media"
    with db_factory() as s:
        s.add_all([make_user(ADMIN, role="admin", username="admin"), make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(s, root)
        seed_app_settings(s)

    def encode(data, image_type, *, ffmpeg):  # noqa: ANN001, ANN202
        if data.startswith(b"<svg"):
            raise UploadError(415, "unsupported_image")
        return Encoded(hashlib.sha256(data).hexdigest(), "image/jpeg", 200, 300, data)

    monkeypatch.setattr(title_images, "encode", encode)

    def client(user_id: str = ADMIN):  # noqa: ANN202
        with db_factory() as s:
            return api_client(user=s.get(User, user_id), base_url="http://localhost")

    def title(title_id: str = MOVIE) -> MediaTitle:
        with db_factory() as s:
            return s.get(MediaTitle, title_id)

    return client, title, db_factory


def url(title_id: str, image_type: str, index: int = 0, base: str | None = None) -> str:
    return f"/api/titles/{title_id}/images/{image_type}/{index}" + (f"?base_tag={base}" if base is not None else "")


def put(client, title_id, image_type, index, data: bytes, base=None):  # noqa: ANN001, ANN201
    return client.put(url(title_id, image_type, index, base or ""), content=data, headers={"content-type": "image/png"})


def choose(client, title_id, image_type, index, path, base=None):  # noqa: ANN001, ANN201
    return client.put(url(title_id, image_type, index), json={"tmdb_path": path, "base_tag": base})


def edits(db_factory) -> int:  # noqa: ANN001
    with db_factory() as s:
        return len(s.scalars(select(TitleEdit)).all())


def tags(rows, image_type="Backdrop") -> list[str]:  # noqa: ANN001
    return [r["tag"] for r in rows if r["type"] == image_type]


def test_permission_matrix(env) -> None:  # noqa: ANN001
    client, _title, db_factory = env
    assert put(client(), MOVIE, "Primary", 0, b"a" * 10, base="Movie (2020)/poster.png").status_code == 200
    assert put(client(ALICE), MOVIE, "Primary", 0, b"b" * 10).status_code == 403
    assert put(client(ALICE), MOVIE, "Primary", 0, b"b" * 10).json() == {"detail": "editing_not_allowed"}
    with db_factory() as s:
        s.get(AppSettings, 1).members_edit_metadata = True
        s.commit()
    assert put(client(ALICE), MOVIE, "Backdrop", 0, b"c" * 10).status_code == 200
    assert put(client(ALICE), "00000000-0000-0000-0000-00000000dead", "Primary", 0, b"c").status_code == 404
    with db_factory() as s:  # a member sees neither another member's private title nor learns it exists
        s.get(User, ALICE).is_active = False
        s.commit()
    assert put(client(ALICE), MOVIE, "Primary", 0, b"d" * 10).status_code in (401, 403)


def test_an_unauthenticated_upload_is_refused(env) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from app.main import app
    anonymous = TestClient(app, base_url="http://localhost")
    reply = anonymous.put(url(MOVIE, "Primary", 0), content=b"x" * 1_000_000)
    assert reply.status_code == 401 and edits(env[2]) == 0
    anonymous.close()


@pytest.mark.parametrize("title_id, image_type, index, code, detail", [
    (MOVIE, "Backdrop", 5, 422, "invalid_index"), (MOVIE, "Primary", 1, 422, "invalid_index"), (MOVIE, "Logo", 1, 422, "invalid_index"),
    (S1E1, "Logo", 0, 422, "type_not_allowed"), (S1E1, "Backdrop", 0, 422, "type_not_allowed"), (SEASON1, "Logo", 0, 422, "type_not_allowed"),
    (MOVIE, "Thumb", 0, 422, "type_not_allowed"), (MOVIE, "Backdrop", 2, 422, "index_gap"), (MOVIE, "Backdrop", -1, 422, "invalid_index"),
])
def test_index_and_type_matrix(env, title_id, image_type, index, code, detail) -> None:  # noqa: ANN001
    client, _title, _db = env
    reply = client().put(url(title_id, image_type, index), content=b"x" * 10)
    assert (reply.status_code, reply.json()["detail"]) == (code, detail)
    assert client().put(f"/api/titles/{MOVIE}/images/Backdrop/x", content=b"x").status_code == 422  # not an integer


def test_choose_a_candidate_writes_a_user_entry_and_a_new_tag(env) -> None:  # noqa: ANN001
    client, title, db_factory = env
    before = titles.image_url(title(), "Primary")
    reply = choose(client(), MOVIE, "Primary", 0, "/abc.jpg", base="Movie (2020)/poster.png")
    assert reply.status_code == 200 and reply.json()[0]["origin"] == "tmdb" and reply.json()[0]["tag"] == "tmdb:/abc.jpg"
    assert title().images["Primary"] == {"tmdb": "/abc.jpg", "tag": "tmdb:/abc.jpg"} and title().field_sources["images.Primary"] == "user"
    assert titles.image_url(title(), "Primary") != before and edits(db_factory) == 1
    assert choose(client(), MOVIE, "Primary", 0, "/abc.jpg", base="tmdb:/abc.jpg").status_code == 200
    assert edits(db_factory) == 1  # test_no_history_row_when_nothing_changes
    for bad in ("/x.svg", "abc.jpg", "/a/b.jpg", "http://evil/x.jpg"):
        assert choose(client(), MOVIE, "Primary", 0, bad, base="tmdb:/abc.jpg").status_code == 422


def test_a_stale_base_tag_is_a_409_and_nothing_is_written(env) -> None:  # noqa: ANN001
    client, title, db_factory = env
    snapshot = dict(title().images)
    for reply in (choose(client(), MOVIE, "Primary", 0, "/a.jpg", base="stale"), put(client(), MOVIE, "Primary", 0, b"z" * 9, base="stale"),
                  client().delete(url(MOVIE, "Primary", 0, "stale")), choose(client(), MOVIE, "Backdrop", 0, "/a.jpg", base="notempty")):
        assert reply.status_code == 409 and reply.json()["detail"] == "conflict"
    assert title().images == snapshot and edits(db_factory) == 0
    assert client().post(f"/api/titles/{MOVIE}/images/Backdrop/order", json={"tags": ["a"]}).status_code == 409


def test_upload_flow_and_serving(env) -> None:  # noqa: ANN001
    client, title, db_factory = env
    data = b"\xff\xd8\xff" + b"jpegbytes" * 5
    sha = hashlib.sha256(data).hexdigest()
    reply = put(client(), MOVIE, "Primary", 0, data, base="Movie (2020)/poster.png")
    assert reply.status_code == 200 and reply.json()[0]["tag"] == sha and reply.json()[0]["width"] == 200
    assert title().images["Primary"] == {"upload": sha, "tag": sha}
    served = client().get(titles.image_url(title(), "Primary"))
    assert served.status_code == 200 and served.content == data and served.headers["content-type"] == "image/jpeg"
    assert put(client(), SERIES, "Primary", 0, data, base="1").status_code in (200, 409)
    with db_factory() as s:
        assert len(s.scalars(select(TitleUpload)).all()) == 1  # identical bytes: one row


def test_upload_error_codes(env, monkeypatch) -> None:  # noqa: ANN001
    client, title, db_factory = env
    snapshot = dict(title().images)
    svg = put(client(), MOVIE, "Primary", 0, b"<svg/>", base="Movie (2020)/poster.png")
    assert (svg.status_code, svg.json()["detail"]) == (415, "unsupported_image")
    huge = client().put(url(MOVIE, "Primary", 0), content=b"0" * (title_images.MAX_UPLOAD_BYTES + 1))
    assert (huge.status_code, huge.json()["detail"]) in ((413, "Request body is too large."), (413, "too_large")) or huge.status_code == 413
    streamed = client().put(url(MOVIE, "Primary", 0), content=(b"0" * 1_000_000 for _ in range(16)))
    assert streamed.status_code == 413
    for status, detail in ((422, "image_dimensions"), (503, "encoder_busy"), (503, "encoder_unavailable")):
        def refuse(data, image_type, *, ffmpeg, _s=status, _d=detail):  # noqa: ANN001, ANN202
            raise UploadError(_s, _d)

        monkeypatch.setattr(title_images, "encode", refuse)
        reply = put(client(), MOVIE, "Primary", 0, b"x" * 10, base="Movie (2020)/poster.png")
        assert (reply.status_code, reply.json()["detail"]) == (status, detail)
        assert reply.headers.get("retry-after") == ("5" if detail == "encoder_busy" else None)
    assert title().images == snapshot and edits(db_factory) == 0
    with db_factory() as s:
        assert s.scalars(select(TitleUpload)).all() == []


def test_upload_rate_limit_is_30_an_hour_per_user(env) -> None:  # noqa: ANN001
    client, _title, _db = env
    c = client()
    codes = [put(c, MOVIE, "Backdrop", 0, bytes([i]) * 10, base=None).status_code for i in range(31)]
    assert codes[-1] == 429 and 429 not in codes[:30]


def add_backdrops(client, count: int) -> list[str]:  # noqa: ANN001
    rows: list = []
    for n in range(count):
        rows = choose(client, MOVIE, "Backdrop", n, f"/b{n}.jpg").json()
    return tags(rows)


def test_delete_backdrop_shifts_later_ones_down_and_writes_one_batch(env) -> None:  # noqa: ANN001
    client, title, db_factory = env
    c = client()
    assert add_backdrops(c, 3) == ["tmdb:/b0.jpg", "tmdb:/b1.jpg", "tmdb:/b2.jpg"]
    with db_factory() as s:
        known = len(s.scalars(select(TitleEdit)).all())
    reply = c.delete(url(MOVIE, "Backdrop", 1, "tmdb:/b1.jpg"))
    assert reply.status_code == 200 and tags(reply.json()) == ["tmdb:/b0.jpg", "tmdb:/b2.jpg"]
    assert title().images["Backdrop.2"]["removed"] is True and title().images["Backdrop.1"]["tmdb"] == "/b2.jpg"
    with db_factory() as s:
        new = s.scalars(select(TitleEdit).order_by(TitleEdit.id)).all()[known:]
    assert len(new) == 2 and len({e.batch_id for e in new}) == 1
    assert c.delete(url(MOVIE, "Backdrop", 2, "x")).status_code == 404  # now empty


def test_reorder_is_a_permutation_in_one_batch(env) -> None:  # noqa: ANN001
    client, title, db_factory = env
    c = client()
    t = add_backdrops(c, 3)
    reply = c.post(f"/api/titles/{MOVIE}/images/Backdrop/order", json={"tags": [t[2], t[0], t[1]]})
    assert reply.status_code == 200 and tags(reply.json()) == [t[2], t[0], t[1]]
    with db_factory() as s:
        assert len({e.batch_id for e in s.scalars(select(TitleEdit)).all() if e.after_source == "user" and e.id > 3}) == 1


@pytest.mark.parametrize("body, code", [({"tags": ["tmdb:/b0.jpg", "tmdb:/b0.jpg"]}, 422), ({"tags": ["tmdb:/b0.jpg"]}, 409),
                                        ({"tags": ["tmdb:/b0.jpg", "tmdb:/b1.jpg", "nope"]}, 409), ({"tags": []}, 422)])
def test_reorder_rejects_duplicates_missing_and_wrong_length(env, body, code) -> None:  # noqa: ANN001
    client, title, db_factory = env
    c = client()
    add_backdrops(c, 2)
    known = edits(db_factory)
    assert c.post(f"/api/titles/{MOVIE}/images/Backdrop/order", json=body).status_code == code
    assert edits(db_factory) == known


def test_every_write_changes_every_cache_key(env) -> None:  # noqa: ANN001
    client, title, _db = env
    c = client()
    key = b"k" * 32

    def fingerprint():  # noqa: ANN202
        t = title()
        source = titles.title_image_source(t, "Primary")
        return titles.image_url(t, "Primary"), art_urls.source_key(t.id, "Primary", source or ""), jf.title_tag(key, t, "Primary"), source

    seen = [fingerprint()]
    base = titles.title_image_source(title(), "Primary")
    for step in (lambda b: choose(c, MOVIE, "Primary", 0, "/q.jpg", base=b), lambda b: put(c, MOVIE, "Primary", 0, b"\xff\xd8\xff" + b"u" * 20, base=b),
                 lambda b: c.delete(url(MOVIE, "Primary", 0, b))):
        assert step(base).status_code == 200
        base = titles.title_image_source(title(), "Primary")
        now = fingerprint()
        assert all(a != b for a, b in zip(now[:3], seen[-1][:3])) and now not in seen
        seen.append(now)
    for count in (3,):  # reorder
        add_backdrops(c, count)
        before = [titles.image_url(title(), titles.image_key("Backdrop", n)) for n in range(count)]
        t = [titles.title_image_source(title(), titles.image_key("Backdrop", n)) for n in range(count)]
        assert c.post(f"/api/titles/{MOVIE}/images/Backdrop/order", json={"tags": t[::-1]}).status_code == 200
        assert [titles.image_url(title(), titles.image_key("Backdrop", n)) for n in range(count)] != before


def test_a_removed_image_is_not_re_added_by_a_scan_or_a_refresh(env) -> None:  # noqa: ANN001
    client, title, db_factory = env
    assert client().delete(url(MOVIE, "Primary", 0, "Movie (2020)/poster.png")).status_code == 200
    with db_factory() as s:
        t = s.get(MediaTitle, MOVIE)
        for source, entry in (("path", {"path": "Movie (2020)/poster.png", "tag": "9"}), ("tmdb", {"tmdb": "/x.jpg", "tag": "tmdb:/x.jpg"})):
            media_titles.apply_field(t, "images.Primary", entry, source)
        s.commit()
    assert titles.title_image_source(title(), "Primary") is None


def test_an_uploaded_image_survives_a_tmdb_refresh(env) -> None:  # noqa: ANN001
    client, title, db_factory = env
    data = b"\xff\xd8\xff" + b"keep" * 5
    assert put(client(), MOVIE, "Primary", 0, data, base="Movie (2020)/poster.png").status_code == 200
    with db_factory() as s:
        t = s.get(MediaTitle, MOVIE)
        media_titles.apply_field(t, "images.Primary", {"tmdb": "/x.jpg", "tag": "tmdb:/x.jpg"}, "tmdb")
        s.commit()
    assert title().images["Primary"]["upload"] == hashlib.sha256(data).hexdigest()


class FakeClient:
    lang2, image_languages = "en", "en,null"

    def __init__(self, raw) -> None:  # noqa: ANN001
        self.raw, self.paths = raw, []

    def get(self, path, **_params):  # noqa: ANN001, ANN201
        self.paths.append(path)
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw


def test_candidates_route_codes(env, monkeypatch) -> None:  # noqa: ANN001
    client, _title, db_factory = env
    c = client()
    route = f"/api/titles/{MOVIE}/images/Primary/candidates"
    assert c.get(route).json()["detail"] == "tmdb_not_configured"
    raw = {"posters": [{"file_path": "/p.jpg", "width": 500, "height": 750, "iso_639_1": "en", "vote_average": 5}, {"file_path": "/bad.svg", "width": 1, "height": 1}]}
    fake = FakeClient(raw)
    monkeypatch.setattr(tmdb, "client_for", lambda record: fake)
    rows = c.get(route).json()
    assert [r["tmdb_path"] for r in rows] == ["/p.jpg"] and rows[0]["preview_url"] == "/api/metadata/candidate-image?path=/p.jpg&type=Primary"
    assert fake.paths == [f"/movie/603/images"]
    with db_factory() as s:
        s.get(MediaTitle, SERIES).provider_ids = {}
        s.commit()
    assert c.get(f"/api/titles/{SERIES}/images/Primary/candidates").json()["detail"] == "no_tmdb_id"
    assert c.get(f"/api/titles/{SEASON1}/images/Backdrop/candidates").json() == []
    assert c.get(f"/api/titles/{MOVIE}/images/Logo/candidates").status_code == 200
    assert c.get(f"/api/titles/{S1E1}/images/Logo/candidates").json()["detail"] == "type_not_allowed"
    monkeypatch.setattr(tmdb, "client_for", lambda record: FakeClient(tmdb.TmdbError("boom")))
    assert c.get(route).status_code == 502
    assert client(ALICE).get(route).status_code == 403


def test_candidate_image_route(env, monkeypatch) -> None:  # noqa: ANN001
    client, _title, _db = env
    seen = []

    def load(artwork, path, kind, *, size=None, **_pinned):  # noqa: ANN001, ANN202
        seen.append((path, kind, size))
        from app.services.artwork import ArtworkNotFoundError, ArtworkResponse
        if not tmdb._IMAGE_PATH.fullmatch(path):
            raise ArtworkNotFoundError("no")
        return ArtworkResponse(content=b"img", content_type="image/jpeg")

    monkeypatch.setattr(tmdb, "load_image", load)
    c = client()
    ok = c.get("/api/metadata/candidate-image", params={"path": "/p.jpg", "type": "Backdrop"})
    assert ok.status_code == 200 and ok.headers["x-content-type-options"] == "nosniff" and seen == [("/p.jpg", "Backdrop", "w300")]
    assert c.get("/api/metadata/candidate-image", params={"path": "/p.jpg", "type": "Primary"}).status_code == 200 and seen[-1][2] == "w185"
    assert c.get("/api/metadata/candidate-image", params={"path": "/x.svg", "type": "Primary"}).status_code == 404
    assert c.get("/api/metadata/candidate-image", params={"path": "/p.jpg", "type": "Person"}).status_code == 422
    assert client(ALICE).get("/api/metadata/candidate-image", params={"path": "/p.jpg", "type": "Primary"}).status_code == 403


def test_listing_shape(env) -> None:  # noqa: ANN001
    client, _title, _db = env
    c = client()
    data = b"\xff\xd8\xff" + b"shape" * 5
    put(c, MOVIE, "Backdrop", 0, data)
    rows = choose(c, MOVIE, "Backdrop", 1, "/b.jpg").json()
    first = next(r for r in rows if r["type"] == "Backdrop" and r["index"] == 0)
    assert first["origin"] == "upload" and first["source"] == "user" and first["locked"] is True and (first["width"], first["height"]) == (200, 300)
    assert next(r for r in rows if r["index"] == 1)["origin"] == "tmdb"


@needs_ffmpeg
def test_real_ffmpeg_upload(env, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    client, title, _db = env
    monkeypatch.undo()  # the real encode
    monkeypatch.setattr(media_titles, "keep_source_value", lambda t, f: None)
    out = tmp_path / "a.png"
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc2=size=300x400", "-frames:v", "1", str(out)], check=True)
    reply = put(client(), MOVIE, "Primary", 0, out.read_bytes(), base="Movie (2020)/poster.png")
    assert reply.status_code == 200 and reply.json()[0]["origin"] == "upload"


def test_uploads_are_in_a_backup_and_restore_brings_them_back(tmp_path) -> None:  # noqa: ANN001
    import sqlite3

    from app import db as db_module
    from app.config import settings
    from app.restore import restore
    from app.services import backups
    from app.models import utcnow

    db_module.init_db()
    with db_module.SessionLocal.begin() as db:
        db.add(TitleUpload(sha256="c" * 64, content_type="image/jpeg", width=200, height=300, data=b"\xff\xd8\xffbackup", created_at=utcnow()))
    manifest = backups.create_backup("manual")
    target = tmp_path / "restored"
    restore(backups.backup_root() / f"{manifest['name']}.db", target)
    restored = sqlite3.connect(target / settings.database_filename)
    assert restored.execute("SELECT content_type, width, height, data FROM title_uploads WHERE sha256 = ?", ("c" * 64,)).fetchone() == ("image/jpeg", 200, 300, b"\xff\xd8\xffbackup")
    restored.close()


# ---- security fixes ----

def _race_on_first_load(monkeypatch, db_factory, mutate) -> None:  # noqa: ANN001
    """Run ``mutate(images)`` right after the route's dependency loads the title: the write transaction must see it."""
    from app.routers import title_images as router

    real, raced = router._load, []

    def racing(db, user, title_id):  # noqa: ANN001, ANN202
        loaded = real(db, user, title_id)
        if not raced:
            raced.append(True)
            with db_factory() as s:
                t = s.get(MediaTitle, MOVIE)
                t.images = mutate(dict(t.images))
                s.commit()
        return loaded

    monkeypatch.setattr(router, "_load", racing)


def _a_deletes_backdrop_0(images: dict) -> dict:
    return {**images, "Backdrop.0": images["Backdrop.1"], "Backdrop.1": images["Backdrop.2"], "Backdrop.2": title_images.tombstone()}


def test_a_racing_delete_cannot_leave_a_backdrop_gap(env, monkeypatch) -> None:  # noqa: ANN001
    """The route's pre-check saw three backdrops; another delete lands first, so slot 3 is now past the end: 422, nothing written."""
    client, title, db_factory = env
    c = client()
    add_backdrops(c, 3)
    _race_on_first_load(monkeypatch, db_factory, _a_deletes_backdrop_0)
    reply = put(c, MOVIE, "Backdrop", 3, b"\xff\xd8\xff" + b"race" * 5, base="")
    assert (reply.status_code, reply.json()["detail"]) == (422, "index_gap") and "Backdrop.3" not in title().images
    monkeypatch.undo()
    monkeypatch.setattr(title_images, "encode", lambda *a, **k: Encoded("e" * 64, "image/jpeg", 200, 300, b"x"))
    assert choose(c, MOVIE, "Backdrop", 2, "/b9.jpg").status_code == 200   # three again
    _race_on_first_load(monkeypatch, db_factory, _a_deletes_backdrop_0)
    reply = choose(c, MOVIE, "Backdrop", 3, "/b10.jpg")
    assert (reply.status_code, reply.json()["detail"]) == (422, "index_gap") and "Backdrop.3" not in title().images


def test_the_same_image_cannot_sit_in_two_backdrop_slots(env) -> None:  # noqa: ANN001
    """A repeated backdrop tag would lock reorder out (a repeat is 422, the set without it 409)."""
    client, _title, db_factory = env
    c = client()
    add_backdrops(c, 2)
    known = edits(db_factory)
    dup = choose(c, MOVIE, "Backdrop", 2, "/b0.jpg")
    assert (dup.status_code, dup.json()["detail"]) == (409, "duplicate") and edits(db_factory) == known
    data = b"\xff\xd8\xff" + b"same" * 5
    assert put(c, MOVIE, "Backdrop", 2, data).status_code == 200
    again = put(c, MOVIE, "Backdrop", 3, data)
    assert (again.status_code, again.json()["detail"]) == (409, "duplicate")
    assert choose(c, MOVIE, "Backdrop", 0, "/b0.jpg", base="tmdb:/b0.jpg").status_code == 200  # a slot may keep its own image
    assert choose(c, MOVIE, "Primary", 0, "/b0.jpg", base="Movie (2020)/poster.png").status_code == 200  # other kinds may share it
    t = ["tmdb:/b0.jpg", "tmdb:/b1.jpg", hashlib.sha256(data).hexdigest()]
    assert tags(c.post(f"/api/titles/{MOVIE}/images/Backdrop/order", json={"tags": t[::-1]}).json()) == t[::-1]


def test_uploads_past_the_storage_cap_are_507(env, monkeypatch) -> None:  # noqa: ANN001
    """The stored upload bytes are capped (2 GiB); a repeat of stored bytes adds nothing and is allowed."""
    client, title, db_factory = env
    assert title_images.MAX_STORED_BYTES == 2 * 1024**3
    monkeypatch.setattr(title_images, "MAX_STORED_BYTES", 100)
    c = client()
    first = b"\xff\xd8\xff" + b"a" * 60
    assert put(c, MOVIE, "Backdrop", 0, first).status_code == 200
    known = edits(db_factory)
    full = put(c, MOVIE, "Backdrop", 1, b"\xff\xd8\xff" + b"b" * 60)
    assert (full.status_code, full.json()) == (507, {"detail": "storage_full"}) and edits(db_factory) == known
    assert "Backdrop.1" not in title().images
    with db_factory() as s:
        assert len(s.scalars(select(TitleUpload)).all()) == 1
    assert put(c, MOVIE, "Primary", 0, first, base="Movie (2020)/poster.png").status_code == 200


def test_a_stalled_upload_body_times_out(env, monkeypatch) -> None:  # noqa: ANN001
    """An upload whose body stops arriving is a 408 after the idle timeout, before any encoder slot is taken."""
    import asyncio

    import httpx

    from app import http_boundaries
    from app.main import app

    client, _title, _db = env
    client()  # installs the auth override
    monkeypatch.setattr(http_boundaries, "UPLOAD_IDLE_TIMEOUT_SECONDS", 0.3)
    encoded: list = []
    monkeypatch.setattr(title_images, "encode", lambda *a, **k: encoded.append(1))

    async def stalled():
        yield b"\xff\xd8\xff" + b"x" * 100
        await asyncio.sleep(30)
        yield b"never"

    async def go():  # noqa: ANN202
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as ac:
            return await asyncio.wait_for(ac.put(url(MOVIE, "Backdrop", 0), content=stalled(), headers={"content-type": "image/jpeg"}), 10)

    reply = asyncio.run(go())
    assert (reply.status_code, reply.json()["detail"]) == (408, "body_timeout") and encoded == []


def test_candidate_previews_charge_the_artwork_bucket_on_a_cold_fetch_and_use_a_capped_cache(env, monkeypatch) -> None:  # noqa: ANN001
    """A cold candidate fetch costs the per-user "artwork" bucket; previews live in their own size-capped LRU cache."""
    from fastapi import HTTPException

    client, _title, _db = env
    seen: list = []

    def load(artwork, path, kind, *, size=None, **pinned):  # noqa: ANN001, ANN202
        from app.services.artwork import ArtworkResponse
        seen.append(pinned)
        return ArtworkResponse(content=b"img", content_type="image/jpeg")

    monkeypatch.setattr(tmdb, "load_image", load)
    assert client().get("/api/metadata/candidate-image", params={"path": "/p.jpg", "type": "Backdrop"}).status_code == 200
    pinned = seen[0]
    assert (pinned["bucket"], pinned["max_bytes"]) == ("candidates", tmdb.CANDIDATE_CACHE_BYTES)
    for _ in range(120):
        pinned["before_upstream_fetch"]()
    with pytest.raises(HTTPException) as raised:
        pinned["before_upstream_fetch"]()
    assert raised.value.status_code == 429


def test_choosing_a_tmdb_image_spends_the_artwork_bucket(env) -> None:  # noqa: ANN001
    """Choose pins into the TMDB cache, so it shares the candidate proxy's per-user bucket (120/min)."""
    client, _title, _db = env
    c = client()
    codes = [choose(c, MOVIE, "Backdrop", 0, f"/c{i}.jpg").status_code for i in range(121)]
    assert codes[-1] == 429 and 429 not in codes[:120]
