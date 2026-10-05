"""Seed a production-scale media library for the gallery budgets.

    backend/.venv/bin/python scripts/perf/seed_titles.py DATA_DIR [--movies 1735] [--series 850] [--episodes 37500] [--progress 10700]
        [--anime-series 60] [--anime-movies 20] [--albums 120] [--tracks 1400]

Refuses a non-empty DATA_DIR. DATA_DIR/data is the app root and DATA_DIR/media the one external root, with the
24 sample images from sample_art.py in media/art/. Every member's password is PASSWORD. Anime titles have files under TV/Anime and Movies/Anime; albums hold `kind = track` items, with a folder cover.jpg (6 in 10), an embedded cover in a real 1 s FLAC (3 in 10) or none. The last stdout line is a
JSON summary. Deterministic (random.Random(7)); no network, no personal media.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASSWORD = "perf-household-password"
USERS = ("perfadmin", "alex", "blair")
GENRES = (
    "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy",
    "History", "Horror", "Music", "Mystery", "Romance", "Science Fiction", "Thriller", "War", "Western",
)
WORDS = (
    "amber", "bridge", "cedar", "delta", "ember", "falcon", "garden", "harbor", "island", "juniper", "kestrel", "lantern",
    "meadow", "north", "orbit", "prairie", "quarry", "river", "signal", "tundra", "umber", "violet", "willow", "xenon",
    "yonder", "zephyr",
)
RESOLUTIONS = ((0.20, (3840, 2160)), (0.55, (1920, 1080)), (0.15, (1280, 720)), (0.10, (720, 480)))  # 4k / 1080p / 720p / sd
COVERAGE = {  # share of titles with each image
    "movie": {"Primary": 0.97, "Backdrop": 0.96, "Logo": 0.94},
    "series": {"Primary": 0.96, "Backdrop": 0.93, "Logo": 0.91},
    "season": {"Primary": 0.60},
    "episode": {"Primary": 0.99},
}
SAMPLE_KIND = {"Primary": "poster", "Backdrop": "backdrop", "Logo": "logo"}  # episodes' Primary uses "still"
BIG_SERIES, BIG_SEASONS, BIG_EPISODES = 5, 10, 20  # 10 seasons x 20 episodes: the spec 3.4 detail budget
SEASON_WEIGHTS = (30, 25, 18, 8, 6, 5, 3, 3, 2)  # 1..9 seasons, skewed to 1-3
FAVORITES = 150
BASE = datetime(2024, 1, 1)
ANIME_EPISODES = 12  # one season per anime series


def embedded_flac(target: Path, cover: Path) -> None:
    """A 1 s FLAC carrying ``cover`` as its front-cover picture: an album whose only art is embedded."""
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=duration=1", "-i", str(cover),
         "-map", "0:a", "-map", "1:v", "-c:a", "flac", "-c:v", "copy", "-disposition:v", "attached_pic",
         "-metadata:s:v", "comment=Cover (front)", str(target)],
        check=True, timeout=60,
    )


def season_plans(rng: random.Random, series: int, episodes: int) -> list[list[int]]:
    """Episodes per season for every series, summing to ``episodes``; the first five are the big shows when they fit."""
    big = BIG_SERIES if series >= BIG_SERIES and episodes >= BIG_SERIES * BIG_SEASONS * BIG_EPISODES + series - BIG_SERIES else 0
    plans = [[BIG_EPISODES] * BIG_SEASONS for _ in range(big)]
    shapes = [rng.choices(range(1, 10), weights=SEASON_WEIGHTS)[0] for _ in range(series - big)]
    base, extra = divmod(episodes - big * BIG_SEASONS * BIG_EPISODES, max(1, sum(shapes)))
    for count in shapes:
        seasons = []
        for _ in range(count):
            seasons.append(base + (1 if extra > 0 else 0))
            extra -= 1
        plans.append(seasons)
    return plans


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed a production-scale media library.")
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--movies", type=int, default=1735)
    parser.add_argument("--series", type=int, default=850)
    parser.add_argument("--episodes", type=int, default=37_500)
    parser.add_argument("--progress", type=int, default=10_700)
    parser.add_argument("--anime-series", type=int, default=60)
    parser.add_argument("--anime-movies", type=int, default=20)
    parser.add_argument("--albums", type=int, default=120)
    parser.add_argument("--tracks", type=int, default=1400)
    args = parser.parse_args(argv)
    root = args.data_dir.resolve()
    if root.exists() and any(root.iterdir()):
        sys.exit(f"{root} is not empty; seed a fresh directory")
    (root / "data").mkdir(parents=True)
    os.environ["LUMINA_DATA_DIR"] = str(root / "data")  # before the app reads its settings
    sys.path[:0] = [str(HERE.parents[1] / "backend"), str(HERE)]

    import sample_art
    from sqlalchemy import insert

    from app import models as m
    from app.db import engine, init_db, session_scope
    from app.security import hash_password
    from app.services.library import EXTERNAL_LIBRARY_ORIGIN
    from app.services.user_settings import UserSettingsService
    from app.services.yt_dlp_service import YtDlpService

    rng = random.Random(7)
    samples = {kind: [f"art/{path.name}" for path in paths] for kind, paths in sample_art.generate(root / "media" / "art").items()}

    def new_id() -> str:
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))

    init_db()
    with session_scope() as db:
        YtDlpService(db).ensure_app_settings()
        password_hash = hash_password(PASSWORD)
        users = [m.User(id=new_id(), username=name, display_name=name.title(), password_hash=password_hash,
                        role="admin" if index == 0 else "viewer", onboarding_status="complete") for index, name in enumerate(USERS)]
        db.add_all(users)
        db.flush()
        UserSettingsService(db).ensure_for_all_users()
        media = root / "media"
        storage = m.StorageRoot(id=new_id(), label="Media", path=str(media), mode="external", identity=str(media.stat().st_dev), observation={"state": "available"})
        db.add(storage)
        root_id, admin_id, member_ids = storage.id, users[0].id, [user.id for user in users]

    titles: list[dict] = []
    items: list[dict] = []
    artifacts: list[dict] = []
    links: list[dict] = []

    def images(kind: str) -> dict:
        found = {}
        for image_type, share in COVERAGE.get(kind, {}).items():
            if rng.random() >= share:
                continue
            sample = "still" if kind == "episode" else SAMPLE_KIND[image_type]
            path = "art/poster-0.avif" if image_type == "Primary" and rng.random() < 0.01 else rng.choice(samples[sample])  # 1 %: unsupported
            found[image_type] = {"path": path, "tag": f"{rng.getrandbits(64):016x}"}  # unique per title: distinct source keys
        return found

    def naming() -> tuple[str, str | None]:
        words = f"{rng.choice(WORDS).title()} {rng.choice(WORDS)}"
        roll = rng.random()
        if roll < 0.01:
            return f"{rng.randint(1, 99)} {words}", None  # the rail's "#"
        if roll < 0.02:
            return f"Été {words}", None  # a non-ASCII initial, after "Z"
        if roll < 0.12:
            return f"The {words}", words
        return words, None

    def meta() -> dict:
        data = {"genres": rng.sample(GENRES, rng.randint(1, 3)), "overview": f"A {rng.choice(WORDS)} story about the {rng.choice(WORDS)}."}
        if rng.random() >= 0.05:
            data["community_rating"] = round(rng.uniform(3.0, 9.5), 1)
        return data

    def year() -> int | None:
        return None if rng.random() < 0.03 else rng.randint(1950, 2026)

    def resolution() -> tuple[int, int]:
        roll, total = rng.random(), 0.0
        for share, size in RESOLUTIONS:
            total += share
            if roll < total:
                return size
        return RESOLUTIONS[-1][1]

    def title(kind: str, name: str, **fields) -> str:
        title_id = new_id()
        added = BASE + timedelta(seconds=rng.randint(0, 60_000_000))
        titles.append({
            "id": title_id, "type": kind, "key": f"perf:{title_id}", "root_id": root_id, "name": name, "sort_name": None,
            "parent_id": None, "boxset_id": None, "index_number": None, "year": None, "provider_ids": {}, "field_sources": {},
            "images": images(kind), "metadata_json": {}, "created_at": added, "added_at": added,
            "updated_at": BASE, "category": m.CATEGORY_OF_TYPE.get(kind), **fields,
        })
        return title_id

    def version(title_id: str, kind: str, size: tuple[int, int] | None, label: str | None = None, *,
                relative: str | None = None, metadata: dict | None = None) -> str:
        item_id, artifact_id, n = new_id(), new_id(), len(items)
        items.append({
            "id": item_id, "user_id": admin_id, "visibility": "shared", "extractor": EXTERNAL_LIBRARY_ORIGIN, "remote_id": f"perf-{n}",
            "title": f"Item {n}" + (f" · {label}" if label else ""), "duration": None, "file_size": rng.randint(10**8, 4 * 10**9),
            "downloaded_at": BASE, "availability": "public", "metadata_json": {"lumina_import_kind": kind, **(metadata or {})},
            "metadata_summary": {}, "status": "available", "kind": kind, "title_id": title_id, "created_at": BASE, "updated_at": BASE,
        })
        dimensions = {"width": size[0], "height": size[1]} if size else {}
        artifacts.append({
            "id": artifact_id, "root_id": root_id, "relative_path": relative or f"{kind}/{n}.mkv", "ownership": "external",
            "owner_user_id": admin_id, "lifecycle": "available", "size": 1, "probe": {"duration": 1500.0 + n % 900, **dimensions},
            "created_at": BASE, "updated_at": BASE,
        })
        links.append({"library_item_id": item_id, "artifact_id": artifact_id, "created_at": BASE})
        return item_id

    movie_items: dict[str, str] = {}
    for _ in range(args.movies):
        name, sort_name = naming()
        movie = title("movie", name, sort_name=sort_name, year=year(), metadata_json=meta())
        if rng.random() < 0.15:  # a 4K and a 1080p version
            version(movie, "movie", (1920, 1080), "1080p")
            movie_items[movie] = version(movie, "movie", (3840, 2160), "4K")
        else:
            movie_items[movie] = version(movie, "movie", resolution())

    series_ids: list[str] = []
    watch_orders: list[list[str]] = []  # per series: regular episodes' items in watch order
    for plan in season_plans(rng, args.series, args.episodes):
        name, sort_name = naming()
        series = title("series", name, sort_name=sort_name, year=year(), metadata_json=meta())
        series_ids.append(series)
        size = resolution()
        numbers = list(range(1, len(plan) + 1))
        if len(plan) > 1 and len(plan) != BIG_SEASONS and rng.random() < 0.05:
            numbers[-1] = 0  # Specials
        order: list[str] = []
        for number, count in zip(numbers, plan):
            season = title("season", "Specials" if number == 0 else f"Season {number}", parent_id=series, index_number=number)
            for episode in range(1, count + 1):
                episode_id = title("episode", f"Episode {episode}", parent_id=season, index_number=episode,
                                   metadata_json={"overview": f"The {rng.choice(WORDS)} and the {rng.choice(WORDS)}."})
                item = version(episode_id, "episode", size)
                if number:
                    order.append(item)
        watch_orders.append(order)

    for number in range(args.anime_movies):  # Files under Movies/Anime/
        name, sort_name = naming()
        movie = title("movie", name, sort_name=sort_name, year=year(), metadata_json=meta(), category="anime")
        movie_items[movie] = version(movie, "movie", resolution(), relative=f"Movies/Anime/{number}/{number}.mkv")
    for number in range(args.anime_series):  # files under TV/Anime/, one season each
        name, sort_name = naming()
        series = title("series", name, sort_name=sort_name, year=year(), metadata_json=meta(), category="anime")
        series_ids.append(series)
        season = title("season", "Season 1", parent_id=series, index_number=1, category="anime")
        size, order = resolution(), []
        for episode in range(1, ANIME_EPISODES + 1):
            episode_id = title("episode", f"Episode {episode}", parent_id=season, index_number=episode, category="anime",
                               metadata_json={"overview": f"The {rng.choice(WORDS)} and the {rng.choice(WORDS)}."})
            order.append(version(episode_id, "episode", size, relative=f"TV/Anime/{number}/Season 01/{episode}.mkv"))
        watch_orders.append(order)

    artists: list[tuple[str, str]] = []  # (id, name): about 110 albums by 108 artists in production
    for number in range(args.albums - args.albums // 10):
        name = f"{rng.choice(WORDS).title()} {rng.choice(WORDS).title()} {number}"
        artists.append((title("artist", name, key=f"artist:{name.casefold()}"), name))
    albums: list[tuple[dict, str]] = []  # (title row, album folder)
    for number in range(args.albums):
        artist_id, artist_name = artists[number % len(artists)]
        name = f"{rng.choice(WORDS).title()} {rng.choice(WORDS)} {number}"
        title("album", name, key=f"album:{artist_name.casefold()}/{name.casefold()}", parent_id=artist_id, year=year(),
              metadata_json={"genres": rng.sample(GENRES, 1)})
        albums.append((titles[-1], f"Music/{artist_name}/{name}"))
    first_tracks: dict[str, str] = {}
    for number in range(args.tracks if albums else 0):
        album, folder = albums[number % len(albums)]
        track = number // len(albums) + 1
        relative = f"{folder}/{track:02d} Track {track}.flac"
        first_tracks.setdefault(album["id"], relative)
        version(album["id"], "track", None, relative=relative, metadata={"track_number": track, "disc_number": 1})
    posters = [root / "media" / path for path in samples["poster"]]
    for number, (album, folder) in enumerate(albums):  # Pinned interpretation 7: 6 folder covers, 3 embedded, 1 none per 10
        cover = rng.choice(posters)
        if number % 10 < 6:
            (root / "media" / folder).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cover, root / "media" / folder / "cover.jpg")
            album["images"] = {"Primary": {"path": f"{folder}/cover.jpg", "tag": f"{rng.getrandbits(64):016x}"}}
        elif number % 10 < 9 and album["id"] in first_tracks:
            track = root / "media" / first_tracks[album["id"]]
            embedded_flac(track, cover)
            info = track.stat()
            album["images"] = {"Primary": {"embedded": first_tracks[album["id"]], "tag": f"{info.st_size}:{info.st_mtime_ns}"}}

    progress: list[dict] = []
    per_member = args.progress // len(member_ids)
    movie_ids = list(movie_items)
    for member in member_ids:
        rows: list[tuple[str, datetime]] = []
        for movie in rng.sample(movie_ids, min(len(movie_ids), int(per_member * 0.4))):
            rows.append((movie_items[movie], BASE + timedelta(minutes=rng.randint(0, 500_000))))
        shows = [order for order in watch_orders if order]
        rng.shuffle(shows)
        for order in shows:
            if len(rows) >= per_member:
                break
            started = BASE + timedelta(minutes=rng.randint(0, 500_000))
            prefix = order[: min(len(order), rng.randint(3, 40), per_member - len(rows))]
            rows.extend((item, started + timedelta(hours=position)) for position, item in enumerate(prefix))
        for item, at in rows[:per_member]:
            completed = rng.random() < 0.8
            progress.append({
                "id": new_id(), "user_id": member, "item_id": item, "position_seconds": rng.randint(60, 1400), "duration_seconds": 1500,
                "completed": completed, "last_watched_at": at, "created_at": BASE, "updated_at": BASE,
            })
    favorites = [
        {"user_id": member, "target_id": target, "created_at": BASE}
        for member in member_ids for target in rng.sample(movie_ids + series_ids, min(FAVORITES, len(movie_ids) + len(series_ids)))
    ]

    with engine.begin() as connection:
        for model, rows in ((m.MediaTitle, titles), (m.LibraryItem, items), (m.MediaArtifact, artifacts),
                            (m.LibraryItemArtifact, links), (m.PlaybackProgress, progress), (m.MemberFavorite, favorites)):
            for start in range(0, len(rows), 5000):
                connection.execute(insert(model), rows[start:start + 5000])

    kinds = Counter((row["type"], row["category"]) for row in titles)
    print(json.dumps({
        "data_dir": str(root / "data"), "movies": kinds["movie", "movies"], "series": kinds["series", "shows"],
        "seasons": kinds["season", "shows"], "episodes": kinds["episode", "shows"], "anime_movies": kinds["movie", "anime"],
        "anime_series": kinds["series", "anime"], "anime_episodes": kinds["episode", "anime"], "artists": kinds["artist", None],
        "albums": kinds["album", None], "tracks": sum(item["kind"] == "track" for item in items), "progress": len(progress),
        "images": sum(len(row["images"]) for row in titles),
    }))


if __name__ == "__main__":
    main()
