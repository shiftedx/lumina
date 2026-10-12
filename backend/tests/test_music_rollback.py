"""Library gallery T3: the documented rollback from schema 8 to 1.5.1, run exactly as docker/README.md prints it."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app.models import MemberFavorite, PlaybackProgress
from support import make_user
from test_schema_version import _columns, _init
from title_support import ALBUM, ALICE, BOB, MOVIE, TRACKS, seed_gallery, seed_tree

REPO = Path(__file__).resolve().parents[2]


def rollback_sql() -> str:
    match = re.search(r"sqlite3 app-data/app\.db <<'SQL'\n(.*?)\nSQL\n", (REPO / "docker" / "README.md").read_text(encoding="utf-8"), re.S)
    assert match, "docker/README.md has no schema 8 rollback block"
    return match.group(1)


def test_the_readme_rollback_keeps_member_data_and_upgrading_again_is_safe(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    engine = _init(tmp_path, monkeypatch)
    root = tmp_path.resolve() / "media"
    with sessionmaker(bind=engine)() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        seed_gallery(session, root)
        session.add_all([
            MemberFavorite(user_id=ALICE, target_id=ALBUM), MemberFavorite(user_id=ALICE, target_id=MOVIE),
            PlaybackProgress(id="track-progress", user_id=ALICE, item_id=TRACKS[0], position_seconds=30, duration_seconds=180,
                             completed=False, last_watched_at=datetime(2026, 9, 29)),
        ])
        session.commit()
    engine.dispose()
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.executescript(rollback_sql())
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert connection.execute("SELECT count(*) FROM media_titles WHERE type IN ('album', 'artist')").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM library_items WHERE kind = 'track' AND title_id IS NULL").fetchone()[0] == 3
        assert connection.execute("SELECT target_id FROM member_favorites").fetchall() == [(MOVIE,)]  # album favourites are the loss
        assert connection.execute("SELECT item_id, position_seconds FROM playback_progress").fetchall() == [(TRACKS[0], 30)]
    # 1.5.1 reads every table and column it knew: nothing was dropped (step 8's columns stay and are ignored).
    assert _columns(engine) == json.loads((Path(__file__).parent / "schema_snapshot.json").read_text())["columns"]
    upgraded = _init(tmp_path, monkeypatch)  # upgrading again re-runs step 8 over the rolled-back database
    with upgraded.connect() as connection:
        assert connection.scalar(text("PRAGMA user_version")) == db_module.SCHEMA_VERSION
        assert connection.scalar(text("SELECT category FROM media_titles WHERE id = :id"), {"id": MOVIE}) == "movies"
