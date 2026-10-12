from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from functools import cache
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from time import monotonic
from typing import Any, get_args
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

import yt_dlp
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSettings
from app.schemas import AcquisitionErrorCategory, FormatResolutionState, FormatSelection, OutputProfile, PreviewResponse, SearchSource, SearchSourceError, YouTubeSearchResponse, YouTubeSearchResult
from app.services import provider_budget
from app.services.format_resolution import AcquisitionResult, FormatResolutionPlan, FormatResolutionPolicy
from app.services.hls_relay_support import derive_provider, public_provider
from app.services.kick_public import KICK_UNSUPPORTED_LINK, is_kick_host, kick_ref_from_info, parse_kick_url
from app.services.media_capabilities import derive_media_capabilities
from app.services.network_policy import (
    PUBLIC_SOURCE_POLICY_MESSAGE,
    PolicyYoutubeDL,
    PublicSourcePolicy,
    PublicSourcePolicyError,
)
from app.services.output_policy import OutputPolicy


class DownloadCancelled(Exception):
    """Raised when the user cancels an active download."""


# Raw ceiling on the replay-chat subtitle bytes read into memory. It is set
# above the normalization byte budget so normalization stays the single
# authority on truncation ("oversized"); this only guards process memory.
REPLAY_CHAT_MAX_SOURCE_BYTES = 24_000_000


@dataclass
class ReplayChatFetch:
    """Result of one coordinated replay-chat download.

    ``status`` is "available" when a ``live_chat`` track was produced,
    "unavailable" when the source exposed none, and "failed" on a provider or
    policy error. ``lines`` are raw JSONL bytes; nothing here has been
    normalized yet, and no continuation tokens are retained beyond the file.
    """

    status: str
    lines: list[bytes] = field(default_factory=list)


SEARCH_CACHE_TTL_SECONDS = 180.0
SEARCH_COALESCED_WAIT_TIMEOUT_SECONDS = 20.0
PREVIEW_CACHE_TTL_SECONDS = 120.0  # live and upcoming items: their state changes minute to minute
PREVIEW_VIDEO_CACHE_TTL_SECONDS = 600.0  # a finished YouTube video: every member's format choice shares one extraction
RESOLVE_CACHE_TTL_SECONDS = 120.0
SIGNED_URL_MARGIN_SECONDS = 60.0
PREVIEW_COALESCED_WAIT_TIMEOUT_SECONDS = 30.0
SEARCH_CACHE_MAX_ENTRIES = 96
PREVIEW_CACHE_MAX_ENTRIES = 64
# The encoded form of YouTube's "Live" search feature filter, as it appears
# in a browser address bar after choosing Filters -> Live.
_YOUTUBE_LIVE_FILTER_SP = "EgJAAQ%253D%253D"
_YOUTUBE_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_SEARCH_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_PREVIEW_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_RESOLVE_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


@dataclass
class _SearchFlight:
    complete: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


_SEARCH_CACHE_LOCK = threading.RLock()
_SEARCH_INFLIGHT: dict[tuple[str, str], _SearchFlight] = {}
_PREVIEW_INFLIGHT: dict[tuple[Any, ...], _SearchFlight] = {}
_RESOLVE_INFLIGHT: dict[tuple[Any, ...], _SearchFlight] = {}
_GOOGLEVIDEO_EXPIRE = re.compile(r"[/?&]expire[=/](\d+)")
_YOUTUBE_LISTING_PATHS = ("/@", "/channel/", "/c/", "/user/", "/playlist")
_YOUTUBE_VIDEO_REQUESTS = 2


def _youtube_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"youtu.be", "youtube.com"} or host.endswith(".youtube.com")


def _youtube_video(url: str) -> bool:
    """One YouTube video (watch, shorts, live, youtu.be), not a listing, search or watch-with-playlist."""
    parsed = urlparse(url)
    return (
        _youtube_host(url)
        and parsed.path != "/results"
        and not parsed.path.startswith(_YOUTUBE_LISTING_PATHS)
        and "list" not in parse_qs(parsed.query)
    )


def youtube_request_cost(url: str, options: dict[str, Any]) -> int | None:
    """Requests one yt-dlp call asks of YouTube, or None when it does not go to YouTube (googlevideo media never counts)."""
    search = re.match(r"ytsearch(\d*):", url)
    if search:
        return provider_budget.youtube_search_cost(int(search.group(1) or 1))
    if not _youtube_host(url):
        return None
    path = urlparse(url).path
    if path == "/results":
        return provider_budget.youtube_search_cost(options.get("playlistend") or provider_budget.YOUTUBE_RESULTS_PER_REQUEST)
    return provider_budget.PROBE_REQUESTS if path.startswith(_YOUTUBE_LISTING_PATHS) else _YOUTUBE_VIDEO_REQUESTS


@contextmanager
def youtube_budget(url: str, options: dict[str, Any]) -> Iterator[None]:
    """The household's one YouTube meter: every extraction, search and listing spends here at the caller's priority
    (provider_budget.priority). A rate-limit answer to anyone pauses background work for everyone; clicks still go."""
    cost = youtube_request_cost(url, options)
    if cost is None:
        yield
        return
    budget = provider_budget.youtube
    provider_budget.spend(budget, cost)
    try:
        yield
    except BaseException as exc:
        # A media download's own 403 (googlevideo) is not a rate limit; only metadata-only calls pause.
        if options.get("skip_download"):
            budget.failed(exc)
        raise
    budget.succeeded()


def signed_url_ttl(info: dict[str, Any], ttl: float) -> float:
    """``ttl`` cut so a cached extraction is never served within a minute of its signed googlevideo URLs expiring."""
    urls = [info.get("url"), info.get("manifest_url")]
    for candidate in info.get("formats") or ():
        if isinstance(candidate, dict):
            urls += [candidate.get("url"), candidate.get("manifest_url")]
    expiries = [int(found) for url in urls if isinstance(url, str) for found in _GOOGLEVIDEO_EXPIRE.findall(url)]
    if expiries:
        ttl = min(ttl, min(expiries) - time.time() - SIGNED_URL_MARGIN_SECONDS)
    return ttl


class SearchBusyError(RuntimeError):
    """A duplicate provider search exceeded its bounded coalescing wait."""


class PublicOnlyOptionsError(RuntimeError):
    """A constructed yt-dlp options dict left the closed public-only allowlist."""


# The 1.0 MVP cut closed yt-dlp option construction to a public-only allowlist.
# Every options dict that reaches the extractor must consist solely of these
# keys: no cookie, credential, netrc, client-cert, exec, plugin, or
# user-executable option is ever constructed by any Lumina code path. Anything
# outside this set fails loud at the extraction seam.
PUBLIC_OPTION_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ignoreconfig",
        "paths",
        "download_archive",
        "noprogress",
        "restrictfilenames",
        "continuedl",
        "windowsfilenames",
        "proxy",
        "external_downloader",
        "hls_prefer_native",
        "skip_unavailable_fragments",
        "ffmpeg_location",
        "js_runtimes",
        "skip_download",
        "quiet",
        "extract_flat",
        "playlistend",
        "format",
        "format_sort",
        "progress_hooks",
        "postprocessor_hooks",
        "merge_output_format",
        "outtmpl",
        "writethumbnail",
        "embedthumbnail",
        "addmetadata",
        "writesubtitles",
        "writeautomaticsub",
        "postprocessors",
        "subtitleslangs",
        "writeinfojson",
        # Only ever FAST_YOUTUBE_EXTRACTOR_ARGS (checked below): extractor arguments can also carry PO tokens or
        # visitor data, which stay out.
        "extractor_args",
        # Only ever POSTPROCESSOR_INPUT_ARGS (checked below).
        "postprocessor_args",
    }
)
# Preview skips the web client's player JS: solving its challenge in node cost 1-2 s per YouTube click, for one
# 360p muxed format the player never picks (the visionos client carries the whole ladder without a challenge).
FAST_YOUTUBE_EXTRACTOR_ARGS: dict[str, Any] = {"youtube": {"player_skip": ["js"]}}
# Every input of every yt-dlp ffmpeg postprocessor (merge, metadata, fixups, audio extraction, thumbnails) is a
# downloaded, attacker-shaped file: only the file protocol and real media/image demuxers, so a playlist, concat or
# SDP file can never steer ffmpeg to other members' files or the LAN (media_probe.LOCAL_INPUT_ARGS, plus images).
_POSTPROCESSOR_INPUT = [
    "-protocol_whitelist", "file",
    "-format_whitelist", "mov,matroska,webm,mpegts,avi,asf,mpeg,flv,mp3,aac,flac,ogg,wav,image2,png_pipe,jpeg_pipe,webp_pipe",
]
POSTPROCESSOR_INPUT_ARGS: dict[str, list[str]] = {
    f"{pp.pp_key().lower()}+ffmpeg_i": _POSTPROCESSOR_INPUT
    for pp in vars(yt_dlp.postprocessor).values()
    if isinstance(pp, type) and issubclass(pp, yt_dlp.postprocessor.FFmpegPostProcessor)
}


def assert_public_only_options(options: dict[str, Any]) -> None:
    blocked = sorted(set(options) - PUBLIC_OPTION_ALLOWLIST)
    if "extractor_args" in options and options["extractor_args"] != FAST_YOUTUBE_EXTRACTOR_ARGS:
        blocked.append("extractor_args")
    if "postprocessor_args" in options and options["postprocessor_args"] != POSTPROCESSOR_INPUT_ARGS:
        blocked.append("postprocessor_args")
    if blocked:
        raise PublicOnlyOptionsError(
            "yt-dlp options outside the closed public-only allowlist: " + ", ".join(blocked)
        )


# Ordered: a bot/rate challenge is not a content restriction, so it wins.
_RESTRICTION_MARKERS: tuple[tuple[AcquisitionErrorCategory, tuple[str, ...]], ...] = (
    ("rate_limited", ("limiting anonymous requests", "http error 429", "too many requests", "not a bot", "rate limit")),
    ("sign_in_required", (
        "restricted to signed-in viewers", "sign in to confirm your age", "age-restricted", "inappropriate for some users",
        "members-only", "join this channel", "private video", "login required", "login_required",
        "requires authentication", "authentication is required", "only available for registered users",
        "only available to subscribers", "only available for subscribers", "subscriber-only",
        "without being logged-in", "sign in if you've been granted access",
    )),
    ("region_blocked", ("in this server's region", "available in your country", "geo restriction", "geo-restrict", "not available from your location")),
    ("removed", (
        "has been removed", "has been terminated", "no longer available", "video unavailable",
        "does not exist", "has been deleted",
    )),
    ("unsupported", ("public media on this page", "unsupported url", "no video formats found", "no media found")),
)
_RESTRICTION_MESSAGES: dict[str, str] = {
    "rate_limited": "The provider is limiting anonymous requests from this server right now. Try the link again later.",
    "sign_in_required": (
        "This source is restricted to signed-in viewers (age, membership, subscription or private). "
        "Lumina only plays and saves public media, so open the original link to view it with the provider."
    ),
    "region_blocked": "The provider does not offer this source in this server's region, so Lumina cannot play or save it.",
    "removed": "This source was removed or is no longer available from the provider.",
    "unsupported": "Lumina could not find public media on this page. Open the original link to view it on the site.",
}
# yt-dlp's operator advice (cookies, logins, bug reports, self-update) never reaches members.
_OPERATOR_ADVICE = re.compile(
    r"\s*(?:(?:use\s+|pass\s+)?--(?:cookies|username|netrc)|;?\s*please report this issue|confirm you are on the latest)",
    re.IGNORECASE,
)


@cache
def _node_version(node_path: str) -> str | None:
    """Cached: spawning ``node --version`` per diagnostics request is wasted work."""
    try:
        result = subprocess.run([node_path, "--version"], capture_output=True, text=True, timeout=2, check=False)
        return (result.stdout or result.stderr).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


DEEP_SEARCH_LIMIT = 120


class YtDlpService:
    def __init__(
        self,
        db: Session,
        *,
        ydl_factory: Any | None = None,
        network_policy: PublicSourcePolicy | None = None,
    ):
        self.db = db
        self.network_policy = network_policy or PublicSourcePolicy()
        factory = ydl_factory or (lambda options: PolicyYoutubeDL(options, policy=self.network_policy))

        def guarded_factory(options: dict[str, Any]) -> Any:
            # Every extraction path (search, live search, preview, replay chat)
            # goes through this one seam, so no path can smuggle a credential option.
            assert_public_only_options(options)
            return factory(options)

        self._ydl_factory = guarded_factory

    def ensure_app_settings(self) -> AppSettings:
        """Create or repair the durable settings row; only startup and the settings write own this.

        Request paths must use `get_app_settings`: this method flushes the
        ffmpeg-path correction, and a flush at the head of an options build
        would leave an unadmitted write transaction open across extraction.
        """
        record = self.db.get(AppSettings, 1)
        detected_ffmpeg = shutil.which("ffmpeg")
        if record is None:
            record = self._default_app_settings(detected_ffmpeg)
            self.db.add(record)
            self.db.flush()
        elif (
            detected_ffmpeg
            and record.ffmpeg_path != detected_ffmpeg
            # A deliberately-set path that still points at an executable is
            # authoritative (issue #102); only an unusable one is repaired.
            and not self._ffmpeg_path_is_usable(record.ffmpeg_path)
        ):
            record.ffmpeg_path = detected_ffmpeg
            self.db.flush()
        return record

    def get_app_settings(self) -> AppSettings:
        """Read the settings without writing: request paths never flush corrections."""
        record = self.db.get(AppSettings, 1)
        if record is not None:
            return record
        # Startup creates the durable row; until then (fresh database) serve
        # the same defaults transiently so read paths stay write-free.
        return self._default_app_settings(shutil.which("ffmpeg"))

    @staticmethod
    def _ffmpeg_path_is_usable(stored: str | None) -> bool:
        """Whether a stored ffmpeg path still points at something yt-dlp can run.

        yt-dlp's ``ffmpeg_location`` accepts either the binary itself or a
        directory containing the ffmpeg binaries, so both are honored here.
        """
        if not stored:
            return False
        candidate = Path(stored)
        if candidate.is_dir():
            return any(
                (candidate / name).is_file() and os.access(candidate / name, os.X_OK)
                for name in ("ffmpeg", "ffmpeg.exe")
            )
        return candidate.is_file() and os.access(candidate, os.X_OK)

    @classmethod
    def resolve_ffmpeg_path(cls, stored: str | None) -> str | None:
        """Prefer a deliberately-set, still-usable stored path over which() (issue #102)."""
        if cls._ffmpeg_path_is_usable(stored):
            return stored
        return shutil.which("ffmpeg")

    @staticmethod
    def _default_app_settings(detected_ffmpeg: str | None) -> AppSettings:
        return AppSettings(
            id=1,
            temp_root=str(settings.temp_root),
            archive_path=str(settings.archive_path),
            concurrency=1,
            max_active_jobs_per_user=25,
            min_free_disk_mb=2048,
            ffmpeg_path=detected_ffmpeg,
            yt_dlp_defaults={"ignoreconfig": True, "noprogress": True},
            ui_prefs={"density": "comfortable"},
        )

    def preview(
        self,
        source_url: str,
        lazy_playlist: bool = True,
        *,
        format_selection: FormatSelection | None = None,
        entries_limit: int | None = None,
    ) -> PreviewResponse:
        if is_kick_host(source_url):
            # Only the exact public Kick forms resolve, always via their canonical address.
            kick_link = parse_kick_url(source_url)
            if kick_link is None:
                raise yt_dlp.utils.DownloadError(KICK_UNSUPPORTED_LINK)
            source_url = kick_link.url
        source_url = self.validate_source_url(source_url)
        selection = format_selection or FormatSelection()
        format_plan = FormatResolutionPolicy.resolve(selection)
        # The response never serves more than 100 entries, so extraction never needs
        # to materialize more than that either — a flat channel-tab listing otherwise
        # pages through the entire upstream catalog just to have it truncated here.
        # `entries_limit or 100` treats a falsy caller value (None or 0) as "no limit
        # requested", then the bound is clamped into [1, 100].
        entries_bound = max(1, min(entries_limit or 100, 100))
        # A single YouTube video's extraction does not depend on the member's format choice: yt-dlp re-selects from the
        # cached format list locally, so every member and every prefetch of it share one extraction (one per source at a
        # time: a click while a prefetch of the same source resolves joins it).
        shared = _youtube_video(source_url)
        selector = format_plan.state.requested_selector
        cache_key = (source_url, lazy_playlist, None if shared else selector, entries_bound)

        def ttl(payload: dict[str, Any]) -> float:
            raw = payload["raw"]
            live = raw.get("is_live") or raw.get("live_status") in {"is_live", "is_upcoming", "post_live"}
            return signed_url_ttl(raw, PREVIEW_VIDEO_CACHE_TTL_SECONDS if shared and not live else PREVIEW_CACHE_TTL_SECONDS)

        payload = self._single_flight(
            _PREVIEW_CACHE,
            _PREVIEW_INFLIGHT,
            cache_key,
            lambda: self._extract_preview(source_url, lazy_playlist, selection, format_plan, entries_bound).model_dump(mode="python"),
            ttl=ttl,
            max_entries=PREVIEW_CACHE_MAX_ENTRIES,
            wait_seconds=PREVIEW_COALESCED_WAIT_TIMEOUT_SECONDS,
        )
        if payload["format_resolution"]["requested_selector"] == selector:
            return PreviewResponse.model_validate(payload)
        options = self._build_acquisition_options(selection, format_plan=format_plan)
        options.update({"skip_download": True, "quiet": True})
        # The top-level copies of the previously selected format(s) go; the format list stays, and yt-dlp selects anew.
        stale = {key for entry in payload["raw"].get("formats") or () if isinstance(entry, dict) for key in entry}
        stale |= {"requested_formats", "requested_downloads", "format_resolution"}
        raw = {key: value for key, value in payload["raw"].items() if key not in stale}
        with self._ydl_factory(options) as ydl:  # format selection only: no network
            info = ydl.sanitize_info(ydl.process_ie_result(raw, download=False))
        return self._preview_response(info, selection, format_plan, entries_bound)

    @staticmethod
    def _single_flight(
        store: dict[tuple[Any, ...], tuple[float, dict[str, Any]]],
        inflight: dict[tuple[Any, ...], _SearchFlight],
        key: tuple[Any, ...],
        produce: Callable[[], dict[str, Any]],
        *,
        ttl: Callable[[dict[str, Any]], float],
        max_entries: int,
        wait_seconds: float,
    ) -> dict[str, Any]:
        """A cached ``produce()`` with one producer per key at a time; a follower that times out produces itself, and one
        whose leader was refused by the budget (a prefetch, say) tries at its own priority."""
        flight = None
        while flight is None:
            with _SEARCH_CACHE_LOCK:
                cached = YtDlpService._cache_get(store, key, ttl_seconds=0)
                if cached is not None:
                    return cached
                leading = inflight.get(key)
                if leading is None:
                    flight = inflight[key] = _SearchFlight()
                    break
            if not leading.complete.wait(timeout=wait_seconds):
                break
            if leading.error is not None and not isinstance(leading.error, provider_budget.BudgetExhausted):
                raise leading.error
        try:
            payload = produce()
        except BaseException as exc:
            if flight is not None:
                with _SEARCH_CACHE_LOCK:
                    flight.error = exc
                    inflight.pop(key, None)
                    flight.complete.set()
            raise
        with _SEARCH_CACHE_LOCK:
            seconds = ttl(payload)
            if seconds > 0:
                YtDlpService._cache_set(store, key, payload, max_entries=max_entries, ttl_seconds=seconds)
            if flight is not None:
                inflight.pop(key, None)
                flight.complete.set()
        return payload

    def _extract_preview(
        self,
        source_url: str,
        lazy_playlist: bool,
        selection: FormatSelection,
        format_plan: Any,
        entries_bound: int,
    ) -> PreviewResponse:
        options = self._build_acquisition_options(selection, format_plan=format_plan)
        options.update(
            {
                "skip_download": True,
                "quiet": True,
                "extract_flat": "in_playlist" if lazy_playlist else False,
                "playlistend": entries_bound,
            }
        )
        return self._preview_response(self._extract_fast(source_url, options), selection, format_plan, entries_bound)

    def _preview_response(
        self, sanitized: dict[str, Any], selection: FormatSelection, format_plan: Any, entries_bound: int
    ) -> PreviewResponse:
        format_plan = FormatResolutionPolicy.resolve(selection, info=sanitized, previous=format_plan.state)
        sanitized["format_resolution"] = format_plan.state.model_dump(mode="json")
        entries = []
        if isinstance(sanitized, dict) and sanitized.get("_type") == "playlist":
            for entry in sanitized.get("entries", [])[:entries_bound]:
                if not entry:
                    continue
                entries.append(
                    {
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "duration": entry.get("duration"),
                        "thumbnail": self.resolve_search_thumbnail(entry),
                        "webpage_url": entry.get("webpage_url") or entry.get("url"),
                        "uploader": (
                            entry.get("uploader")
                            or entry.get("channel")
                            or sanitized.get("uploader")
                            or sanitized.get("channel")
                        ),
                        "availability": entry.get("availability"),
                        "published_at": self._normalize_publication_time(entry),
                        "media_kind": self._normalize_media_kind(entry),
                        # The channel page link and the caption's view count on follow feeds.
                        "channel_id": entry.get("channel_id") or entry.get("uploader_id"),
                        "channel_url": entry.get("channel_url") or entry.get("uploader_url"),
                        "view_count": entry.get("view_count") if isinstance(entry.get("view_count"), int) else None,
                        "capabilities": derive_media_capabilities({
                            "extractor": entry.get("extractor") or sanitized.get("extractor"),
                            "extractor_key": entry.get("extractor_key") or sanitized.get("extractor_key"),
                            **entry,
                        }),
                    }
                )
            kind = "playlist"
        else:
            entries = []
            kind = "video"
        kick_ref = kick_ref_from_info(sanitized)
        if kick_ref is not None:
            # Kick metadata names the channel slug only; expose its canonical followable address.
            sanitized["channel_url"] = f"https://kick.com/{kick_ref.channel}"
        return PreviewResponse(
            kind=kind,
            title=sanitized.get("title"),
            extractor=sanitized.get("extractor"),
            extractor_key=sanitized.get("extractor_key"),
            # Kick's ?clip= and www. forms converge on one canonical address.
            webpage_url=kick_ref.url if kick_ref else sanitized.get("webpage_url"),
            availability=sanitized.get("availability"),
            published_at=self._normalize_publication_time(sanitized),
            media_kind=self._normalize_media_kind(sanitized),
            capabilities=derive_media_capabilities(sanitized),
            format_resolution=format_plan.state,
            entries=entries,
            raw=sanitized,
        )

    @staticmethod
    def _normalize_publication_time(info: dict[str, Any]) -> datetime | None:
        timestamp = info.get("timestamp") or info.get("release_timestamp")
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and timestamp > 0:
            return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
        for key in ("upload_date", "release_date"):
            value = info.get(key)
            if not isinstance(value, str):
                continue
            try:
                return datetime.strptime(value, "%Y%m%d")
            except ValueError:
                continue
        return None

    @staticmethod
    def _normalize_media_kind(info: dict[str, Any]) -> str | None:
        vcodec = info.get("vcodec")
        video_ext = info.get("video_ext")
        has_video = (
            info.get("has_video") is True
            or (isinstance(vcodec, str) and vcodec.lower() != "none")
            or (isinstance(video_ext, str) and video_ext.lower() != "none")
            or isinstance(info.get("width"), (int, float))
            or isinstance(info.get("height"), (int, float))
        )
        if has_video:
            return "video"
        acodec = info.get("acodec")
        audio_ext = info.get("audio_ext")
        has_audio = (
            info.get("has_audio") is True
            or (isinstance(acodec, str) and acodec.lower() != "none")
            or (isinstance(audio_ext, str) and audio_ext.lower() != "none")
        )
        if has_audio:
            return "audio"
        return None

    def resolve_remote_playback(self, source_url: str) -> dict[str, Any]:
        """Resolve fresh private transport metadata for the streaming registry."""

        source_url = self.validate_source_url(source_url)

        def extract() -> dict[str, Any]:
            options = self.build_base_options()
            options.pop("download_archive", None)
            options.update({"skip_download": True, "quiet": True, "extract_flat": False})
            return self._extract_fast(source_url, options)

        # Members watching the same stream (and each one's expiry re-resolve) share one extraction for a couple of
        # minutes, never past a minute before its signed URLs expire. The result carries no member state.
        return self._single_flight(
            _RESOLVE_CACHE,
            _RESOLVE_INFLIGHT,
            (source_url,),
            extract,
            ttl=lambda info: signed_url_ttl(info, RESOLVE_CACHE_TTL_SECONDS),
            max_entries=PREVIEW_CACHE_MAX_ENTRIES,
            wait_seconds=PREVIEW_COALESCED_WAIT_TIMEOUT_SECONDS,
        )

    def _extract_fast(self, source_url: str, options: dict[str, Any]) -> dict[str, Any]:
        """Extract for playback without YouTube's player JS; a refused fast extraction (visionos blocked, say)
        retries in full, so a YouTube client change costs one extra request, never playback."""
        if not _youtube_host(source_url):
            return self._extract(source_url, options, download=False)
        # visionos answers 403 now and then, leaving only the web client's 360p muxed format.
        # A refusal can also come back as success with only that 360p format: then the full extraction decides, so
        # skipping the JS never starts a video lower than it would have (owner rule: top quality first).
        # One fast try: in production a refused one (403) was followed by a second refusal, adding ~3 s and requests.
        for _attempt in range(1):
            try:
                info = self._extract(source_url, {**options, "extractor_args": FAST_YOUTUBE_EXTRACTOR_ARGS}, download=False)
            except yt_dlp.utils.DownloadError:
                continue
            formats = info.get("formats")  # a playlist or channel has none: nothing to judge
            if not formats or max((f.get("height") or 0 for f in formats), default=0) > 360:
                return info
        return self._extract(source_url, options, download=False)

    def download_replay_chat(
        self,
        source_url: str,
        *,
        max_source_bytes: int = REPLAY_CHAT_MAX_SOURCE_BYTES,
    ) -> ReplayChatFetch:
        """Download only the completed-live ``live_chat`` replay track.

        Reuses the coordinated PolicyYoutubeDL seam, so the subtitle fetch flows
        through the same public-only network policy as every other extraction —
        the policy is not weakened for chat. The media itself is never
        downloaded (``skip_download``); only the replay-chat subtitle is written
        to a private temp directory, read back under a byte ceiling, and
        discarded. No archive is consulted or written.
        """

        try:
            source_url = self.validate_source_url(source_url)
        except PublicSourcePolicyError:
            return ReplayChatFetch(status="failed")
        options = self.build_base_options()
        options.pop("download_archive", None)
        with tempfile.TemporaryDirectory(prefix="lumina-replay-chat-") as work_dir:
            options.update(
                {
                    "skip_download": True,
                    "quiet": True,
                    "noprogress": True,
                    "extract_flat": False,
                    "writesubtitles": True,
                    "writeautomaticsub": False,
                    "writeinfojson": False,
                    "subtitleslangs": ["live_chat"],
                    "outtmpl": {"default": str(Path(work_dir) / "%(id)s.%(ext)s")},
                    "paths": {"home": work_dir, "temp": work_dir},
                }
            )
            try:
                with self._ydl_factory(options) as ydl, youtube_budget(source_url, options):
                    ydl.extract_info(source_url, download=True)
            except yt_dlp.utils.DownloadError:
                return ReplayChatFetch(status="failed")
            chat_files = sorted(Path(work_dir).glob("*.live_chat.json"))
            if not chat_files:
                return ReplayChatFetch(status="unavailable")
            return ReplayChatFetch(
                status="available",
                lines=self._read_bounded_lines(chat_files[0], max_source_bytes),
            )

    @staticmethod
    def _read_bounded_lines(path: Path, max_bytes: int) -> list[bytes]:
        data = bytearray()
        with path.open("rb") as handle:
            while len(data) < max_bytes:
                chunk = handle.read(min(65536, max_bytes - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
        hit_ceiling = len(data) >= max_bytes
        lines = data.split(b"\n")
        if hit_ceiling and lines:
            # The final line may be truncated by the ceiling; drop it so the
            # normalizer only ever sees whole JSONL records.
            lines = lines[:-1]
        return [line for line in lines if line.strip()]

    def download(
        self,
        source_url: str,
        *,
        format_selection: FormatSelection,
        output_profile: OutputProfile,
        owner_user_id: str,
        output_root: Path,
        progress_hooks: list,
        postprocessor_hooks: list,
        previous_resolution: FormatResolutionState | dict[str, Any] | None = None,
    ) -> AcquisitionResult:
        """Download into output_root (a staging directory); publication happens afterwards."""
        format_plan = FormatResolutionPolicy.resolve(format_selection, previous=previous_resolution)
        options = self.build_download_options(
            format_selection=format_selection,
            output_profile=output_profile,
            owner_user_id=owner_user_id,
            output_root=output_root,
            progress_hooks=progress_hooks,
            postprocessor_hooks=postprocessor_hooks,
            format_plan=format_plan,
        )
        sanitized = self._extract(
            self.validate_source_url(source_url),
            options,
            download=True,
        )
        resolved = FormatResolutionPolicy.resolve(format_selection, info=sanitized, previous=format_plan.state)
        return AcquisitionResult(info=sanitized, format_resolution=resolved.state)

    def youtube_search(self, query: str, limit: int = 10, *, cache: bool = True, deep: bool = False) -> YouTubeSearchResponse:
        # deep: Popular's background refresh only (ytsearch pages ~20 results, so 40 is two result pages, 120 six).
        items = self._search_provider("youtube", query, limit, cache=cache, cap=DEEP_SEARCH_LIMIT if deep else 24)
        return YouTubeSearchResponse(query=query.strip(), items=items)

    def youtube_live_search(self, query: str, limit: int = 8, *, max_limit: int = 24) -> YouTubeSearchResponse:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return YouTubeSearchResponse(query=normalized_query, items=[])
        bounded_limit = max(1, min(limit, max_limit))
        options = self.build_base_options()
        options.pop("download_archive", None)
        options.update(
            {
                "skip_download": True,
                "quiet": True,
                "extract_flat": True,
                "playlistend": bounded_limit,
            }
        )
        url = (
            "https://www.youtube.com/results?search_query="
            + quote_plus(normalized_query)
            + "&sp="
            + _YOUTUBE_LIVE_FILTER_SP
        )
        with self._ydl_factory(options) as ydl, youtube_budget(url, options):
            info = ydl.extract_info(url, download=False)
            sanitized = ydl.sanitize_info(info)
        entries = sanitized.get("entries") if isinstance(sanitized, dict) else []
        items = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            # Every result behind the live filter is live; flat entries may omit
            # the signal, and capabilities must classify them as live. Flat
            # entries also carry viewer counts under concurrent_view_count,
            # which normalize_search_result does not read directly.
            live_entry = {
                **entry,
                "live_status": entry.get("live_status") or "is_live",
                "view_count": entry.get("concurrent_view_count") or entry.get("view_count"),
            }
            items.append(self.normalize_search_result(live_entry, "youtube"))
        return YouTubeSearchResponse(query=normalized_query, items=items[:bounded_limit])

    def probe_live_source(self, channel_url: str) -> YouTubeSearchResult | None:
        """Full-extract a channel's live endpoint; None unless it is live right now.

        A provider-confirmed "not live" answer is None; any other failure (an
        outage, drift) raises so the caller keeps the last-known status.
        """
        options = self.build_base_options()
        options.pop("download_archive", None)
        options.update({"skip_download": True, "quiet": True})
        try:
            sanitized = self._extract_with_options(channel_url, options, download=False)
        except (yt_dlp.utils.UserNotLive, yt_dlp.utils.DownloadError) as exc:
            cause = exc.exc_info[1] if isinstance(exc, yt_dlp.utils.DownloadError) and exc.exc_info else exc
            if isinstance(cause, yt_dlp.utils.UserNotLive):
                return None
            raise
        derived = derive_provider(sanitized)
        provider = derived if derived in get_args(SearchSource) else "youtube"
        result = self.normalize_search_result(sanitized, provider)
        if result.capabilities is None or result.capabilities.lifecycle != "live":
            return None
        return result

    def extract_channel_listing(self, url: str, limit: int) -> dict[str, Any]:
        """One flat extraction of a YouTube channel root or tab: the extractor and request
        type a follow refresh already uses. DownloadErrors stay raw so the caller can classify them."""
        options = self.build_base_options()
        options.pop("download_archive", None)
        options.update({"skip_download": True, "quiet": True, "extract_flat": "in_playlist", "playlistend": max(1, min(limit, 121))})
        return self._extract_with_options(self.validate_source_url(url), options, download=False)

    def source_search(self, query: str, limit: int = 10) -> YouTubeSearchResponse:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Enter a search query.")

        bounded_limit = self.normalize_search_limit(limit)
        groups: list[list[YouTubeSearchResult]] = []
        errors: list[SearchSourceError] = []
        first_failure: BaseException | None = None
        for provider in ("soundcloud", "youtube"):
            # One provider failing keeps the other's results and reports an
            # honest, retryable per-source error instead of failing the search.
            try:
                groups.append(self._search_provider(provider, normalized_query, min(bounded_limit, max(8, bounded_limit * 2 // 3))))
            except (SearchBusyError, yt_dlp.utils.DownloadError, PublicSourcePolicyError, OSError) as exc:
                first_failure = first_failure or exc
                errors.append(SearchSourceError(
                    source=provider,
                    message=self.explain_download_error(str(exc)),
                    retryable=not isinstance(exc, PublicSourcePolicyError),
                ))
        if first_failure is not None and not groups:
            raise first_failure
        items = self.interleave_search_results(*groups)[:bounded_limit]
        return YouTubeSearchResponse(query=normalized_query, items=items, errors=errors)

    def _search_provider(self, provider: str, query: str, limit: int = 10, *, cache: bool = True, cap: int = 24) -> list[YouTubeSearchResult]:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            if provider == "soundcloud":
                raise ValueError("Enter a SoundCloud search query.")
            raise ValueError("Enter a YouTube search query.")

        bounded_limit = self.normalize_search_limit(limit, cap)
        cache_key = (provider, normalized_query.casefold())
        flight = None  # cache=False (history-derived refresher queries) never touches the shared cache or in-flight map
        while cache:
            with _SEARCH_CACHE_LOCK:
                cached = self._cache_get(_SEARCH_CACHE, cache_key, ttl_seconds=SEARCH_CACHE_TTL_SECONDS)
                cached_limit = cached.get("limit") if cached is not None else None
                if isinstance(cached_limit, int) and cached_limit >= bounded_limit:
                    return [
                        YouTubeSearchResult.model_validate(entry)
                        for entry in cached.get("items", [])[:bounded_limit]
                        if isinstance(entry, dict)
                    ]
                flight = _SEARCH_INFLIGHT.get(cache_key)
                if flight is None:
                    flight = _SearchFlight()
                    _SEARCH_INFLIGHT[cache_key] = flight
                    break
            if not flight.complete.wait(timeout=SEARCH_COALESCED_WAIT_TIMEOUT_SECONDS):
                raise SearchBusyError("This search is still being prepared. Try again shortly.")
            if flight.error is not None and not isinstance(flight.error, provider_budget.BudgetExhausted):
                raise flight.error  # a refused background search leaves this caller to try at its own priority

        try:
            options = self.build_base_options()
            options.pop("download_archive", None)
            options.update(
                {
                    "skip_download": True,
                    "quiet": True,
                    "extract_flat": True,
                }
            )
            search_query = self.build_search_query(provider, normalized_query, bounded_limit)
            with self._ydl_factory(options) as ydl, youtube_budget(search_query, options):
                info = ydl.extract_info(search_query, download=False)
                sanitized = ydl.sanitize_info(info)
            entries = sanitized.get("entries") if isinstance(sanitized, dict) else []
            items = []
            seen: set[str] = set()
            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                item = self.normalize_search_result(entry, provider)
                # Dedupe on the stable provider id (or canonical URL), never the title.
                identity = item.id or item.webpage_url
                if identity and identity in seen:
                    continue
                if identity:
                    seen.add(identity)
                items.append(item)
        except BaseException as exc:
            # Lazy playlist paging lets transport errors escape unwrapped; treat them as extraction failures.
            error = yt_dlp.utils.DownloadError(str(exc)) if isinstance(exc, yt_dlp.networking.exceptions.RequestError) else exc
            if flight is not None:
                with _SEARCH_CACHE_LOCK:
                    flight.error = error
                    _SEARCH_INFLIGHT.pop(cache_key, None)
                    flight.complete.set()
            if error is not exc:
                raise error from exc
            raise
        if flight is not None:
            with _SEARCH_CACHE_LOCK:
                self._cache_set(
                    _SEARCH_CACHE,
                    cache_key,
                    {"limit": bounded_limit, "items": [item.model_dump(mode="python") for item in items]},
                    max_entries=SEARCH_CACHE_MAX_ENTRIES,
                )
                _SEARCH_INFLIGHT.pop(cache_key, None)
                flight.complete.set()
        return items

    @staticmethod
    def normalize_source_url(source_url: str) -> str:
        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or parsed.path != "/watch":
            return source_url
        query = parse_qs(parsed.query, keep_blank_values=True)
        video_id = query.get("v", [None])[0]
        playlist_id = query.get("list", [None])[0]
        start_radio = query.get("start_radio", [None])[0]
        if not video_id or not playlist_id or not playlist_id.startswith("RD") or start_radio not in {"1", "true"}:
            return source_url
        normalized_query = urlencode({"v": video_id})
        return urlunparse(parsed._replace(query=normalized_query, fragment=""))

    def validate_source_url(self, source_url: str) -> str:
        return self.network_policy.validate_url(self.normalize_source_url(source_url))

    @staticmethod
    def normalize_search_limit(limit: int | None, cap: int = 24) -> int:
        if limit is None:
            return 10
        return max(1, min(limit, cap))

    @staticmethod
    def build_search_query(provider: str, query: str, limit: int) -> str:
        search_prefix = {
            "youtube": "ytsearch",
            "soundcloud": "scsearch",
        }.get(provider)
        if not search_prefix:
            raise ValueError(f"Unsupported search provider: {provider}")
        return f"{search_prefix}{limit}:{query}"

    @classmethod
    def normalize_search_result(cls, entry: dict[str, Any], provider: str) -> YouTubeSearchResult:
        timestamp = entry.get("timestamp") or entry.get("release_timestamp")
        published_at = None
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            published_at = datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
        thumbnail_url = cls.resolve_search_thumbnail(entry)
        webpage_url = entry.get("webpage_url") if isinstance(entry.get("webpage_url"), str) else None
        if not webpage_url:
            raw_url = entry.get("url")
            if isinstance(raw_url, str) and raw_url.startswith("http"):
                webpage_url = raw_url
        if not webpage_url and provider == "youtube":
            video_id = entry.get("id")
            if isinstance(video_id, str) and video_id:
                webpage_url = f"https://www.youtube.com/watch?v={video_id}"
        kind = cls._youtube_result_kind(entry, webpage_url) if provider == "youtube" else None
        video_id = entry.get("id")
        if kind in {"video", "short", "live"} and isinstance(video_id, str) and _YOUTUBE_VIDEO_ID.fullmatch(video_id):
            webpage_url = f"https://www.youtube.com/watch?v={video_id}"
        channel_url = entry.get("uploader_url") or entry.get("channel_url")
        # The immutable channel id (UC…) outranks the renamable @handle.
        channel_id = entry.get("channel_id") or entry.get("uploader_id")
        return YouTubeSearchResult(
            id=entry.get("id"),
            title=entry.get("title") or entry.get("track"),
            uploader=entry.get("uploader") or entry.get("channel") or entry.get("artist"),
            uploader_url=channel_url if isinstance(channel_url, str) else None,
            uploader_id=channel_id if isinstance(channel_id, str) else None,
            duration=int(entry["duration"]) if isinstance(entry.get("duration"), (int, float)) else None,
            thumbnail=thumbnail_url,
            webpage_url=webpage_url,
            view_count=int(entry["view_count"]) if isinstance(entry.get("view_count"), (int, float)) else None,
            availability=entry.get("availability"),
            published_at=published_at,
            source=provider,
            source_label=public_provider(provider).label,
            kind=kind,
            capabilities=derive_media_capabilities({
                **entry,
                "extractor_key": entry.get("extractor_key") or provider,
            }),
        )

    @staticmethod
    def _youtube_result_kind(entry: dict[str, Any], webpage_url: str | None) -> str:
        parsed = urlparse(webpage_url or "")
        first = next((segment for segment in parsed.path.split("/") if segment), "")
        if first == "shorts":
            return "short"
        if first == "playlist" or entry.get("_type") == "playlist":
            return "playlist"
        if first in {"channel", "c", "user"} or first.startswith("@"):
            return "channel"
        if entry.get("live_status") == "is_live" or entry.get("is_live") is True:
            return "live"
        return "video"

    @staticmethod
    def resolve_search_thumbnail(entry: dict[str, Any]) -> str | None:
        thumbnails = entry.get("thumbnails") if isinstance(entry.get("thumbnails"), list) else []
        thumbnail_candidates: list[tuple[int, str]] = []
        for thumbnail in thumbnails:
            if isinstance(thumbnail, dict) and isinstance(thumbnail.get("url"), str):
                normalized = YtDlpService._normalize_youtube_thumbnail_variant(thumbnail["url"])
                if normalized:
                    width = int(thumbnail.get("width") or 0)
                    height = int(thumbnail.get("height") or 0)
                    thumbnail_candidates.append((width * height, normalized))
        if thumbnail_candidates:
            return max(thumbnail_candidates, key=lambda candidate: candidate[0])[1]
        raw_thumbnail = entry.get("thumbnail")
        if isinstance(raw_thumbnail, str):
            return YtDlpService._normalize_youtube_thumbnail_variant(raw_thumbnail)
        return None

    @staticmethod
    def resolve_channel_avatar(info: dict[str, Any]) -> str | None:
        """Choose a square creator image without mistaking a channel banner for it."""

        thumbnails = info.get("thumbnails") if isinstance(info.get("thumbnails"), list) else []
        candidates: list[tuple[bool, float, str]] = []
        for thumbnail in thumbnails:
            if not isinstance(thumbnail, dict) or not isinstance(thumbnail.get("url"), str):
                continue
            width = thumbnail.get("width")
            height = thumbnail.get("height")
            if (
                isinstance(width, bool)
                or isinstance(height, bool)
                or not isinstance(width, (int, float))
                or not isinstance(height, (int, float))
                or not math.isfinite(width)
                or not math.isfinite(height)
                or width <= 0
                or height <= 0
                or max(width, height) / min(width, height) > 1.1
            ):
                continue
            normalized = YtDlpService._normalize_youtube_thumbnail_variant(thumbnail["url"])
            if not normalized:
                continue
            thumbnail_id = str(thumbnail.get("id") or "").lower()
            candidates.append(("avatar" in thumbnail_id or "profile" in thumbnail_id, width * height, normalized))
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]

    @staticmethod
    def interleave_search_results(*groups: list[YouTubeSearchResult]) -> list[YouTubeSearchResult]:
        normalized_groups = [list(group) for group in groups if group]
        results: list[YouTubeSearchResult] = []
        while any(normalized_groups):
            for group in normalized_groups:
                if group:
                    results.append(group.pop(0))
        return results

    @staticmethod
    def resolve_channel_banner(info: dict[str, Any]) -> str | None:
        """The widest thumbnail wider than 3:1: a channel's banner, never its avatar."""
        thumbnails = info.get("thumbnails") if isinstance(info.get("thumbnails"), list) else []
        candidates: list[tuple[float, str]] = []
        for thumbnail in thumbnails:
            if not isinstance(thumbnail, dict) or not isinstance(thumbnail.get("url"), str):
                continue
            width, height = thumbnail.get("width"), thumbnail.get("height")
            if (
                isinstance(width, bool) or isinstance(height, bool)
                or not isinstance(width, (int, float)) or not isinstance(height, (int, float))
                or not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0
                or width / height <= 3
            ):
                continue
            normalized = YtDlpService._normalize_youtube_thumbnail_variant(thumbnail["url"])
            if normalized:
                candidates.append((width, normalized))
        return max(candidates)[1] if candidates else None

    @staticmethod
    def _normalize_youtube_thumbnail_variant(url: str) -> str:
        normalized = url.strip()
        if "i.ytimg.com" not in normalized:
            return normalized
        return (
            normalized.replace("/maxresdefault.", "/sddefault.")
            .replace("/hq720.", "/sddefault.")
            .replace("/hq2.", "/sddefault.")
            .replace("/mq2.", "/mqdefault.")
            .replace("/default_live.", "/default.")
        )

    def build_base_options(self) -> dict[str, Any]:
        app_settings = self.get_app_settings()
        options: dict[str, Any] = {
            "ignoreconfig": True,
            "paths": {"temp": app_settings.temp_root},
            "download_archive": app_settings.archive_path,
            "noprogress": True,
            "restrictfilenames": True,
            "continuedl": True,
            "windowsfilenames": True,
            "proxy": "",
            "external_downloader": {"default": "native"},
            "hls_prefer_native": True,
            # A fragmented acquisition (the guarded Twitch VOD HLS path, or DASH)
            # must fail closed rather than silently drop a fragment the guarded
            # transport refused: a manifest that points a segment, key, or map at
            # private space aborts the whole job instead of publishing a truncated
            # file. yt-dlp defaults this to skipping unavailable fragments.
            "skip_unavailable_fragments": False,
            "postprocessor_args": POSTPROCESSOR_INPUT_ARGS,
        }
        # Startup owns the durable ffmpeg-path correction; this build stays
        # write-free. A deliberately-set stored path that is still usable is
        # authoritative (issue #102); only an unusable one falls back to the
        # detected binary, absorbing mid-run drift without writing.
        ffmpeg_path = self.resolve_ffmpeg_path(app_settings.ffmpeg_path)
        if ffmpeg_path:
            options["ffmpeg_location"] = ffmpeg_path
        node_path = shutil.which("node")
        if node_path:
            options["js_runtimes"] = {"node": {"path": node_path}}
        # The 1.0 MVP cut closes construction to the public-only allowlist: no
        # credential, cookie, netrc, client-cert, exec, or plugin option is ever
        # merged in here, and the extraction seam re-checks the final dict.
        return options

    @staticmethod
    def runtime_diagnostics() -> dict[str, Any]:
        node_path = shutil.which("node")
        node_version = _node_version(node_path) if node_path else None
        return {
            "yt_dlp_version": getattr(getattr(yt_dlp, "version", None), "__version__", None),
            "node_path": node_path,
            "node_version": node_version,
            "js_runtime_available": bool(node_path),
            "yt_dlp_ejs_available": find_spec("yt_dlp_ejs") is not None,
        }

    @staticmethod
    def _cache_get(
        store: dict[tuple[Any, ...], tuple[float, dict[str, Any]]],
        key: tuple[Any, ...],
        *,
        ttl_seconds: float,
    ) -> dict[str, Any] | None:
        record = store.get(key)
        if record is None:
            return None
        expires_at, payload = record
        if expires_at <= monotonic():
            store.pop(key, None)
            return None
        return deepcopy(payload)

    @staticmethod
    def _cache_set(
        store: dict[tuple[Any, ...], tuple[float, dict[str, Any]]],
        key: tuple[Any, ...],
        payload: dict[str, Any],
        *,
        max_entries: int,
        ttl_seconds: float | None = None,
    ) -> None:
        expires_at = monotonic() + (ttl_seconds if ttl_seconds is not None else SEARCH_CACHE_TTL_SECONDS)
        store[key] = (expires_at, deepcopy(payload))
        if len(store) <= max_entries:
            return
        expired_keys = [candidate for candidate, (candidate_expiry, _) in store.items() if candidate_expiry <= monotonic()]
        for expired_key in expired_keys:
            store.pop(expired_key, None)
        while len(store) > max_entries:
            oldest_key = min(store.items(), key=lambda entry: entry[1][0])[0]
            store.pop(oldest_key, None)

    def build_download_options(
        self,
        format_selection: FormatSelection,
        output_profile: OutputProfile,
        owner_user_id: str,
        output_root: Path,
        progress_hooks: list,
        postprocessor_hooks: list,
        format_plan: FormatResolutionPlan | None = None,
    ) -> dict[str, Any]:
        options = self._build_acquisition_options(format_selection, format_plan=format_plan)
        options["progress_hooks"] = progress_hooks
        options["postprocessor_hooks"] = postprocessor_hooks
        if not format_selection.extract_audio:
            options["merge_output_format"] = self.resolve_output_container(format_selection)
        options["outtmpl"] = {"default": str(self.resolve_output_path(output_profile, owner_user_id, output_root))}
        options["writethumbnail"] = format_selection.embed_thumbnail
        options["embedthumbnail"] = format_selection.embed_thumbnail
        options["addmetadata"] = format_selection.embed_metadata
        options["writesubtitles"] = format_selection.subtitles
        options["writeautomaticsub"] = format_selection.subtitles
        postprocessors = []
        if format_selection.extract_audio:
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": format_selection.audio_format or "mp3",
                    "preferredquality": "0",
                }
            )
        if postprocessors:
            options["postprocessors"] = postprocessors
        return options

    def _build_acquisition_options(
        self,
        format_selection: FormatSelection,
        *,
        format_plan: FormatResolutionPlan | None = None,
    ) -> dict[str, Any]:
        options = self.build_base_options()
        # Lumina manages dedupe itself, and users may intentionally inspect or download
        # a source into a different folder, format, or quality variant.
        options.pop("download_archive", None)
        options["format"] = (format_plan or FormatResolutionPolicy.resolve(format_selection)).selector
        # At equal resolution prefer plain HTTP(S): YouTube ranks some HLS-only "Premium" formats first,
        # and the download gate refuses YouTube HLS, which would fail the whole job.
        options["format_sort"] = ["res", "proto"]
        return options

    def _extract(
        self,
        source_url: str,
        options: dict[str, Any],
        *,
        download: bool,
    ) -> dict[str, Any]:
        try:
            return self._extract_with_options(source_url, options, download=download)
        except yt_dlp.utils.DownloadError as exc:
            explained = self.explain_download_error(str(exc))
            if explained == str(exc):
                raise
            raise yt_dlp.utils.DownloadError(explained) from exc
    def _extract_with_options(self, source_url: str, options: dict[str, Any], *, download: bool) -> dict[str, Any]:
        with self._ydl_factory(options) as ydl, youtube_budget(source_url, options):
            info = ydl.extract_info(source_url, download=download)
            sanitized = ydl.sanitize_info(info)
        if not isinstance(sanitized, dict):
            operation = "Download" if download else "Preview"
            raise yt_dlp.utils.DownloadError(f"{operation} returned no metadata")
        return sanitized

    @staticmethod
    def classify_download_error(message: str) -> AcquisitionErrorCategory | None:
        lowered = message.strip().lower()
        if (
            "only images are available for download" in lowered
            or "requested format is not available" in lowered
            or "could not resolve any playable media formats" in lowered
            or "storyboard images" in lowered
        ):
            return "format_unavailable"
        for category, markers in _RESTRICTION_MARKERS:
            if any(marker in lowered for marker in markers):
                return category
        return None

    @staticmethod
    def explain_download_error(message: str) -> str:
        normalized = message.strip()
        lowered = normalized.lower()
        if PUBLIC_SOURCE_POLICY_MESSAGE.lower() in lowered:
            return PUBLIC_SOURCE_POLICY_MESSAGE
        category = YtDlpService.classify_download_error(normalized)
        if category == "format_unavailable":
            return (
                "YouTube did not expose any playable audio/video formats for this item in the current session. "
                "Lumina could only see storyboard images, so this is not a simple format-preset issue. "
                "YouTube may be challenging anonymous extraction for this video right now; test the exact "
                "link again later. If it still fails, this video is being blocked by YouTube's current "
                "challenge or token flow in this environment."
            )
        if category in _RESTRICTION_MESSAGES:
            return _RESTRICTION_MESSAGES[category]
        if "n challenge solving failed" in lowered:
            return (
                "YouTube blocked format extraction with a client challenge before Lumina could see playable media formats. "
                "Lumina extracts anonymously, so yt-dlp could not unlock the stream list for this video; "
                "test the exact link again later."
            )
        # Never relay yt-dlp's sign-in advice: Lumina has no cookie or account path.
        return _OPERATOR_ADVICE.split(normalized, 1)[0].rstrip(" .;:") or normalized

    @staticmethod
    def resolve_output_container(selection: FormatSelection) -> str:
        if selection.preset == "best_editable":
            return "mp4"  # an editable H.264 stream in mkv/webm defeats the purpose
        return selection.output_container if selection.output_container in {"mp4", "webm", "mkv"} else "mp4"

    @staticmethod
    def resolve_output_path(output_profile: OutputProfile, owner_user_id: str, output_root: Path) -> Path:
        return OutputPolicy(output_root, owner_user_id).resolve(output_profile)
