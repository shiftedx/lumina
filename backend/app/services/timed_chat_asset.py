"""Durable, member-scoped orchestration of timed chat asset builds.

This is the seam that turns a deliberate member Load action into a bounded,
resumable build. It records a build lifecycle the way a Download job does — a
non-terminal ``building`` claim with ``build_id``/``started_at``, then a
terminal status carrying the normalized events — rather than inventing a second
job framework. A crashed build leaves a stale ``building`` row that the next
load safely replaces; a superseded build's late finalize is discarded by a
compare-and-set on ``build_id``.

Chat is member-owned: every read and write is scoped to the requesting member
and the canonical source identity, so one member's session can never read
another's asset.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.orm import Session

from app.models import ChatReplayAsset, User
from app.schemas import (
    ChatReplayAssetResponse,
    TimedChatAuthorResponse,
    TimedChatEventResponse,
)
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.remote_streaming import _canonical_remote_source_url
from app.services.timed_chat import TimedChatBudget, TimedChatNormalizationResult
from app.services.yt_dlp_service import ReplayChatFetch


# A build older than this with no terminal state is treated as interrupted and
# replaceable; a younger building row is assumed in-flight so a second load does
# not launch duplicate work.
STALE_BUILD_SECONDS = 300

_TERMINAL_STATUSES = {"ready", "empty", "partial", "oversized", "unavailable", "malformed", "failed"}

# The replay-chat BUILD path downloads a provider's historical replay chat via
# yt-dlp; only YouTube completed-live exposes a live_chat replay track Lumina
# imports. FORWARD-ONLY HARD INVARIANT (issue #100): a non-YouTube source is
# NEVER backfilled from provider history — notably a Twitch VOD, whose "rechat"
# yt-dlp can also fetch. Its only chat is a durably-captured forward-only asset
# (from a live recording), so a build is claimed only for a YouTube identity;
# every other identity returns its captured asset if one exists, else an honest
# ``unavailable`` — never a provider import.
_REPLAY_BUILD_PROVIDERS = frozenset({"youtube"})


def _identity_provider(canonical: str) -> str:
    prefix, _, _value = canonical.partition(":")
    return prefix.strip().casefold()


def _identity_from_source_url(source_url: str) -> str | None:
    """Mirror the frontend's source identity derivation from a URL.

    YouTube watch/short URLs resolve to ``youtube:<id>``; any other http(s) URL
    resolves to the same canonical ``url:<...>`` form the streaming seam uses.
    Returns ``None`` when no identity can be derived so the caller stays
    permissive rather than falsely rejecting an unusual-but-valid URL.
    """

    candidate = source_url.strip()
    if not candidate:
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    video_id: str | None = None
    if host == "youtu.be":
        video_id = parts.path.strip("/").split("/")[0] or None
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        video_id = parse_qs(parts.query).get("v", [None])[0]
    if video_id:
        return f"youtube:{video_id}"
    if parts.scheme and parts.hostname:
        return f"url:{_canonical_remote_source_url(candidate)}"
    return None


def _url_host_is_youtube(source_url: str) -> bool:
    """Whether ``source_url`` is served from a YouTube-family host.

    This is the load-bearing closure of the no-backfill invariant (issue #100).
    ``begin_build`` gates the replay BUILD on the path-supplied identity prefix,
    but that prefix is caller-controlled: a crafted ``youtube:<id>`` identity
    paired with a non-YouTube ``source_url`` would otherwise reach the yt-dlp
    replay-chat download. The gate cannot instead require the URL-DERIVED
    identity to be ``youtube:`` — legitimate YouTube ``/live/``, ``/embed/``,
    and ``/shorts/`` forms only derive ``url:<...>`` from the raw URL (see
    :func:`_identity_from_source_url`). What those legitimate cases share, and a
    Twitch (or any other) backfill attempt never does, is the URL host. So the
    build is claimed only for an affirmative YouTube-family host; every other
    host — Twitch, or anything unrecognized — fails closed, no longer relying on
    an upstream extractor lacking a replay-chat track. Uses the same host
    normalization as :func:`_identity_from_source_url`.
    """

    try:
        parts = urlsplit(source_url.strip())
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


@dataclass
class BuildTicket:
    """The outcome of claiming (or declining to claim) a build.

    ``build_id`` is ``None`` when no build should run — either a fresh terminal
    asset already exists or another build is in flight — and ``response`` is the
    asset to return as-is. When ``build_id`` is set, the caller must run the
    download + normalization and hand the result to :meth:`store_result`.
    """

    canonical: str
    build_id: str | None
    response: ChatReplayAssetResponse


class TimedChatAssetService:
    def __init__(self, db: Session, *, budget: TimedChatBudget | None = None):
        self.db = db
        self.budget = budget or TimedChatBudget()

    def get(self, source_identity: str, user: User) -> ChatReplayAsset | None:
        canonical = RemotePlaybackProgressService.canonical_source_identity(source_identity)
        return self._record(canonical, user)

    def begin_build(
        self,
        source_identity: str,
        source_url: str,
        user: User,
        *,
        refresh: bool = False,
    ) -> BuildTicket:
        """Claim a build for (member, source), or decline when one is unneeded.

        Must be called inside the request's write transaction: on a claim it
        upserts the ``building`` row so the lifecycle is durable and observable.
        """

        canonical = RemotePlaybackProgressService.canonical_source_identity(source_identity)
        if _identity_provider(canonical) not in _REPLAY_BUILD_PROVIDERS or not _url_host_is_youtube(source_url):
            # Forward-only: never import historical provider replay chat for a
            # non-YouTube source. Serve the durably-captured forward-only asset if
            # one exists, otherwise an honest unavailable — never a backfill build.
            #
            # The build is claimed only when BOTH the path identity is a build
            # provider AND the source_url is affirmatively a YouTube-family host
            # (see _url_host_is_youtube). The path identity is caller-supplied, so
            # the host check is what fails closed against a crafted youtube:<id>
            # identity carrying a Twitch (or any non-YouTube) source_url — the
            # load-bearing closure of issue #100's no-backfill invariant.
            existing = self._record(canonical, user)
            if existing is not None:
                return BuildTicket(canonical=canonical, build_id=None, response=self.serialize(existing))
            return BuildTicket(canonical=canonical, build_id=None, response=self._unavailable_response(canonical))
        self._require_matching_source(canonical, source_url)
        existing = self._record(canonical, user)
        now = datetime.now(UTC).replace(tzinfo=None)

        if existing is not None and not refresh:
            if existing.status in _TERMINAL_STATUSES:
                return BuildTicket(canonical=canonical, build_id=None, response=self.serialize(existing))
            if existing.status == "building" and not self._is_stale(existing, now):
                # A fresh build is in flight; return the loading placeholder.
                return BuildTicket(canonical=canonical, build_id=None, response=self.serialize(existing))

        build_id = str(uuid.uuid4())
        asset = existing
        if asset is None:
            asset = ChatReplayAsset(
                id=str(uuid.uuid4()),
                user_id=user.id,
                source_identity=canonical,
                source_identity_key=self._key(canonical),
                created_at=now,
            )
            self.db.add(asset)
        asset.source_url = source_url
        asset.status = "building"
        asset.build_id = build_id
        asset.error = None
        asset.started_at = now
        asset.finished_at = None
        asset.updated_at = now
        self.db.flush()
        return BuildTicket(canonical=canonical, build_id=build_id, response=self.serialize(asset))

    def store_result(
        self,
        canonical: str,
        source_url: str,
        user: User,
        build_id: str,
        fetch: ReplayChatFetch,
        result: TimedChatNormalizationResult,
    ) -> ChatReplayAsset:
        """Persist the terminal build state, unless a newer build superseded it.

        Must be called inside the request's write transaction. The compare-and-
        set on ``build_id`` makes a late finalize from an interrupted or
        superseded build a no-op instead of clobbering the current asset.
        """

        asset = self._record(canonical, user)
        if asset is None or asset.build_id != build_id:
            # A newer build claimed the row (or it was cleared); discard silently.
            if asset is not None:
                return asset
            raise RuntimeError("Timed chat asset row disappeared during build")

        now = datetime.now(UTC).replace(tzinfo=None)
        status = self._terminal_status(fetch, result)
        events = list(result.events) if fetch.status == "available" else []
        asset.source_url = source_url
        asset.status = status
        asset.event_count = len(events)
        asset.total_seen = result.total_seen if fetch.status == "available" else 0
        asset.bytes_processed = result.bytes_processed if fetch.status == "available" else 0
        asset.dropped_malformed = result.dropped_malformed if fetch.status == "available" else 0
        asset.truncated = bool(result.truncated) if fetch.status == "available" else False
        asset.events_json = [event.to_public_dict() for event in events]
        asset.error = None
        asset.finished_at = now
        asset.updated_at = now
        self.db.flush()
        return asset

    @staticmethod
    def _unavailable_response(canonical: str) -> ChatReplayAssetResponse:
        return ChatReplayAssetResponse(source_identity=canonical, status="unavailable")

    def serialize(self, asset: ChatReplayAsset) -> ChatReplayAssetResponse:
        return ChatReplayAssetResponse(
            source_identity=asset.source_identity,
            status=asset.status,  # type: ignore[arg-type]
            event_count=asset.event_count,
            truncated=asset.truncated,
            dropped_malformed=asset.dropped_malformed,
            events=[self._event(entry) for entry in (asset.events_json or []) if isinstance(entry, dict)],
            updated_at=asset.updated_at,
        )

    @staticmethod
    def _event(entry: dict) -> TimedChatEventResponse:
        author_entry = entry.get("author")
        author = None
        if isinstance(author_entry, dict):
            author = TimedChatAuthorResponse(
                name=str(author_entry.get("name") or ""),
                channel_id=author_entry.get("channel_id"),
                badges=[str(badge) for badge in author_entry.get("badges") or []],
            )
        return TimedChatEventResponse(
            id=str(entry.get("id")),
            offset_ms=entry.get("offset_ms"),
            kind=entry.get("kind", "message"),
            text=str(entry.get("text") or ""),
            author=author,
            moderation=entry.get("moderation", "visible"),
            amount=entry.get("amount"),
        )

    @staticmethod
    def _terminal_status(fetch: ReplayChatFetch, result: TimedChatNormalizationResult) -> str:
        if fetch.status == "unavailable":
            return "unavailable"
        if fetch.status == "failed":
            return "failed"
        return result.outcome

    @staticmethod
    def _is_stale(asset: ChatReplayAsset, now: datetime) -> bool:
        started = asset.started_at or asset.updated_at or asset.created_at
        if started is None:
            return True
        return now - started > timedelta(seconds=STALE_BUILD_SECONDS)

    @staticmethod
    def _require_matching_source(canonical: str, source_url: str) -> None:
        """Reject a source URL that does not resolve to the requested identity.

        The client derives the source identity from the same URL, so a mismatch
        means a malformed or crafted request trying to key a chat asset under an
        identity the URL does not belong to. Derivations that cannot be resolved
        (non-http, unusual URLs) are left permissive: the download step still
        enforces the public network policy.
        """

        derived = _identity_from_source_url(source_url)
        if derived is None:
            return
        try:
            derived_canonical = RemotePlaybackProgressService.canonical_source_identity(derived)
        except ValueError:
            return
        # Non-canonical YouTube forms (/live/, /embed/, /shorts/) key
        # youtube:<id> in the client via its extractor-id fallback while the URL
        # alone only yields url:<...>. A differing scheme prefix is that benign
        # case, so stay permissive; only a same-scheme disagreement is the
        # crafted mismatch the check defends against.
        if derived_canonical.split(":", 1)[0] != canonical.split(":", 1)[0]:
            return
        if derived_canonical != canonical:
            raise ValueError("Source URL does not match the requested chat source identity")

    @staticmethod
    def _key(canonical: str) -> str:
        return RemotePlaybackProgressService.source_identity_key(canonical)

    def _record(self, canonical: str, user: User) -> ChatReplayAsset | None:
        return (
            self.db.query(ChatReplayAsset)
            .filter(
                ChatReplayAsset.user_id == user.id,
                ChatReplayAsset.source_identity_key == self._key(canonical),
            )
            .first()
        )
