"""EXPLAIN QUERY PLAN guards: the paged hot paths must stay on the v4 indexes.

Each test runs the real service query against a POPULATED, UNANALYZED
database — production shape, since the app never runs ANALYZE — captures the
exact SQL and parameters the service executed, and asserts SQLite's plan for
that statement seeks the composite indexes instead of scanning the table.

The accepted production plan for the library page is MULTI-INDEX OR over the
two composites plus a top-K temp b-tree sort; its O(visible-count) per-page
cost is a Stage 3 soak measurement item with a UNION-ALL keyset escape hatch
on record. Do not add ANALYZE here: with statistics SQLite abandons the
multi-index plan for a full scan, which is not what deployments run.
"""

import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AutomationDecision, DownloadJob, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MemberFavorite, PlaybackProgress,
    SourceAutomation, User,
)
from app.services.job_manager import JobManager
from app.services.library import LibraryService
from app.services.playback import PlaybackProgressService
from app.services.source_automation import SourceAutomationService
from app.services.titles import TitleFilters, TitleService


def _seed_production_shape(session, member: User, friend: User) -> None:  # noqa: ANN001
    base_time = datetime(2026, 6, 1)
    for index in range(300):
        owner = member if index % 3 else friend
        metadata = {"id": f"seed-{index:03d}", "ext": "mp4", "formats_count": 12, "duration": 600 + index}
        session.add(LibraryItem(
            id=f"seed-{index:03d}",
            user_id=owner.id,
            visibility="shared" if index % 4 else "private",
            extractor="YouTube",
            remote_id=f"remote-{index:03d}",
            title=f"Seeded item {index}",
            uploader="Creator",
            duration=600 + index,
            file_path=f"/library/{owner.id}/seed-{index:03d}.mp4",
            file_size=1_000_000 + index,
            downloaded_at=None if index % 25 == 0 else base_time + timedelta(hours=index),
            created_at=base_time + timedelta(minutes=index),
            metadata_json=metadata,
            metadata_summary=LibraryService.summarize_metadata(metadata),
            status="available" if index % 20 else "missing",
        ))
    for index in range(60):
        session.add(PlaybackProgress(
            id=f"progress-{index:02d}",
            user_id=member.id if index % 2 else friend.id,
            item_id=f"seed-{index:03d}",
            position_seconds=30 + index,
            duration_seconds=600,
            completed=index % 5 == 0,
            last_watched_at=base_time + timedelta(hours=index),
        ))
    statuses = ("completed", "failed", "queued", "cancelled", "staged")
    for index in range(120):
        session.add(DownloadJob(
            id=f"job-{index:03d}",
            user_id=member.id if index % 2 else friend.id,
            source_url=f"https://example.com/media/{index}",
            status=statuses[index % len(statuses)],
            format_selection={},
            output_profile={},
            created_at=base_time + timedelta(minutes=index),
        ))
    actions = ("queued", "skipped_duplicate", "manual", "failed")
    for index in range(200):
        session.add(AutomationDecision(
            id=f"decision-{index:03d}",
            run_id=f"run-{index // 25}",
            automation_id="automation-1" if index % 2 else "automation-2",
            user_id=member.id,
            action=actions[index % len(actions)],
            reason="seeded",
            created_at=base_time + timedelta(hours=index),
        ))
    session.commit()


@pytest.fixture()
def plan_env(tmp_path):  # noqa: ANN001
    engine = create_engine(f"sqlite:///{tmp_path / 'plans.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    session = session_factory()
    user = User(id="member-1", username="alice", display_name="Alice", role="viewer", is_active=True)
    friend = User(id="member-2", username="bob", display_name="Bob", role="viewer", is_active=True)
    session.add_all([user, friend])
    session.commit()
    _seed_production_shape(session, user, friend)
    yield engine, session, user
    session.close()
    engine.dispose()


def explain_plan(engine, session, run_query) -> str:  # noqa: ANN001
    captured: list[tuple[str, object]] = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany) -> None:  # noqa: ANN001
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        run_query()
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    connection = session.connection()
    details: list[str] = []
    for statement, parameters in captured:
        if not statement.lstrip().upper().startswith("SELECT") or statement.lstrip().startswith("SELECT member_access."):
            continue  # the session's one member access lookup (ADR 0019) is a primary-key read, not the query under test
        for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters):
            details.append(str(row[-1]))
    assert details, "expected the query under test to emit at least one SELECT"
    return "\n".join(details)


def assert_no_unindexed_scan(plan: str, table: str) -> None:
    offenders = [
        line for line in plan.splitlines()
        if f"SCAN {table}" in line and "USING" not in line
    ]
    assert not offenders, f"unindexed scan of {table}:\n{plan}"


def test_library_page_seeks_the_grid_and_owner_indexes(plan_env) -> None:  # noqa: ANN001
    engine, session, user = plan_env
    service = LibraryService(session)

    first_page = explain_plan(engine, session, lambda: service.list_items_page(user, limit=60))

    assert "ix_library_items_visibility_downloaded_created_id" in first_page
    assert "ix_library_items_user_downloaded" in first_page
    assert_no_unindexed_scan(first_page, "library_items")

    cursor_row = LibraryItem(id="cursor-row", downloaded_at=datetime(2026, 3, 1), created_at=datetime(2026, 1, 1))
    cursor = LibraryService._page_cursor_for(cursor_row)
    later_page = explain_plan(engine, session, lambda: service.list_items_page(user, cursor=cursor, limit=60))

    assert "ix_library_items_visibility_downloaded_created_id" in later_page
    assert_no_unindexed_scan(later_page, "library_items")


def test_continue_watching_seeks_the_progress_shelf_index(plan_env) -> None:  # noqa: ANN001
    engine, session, user = plan_env
    service = PlaybackProgressService(session)

    plan = explain_plan(engine, session, lambda: service.list_continue_watching(user))

    assert "ix_playback_progress_user_completed_watched" in plan
    assert_no_unindexed_scan(plan, "playback_progress")
    assert_no_unindexed_scan(plan, "library_items")


def test_job_page_seeks_the_member_status_index(plan_env) -> None:  # noqa: ANN001
    engine, session, user = plan_env

    plan = explain_plan(engine, session, lambda: JobManager.list_for_user(session, user))

    assert "ix_download_jobs_user_status_created" in plan
    assert_no_unindexed_scan(plan, "download_jobs")


def test_daily_limit_count_is_covered_by_the_decision_index(plan_env) -> None:  # noqa: ANN001
    engine, session, _user = plan_env
    automation = SourceAutomation(
        id="automation-1",
        user_id="member-1",
        label="Watch",
        source_url="https://example.com/playlist",
        cron_expression="0 * * * *",
        max_items_per_day=5,
    )
    service = SourceAutomationService(session, None, None)

    plan = explain_plan(
        engine,
        session,
        lambda: service._daily_remaining(automation, datetime(2026, 7, 18)),  # noqa: SLF001
    )

    assert "ix_automation_decisions_automation_action_created" in plan
    assert_no_unindexed_scan(plan, "automation_decisions")


def test_rows_reached_by_id_never_start_from_every_visible_item(plan_env) -> None:  # noqa: ANN001
    """An unanalyzed planner drove these from a MULTI-INDEX OR over all visible
    items (1.5s for one collections list at 20k items); they must seek by id instead."""
    from app.main import list_household_collections
    from app.models import HouseholdCollection, HouseholdCollectionMembership, WatchQueueEntry
    from app.services.member_recommendations import MemberRecommendationPolicy
    from app.services.popular_discovery import PopularItem
    from app.services.watch_queue import WatchQueueService

    engine, session, user = plan_env
    session.add(HouseholdCollection(id="c1", owner_user_id=user.id, name="Mix", name_key="mix"))
    for index in range(10):
        session.add(HouseholdCollectionMembership(
            id=f"m{index}", collection_id="c1", position=index, library_item_id=f"seed-{index:03d}", added_by_user_id=user.id,
        ))
        session.add(WatchQueueEntry(id=f"q{index}", user_id=user.id, position=index, library_item_id=f"seed-{index:03d}"))
    session.commit()
    candidate = PopularItem(
        id="remote-001", title="t", uploader="u", duration=None, thumbnail=None, artwork_url=None,
        webpage_url="https://example.com/watch", view_count=None, availability=None, published_at=None,
        source="youtube", source_label="YouTube", capabilities=None, category_keys=(),
    )

    for run in (
        lambda: list_household_collections(current_user=user, db=session),
        lambda: WatchQueueService(session).read(user),
        lambda: MemberRecommendationPolicy(session)._visible_source_keys(user, [("k", candidate)]),  # noqa: SLF001
    ):
        plan = explain_plan(engine, session, run)
        assert "ix_library_items_visibility_downloaded_created_id" not in plan, plan
        assert "ix_library_items_user_downloaded" not in plan, plan
        assert_no_unindexed_scan(plan, "library_items")


def test_title_progress_subqueries_seek_by_title_not_every_progress_row(plan_env) -> None:  # noqa: ANN001
    """S131: the shared-episode progress lookup was driven from ix_playback_progress_user_watched,
    walking the member's whole history per episode instead of seeking by title_id (24s Shows page)."""
    engine, session, user = plan_env
    session.add_all([
        MediaTitle(id="show", type="series", key="k:show", name="Show"),
        MediaTitle(id="s1", type="season", key="k:s1", name="Season 1", parent_id="show", index_number=1),
    ])
    for n in range(20):
        session.add(MediaTitle(id=f"ep{n}", type="episode", key=f"k:ep{n}", name=f"Ep {n}", parent_id="s1", index_number=n + 1))
        session.add(LibraryItem(id=f"epi{n}", user_id=user.id, visibility="shared", extractor="local", remote_id=f"e{n}",
                                title=f"Ep {n}", title_id=f"ep{n}", status="available"))
        session.add(PlaybackProgress(id=f"epp{n}", user_id=user.id, item_id=f"epi{n}", position_seconds=10, duration_seconds=600, completed=True))
    session.commit()
    service = TitleService(session)
    show = session.get(MediaTitle, "show")
    for run in (lambda: service.load(user, [show]), lambda: service.series_progress(user, ["show"])):
        plan = explain_plan(engine, session, run)
        assert "ix_playback_progress_user_watched" not in plan, plan
        assert "ix_library_items_title_id" in plan, plan


def _seed_gallery(session, user: User) -> None:  # noqa: ANN001
    """40 movies and 4 two-season shows with probed versions, genres and favourites, unanalyzed like production.

    Movies gm00..gm39 (every 3rd has progress, every 5th is a favourite); series gs0..gs3 with seasons gsN-s1/s2 and
    episodes gsN-sMe1..e5, one version ``<id>-v`` each. Episodes carry no progress: tests add their own.
    """
    base = datetime(2026, 6, 1)

    def version(title_id: str, n: int, *, kind: str) -> None:
        big = n % 4 == 0
        session.add_all([
            LibraryItem(id=f"{title_id}-v", user_id=user.id, visibility="shared", extractor="local", remote_id=title_id,
                        title=title_id, title_id=title_id, status="available", kind=kind),
            MediaArtifact(id=f"{title_id}-a", root_id="root", relative_path=f"{title_id}.mkv", ownership="external",
                          probe={"width": 3840 if big else 1920, "height": 2160 if big else 1080}),
            LibraryItemArtifact(library_item_id=f"{title_id}-v", artifact_id=f"{title_id}-a"),
        ])

    for n in range(40):
        movie = f"gm{n:02d}"
        session.add(MediaTitle(
            id=movie, type="movie", key=f"k:{movie}", name=f"{chr(65 + n % 26)} movie {n}", year=1990 + n % 30,
            created_at=base + timedelta(minutes=n), metadata_json={"genres": ["Drama" if n % 2 else "Action"], "community_rating": n % 9},
        ))
        version(movie, n, kind="movie")
        if n % 3 == 0:
            session.add(PlaybackProgress(id=f"{movie}-p", user_id=user.id, item_id=f"{movie}-v", position_seconds=60,
                                         duration_seconds=1200, completed=n % 2 == 0, last_watched_at=base + timedelta(hours=n)))
        if n % 5 == 0:
            session.add(MemberFavorite(user_id=user.id, target_id=movie))
    for s in range(4):
        show = f"gs{s}"
        session.add(MediaTitle(id=show, type="series", key=f"k:{show}", name=f"{chr(70 + s)} show",
                               created_at=base + timedelta(minutes=s), metadata_json={"genres": ["Drama"]}))
        for season_no in (1, 2):
            season = f"{show}-s{season_no}"
            session.add(MediaTitle(id=season, type="season", key=f"k:{season}", name=f"Season {season_no}", parent_id=show, index_number=season_no))
            for e in range(1, 6):
                episode = f"{season}e{e}"
                session.add(MediaTitle(id=episode, type="episode", key=f"k:{episode}", name=f"Ep {e}", parent_id=season, index_number=e))
                version(episode, s + season_no + e, kind="episode")
    session.commit()


WALL_INDEXES = ("ix_media_titles_type_sort", "ix_media_titles_type_created", "ix_media_titles_type_added")


def test_episode_lookups_seek_parents_not_the_wall_type_indexes(plan_env) -> None:  # noqa: ANN001
    """Schema step 6's (type, …) indexes lured the unanalyzed planner into walking every episode of the library
    through type = 'episode' instead of seeking parent_id from the series (the 1.3.1 Shows-page failure class).
    The indexes are partial (movie, series) so no episode or season lookup can ever match them."""
    from app.services.jellyfin import Entity, ItemsQuery, JellyfinMapper
    from app.services.jellyfin_history import Matcher
    from app.services.recaps import _prior_episodes

    engine, session, user = plan_env
    _seed_gallery(session, user)
    session.add(MediaTitle(id="box", type="boxset", key="k:box", name="Box"))
    session.get(MediaTitle, "gm01").boxset_id = "box"
    session.commit()
    service = TitleService(session)
    mapper, matcher = JellyfinMapper(session, user), Matcher(session, user)  # the matcher's own indexes scan by design
    show, season = session.get(MediaTitle, "gs0"), session.get(MediaTitle, "gs0-s1")
    parent_seeks = (
        lambda: service.series_progress(user, ["gs0", "gs1"]),
        lambda: service.load(user, [show, season]),
        lambda: service.episodes(user, show, 1),
        lambda: service.episodes(user, show),
        lambda: _prior_episodes(session, user, "gs0", 2, 3),
        lambda: session.scalars(mapper.title_filter(Entity("s", title=season), ItemsQuery())).all(),
        lambda: session.scalars(mapper.title_filter(Entity("s", title=show), ItemsQuery(recursive=True))).all(),
        lambda: matcher._episodes({"gs0", "gs1"}),  # noqa: SLF001
    )
    for run in parent_seeks:
        plan = explain_plan(engine, session, run)
        assert not any(index in plan for index in WALL_INDEXES), plan
        assert "ix_media_titles_parent_id" in plan, plan
        assert_no_unindexed_scan(plan, "media_titles")
    boxset_counts = explain_plan(engine, session, lambda: service.load(user, [session.get(MediaTitle, "box")]))
    assert not any(index in boxset_counts for index in WALL_INDEXES), boxset_counts


def test_wall_pages_use_the_partial_title_indexes(plan_env) -> None:  # noqa: ANN001
    """A partial index only serves a query that repeats its WHERE as literals: titles.WALL_TYPES."""
    engine, session, user = plan_env
    _seed_gallery(session, user)
    service = TitleService(session)
    for types, name_index, created_index in (
        (("movie",), ("ix_media_titles_type_sort",), ("ix_media_titles_type_added",)),
        (("series",), ("ix_media_titles_type_sort",), ("ix_media_titles_type_added",)),
        (("movie", "series"), WALL_INDEXES, WALL_INDEXES),  # two types sort in a temp b-tree whichever index drives
    ):
        for sort, wanted in (("name", name_index), ("created", created_index)):
            plan = explain_plan(engine, session, lambda: service.page(user, types=types, sort=sort, limit=60))
            assert any(index in plan.splitlines()[0] for index in wanted), plan
            assert_no_unindexed_scan(plan, "media_titles")


def test_title_artwork_batch_seeks_its_primary_key(plan_env) -> None:  # noqa: ANN001
    engine, session, user = plan_env
    _seed_gallery(session, user)
    movies = [session.get(MediaTitle, f"gm{n:02d}") for n in range(20)]
    plan = explain_plan(engine, session, lambda: TitleService(session).load(user, movies))
    assert "sqlite_autoindex_title_artwork_1" in plan, plan
    assert_no_unindexed_scan(plan, "title_artwork")


def test_series_best_height_walks_its_own_episodes(plan_env) -> None:  # noqa: ANN001
    engine, session, user = plan_env
    _seed_gallery(session, user)
    service = TitleService(session)
    for title_id in ("gs0", "gs0-s1", "gm04"):
        plan = explain_plan(engine, session, lambda: service.best_height(user, session.get(MediaTitle, title_id)))
        assert "ix_library_items_title_id" in plan, plan
        assert "ix_media_titles_type_sort" not in plan, plan
        assert_no_unindexed_scan(plan, "library_items")
        assert_no_unindexed_scan(plan, "media_titles")


WALL_FILTERS = (
    TitleFilters(unwatched=True), TitleFilters(in_progress=True), TitleFilters(favorites=True),
    TitleFilters(genres=("drama",)), TitleFilters(resolutions=("4k",)),
)
SEED_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "perf" / "seed_titles.py"


@pytest.fixture(scope="module")
def production_seed(tmp_path_factory):  # noqa: ANN001, ANN201
    """scripts/perf/seed_titles.py at its production defaults (1,735 movies, 850 shows, 37,500 episodes), unanalyzed."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("the title seed draws its sample art with ffmpeg")
    target = tmp_path_factory.mktemp("production") / "seed"
    subprocess.run([sys.executable, str(SEED_SCRIPT), str(target)], capture_output=True, text=True, timeout=600, check=True)
    engine = create_engine(f"sqlite:///{target / 'data' / 'app.db'}", future=True)
    yield engine
    engine.dispose()


@pytest.fixture(params=["gallery", "production"])
def wall_env(request, plan_env):  # noqa: ANN001, ANN201
    """The wall guards run on the small hand-built gallery and on the production-scale seed."""
    if request.param == "gallery":
        engine, session, user = plan_env
        _seed_gallery(session, user)
        yield engine, session, user
        return
    engine = request.getfixturevalue("production_seed")
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)()
    yield engine, session, session.scalar(select(User).where(User.username == "alex"))
    session.close()


def test_wall_pages_seek_the_title_indexes(wall_env) -> None:  # noqa: ANN001
    engine, session, user = wall_env
    service = TitleService(session)
    for kind in ("movie", "series"):
        for sort, index in (("name", "ix_media_titles_type_sort"), ("created", "ix_media_titles_type_added")):
            first = explain_plan(engine, session, lambda: service.page(user, types=(kind,), sort=sort, limit=60))
            _titles, _start, seek = service.page(user, types=(kind,), sort=sort, limit=2)
            later = explain_plan(engine, session, lambda: service.page(user, types=(kind,), sort=sort, limit=60, after=seek))
            for plan in (first, later):
                assert index in plan, plan
                assert_no_unindexed_scan(plan, "media_titles")
            assert f"{index} (type=? AND " in later, later  # a range seek, never a walk from the list's start


def test_letter_offsets_and_the_rail_use_the_name_index(wall_env) -> None:  # noqa: ANN001
    engine, session, user = wall_env
    service = TitleService(session)
    for kind in ("movie", "series"):
        for run in (
            lambda: service.letters(user, types=(kind,), filters=TitleFilters()),
            lambda: service.page(user, types=(kind,), sort="name", limit=60, letter="M"),
        ):
            plan = explain_plan(engine, session, run)
            assert "ix_media_titles_type_sort" in plan, plan
            assert "ix_media_titles_type_added" not in plan, plan
            assert_no_unindexed_scan(plan, "media_titles")


def test_wall_totals_use_a_wall_index(wall_env) -> None:  # noqa: ANN001
    engine, session, user = wall_env
    service = TitleService(session)
    for kind in ("movie", "series"):
        plan = explain_plan(engine, session, lambda: service.count(user, types=(kind,), filters=TitleFilters()))
        assert any(index in plan for index in WALL_INDEXES), plan
        assert_no_unindexed_scan(plan, "media_titles")


def test_wall_filters_seek_by_title_never_the_progress_history(wall_env) -> None:  # noqa: ANN001
    engine, session, user = wall_env
    service = TitleService(session)
    for kind in ("movie", "series"):
        for filters in WALL_FILTERS:
            plan = explain_plan(engine, session, lambda: (
                service.page(user, types=(kind,), sort="created", limit=60, filters=filters),
                service.count(user, types=(kind,), filters=filters),
            ))
            assert "ix_library_items_title_id" in plan, (filters, plan)
            assert "ix_playback_progress_user_watched" not in plan, (filters, plan)
            if kind == "series" and (filters.unwatched or filters.in_progress or filters.resolutions):
                # episodes are reached by parent id; the partial wall indexes only ever drive the outer series rows
                assert "ix_media_titles_parent_id" in plan, (filters, plan)
                assert not re.search(r"media_titles_\d+ USING .*ix_media_titles_type_", plan), (filters, plan)
            for table in ("library_items", "playback_progress", "media_titles"):
                assert_no_unindexed_scan(plan, table)


def test_ai_extras_seek_summaries_by_item(plan_env) -> None:  # noqa: ANN001
    from app.models import Summary, TranscriptCue
    from app.services import ai_extras

    engine, session, user = plan_env
    _seed_gallery(session, user)
    for n in range(1, 6):
        item = f"gs0-s1e{n}-v"
        session.add_all([
            PlaybackProgress(id=f"ai-{n}", user_id=user.id, item_id=item, position_seconds=60, duration_seconds=1200,
                             completed=True, last_watched_at=datetime(2026, 7, 1) + timedelta(hours=n)),
            Summary(id=f"sum-{n}", library_item_id=item, transcript_id=f"t-{n}", transcript_revision=1, model_id="m", state="succeeded",
                    overview="Text.", key_points=[{"text": "Point", "cue_ordinals": [1]}], completed_at=datetime(2026, 7, 2)),
            TranscriptCue(transcript_id=f"t-{n}", ordinal=1, start_ms=1000, end_ms=2000, text="A line long enough to quote."),
        ])
    session.commit()
    series = session.get(MediaTitle, "gs0")
    for run in (lambda: ai_extras.episode_summaries(session, user, series, 1), lambda: ai_extras.key_scenes(session, user, series)):
        plan = explain_plan(engine, session, run)
        assert "ix_summaries_library_item_id" in plan, plan
        assert "ix_playback_progress_user_watched" not in plan and "ix_media_titles_type_sort" not in plan, plan
        for table in ("summaries", "playback_progress", "library_items", "transcript_cues"):
            assert_no_unindexed_scan(plan, table)


# ---- Library gallery: category and music walls, sections, category SQL, list progress --------------------
CATEGORY_INDEXES = ("ix_media_titles_category_sort", "ix_media_titles_category_added")
MUSIC_INDEXES = ("ix_media_titles_music_sort", "ix_media_titles_music_added")


def _seed_categories(session, user: User) -> None:  # noqa: ANN001
    """On top of _seed_gallery: every 4th movie and show gs3 (seasons and episodes too) are anime, and 5 artists
    gr0..gr4 with 10 albums ga0..ga9 of 3 shared tracks each."""
    from sqlalchemy import update

    anime = [f"gm{n:02d}" for n in range(0, 40, 4)] + ["gs3", "gs3-s1", "gs3-s2"] + [f"gs3-s{s}e{e}" for s in (1, 2) for e in range(1, 6)]
    session.execute(update(MediaTitle).where(MediaTitle.id.in_(anime)).values(category="anime"))
    for n in range(5):
        session.add(MediaTitle(id=f"gr{n}", type="artist", key=f"artist:gr{n}", name=f"{chr(65 + n)} artist"))
    for n in range(10):
        album = f"ga{n}"
        session.add(MediaTitle(id=album, type="album", key=f"album:gr{n % 5}/{album}", name=f"{chr(75 + n)} album",
                               parent_id=f"gr{n % 5}", created_at=datetime(2026, 6, 1) + timedelta(minutes=n)))
        for t in range(3):
            session.add(LibraryItem(id=f"{album}-t{t}", user_id=user.id, visibility="shared", extractor="local",
                                    remote_id=f"{album}-t{t}", title=f"Track {t}", title_id=album, status="available", kind="track"))
    session.commit()


@pytest.fixture(params=["gallery", "production"])
def category_env(request, plan_env):  # noqa: ANN001, ANN201
    """The category and music guards run on the hand-built gallery and on the production-scale seed (anime and albums)."""
    if request.param == "gallery":
        engine, session, user = plan_env
        _seed_gallery(session, user)
        _seed_categories(session, user)
        yield engine, session, user
        return
    engine = request.getfixturevalue("production_seed")
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)()
    yield engine, session, session.scalar(select(User).where(User.username == "alex"))
    session.close()


def test_category_walls_seek_the_category_indexes(category_env) -> None:  # noqa: ANN001
    """WALL_TYPES + category = ?, never type, so the category indexes drive in order and a later page is a
    range seek, never a walk from the list's start or a temp b-tree sort."""
    from app.services.titles import CATEGORY_TYPES

    engine, session, user = category_env
    service = TitleService(session)
    for category, types in CATEGORY_TYPES.items():
        for sort, index in (("name", "ix_media_titles_category_sort"), ("created", "ix_media_titles_category_added")):
            first = explain_plan(engine, session, lambda: service.page(user, types=types, sort=sort, limit=60, category=category))
            _titles, _start, seek = service.page(user, types=types, sort=sort, limit=2, category=category)
            assert seek is not None, (category, sort)  # both fixtures hold at least three titles per category
            later = explain_plan(engine, session, lambda: service.page(user, types=types, sort=sort, limit=60, after=seek, category=category))
            for plan in (first, later):
                assert plan.splitlines()[0].startswith(f"SEARCH media_titles USING INDEX {index} (category=?"), plan
                assert "USE TEMP B-TREE FOR ORDER BY" not in plan, plan
                assert "ix_media_titles_type_" not in plan, plan
                assert_no_unindexed_scan(plan, "media_titles")
            assert f"{index} (category=? AND " in later, later


def test_category_letters_and_totals_use_the_category_indexes(category_env) -> None:  # noqa: ANN001
    from app.services.titles import CATEGORY_TYPES

    engine, session, user = category_env
    service = TitleService(session)
    for category, types in CATEGORY_TYPES.items():
        for run in (
            lambda: service.letters(user, types=types, filters=TitleFilters(), category=category),
            lambda: service.page(user, types=types, sort="name", limit=60, letter="M", category=category),
        ):
            plan = explain_plan(engine, session, run)
            assert "ix_media_titles_category_sort" in plan, plan
            assert "ix_media_titles_type_" not in plan, plan
            assert_no_unindexed_scan(plan, "media_titles")
        total = explain_plan(engine, session, lambda: service.count(user, types=types, filters=TitleFilters(), category=category))
        assert any(index in total for index in CATEGORY_INDEXES), total
        assert "ix_media_titles_type_" not in total, total


def test_category_filters_seek_by_title_never_the_progress_history(category_env) -> None:  # noqa: ANN001
    from app.services.titles import CATEGORY_TYPES

    engine, session, user = category_env
    service = TitleService(session)
    for category, types in CATEGORY_TYPES.items():
        for filters in WALL_FILTERS:
            plan = explain_plan(engine, session, lambda: (
                service.page(user, types=types, sort="created", limit=60, filters=filters, category=category),
                service.count(user, types=types, filters=filters, category=category),
            ))
            assert any(index in plan.splitlines()[0] for index in CATEGORY_INDEXES), (category, filters, plan)
            assert "ix_library_items_title_id" in plan, (category, filters, plan)
            assert "ix_playback_progress_user_watched" not in plan, (category, filters, plan)
            for table in ("library_items", "playback_progress", "media_titles"):
                assert_no_unindexed_scan(plan, table)


def test_music_walls_seek_the_music_indexes(category_env) -> None:  # noqa: ANN001
    engine, session, user = category_env
    service = TitleService(session)
    for kind in ("album", "artist"):
        for sort, index in (("name", "ix_media_titles_music_sort"), ("created", "ix_media_titles_music_added")):
            first = explain_plan(engine, session, lambda: service.page(user, types=(kind,), sort=sort, limit=60))
            _titles, _start, seek = service.page(user, types=(kind,), sort=sort, limit=2)
            assert seek is not None, (kind, sort)
            later = explain_plan(engine, session, lambda: service.page(user, types=(kind,), sort=sort, limit=60, after=seek))
            for plan in (first, later):
                assert plan.splitlines()[0].startswith(f"SEARCH media_titles USING INDEX {index} (type=?"), plan
                assert "USE TEMP B-TREE FOR ORDER BY" not in plan, plan
                assert_no_unindexed_scan(plan, "media_titles")
            assert f"{index} (type=? AND " in later, later
        letters = explain_plan(engine, session, lambda: service.letters(user, types=(kind,), filters=TitleFilters()))
        assert "ix_media_titles_music_sort" in letters and "USE TEMP B-TREE FOR ORDER BY" not in letters, letters


def test_sections_count_from_indexes(category_env) -> None:  # noqa: ANN001
    from app.routers.library_sections import count_sections

    engine, session, user = category_env
    plan = explain_plan(engine, session, lambda: count_sections(session, user))
    assert any(index in plan for index in (*WALL_INDEXES, *CATEGORY_INDEXES)), plan
    assert any(index in plan for index in MUSIC_INDEXES), plan
    assert "ix_library_items_kind_downloaded_created_id" in plan and "ix_library_items_status" in plan, plan
    for table in ("media_titles", "library_items"):
        assert_no_unindexed_scan(plan, table)


def test_category_sql_seeks_items_by_title(plan_env) -> None:  # noqa: ANN001
    """The backfill's EXISTS seek each title's items; a scan batch's refresh never walks the title table."""
    from app.db import CATEGORY_BACKFILL_SQL
    from app.services.categories import refresh_category

    engine, session, user = plan_env
    _seed_gallery(session, user)
    _seed_categories(session, user)
    connection = session.connection()

    def plan_of(statement: str, parameters=()) -> str:  # noqa: ANN001
        return "\n".join(str(row[-1]) for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters))

    movies, series = plan_of(CATEGORY_BACKFILL_SQL[0]), plan_of(CATEGORY_BACKFILL_SQL[1])
    assert "ix_library_items_title_id" in movies, movies
    assert "ix_library_items_title_id" in series and "ix_media_titles_parent_id" in series, series
    updates: list[tuple[str, object]] = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany) -> None:  # noqa: ANN001
        if statement.lstrip().upper().startswith("UPDATE"):
            updates.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        refresh_category(session, ["gm00", "gs3-s1e1"])
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(updates) == 4, updates
    for statement, parameters in updates:
        plan = plan_of(statement, parameters)
        assert not any(line.startswith("SCAN") for line in plan.splitlines()), plan
    session.rollback()


def test_library_list_progress_seeks_the_member_item_pair(plan_env) -> None:  # noqa: ANN001
    from app.main import list_library

    engine, session, user = plan_env
    plan = explain_plan(engine, session, lambda: list_library(
        search=None, cursor=None, limit=60, kind=None, source=None, group=None, status=None, sort="recent", current_user=user, db=session,
    ))
    progress = [line for line in plan.splitlines() if "playback_progress" in line]
    assert progress and all("(user_id=? AND item_id=?)" in line for line in progress), plan


# ---- Library gallery: music -----------------------------------------------------------------


def _seed_music(session, user: User) -> None:  # noqa: ANN001
    """20 artists (arNN) with two albums each (arNN-al0/al1) of five tracks (…-t1..t5), unanalyzed like production."""
    for a in range(20):
        artist = f"ar{a:02d}"
        session.add(MediaTitle(id=artist, type="artist", key=f"artist:{artist}", name=f"Artist {a}"))
        for b in range(2):
            album = f"{artist}-al{b}"
            session.add(MediaTitle(id=album, type="album", key=f"album:{album}", name=f"Album {a}.{b}", parent_id=artist, year=2000 + b))
            for n in range(1, 6):
                item = f"{album}-t{n}"
                session.add_all([
                    LibraryItem(id=item, user_id=user.id, visibility="shared", extractor="local", remote_id=item, title=f"Track {n}",
                                title_id=album, status="available", kind="track", metadata_json={"track_number": n, "disc_number": 1}),
                    MediaArtifact(id=f"{item}-a", root_id="root", relative_path=f"{item}.flac", ownership="external", probe={"duration": 180.0}),
                    LibraryItemArtifact(library_item_id=item, artifact_id=f"{item}-a"),
                ])
    session.commit()


def test_album_tracks_and_music_counts_seek_by_title(plan_env) -> None:  # noqa: ANN001
    engine, session, user = plan_env
    _seed_music(session, user)
    service = TitleService(session)
    album = session.get(MediaTitle, "ar05-al1")
    plan = explain_plan(engine, session, lambda: service.tracks(user, album, "Artist 5"))
    assert "ix_library_items_title_id" in plan, plan
    assert_no_unindexed_scan(plan, "library_items")
    page = [session.get(MediaTitle, title_id) for title_id in ("ar03", "ar04-al0", "ar04-al1")]
    plan = explain_plan(engine, session, lambda: service.load(user, page))
    assert "ix_library_items_title_id" in plan and "ix_media_titles_parent_id" in plan, plan
    assert_no_unindexed_scan(plan, "library_items")
    assert_no_unindexed_scan(plan, "media_titles")


# ---- Query-plan guards. SQLite names the indexes behind the two unique
# constraints sqlite_autoindex_*, so the guards assert a SEARCH on the table and no unindexed scan.
from app.models import RemotePlaybackProgress  # noqa: E402
from app.schemas import YouTubeSearchResult  # noqa: E402
from app.services.remote_annotation import annotate_remote_entries  # noqa: E402
from app.services.remote_playback import RemotePlaybackProgressService  # noqa: E402


def test_annotation_seeks_saved_items_and_both_progress_tables(plan_env) -> None:  # noqa: ANN001
    engine, session, user = plan_env
    for index in range(40):
        canonical = f"youtube:remote-{index:03d}"
        session.add(RemotePlaybackProgress(
            id=f"rp-{index}", user_id=user.id, source_identity=canonical,
            source_identity_key=RemotePlaybackProgressService.source_identity_key(canonical), source_url="https://example.test",
            position_seconds=10, duration_seconds=100,
        ))
    session.commit()
    entries = [YouTubeSearchResult(id=f"remote-{index:03d}", source="youtube") for index in range(120)]

    plan = explain_plan(engine, session, lambda: annotate_remote_entries(session, user, entries))

    assert "ix_library_items_remote_id" in plan or "ix_library_items_extractor_remote_id" in plan
    assert "SEARCH playback_progress" in plan and "SEARCH remote_playback_progress" in plan
    # The member's checkpoints by identity key: the (user_id, source_identity_key) unique index, never the low-selectivity
    # cleared index, which reads every member's uncleared checkpoints.
    assert "SEARCH remote_playback_progress USING INDEX sqlite_autoindex_remote_playback_progress" in plan, plan
    for table in ("library_items", "playback_progress", "remote_playback_progress"):
        assert_no_unindexed_scan(plan, table)


def test_library_channels_joins_progress_by_member_and_item(plan_env) -> None:  # noqa: ANN001
    engine, session, user = plan_env
    service = LibraryService(session)

    plan = explain_plan(engine, session, lambda: service.list_channels(user, source="youtube", sort="recent", artwork_url=lambda url: url))

    assert "SEARCH playback_progress" in plan
    assert_no_unindexed_scan(plan, "playback_progress")


def test_a_restricted_members_walls_and_grid_keep_their_indexes(plan_env) -> None:  # noqa: ANN001
    """Member access (ADR 0019) adds primary-key checks, never a different driver or a scan."""
    from app.models import MemberAccess

    engine, session, user = plan_env
    _seed_gallery(session, user)
    session.add(MemberAccess(user_id=user.id, sections=["movies", "shows"], movie_rating_max="PG", tv_rating_max="TV-PG", unrated="hide"))
    session.commit()
    service = TitleService(session)
    for kind in ("movie", "series"):
        plan = explain_plan(engine, session, lambda: service.page(user, types=(kind,), sort="name", limit=60))
        assert "ix_media_titles_type_sort" in plan.splitlines()[0], plan
        assert_no_unindexed_scan(plan, "media_titles")
    grid = explain_plan(engine, session, lambda: LibraryService(session).list_items_page(user, limit=60))
    assert "ix_library_items_visibility_downloaded_created_id" in grid and "sqlite_autoindex_media_titles_1" in grid, grid
    assert_no_unindexed_scan(grid, "library_items")
    assert_no_unindexed_scan(grid, "media_titles")
