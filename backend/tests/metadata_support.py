"""One fixture for the editor API tests: seed_tree + an admin, a member, settings, the FTS index. Read-only for tracks."""
from __future__ import annotations

import pytest

from app.models import MediaTitle, User
from app.services import library_search
from support import make_user, seed_app_settings
from title_support import ALICE, BOB, MOVIE, SERIES, seed_tree

ADMIN = "admin-1"


@pytest.fixture
def world(db_factory, tmp_path, api_client):  # noqa: ANN001, ANN201
    """(db_factory, client_for) where client_for(user_id, **settings) logs in as that user; alice is a member."""
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ADMIN, role="admin", username="admin"), make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        seed_app_settings(session)  # commits
        library_search.ensure_search_index(session.get_bind())
        for title_id in (SERIES, MOVIE):
            library_search.index_title(session, session.get(MediaTitle, title_id))
        session.commit()

    def client_for(user_id: str):  # noqa: ANN202
        with db_factory() as session:
            return api_client(user=session.get(User, user_id), base_url="http://localhost")

    return db_factory, client_for


def allow_members(db_factory, on: bool = True) -> None:  # noqa: ANN001
    from app.models import AppSettings
    with db_factory() as session:
        session.get(AppSettings, 1).members_edit_metadata = on
        session.commit()


def _fake_t1(mp) -> None:  # noqa: ANN001
    """Stand in for the edit-model primitives (user>nfo>tmdb>path, kept values, lock)."""
    from datetime import datetime as _dt

    from app.services import media_titles as mt

    real_apply = mt.apply_field

    def shown(title, field):  # noqa: ANN001, ANN202
        if field in ("added_at",):
            v = getattr(title, field)
            return v.isoformat() if v else None
        if field in mt.TITLE_COLUMNS:
            return getattr(title, field)
        return (title.metadata_json or {}).get(field)

    def raw_set(title, field, value, source):  # noqa: ANN001, ANN202
        if field in mt.TITLE_COLUMNS:
            setattr(title, field, value)
        else:
            title.metadata_json = {**(title.metadata_json or {}), field: value}
        title.field_sources = {**(title.field_sources or {}), field: source}

    def apply(title, field, value, source):  # noqa: ANN001, ANN202
        sources = title.field_sources or {}
        if source == "user":
            raw_set(title, field, value, "user")
            return True
        if title.locked or sources.get(field) == "user":
            if value is not None:  # a blocked write is kept for revert
                title.source_values = {**(title.source_values or {}), field: {"source": source, "value": value}}
            return False
        return real_apply(title, field, value, source)

    def keep(title, field):  # noqa: ANN001, ANN202
        if (title.field_sources or {}).get(field) != "user":
            title.source_values = {**(title.source_values or {}), field: {"source": (title.field_sources or {}).get(field), "value": shown(title, field)}}

    def revert(title, field, now):  # noqa: ANN001, ANN202
        if (title.field_sources or {}).get(field) != "user":
            return False
        kept = dict(title.source_values or {})
        entry = kept.pop(field, None)
        title.source_values = kept
        sources = {k: v for k, v in (title.field_sources or {}).items() if k != field}
        if entry and entry["source"]:
            raw_set(title, field, entry["value"], entry["source"])
            if entry["source"] == "tmdb" and title.type in mt.MATCHABLE_TYPES:
                title.metadata_due_at = now
        else:
            title.field_sources = sources
            if field in mt.TITLE_COLUMNS:
                if field != "name":
                    setattr(title, field, {} if field == "provider_ids" else None)
            else:
                title.metadata_json = {k: v for k, v in (title.metadata_json or {}).items() if k != field}
        return True

    def lock(title, locked, now):  # noqa: ANN001, ANN202
        if bool(title.locked) == locked:
            return False
        title.locked = locked
        if locked:
            title.metadata_due_at = None
            return True
        for field, entry in dict(title.source_values or {}).items():
            if (title.field_sources or {}).get(field) != "user" and entry["source"]:
                raw_set(title, field, entry["value"], entry["source"])
                title.source_values = {k: v for k, v in title.source_values.items() if k != field}
        if title.type in mt.MATCHABLE_TYPES:
            title.metadata_due_at = now
        return True

    for name, fn in (("apply_field", apply), ("keep_source_value", keep), ("revert_field", revert), ("set_item_locked", lock)):
        mp.setattr(mt, name, fn)
    mp.setattr("app.services.title_metadata.apply_field", apply)


@pytest.fixture(autouse=True)
def t1_seam(monkeypatch):  # noqa: ANN001, ANN201
    from app.services import media_titles as mt
    try:
        mt.keep_source_value(MediaTitle(), "x")
    except NotImplementedError:
        _fake_t1(monkeypatch)
