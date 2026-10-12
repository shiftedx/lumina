"""Why this?: every served annotation follows the reason grammar; key cases end to end."""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from app.models import MediaTitle, RecoPool, RemoteMedia, SourceAutomation, UserSettings, utcnow
from app.routers import discovery
from app.schemas import UpNextRequest
from app.services import jellyfin_discovery
from app.services.reco import SOURCE_FOLLOW, SOURCE_SEED
from app.services.reco.policy import RecommendationPolicy, remote_key, uncut
from discovery_support import BASE, add_movie, add_progress, add_title
from support import make_user, memory_session_factory, popular_snapshot

NOW = BASE + timedelta(days=5)
UC = "UC" + "h" * 22
CHANNEL = f"https://www.youtube.com/channel/{UC}"
LONG_NAME = "The Extraordinarily Long Harbor Films Channel Name"
# Verbatim grammar per reason code. Names are ≤ 32 characters, the whole line ≤ 64.
GRAMMAR = {
    "next_part": r"Next part",
    "next_episode": r"Next episode",
    "explore": r"Something different(?: · like .{1,32})?",
    "same_channel": r"More from .{1,32}",
    "like_current": r"Like what you're watching",
    "like_anchor": r"Like .{1,32}",
    "follow_new": r"New from .{1,32}, which you follow",
    "channel": r"Because you watch .{1,32}|With .{1,32}|More .{1,32}",
    "finished": r"Because you finished .{1,32}",
    "interest": r"Popular in .{1,32}, which you picked",
    "popular": r"Popular in .{1,32}|Popular now",
    "new_arrival": r"New in your library",
    "well_rated": r"Well rated",
}


def _tid(n: int) -> str:
    return str(uuid.UUID(int=n))


def _member():  # noqa: ANN202
    return make_user(f"m-{uuid.uuid4().hex[:12]}")


def _session(*members):  # noqa: ANN002, ANN202
    session = memory_session_factory()()
    session.add_all(members)
    session.commit()
    return session


def _watch(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _pooled(db, member, vid: str, title: str, *, sources: int = SOURCE_SEED, channel: str = CHANNEL,  # noqa: ANN001
            uploader: str = "Harbor Films", days: float = 1) -> str:
    key = remote_key("youtube", vid, _watch(vid))
    db.add(RemoteMedia(
        key=key, source_identity=f"youtube:{vid}", extractor="youtube", remote_id=vid, webpage_url=_watch(vid), title=title,
        uploader=uploader, channel_key=channel, channel_url=channel, duration=600, view_count=1_000,
        published_at=NOW - timedelta(days=days), kind="video", category_keys=["music"], thumbnail=None, availability="public",
        tokens=[], fetched_at=NOW, last_nominated_at=NOW,
    ))
    db.add(RecoPool(user_id=member.id, item_key=key, sources=sources, first_seen_at=NOW, last_nominated_at=NOW))
    return key


class _Pages:
    def peek_tab(self, *_args) -> None:  # noqa: ANN002
        return None


class _Refresher:
    def warm_channel(self, _channel_id: str) -> None:
        return None


def _policy(db) -> RecommendationPolicy:  # noqa: ANN001
    return RecommendationPolicy(db, refresher=_Refresher(), channel_pages=_Pages(), now=lambda: NOW)


def _assert_grammar(annotations) -> None:  # noqa: ANN001
    for code, reason in annotations:
        assert re.fullmatch(GRAMMAR[code], reason), (code, reason)
        assert len(reason) <= 64, reason


def test_up_next_pins_the_next_part_with_its_reason_and_every_reason_follows_the_grammar() -> None:
    member = _member()
    db = _session(member)
    part = _pooled(db, member, "part4", "Harbor walk part 4")
    for n in range(14):
        _pooled(db, member, f"o{n}", f"Tidal pools {n}", channel=f"{CHANNEL}{n}")
    db.commit()
    request = UpNextRequest(source_url=_watch("part3"), source_id="part3", title="Harbor walk part 3", uploader="Harbor Films",
                            channel_id=UC)

    served = _policy(db).up_next(member, uncut(popular_snapshot(), ()), request)

    assert (served.items[0].candidate.key, served.items[0].reason_code, served.items[0].reason) == (part, "next_part", "Next part")
    assert served.items[11].slot == "explore" and served.items[11].reason_code == "explore"  # 1 of 12 at position 12
    _assert_grammar((r.reason_code, r.reason) for r in served.items)


def test_a_fresh_upload_of_a_followed_channel_says_so_with_a_truncated_name() -> None:
    member = _member()
    db = _session(member)
    fresh = _pooled(db, member, "new1", "Harbor lanterns", sources=SOURCE_FOLLOW, uploader=LONG_NAME)
    db.add(SourceAutomation(id=f"f-{member.id}", user_id=member.id, label=LONG_NAME, source_url=CHANNEL, source_type="channel",
                            cron_expression="*/30 * * * *", active=True, auto_download=False, feed_entries=[]))
    db.add(UserSettings(id=f"s-{member.id}", user_id=member.id, ui_prefs={"home_shelves": [{"id": "from_follows", "visible": False}]}))
    db.commit()

    served = _policy(db).home(member, uncut(popular_snapshot(), ()))

    first = served.items[0]
    assert (first.candidate.key, first.reason_code) == (fresh, "follow_new")
    name = first.reason.removeprefix("New from ").removesuffix(", which you follow")
    assert len(name) <= 32 and name.endswith("…") and LONG_NAME.startswith(name[:-1])
    _assert_grammar((r.reason_code, r.reason) for r in served.items)


def test_home_exploration_slots_say_something_different() -> None:
    member = _member()
    db = _session(member)
    for n in range(30):  # more than the 18 exploit slots, so the draw has items left to explore
        _pooled(db, member, f"s{n}", f"Unfamiliar find {n}", channel=f"{CHANNEL}{n}", uploader=f"Channel {n}")
    db.commit()

    served = _policy(db).home(member, uncut(popular_snapshot(), ()))

    assert [r.slot for r in served.items[10:12]] == ["explore", "explore"]
    assert all(r.reason.startswith("Something different") for r in served.items[10:12])
    _assert_grammar((r.reason_code, r.reason) for r in served.items)


def test_more_like_this_names_the_anchor_for_its_franchise_and_follows_the_grammar() -> None:
    member = _member()
    db = _session(member)
    add_title(db, "box", "boxset", "Harbor Saga")
    add_movie(db, _tid(1), "Arrival", genres=["Drama"], boxset_id="box")
    add_movie(db, _tid(2), "Arrival Returns", genres=["Drama"], boxset_id="box")
    for n in range(3, 8):
        add_movie(db, _tid(n), f"Drama {n}", genres=["Drama"])
    db.commit()

    served = _policy(db).similar(member, db.get(MediaTitle, _tid(1)), k=12)

    sequel = next(r for r in served.items if r.candidate.key == _tid(2))
    assert (sequel.reason_code, sequel.reason) == ("like_anchor", "Like Arrival")
    _assert_grammar((r.reason_code, r.reason) for r in served.items)


def test_title_rows_and_similar_routes_annotate_every_item() -> None:
    member = _member()
    db = _session(member)
    for n in range(1, 7):
        add_movie(db, _tid(n), f"Drama {n}", genres=["Drama"], people=["Ada Quill"])
    db.commit()
    add_progress(db, member.id, f"{_tid(1)}-v", completed=True, at=utcnow() - timedelta(days=1))  # the routes read the real clock
    db.commit()

    rows = discovery.get_home_title_rows(member, db).rows
    similar = discovery.list_similar_titles(_tid(1), 12, member, db)

    assert rows and all(item.reco is not None for row in rows for item in row.items)
    assert rows[0].kind == "because_you_watched" and rows[0].title == "Because you watched Drama 1"
    assert similar and all(item.reco and item.reco.list_id == similar[0].reco.list_id for item in similar)
    _assert_grammar((item.reco.reason_code, item.reco.reason) for row in rows for item in row.items)


def test_jellyfin_gets_ranked_ids_and_no_reasons() -> None:
    member = _member()
    db = _session(member)
    for n in range(1, 5):
        add_movie(db, _tid(n), f"Drama {n}", genres=["Drama"])
    db.commit()
    add_progress(db, member.id, f"{_tid(1)}-v", completed=True, at=NOW)
    db.commit()

    similar = jellyfin_discovery.similar_refs(db, member, _tid(1), 3)
    suggestions = jellyfin_discovery.suggestion_refs(db, member, {"movie"}, 3)

    assert similar and all(isinstance(ref, str) for ref in similar) and _tid(1) not in similar and len(similar) <= 3
    assert all(isinstance(ref, str) for ref in suggestions) and _tid(1) not in suggestions
    assert jellyfin_discovery.similar_refs(db, member, "nope", 3) is None
    assert jellyfin_discovery.suggestion_refs(db, _member(), {"movie"}, 3) == []  # a cold member gets []
