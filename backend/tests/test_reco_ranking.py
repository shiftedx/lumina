"""Recommendation ranking: pure maths, hand-computed expectations."""
from __future__ import annotations

from array import array
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from app.services.reco import (
    CONSTANTS, SOURCE_CHANNEL, SOURCE_FOLLOW, SOURCE_HISTORY, SOURCE_LIBRARY, SOURCE_POPULAR, SOURCE_SEED, Candidate,
    MemberProfile, SatisfiedItem, SurfaceContext,
)
from app.services.reco import ranking

NOW = datetime(2026, 10, 1, 12, 0, 0)
DAY = date(2026, 10, 1)


def _candidate(key: str, **fields) -> Candidate:  # noqa: ANN003
    values = {
        "key": key, "target_kind": "remote", "title": f"Video {key}", "channel_key": f"https://www.youtube.com/channel/UC{key:0>22}",
        "channel_name": f"Channel {key}", "tokens": frozenset({key}), "published_at": None, "sources": SOURCE_POPULAR,
    }
    values.update(fields)
    return Candidate(**values)


def _title(key: str, **fields) -> Candidate:  # noqa: ANN003
    values = {"target_kind": "title", "title": f"Film {key}", "channel_key": None, "channel_name": None, "sources": SOURCE_LIBRARY}
    values.update(fields)
    return _candidate(key, **values)


def _seed(key: str, *, kind: str = "remote", tokens=frozenset(), vector=None, title: str | None = None) -> SatisfiedItem:  # noqa: ANN001
    return SatisfiedItem(key=key, target_kind=kind, title=title or f"Seen {key}", channel_key=None, tokens=frozenset(tokens),
                         weight=1.0, at=NOW, vector=vector)


def _profile(**fields) -> MemberProfile:  # noqa: ANN003
    values = {
        "user_id": "alice", "built_at": NOW, "generation": 0, "affinity": {}, "channel_aliases": {}, "followed": frozenset(),
        "interests": frozenset(), "satisfied": (), "centroids": (), "centroid_mass": (), "taste_mix": {}, "item_fatigue": {},
        "channel_fatigue": {}, "fatigued_out": frozenset(), "fewer": {}, "spill": {}, "hidden_items": frozenset(),
        "hidden_channels": frozenset(), "hidden_titles": frozenset(), "excluded": frozenset(),
    }
    values.update(fields)
    return MemberProfile(**values)


def _ctx(surface: str = "home_picked", k: int = 20, **fields) -> SurfaceContext:  # noqa: ANN003
    return SurfaceContext(surface=surface, k=k, local_date=fields.pop("local_date", DAY), **fields)


def _channel(key: str) -> str:
    return _candidate(key).channel_key


def _unit(*values: float) -> array:
    norm = sum(value * value for value in values) ** 0.5
    return array("f", (value / norm for value in values))


# ---- factors and the score ---------------------------------------------------------------------------------

def test_affinity_matches_the_spec_table() -> None:
    assert ranking.affinity(3.0) == pytest.approx(0.632121, abs=1e-6)  # a follow alone
    assert ranking.affinity(1.5) == pytest.approx(0.393469, abs=1e-6)  # one fresh completion
    assert ranking.affinity(3.0 + 3 * 1.5) == pytest.approx(0.917915, abs=1e-6)  # a follow and three completions (spec rounds to 0.95)
    assert ranking.affinity(-0.5) == 0.0  # one skip
    assert ranking.decay(30.0, 30.0) == pytest.approx(0.5) and ranking.decay(60.0, 30.0) == pytest.approx(0.25) and ranking.decay(-1.0, 30.0) == 1.0


def test_topic_is_normalised_between_the_median_and_the_99th_percentile() -> None:
    assert ranking.normalise_topic([0.1, 0.2, 0.3, 0.4, 0.5]) == pytest.approx([0.0, 0.0, 0.0, 0.510204, 1.0], abs=1e-6)
    assert ranking.normalise_topic([0.4, 0.4, 0.4]) == [0.0, 0.0, 0.0]
    assert ranking.normalise_topic([]) == []


def test_raw_topic_is_the_best_centroid_cosine_else_the_best_jaccard() -> None:
    with_vectors = _profile(centroids=(_unit(1, 0), _unit(0, 1)))
    assert ranking.topic_raw(_candidate("a", vector=_unit(3, 4)), with_vectors) == pytest.approx(0.8, abs=1e-6)
    tokens_only = _profile(satisfied=(_seed("s1", tokens={"x", "y"}), _seed("s2", tokens={"x", "z", "w"})))
    assert ranking.topic_raw(_candidate("a", tokens=frozenset({"x", "y", "q"})), tokens_only) == pytest.approx(2 / 3)
    # Another model's vector (different length) falls back to tokens.
    assert ranking.topic_raw(_candidate("a", vector=array("f", [1.0, 0.0, 0.0]), tokens=frozenset({"x", "y"})),
                     _profile(centroids=(_unit(1, 0),), satisfied=(_seed("s", tokens={"x", "y"}),))) == 1.0


def test_freshness_quality_fatigue_and_feedback_at_their_boundaries() -> None:
    assert ranking.freshness(_candidate("a"), NOW) == 1.0  # unknown age
    assert ranking.freshness(_candidate("a", published_at=NOW), NOW) == pytest.approx(1.6)
    assert ranking.freshness(_candidate("a", published_at=NOW - timedelta(days=7)), NOW) == pytest.approx(1 + 0.6 / 2.718281828, abs=1e-6)
    assert ranking.freshness(_title("t", published_at=NOW - timedelta(days=30)), NOW) == pytest.approx(1.110364, abs=1e-6)
    assert ranking.quality(_candidate("a")) == 1.0  # unknown views
    assert ranking.quality(_candidate("a", views=0)) == 0.75
    assert ranking.quality(_candidate("a", views=10_000_000)) == pytest.approx(1.25, abs=1e-6)
    assert ranking.quality(_candidate("a", views=10_000_000, channel_view_percentile=0.5)) == 1.0  # the channel percentile wins
    assert ranking.quality(_title("t")) == 1.0 and ranking.quality(_title("t", rating=4.0)) == pytest.approx(0.8)
    assert ranking.quality(_title("t", rating=7.0)) == pytest.approx(1.0) and ranking.quality(_title("t", rating=9.5)) == pytest.approx(1.2)
    tired = _profile(item_fatigue={"a": 2.0}, channel_fatigue={_channel("a"): 30.0})
    assert ranking.fatigue(_candidate("a"), tired) == pytest.approx(0.25)  # 1/(1 + 1) × the 0.5 channel floor
    assert ranking.fatigue(_candidate("a"), _profile(channel_fatigue={_channel("a"): 4.0})) == pytest.approx(1 / 1.2)
    assert [ranking.fewer_factor(days) for days in (0, 34.9, 35, 70, 105, 139.9, 140, 400)] == pytest.approx([0.2, 0.2, 0.4, 0.6, 0.8, 0.8, 1.0, 1.0])
    assert [ranking.spill_factor(days) for days in (0, 15, 30, 90)] == pytest.approx([0.7, 0.85, 1.0, 1.0])
    both = _profile(fewer={_channel("a"): NOW - timedelta(days=35)}, spill={_channel("a"): NOW - timedelta(days=15)})
    assert ranking.feedback(_candidate("a"), both, NOW) == pytest.approx(0.4 * 0.85)
    # A pre-1.9.0 row keyed by the bare casefolded name still applies.
    assert ranking.feedback(_candidate("a", channel_name="Some Channel"), _profile(fewer={"some channel": NOW}), NOW) == pytest.approx(0.2)


def test_the_spec_sanity_examples_to_three_decimals() -> None:
    home = _ctx()
    followed = _profile(affinity={_channel("a"): 3.0})
    value, contributions = ranking.score(_candidate("a", published_at=NOW - timedelta(days=1), channel_view_percentile=0.5),
                                 followed, home, topic=0.4, now=NOW)
    assert value == pytest.approx(4.658, abs=5e-4)
    assert contributions == pytest.approx({"channel": 1.264241, "topic": 0.8, "interest": 0.0, "context": 0.0}, abs=1e-6)
    picked = _profile(interests=frozenset({"music"}))
    value, _ = ranking.score(_candidate("b", published_at=NOW - timedelta(days=3), views=2_000_000, category_keys=("music",)),
                     picked, home, topic=0.2, now=NOW)
    assert value == pytest.approx(3.171, abs=5e-4)
    completed_once = _profile(affinity={_channel("c"): 1.5})
    value, _ = ranking.score(_candidate("c", published_at=NOW - timedelta(days=200)), completed_once, home, topic=0.6, now=NOW)
    assert value == pytest.approx(2.987, abs=5e-4)


def test_title_affinity_and_the_anchor_context() -> None:
    seen = _profile(satisfied=(_seed("m1", kind="title", tokens={"col:b1", "dir:p1", "cast:p2"}),))
    assert ranking.score(_title("t", tokens=frozenset({"col:b1"})), seen, _ctx("home_recommended"), topic=0, now=NOW)[1]["channel"] == 2.0
    assert ranking.score(_title("t", tokens=frozenset({"dir:p1"})), seen, _ctx("home_recommended"), topic=0, now=NOW)[1]["channel"] == pytest.approx(1.2)
    assert ranking.score(_title("t", tokens=frozenset({"cast:p2"})), seen, _ctx("home_recommended"), topic=0, now=NOW)[1]["channel"] == pytest.approx(0.4)
    anchor = _title("anchor", tokens=frozenset({"genre:drama", "col:b9"}), channel_key="b9")
    similar = _ctx("title_similar", 12, anchor=anchor, context_key="anchor")
    value, contributions = ranking.score(_title("t", tokens=frozenset({"genre:drama", "col:b9"}), channel_key="b9"), _profile(), similar, topic=0, now=NOW)
    assert contributions["context"] == pytest.approx(3 * 1.0 + 1.0) and value == pytest.approx(5.0)
    current = _candidate("now", tokens=frozenset({"x", "y"}))
    up_next = _ctx("up_next", 12, anchor=current, context_key="now")
    _value, contributions = ranking.score(_candidate("a", tokens=frozenset({"x"}), channel_key=current.channel_key), _profile(), up_next, topic=0, now=NOW)
    assert contributions["context"] == pytest.approx(2 * 0.5 + 1.0)


def test_vetoes_remove_suppressed_finished_fatigued_and_current_items() -> None:
    profile = _profile(hidden_items=frozenset({"h"}), hidden_channels=frozenset({_channel("c"), "legacy name"}),
                       hidden_titles=frozenset({"t"}), excluded=frozenset({"done"}), fatigued_out=frozenset({"tired"}))
    home = _ctx()
    assert ranking.vetoed(_candidate("h"), profile, home) and ranking.vetoed(_candidate("c"), profile, home)
    assert ranking.vetoed(_candidate("n", channel_name="Legacy Name"), profile, home)
    assert ranking.vetoed(_title("t"), profile, home) and ranking.vetoed(_candidate("done"), profile, home) and ranking.vetoed(_candidate("tired"), profile, home)
    assert ranking.vetoed(_candidate("now"), profile, _ctx("up_next", 12, context_key="now"))
    assert ranking.vetoed(_candidate("anchor", sources=SOURCE_HISTORY), profile, home)
    assert not ranking.vetoed(_candidate("fine"), profile, home)


# ---- channel discount, cap, calibration, DPP -------------------------------------------------------------------

def test_the_channel_discount_sequence() -> None:
    five = [(_candidate(f"v{i}", channel_key="ch:x"), 1.0) for i in range(5)]
    assert [value for _c, value in ranking.channel_discount(five, _profile())] == pytest.approx([1.0, 0.7, 0.55, 0.475, 0.4375])
    assert [value for _c, value in ranking.channel_discount(five, _profile(), current_channel="ch:x")] == pytest.approx(
        [1.0, 0.85, 0.775, 0.7375, 0.71875])
    loose = [(_candidate("a", channel_key=None), 0.5), (_candidate("b", channel_key=None), 0.6)]
    assert [(c.key, v) for c, v in ranking.channel_discount(loose, _profile())] == [("b", 0.6), ("a", 0.5)]
    # A legacy name key merges into its stable key before grouping.
    merged = _profile(channel_aliases={"name:youtube:x": "ch:x"})
    pair = [(_candidate("a", channel_key="ch:x"), 1.0), (_candidate("b", channel_key="name:youtube:x"), 0.9)]
    assert [value for _c, value in ranking.channel_discount(pair, merged)] == pytest.approx([1.0, 0.63])


def test_no_channel_fills_more_than_four_of_twenty() -> None:
    ordered = [(_candidate(f"v{i}", channel_key="ch:x"), 1.0 - i / 100) for i in range(8)] + [(_candidate("o"), 0.1)]
    assert [c.key for c, _v in ranking.channel_cap(ordered, _profile(), 20)] == ["v0", "v1", "v2", "v3", "o"]
    assert len(ranking.channel_cap(ordered, _profile(), 40)) == 9  # 8 per 40


def _mixed_pool(news_score: float) -> tuple[list, list, MemberProfile]:
    profile = _profile(taste_mix={"music": 0.7, "news": 0.3})
    pool = [(_candidate(f"m{i:02d}", category_keys=("music",)), 1.0) for i in range(30)]
    pool += [(_candidate(f"n{i:02d}", category_keys=("news",)), news_score) for i in range(30)]
    return pool, [ranking.group_of(candidate, profile) for candidate, _v in pool], profile


def test_calibration_gives_a_70_30_taste_a_14_6_split_of_twenty() -> None:
    pool, groups, profile = _mixed_pool(1.0)
    chosen = ranking.calibrate(pool, groups, profile.taste_mix, 20)
    assert len(chosen) == 20 and sum(groups[i] == "news" for i in chosen) == 6
    # A 5% lower score still lets the minority in (λ = 0.3 against 20 summed scores): 18/2, never 20/0.
    pool, groups, profile = _mixed_pool(0.95)
    assert sum(groups[i] == "news" for i in ranking.calibrate(pool, groups, profile.taste_mix, 20)) == 2
    # No taste mix: the top of the pool, in order.
    assert ranking.calibrate(pool, groups, {}, 3) == [0, 1, 2]


def test_groups_are_centroids_else_the_strongest_category_or_genre() -> None:
    centroids = _profile(centroids=(_unit(1, 0), _unit(0, 1)), taste_mix={"centroid:0": 0.5, "centroid:1": 0.5})
    assert ranking.group_of(_candidate("a", vector=_unit(1, 3)), centroids) == "centroid:1"
    mix = _profile(taste_mix={"music": 0.2, "news": 0.5, "genre:drama": 0.3})
    assert ranking.group_of(_candidate("a", category_keys=("music", "news")), mix) == "news"
    assert ranking.group_of(_title("t", tokens=frozenset({"genre:drama", "genre:war"})), mix) == "genre:drama"
    assert ranking.group_of(_candidate("a", category_keys=("cooking",)), mix) is None


def test_dpp_never_puts_two_identical_items_side_by_side_when_another_exists() -> None:
    a, b = _candidate("a", tokens=frozenset({"x", "y"})), _candidate("b", tokens=frozenset({"x", "y"}))
    c = _candidate("c", tokens=frozenset({"z"}))
    # q = 1, 0.91, 0.55. After a: d_b² = 0.91²·(1 − 0.9²) = 0.1573; d_c² = 0.55² − (0.9·0.55·e^(−1/0.98))² = 0.2707. c is second.
    assert ranking.dpp_select([(a, 1.0), (b, 0.9), (c, 0.5)], 3) == [0, 2, 1]
    # A weak alternative loses to a strong duplicate: q_c = 0.19, d_c² = 0.19² − (0.9·0.19·e^(−1/0.98))² = 0.0323 < 0.1573.
    assert ranking.dpp_select([(a, 1.0), (b, 0.9), (c, 0.1)], 3) == [0, 1, 2]


def test_dpp_windows_restart_every_twelve() -> None:
    twins = [(_candidate(f"t{i:02d}", tokens=frozenset({"same"})), 1.0 - i / 100) for i in range(24)]
    order = ranking.dpp_select(twins, 24)
    assert sorted(order[:12]) == order[:12] and len(order) == 24 and order[0] == 0 and order[12] == 12


def test_a_near_singular_kernel_falls_back_to_score_order() -> None:
    twins = [(_candidate(f"t{i}", tokens=frozenset({"same"})), score_) for i, score_ in enumerate((1.0, 0.9, 0.8, 0.7))]
    assert ranking.dpp_select(twins, 4, replace(CONSTANTS, dpp_alpha=1.0)) == [0, 1, 2, 3]  # rank one: every later gain is 0
    assert ranking.dpp_select(twins, 4, replace(CONSTANTS, dpp_alpha=1.2)) == [0, 1, 2, 3]  # not PSD: gains go negative


# ---- reasons ----------------------------------------------------------------------------------

def _reason(candidate: Candidate, contributions: dict, profile: MemberProfile | None = None, ctx: SurfaceContext | None = None,
            slot: str = "exploit", names=None) -> tuple[str, str]:  # noqa: ANN001
    base = {"channel": 0.0, "topic": 0.0, "interest": 0.0, "context": 0.0}
    return ranking.choose_reason(candidate, {**base, **contributions}, profile or _profile(), ctx or _ctx(), slot=slot, now=NOW,
                         names=names or {})


def test_pinned_and_explore_slots_explain_themselves() -> None:
    assert _reason(_candidate("a"), {}, slot="pinned") == ("next_part", "Next part")
    assert _reason(_title("t"), {}, slot="pinned") == ("next_episode", "Next episode")
    seen = _profile(satisfied=(_seed("s1", tokens={"x"}, title="Old Favourite"), _seed("s2", tokens={"q"})))
    assert _reason(_candidate("a", tokens=frozenset({"x", "y"})), {}, seen, slot="explore") == (
        "explore", "Something different · like Old Favourite")
    assert _reason(_candidate("a", tokens=frozenset({"z"})), {}, seen, slot="explore") == ("explore", "Something different")


def test_anchored_surfaces_name_the_anchor_when_context_dominates() -> None:
    current = _candidate("now", channel_key="ch:x")
    up_next = _ctx("up_next", 12, anchor=current, context_key="now")
    assert _reason(_candidate("a", channel_key="ch:x", channel_name="Tide Pools"), {"context": 1.0}, ctx=up_next) == (
        "same_channel", "More from Tide Pools")
    assert _reason(_candidate("a"), {"context": 1.2}, ctx=up_next) == ("like_current", "Like what you're watching")
    similar = _ctx("title_similar", 12, anchor=_title("anchor", title="The Long Winter"), context_key="anchor")
    assert _reason(_title("t"), {"context": 3.0}, ctx=similar) == ("like_anchor", "Like The Long Winter")
    # Below 1.0 the anchor does not explain the item.
    assert _reason(_candidate("a", channel_name="Tide Pools"), {"context": 0.9, "channel": 1.0}, ctx=up_next) == (
        "channel", "Because you watch Tide Pools")


def test_a_followed_upload_within_seven_days_says_so() -> None:
    fresh = _candidate("a", sources=SOURCE_FOLLOW, published_at=NOW - timedelta(days=7), channel_name="Tide Pools")
    assert _reason(fresh, {"channel": 1.26}) == ("follow_new", "New from Tide Pools, which you follow")
    stale = replace(fresh, published_at=NOW - timedelta(days=8))
    assert _reason(stale, {"channel": 1.26}) == ("channel", "Because you watch Tide Pools")


def test_the_strongest_relevance_term_picks_the_reason() -> None:
    seen = _profile(satisfied=(_seed("s1", tokens={"x"}, title="Deep Sea Week"),), interests=frozenset({"travel"}))
    candidate = _candidate("a", tokens=frozenset({"x"}), category_keys=("cooking", "travel"), channel_name="Tide Pools")
    assert _reason(candidate, {"channel": 0.5, "topic": 0.8}, seen) == ("finished", "Because you finished Deep Sea Week")
    assert _reason(candidate, {"channel": 0.8, "topic": 0.8}, seen) == ("channel", "Because you watch Tide Pools")  # ties: channel
    assert _reason(candidate, {"interest": 0.5}, seen) == ("interest", "Popular in Travel, which you picked")
    assert _reason(candidate, {"channel": 0.29}, seen) == ("popular", "Popular in Cooking")  # weak evidence
    assert _reason(_candidate("b"), {}) == ("popular", "Popular now")


def test_title_reasons_name_the_person_or_collection_when_known() -> None:
    seen = _profile(satisfied=(_seed("m1", kind="title", tokens={"dir:p1", "cast:p2", "col:b1"}, title="First Film"),))
    names = {"p1": "Ada Director", "p2": "Ben Actor", "b1": "The Saga Collection"}
    assert _reason(_title("t", tokens=frozenset({"col:b1"})), {"channel": 2.0}, seen, names=names) == ("channel", "More The Saga Collection")
    assert _reason(_title("t", tokens=frozenset({"dir:p1", "cast:p2"})), {"channel": 1.2}, seen, names=names) == ("channel", "With Ada Director")
    assert _reason(_title("t", tokens=frozenset({"cast:p2"})), {"channel": 0.4}, seen) == ("finished", "Because you finished First Film")
    assert _reason(_title("t", published_at=NOW - timedelta(days=30)), {}) == ("new_arrival", "New in your library")
    assert _reason(_title("t", published_at=NOW - timedelta(days=31)), {}) == ("well_rated", "Well rated")


def test_names_are_cut_at_32_and_lines_at_64() -> None:
    long_name = "A" * 40
    code, text = _reason(_candidate("a", channel_name=long_name), {"channel": 1.0})
    assert text == "Because you watch " + "A" * 31 + "…" and len(text) <= 64
    code, text = _reason(_candidate("a", sources=SOURCE_FOLLOW, published_at=NOW, channel_name=long_name), {})
    assert code == "follow_new" and text == "New from " + "A" * 31 + "…, which you follow" and len(text) == 59
    # No grammar line exceeds 64 with 32-character names; the 64 cut is the backstop.
    assert ranking.clip("x" * 70, 64) == "x" * 63 + "…" and ranking.clip("x" * 64, 64) == "x" * 64


# ---- exploration and the served list ---------------------------------------------------------------------------

def test_boltzmann_and_the_seeded_draw() -> None:
    assert ranking.boltzmann([1.0, 0.5], 0.3) == pytest.approx([0.909742, 0.090258], abs=1e-6)  # 1 : 0.5^(10/3)
    assert sum(ranking.boltzmann([3.0, 2.0, 1.0, 0.5], 0.3)) == pytest.approx(1.0)
    assert ranking.boltzmann([0.0, 0.0], 0.3) == [0.5, 0.5]
    first = ranking.explore_rng("alice", DAY, "home_picked", "-").random()
    assert first == ranking.explore_rng("alice", DAY, "home_picked", "-").random()
    assert first != ranking.explore_rng("alice", DAY + timedelta(days=1), "home_picked", "-").random()
    assert first != ranking.explore_rng("bob", DAY, "home_picked", "-").random()
    pool = [(_candidate(f"x{i}"), 1.0 - i / 10) for i in range(5)]
    draws = ranking.explore_draw(pool, 2, ranking.explore_rng("alice", DAY, "home_picked", "-"))
    assert len({c.key for c, _s, _p in draws}) == 2 and all(0 < p <= 1 for _c, _s, p in draws)
    first_key, _score, first_p = draws[0]
    index = [c.key for c, _v in pool].index(first_key.key)
    assert first_p == pytest.approx(ranking.boltzmann([v for _c, v in pool], 0.3)[index])
    assert len(ranking.explore_draw(pool * 10, 50, ranking.explore_rng("a", DAY, "s", "-"))) == 40  # only the top M = 40 are drawn from


def _household(unfamiliar: int = 10) -> tuple[list[Candidate], MemberProfile]:
    familiar = [_candidate(f"f{i:02d}", channel_key=f"ch:{i % 10}", tokens=frozenset({f"t{i}"}), published_at=NOW - timedelta(days=i))
                for i in range(40)]
    strangers = [_candidate(f"u{i:02d}", channel_key=f"new:{i}", tokens=frozenset({f"u{i}"}), sources=SOURCE_SEED) for i in range(unfamiliar)]
    profile = _profile(affinity={f"ch:{i}": 3.0 for i in range(10)})
    return familiar + strangers, profile


def test_home_picked_reserves_positions_11_and_12_for_exploration() -> None:
    candidates, profile = _household()
    served = ranking.rank(candidates, profile, _ctx(), now=NOW)
    assert len(served) == 20 and [r.position for r in served] == list(range(20))
    assert [r.position for r in served if r.slot == "explore"] == [10, 11]
    assert all(r.candidate.key.startswith("u") and 0 < r.p_shown <= 1 and r.reason_code == "explore" for r in served if r.slot == "explore")
    assert all(r.p_shown is None for r in served if r.slot == "exploit")
    assert max(sum(1 for r in served if r.candidate.channel_key == channel) for channel in {r.candidate.channel_key for r in served}) <= 4


def test_the_same_day_gives_the_same_list_and_the_next_day_changes_only_exploration() -> None:
    candidates, profile = _household(unfamiliar=20)
    today = ranking.rank(candidates, profile, _ctx(), now=NOW)
    assert today == ranking.rank(candidates, profile, _ctx(), now=NOW)
    later = [ranking.rank(candidates, profile, _ctx(local_date=DAY + timedelta(days=n)), now=NOW) for n in range(1, 8)]
    for other in later:
        assert [(r.position, r.candidate.key) for r in other if r.slot == "exploit"] == [
            (r.position, r.candidate.key) for r in today if r.slot == "exploit"]
    assert any({r.candidate.key for r in other if r.slot == "explore"} != {r.candidate.key for r in today if r.slot == "explore"}
               for other in later)


@pytest.mark.parametrize("surface,k,explore", [("home_picked", 24, [10, 11, 22, 23]), ("up_next", 12, [11]),
                                               ("title_similar", 12, [11]), ("explore_popular", 24, [])])
def test_exploration_sits_only_at_the_tail_and_never_at_the_top(surface: str, k: int, explore: list[int]) -> None:
    candidates, profile = _household(unfamiliar=30)
    served = ranking.rank(candidates, profile, _ctx(surface, k), now=NOW)
    assert [r.position for r in served if r.slot == "explore"] == explore
    assert served[0].slot != "explore"  # autoplay takes the first non-explore item


def test_a_pinned_next_part_goes_first() -> None:
    candidates, profile = _household()
    served = ranking.rank(candidates, profile, _ctx("up_next", 12, pinned_key="f30", anchor=_candidate("now"), context_key="now"), now=NOW)
    assert (served[0].candidate.key, served[0].slot, served[0].reason_code) == ("f30", "pinned", "next_part")
    assert len(served) == 12 and [r.position for r in served if r.slot == "explore"] == [11]


def test_too_few_explorable_items_are_backfilled_by_exploit_items() -> None:
    candidates, profile = _household(unfamiliar=0)
    served = ranking.rank(candidates, profile, _ctx(), now=NOW)
    assert len(served) == 20 and {r.slot for r in served} == {"exploit"}
    gated = [_candidate("g", channel_key="new:g", sources=SOURCE_POPULAR)]  # unfamiliar, s = 0, no seed or listing source
    assert {r.slot for r in ranking.rank(candidates + gated, profile, _ctx(), now=NOW)} == {"exploit"}


def test_a_short_list_is_served_short_and_an_empty_one_empty() -> None:
    assert ranking.rank([], _profile(), _ctx(), now=NOW) == []
    served = ranking.rank([_candidate("a"), _candidate("b")], _profile(), _ctx(), now=NOW)
    assert [r.position for r in served] == [0, 1]
    assert ranking.rank([_candidate("h")], _profile(hidden_items=frozenset({"h"})), _ctx(), now=NOW) == []


def test_unfamiliar_titles_share_no_genre_or_person_with_satisfied_titles() -> None:
    seen = _profile(satisfied=(_seed("m1", kind="title", tokens={"genre:drama", "cast:p1"}),))
    assert ranking.unfamiliar(_title("t", tokens=frozenset({"genre:comedy"})), seen)
    assert not ranking.unfamiliar(_title("t", tokens=frozenset({"genre:drama"})), seen)
    assert not ranking.unfamiliar(_candidate("a"), _profile(followed=frozenset({_channel("a")})))
    assert ranking.unfamiliar(_candidate("a"), _profile(affinity={_channel("a"): -0.5}))
    assert ranking.explorable(_candidate("a"), 0.25, _profile()) and not ranking.explorable(_candidate("a"), 0.24, _profile())
    assert ranking.explorable(_candidate("a", sources=SOURCE_CHANNEL), 0.0, _profile())


def test_the_exploration_scan_builds_taste_once_reuses_affinity_and_stops_at_the_top_m(monkeypatch: pytest.MonkeyPatch) -> None:
    satisfied = tuple(_seed(f"s{i}", kind="title", tokens={f"genre:g{i}", f"cast:c{i}", f"dir:d{i}"}) for i in range(100))
    titles = [_title(f"t{i:03d}", tokens=frozenset({f"genre:new{i}"}), sources=SOURCE_SEED) for i in range(200)]
    calls = {"taste": 0, "affinity": 0, "explorable": 0}
    taste, affinity, explorable = ranking.title_taste, ranking.title_affinity, ranking.explorable

    def count(name, real):  # noqa: ANN001, ANN202
        def wrapped(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            calls[name] += 1
            return real(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(ranking, "title_taste", count("taste", taste))
    monkeypatch.setattr(ranking, "title_affinity", count("affinity", affinity))
    monkeypatch.setattr(ranking, "explorable", count("explorable", explorable))
    served = ranking.rank(titles, _profile(satisfied=satisfied), _ctx("home_recommended", 20), now=NOW)
    assert any(r.slot == "explore" for r in served)
    assert calls["taste"] == 1  # one taste set per list, not one per candidate
    assert calls["affinity"] <= len(titles) + len(served)  # score's a_c is reused; only reasons may look again
    assert calls["explorable"] <= CONSTANTS.explore_top_m  # the draw only sees the top M, so the scan stops there
