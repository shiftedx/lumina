"""The offline recommendation replay runs end to end on the synthetic household, deterministically and without leakage."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.models import RecoEvent, RecoPool, RemoteMedia, RemotePlaybackProgress, SourceAutomation
from app.services.reco import SOURCE_FOLLOW
from discovery_support import add_movie
from sqlalchemy import func, select
from support import make_user, memory_session_factory

PERF = Path(__file__).resolve().parents[2] / "scripts" / "perf"


def load(name: str):  # noqa: ANN201
    spec = importlib.util.spec_from_file_location(name, PERF / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def test_the_replay_scores_every_member_of_the_synthetic_household_deterministically() -> None:
    replay = load("reco_replay")
    factory = memory_session_factory()
    with factory() as session:
        load("seed_reco").seed(session, settings.data_dir)

    report = replay.replay(factory)

    assert report == replay.replay(factory)
    assert report["members_evaluated"] == 4
    assert report["instances"]["home_picked"] == 40 and report["instances"]["home_recommended"] == 40
    assert report["instances"]["up_next"] > 0 and report["instances"]["home_because"] > 0
    assert report["hidden_rows_summed_over_instances"]["source_automations"] == 80 * 4  # every instance hides the 4 late follows
    for metric, rows in report["hit_at_10"].items():
        assert set(rows) == {"legacy", "newest_first", "household_top_channel", "last_watch_channel"}
        assert all(0.0 <= value <= 1.0 for value in rows.values()), metric
    # Taste structure is recoverable: the held-out watches' channels show up on Home.
    assert report["hit_at_10"]["remote_channel_hit@10"]["legacy"] > 0.5
    assert 0 < report["coverage_at_20"]["home_picked"] <= 1
    assert report["home_picked_top12"]["freshness_median_days"] is not None
    # Spread is scored on paired instances only; legacy's empty early lists still show in the unpaired figures.
    paired, unpaired = report["home_picked_top12"], report["home_picked_top12"]["unpaired"]
    assert 0 < paired["instances"] < unpaired["instances"] == 40
    assert paired["distinct_channels"] > unpaired["distinct_channels"]
    assert paired["max_one_channel_over_cap"] <= 0
    text = json.dumps(report)
    for private in ("ana", "Kitchen Lab", "kitchen lab", "kitchen-lab", "film"):
        assert private not in text


def _remote_watch(member: str, video: str, channel: str, at: datetime) -> RemotePlaybackProgress:
    return RemotePlaybackProgress(
        id=f"{member}-{video}", user_id=member, source_identity=f"youtube:{video}",
        source_identity_key=hashlib.sha256(video.encode()).hexdigest(), extractor="youtube", remote_id=video,
        source_url=f"https://www.youtube.com/watch?v={video}", title=video, uploader=channel, position_seconds=600.0,
        duration_seconds=600.0, completed=True, last_watched_at=at, created_at=at,
    )


def test_a_held_out_watch_and_later_signals_never_shape_the_list_they_are_scored_against() -> None:
    """The held-out video is the member's only evidence for its channel, followed later; neither may lift it."""
    replay = load("reco_replay")
    base = datetime(2026, 9, 1, 12)
    snapshot_items = [("zeta-1", "Zeta"), *((f"alpha-{n}", "Alpha") for n in range(3)), *((f"other-{n}", f"Other {n}") for n in range(4))]
    store = {"version": 1, "categories": {"music": {
        "items": [{"id": video, "uploader": channel, "title": video, "view_count": 1_000 * (n + 1),
                   "published_at": (base - timedelta(days=30)).replace(tzinfo=UTC).timestamp(), "source": "youtube"}
                  for n, (video, channel) in enumerate(snapshot_items)],
        "last_success_at": base.replace(tzinfo=UTC).timestamp(),
    }}, "refreshed_at": base.replace(tzinfo=UTC).timestamp()}
    (settings.data_dir / "popular-discovery.json").write_text(json.dumps(store), encoding="utf-8")

    def household(*, with_future: bool):  # noqa: ANN202
        factory = memory_session_factory()
        with factory.begin() as session:
            session.add(make_user("m"))
            session.add_all(_remote_watch("m", f"alpha-old{n}", "Alpha", base - timedelta(days=10 - n)) for n in range(3))
            if with_future:
                session.add(_remote_watch("m", "zeta-1", "Zeta", base - timedelta(days=1)))
                session.add(SourceAutomation(id="late", user_id="m", label="Zeta", source_url="https://www.youtube.com/@zeta",
                                             source_type="channel", cron_expression="0 * * * *", created_at=base))
        return factory

    as_of = base - timedelta(days=1, seconds=1)
    leaky = household(with_future=True)
    with leaky() as session:
        unhidden = replay.legacy_policy(session, "m", as_of, "home_picked", 5)
        held = replay.pick_instances(replay.watches(session, "m"))[-1]
        assert held.key == "id:youtube:zeta-1" and held.at - timedelta(seconds=1) == as_of
        replay.hide_after(session, as_of, [held])
        hidden = replay.legacy_policy(session, "m", as_of, "home_picked", 5)
        session.rollback()
    with household(with_future=False)() as session:
        clean = replay.legacy_policy(session, "m", as_of, "home_picked", 5)

    assert unhidden[0].key == "id:youtube:zeta-1"  # the setup would leak without hiding
    assert hidden == clean
    assert "id:youtube:zeta-1" not in [candidate.key for candidate in hidden][:1]


def _hash(video: str) -> str:
    return hashlib.sha256(f"youtube:{video}".encode()).hexdigest()


def _media(video: str, channel: str, published: datetime) -> RemoteMedia:
    return RemoteMedia(
        key=_hash(video), source_identity=f"youtube:{video}", extractor="youtube", remote_id=video, webpage_url=f"https://www.youtube.com/watch?v={video}",
        title=video, uploader=channel, published_at=published, category_keys=[], tokens=[], fetched_at=published, last_nominated_at=published,
    )


def _stub_recommend(monkeypatch, recommend) -> None:  # noqa: ANN001
    """The adapter-translation test pins what the adapter does with keys the seam returns, so it fixes the seam's output on the real module."""
    from app.services.reco import policy

    monkeypatch.setattr(policy, "recommend", recommend)


def test_the_adapter_speaks_the_legacy_key_and_channel_space(monkeypatch) -> None:  # noqa: ANN001
    replay = load("reco_replay")
    factory = memory_session_factory()
    base = datetime(2026, 9, 1, 12)
    with factory.begin() as session:
        session.add(make_user("m"))
        session.add(_media("alpha-1", "Alpha Channel", base - timedelta(days=3)))
        add_movie(session, "film", "A film", genres=("Drama",))
    _stub_recommend(monkeypatch, lambda session, member_id, as_of, surface, k, popular=(): [_hash("alpha-1"), "f" * 64])
    replay.UNRESOLVED.clear()
    with factory() as session:
        picked = replay.new_policy(session, "m", base, "home_picked", 10)
    assert picked == [replay.Candidate("id:youtube:alpha-1", "alpha channel", base - timedelta(days=3))]
    assert replay.UNRESOLVED["home_picked"] == 1  # an unknown key is counted, never silently dropped

    _stub_recommend(monkeypatch, lambda session, member_id, as_of, surface, k, popular=(): ["film", "no-such-title"])
    with factory() as session:
        titles = replay.new_policy(session, "m", base, "home_recommended", 10)
        assert titles == [replay._title(session.get(replay_models().MediaTitle, "film"))]
    assert replay.UNRESOLVED["home_recommended"] == 1


def test_the_new_policy_and_its_candidate_set_see_the_uncut_popular_snapshot(monkeypatch) -> None:  # noqa: ANN001
    """The 24-item feed cut limits the legacy policy only; the new policy ranks every snapshot item."""
    replay = load("reco_replay")
    base = datetime(2026, 9, 1, 12)
    stamp = base.replace(tzinfo=UTC).timestamp()
    store = {"version": 1, "refreshed_at": stamp, "categories": {category: {
        "items": [{"id": f"{category}-{n}", "uploader": f"{category} {n}", "title": f"{category} {n}", "view_count": 1_000 * (n + 1),
                   "published_at": stamp - 86_400 * (n + 1), "source": "youtube"} for n in range(8)],
        "last_success_at": stamp,
    } for category in ("music", "news", "gaming", "travel")}}  # 8 per category (per_category_limit), 32 in all
    (settings.data_dir / "popular-discovery.json").write_text(json.dumps(store), encoding="utf-8")
    factory = memory_session_factory()
    with factory.begin() as session:
        session.add(make_user("m"))
    seen: list[int] = []
    _stub_recommend(monkeypatch, lambda session, member_id, as_of, surface, k, popular=(): seen.append(len(popular)) or [])
    with factory() as session:
        replay.new_policy(session, "m", base, "home_picked", 10)
        assert len(replay.new_candidates(session, "m", base, "home_picked")) == 32
        assert len(replay.eligible(session, "m", base, "home_picked")) == 24  # the legacy policy's candidate set keeps the cut
    assert seen == [32]


def replay_models():  # noqa: ANN201
    import app.models

    return app.models


def test_the_harness_hides_reco_rows_from_the_cutoff_on() -> None:
    replay = load("reco_replay")
    factory = memory_session_factory()
    base = datetime(2026, 9, 1, 12)
    as_of = base - timedelta(days=1)
    with factory.begin() as session:
        session.add(make_user("m"))
        session.add_all([_media("old", "Alpha", base - timedelta(days=9)), _media("new", "Zeta", base)])
        session.add_all([
            RecoPool(user_id="m", item_key=_hash("old"), sources=SOURCE_FOLLOW, first_seen_at=as_of - timedelta(days=1), last_nominated_at=base),
            RecoPool(user_id="m", item_key=_hash("new"), sources=SOURCE_FOLLOW, first_seen_at=as_of, last_nominated_at=base),  # first seen exactly at the cutoff
        ])
        session.add_all([
            RecoEvent(user_id="m", at=as_of - timedelta(seconds=1), kind="open", target_kind="remote", item_key=_hash("old")),
            RecoEvent(user_id="m", at=as_of, kind="open", target_kind="remote", item_key=_hash("new")),
        ])
    with factory() as session:
        counts = replay.hide_after(session, as_of, [])
        assert counts["reco_pool"] == 1 and counts["reco_events"] == 1
        assert session.scalar(select(func.count()).select_from(RecoPool)) == 1
        assert session.scalar(select(func.count()).select_from(RecoEvent)) == 1
        session.rollback()


def _popular_store(base: datetime, items: list[tuple[str, str]]) -> None:
    store = {"version": 1, "categories": {"music": {
        "items": [{"id": video, "uploader": channel, "title": video, "view_count": 1_000 * (n + 1),
                   "published_at": (base - timedelta(days=30)).replace(tzinfo=UTC).timestamp(), "source": "youtube"}
                  for n, (video, channel) in enumerate(items)],
        "last_success_at": base.replace(tzinfo=UTC).timestamp(),
    }}, "refreshed_at": base.replace(tzinfo=UTC).timestamp()}
    (settings.data_dir / "popular-discovery.json").write_text(json.dumps(store), encoding="utf-8")


def test_a_watch_started_before_the_cutoff_and_finished_after_it_is_not_satisfied_history_at_the_cutoff() -> None:
    """Review I1: a row reverted to "started" keeps no post-cutoff watch depth (completions, max_fraction) for the profile to read."""
    from app.models import PlaybackProgress
    from app.services.reco.profile import build_profile

    replay = load("reco_replay")
    base = datetime(2026, 9, 1, 12)
    as_of, started, finished = base - timedelta(days=5), base - timedelta(days=9), base - timedelta(days=1)
    factory = memory_session_factory()
    with factory.begin() as session:
        session.add_all([make_user("m"), make_user("owner")])
        add_movie(session, "film", "Film", genres=["Drama"], days=-60)
        remote = _remote_watch("m", "zeta-1", "Zeta", finished)
        remote.created_at, remote.plays, remote.completions, remote.max_fraction = started, 2, 2, 1.0
        session.add_all([remote, PlaybackProgress(
            id="p", user_id="m", item_id="film-v", position_seconds=1200, duration_seconds=1200, completed=True,
            max_fraction=1.0, plays=1, completions=1, last_watched_at=finished, created_at=started,
        )])
    with factory() as session:
        replay.hide_after(session, as_of, [])
        profile = build_profile(session, "m", now=base, as_of=as_of)
        assert profile.satisfied == ()
        assert "film" not in profile.excluded  # the movie is still a candidate at the cutoff
        assert max(profile.affinity.values(), default=0.0) <= 0.0  # no completion credit for Zeta
        session.rollback()


def test_later_pool_rows_events_and_follows_never_shape_the_new_policys_list(monkeypatch) -> None:  # noqa: ANN001
    """The held-out video is in the pool only from after the cutoff, with an open and a follow after it; none may lift it."""
    replay = load("reco_replay")
    base = datetime(2026, 9, 1, 12)
    _popular_store(base, [(f"other-{n}", f"Other {n}") for n in range(6)])
    watched_at = base - timedelta(days=1)
    as_of = watched_at - timedelta(seconds=1)

    def household(*, with_future: bool):  # noqa: ANN202
        factory = memory_session_factory()
        with factory.begin() as session:
            session.add(make_user("m"))
            session.add_all(_media(f"alpha-{n}", "Alpha", base - timedelta(days=20 - n)) for n in range(6))
            session.add(_media("zeta-1", "Zeta", base - timedelta(days=2)))  # the household cache holds it in both worlds
            session.add_all(RecoPool(user_id="m", item_key=_hash(f"alpha-{n}"), sources=SOURCE_FOLLOW, first_seen_at=base - timedelta(days=19 - n),
                                     last_nominated_at=base - timedelta(days=19 - n)) for n in range(6))
            session.add_all(_remote_watch("m", f"alpha-old{n}", "Alpha", base - timedelta(days=10 - n)) for n in range(3))
            if with_future:
                session.add(_remote_watch("m", "zeta-1", "Zeta", watched_at))
                session.add(RecoPool(user_id="m", item_key=_hash("zeta-1"), sources=SOURCE_FOLLOW, first_seen_at=watched_at + timedelta(hours=1),
                                     last_nominated_at=watched_at + timedelta(hours=1)))
                session.add(RecoEvent(user_id="m", at=watched_at + timedelta(hours=2), kind="open", surface="home_picked", list_id="c" * 16, position=0,
                                      slot="exploit", target_kind="remote", item_key=_hash("zeta-1")))
                session.add(SourceAutomation(id="late", user_id="m", label="Zeta", source_url="https://www.youtube.com/@zeta", source_type="channel",
                                             cron_expression="0 * * * *", created_at=base))
        return factory

    leaky = household(with_future=True)
    with leaky() as session:
        held = replay.pick_instances(replay.watches(session, "m"))[-1]
        assert held.at - timedelta(seconds=1) == as_of
        counts = replay.hide_after(session, as_of, [held])
        assert counts["reco_pool"] == 1 and counts["reco_events"] == 1 and counts["source_automations"] == 1
        hidden = replay.new_policy(session, "m", as_of, "home_picked", 10)
        session.rollback()
    with household(with_future=False)() as session:
        clean = replay.new_policy(session, "m", as_of, "home_picked", 10)

    assert [c.key for c in hidden] == [c.key for c in clean]
    assert "id:youtube:zeta-1" not in [c.key for c in hidden]


def test_the_seeded_household_carries_pool_rows_and_the_late_follows_pool_is_after_every_cutoff() -> None:
    seed = load("seed_reco")
    factory = memory_session_factory()
    with factory() as session:
        seed.seed(session, settings.data_dir)
        assert session.scalar(select(func.count()).select_from(RecoPool)) > 0
        assert session.scalar(select(func.count()).select_from(RemoteMedia)) == len(seed.CHANNEL_CATEGORY) * seed.VIDEOS_PER_CHANNEL
        late = session.scalar(select(func.min(RecoPool.first_seen_at)).where(RecoPool.first_seen_at >= seed.BASE - timedelta(hours=1)))
        assert late is not None  # the late follow's nominations land after every instance, so the replay must hide them


def test_the_seeded_rows_carry_what_1_9_0_writes() -> None:
    """Pooled rows carry a channel key and tokens (pool._fill), progress carries watch depth: without them the
    channel discount and cap skip every pooled item and partial satisfied watches vanish from the profile."""
    from app.models import PlaybackProgress

    seed = load("seed_reco")
    factory = memory_session_factory()
    with factory() as session:
        seed.seed(session, settings.data_dir)
        assert session.scalar(select(func.count()).select_from(RemoteMedia).where(RemoteMedia.channel_key.is_(None))) == 0
        assert all(row.tokens for row in session.scalars(select(RemoteMedia)))
        for model in (RemotePlaybackProgress, PlaybackProgress):
            assert session.scalar(select(func.count()).select_from(model).where(model.plays == 0)) == 0
            assert session.scalar(select(func.count()).select_from(model).where(model.completed, model.completions == 0)) == 0
        # Films carry five actors, a director and two genres, so no two films share a whole token set.
        from app.models import MediaTitle
        from app.services.reco.profile import title_tokens

        films = list(session.scalars(select(MediaTitle).where(MediaTitle.type == "movie")))
        tokens = [title_tokens(film) for film in films]
        assert len(set(tokens)) == len(films)
        assert all(sum(token.startswith("cast:") for token in t) == 5 and any(token.startswith("dir:") for token in t) for t in tokens)
        assert session.scalar(select(func.count()).select_from(RemotePlaybackProgress).where(RemotePlaybackProgress.max_fraction >= 0.5)) > (
            session.scalar(select(func.count()).select_from(RemotePlaybackProgress).where(RemotePlaybackProgress.completed)))


def test_the_ablation_ranks_the_new_pool_with_the_legacy_scorer(monkeypatch) -> None:  # noqa: ANN001
    """Alpha is the member's history channel but is outside the Popular snapshot: only a policy that sees the pool can recommend it."""
    replay = load("reco_replay")
    base = datetime(2026, 9, 1, 12)
    _popular_store(base, [(f"other-{n}", f"Other {n}") for n in range(6)])
    factory = memory_session_factory()
    with factory.begin() as session:
        session.add(make_user("m"))
        session.add_all(_media(f"alpha-{n}", "Alpha", base - timedelta(days=20 - n)) for n in range(6))
        session.add_all(RecoPool(user_id="m", item_key=_hash(f"alpha-{n}"), sources=SOURCE_FOLLOW, first_seen_at=base - timedelta(days=30),
                                 last_nominated_at=base - timedelta(days=1)) for n in range(6))
        session.add_all(_remote_watch("m", f"alpha-old{n}", "Alpha", base - timedelta(days=10 - n)) for n in range(4))
    with factory() as session:
        legacy = replay.legacy_policy(session, "m", base, "home_picked", 5)
        ablated = replay.ablation_policy(session, "m", base, "home_picked", 5)
    assert "alpha" not in {c.channel for c in legacy}
    assert ablated and ablated[0].channel == "alpha"  # the legacy scorer's creator affinity, now with Alpha's uploads to choose from


def test_the_compare_run_scores_every_policy_on_one_pass_and_resolves_every_key(monkeypatch) -> None:  # noqa: ANN001
    replay = load("reco_replay")
    factory = memory_session_factory()
    with factory() as session:
        load("seed_reco").seed(session, settings.data_dir)

    report = replay.replay(factory, "new", also=("legacy", "ablation"))

    assert report == replay.replay(factory, "new", also=("legacy", "ablation"))
    assert report["policy"] == "new" and report["policies"] == ["new", "legacy", "ablation"]
    assert report["unresolved_keys"] == 0
    for metric, rows in report["hit_at_10"].items():
        assert set(rows) == {"new", "legacy", "ablation", "newest_first", "household_top_channel", "last_watch_channel"}, metric
    assert set(report["by_policy"]) == {"new", "legacy", "ablation"}
    assert report["coverage_at_20"] == report["by_policy"]["new"]["coverage_at_20"]
    # One denominator for everyone: legacy's whole pool is smaller than the shared universe, so its coverage is below 1.
    assert report["by_policy"]["legacy"]["coverage_at_20"]["home_picked"] < 1.0
    assert all(0 <= value <= 1 for policy in report["by_policy"].values() for value in policy["coverage_at_20"].values() if value is not None)
    assert 0 < report["pool_recall"] <= 1
    text = json.dumps(report)
    for private in ("ana", "Kitchen Lab", "kitchen lab", "kitchen-lab", "film"):
        assert private not in text


def test_a_legacy_only_run_keeps_its_old_shape() -> None:
    replay = load("reco_replay")
    factory = memory_session_factory()
    with factory() as session:
        load("seed_reco").seed(session, settings.data_dir)
    report = replay.replay(factory)
    assert report["policies"] == ["legacy"] and report["pool_recall"] is None and "legacy" in report["by_policy"]
    assert set(report["hit_at_10"]["remote_channel_hit@10"]) == {"legacy", "newest_first", "household_top_channel", "last_watch_channel"}


def _top12(distinct, over_cap, freshness):  # noqa: ANN001, ANN202
    return {"distinct_channels": distinct, "max_one_channel_over_cap": over_cap, "max_one_channel_worst": 3, "freshness_median_days": freshness}


def _report(**override):  # noqa: ANN003, ANN202
    """A compare report that meets every target against B = the legacy values below."""
    hits = {
        "remote_channel_hit@10": {"new": 0.90, "legacy": 0.575, "newest_first": 0.55, "household_top_channel": 0.30, "last_watch_channel": 0.575},
        "remote_item_hit@10": {"new": 0.80, "legacy": 0.375, "newest_first": 0.35, "household_top_channel": 0.175, "last_watch_channel": 0.35},
        "title_item_hit@10": {"new": 0.95, "legacy": 0.55, "newest_first": 0.10, "household_top_channel": 0.0, "last_watch_channel": 0.50},
        "anchored_title_hit@10": {"new": 0.95, "legacy": 0.95, "newest_first": 0.55, "household_top_channel": 0.025, "last_watch_channel": 0.70},
        "up_next_channel_hit@10": {"new": 0.60, "legacy": 0.471, "newest_first": 0.488, "household_top_channel": 0.367, "last_watch_channel": 0.529},
    }
    report = {
        "hit_at_10": hits,
        "by_policy": {
            "new": {"coverage_at_20": {"home_picked": 0.6}, "home_picked_top12": _top12(9.5, 0, 4.0)},
            "legacy": {"coverage_at_20": {"home_picked": 0.3}, "home_picked_top12": _top12(7.15, -1, 5.15)},
        },
    }
    for path, value in override.items():
        node = report
        *parents, leaf = path.split("/")
        for part in parents:
            node = node[part]
        node[leaf] = value
    return report


def _failed(rows):  # noqa: ANN001, ANN202
    return {row["metric"] for row in rows if not row["ok"]}


def test_a_report_that_meets_every_target_passes_all_of_them() -> None:
    replay = load("reco_replay")
    rows = replay.evaluate_targets(_report(), synthetic=True)
    assert _failed(rows) == set()
    assert len(rows) == 24  # 5 hit rows + coverage + distinct + max-one + freshness + 5 hit metrics x 3 trivial baselines


def test_each_relative_target_and_floor_is_applied_at_its_boundary() -> None:
    replay = load("reco_replay")
    cases = {  # path, value just under the bound, the row it must fail, on the synthetic seed
        "hit_at_10/remote_channel_hit@10/new": (0.719, "remote_channel_hit@10"),  # max(0.575 + 0.10, 0.9 x oracle 0.80) = 0.72
        "hit_at_10/remote_item_hit@10/new": (0.459, "remote_item_hit@10"),  # max(1.2 x 0.375, 0.8 x oracle 0.575) = 0.46
        "hit_at_10/title_item_hit@10/new": (0.824, "title_item_hit@10"),  # R-T6: max(1.5 x 0.55, 0.8 x oracle 0.82) = 0.825
        "hit_at_10/anchored_title_hit@10/new": (0.9, "anchored_title_hit@10"),  # 0.95 x 0.95 = 0.9025
        "hit_at_10/up_next_channel_hit@10/new": (0.565, "up_next_channel_hit@10"),  # 1.2 x 0.471 = 0.5652
        "by_policy/new/coverage_at_20": ({"home_picked": 0.44}, "coverage@20"),  # 1.5 x 0.3 = 0.45
        "by_policy/new/home_picked_top12": (_top12(9.1, 0, 4.0), "distinct_channels_top12"),  # R-T6: max(7.15, min(9.15, 10.8))
    }
    for path, (value, metric) in cases.items():
        assert metric in _failed(replay.evaluate_targets(_report(**{path: value}), synthetic=True)), path
        passing = {"hit_at_10/remote_channel_hit@10/new": 0.72, "hit_at_10/remote_item_hit@10/new": 0.46}
        if path in passing:
            assert metric not in _failed(replay.evaluate_targets(_report(**{path: passing[path]}), synthetic=True)), path
    def bars(legacy_title, legacy_distinct):  # noqa: ANN001, ANN202
        return replay.evaluate_targets(_report(**{"hit_at_10/title_item_hit@10/legacy": legacy_title,
                                                  "by_policy/legacy/home_picked_top12": _top12(legacy_distinct, -1, 5.15)}), synthetic=True)

    rows = {row["metric"]: row["required"] for row in bars(0.40, 9.84)}
    assert rows["title_item_hit@10"] == 0.656 and rows["distinct_channels_top12"] == 10.8  # 0.8 x 0.82 beats 1.5 x 0.40; 0.9 x 12 caps 11.84
    rows = {row["metric"]: row["required"] for row in bars(0.50, 11.5)}
    assert rows["title_item_hit@10"] == 0.75 and rows["distinct_channels_top12"] == 11.5  # 1.5 x B; never below B
    box = {  # the production copy keeps the relative targets
        "hit_at_10/remote_channel_hit@10/new": (0.862, "remote_channel_hit@10"),  # max(1.5 x 0.575, 0.675) = 0.8625
        "hit_at_10/title_item_hit@10/new": (0.659, "title_item_hit@10"),  # max(1.2 x 0.55, 0.57) = 0.66
        "by_policy/new/home_picked_top12": (_top12(9.1, 0, 4.0), "distinct_channels_top12"),  # 7.15 + 2
    }
    for path, (value, metric) in box.items():
        assert metric in _failed(replay.evaluate_targets(_report(**{path: value}), synthetic=False)), path


def test_the_absolute_floors_hold_even_when_the_relative_target_is_easy() -> None:
    replay = load("reco_replay")
    easy = {"hit_at_10/remote_channel_hit@10/legacy": 0.05, "hit_at_10/remote_channel_hit@10/new": 0.2}
    assert "remote_channel_hit@10" in _failed(replay.evaluate_targets(_report(**easy), synthetic=True))  # 0.2 beats 1.5 x 0.05 but not the 0.30 floor
    wide = {"by_policy/new/home_picked_top12": _top12(12, 1, 22.0)}  # one list holds one channel more than its R-T3 cap
    failed = _failed(replay.evaluate_targets(_report(**wide), synthetic=True))
    assert {"max_one_channel_top12_over_cap", "freshness_median_days"} <= failed and "distinct_channels_top12" not in failed


def test_a_missing_value_never_passes() -> None:
    replay = load("reco_replay")
    assert "remote_channel_hit@10" in _failed(replay.evaluate_targets(_report(**{"hit_at_10/remote_channel_hit@10/new": None}), synthetic=True))
    assert "remote_channel_hit@10" in _failed(replay.evaluate_targets(_report(**{"hit_at_10/remote_channel_hit@10/legacy": None}), synthetic=True))
    assert "coverage@20" in _failed(replay.evaluate_targets(_report(**{"by_policy/new/coverage_at_20": {"home_picked": None}}), synthetic=True))
    assert "freshness_median_days" in _failed(replay.evaluate_targets(
        _report(**{"by_policy/new/home_picked_top12": _top12(9.5, 0, None)}), synthetic=True))
    assert "max_one_channel_top12_over_cap" in _failed(replay.evaluate_targets(
        _report(**{"by_policy/new/home_picked_top12": _top12(9.5, None, 4.0)}), synthetic=True))


def test_a_tie_with_a_trivial_baseline_fails() -> None:
    replay = load("reco_replay")
    failed = _failed(replay.evaluate_targets(_report(**{"hit_at_10/up_next_channel_hit@10/last_watch_channel": 0.60}), synthetic=True))
    assert failed == {"up_next_channel_hit@10 > last_watch_channel"}


def test_the_box_caps_the_distinct_channel_bar_and_holds_one_channel_to_3_of_12() -> None:
    """Ruling R-T7: box legacy B = 12 would ask B + 2 = 14 of 12; the bar is max(7, min(B + 2, 10.8)), one channel <= 3."""
    replay = load("reco_replay")

    def rows(distinct, worst):  # noqa: ANN001, ANN202
        top12 = {**_top12(distinct, 0, 4.0), "max_one_channel_worst": worst}  # the box judges the worst count, not the R-T3 excess
        return replay.evaluate_targets(_report(**{"by_policy/legacy/home_picked_top12": {**_top12(12, -1, 5.15), "max_one_channel_worst": 1},
                                                  "by_policy/new/home_picked_top12": top12}), synthetic=False)

    assert {row["metric"]: row["required"] for row in rows(10.8, 3)}["distinct_channels_top12"] == 10.8
    assert _failed(rows(10.8, 3)) == set()
    assert _failed(rows(10.7, 3)) == {"distinct_channels_top12"}
    assert _failed(rows(10.8, 4)) == {"max_one_channel_top12"}


def test_remote_item_may_tie_newest_first_on_the_synthetic_seed_only() -> None:
    """Ruling R-T4: the seed draws the next watch from the newest uploads, so newest_first is near its oracle there."""
    replay = load("reco_replay")
    tie = _report(**{"hit_at_10/remote_item_hit@10/newest_first": 0.80})
    assert _failed(replay.evaluate_targets(tie, synthetic=True)) == set()
    behind = _report(**{"hit_at_10/remote_item_hit@10/newest_first": 0.81})
    assert _failed(replay.evaluate_targets(behind, synthetic=True)) == {"remote_item_hit@10 >= newest_first"}


def test_the_remote_item_row_is_judged_on_the_synthetic_seed_only() -> None:
    replay = load("reco_replay")
    weak = _report(**{"hit_at_10/remote_item_hit@10/new": 0.0})
    assert "remote_item_hit@10" in _failed(replay.evaluate_targets(weak, synthetic=True))
    rows = replay.evaluate_targets(weak, synthetic=False)
    assert _failed(rows) == set() and len(rows) == 24 - 1 - 3  # no item row, no item-versus-trivial rows


def test_the_new_policy_meets_every_synthetic_target_of_spec_8_1() -> None:
    """The release gate on the synthetic household (runs in make check). A failure means the ranking regressed; fix the ranking, not the gate."""
    replay = load("reco_replay")
    factory = memory_session_factory()
    with factory() as session:
        load("seed_reco").seed(session, settings.data_dir)
    report = replay.replay(factory, "new", also=("legacy", "ablation"))
    rows = replay.evaluate_targets(report, synthetic=True)
    assert len(rows) == 24
    failed = [f"{row['metric']}: {row['value']} needs {row['required']}" for row in rows if not row["ok"]]
    assert not failed, "\n".join(failed)
