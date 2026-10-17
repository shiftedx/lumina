"""Normalize provider replay-chat actions into a bounded timed chat asset.

A *timed chat asset* is a synchronized, member-scoped sequence of timed chat
events reconstructed from a completed live broadcast. It is deliberately NOT a
subtitle track (it carries author presentation, badges, paid amounts,
membership events, and moderation state) and NOT a Library comment (it is not
household-authored and never grants Library access).

The normalized shape here is provider-neutral so later providers (e.g. Twitch)
can populate the same events, but only the YouTube replay path is implemented.
Public serialization retains no continuation tokens, request headers, cookies,
author photo URLs, or other upstream addresses. Custom-emote source images are
kept only in storage metadata and must be mapped to Lumina artwork URLs before
they can cross the public API.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit


# Kinds are provider-neutral. A later provider maps its native item types onto
# this same closed vocabulary rather than inventing parallel ones.
EVENT_KIND_MESSAGE = "message"
EVENT_KIND_PAID_MESSAGE = "paid_message"
EVENT_KIND_PAID_STICKER = "paid_sticker"
EVENT_KIND_MEMBERSHIP = "membership"
EVENT_KIND_SYSTEM = "system"

MODERATION_VISIBLE = "visible"
MODERATION_DELETED = "deleted"
MODERATION_AUTHOR_REMOVED = "author_removed"

# Outcomes double as the durable asset status once a build completes. "partial"
# and "oversized" both keep a bounded, usable prefix of events; the media player
# is never affected by either.
OUTCOME_READY = "ready"
OUTCOME_EMPTY = "empty"
OUTCOME_PARTIAL = "partial"
OUTCOME_OVERSIZED = "oversized"
OUTCOME_MALFORMED = "malformed"

_MAX_EMOTES = 32
_MAX_EMOTE_LABEL_CHARS = 128
_MAX_EMOTE_IMAGE_URL_CHARS = 2048
_PUBLIC_ARTWORK_URL = re.compile(r"/api/artwork/(?:emotes|remote)/[A-Za-z0-9_-]{1,256}")


@dataclass(frozen=True)
class TimedChatBudget:
    """Explicit event, byte, and per-item processing limits for one build.

    ``max_bytes`` bounds the raw input actually processed; ``max_events`` bounds
    the retained normalized events; ``max_line_bytes`` rejects a single absurd
    line without aborting the build; ``max_text_chars`` and ``max_badges`` bound
    per-event fan-out so one crafted event cannot inflate the asset.
    """

    max_events: int = 4000
    max_bytes: int = 8_000_000
    max_line_bytes: int = 262_144
    max_text_chars: int = 500
    max_name_chars: int = 120
    max_badges: int = 6


@dataclass(frozen=True)
class TimedChatAuthor:
    """Safe author presentation: display name, stable channel id, badge kinds."""

    name: str
    channel_id: str | None = None
    badges: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimedChatEmote:
    """One custom emote span and its private provider image source."""

    start: int
    end: int
    label: str
    image_url: str


@dataclass(frozen=True)
class TimedChatEvent:
    """One normalized timed chat event with stable identity and a media offset.

    ``offset_ms`` is the validated media offset in milliseconds, or ``None`` when
    the provider gave no usable offset (the event still displays but is not
    seekable and does not drive playback synchronization).
    """

    id: str
    offset_ms: int | None
    kind: str
    text: str
    author: TimedChatAuthor | None = None
    moderation: str = MODERATION_VISIBLE
    amount: str | None = None
    # The display name of the *origin* channel when a provider relays a message
    # from another channel's chat (Twitch shared chat). ``None`` for an ordinary
    # message that originated in the channel being watched.
    source_channel: str | None = None
    emotes: tuple[TimedChatEmote, ...] = ()

    def _base_dict(self) -> dict[str, Any]:
        author = None
        if self.author is not None:
            author = {
                "name": self.author.name,
                "channel_id": self.author.channel_id,
                "badges": list(self.author.badges),
            }
        return {
            "id": self.id,
            "offset_ms": self.offset_ms,
            "kind": self.kind,
            "text": self.text,
            "author": author,
            "moderation": self.moderation,
            "amount": self.amount,
            "source_channel": self.source_channel,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        stored = self._base_dict()
        emotes = _valid_event_emotes(self)
        if emotes:
            stored["emotes"] = [
                {"start": emote.start, "end": emote.end, "label": emote.label, "image_url": emote.image_url}
                for emote in emotes
            ]
        return stored

    def to_public_dict(
        self,
        artwork_url_resolver: Callable[[str], str | None] | None = None,
    ) -> dict[str, Any]:
        public = self._base_dict()
        if artwork_url_resolver is None:
            return public
        mapped: list[dict[str, Any]] = []
        for emote in _valid_event_emotes(self):
            try:
                artwork_url = artwork_url_resolver(emote.image_url)
            except Exception:
                continue
            if not isinstance(artwork_url, str) or _PUBLIC_ARTWORK_URL.fullmatch(artwork_url) is None:
                continue
            mapped.append({
                "start": emote.start,
                "end": emote.end,
                "label": emote.label,
                "artwork_url": artwork_url,
            })
        if mapped:
            public["emotes"] = mapped
        return public


def _valid_event_emotes(event: TimedChatEvent) -> tuple[TimedChatEmote, ...]:
    if event.moderation != MODERATION_VISIBLE:
        return ()
    valid: list[TimedChatEmote] = []
    prior_end = 0
    for emote in event.emotes[:_MAX_EMOTES]:
        if not isinstance(emote, TimedChatEmote):
            continue
        if type(emote.start) is not int or type(emote.end) is not int:
            continue
        if not 0 <= emote.start < emote.end <= len(event.text) or emote.start < prior_end:
            continue
        if not isinstance(emote.label, str) or not 0 < len(emote.label) <= _MAX_EMOTE_LABEL_CHARS:
            continue
        if event.text[emote.start : emote.end] != emote.label or not _bounded_https_url(emote.image_url):
            continue
        valid.append(emote)
        prior_end = emote.end
    return tuple(valid)


def _bounded_https_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > _MAX_EMOTE_IMAGE_URL_CHARS:
        return False
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname is not None and parsed.username is None and parsed.password is None


@dataclass(frozen=True)
class TimedChatNormalizationResult:
    events: tuple[TimedChatEvent, ...]
    total_seen: int
    truncated: bool
    dropped_malformed: int
    bytes_processed: int
    outcome: str


# Offsets beyond this are treated as unusable (a completed live broadcast longer
# than ~48h is not something Lumina synchronizes against).
_MAX_OFFSET_MS = 48 * 60 * 60 * 1000

_MESSAGE_RENDERERS = {
    "liveChatTextMessageRenderer": EVENT_KIND_MESSAGE,
    "liveChatPaidMessageRenderer": EVENT_KIND_PAID_MESSAGE,
    "liveChatPaidStickerRenderer": EVENT_KIND_PAID_STICKER,
    "liveChatMembershipItemRenderer": EVENT_KIND_MEMBERSHIP,
    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": EVENT_KIND_MEMBERSHIP,
    "liveChatViewerEngagementMessageRenderer": EVENT_KIND_SYSTEM,
}


@dataclass
class _BuildState:
    budget: TimedChatBudget
    events: list[TimedChatEvent] = field(default_factory=list)
    index_by_id: dict[str, int] = field(default_factory=dict)
    ids_by_channel: dict[str, list[int]] = field(default_factory=dict)
    removed_channels: set[str] = field(default_factory=set)
    total_seen: int = 0
    dropped_malformed: int = 0
    bytes_processed: int = 0
    truncated: bool = False
    stop_reason: str | None = None  # None | "events" | "bytes"
    # Live-only: keep a rolling most-recent window instead of a bounded historical
    # prefix. The replay build leaves this False so its behavior is unchanged.
    rolling: bool = False


def normalize_youtube_replay_chat(
    lines: Iterable[bytes | str],
    *,
    budget: TimedChatBudget | None = None,
) -> TimedChatNormalizationResult:
    """Turn yt-dlp replay-chat JSONL into a bounded, ordered timed chat asset.

    Input is one JSON action per line (yt-dlp's ``.live_chat.json`` replay
    format). Malformed lines are skipped and counted, never fatal. Deletion and
    author-removal actions are applied to already-emitted events so a moderated
    message is represented as removed instead of being silently resurrected.
    """

    state = _BuildState(budget=budget or TimedChatBudget())
    for line in lines:
        raw = line.encode("utf-8") if isinstance(line, str) else line
        if state.bytes_processed + len(raw) > state.budget.max_bytes:
            state.truncated = True
            state.stop_reason = "bytes"
            break
        state.bytes_processed += len(raw)
        if len(raw) > state.budget.max_line_bytes:
            state.dropped_malformed += 1
            continue
        action = _load_line(raw)
        if action is None:
            state.dropped_malformed += 1
            continue
        _consume_action(state, action)

    events = tuple(state.events)
    outcome = _outcome(state, events)
    return TimedChatNormalizationResult(
        events=events,
        total_seen=state.total_seen,
        truncated=state.truncated,
        dropped_malformed=state.dropped_malformed,
        bytes_processed=state.bytes_processed,
        outcome=outcome,
    )


def new_build_state(budget: TimedChatBudget | None = None, *, rolling: bool = False) -> _BuildState:
    """A fresh normalization state, reused by the live-chat path across polls.

    The live path applies continuation actions incrementally to one persistent
    state so dedupe (overlapping continuations re-send items) and moderation
    (a deletion in a later page redacts an earlier message) behave exactly as
    they do for a completed-broadcast replay build.

    ``rolling`` is the one live-vs-replay difference: a live rail is forward-only
    and unbounded, so at capacity it evicts the oldest admitted event to keep the
    most-recent window, where a replay build (a bounded historical snapshot) keeps
    its bounded prefix and drops later additions.
    """

    return _BuildState(budget=budget or TimedChatBudget(), rolling=rolling)


def apply_live_action(state: _BuildState, action: Any, *, offset_ms: int | None = None) -> None:
    """Apply one live-chat continuation action to a persistent build state.

    Live continuation actions carry the same inner shapes as a replay envelope's
    inner actions (``addChatItemAction``/``markChatItemAsDeletedAction``/
    ``markChatItemsByAuthorAsDeletedAction``), so they route through the same
    single normalization authority. Live events carry no media offset.
    """

    if isinstance(action, TimedChatEvent):
        # A provider that normalizes its own events (Twitch IRC) admits them directly.
        state.total_seen += 1
        admit_event(state, action)
        return
    if not isinstance(action, Mapping):
        state.dropped_malformed += 1
        return
    state.total_seen += 1
    _dispatch_inner(state, action, offset_ms)


def _load_line(raw: bytes) -> Mapping[str, Any] | None:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _consume_action(state: _BuildState, action: Mapping[str, Any]) -> None:
    """Process one JSONL envelope, applying budgets per-action.

    Never full-stops: once the add budget is reached, new messages are dropped
    but deletion and author-removal actions keep applying to already-admitted
    events, so a moderator deletion after the cutoff still removes its target
    instead of leaving it silently visible in a truncated asset. Total input is
    bounded by the byte budget in the outer read loop.
    """

    envelope = action.get("replayChatItemAction")
    if not isinstance(envelope, Mapping):
        # Not a replay envelope (e.g. a bare live pseudo-action or unknown
        # top-level shape). Nothing to normalize, but it is not corrupt input.
        return
    offset_ms = _offset_ms(envelope.get("videoOffsetTimeMsec"), action.get("videoOffsetTimeMsec"))
    inner_actions = envelope.get("actions")
    if not isinstance(inner_actions, list):
        state.dropped_malformed += 1
        return
    for inner in inner_actions:
        if not isinstance(inner, Mapping):
            state.dropped_malformed += 1
            continue
        state.total_seen += 1
        _dispatch_inner(state, inner, offset_ms)


def _dispatch_inner(state: _BuildState, inner: Mapping[str, Any], offset_ms: int | None) -> None:
    if "addChatItemAction" in inner:
        if not state.rolling and len(state.events) >= state.budget.max_events:
            # Replay (non-rolling): the add budget is full, so drop the new
            # message but keep processing later moderation so admitted events are
            # never left un-moderated. The live path instead rolls the window in
            # _handle_add so the newest message is always admitted.
            state.truncated = True
            if state.stop_reason is None:
                state.stop_reason = "events"
            return
        _handle_add(state, inner["addChatItemAction"], offset_ms)
    elif "markChatItemAsDeletedAction" in inner:
        _handle_delete(state, inner["markChatItemAsDeletedAction"])
    elif "markChatItemsByAuthorAsDeletedAction" in inner:
        _handle_author_removed(state, inner["markChatItemsByAuthorAsDeletedAction"])
    # Ticker, banner, and other renderers carry no durable chat content we
    # represent; they are intentionally ignored rather than counted malformed.


def admit_event(state: _BuildState, event: TimedChatEvent) -> None:
    """Admit one already-normalized event into the shared build state.

    This is the single admission authority every provider routes through, so
    dedupe, already-known author removal, the rolling most-recent window (live)
    or bounded prefix (replay), and position tracking behave identically no
    matter which provider produced the event.

    - A duplicate (an id already retained) keeps the first copy and is dropped,
      so overlapping continuations or re-delivered frames are idempotent.
    - A message from an author already removed in this session arrives redacted,
      matching how a later author-removal redacts earlier messages.
    - Positions are recorded so a later deletion or author-removal can redact
      this event in place.
    """

    if event.id in state.index_by_id:
        return  # Overlapping/duplicated deliveries re-emit items; keep the first.
    channel_id = event.author.channel_id if event.author is not None else None
    if event.moderation == MODERATION_VISIBLE and channel_id is not None and channel_id in state.removed_channels:
        event = _redact_event(event, MODERATION_AUTHOR_REMOVED)
    if state.rolling and len(state.events) >= state.budget.max_events:
        # Live rail: roll the oldest admitted message off the window so the
        # newest is always shown. Only the live path enables this.
        _evict_oldest_event(state)
        state.truncated = True
    position = len(state.events)
    state.events.append(event)
    state.index_by_id[event.id] = position
    if channel_id is not None:
        state.ids_by_channel.setdefault(channel_id, []).append(position)


def _handle_add(state: _BuildState, add: Mapping[str, Any], offset_ms: int | None) -> None:
    if not isinstance(add, Mapping):
        state.dropped_malformed += 1
        return
    item = add.get("item")
    if not isinstance(item, Mapping):
        state.dropped_malformed += 1
        return
    renderer_key = next((key for key in _MESSAGE_RENDERERS if key in item), None)
    if renderer_key is None:
        return
    renderer = item.get(renderer_key)
    if not isinstance(renderer, Mapping):
        state.dropped_malformed += 1
        return
    event_id = _clean_id(renderer.get("id"))
    if event_id is None:
        # Without a stable provider id the event cannot be moderated or
        # deduplicated; synthesize a deterministic id from arrival order.
        event_id = f"_syn:{state.total_seen}"

    channel_id = _clean_channel(renderer.get("authorExternalChannelId"))
    author = _author(renderer, channel_id, state.budget)
    text, emotes = _display_content(renderer, state.budget)
    event = TimedChatEvent(
        id=event_id,
        offset_ms=offset_ms,
        kind=_MESSAGE_RENDERERS[renderer_key],
        text=text,
        author=author,
        moderation=MODERATION_VISIBLE,
        amount=_amount(renderer, state.budget),
        emotes=emotes,
    )
    admit_event(state, event)


def _evict_oldest_event(state: _BuildState) -> None:
    """Drop the oldest event from a live rolling window and re-base positions.

    The moderation model keys on list positions, which shift when the head is
    removed, so index_by_id and ids_by_channel are rebuilt for the survivors. A
    later moderation action targeting an evicted (rolled-off) message simply
    finds no target, which is correct: it is no longer in the window.
    """

    if not state.events:
        return
    state.events.pop(0)
    state.index_by_id = {event.id: index for index, event in enumerate(state.events)}
    channels: dict[str, list[int]] = {}
    for index, event in enumerate(state.events):
        channel_id = event.author.channel_id if event.author is not None else None
        if channel_id is not None:
            channels.setdefault(channel_id, []).append(index)
    state.ids_by_channel = channels


def delete_event(state: _BuildState, target_id: Any) -> None:
    """Redact a single retained message by its stable id (a moderator deletion).

    A target that rolled off the window or was never admitted simply finds no
    event, which is correct: there is nothing left to redact.
    """

    target = _clean_id(target_id)
    if target is None:
        return
    position = state.index_by_id.get(target)
    if position is None:
        return
    state.events[position] = _redact_event(state.events[position], MODERATION_DELETED)


def remove_author(state: _BuildState, channel_id: Any) -> None:
    """Redact every still-visible retained message from one author and remember it.

    The author is recorded so a message that arrives *after* the removal is
    admitted already redacted (see :func:`admit_event`).
    """

    channel_id = _clean_channel(channel_id)
    if channel_id is None:
        return
    state.removed_channels.add(channel_id)
    for position in state.ids_by_channel.get(channel_id, []):
        # A message already targeted for deletion keeps that more specific
        # state; author-level removal only claims still-visible messages.
        if state.events[position].moderation == MODERATION_VISIBLE:
            state.events[position] = _redact_event(state.events[position], MODERATION_AUTHOR_REMOVED)


def _handle_delete(state: _BuildState, deleted: Mapping[str, Any]) -> None:
    if not isinstance(deleted, Mapping):
        state.dropped_malformed += 1
        return
    delete_event(state, deleted.get("targetItemId"))


def _handle_author_removed(state: _BuildState, removed: Mapping[str, Any]) -> None:
    if not isinstance(removed, Mapping):
        state.dropped_malformed += 1
        return
    remove_author(state, removed.get("externalChannelId"))


def _redact_event(event: TimedChatEvent, moderation: str) -> TimedChatEvent:
    return replace(event, moderation=moderation, text="", emotes=())


def _outcome(state: _BuildState, events: tuple[TimedChatEvent, ...]) -> str:
    if events:
        if state.stop_reason == "bytes":
            return OUTCOME_OVERSIZED
        if state.truncated:
            return OUTCOME_PARTIAL
        return OUTCOME_READY
    if state.dropped_malformed > 0:
        return OUTCOME_MALFORMED
    return OUTCOME_EMPTY


def _offset_ms(primary: Any, fallback: Any) -> int | None:
    for candidate in (primary, fallback):
        if candidate is None:
            continue
        try:
            value = int(str(candidate).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= value <= _MAX_OFFSET_MS:
            return value
    return None


def _clean_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def _clean_channel(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or len(trimmed) > 128:
        return None
    return trimmed


def _author(renderer: Mapping[str, Any], channel_id: str | None, budget: TimedChatBudget) -> TimedChatAuthor | None:
    name = _simple_text(renderer.get("authorName"))
    if name:
        name = name[: budget.max_name_chars]
    badges = _badges(renderer.get("authorBadges"), budget)
    if not name and channel_id is None and not badges:
        return None
    return TimedChatAuthor(name=name or "", channel_id=channel_id, badges=badges)


def _badges(raw: Any, budget: TimedChatBudget) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    kinds: list[str] = []
    for entry in raw[: budget.max_badges]:
        if not isinstance(entry, Mapping):
            continue
        renderer = entry.get("liveChatAuthorBadgeRenderer")
        if not isinstance(renderer, Mapping):
            continue
        kind = _badge_kind(renderer)
        if kind and kind not in kinds:
            kinds.append(kind)
    return tuple(kinds)


def _badge_kind(renderer: Mapping[str, Any]) -> str | None:
    icon = renderer.get("icon")
    icon_type = icon.get("iconType") if isinstance(icon, Mapping) else None
    if isinstance(icon_type, str):
        lowered = icon_type.strip().lower()
        if lowered in {"owner"}:
            return "owner"
        if lowered in {"moderator"}:
            return "moderator"
        if lowered in {"verified"}:
            return "verified"
    # A custom badge thumbnail with no owner/moderator icon is a paid-membership
    # badge; represent it as the neutral "member" kind without leaking its image.
    if renderer.get("customThumbnail") is not None:
        return "member"
    return None


def _display_content(renderer: Mapping[str, Any], budget: TimedChatBudget) -> tuple[str, tuple[TimedChatEmote, ...]]:
    text, emotes = _runs_content(renderer.get("message"), budget.max_text_chars)
    if text:
        return text, emotes
    # Membership joins and some system events carry their text in a header.
    text, _ = _runs_content(renderer.get("headerSubtext"), budget.max_text_chars)
    if not text:
        text, _ = _runs_content(renderer.get("headerPrimaryText"), budget.max_text_chars)
    return text, ()


# Chat is untrusted text: drop C0/C1 controls (newlines included — a chat line
# is one line) and bidi overrides that could visually spoof neighbouring text.
_UNSAFE_CHARS = re.compile("[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_KEYCAP_EMOJI = re.compile(r"[#*0-9]\ufe0f?\u20e3")
_EMOJI_BASES = frozenset({
    0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x2328, 0x23CF, 0x24C2,
    0x2B50, 0x2B55, 0x3030, 0x303D, 0x3297, 0x3299,
})
_EMOJI_BASE_RANGES = (
    (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B), (0x23E9, 0x23F3),
    (0x23F8, 0x23FA), (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0),
    (0x25FB, 0x25FE), (0x2600, 0x26FF), (0x2700, 0x27BF), (0x2934, 0x2935),
    (0x2B05, 0x2B07), (0x2B1B, 0x2B1C), (0x1F000, 0x1FAFF),
)


def _runs_content(node: Any, max_text_chars: int) -> tuple[str, tuple[TimedChatEmote, ...]]:
    if not isinstance(node, Mapping):
        return "", ()
    if isinstance(node.get("simpleText"), str):
        return _UNSAFE_CHARS.sub("", node["simpleText"])[:max_text_chars], ()
    runs = node.get("runs")
    if not isinstance(runs, list):
        return "", ()
    parts: list[str] = []
    emotes: list[TimedChatEmote] = []
    length = 0
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        emoji: Mapping[str, Any] | None = None
        image_url: str | None = None
        if isinstance(run.get("text"), str):
            piece = run["text"]
        elif isinstance(run.get("emoji"), Mapping):
            emoji = run["emoji"]
            if emoji.get("isCustomEmoji") is True:
                image_url = _custom_emote_image_url(emoji)
            shortcut = _emoji_shortcut(emoji)
            piece = _custom_emote_label(emoji, shortcut) if image_url is not None else shortcut or ""
        else:
            continue
        piece = _UNSAFE_CHARS.sub("", piece)
        start = length
        parts.append(piece)
        length += len(piece)
        if (
            emoji is not None
            and image_url is not None
            and len(emotes) < _MAX_EMOTES
            and piece
        ):
            emotes.append(TimedChatEmote(start=start, end=length, label=piece, image_url=image_url))
    text = "".join(parts)[:max_text_chars]
    return text, tuple(emote for emote in emotes if emote.end <= len(text))


def _custom_emote_image_url(emoji: Mapping[str, Any]) -> str | None:
    image = emoji.get("image")
    thumbnails = image.get("thumbnails") if isinstance(image, Mapping) else None
    if not isinstance(thumbnails, list):
        return None
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for index, thumbnail in enumerate(thumbnails):
        if not isinstance(thumbnail, Mapping):
            continue
        url = thumbnail.get("url")
        if not _bounded_https_url(url):
            continue
        width, height = thumbnail.get("width"), thumbnail.get("height")
        if type(width) is int and type(height) is int and width > 0 and height > 0:
            score = (0, width * height, index)
        else:
            score = (1, index, 0)
        candidates.append((score, url))
    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def _custom_emote_label(emoji: Mapping[str, Any], shortcut: str | None) -> str:
    safe_shortcut = _UNSAFE_CHARS.sub("", shortcut or "")
    if safe_shortcut:
        return safe_shortcut[:_MAX_EMOTE_LABEL_CHARS]
    image = emoji.get("image")
    accessibility = image.get("accessibility") if isinstance(image, Mapping) else None
    data = accessibility.get("accessibilityData") if isinstance(accessibility, Mapping) else None
    label = data.get("label") if isinstance(data, Mapping) else None
    safe_label = _UNSAFE_CHARS.sub("", label) if isinstance(label, str) else ""
    return safe_label[:_MAX_EMOTE_LABEL_CHARS] or "[emote]"


def _runs_to_text(node: Any) -> str:
    return _UNSAFE_CHARS.sub("", _raw_runs_text(node))


def _raw_runs_text(node: Any) -> str:
    if isinstance(node, Mapping):
        if isinstance(node.get("simpleText"), str):
            return node["simpleText"]
        runs = node.get("runs")
        if isinstance(runs, list):
            parts: list[str] = []
            for run in runs:
                if not isinstance(run, Mapping):
                    continue
                if isinstance(run.get("text"), str):
                    parts.append(run["text"])
                    continue
                emoji = run.get("emoji")
                if isinstance(emoji, Mapping):
                    shortcut = _emoji_shortcut(emoji)
                    if shortcut:
                        parts.append(shortcut)
            return "".join(parts)
    return ""


def _unicode_emoji_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 32:
        return None
    if _KEYCAP_EMOJI.fullmatch(value):
        return value
    has_base = False
    for character in value:
        codepoint = ord(character)
        if codepoint in {0x200D, 0xFE0E, 0xFE0F} or 0x1F3FB <= codepoint <= 0x1F3FF or 0xE0020 <= codepoint <= 0xE007F:
            continue
        if codepoint in _EMOJI_BASES or any(start <= codepoint <= end for start, end in _EMOJI_BASE_RANGES):
            has_base = True
            continue
        return None
    return value if has_base else None


def _emoji_shortcut(emoji: Mapping[str, Any]) -> str | None:
    if not emoji.get("isCustomEmoji") and (emoji_id := _unicode_emoji_id(emoji.get("emojiId"))):
        return emoji_id
    shortcuts = emoji.get("shortcuts")
    if isinstance(shortcuts, list):
        for shortcut in shortcuts:
            if isinstance(shortcut, str) and shortcut:
                return shortcut
    return None


def _simple_text(node: Any) -> str:
    return _runs_to_text(node)


def _amount(renderer: Mapping[str, Any], budget: TimedChatBudget) -> str | None:
    amount = _simple_text(renderer.get("purchaseAmountText"))
    if not amount:
        return None
    return amount[: budget.max_name_chars]
