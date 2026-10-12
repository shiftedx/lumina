"""The recommendation policy: helpers, remote surfaces, title surfaces, replay."""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.media_schemas import RecoAnnotation
from app.models import LibraryItem, MediaTitle, MemberInterest, RecoPool, RemoteMedia, RemotePlaybackProgress, SourceAutomation, UserSettings
from app.schemas import UpNextRequest, YouTubeSearchResult
from app.services.member_suppressions import MemberSuppressionService
from app.services.reco import SOURCE_FOLLOW, SOURCE_SEED, Candidate, MemberProfile, Ranked, SatisfiedItem, ServedList
from app.services.reco.events import served_lists
from app.services.reco.policy import (
    RecommendationPolicy,
    annotated_titles,
    annotation,
    category_order,
    entry_from_media,
    follow_shelf_keys,
    next_part_key,
    recommend,
    remote_key,
    shelf_visible,
    top_title,
    uncut,
)
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.youtube_channels import ChannelTabPage
from discovery_support import add_movie, add_progress, add_series
from support import make_user, memory_session_factory, popular_item, popular_snapshot

NOW = datetime(2026, 10, 1, 12, 0, 0)
UC = "UC" + "h" * 22
CHANNEL = f"https://www.youtube.com/channel/{UC}"
OTHER_CHANNEL = "https://www.youtube.com/channel/UC" + "q" * 22
LIST_ID = "0123456789abcdef"


def _tid(n: int) -> str:
    """A uuid-shaped title id: served title keys must match RECO_KEY."""
    return str(uuid.UUID(int=n))


def _candidate(key: str, title: str, *, channel: str | None = CHANNEL, sources: int = 4) -> Candidate:
    return Candidate(key=key, target_kind="remote", title=title, channel_key=channel, channel_name="Harbor Films",
                     tokens=frozenset(), published_at=None, sources=sources)


def _profile(*, satisfied=(), interests=frozenset()) -> MemberProfile:  # noqa: ANN001
    empty: dict = {}
    return MemberProfile(
        user_id="member", built_at=NOW, generation=0, affinity=empty, channel_aliases=empty, followed=frozenset(),
        interests=frozenset(interests), satisfied=tuple(satisfied), centroids=(), centroid_mass=(), taste_mix=empty,
        item_fatigue=empty, channel_fatigue=empty, fatigued_out=frozenset(), fewer=empty, spill=empty,
        hidden_items=frozenset(), hidden_channels=frozenset(), hidden_titles=frozenset(), excluded=frozenset(),
    )


def _satisfied(key: str, categories: tuple[str, ...], weight: float = 1.0) -> SatisfiedItem:
    return SatisfiedItem(key=key, target_kind="remote", title=key, channel_key=None, tokens=frozenset(), weight=weight,
                         at=NOW, category_keys=categories)


def _session(*members):  # noqa: ANN002, ANN202
    session = memory_session_factory()()
    session.add_all(members)
    session.commit()
    return session


# ---- helpers ------------------------------------------------------------------------------------------------

def test_remote_key_is_the_remote_progress_key_for_every_spelling() -> None:
    expected = RemotePlaybackProgressService.source_identity_key(RemotePlaybackProgressService.canonical_source_identity("youtube:abc123"))

    assert remote_key("youtube", "abc123", "https://www.youtube.com/watch?v=abc123") == expected
    assert remote_key("youtube", None, "https://youtu.be/abc123?si=share") == expected
    assert remote_key(None, None, None) is None
    assert len(remote_key("twitch", "v1", "https://www.twitch.tv/videos/1") or "") == 64


@pytest.mark.parametrize(("current", "sibling", "same_channel", "expected"), [
    ("Harbor walk part 3", "Harbor walk part 4", True, True),
    ("Harbor walk Pt.3", "Harbor walk pt 4", True, False),  # "Pt.3": the dot breaks \s*(\d+); no number, no pin
    ("Harbor walk pt 3", "Harbor walk pt4", True, True),
    ("Harbor walk episode 9", "Harbor walk episode 10", True, True),
    ("Harbor walk #12", "Harbor walk #13", True, True),
    ("Harbor walk part 3", "Harbor walk part 5", True, False),  # not the next number
    ("Harbor walk part 3", "Harbor walk part 3", True, False),  # the same number
    ("Harbor walk part 3", "Harbor walk part 4", False, False),  # another channel
    ("Harbor walk lighthouse lantern part 3", "Tidal pools part 4", True, False),  # < 60% of the words shared
    ("Manuscript 2", "Manuscript 3", True, False),  # "pt" inside a word is not a part
    ("Step 2 of the build", "Step 3 of the build", True, False),  # "ep" inside a word is not an episode
    ("Harbor walk", "Harbor walk part 2", True, False),  # the current title carries no number
])
def test_next_part_key_table(current: str, sibling: str, same_channel: bool, expected: bool) -> None:
    now_playing = _candidate("a" * 64, current)
    other = _candidate("b" * 64, sibling, channel=CHANNEL if same_channel else OTHER_CHANNEL)

    assert next_part_key(now_playing, [now_playing, other]) == ("b" * 64 if expected else None)


def test_next_part_key_prefers_the_lowest_key_among_equal_matches() -> None:
    now_playing = _candidate("a" * 64, "Harbor walk part 1")
    twins = [_candidate("c" * 64, "Harbor walk part 2"), _candidate("b" * 64, "Harbor walk part 2")]

    assert next_part_key(now_playing, twins) == "b" * 64


def test_category_order_puts_declared_interests_first_without_history() -> None:
    order = category_order(_profile(interests={"cooking"}), ["music", "gaming", "cooking"])

    assert order == ("cooking", "music", "gaming")


def test_category_order_mixes_history_and_interests_seventy_thirty() -> None:
    # History: gaming 2/3, music 1/3. Declared: music. gaming 0.7·2/3 = 0.467; music 0.7·1/3 + 0.3 = 0.533.
    profile = _profile(satisfied=[_satisfied("g", ("gaming",), 2.0), _satisfied("m", ("music",), 1.0)], interests={"music"})

    assert category_order(profile, ["cooking", "gaming", "music"]) == ("music", "gaming", "cooking")


def test_category_order_keeps_the_snapshot_order_for_a_cold_member() -> None:
    assert category_order(_profile(), ["music", "gaming", "cooking"]) == ("music", "gaming", "cooking")


def test_entry_from_media_maps_public_metadata_only() -> None:
    row = RemoteMedia(
        key="d" * 64, source_identity="url:https://www.twitch.tv/videos/1", extractor="twitch:vod", remote_id="1",
        webpage_url="https://www.twitch.tv/videos/1", title="Harbor stream", uploader="Harbor Films", channel_key=CHANNEL,
        channel_url="https://www.twitch.tv/harbor", duration=600, view_count=42, published_at=NOW, kind="video",
        category_keys=["gaming"], thumbnail="https://static.example/t.jpg", availability="public", tokens=[],
    )

    entry = entry_from_media(row)

    assert (entry.id, entry.source, entry.source_label, entry.uploader_url, entry.kind) == ("1", "twitch", "Twitch", "https://www.twitch.tv/harbor", "video")
    assert (entry.title, entry.duration, entry.view_count, entry.category_keys) == ("Harbor stream", 600, 42, ["gaming"])
    assert entry.reco is None


def test_annotation_copies_the_served_position_slot_and_reason() -> None:
    candidate = _candidate("e" * 64, "Harbor walk")
    ranked = Ranked(candidate=candidate, position=10, slot="explore", score=1.0, p_shown=0.2, reason_code="explore",
                    reason="Something different · like Tidal pools")
    served = ServedList(list_id=LIST_ID, user_id="member", surface="home_picked", context_key="-", created_at=NOW, items=(ranked,))

    assert annotation(served, ranked) == RecoAnnotation(list_id=LIST_ID, key="e" * 64, position=10, slot="explore",
                                                       reason_code="explore", reason="Something different · like Tidal pools")


def test_annotated_titles_keep_served_order_and_drop_invisible_titles() -> None:
    member = make_user("member")
    db = _session(member)
    add_movie(db, _tid(1), "Arrival")
    add_movie(db, _tid(2), "Contact")
    add_movie(db, _tid(3), "Private", visibility="private")
    db.commit()
    items = tuple(
        Ranked(candidate=Candidate(key=key, target_kind="title", title=key, channel_key=None, channel_name=None, tokens=frozenset(),
                                   published_at=None, sources=64),
               position=position, slot="exploit", score=1.0, p_shown=None, reason_code="well_rated", reason="Well rated")
        for position, key in enumerate((_tid(2), _tid(3), _tid(1)))
    )
    served = ServedList(list_id=LIST_ID, user_id="member", surface="home_recommended", context_key="-", created_at=NOW, items=items)

    summaries = annotated_titles(db, member, served)

    assert [(s.id, s.reco.position) for s in summaries] == [(_tid(2), 0), (_tid(1), 2)]


def test_shelf_visible_reads_the_members_home_layout() -> None:
    member, other = make_user("member"), make_user("other")
    db = _session(member, other)
    assert shelf_visible(db, member.id, "picked_for_you") is True  # no settings row: the default layout
    db.add(UserSettings(id="s1", user_id=member.id, ui_prefs={"home_shelves": [
        {"id": "picked_for_you", "visible": False}, {"id": "picked_for_you", "visible": True},
        {"id": "recommended", "visible": "yes"},
    ]}))
    db.add(UserSettings(id="s2", user_id=other.id, ui_prefs={"home_shelves": "not a list"}))
    db.commit()

    assert shelf_visible(db, member.id, "picked_for_you") is False  # the first entry wins (normalizeHomeShelves)
    assert shelf_visible(db, member.id, "recommended") is True  # a non-boolean visible is true
    assert shelf_visible(db, member.id, "from_follows") is True  # unlisted: appended visible
    assert shelf_visible(db, other.id, "picked_for_you") is True


def test_follow_shelf_keys_are_the_members_newest_twelve_per_follow() -> None:
    member, other = make_user("member"), make_user("other")
    db = _session(member, other)
    entries = [{"id": f"v{n}", "webpage_url": f"https://www.youtube.com/watch?v=v{n}"} for n in range(14)]
    db.add_all([
        SourceAutomation(id="f1", user_id=member.id, label="Harbor Films", source_url=CHANNEL, source_type="channel",
                         cron_expression="*/30 * * * *", active=True, auto_download=False, feed_entries=entries),
        SourceAutomation(id="f2", user_id=other.id, label="Quiet Lane", source_url=OTHER_CHANNEL, source_type="channel",
                         cron_expression="*/30 * * * *", active=True, auto_download=False,
                         feed_entries=[{"id": "secret", "webpage_url": "https://www.youtube.com/watch?v=secret"}]),
    ])
    db.commit()

    keys = follow_shelf_keys(db, member.id)

    assert keys == frozenset(remote_key("youtube", f"v{n}", f"https://www.youtube.com/watch?v=v{n}") for n in range(12))
    assert remote_key("youtube", "secret", "https://www.youtube.com/watch?v=secret") not in keys


def test_top_title_anchors_an_episode_on_its_series() -> None:
    member = make_user("member")
    db = _session(member)
    add_series(db, "show", "Vault Show", seasons={1: 2})
    db.commit()

    assert top_title(db, db.get(MediaTitle, "show-s1e2")).id == "show"
    assert top_title(db, db.get(MediaTitle, "show")).id == "show"


# ---- remote surfaces ----------------------------------------------------------------------------------------

def _member():  # noqa: ANN202
    """A fresh id per test: served_lists and the profile cache are process-wide."""
    return make_user(f"m-{uuid.uuid4().hex[:12]}")


def _watch(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _media(db, vid: str, title: str, *, channel: str = CHANNEL, uploader: str = "Harbor Films", days: int = 2) -> str:  # noqa: ANN001
    key = remote_key("youtube", vid, _watch(vid))
    db.add(RemoteMedia(
        key=key, source_identity=f"youtube:{vid}", extractor="youtube", remote_id=vid, webpage_url=_watch(vid), title=title,
        uploader=uploader, channel_key=channel, channel_url=channel, duration=600, view_count=1_000,
        published_at=NOW - timedelta(days=days), kind="video", category_keys=["music"], thumbnail=None, availability="public",
        tokens=[], fetched_at=NOW, last_nominated_at=NOW,
    ))
    return key


def _pooled(db, member, vid: str, title: str, *, sources: int = SOURCE_SEED, **fields) -> str:  # noqa: ANN001
    key = _media(db, vid, title, **fields)
    db.add(RecoPool(user_id=member.id, item_key=key, sources=sources, first_seen_at=NOW, last_nominated_at=NOW))
    return key


class FakeRefresher:
    def __init__(self) -> None:
        self.warmed: list[str] = []
        self.triggered: list[str] = []

    def warm_channel(self, channel_id: str) -> None:
        self.warmed.append(channel_id)

    def trigger(self, member_id: str) -> None:
        self.triggered.append(member_id)


class FakePages:
    def __init__(self, page: ChannelTabPage | None = None) -> None:
        self.value = page
        self.peeks: list[tuple[str, str, int]] = []

    def peek_tab(self, channel_id: str, tab: str, limit: int) -> ChannelTabPage | None:
        self.peeks.append((channel_id, tab, limit))
        return self.value

    def page(self, *_args) -> None:  # noqa: ANN002
        raise AssertionError("a request thread never loads a channel page")


def _policy(db, *, pages: FakePages | None = None, refresher: FakeRefresher | None = None, at: datetime = NOW):  # noqa: ANN001, ANN202
    return RecommendationPolicy(db, refresher=refresher or FakeRefresher(), channel_pages=pages or FakePages(), now=lambda: at)


def _snapshot(*items):  # noqa: ANN002, ANN202
    return uncut(popular_snapshot(*items), items)


def _keys(served: ServedList) -> list[str]:
    return [ranked.candidate.key for ranked in served.items]


def test_home_serves_the_members_pool_and_popular_with_annotations() -> None:
    member = _member()
    db = _session(member)
    pooled = [_pooled(db, member, f"p{n}", f"Harbor walk {n}", channel=f"{CHANNEL}{n}") for n in range(3)]
    db.commit()
    snapshot = _snapshot(popular_item("pop1", ("music",)), popular_item("pop2", ("music",)))
    policy = _policy(db)

    served = policy.home(member, snapshot)
    entries = policy.remote_entries(served, snapshot)

    assert set(pooled) <= set(_keys(served))
    assert remote_key("youtube", "pop1", _watch("pop1")) in _keys(served)
    assert served.surface == "home_picked" and len(served.list_id) == 16 and len(served.items) <= 20
    assert [entry.reco.position for entry in entries] == [ranked.position for ranked in served.items]
    assert all(entry.reco.list_id == served.list_id and entry.reco.reason for entry in entries)
    assert {entry.id for entry in entries} >= {"p0", "p1", "p2", "pop1", "pop2"}


def test_a_repeat_inside_thirty_minutes_is_the_same_list_and_later_a_new_one() -> None:
    member = _member()
    db = _session(member)
    _pooled(db, member, "p1", "Harbor walk")
    db.commit()
    snapshot = _snapshot()

    first = _policy(db).home(member, snapshot)
    assert served_lists.get(member.id, first.list_id, NOW) == first
    again = _policy(db, at=NOW + timedelta(minutes=29)).home(member, snapshot)
    later = _policy(db, at=NOW + timedelta(minutes=31)).home(member, snapshot)

    assert again.list_id == first.list_id
    assert later.list_id != first.list_id


def test_library_copies_completed_items_and_legacy_item_suppressions_never_appear() -> None:
    member = _member()
    db = _session(member)
    saved = _pooled(db, member, "saved", "Harbor walk saved")
    hidden = _pooled(db, member, "hidden", "Harbor walk hidden", channel=f"{CHANNEL}2")
    kept = _pooled(db, member, "kept", "Harbor walk kept", channel=f"{CHANNEL}3")
    db.add(LibraryItem(id="lib-1", user_id="owner", visibility="shared", extractor="youtube", remote_id="saved", title="Saved",
                       status="available", metadata_json={}))
    MemberSuppressionService(db).suppress_item(member, source="youtube", item_id="hidden", webpage_url=_watch("hidden"),
                                               title="Harbor walk hidden", uploader="Harbor Films")
    # A Popular item the member finished: the Popular loader knows no member, so the policy drops it.
    done = remote_key("youtube", "done", _watch("done"))
    db.add(RemotePlaybackProgress(id=f"rp-{member.id}", user_id=member.id, source_identity="youtube:done", source_identity_key=done,
                                  source_url=_watch("done"), extractor="youtube", remote_id="done", position_seconds=600,
                                  duration_seconds=600, completed=True, max_fraction=1.0, plays=1, completions=1,
                                  last_watched_at=NOW - timedelta(days=1)))
    db.add(LibraryItem(id="lib-2", user_id="owner", visibility="shared", extractor="youtube", remote_id="pop-saved", title="Saved",
                       status="available", metadata_json={}))
    db.commit()
    snapshot = _snapshot(popular_item("hidden", ("music",)), popular_item("done", ("music",)), popular_item("pop-saved", ("music",)),
                         popular_item("pop-kept", ("music",)))

    keys = _keys(_policy(db).home(member, snapshot))

    assert kept in keys and remote_key("youtube", "pop-kept", _watch("pop-kept")) in keys
    assert saved not in keys and hidden not in keys and done not in keys
    assert remote_key("youtube", "pop-saved", _watch("pop-saved")) not in keys


def test_up_next_reads_the_peeked_listing_and_never_the_current_item() -> None:
    member = _member()
    db = _session(member)
    current = _pooled(db, member, "now", "Harbor walk now")
    db.commit()
    listing = ChannelTabPage(entries=(
        YouTubeSearchResult(id="fresh", title="Harbor lanterns", uploader="Harbor Films", uploader_id=UC, uploader_url=CHANNEL,
                            duration=600, webpage_url=_watch("fresh"), source="youtube"),
    ), has_more=False)
    pages = FakePages(listing)
    request = UpNextRequest(source_url=_watch("now"), source_id="now", uploader="Harbor Films", channel_id=UC, limit=12)
    policy = _policy(db, pages=pages)

    served = policy.up_next(member, _snapshot(), request)
    entries = policy.remote_entries(served, _snapshot(), channel_id=UC)

    assert pages.peeks[0] == (UC, "videos", 60)
    assert remote_key("youtube", "fresh", _watch("fresh")) in _keys(served)
    assert current not in _keys(served)
    assert "fresh" in {entry.id for entry in entries}
    assert served.context_key == f"{current}:12"


def test_up_next_warms_the_channel_on_a_miss_and_never_loads_it() -> None:
    member = _member()
    db = _session(member)
    _pooled(db, member, "p1", "Tidal pools", channel=OTHER_CHANNEL)
    db.commit()
    refresher = FakeRefresher()
    request = UpNextRequest(source_url=_watch("now"), source_id="now", uploader="Harbor Films", channel_id=UC)

    served = _policy(db, pages=FakePages(None), refresher=refresher).up_next(member, _snapshot(), request)

    assert refresher.warmed == [UC]
    assert remote_key("youtube", "p1", _watch("p1")) in _keys(served)  # the pool alone serves the miss


def test_up_next_pins_the_next_part_first() -> None:
    member = _member()
    db = _session(member)
    part_four = _pooled(db, member, "part4", "Harbor walk part 4")
    for n in range(5):
        _pooled(db, member, f"o{n}", f"Tidal pools {n}", channel=f"{OTHER_CHANNEL}{n}")
    db.commit()
    request = UpNextRequest(source_url=_watch("part3"), source_id="part3", title="Harbor walk part 3", uploader="Harbor Films",
                            channel_id=UC)

    served = _policy(db).up_next(member, _snapshot(), request)

    first = served.items[0]
    assert (first.candidate.key, first.slot, first.reason_code) == (part_four, "pinned", "next_part")


def test_picked_leaves_the_follows_shelf_its_items_and_caps_follow_only_picks() -> None:
    member = _member()
    db = _session(member)
    on_shelf = _pooled(db, member, "shelf", "Harbor walk new", sources=SOURCE_FOLLOW)
    older = [_pooled(db, member, f"f{n}", f"Lantern {n}", sources=SOURCE_FOLLOW, channel=f"{CHANNEL}{n}", days=20) for n in range(10)]
    db.add(SourceAutomation(id=f"follow-{member.id}", user_id=member.id, label="Harbor Films", source_url=CHANNEL,
                            source_type="channel", cron_expression="*/30 * * * *", active=True, auto_download=False,
                            feed_entries=[{"id": "shelf", "webpage_url": _watch("shelf")}]))
    db.commit()

    keys = _keys(_policy(db).home(member, _snapshot()))

    assert on_shelf not in keys
    assert len([key for key in keys if key in older]) == 6

    db.add(UserSettings(id=f"s-{member.id}", user_id=member.id, ui_prefs={"home_shelves": [{"id": "from_follows", "visible": False}]}))
    db.commit()
    served_lists.drop(member.id)
    hidden_shelf = _keys(_policy(db).home(member, _snapshot()))

    assert on_shelf in hidden_shelf
    assert len([key for key in hidden_shelf if key in older]) > 6


def test_a_hidden_picked_shelf_is_not_computed() -> None:
    member = _member()
    db = _session(member)
    _pooled(db, member, "p1", "Harbor walk")
    db.add(UserSettings(id=f"s-{member.id}", user_id=member.id, ui_prefs={"home_shelves": [{"id": "picked_for_you", "visible": False}]}))
    db.commit()

    served = _policy(db).home(member, _snapshot())

    assert served.items == ()
    assert served_lists.find(member.id, "home_picked", "-", NOW) is None


def test_explore_returns_for_you_and_the_category_order() -> None:
    member = _member()
    db = _session(member)
    pooled = _pooled(db, member, "p1", "Harbor walk")
    db.add(MemberInterest(id=f"i-{member.id}", user_id=member.id, category_key="cooking", created_at=NOW - timedelta(days=1)))
    db.commit()

    served, order = _policy(db).explore(member, _snapshot(popular_item("pop1", ("music",))))

    assert served.surface == "explore_for_you" and pooled in _keys(served) and len(served.items) <= 24
    assert order == ("cooking", "music", "gaming")


def test_rails_drop_hidden_items_and_keep_what_the_loaders_skip() -> None:
    member = _member()
    db = _session(member)
    MemberSuppressionService(db).suppress_item(member, source="youtube", item_id="hidden", webpage_url=_watch("hidden"),
                                               title="Hidden", uploader="Creator")
    db.commit()
    snapshot = popular_snapshot(
        popular_item("tiny", ("music",), duration=30),  # a Short: no loader nominates it
        popular_item("hidden", ("music",)),
        popular_item("keep", ("music",)),
    )

    rails = _policy(db).rails(member, snapshot)

    assert [item.id for item in rails] == ["keep", "tiny"]


def test_display_lookup_seeks_remote_media_by_primary_key() -> None:
    member = _member()
    db = _session(member)
    _pooled(db, member, "p1", "Harbor walk")
    db.commit()
    statements: list[tuple[str, object]] = []

    def capture(_conn, _cursor, statement, parameters, _context, _many):  # noqa: ANN001, ANN202
        if "FROM remote_media" in statement and 'remote_media."key" IN' in statement:
            statements.append((statement, parameters))

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        _policy(db).home(member, _snapshot())
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)

    assert statements
    for statement, parameters in statements:
        plan = " ".join(str(row[-1]) for row in db.connection().exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters))
        assert "SCAN remote_media" not in plan, plan
        assert "sqlite_autoindex_remote_media_1" in plan or "PRIMARY KEY" in plan, plan


# ---- title surfaces and the replay seam ---------------------------------------------------------------------

def _library(db, member) -> dict[str, str]:  # noqa: ANN001
    """Two completed dramas (anchors), four unstarted dramas, two unstarted comedies, one private drama of someone else."""
    ids = {name: _tid(n) for n, name in enumerate(
        ("Anchor One", "Anchor Two", "Drama A", "Drama B", "Drama C", "Drama D", "Comedy A", "Comedy B", "Private"), start=1)}
    for name, title_id in ids.items():
        genre = "Comedy" if name.startswith("Comedy") else "Drama"
        add_movie(db, title_id, name, genres=[genre], people=["Ada Quill"],
                  visibility="private" if name == "Private" else "shared")
    db.commit()
    add_progress(db, member.id, f"{ids['Anchor One']}-v", completed=True, at=NOW - timedelta(days=2))
    add_progress(db, member.id, f"{ids['Anchor Two']}-v", completed=True, at=NOW - timedelta(days=1))
    db.commit()
    return ids


def test_title_rows_dedupe_in_order_and_never_offer_an_anchor_or_a_private_title() -> None:
    member = _member()
    db = _session(member)
    ids = _library(db, member)

    rows = _policy(db).title_rows(member)

    assert [kind for kind, _anchor, _served in rows] == ["because_you_watched", "because_you_watched", "recommended"]
    assert [anchor.id for _kind, anchor, _served in rows[:2]] == [ids["Anchor Two"], ids["Anchor One"]]
    served_keys = [key for _kind, _anchor, served in rows for key in _keys(served)]
    assert len(served_keys) == len(set(served_keys))  # deduplicated in order across rows
    assert not {ids["Anchor One"], ids["Anchor Two"], ids["Private"]} & set(served_keys)
    assert [served.surface for _kind, _anchor, served in rows] == ["home_because", "home_because", "home_recommended"]


def test_a_hidden_because_shelf_is_not_computed() -> None:
    member = _member()
    db = _session(member)
    _library(db, member)
    db.add(UserSettings(id=f"s-{member.id}", user_id=member.id,
                        ui_prefs={"home_shelves": [{"id": "because_you_watched", "visible": False}]}))
    db.commit()

    rows = _policy(db).title_rows(member)

    assert [kind for kind, _anchor, _served in rows] == ["recommended"]
    assert served_lists.find(member.id, "home_because", _tid(2), NOW) is None


def test_recommended_and_suggestions_are_empty_for_a_member_with_no_satisfied_history() -> None:
    member = _member()
    db = _session(member)
    add_movie(db, _tid(1), "Drama A", genres=["Drama"])
    db.commit()
    policy = _policy(db)

    assert [_keys(served) for _kind, _anchor, served in policy.title_rows(member)] == [[]]
    assert _keys(policy.suggestions(member, k=10, types={"movie"})) == []


def test_similar_compares_like_with_like_and_anchors_episodes_on_their_series() -> None:
    member = _member()
    db = _session(member)
    add_movie(db, _tid(1), "Drama A", genres=["Drama"])
    add_series(db, "show", "Vault Show", seasons={1: 2}, genres=["Drama"])
    add_series(db, "other-show", "Harbor Show", seasons={1: 1}, genres=["Drama"])
    db.commit()

    served = _policy(db).similar(member, db.get(MediaTitle, "show-s1e2"), k=12)

    assert served.surface == "title_similar" and served.context_key == "show:12"
    assert "show" not in _keys(served) and _tid(1) not in _keys(served)  # never the anchor; a series is like a series


def test_suggestions_filter_types_and_never_replace_the_home_list() -> None:
    member = _member()
    db = _session(member)
    ids = _library(db, member)
    add_series(db, "show", "Vault Show", seasons={1: 1}, genres=["Drama"])
    db.commit()
    policy = _policy(db)

    movies = policy.suggestions(member, k=5, types={"movie"})
    home = [served for kind, _anchor, served in policy.title_rows(member) if kind == "recommended"][0]

    assert movies.context_key == "jellyfin:5:movie" and len(movies.items) <= 5
    assert "show" not in _keys(movies) and ids["Drama A"] in set(_keys(movies)) | set(_keys(home))
    assert home.list_id != movies.list_id


def test_title_reasons_name_the_shared_person(monkeypatch) -> None:  # noqa: ANN001
    from app.models import Person
    from app.services.reco import policy as policy_module

    member = _member()
    db = _session(member)
    db.add(Person(id="person-ada", tmdb_id=4242, name="Ada Quill"))
    add_movie(db, _tid(1), "Seen Drama", genres=["Drama"])
    add_movie(db, _tid(2), "Ada's Next", genres=["Comedy"])
    db.commit()
    seen = Candidate(key=_tid(1), target_kind="title", title="Seen Drama", channel_key=None, channel_name=None,
                     tokens=frozenset({"dir:person-ada", "col:box-9"}), published_at=None, sources=64)
    shared = dataclasses.replace(seen, key=_tid(2), title="Ada's Next")
    profile = _profile(satisfied=[SatisfiedItem(key=_tid(1), target_kind="title", title="Seen Drama", channel_key=None,
                                                tokens=seen.tokens, weight=1.0, at=NOW)])

    assert policy_module.title_names(db, [shared], profile) == {"person-ada": "Ada Quill"}  # box-9 is not a title: no name

    passed: list[dict] = []
    real_rank = policy_module.rank
    monkeypatch.setattr(policy_module, "rank", lambda *args, names=None, **kwargs: passed.append(names) or real_rank(*args, names=names or {}, **kwargs))
    add_progress(db, member.id, f"{_tid(1)}-v", completed=True, at=NOW - timedelta(days=1))
    db.commit()
    _policy(db).title_rows(member)

    assert passed and all(isinstance(names, dict) for names in passed)


def test_recommend_replays_deterministically_without_a_provider(monkeypatch) -> None:  # noqa: ANN001
    from app.services import yt_dlp_service

    def refuse(*_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        raise AssertionError("the replay seam never calls a provider")

    monkeypatch.setattr(yt_dlp_service.YtDlpService, "youtube_search", refuse)
    member = _member()
    db = _session(member)
    _library(db, member)
    _pooled(db, member, "p1", "Harbor walk")
    db.commit()
    popular = [popular_item("pop1", ("music",))]

    titles = recommend(db, member.id, NOW, "home_recommended", 10)
    remote = recommend(db, member.id, NOW, "home_picked", 10, popular=popular)

    assert titles and titles == recommend(db, member.id, NOW, "home_recommended", 10)
    assert remote == recommend(db, member.id, NOW, "home_picked", 10, popular=popular)
    assert remote_key("youtube", "pop1", _watch("pop1")) in remote
    assert served_lists.find(member.id, "home_picked", "-", NOW) is None  # the seam never caches
    assert recommend(db, "nobody", NOW, "home_picked", 10) == []


@pytest.mark.parametrize("surface", ["home_because", "title_similar", "home_recommended"])
def test_recommend_ignores_a_completion_after_as_of(surface: str) -> None:
    member = _member()
    db = _session(member)
    ids = _library(db, member)
    add_progress(db, member.id, f"{ids['Drama A']}-v", completed=True, at=NOW + timedelta(days=1))
    db.commit()

    assert _tid(3) in recommend(db, member.id, NOW, surface, 20)  # Drama A: not the anchor, not "started", as of NOW
