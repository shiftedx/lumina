"""Recommendations policy.

One facade per surface over the member's pool (pool.py), profile and ranking (profile.py, ranking.py). Request threads
only: no provider call, no embedding call and no write. A member's candidates come only from their own pool,
the household Popular snapshot, Library titles visible to them and the peeked listing of the channel they are watching
. The legacy member_recommendations policy stays untouched behind the kill switch.
"""
from __future__ import annotations

import dataclasses
import re
import secrets
from array import array
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import batched
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.media_schemas import RecoAnnotation, RecoSurface, TitleRowKind, TitleSummary
from app.models import (
    MediaTitle,
    MemberRecommendationSuppression,
    Person,
    RemoteMedia,
    RemotePlaybackProgress,
    SourceAutomation,
    User,
    UserSettings,
    utcnow,
)
from app.schemas import PopularItemResponse, UpNextRequest, YouTubeSearchResult
from app.services.hls_relay_support import public_provider
from app.services.member_recommendations import TITLE_POOL_TYPES, MemberRecommendationPolicy
from app.services.popular_discovery import PopularItem, PopularSnapshot, _source_key
from app.services.reco import (
    CONSTANTS,
    SOURCE_CURRENT_CHANNEL,
    SOURCE_FOLLOW,
    SURFACE_K,
    Candidate,
    MemberProfile,
    Ranked,
    ServedList,
    SurfaceContext,
)
from app.services.reco.events import channel_key, served_lists
from app.services.reco.pool import (
    TitleBase,
    listing_candidates,
    popular_candidates,
    remote_candidates,
    saved_remote_keys,
    title_base,
    title_candidates,
)
from app.services.reco.profile import build_profile, candidate_from_remote, candidate_from_title, profiles, remote_tokens
from app.services.reco.ranking import rank
from app.services.remote_annotation import remote_source_identity
from app.services.remote_playback import RemotePlaybackProgressService

if TYPE_CHECKING:
    from app.services.reco.pool import RecoRefresher
    from app.services.youtube_channels import ChannelPages, ChannelTabPage

_SEARCH_SOURCES = frozenset({"youtube", "soundcloud", "twitch", "kick"})  # schemas.SearchSource
_ENTRY_KINDS = frozenset({"video", "short", "live", "channel", "playlist"})
# Up Next slot 1. The word forms need a word boundary, so "Manuscript 2" is not "pt 2" and "Step 2" not "ep 2".
_PART = re.compile(r"(?:\b(?:part|pt|ep|episode)|#)\s*(\d+)", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]{3,}")
FOLLOW_SHELF_ENTRIES = 12  # New from your follows shows each follow's newest 12 (SourceAutomationService.FEED_ENTRY_LIMIT)
STORED_SHELVES_CAP = 64  # homeShelves.ts reads at most this many entries of ui_prefs.home_shelves
_LOOKUP_CHUNK = 500  # keys per IN (…) lookup; SQLite's variable limit is far above it

# Because you watched: completions of the last 30 days, at most 3 rows.
BECAUSE_WINDOW = timedelta(days=30)
BECAUSE_ROWS = 3
# What a surface's builder hands the ranker: the candidates, the anchor (title or current video), the pinned key.
_Build = Callable[[MemberProfile], tuple[list[Candidate], Candidate | None, str | None]]


def uncut(snapshot: PopularSnapshot, items: Iterable[PopularItem]) -> PopularSnapshot:
    """The snapshot with other items: every ready category's uncut items (source f), or none for a response shell."""
    return dataclasses.replace(snapshot, items=tuple(items))


def remote_key(source: str | None, entry_id: str | None, webpage_url: str | None) -> str | None:
    """A remote entry's served key: its remote progress identity key, as remote_media.key."""
    identity = remote_source_identity(source, entry_id, webpage_url)
    if identity is None:
        return None
    try:
        return RemotePlaybackProgressService.source_identity_key(RemotePlaybackProgressService.canonical_source_identity(identity))
    except ValueError:
        return None


def local_date(now: datetime) -> date:
    """The household's local day of a naive-UTC moment: the exploration seed's date."""
    return now.replace(tzinfo=UTC).astimezone().date()


def _part_number(title: str) -> int | None:
    match = _PART.search(title or "")
    return int(match.group(1)) if match else None


def _words(title: str) -> frozenset[str]:
    return frozenset(_WORD.findall((title or "").casefold()))


def next_part_key(current: Candidate, candidates: Iterable[Candidate]) -> str | None:
    """Up Next's pinned slot: a same-channel candidate sharing ≥ 60% of the current title's words and carrying
    the next number. Ties go to the lowest key, so the pin is the same on every request."""
    number = _part_number(current.title)
    words = _words(current.title)
    if number is None or not words or not current.channel_key:
        return None
    matches = sorted(
        candidate.key for candidate in candidates
        if candidate.key != current.key and candidate.channel_key == current.channel_key
        and _part_number(candidate.title) == number + 1
        and len(words & _words(candidate.title)) / len(words) >= CONSTANTS.next_part_overlap
    )
    return matches[0] if matches else None


def category_order(profile: MemberProfile, keys: Sequence[str]) -> tuple[str, ...]:
    """Explore's rails in the member's taste order: p = 0.7·history + 0.3·declared over category keys,
    or the declared interests alone without history. Ties keep the snapshot's order, so a cold member sees it unchanged."""
    history: dict[str, float] = {}
    for item in profile.satisfied:
        for key in item.category_keys:
            history[key] = history.get(key, 0.0) + item.weight / len(item.category_keys)
    total = sum(history.values())
    declared = [key for key in keys if key in profile.interests]

    def share(key: str) -> float:
        declared_share = 1 / len(declared) if key in declared else 0.0
        if not total:
            return declared_share
        return CONSTANTS.mix_history * history.get(key, 0.0) / total + CONSTANTS.mix_declared * declared_share

    position = {key: index for index, key in enumerate(keys)}
    return tuple(sorted(keys, key=lambda key: (-share(key), position[key])))


def entry_from_media(row: RemoteMedia) -> PopularItemResponse:
    """A pooled video as a wire item: public metadata only."""
    family = (row.extractor or "youtube").lower().split(":", 1)[0]
    source = family if family in _SEARCH_SOURCES else "youtube"
    return PopularItemResponse(
        id=row.remote_id, title=row.title, uploader=row.uploader, uploader_url=row.channel_url, duration=row.duration,
        thumbnail=row.thumbnail, webpage_url=row.webpage_url, view_count=row.view_count, availability=row.availability,
        published_at=row.published_at, source=source, source_label=public_provider(source).label,
        kind=row.kind if row.kind in _ENTRY_KINDS else None, category_keys=list(row.category_keys or []),
    )


def entry_from_popular(item: PopularItem) -> PopularItemResponse:
    return PopularItemResponse.model_validate(item)


def entry_from_listing(entry: YouTubeSearchResult) -> PopularItemResponse:
    return PopularItemResponse.model_validate(entry.model_dump())


def annotation(served: ServedList, ranked: Ranked) -> RecoAnnotation:
    """Why one item is in one served list: the reason exactly as ranking rendered it."""
    return RecoAnnotation(
        list_id=served.list_id, key=ranked.candidate.key, position=ranked.position, slot=ranked.slot,
        reason_code=ranked.reason_code, reason=ranked.reason,
    )


def annotated_titles(db: Session, member: User, served: ServedList) -> list[TitleSummary]:
    """A served title list as summaries in served order, each with its annotation; titles no longer visible drop out."""
    from app.services.title_summaries import title_summaries  # title_summaries -> titles -> playback: keep imports light

    by_id = {ranked.candidate.key: ranked for ranked in served.items}
    return [summary.model_copy(update={"reco": annotation(served, by_id[summary.id])}) for summary in title_summaries(db, member, by_id)]


def shelf_visible(db: Session, member_id: str, shelf_id: str) -> bool:
    """Whether the member's Home shows ``shelf_id`` (ui_prefs.home_shelves, read as homeShelves.ts reads it): a hidden
    recommendation shelf is not computed at all."""
    prefs = db.scalar(select(UserSettings.ui_prefs).where(UserSettings.user_id == member_id))
    shelves = (prefs or {}).get("home_shelves")
    if not isinstance(shelves, list):
        return True
    for entry in shelves[:STORED_SHELVES_CAP]:
        if isinstance(entry, dict) and entry.get("id") == shelf_id:
            return entry.get("visible") is not False
    return True


def follow_shelf_keys(db: Session, member_id: str) -> frozenset[str]:
    """What New from your follows already shows: the newest 12 entries of each of the member's follows."""
    keys: set[str] = set()
    for entries in db.scalars(
        select(SourceAutomation.feed_entries).where(SourceAutomation.user_id == member_id, SourceAutomation.source_type == "channel")
    ):
        for entry in (entries or [])[:FOLLOW_SHELF_ENTRIES]:
            if not isinstance(entry, dict):
                continue
            provider = (entry.get("capabilities") or {}).get("provider") or "youtube"
            if key := remote_key(provider, entry.get("id"), entry.get("webpage_url")):
                keys.add(key)
    return frozenset(keys)


def top_title(db: Session, title: MediaTitle) -> MediaTitle:
    """An episode or season anchors on its series, as the legacy policy does."""
    while title.type in ("episode", "season") and title.parent_id:
        parent = db.get(MediaTitle, title.parent_id)
        if parent is None:
            break
        title = parent
    return title


def title_vector(db: Session, title_id: str) -> array | None:
    """The title's resident vector under the serving model, or None."""
    from app.services import embeddings  # embeddings -> yt_dlp_service; keep this module's import graph light

    choice = embeddings.serving(db)
    if choice is None:
        return None
    found = embeddings.vector_map(db, choice.model_id).get(title_id)
    return found[1] if found else None


class RecommendationPolicy:
    """The 1.9.0 policy: one method per surface. Each list is ranked once and kept 30 minutes in
    ``served_lists``, so a visit stays stable and events read exactly what was served."""

    def __init__(self, db: Session, *, refresher: RecoRefresher | None, channel_pages: ChannelPages | None,
                 now: Callable[[], datetime] = utcnow) -> None:
        self._db = db
        self._refresher = refresher
        self._channel_pages = channel_pages
        self._now = now
        self._title_bases: dict[tuple[str, datetime | None], TitleBase] = {}  # one per request, shared by its title lists (reco I2)

    # ---- remote surfaces -----------------------------------------------------------------------------------------

    def home(self, member: User, snapshot: PopularSnapshot) -> ServedList:
        """Picked for you: pool ∪ Popular. While New from your follows is visible it keeps its own items,
        and at most 6 of the 20 picks are follow-only."""
        if not shelf_visible(self._db, member.id, "picked_for_you"):
            return self._unserved(member, "home_picked")
        follows_shown = shelf_visible(self._db, member.id, "from_follows")
        shelf = follow_shelf_keys(self._db, member.id) if follows_shown else frozenset()

        def build(_profile: MemberProfile) -> tuple[list[Candidate], None, None]:
            return [c for c in self._remote_pool(member, _profile, snapshot.items) if c.key not in shelf], None, None

        return self._serve(member, "home_picked", "-", SURFACE_K["home_picked"], build, follow_cap=follows_shown)

    def up_next(self, member: User, snapshot: PopularSnapshot, request: UpNextRequest) -> ServedList:
        """Up next: pool ∪ Popular ∪ the peeked listing of the channel being watched; the next part pinned."""
        current = remote_key(request.source, request.source_id, request.source_url)
        page = self._peek(request.channel_id, warm=True)

        def build(_profile: MemberProfile) -> tuple[list[Candidate], Candidate | None, str | None]:
            anchor = self._current(request, current)
            candidates = self._remote_pool(member, _profile, snapshot.items, page=page, exclude=frozenset({current} if current else ()))
            return candidates, anchor, next_part_key(anchor, candidates) if anchor else None

        return self._serve(member, "up_next", f"{current or '-'}:{request.limit}", request.limit, build)

    def explore(self, member: User, snapshot: PopularSnapshot) -> tuple[ServedList, tuple[str, ...]]:
        """Explore's For you rail and the member's category order."""
        served = self._serve(member, "explore_for_you", "-", SURFACE_K["explore_for_you"],
                             lambda profile: (self._remote_pool(member, profile, snapshot.items), None, None))
        profile = profiles.get(self._db, member.id, now=self._now())
        return served, category_order(profile, [category.key for category in snapshot.categories])

    def rails(self, member: User, snapshot: PopularSnapshot) -> tuple[PopularItem, ...]:
        """Explore's category rails: the snapshot's own items with hidden and fatigued ones
        removed, re-ordered by score. Items no loader nominates (Shorts, live) keep their order after the ranked ones."""
        now = self._now()
        by_key = {key: item for item in snapshot.items if (key := remote_key(item.source, item.id, item.webpage_url))}
        hidden = self._hidden_items(member, {key: entry_from_popular(item) for key, item in by_key.items()})
        loaded = popular_candidates(self._db, snapshot.items)
        candidates = [c for c in loaded if c.key in by_key and c.key not in hidden]
        ctx = SurfaceContext(surface="explore_popular", k=len(candidates), local_date=local_date(now))
        profile = profiles.get(self._db, member.id, now=now)
        ranked = tuple(by_key[r.candidate.key] for r in rank(candidates, profile, ctx, now=now))
        nominated = {c.key for c in loaded}
        rest = tuple(
            item for item in snapshot.items
            if (key := remote_key(item.source, item.id, item.webpage_url)) not in nominated and key not in hidden
            and not _channel_hidden(item, profile)  # rank() never saw these, so apply its channel veto here
        )
        return ranked + rest

    def remote_entries(self, served: ServedList, snapshot: PopularSnapshot, *, channel_id: str | None = None
                       ) -> list[PopularItemResponse]:
        """The served list as wire items in served order, each carrying its annotation."""
        keys = [r.candidate.key for r in served.items]
        entries = self._entries(self._media_rows(keys), keys, snapshot.items, self._peek(channel_id, warm=False))
        return [entries[r.candidate.key].model_copy(update={"reco": annotation(served, r)})
                for r in served.items if r.candidate.key in entries]

    # ---- title surfaces ------------------------------------------------------------------------------------------

    def title_rows(self, member: User) -> list[tuple[TitleRowKind, MediaTitle | None, ServedList]]:
        """Home's Because you watched rows, then Recommended for you, deduplicated in order."""
        now = self._now()
        rows: list[tuple[TitleRowKind, MediaTitle | None, ServedList]] = []
        shown: set[str] = set()
        if shelf_visible(self._db, member.id, "because_you_watched"):
            anchors = MemberRecommendationPolicy(self._db).recently_completed(member, since=now - BECAUSE_WINDOW, limit=BECAUSE_ROWS)
            for anchor in anchors:
                served = self._serve(member, "home_because", anchor.id, SURFACE_K["home_because"], self._anchored(member, anchor))
                rows.append(("because_you_watched", anchor, _unseen(served, shown)))
        if shelf_visible(self._db, member.id, "recommended"):
            served = self._serve(member, "home_recommended", "-", SURFACE_K["home_recommended"],
                                 lambda profile: (self._titles(member, profile) if profile.satisfied else [], None, None))
            rows.append(("recommended", None, _unseen(served, shown)))
        return rows

    def similar(self, member: User, anchor: MediaTitle, *, k: int) -> ServedList:
        """More like this and Jellyfin Similar: an episode or season anchors on its series."""
        top = top_title(self._db, anchor)
        return self._serve(member, "title_similar", f"{top.id}:{k}", k, self._anchored(member, top))

    def suggestions(self, member: User, *, k: int, types: Iterable[str]) -> ServedList:
        """Jellyfin Suggestions: Recommended's ranking at the client's length; empty for a cold member."""
        wanted = tuple(sorted(types))
        return self._serve(member, "home_recommended", f"jellyfin:{k}:{','.join(wanted)}", k,
                           lambda profile: (self._titles(member, profile, types=wanted) if profile.satisfied else [], None, None))

    # ---- internals -------------------------------------------------------------------------------------------------

    def _serve(self, member: User, surface: RecoSurface, context_key: str, k: int, build: _Build, *,
               follow_cap: bool = False) -> ServedList:
        now = self._now()
        cached = served_lists.find(member.id, surface, context_key, now)
        if cached is not None:
            return cached
        profile = profiles.get(self._db, member.id, now=now)
        candidates, anchor, pinned = build(profile)
        ctx = SurfaceContext(surface=surface, k=k, local_date=local_date(now), context_key=context_key, anchor=anchor,
                             pinned_key=pinned)
        ranked = rank(candidates, profile, ctx, now=now, names=title_names(self._db, candidates, profile))
        if follow_cap:
            ranked = _cap_follow_only(ranked, candidates, profile, ctx, now)
        served = ServedList(list_id=secrets.token_hex(8), user_id=member.id, surface=surface, context_key=context_key,
                            created_at=now, items=tuple(ranked))
        served_lists.put(served)
        return served

    def _unserved(self, member: User, surface: RecoSurface) -> ServedList:
        """A hidden shelf's empty list: nothing ranked, nothing cached."""
        return ServedList(list_id=secrets.token_hex(8), user_id=member.id, surface=surface, context_key="-",
                          created_at=self._now(), items=())

    def _peek(self, channel_id: str | None, *, warm: bool) -> ChannelTabPage | None:
        """The cached videos tab of the channel being watched; a miss warms it on the refresher's worker."""
        if not channel_id or self._channel_pages is None:
            return None
        page = self._channel_pages.peek_tab(channel_id, "videos", 60)
        if page is None and warm and self._refresher is not None:
            self._refresher.warm_channel(channel_id)
        return page

    def _remote_pool(self, member: User, profile: MemberProfile, items: Sequence[PopularItem], *, page: ChannelTabPage | None = None,
                     exclude: frozenset[str] = frozenset(), as_of: datetime | None = None) -> list[Candidate]:
        """The member's remote candidates: their own pool, the household Popular items and the peeked
        listing; minus the current item, visible Library copies and completed items (the Popular and listing loaders know no
        member), legacy-keyed item suppressions and anything with no wire entry. Shorts, live and
        unplayable items never come out of the loaders."""
        merged: dict[str, Candidate] = {}
        for candidate in (*remote_candidates(self._db, member.id, as_of=as_of), *popular_candidates(self._db, items),
                          *(listing_candidates(self._db, page) if page is not None else ())):
            seen = merged.get(candidate.key)
            merged[candidate.key] = candidate if seen is None else dataclasses.replace(seen, sources=seen.sources | candidate.sources)
        rows = self._media_rows(list(merged))
        entries = self._entries(rows, list(merged), items, page)
        saved = saved_remote_keys(self._db, member, rows)
        blocked = self._hidden_items(member, entries) | saved | profile.excluded | exclude
        return [candidate for key, candidate in merged.items() if key in entries and key not in blocked]

    def _media_rows(self, keys: Sequence[str]) -> list[RemoteMedia]:
        """remote_media rows by primary key, only for keys the member's own sources produced."""
        return [row for chunk in batched(sorted(set(keys)), _LOOKUP_CHUNK)
                for row in self._db.scalars(select(RemoteMedia).where(RemoteMedia.key.in_(chunk)))]

    def _entries(self, rows: Sequence[RemoteMedia], keys: Sequence[str], items: Sequence[PopularItem],
                 page: ChannelTabPage | None) -> dict[str, PopularItemResponse]:
        """Wire entries for served keys: the remote_media rows, overlaid by the fresher Popular and listing entries."""
        wanted = set(keys)
        found = {row.key: entry_from_media(row) for row in rows}
        for entry in (*map(entry_from_popular, items), *map(entry_from_listing, page.entries if page is not None else ())):
            key = remote_key(entry.source, entry.id, entry.webpage_url)
            if key in wanted:
                found[key] = entry
        return found

    def _hidden_items(self, member: User, entries: dict[str, PopularItemResponse]) -> set[str]:
        """Not interested on a video: item suppressions keep the legacy _source_key, so match on it."""
        hidden = set(self._db.scalars(select(MemberRecommendationSuppression.target_key).where(
            MemberRecommendationSuppression.user_id == member.id, MemberRecommendationSuppression.scope == "item")))
        if not hidden:
            return set()
        return {key for key, entry in entries.items()
                if _source_key(entry.source, entry.id, entry.webpage_url, entry.title, entry.uploader) in hidden}

    def _current(self, request: UpNextRequest, key: str | None) -> Candidate | None:
        """The video being watched as Up Next's anchor: its remote_media row when one exists (vector, tokens), else built
        from the request alone."""
        if key is None:
            return None
        row = self._db.get(RemoteMedia, key)
        if row is not None:
            return candidate_from_remote(row, sources=SOURCE_CURRENT_CHANNEL)
        stable = channel_key(request.source, request.channel_id, request.channel_url, request.uploader)
        return Candidate(
            key=key, target_kind="remote", title=request.title or "", channel_key=stable, channel_name=request.uploader,
            tokens=remote_tokens(request.title, stable, request.category_keys), published_at=None,
            sources=SOURCE_CURRENT_CHANNEL, category_keys=tuple(request.category_keys),
        )

    def _anchored(self, member: User, anchor: MediaTitle) -> _Build:
        return lambda profile: (self._titles(member, profile, anchor=anchor),
                                candidate_from_title(anchor, title_vector(self._db, anchor.id)), None)

    def _titles(self, member: User, profile: MemberProfile, *, anchor: MediaTitle | None = None,
                types: Iterable[str] | None = None, as_of: datetime | None = None) -> list[Candidate]:
        """Library candidates visible to the member, never the anchor, of the wanted types (like with like)."""
        from app.routers.discovery import similar_types  # the route module imports this one

        if types is None:
            types = similar_types(anchor.type) if anchor is not None else TITLE_POOL_TYPES
        wanted = frozenset(types)
        base = self._title_bases.get((member.id, as_of))
        if base is None:
            base = self._title_bases[member.id, as_of] = title_base(self._db, member, as_of=as_of)
        candidates = [c for c in title_candidates(self._db, member, profile, anchor=anchor, as_of=as_of, base=base)
                      if anchor is None or c.key != anchor.id]
        kinds = base.kinds()
        return [c for c in candidates if kinds.get(c.key) in wanted]


def _channel_hidden(item: PopularItem, profile: MemberProfile) -> bool:
    """ranking.in_channels for an item no loader nominates: its stable channel key (aliased) or the 1.8.0 name key."""
    stable = channel_key(item.source, item.uploader_id, item.uploader_url, item.uploader)
    name = (item.uploader or "").strip().casefold()
    return (stable is not None and profile.channel_aliases.get(stable, stable) in profile.hidden_channels) or (
        bool(name) and name in profile.hidden_channels)


def _cap_follow_only(ranked: list[Ranked], candidates: list[Candidate], profile: MemberProfile, ctx: SurfaceContext,
                     now: datetime) -> list[Ranked]:
    """Home content balance: at most 6 picks whose only source is a follow; the best 6 stay, then re-rank."""
    follow_only = [r.candidate.key for r in ranked if r.candidate.sources == SOURCE_FOLLOW]
    if len(follow_only) <= CONSTANTS.follows_max_in_picked:
        return ranked
    keep = set(follow_only[:CONSTANTS.follows_max_in_picked])
    return rank([c for c in candidates if c.sources != SOURCE_FOLLOW or c.key in keep], profile, ctx, now=now)


def title_names(db: Session, candidates: Sequence[Candidate], profile: MemberProfile) -> dict[str, str]:
    """Display names for "More {collection}" and "With {person}": only the boxset and person ids a
    title candidate shares with the member's satisfied titles, one IN query each. Remote lists get {}."""
    seen = frozenset().union(*(item.tokens for item in profile.satisfied if item.target_kind == "title"))
    shared = {token for c in candidates if c.target_kind == "title" for token in c.tokens & seen}
    boxsets = sorted(token[4:] for token in shared if token.startswith("col:"))
    people = sorted(token.split(":", 1)[1] for token in shared if token.startswith(("dir:", "cast:")))
    names: dict[str, str] = {}
    if boxsets:
        names.update(db.execute(select(MediaTitle.id, MediaTitle.name).where(MediaTitle.id.in_(boxsets))).tuples().all())
    if people:
        names.update(db.execute(select(Person.id, Person.name).where(Person.id.in_(people))).tuples().all())
    return names


def _unseen(served: ServedList, shown: set[str]) -> ServedList:
    """Title rows are deduplicated in order; kept items keep their served positions for events."""
    items = tuple(r for r in served.items if r.candidate.key not in shown)
    shown.update(r.candidate.key for r in items)
    return dataclasses.replace(served, items=items)


def recommend(session: Session, member_id: str, as_of: datetime, surface: RecoSurface, k: int, *,
              popular: Sequence[PopularItem] = ()) -> list[str]:
    """The replay seam: item keys in order, built from the member's state as of ``as_of``.

    No provider, embedding or channel-page call, no list cache and no write. ``popular`` is the as-of Popular snapshot's
    items (the harness reads popular-discovery.json). Sampling is seeded on ``as_of``'s date, so a replay repeats.
    The follows-shelf rules of Picked for you need the member's UI state at ``as_of``, which is not stored,
    so the seam leaves them out.
    """
    member = session.get(User, member_id)
    if member is None:
        return []
    policy = RecommendationPolicy(session, refresher=None, channel_pages=None, now=lambda: as_of)
    profile = build_profile(session, member_id, now=as_of, as_of=as_of)
    anchor: Candidate | None = None
    context = "-"
    if surface == "up_next":
        current = session.scalar(
            select(RemotePlaybackProgress.source_identity_key)
            .where(RemotePlaybackProgress.user_id == member_id, RemotePlaybackProgress.last_watched_at < as_of)
            .order_by(RemotePlaybackProgress.last_watched_at.desc()).limit(1)
        )
        row = session.get(RemoteMedia, current) if current else None
        anchor = candidate_from_remote(row, sources=SOURCE_CURRENT_CHANNEL) if row is not None else None
        candidates = policy._remote_pool(member, profile, popular, exclude=frozenset({current} if current else ()), as_of=as_of)
        context = current or "-"
    elif surface in ("home_picked", "explore_for_you", "explore_popular"):
        candidates = policy._remote_pool(member, profile, popular, as_of=as_of)
    elif surface in ("home_because", "title_similar"):
        anchors = MemberRecommendationPolicy(session).recently_completed(member, until=as_of, limit=1)
        if not anchors:
            return []
        anchor = candidate_from_title(anchors[0], title_vector(session, anchors[0].id))
        candidates = policy._titles(member, profile, anchor=anchors[0], as_of=as_of)
        context = anchors[0].id
    else:
        candidates = policy._titles(member, profile, as_of=as_of) if profile.satisfied else []
    pinned = next_part_key(anchor, candidates) if surface == "up_next" and anchor is not None else None
    ctx = SurfaceContext(surface=surface, k=k, local_date=as_of.date(), context_key=context, anchor=anchor, pinned_key=pinned)
    return [r.candidate.key for r in rank(candidates, profile, ctx, now=as_of, names=title_names(session, candidates, profile))]
