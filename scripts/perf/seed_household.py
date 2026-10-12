"""Seed a household-scale Lumina data dir for load measurement (no network).

    backend/.venv/bin/python scripts/perf/seed_household.py /tmp/lumina-perf

Refuses a non-empty directory. Every member's password is PASSWORD below.
"""
from __future__ import annotations

import os
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/lumina-perf").resolve()
if DATA_DIR.exists() and any(DATA_DIR.iterdir()):
    sys.exit(f"{DATA_DIR} is not empty; seed a fresh directory")
os.environ["LUMINA_DATA_DIR"] = str(DATA_DIR)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy import insert  # noqa: E402

from app import models as m  # noqa: E402
from app.db import engine, init_db, session_scope  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.library import EXTERNAL_LIBRARY_ORIGIN, LibraryService  # noqa: E402
from app.services.library_search import ensure_search_index  # noqa: E402
from app.services.storage_roots import StorageRootService  # noqa: E402
from app.services.user_settings import UserSettingsService  # noqa: E402
from app.services.yt_dlp_service import YtDlpService  # noqa: E402

PASSWORD = "perf-household-password"
USERS = ["perfadmin", "alex", "blair", "casey", "devon", "emery"]
ITEMS, FOLLOWS, NOTES, TRANSCRIPTS, CUES, COLLECTIONS, PROGRESS, JOBS = 20_000, 300, 5_000, 500, 1_000, 200, 50_000, 5_000
WORDS = (
    "river forest light ocean mountain city night garden coffee piano guitar history science space robot "
    "kitchen bread travel winter summer desert jazz orbit signal quiet storm engine island harvest canyon "
    "lantern meadow ember glacier harbor falcon violet copper marble thunder willow cedar prairie aurora"
).split()
rng = random.Random(73)
BASE = datetime(2026, 1, 1)


def uid() -> str:
    return str(uuid.uuid4())


def words(n: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n))


def bulk(model, rows: list[dict]) -> None:
    with engine.begin() as connection:
        for start in range(0, len(rows), 5000):
            connection.execute(insert(model), rows[start:start + 5000])


def main() -> None:
    init_db()
    with session_scope() as db:
        YtDlpService(db).ensure_app_settings()
        password_hash = hash_password(PASSWORD)
        users = [
            m.User(id=uid(), username=name, display_name=name.title(), password_hash=password_hash,
                   role="admin" if index == 0 else "viewer", onboarding_status="complete")
            for index, name in enumerate(USERS)
        ]
        db.add_all(users)
        db.flush()
        UserSettingsService(db).ensure_for_all_users()
        managed = StorageRootService(db).library_root()
        external_path = DATA_DIR / "external-media"
        external_path.mkdir()
        external = m.StorageRoot(id=uid(), label="Media shelf", path=str(external_path), mode="external",
                                 identity=str(external_path.stat().st_dev), observation={"state": "available"})
        db.add(external)
        managed_id, external_id = managed.id, external.id
    user_ids = [user.id for user in users]

    items, artifacts, links = [], [], []
    series = [f"{words(2).title()} Chronicles" for _ in range(80)]
    artists = [words(2).title() for _ in range(250)]
    for index in range(ITEMS):
        roll = index % 20
        owner = user_ids[index % len(user_ids)]
        meta: dict = {"description": words(80), "formats": [{"format_id": str(f), "url": "x" * 200} for f in range(30)]}
        extractor, uploader, playlist, root_id = "Youtube", words(2).title(), None, managed_id
        if roll < 10:
            kind = "video"
            extractor = "Twitch" if roll == 9 else "Youtube"
            meta.update(channel=uploader, ext="mp4", view_count=rng.randint(10, 10**6))
        elif roll < 12:
            kind, extractor, root_id = "movie", EXTERNAL_LIBRARY_ORIGIN, external_id
            meta.update(lumina_import_kind="movie", release_year=rng.randint(1970, 2025), ext="mkv")
            uploader = None
        elif roll < 16:
            kind, extractor, root_id = "episode", EXTERNAL_LIBRARY_ORIGIN, external_id
            uploader = rng.choice(series)
            season = rng.randint(1, 5)
            playlist = f"Season {season}"
            meta.update(lumina_import_kind="episode", series=uploader, season_number=season, episode_number=index % 24 + 1, ext="mkv")
        elif roll < 19:
            kind, extractor, root_id = "track", EXTERNAL_LIBRARY_ORIGIN, external_id
            uploader = rng.choice(artists)
            playlist = f"{words(2).title()} (Album)"
            meta.update(lumina_import_kind="track", artist=uploader, album=playlist, track_number=index % 12 + 1, ext="flac")
            meta.pop("formats")
        else:
            kind = "audio"
            meta.update(ext="m4a", channel=uploader)
        item_id = uid()
        when = BASE + timedelta(minutes=index * 7)
        items.append(dict(
            id=item_id, user_id=owner, visibility="shared" if index % 3 else "private", extractor=extractor,
            remote_id=f"r{index}", title=f"{words(4).title()} {index}", uploader=uploader, playlist_name=playlist,
            duration=rng.randint(120, 7200), thumbnail_url=None, webpage_url=f"https://example.invalid/{index}",
            file_path=None, file_size=rng.randint(10**6, 4 * 10**9), downloaded_at=when, availability="public",
            metadata_json=meta, metadata_summary=LibraryService.summarize_metadata(meta),
            status="missing" if index % 97 == 0 else "available", kind=kind, created_at=when, updated_at=when,
        ))
        artifact_id = uid()
        artifacts.append(dict(id=artifact_id, root_id=root_id, relative_path=f"{kind}/{index}.bin",
                              ownership="managed" if root_id == managed_id else "external", owner_user_id=owner,
                              lifecycle="available", size=1, created_at=when, updated_at=when))
        links.append(dict(library_item_id=item_id, artifact_id=artifact_id, created_at=when))
    bulk(m.LibraryItem, items)
    bulk(m.MediaArtifact, artifacts)
    bulk(m.LibraryItemArtifact, links)
    item_ids = [row["id"] for row in items]
    visible = {user: [row["id"] for row in items if row["visibility"] == "shared" or row["user_id"] == user] for user in user_ids}

    bulk(m.SourceAutomation, [dict(
        id=uid(), user_id=user_ids[index % len(user_ids)], label=f"{words(2).title()} channel",
        source_url=f"https://www.youtube.com/@perf{index}", source_type="channel", cron_expression="0 * * * *",
        active=True, next_check_at=BASE + timedelta(days=3650), last_checked_at=BASE,
        feed_entries=[dict(id=f"f{index}-{n}", title=words(5).title(), duration=600, webpage_url=f"https://www.youtube.com/watch?v=f{index}x{n}",
                           uploader="Perf", published_at=(BASE - timedelta(hours=n)).isoformat(), media_kind="video") for n in range(12)],
        format_selection={}, output_profile={}, rules={}, last_run_summary={}, created_at=BASE, updated_at=BASE,
    ) for index in range(FOLLOWS)])

    bulk(m.LibraryNote, [dict(
        id=uid(), item_id=item_ids[index % 800], user_id=user_ids[index % len(user_ids)],
        visibility="household" if index % 2 else "private", timestamp_ms=index * 1000 if index % 3 else None,
        body=words(20), created_at=BASE + timedelta(minutes=index), updated_at=BASE + timedelta(minutes=index),
    ) for index in range(NOTES)])

    transcripts, cues = [], []
    for index in range(TRANSCRIPTS):
        transcript_id = uid()
        transcripts.append(dict(id=transcript_id, library_item_id=item_ids[index], language="en", source_kind="source_caption",
                                revision=1, source_digest=uuid.uuid4().hex, cue_count=CUES, created_at=BASE))
        cues.extend(dict(transcript_id=transcript_id, ordinal=n, start_ms=n * 3000, end_ms=n * 3000 + 2900, text=words(9)) for n in range(CUES))
    bulk(m.Transcript, transcripts)
    bulk(m.TranscriptCue, cues)

    collections, members = [], []
    for index in range(COLLECTIONS):
        owner = user_ids[index % len(user_ids)]
        collection_id = uid()
        name = f"{words(2).title()} {index}"
        collections.append(dict(id=collection_id, owner_user_id=owner, name=name, name_key=name.casefold(),
                                visibility="shared" if index % 2 else "private", revision=0, created_at=BASE, updated_at=BASE))
        for position, item_id in enumerate(rng.sample(visible[owner], 25)):
            members.append(dict(id=uid(), collection_id=collection_id, position=position, library_item_id=item_id,
                                added_by_user_id=owner, created_at=BASE))
    bulk(m.HouseholdCollection, collections)
    bulk(m.HouseholdCollectionMembership, members)

    bulk(m.WatchQueue, [dict(user_id=user, revision=0) for user in user_ids])
    bulk(m.WatchQueueEntry, [dict(id=uid(), user_id=user, position=position, library_item_id=item_id, created_at=BASE)
                             for user in user_ids for position, item_id in enumerate(rng.sample(visible[user], 40))])
    bulk(m.SearchHistoryEntry, [dict(id=uid(), user_id=user, query=f"{words(2)} {n}", query_key=f"{words(2)} {n}",
                                     searched_at=BASE + timedelta(minutes=n)) for user in user_ids for n in range(50)])

    per_user = PROGRESS // len(user_ids)
    bulk(m.PlaybackProgress, [dict(
        id=uid(), user_id=user, item_id=item_id, position_seconds=rng.randint(10, 3000), duration_seconds=3600,
        completed=rng.random() < 0.4, last_watched_at=BASE + timedelta(minutes=rng.randint(0, 200_000)),
        created_at=BASE, updated_at=BASE,
    ) for user in user_ids for item_id in rng.sample(visible[user], min(per_user, len(visible[user])))])

    statuses = ("completed", "completed", "completed", "failed", "cancelled")
    bulk(m.DownloadJob, [dict(
        id=uid(), user_id=user_ids[index % len(user_ids)], source_url=f"https://www.youtube.com/watch?v=j{index}",
        status=statuses[index % len(statuses)], format_selection={}, output_profile={},
        preview_snapshot={"title": words(5).title(), "uploader": "Perf", "duration": 600},
        error="Provider refused" if index % 5 == 3 else None,
        attempts=[{"status": "failed", "error": "timeout", "started_at": None, "finished_at": None}] * (index % 3),
        created_at=BASE + timedelta(minutes=index), started_at=BASE + timedelta(minutes=index), finished_at=BASE + timedelta(minutes=index + 3),
    ) for index in range(JOBS)])

    ensure_search_index(engine)
    print(f"seeded {DATA_DIR}: {len(items)} items, {len(cues)} cues; login {USERS} / {PASSWORD}")


if __name__ == "__main__":
    main()
