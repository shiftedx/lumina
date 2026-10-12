"""Seed a small taste-structured household for the recommendation replay (no network).

    backend/.venv/bin/python scripts/perf/seed_reco.py "$TMPDIR/lumina-reco"
    backend/.venv/bin/python scripts/perf/reco_replay.py "$TMPDIR/lumina-reco"

Four members with distinct tastes (channels, Interest categories, film genres), timestamped watch histories,
follows with feed_entries, saves, favorites, one suppression, one follow made after every instance, and a
Popular snapshot file. Enough history for 10 remote and 10 title test instances per member, with Up Next pairs.
Deterministic: the same rows and snapshot on every run. Refuses a non-empty directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE = datetime(2026, 9, 1, 12, 0, 0)  # the snapshot's refresh time; every watch is before it
# member -> (Interest categories, own channels, film genres)
TASTES = {
    "ana": (("cooking", "travel"), ("Kitchen Lab", "Pasta Nonna", "Wander Far"), ("Food", "Documentary")),
    "ben": (("gaming", "science-technology"), ("Speedrun Hub", "Byte Size", "Orbit Lab"), ("Science Fiction", "Action")),
    "cleo": (("music", "live-performances"), ("Stage Lights", "Vinyl Hour", "Choir Room"), ("Music", "Romance")),
    "dev": (("sports", "cars"), ("Track Day", "Gym Theory", "Pit Lane"), ("Sport", "Thriller")),
}
CHANNEL_CATEGORY = {
    "Kitchen Lab": "cooking", "Pasta Nonna": "cooking", "Wander Far": "travel",
    "Speedrun Hub": "gaming", "Byte Size": "science-technology", "Orbit Lab": "science-technology",
    "Stage Lights": "live-performances", "Vinyl Hour": "music", "Choir Room": "music",
    "Track Day": "cars", "Gym Theory": "sports", "Pit Lane": "cars",
    "News Desk": "news", "Laugh Track": "comedy", "Chart Toppers": "music",  # household-wide noise
}
VIDEOS_PER_CHANNEL = 12  # a channel's catalogue: one upload about every 10 days
SNAPSHOT_PER_CHANNEL = 4  # its newest uploads in the Popular snapshot (then 8 per category, 24 overall)
MOVIES_PER_GENRE = 12
SESSIONS = 15  # remote viewing sessions per member, two watches each, under an hour apart
FILM_WATCHES = 16


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


SECOND_GENRES = ("Drama", "Comedy", "Adventure", "History")  # a film's second genre, shared across tastes as in a real library


def film_people(genre: str, n: int) -> list[dict]:
    """A real film's NFO credits five actors and a director. One recurring lead per genre (every third film),
    the rest the film's own, so films share a few people rather than whole token sets."""
    cast = [f"{genre} star {n % 3}", *(f"{genre} actor {n}-{i}" for i in range(4))]
    return [*({"name": name, "type": "Actor"} for name in cast), {"name": f"{genre} director {n}", "type": "Director"}]


def _channel_url(channel: str) -> str:
    return f"https://www.youtube.com/@{_slug(channel)}"  # the address a follow stores and yt-dlp's flat entries carry


def _ts(moment: datetime) -> float:
    return moment.replace(tzinfo=UTC).timestamp()


def catalogue(rng: random.Random) -> dict[str, list[dict]]:
    """channel -> its uploads, newest first, as Popular items (published_at in epoch seconds)."""
    videos: dict[str, list[dict]] = {}
    for channel in CHANNEL_CATEGORY:
        big = channel == "Chart Toppers"
        videos[channel] = [
            {
                "id": f"{_slug(channel)}-{n}", "title": f"{channel} upload {n}", "uploader": channel, "duration": 600,
                "thumbnail": None, "webpage_url": f"https://www.youtube.com/watch?v={_slug(channel)}-{n}", "uploader_url": _channel_url(channel),
                "view_count": 20_000_000 if big else rng.randint(20_000, 3_000_000), "availability": "public",
                "published_at": _ts(BASE - timedelta(days=n * 10 + rng.randint(0, 4))), "source": "youtube", "source_label": "YouTube",
            }
            for n in range(VIDEOS_PER_CHANNEL)
        ]
    return videos


def popular_store(videos: dict[str, list[dict]]) -> dict:
    """The popular-discovery.json store as PopularDiscovery writes it (store version 1)."""
    categories: dict[str, dict] = {}
    for channel, items in videos.items():
        record = categories.setdefault(CHANNEL_CATEGORY[channel], {
            "items": [], "last_success_at": _ts(BASE), "next_refresh_at": _ts(BASE + timedelta(minutes=30)), "error": None,
        })
        record["items"].extend(items[:SNAPSHOT_PER_CHANNEL])
    for record in categories.values():
        record["items"] = sorted(record["items"], key=lambda item: -item["view_count"])[:8]
    return {"version": 1, "cursor": 0, "categories": categories, "next_batch_at": 0.0,
            "last_success_at": _ts(BASE), "refreshed_at": _ts(BASE), "last_error": None}


def feed_entries(videos: list[dict]) -> list[dict]:
    """A follow's last-known newest uploads, shaped like source_automation's PreviewEntry dump."""
    return [
        {"id": video["id"], "title": video["title"], "duration": video["duration"], "thumbnail": None,
         "webpage_url": video["webpage_url"], "uploader": video["uploader"], "channel_url": video["uploader_url"], "availability": "public",
         "published_at": datetime.fromtimestamp(video["published_at"], UTC).isoformat(), "media_kind": "video", "capabilities": None}
        for video in videos
    ]


def _depth(completed: bool, fraction: float) -> dict:
    """Watch depth as 1.9.0 writes it and schema step 9 backfills it: one play, its furthest fraction, a completion."""
    return {"plays": 1, "completions": int(completed), "max_fraction": 1.0 if completed else min(1.0, fraction)}


def seed(session, data_dir: Path) -> None:  # noqa: ANN001
    """Add the household to ``session`` (committed) and write ``data_dir/popular-discovery.json``."""
    from app.models import (
        LibraryItem, MediaTitle, MemberFavorite, MemberInterest, MemberRecommendationSuppression, PlaybackProgress, RecoPool,
        RemoteMedia, RemotePlaybackProgress, SourceAutomation, User,
    )

    from app.services.reco.events import channel_key
    from app.services.reco.profile import remote_tokens

    rng = random.Random(11)
    videos = catalogue(rng)
    rows: list = []
    ids = iter(range(1, 1_000_000))

    def uid() -> str:
        return f"00000000-0000-4000-8000-{next(ids):012d}"

    genres = sorted({genre for _i, _c, member_genres in TASTES.values() for genre in member_genres})
    movies: dict[str, list[tuple[str, str]]] = {}  # genre -> (title id, version item id)
    for genre in genres:
        for n in range(MOVIES_PER_GENRE):
            movie_id, item_id = uid(), uid()
            # Most films arrived long ago; the last one per genre arrives after every instance (hidden by the replay).
            arrived = BASE - timedelta(days=1 if n == MOVIES_PER_GENRE - 1 else 300 - n)
            rows.append(MediaTitle(
                id=movie_id, type="movie", key=f"reco:{movie_id}", name=f"{genre} film {n}", year=1990 + 2 * n,
                metadata_json={"genres": [genre, SECOND_GENRES[(n + genres.index(genre)) % len(SECOND_GENRES)]],
                               "people": film_people(genre, n), "community_rating": 5.0 + (n % 7) * 0.5},
                created_at=arrived, added_at=arrived,
            ))
            rows.append(LibraryItem(
                id=item_id, user_id=None, visibility="shared", title=f"{genre} film {n}", title_id=movie_id, kind="movie",
                status="available", metadata_json={}, created_at=arrived, downloaded_at=arrived,
            ))
            movies.setdefault(genre, []).append((movie_id, item_id))

    for name, (interests, channels, member_genres) in TASTES.items():
        rows.append(User(id=name, username=name, display_name=name.title(), role="viewer", is_active=True,
                         onboarding_status="completed", created_at=BASE - timedelta(days=200)))
        rows += [MemberInterest(id=uid(), user_id=name, category_key=key, created_at=BASE - timedelta(days=150)) for key in interests]
        rows += [
            SourceAutomation(id=uid(), user_id=name, label=channel, source_url=_channel_url(channel),
                             source_type="channel", cron_expression="0 * * * *", created_at=BASE - timedelta(days=140),
                             feed_entries=feed_entries(videos[channel]))
            for channel in channels[:2]
        ]
        # A follow made after every instance: the replay must hide it.
        rows.append(SourceAutomation(id=uid(), user_id=name, label=channels[2], source_url=_channel_url(channels[2]),
                                     source_type="channel", cron_expression="0 * * * *", created_at=BASE - timedelta(minutes=30),
                                     feed_entries=feed_entries(videos[channels[2]])))
        rows += [
            LibraryItem(id=uid(), user_id=name, visibility="private", extractor="youtube", remote_id=f"{_slug(channels[0])}-saved{n}",
                        title=f"{channels[0]} saved {n}", uploader=channels[0], kind="video", status="available", metadata_json={},
                        created_at=BASE - timedelta(days=120 + n), downloaded_at=BASE - timedelta(days=120 + n))
            for n in range(2)
        ]
        if name == "ana":
            rows.append(MemberRecommendationSuppression(id=uid(), user_id=name, scope="channel", target_key="laugh track",
                                                        channel_name="Laugh Track", created_at=BASE - timedelta(days=130)))

        others = [channel for channel in CHANNEL_CATEGORY if channel not in channels]
        watched: set[str] = set()
        for session_no in range(SESSIONS):  # oldest first, about every 6.5 days over 100 days
            at = BASE - timedelta(days=100 - session_no * 6.5, hours=rng.randint(0, 8))
            for pair in range(2):
                at += timedelta(minutes=rng.randint(20, 50))
                channel = rng.choice(channels) if rng.random() < 0.8 else rng.choice(others)
                out = [video for video in videos[channel] if video["published_at"] <= _ts(at) and video["id"] not in watched]
                video_id = (out[0] if rng.random() < 0.6 else rng.choice(out))["id"] if out else f"{_slug(channel)}-old{session_no}{pair}-{name}"
                watched.add(video_id)
                roll = rng.random()
                completed = roll < 0.75
                position = 600.0 if completed else (60.0 if roll > 0.9 else float(rng.randint(300, 540)))
                rows.append(RemotePlaybackProgress(
                    id=uid(), user_id=name, source_identity=f"youtube:{video_id}",
                    source_identity_key=hashlib.sha256(f"youtube:{video_id}".encode()).hexdigest(), extractor="youtube",
                    remote_id=video_id, source_url=f"https://www.youtube.com/watch?v={video_id}", title=video_id, uploader=channel,
                    position_seconds=position, duration_seconds=600.0, completed=completed, last_watched_at=at,
                    created_at=at - timedelta(minutes=10), channel_key=channel_key("youtube", None, _channel_url(channel), channel), **_depth(completed, position / 600),
                ))
        own = [movie for genre in member_genres for movie in movies[genre][:-1]]  # never the late arrivals
        for n, (_movie_id, item_id) in enumerate(rng.sample(own, FILM_WATCHES)):  # about every 7 days over 110 days
            at = BASE - timedelta(days=110 - n * 7, hours=20)
            completed = n % 8 != 3  # two abandoned films (30 %) per member: started, not satisfied
            rows.append(PlaybackProgress(id=uid(), user_id=name, item_id=item_id, position_seconds=5400 if completed else 1600,
                                         duration_seconds=5400, completed=completed, last_watched_at=at, created_at=at - timedelta(hours=2),
                                         **_depth(completed, (5400 if completed else 1600) / 5400)))
        rows.append(MemberFavorite(user_id=name, target_id=movies[member_genres[0]][0][0], created_at=BASE - timedelta(days=130)))

    # The recommender's persisted pools. A stub refresher run per cutoff cannot be replayed inside the
    # harness's rolled-back transaction, so the pools are rows with first_seen_at: each upload is pooled half a day after it is published.
    # R2's tests cover the refresher itself.
    for channel, uploads in videos.items():
        for video in uploads:
            rows.append(RemoteMedia(
                key=hashlib.sha256(f"youtube:{video['id']}".encode()).hexdigest(), source_identity=f"youtube:{video['id']}", extractor="youtube",
                remote_id=video["id"], webpage_url=video["webpage_url"], title=video["title"], uploader=channel, duration=video["duration"],
                view_count=video["view_count"], published_at=datetime.fromtimestamp(video["published_at"], UTC).replace(tzinfo=None), kind="video",
                channel_key=channel_key("youtube", None, video["uploader_url"], channel), channel_url=video["uploader_url"],
                category_keys=[CHANNEL_CATEGORY[channel]], fetched_at=BASE, last_nominated_at=BASE,
                tokens=sorted(remote_tokens(video["title"], channel_key("youtube", None, video["uploader_url"], channel), [CHANNEL_CATEGORY[channel]]))[:40],
            ))
    for name, (interests, channels, _genres) in TASTES.items():
        for channel in CHANNEL_CATEGORY:
            if channel in channels[:2]:
                source = 1  # SOURCE_FOLLOW
            elif channel == channels[2]:
                source = 1  # the late follow: its nominations are first seen after every instance, so the replay must hide them
            elif CHANNEL_CATEGORY[channel] in interests:
                source = 4  # SOURCE_SEED
            else:
                continue
            for video in videos[channel]:
                published = datetime.fromtimestamp(video["published_at"], UTC).replace(tzinfo=None)
                first = BASE - timedelta(minutes=30) if channel == channels[2] else published + timedelta(hours=12)
                rows.append(RecoPool(user_id=name, item_key=hashlib.sha256(f"youtube:{video['id']}".encode()).hexdigest(), sources=source,
                                     first_seen_at=first, last_nominated_at=max(first, published)))

    session.add_all(rows)
    session.commit()
    (data_dir / "popular-discovery.json").write_text(json.dumps(popular_store(videos), sort_keys=True), encoding="utf-8")


def main() -> None:
    data_dir = Path(sys.argv[1]).resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        sys.exit(f"{data_dir} is not empty; seed a fresh directory")
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LUMINA_DATA_DIR"] = str(data_dir)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.db import SessionLocal, init_db

    init_db()
    with SessionLocal() as session:
        seed(session, data_dir)
    print(f"seeded {data_dir}")


if __name__ == "__main__":
    main()
