from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yt_dlp
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.http_compression import ResponseCompressionMiddleware
from app.services import network_policy, public_address, two_factor
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.config import APP_VERSION, settings
from app.routers import admin_invites, admin_ai, admin_backups, admin_activity, member_access as member_access_routes, admin_diagnostics, admin_library_automation, admin_models, admin_imports, admin_overview, admin_recording_retention, admin_storage, admin_tasks, discovery, idea_graph, local_playback, media_tools, profile_export, provenance, search_history, summaries, watch_queue
from app.routers import enrichment as enrichment_routes
from app.routers import admin_media_server, connected_apps, jellyfin_auth
from app.routers import two_factor as two_factor_routes
from app.routers import jellyfin_history
from app.services import title_metadata  # TMDB metadata worker
from app.services import member_access, provider_budget
from app.services import screen_time, streaming_gate
from app.services.streaming_gate import require_streaming
from app.routers import admin_metadata  # TMDB metadata routes
from app.routers import title_images as title_images_routes  # 2.1.0 title artwork routes
from app.routers import metadata_editor as metadata_editor_routes
from app.services import metadata_history, tmdb
from app.routers import jellyfin, titles
from app.routers import requests_catalog, showcase as showcase_routes
from app.routers import library_sections  # library gallery sections
from app.routers import requests as requests_routes  # 2.6.0 Requests (BE-B)
from app.services.requests.catalog import warm_loop as catalog_warm_loop
from app.services.requests.sync import request_sync_loop
from app.db import SessionLocal, get_db, init_db, session_scope
from app.persistence import write_transaction
from app.events import EventBus, sse_stream
from app.http_boundaries import (
    SECURITY_HEADERS,
    FailedRequestLogMiddleware,
    OriginCheckMiddleware,
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
)
from sqlalchemy import func, select

from app import device_ring
from app.device_ring import append_token, apply_ring_cookie, drop_token, read_ring
from app.models import (
    AcquisitionBatch,
    AppSession,
    AcquisitionBatchEntry,
    AcquisitionJobOutput,
    DownloadJob,
    HouseholdCollection,
    HouseholdCollectionMembership,
    LibraryItem,
    LiveRecording,
    PlaybackProgress,
    User,
    utcnow,
)
from app.schemas import (
    AcquisitionBatchCreateRequest,
    DeviceForgetRequest,
    DeviceMember,
    SessionSwitchRequest,
    AcquisitionBatchEntryResponse,
    AcquisitionBatchResponse,
    AcquisitionErrorDetail,
    AutomationRunResponse,
    AppSettingsResponse,
    AppSettingsUpdateRequest,
    BootstrapAdminCreateRequest,
    BootstrapStatusResponse,
    ChatReplayAssetResponse,
    ChatReplayLoadRequest,
    LiveRecordingChatOutput,
    LiveRecordingCreateRequest,
    LiveRecordingKeepRequest,
    LiveRecordingMediaOutput,
    LiveRecordingPageResponse,
    LiveRecordingResponse,
    FormatSelection,
    HealthResponse,
    CollectionEntryMoveRequest,
    CollectionEntryRefResponse,
    CollectionEntryResponse,
    CollectionRemoteRefRequest,
    HouseholdCollectionCreateRequest,
    HouseholdCollectionRenameRequest,
    HouseholdCollectionResponse,
    HouseholdCollectionVisibilityRequest,
    LivenessResponse,
    JobCreateRequest,
    JobPageResponse,
    JobResponse,
    WebhookTestResponse,
    LibraryNoteCreateRequest,
    LibraryNoteResponse,
    LibraryNoteUpdateRequest,
    LibraryItemResponse,
    LibraryItemVisibilityUpdateRequest,
    LibraryChannelResponse,
    LibraryGroupResponse,
    LibraryPageResponse,
    LibraryTagCreateRequest,
    LibraryTagResponse,
    LocalSearchMatchResponse,
    MediaSourceCapabilities,
    NormalizedChapterResponse,
    DescriptionTimestampResponse,
    PlaybackProgressResponse,
    PlaybackProgressUpdateRequest,
    PreviewRequest,
    RemotePrefetchRequest,
    PreviewResponse,
    InterestCategoryResponse,
    MemberInterestsResponse,
    MemberInterestsUpdateRequest,
    MemberOnboardingStateResponse,
    OnboardingCompleteRequest,
    FollowOutcomeResponse,
    FollowedSourceUnavailable,
    ChannelCandidateResponse,
    ChannelHeaderResponse,
    ChannelLiveResponse,
    ChannelPageResponse,
    ChannelResolveRequest,
    ChannelResolveResponse,
    ChannelTab,
    CategoryChannelSuggestionsResponse,
    ChannelSuggestionsResponse,
    ChannelSearchRequest,
    ChannelCandidateListResponse,
    SuppressRecommendationRequest,
    SuppressionResponse,
    SuppressionListResponse,
    LiveSnapshotResponse,
    LiveWallResponse,
    MemberRecommendationSnapshotResponse,
    PopularItemResponse,
    PopularSnapshotResponse,
    PopularWallResponse,
    RemotePlaybackResponse,
    RemotePlaybackProgressResponse,
    RemotePlaybackProgressUpdateRequest,
    SearchResultResponse,
    SessionLoginRequest,
    TwoFactorSignInRequest,
    SessionResponse,
    SourceAutomationCreateRequest,
    SourceAutomationAutoDownloadRequest,
    SourceAutomationResponse,
    UpNextRequest,
    YouTubeSearchRequest,
    YouTubeSearchResponse,
    YouTubeSearchResult,
    UserSettingsResponse,
    UserSettingsUpdateRequest,
    InvitationCreateRequest,
    InvitationRedeemRequest,
    InvitationResponse,
    InvitationSummaryResponse,
    PasswordChangeRequest,
    PasswordResetLinkResponse,
    PasswordResetRedeemRequest,
    UserResponse,
    AdminUserResponse,
    UserSelfUpdateRequest,
    UserUpdateRequest,
)
from app.security import (
    PasswordPolicyError,
    apply_session_cookie,
    ACCOUNT_LOCK_BUCKET,
    account_lock_key,
    audit_log,
    authenticate_rate_limited,
    charge_sign_in_failure,
    check_sign_in_limits,
    client_ip,
    basic_auth,
    clear_session_cookie,
    create_app_session,
    csrf_token_for,
    get_admin_user,
    get_owner_session_user,
    get_current_user,
    peek_session_user,
    revoke_session,
    claim_token,
    revoke_token,
    resolve_user_from_request,
    session_digest,
)
from sqlalchemy.exc import OperationalError
from app.services.acquisition_batch import AcquisitionBatchService, SelectedSourceEntry
from app.services.household_collections import (
    CollectionConflictError,
    CollectionEntryNotFoundError,
    CollectionFullError,
    CollectionNotFoundError,
    CollectionPermissionError,
    DuplicateCollectionItemError,
    DuplicateCollectionNameError,
    HouseholdCollectionService,
    InvisibleLibraryItemError,
    SmartCollectionEditError,
)
from app.media_schemas import ItemProgress, SearchScope, SmartCollectionRule
from app.services import smart_collections
from app.services import backups
from app.services import model_downloads, model_supervisor
from app.services import enrichment as enrichment_jobs
from app.services.redaction import install_log_redaction
from app.services.watch_queue import RemoteRef
from app.services.artifact_quarantine import ArtifactQuarantineService
from app.services.library_import import LibraryImportService, stop_event as import_stop_event
from app.services.job_manager import JobAdmissionError, JobConflictError, JobManager
from app.services.media_artifacts import MediaArtifactService, artifact_file
from app.services.media_notes import MediaNotesService
from app.services.media_response import ClosingStreamingResponse, MediaFileResponse
from app.services.local_playback_sessions import sessions as local_playback_sessions
from app.services.embeddings import start_backfill as start_embedding_backfill
from app.services.library_automation import FIRST_TICK_DELAY_S, automation as library_automation
from app.services.probe_warming import probe_warming
from app.services.renditions import renditions, use_host_format  # artwork renditions pass
from app.routers import art  # /api/art and /api/admin/artwork
from app.routers import visible_item_or_404
from app.services.library import (
    LIBRARY_PAGE_DEFAULT_LIMIT,
    LIBRARY_PAGE_MAX_LIMIT,
    LibraryService,
    LibraryViewKind,
    decode_library_cursor,
    encode_library_cursor,
)
from app.services.artwork import (
    ArtworkError,
    ArtworkNotFoundError,
    ArtworkService,
    LibraryArtwork,
    PublicArtworkFetcher,
)
from app.services.chapters import normalize_chapters
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError
from app.services.playback_log import log_playback
from app.services.playback import PlaybackProgressService
from app.services.remote_annotation import annotate_remote_entries
from app.services.remote_playback import RemotePlaybackProgressService
from app.services.rate_limit import enforce_rate_limit, rate_limit_dependency, rate_limiter, resolve_client_key
from app.services.remote_streaming import (
    AUTO_RENDITION_ID,
    DEFAULT_BROWSER_PROFILES,
    BrowserCapabilities,
    RangeNotSatisfiableError,
    RemoteStreamingService,
    StreamNotFoundError,
    UnsupportedPlaybackError,
    redact_preview_info,
)
from app.services import activity
from app.services.hwaccel import hwaccel
from app.services.media_probe import media_tool
from app.services.hls_relay import HlsRelayService
from app.services.hls_relay_support import supports_hls_relay, supports_live_hls_relay
from app.services.live_hls_relay import LiveHlsRelayService
from app.services.live_recording import (
    LiveRecordingLimitError,
    LiveRecordingService,
    TERMINAL_STATUSES,
    sweep_recording_retention,
)
from app.services.live_recording_adapters import (
    GuardedLiveChatCapturer,
    YtDlpLiveSourceProbe,
    build_guarded_live_media_recorder,
)
from app.services.live_recording_manager import LiveRecordingManager
from app.services.live_recording_waiter import ScheduledLiveWaiter
from app.services.popular_discovery import POPULAR_CATEGORIES, PopularDiscovery, PopularItem, PopularSnapshot, cap_rows, viable_category_keys
from app.services.reco.pool import RecoRefresher
from app.services.followed_live import FollowedLiveChecker
from app.services.live_discovery import LiveSearch, LiveWall, LiveWallError, create_live_discovery
from app.services.twitch_gql_directory import TwitchGqlDirectory
from app.services.channel_discovery import ChannelDiscoveryService
from app.services.member_follows import FollowRequest, MemberFollowService
from app.services.member_suppressions import FEEDBACK_EVENTS, MemberSuppressionService, feedback_event_key, queue_member_changed
from app.services import reco
from app.services.reco.policy import RecommendationPolicy, uncut
from app.services.popular_discovery import _source_key
from app.services.reco.events import RecoEventService, channel_key as reco_channel_key
from app.services.member_onboarding import MemberOnboardingService
from app.services.member_recommendations import MemberInterestService, MemberRecommendationPolicy, PlaybackContext
from app.services.remote_streaming_adapters import (
    FfmpegHlsPackager,
    PublicMediaReader,
    PublicRelayFetcher,
    YtDlpLiveChatFetcher,
    YtDlpPlaybackResolver,
)
from app.services.stream_cache import PersistentStreamRangeCache, StreamCachePolicy
from app.services.semantic_discovery import discovery as local_discovery, match_title_id
from app.services.title_summaries import title_summaries
from app.routers import client_metrics
from app.routers import reco as reco_routes
from app.services import client_metrics as client_metric_store
from app.services.source_automation import AutomationAlreadyRunningError, SourceAutomationService
from app.services.remote_prefetch import IntentPrefetcher
from app.services.user_settings import UserSettingsService, playback_ceiling
from app.services.users import RESET_LINK_HOURS, InvalidAccountToken, UserService, reset_link_url
from app.services.webhooks import WebhookService
from app.services.youtube_channels import CHANNEL_ID as YOUTUBE_CHANNEL_ID, ChannelTimeout, TAB_FRESH_SECONDS, ChannelUnavailable, channel_follow, channel_pages as build_channel_pages, parse_channel_address
from app.services.yt_dlp_service import SearchBusyError, YtDlpService
from app.services.live_chat_viewer import LiveChatViewer
from app.services.twitch_irc_chat import ProviderLiveChatFetcher, TwitchChatFetcher
from app.services.timed_chat import TimedChatBudget, normalize_youtube_replay_chat
from app.services.timed_chat_asset import BuildTicket, TimedChatAssetService


logger = logging.getLogger(__name__)
install_log_redaction()
# uvicorn configures only its own loggers: give Lumina's audit/playback/access lines a stderr handler.
_lumina_log = logging.getLogger("lumina")
if not _lumina_log.handlers:
    _lumina_handler = logging.StreamHandler()
    _lumina_handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
    _lumina_log.addHandler(_lumina_handler)
    _lumina_log.setLevel(logging.INFO)

events = EventBus()
jobs = JobManager(events)
remote_media_reader = PublicMediaReader()
artwork_policy = PublicSourcePolicy()


class _LibraryArtworkAccess:
    def find_visible_artwork(self, household_member_id: str, library_item_id: str) -> LibraryArtwork | None:
        with SessionLocal() as db:
            member = db.get(User, household_member_id)
            if member is None:
                return None
            service = LibraryService(db)
            item = service.get_item(library_item_id, member)
            if item is None:
                return None
            try:
                media_path, root_path = MediaArtifactService(db).locate(item)
            except FileNotFoundError:
                media_path, root_path = None, None
            poster = (item.metadata_json or {}).get("lumina_local_artwork")
            try:
                artwork_path = artifact_file(root_path, poster) if root_path and isinstance(poster, str) else None
            except FileNotFoundError:
                artwork_path = None
            return LibraryArtwork(
                library_item_id=item.id,
                media_path=media_path,
                provider_artwork_url=item.thumbnail_url,
                library_root=root_path,
                artwork_path=artwork_path,
            )


artwork = ArtworkService(
    remote_fetcher=PublicArtworkFetcher(artwork_policy),
    library_access=_LibraryArtworkAccess(),
    library_root=settings.library_root,
    cache_root=settings.data_dir / "artwork-cache",
    pinned_root=settings.data_dir / "metadata-art",
    public_source_policy=artwork_policy,
)


_remote_stream_cache_policies: dict[str, StreamCachePolicy] = {}
_remote_stream_cache_policies_lock = threading.Lock()


def _remote_stream_cache_policy(owner_user_id: str) -> StreamCachePolicy:
    """Resolve the member preference while preserving operator hard limits."""

    with _remote_stream_cache_policies_lock:
        cached = _remote_stream_cache_policies.get(owner_user_id)
    if cached is not None:
        return cached
    with session_scope() as db:
        owner = db.get(User, owner_user_id)
        if owner is None:
            return StreamCachePolicy(recent_video_limit=0, max_bytes=0)
        preference = UserSettingsService(db).serialize(
            UserSettingsService(db).ensure_for_user(owner)
        ).remote_playback_cache
    policy = StreamCachePolicy(
        recent_video_limit=preference.recent_video_limit if preference.enabled else 0,
        max_bytes=preference.storage_limit_mb * 1024 * 1024 if preference.enabled else 0,
    )
    with _remote_stream_cache_policies_lock:
        return _remote_stream_cache_policies.setdefault(owner_user_id, policy)


remote_stream_cache = PersistentStreamRangeCache(
    settings.remote_stream_cache_root,
    policy_provider=_remote_stream_cache_policy,
    max_bytes_global=settings.remote_stream_cache_max_bytes_global,
)
remote_prefetch = IntentPrefetcher()
remote_streams = RemoteStreamingService(
    resolver=YtDlpPlaybackResolver(),
    reader=remote_media_reader,
    hls_packager=FfmpegHlsPackager(
        reader=remote_media_reader,
        temp_root=settings.temp_root,
        max_total_bytes=512 * 1024 * 1024,
    ),
    idle_ttl_seconds=settings.remote_stream_idle_ttl_seconds,
    max_lifetime_seconds=settings.remote_stream_max_lifetime_seconds,
    refresh_margin_seconds=settings.remote_stream_refresh_margin_seconds,
    stream_cache=remote_stream_cache,
)
# The guarded HLS relay is a distinct seam from the local VOD packager above:
# it never materializes upstream media to disk and holds only a bounded,
# per-generation map of opaque resource ids to upstream addresses.
hls_relay = HlsRelayService(
    resolver=YtDlpPlaybackResolver(),
    fetcher=PublicRelayFetcher(request_timeout_seconds=settings.relay_request_timeout_seconds),
    policy=PublicSourcePolicy(),
    idle_ttl_seconds=settings.relay_idle_ttl_seconds,
    max_lifetime_seconds=settings.remote_stream_max_lifetime_seconds,
    refresh_margin_seconds=settings.remote_stream_refresh_margin_seconds,
    max_streams_global=settings.relay_max_sessions_global,
    max_streams_per_user=settings.relay_max_sessions_per_user,
    max_concurrent_reads_global=settings.relay_max_concurrent_reads_global,
    max_concurrent_reads_per_user=settings.relay_max_concurrent_reads_per_user,
    max_manifest_bytes=settings.relay_max_manifest_bytes,
    max_bytes_per_response=settings.relay_max_bytes_per_response,
    max_key_bytes=settings.relay_max_key_bytes,
    max_resources_per_generation=settings.relay_max_resources_per_generation,
    request_timeout_seconds=settings.relay_request_timeout_seconds,
)
# The live-HLS relay is the currently-live YouTube viewing path. It reuses the
# VOD relay's guarded transport and fail-closed rewriting but is a distinct,
# smaller capacity pool: a live viewing session is transient, not seekable, and
# never registered as a VOD (#91).
live_hls_relay = LiveHlsRelayService(
    resolver=YtDlpPlaybackResolver(),
    fetcher=PublicRelayFetcher(request_timeout_seconds=settings.relay_request_timeout_seconds),
    policy=PublicSourcePolicy(),
    idle_ttl_seconds=settings.live_relay_idle_ttl_seconds,
    max_lifetime_seconds=settings.remote_stream_max_lifetime_seconds,
    refresh_margin_seconds=settings.remote_stream_refresh_margin_seconds,
    max_streams_global=settings.live_relay_max_sessions_global,
    max_streams_per_user=settings.live_relay_max_sessions_per_user,
    max_concurrent_reads_global=settings.relay_max_concurrent_reads_global,
    max_concurrent_reads_per_user=settings.relay_max_concurrent_reads_per_user,
    max_manifest_bytes=settings.relay_max_manifest_bytes,
    max_bytes_per_response=settings.relay_max_bytes_per_response,
    max_key_bytes=settings.relay_max_key_bytes,
    max_resources_per_generation=settings.relay_max_resources_per_generation,
    request_timeout_seconds=settings.relay_request_timeout_seconds,
)
# "Record from now" is a durable, long-running acquisition that coordinates a
# media recording and a forward-only chat capture as sibling outputs with
# explicit partial outcomes. It survives a process restart: recover() re-launches
# or finalizes every non-terminal recording at startup. Its concurrency pool is
# bounded well below the transient viewing pools because each recording holds a
# worker pair and writes to disk for its whole runtime. The chat output follows
# the source's public yt-dlp live-chat edge (#97) and publishes a durable
# timed chat asset readable through the chat-replay path, so the
# coordination and honesty authority are provider-agnostic.
# Viewing live chat (2.1.2): shared, poll-driven, in memory; polls only while someone reads it.
live_chat_fetcher = ProviderLiveChatFetcher(YtDlpLiveChatFetcher(request_timeout_seconds=settings.live_recording_chat_request_timeout_seconds), TwitchChatFetcher())
live_chat_viewer = LiveChatViewer(live_chat_fetcher)

live_recording_manager = LiveRecordingManager(
    session_factory=SessionLocal,
    recorder=build_guarded_live_media_recorder(
        session_factory=SessionLocal,
        events=events,
        # Reuse the live relay's guarded resolver + fetcher so every media fetch
        # flows through the same fail-closed public-source transport as #96.
        resolver=YtDlpPlaybackResolver(),
        relay_fetcher=PublicRelayFetcher(request_timeout_seconds=settings.relay_request_timeout_seconds),
        temp_root=settings.temp_root,
        ffmpeg_path=None,
        max_runtime_seconds=settings.live_recording_max_runtime_seconds,
        max_disk_bytes=settings.live_recording_max_disk_bytes,
        max_manifest_bytes=settings.relay_max_manifest_bytes,
        max_segment_bytes=settings.relay_max_bytes_per_response,
        poll_interval_seconds=settings.live_recording_media_poll_interval_seconds,
        max_consecutive_failures=settings.live_recording_media_max_consecutive_failures,
    ),
    chat_capturer=GuardedLiveChatCapturer(
        fetcher=live_chat_fetcher,
        budget=TimedChatBudget(max_events=settings.live_recording_chat_max_events),
        max_consecutive_failures=settings.live_recording_chat_max_consecutive_failures,
        poll_interval_seconds=settings.live_recording_chat_poll_interval_seconds,
    ),
    # Scheduling an upcoming broadcast (issue #98): the waiter wakes near the
    # provider start time (real wall-clock, member-interruptible pause) and
    # re-inspects through the same guarded preview seam as the rest of the product.
    waiter=ScheduledLiveWaiter(
        probe=YtDlpLiveSourceProbe(session_factory=SessionLocal),
        clock=time.time,
        poll_interval_seconds=settings.live_recording_schedule_poll_interval_seconds,
        pre_roll_seconds=settings.live_recording_schedule_pre_roll_seconds,
        max_probe_interval_seconds=settings.live_recording_schedule_max_probe_interval_seconds,
        grace_seconds=settings.live_recording_schedule_grace_seconds,
        max_probe_attempts=settings.live_recording_schedule_max_probe_attempts,
    ),
    events=events,
    max_active_per_user=settings.live_recording_max_active_per_user,
    max_active_global=settings.live_recording_max_active_global,
    max_recovery_attempts=settings.live_recording_max_recovery_attempts,
    max_runtime_seconds=settings.live_recording_max_runtime_seconds,
    disk_path=str(settings.temp_root),
    # Restart-durable waiting-phase bound (issue #98): plan_recovery terminates a
    # wait that outlived scheduled_start + grace, or (no announced start) created_at
    # + the no-start cap, so a perpetual upcoming never holds an active slot forever.
    schedule_grace_seconds=settings.live_recording_schedule_grace_seconds,
    max_waiting_seconds=settings.live_recording_schedule_max_waiting_seconds,
)



def _relay_service_for(current_user_id: str, stream_id: str):
    """Dispatch a relay stream id to the service that owns it.

    Live and VOD relay share the ``/relay/{generation}/...`` address space and
    allocate globally-unique opaque stream ids, so a request routes to whichever
    guarded relay owns the id; nothing else can observe it.
    """

    if live_hls_relay.has_owned_stream(current_user_id, stream_id):
        return live_hls_relay
    return hls_relay


def _popular_search(query: str, limit: int) -> YouTubeSearchResponse:
    with SessionLocal() as db:
        # Keep the durable discovery snapshot independent from ArtworkService's
        # process-local opaque-ID registry.  Route-facing artwork URLs are
        # registered when a snapshot is served, so they remain valid after a
        # backend restart.
        return YtDlpService(db).youtube_search(query, limit, deep=True)


popular_discovery = PopularDiscovery(settings.data_dir / "popular-discovery.json", _popular_search)


def _youtube_live_search(query: str, limit: int) -> YouTubeSearchResponse:
    with SessionLocal() as db:
        return YtDlpService(db).youtube_live_search(query, limit)


def _probe_followed_live(probe_url: str) -> YouTubeSearchResult | None:
    with SessionLocal() as db:
        return YtDlpService(db).probe_live_source(probe_url)


twitch_directory = TwitchGqlDirectory()
live_search = LiveSearch(_youtube_live_search, twitch_directory)
live_wall = LiveWall(twitch_directory, youtube_pool=live_search.youtube_pool, gaming=live_search.gaming)
live_discovery = create_live_discovery(settings.data_dir / "live-discovery.json", live_search)
followed_live_checker = FollowedLiveChecker(_probe_followed_live)


def _extract_channel_listing(url: str, limit: int) -> dict[str, Any]:
    with SessionLocal() as db:
        return YtDlpService(db).extract_channel_listing(url, limit)


channel_pages = build_channel_pages(_extract_channel_listing)


def _reco_search(query: str, limit: int) -> list[YouTubeSearchResult]:
    """The recommendation refresher's provider search: the policy-guarded path (network_policy via YtDlpService).

    ``SearchBusyError`` propagates on purpose: the refresher defers five minutes on it.
    """
    with SessionLocal() as db:
        return list(YtDlpService(db).youtube_search(query, limit, cache=False).items)


reco_refresher = RecoRefresher(SessionLocal, _reco_search, channel_pages)


def _remote_artwork_url(provider_artwork_url: object) -> str | None:
    if not isinstance(provider_artwork_url, str) or not provider_artwork_url.strip():
        return None
    try:
        artwork_id = artwork.register_remote_artwork(provider_artwork_url)
    except ArtworkError:
        return None
    except PublicSourcePolicyError:
        return None
    return f"/api/artwork/remote/{artwork_id}"


def _search_response_with_artwork(response: YouTubeSearchResponse) -> YouTubeSearchResponse:
    return response.model_copy(update={
        "items": [
            item.model_copy(update={"artwork_url": _remote_artwork_url(item.thumbnail)})
            for item in response.items
        ]
    })


def _annotate_in_thread(user: User, entries: list[YouTubeSearchResult]) -> list[YouTubeSearchResult]:
    """Annotation for the async search routes, which hold no request session."""
    with SessionLocal() as db:
        return annotate_remote_entries(db, user, entries)


def _annotated_follows(db: Session, user: User, follows: list[SourceAutomationResponse]) -> list[SourceAutomationResponse]:
    """Every follow's feed entries annotated in one call, so a Subscriptions load stays at two queries."""
    annotated = iter(annotate_remote_entries(db, user, [entry for follow in follows for entry in follow.feed_entries]))
    return [follow.model_copy(update={"feed_entries": [next(annotated) for _ in follow.feed_entries]}) for follow in follows]


def _job_response_with_artwork(response: JobResponse) -> JobResponse:
    thumbnail = (response.preview_snapshot or {}).get("thumbnail")
    return response.model_copy(update={"artwork_url": _remote_artwork_url(thumbnail)})


def _source_automation_service(db: Session) -> SourceAutomationService:
    # The registrar is injected here rather than left to each call site so
    # every consumer of serialize() — the REST responses and the
    # automation_checked/automation_failed SSE payloads built inside the
    # service — maps a stored raw artwork URL through the same serve-boundary
    # proxy as _job_response_with_artwork above.
    return SourceAutomationService(db, jobs, events, artwork_url_resolver=_remote_artwork_url)


def resolve_frontend_dist() -> Path | None:
    candidates = [
        Path("/app/frontend/dist"),
        Path.cwd() / "frontend" / "dist",
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class SpaStaticFiles(StaticFiles):
    """Serve the built frontend, answering client routes (``/watch/...``) with index.html.

    Missing files with an extension and unknown ``/api`` paths stay 404 so broken
    asset references and API typos are not masked by the app shell.
    """

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        if scope["type"] == "websocket":  # e.g. a Jellyfin client's /socket; StaticFiles asserts an http scope
            await send({"type": "websocket.close", "code": 1008})
            return
        await super().__call__(scope, receive, send)

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            leaf = path.rsplit("/", 1)[-1]
            if exc.status_code != 404 or path == "api" or path.startswith("api/") or "." in leaf:
                raise
            return await super().get_response("index.html", scope)


def run_library_maintenance_cycle() -> None:
    """Advance each resumable library sweep by one short batch."""
    with session_scope() as db:
        service = LibraryService(db, events)
        service.reconcile_files_step()
        sweep_recording_retention(db)
        ArtifactQuarantineService(db).sweep()
        LibraryImportService(db).advance_active()
    with session_scope() as db, write_transaction(db, name="reco_sweep"):
        RecoEventService(db).sweep(utcnow())  # At most 2,000 rows per cycle
    with session_scope() as db, write_transaction(db, name="title_edit_maintenance"):
        metadata_history.maintenance(db)  # edit-history prune + upload GC; throttles itself to hourly
    with session_scope() as db:
        tmdb.cap_pinned(db, artwork)  # the pinned TMDB art folder's size cap; throttles itself to hourly
    local_playback_sessions.reap()
    model_supervisor.supervisor.reap_idle()  # idle model servers stop (5 min); crashed ones are noticed
    start_embedding_backfill()  # non-blocking; restart-safe catch-up
    reco_refresher.schedule_due()  # non-blocking; the refresher's worker makes the provider calls
    client_metric_store.flush_due()  # client metrics reach the database every 5 minutes


class MaintenanceSweepHealth:
    """Consecutive-failure visibility for the resumable maintenance sweeps.

    A poisoned persistence state can fail every sweep cycle for hours while the
    unauthenticated liveness check stays green. Runtime health reports this
    counter beside the persistence metrics so operators can see it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._last_error = None
            self._last_success_at = datetime.now(UTC).isoformat()

    def record_failure(self, exc: BaseException) -> None:
        with self._lock:
            self._consecutive_failures += 1
            # Bounded, secret-free breadcrumb — same shape as the log line.
            self._last_error = f"{exc!r:.300}"
            self._last_failure_at = datetime.now(UTC).isoformat()

    def reset(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._last_error = None
            self._last_success_at = None
            self._last_failure_at = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "consecutive_failures": self._consecutive_failures,
                "last_error": self._last_error,
                "last_success_at": self._last_success_at,
                "last_failure_at": self._last_failure_at,
            }


maintenance_sweep_health = MaintenanceSweepHealth()


def run_scheduled_backup() -> None:
    with session_scope() as db:
        schedule = admin_backups.schedule_of(YtDlpService(db).get_app_settings())
    if schedule.daily:
        backups.run_scheduled(schedule.keep)


async def reconcile_loop() -> None:
    while True:
        await asyncio.sleep(settings.reconcile_interval_seconds)
        remote_streams.expire_idle()
        hls_relay.expire_idle()
        live_hls_relay.expire_idle()
        activity.sweep()
        try:
            await asyncio.to_thread(enrichment_jobs.pump_pending)
        except Exception as exc:
            logger.warning("Enrichment pump failed; retrying next interval: %.300s", repr(exc))
        try:
            await asyncio.to_thread(run_library_maintenance_cycle)
        except Exception as exc:
            # Bounded, secret-free breadcrumb: recurring sweep failures must be
            # visible to operators instead of silently stalling maintenance.
            maintenance_sweep_health.record_failure(exc)
            logger.warning("Library maintenance cycle failed; retrying next interval: %.300s", repr(exc))
            continue
        maintenance_sweep_health.record_success()
        try:
            await asyncio.to_thread(run_scheduled_backup)
        except Exception as exc:
            logger.warning("Scheduled backup failed; retrying next interval: %.300s", repr(exc))


AUTOMATION_REFRESH_WORKERS = 3


async def source_automation_loop() -> None:
    while True:
        await asyncio.sleep(15)

        def run_due_checks() -> None:
            now = utcnow()
            with session_scope() as db:
                due = _source_automation_service(db).due_ids(now)

            def refresh(automation_id: str) -> None:
                with session_scope() as db:
                    _source_automation_service(db).process_one(automation_id, now)

            # One refresh path for every provider's follows and automations; a slow or
            # failing source holds one worker, never the whole sweep.
            with ThreadPoolExecutor(max_workers=AUTOMATION_REFRESH_WORKERS, thread_name_prefix="follow-refresh") as pool:
                list(pool.map(refresh, due))

        try:
            await asyncio.to_thread(run_due_checks)
        except Exception:
            continue


METADATA_IDLE_SECONDS = 30
METADATA_BUSY_PAUSE_SECONDS = 0


async def metadata_loop() -> None:
    """TMDB refresh: drain due titles; a full batch goes again immediately.

    No pause on a full batch: the due column is the whole queue, so looping immediately just
    drains it faster. An idle 30 s wait follows once a batch comes back short.
    """
    while True:
        try:
            full = await asyncio.to_thread(title_metadata.run_due_batch)
        except Exception as exc:  # noqa: BLE001 - the type only; the next interval retries
            logger.warning("TMDB metadata batch failed; retrying next interval: %s", type(exc).__name__)
            full = False
        await asyncio.sleep(METADATA_BUSY_PAUSE_SECONDS if full else METADATA_IDLE_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime_security()
    server_lock = backups.hold_server_lock()  # the offline restore refuses while this is held
    init_db()
    with session_scope() as db:
        two_factor.reseal_legacy(db)  # 2.9.0 authenticator secrets → app-data/totp-key, before any sign-in
    configured_concurrency = 1
    with session_scope() as db:
        UserService(db).require_remote_bootstrap_ready(settings.remote_https_enabled)
        app_settings = YtDlpService(db).ensure_app_settings()
        configured_concurrency = app_settings.concurrency
        public_address.set_origin(app_settings.public_address)
        public_address.set_local_origin(app_settings.local_address)
        network_policy.set_extra_ports((app_settings.ui_prefs or {}).get("extra_source_ports"))
        hwaccel.warm(media_tool(db, "ffmpeg"), app_settings.hwaccel or "auto")
        UserSettingsService(db).ensure_for_all_users()
        cache_owner_ids = [owner_id for owner_id, in db.query(User.id).all()]
    # Reconcile durable member policies before the global ceiling. This also
    # finishes a settings change if the prior process stopped after committing
    # the preference but before deleting now-ineligible cache entries.
    for owner_id in cache_owner_ids:
        remote_stream_cache.prune_owner(owner_id)
    remote_stream_cache.prune()
    local_playback_sessions.close_all()  # derivatives are disposable; drop any a crash left
    enrichment_jobs.recover_after_restart()  # before any worker starts: re-pend once, then fail
    probe_warming.start()  # background ffprobe + loudness, one file at a time; also woken after each import
    library_automation.start(first_tick_delay_s=FIRST_TICK_DELAY_S)  # scheduled scans and folder watching; polls off the event loop
    # gallery: artwork renditions, one nice-19 ffmpeg at a time, paused during video transcodes. Off the loop:
    # start() probes ffmpeg's encoders and sweeps staging. With the pass off, on-demand renders still need the format.
    await asyncio.to_thread(renditions.start if settings.artwork_pass else use_host_format)
    events.bind(asyncio.get_running_loop())
    admin_models.wire(events)
    model_downloads.manager.resume_wanted()  # model downloads a restart interrupted continue from their .part files
    import_stop_event.clear()
    await jobs.start(concurrency=configured_concurrency)
    # Re-launch or finalize every live recording left non-terminal by a restart,
    # so a long-running recording survives a process crash deterministically.
    await asyncio.to_thread(live_recording_manager.recover)
    reconcile_task = asyncio.create_task(reconcile_loop())
    automation_task = asyncio.create_task(source_automation_loop())
    metadata_task = asyncio.create_task(metadata_loop())
    request_sync_task = asyncio.create_task(request_sync_loop())  # skips cleanly while Requests are off or no server is set
    catalog_warm_task = asyncio.create_task(catalog_warm_loop())  # Requests catalog first-load warm-up; idle while Requests are off
    popular_discovery.start()
    live_discovery.start()
    try:
        yield
    finally:
        reconcile_task.cancel()
        automation_task.cancel()
        metadata_task.cancel()
        request_sync_task.cancel()
        catalog_warm_task.cancel()
        await asyncio.to_thread(library_automation.stop)
        import_stop_event.set()
        remote_streams.close_all()
        await asyncio.to_thread(probe_warming.stop)
        await asyncio.to_thread(renditions.stop)
        await asyncio.to_thread(local_playback_sessions.close_all)
        hls_relay.close_all()
        live_hls_relay.close_all()
        await asyncio.to_thread(live_recording_manager.close_all)
        await asyncio.to_thread(popular_discovery.close)
        await asyncio.to_thread(live_discovery.close)
        await asyncio.to_thread(followed_live_checker.close)
        await asyncio.to_thread(channel_pages.close)
        await asyncio.to_thread(reco_refresher.close)
        await asyncio.to_thread(twitch_directory.close)
        try:
            await reconcile_task
        except asyncio.CancelledError:
            pass
        try:
            await automation_task
        except asyncio.CancelledError:
            pass
        try:
            await metadata_task
        except asyncio.CancelledError:
            pass
        try:
            await request_sync_task
        except asyncio.CancelledError:
            pass
        await asyncio.to_thread(model_downloads.manager.shutdown)  # partial files stay for the next start
        await asyncio.to_thread(model_supervisor.supervisor.close)
        await asyncio.to_thread(client_metric_store.flush)  # keep the last minutes of client metrics
        await jobs.stop()
        server_lock.close()


app = FastAPI(
    title="yt-dlp ui backend",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)
app.add_middleware(public_address.DynamicTrustedHostMiddleware, allowed_hosts=settings.trusted_hosts_list)
app.add_middleware(public_address.SecureCookieMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestBodyLimitMiddleware)
# Compress text assets and JSON for clients connecting directly over LAN HTTP.
# Media, partial responses and event streams bypass compression in Starlette.
app.add_middleware(
    ResponseCompressionMiddleware,
    minimum_size=1000,
    compresslevel=3,
)
# Keep direct-stream patterns ahead of unrelated routes. The Jellyfin normalizer
# is installed by jellyfin.register below.
app.include_router(jellyfin.stream_router)
admin_storage.register(app)
admin_invites.register(app)
admin_backups.register(app)
admin_ai.register(app)
admin_models.register(app)
admin_overview.register(app, events)
admin_activity.register(app, (hls_relay, live_hls_relay))
member_access_routes.register(app)
admin_diagnostics.register(app, events, lambda: maintenance_sweep_health.snapshot())
client_metrics.register(app)
reco_routes.register(app)
admin_recording_retention.register(app)
admin_tasks.register(app, jobs)
admin_library_automation.register(app)
media_tools.register(app)
enrichment_routes.register(app)
admin_imports.register(app)
summaries.register(app)
idea_graph.register(app)
admin_metadata.register(app, artwork)
title_images_routes.register(app, artwork)
metadata_editor_routes.register(app)
provenance.register(app)
watch_queue.register(app)
discovery.register(app)
local_playback.register(app)
search_history.register(app)
profile_export.register(app)
connected_apps.register(app)
two_factor_routes.register(app)
jellyfin_history.register(app)
jellyfin_auth.register(app)  # before jellyfin.register(app, artwork) and its catch-all
admin_media_server.register(app)
library_sections.register(app)  # before main.py's /api/library/{item_id} routes below
requests_routes.register(app)
art.register(app)
requests_catalog.register(app)
showcase_routes.register(app)  # public: the sign-in showcase (test_unauthenticated_surface)
titles.register(app, artwork)
jellyfin.register(app, artwork)  # after jellyfin_auth.register(app)


def jellyfin_error(request: Request, status_code: int, headers: dict[str, str] | None = None) -> Response | None:
    """An error on the Jellyfin-compatible surface keeps its status and has no body, as Jellyfin's own errors do:
    some clients (Roku) parse any body as the answer they asked for, so a JSON error object reads as a result."""
    if request.scope["path"].startswith("/jellyfin/") or request.scope["path"] == "/jellyfin":  # JellyfinPathMiddleware's rewrite
        return Response(status_code=status_code, headers=headers)
    return None


@app.exception_handler(StarletteHTTPException)
async def http_error_without_body_on_jellyfin(request: Request, exc: StarletteHTTPException) -> Response:
    return jellyfin_error(request, exc.status_code, dict(exc.headers or {})) or await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def request_validation_error_without_submitted_values(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    if (empty := jellyfin_error(request, 400)) is not None:  # Jellyfin has no 422
        return empty
    safe_errors = [
        {key: error[key] for key in ("type", "loc", "msg") if key in error}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": safe_errors})


@app.exception_handler(PublicSourcePolicyError)
async def public_source_policy_error(_request: Request, exc: PublicSourcePolicyError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(PasswordPolicyError)
async def password_policy_error(_request: Request, exc: PasswordPolicyError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(StreamNotFoundError)
async def remote_stream_not_found(_request: Request, _exc: StreamNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Remote stream not found."})


@app.exception_handler(RangeNotSatisfiableError)
async def remote_stream_range_not_satisfiable(_request: Request, exc: RangeNotSatisfiableError) -> JSONResponse:
    headers = {"Content-Range": f"bytes */{exc.length}"} if exc.length is not None else {}
    return JSONResponse(status_code=416, content={"detail": str(exc)}, headers=headers)


@app.exception_handler(UnsupportedPlaybackError)
async def unsupported_remote_playback(_request: Request, exc: UnsupportedPlaybackError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(OperationalError)
async def database_temporarily_unavailable(request: Request, _exc: OperationalError) -> Response:
    if (empty := jellyfin_error(request, 503, {"Retry-After": "1"})) is not None:
        return empty
    return JSONResponse(
        status_code=503,
        content={"detail": "Storage is temporarily unavailable. Please try again."},
        headers={"Retry-After": "1"},
    )


@app.exception_handler(Exception)
async def internal_server_error_with_security_headers(request: Request, _exc: Exception) -> Response:
    # Starlette's ServerErrorMiddleware is outside application middleware, so its
    # generated 500 must carry the policy explicitly without reflecting details.
    if (empty := jellyfin_error(request, 500, SECURITY_HEADERS)) is not None:
        return empty
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=SECURITY_HEADERS,
    )


app.add_middleware(OriginCheckMiddleware)  # pure ASGI: same place, so the order is unchanged


app.add_middleware(FailedRequestLogMiddleware)
# Keep this outermost so framework, host, origin, upload, API, and static responses
# all receive the same browser security policy, including early rejection paths.
app.add_middleware(SecurityHeadersMiddleware)


def serialize_acquisition_batches(
    db: Session,
    batches: list[AcquisitionBatch],
    *,
    include_entries: bool = True,
) -> list[AcquisitionBatchResponse]:
    """Serialize batches with their entries and job outputs in two queries total."""
    entries_by_batch: dict[str, list[AcquisitionBatchEntry]] = {batch.id: [] for batch in batches}
    outputs_by_entry: dict[str, AcquisitionJobOutput] = {}
    if include_entries and batches:
        user_id = batches[0].user_id
        entries = db.scalars(
            select(AcquisitionBatchEntry)
            .where(AcquisitionBatchEntry.user_id == user_id, AcquisitionBatchEntry.batch_id.in_(entries_by_batch))
            .order_by(AcquisitionBatchEntry.selection_index, AcquisitionBatchEntry.id)
        ).all()
        for entry in entries:
            entries_by_batch[entry.batch_id].append(entry)
        if entries:
            outputs_by_entry = {
                output.batch_entry_id: output
                for output in db.query(AcquisitionJobOutput).filter(
                    AcquisitionJobOutput.user_id == user_id,
                    AcquisitionJobOutput.batch_entry_id.in_([entry.id for entry in entries]),
                )
            }
    return [_serialize_acquisition_batch(batch, entries_by_batch[batch.id], outputs_by_entry) for batch in batches]


def _serialize_acquisition_batch(
    batch: AcquisitionBatch,
    entries: list[AcquisitionBatchEntry],
    outputs_by_entry: dict[str, AcquisitionJobOutput],
) -> AcquisitionBatchResponse:
    serialized_entries: list[AcquisitionBatchEntryResponse] = []
    for entry in entries:
        output = outputs_by_entry.get(entry.id)
        serialized_entries.append(AcquisitionBatchEntryResponse(
            id=entry.id,
            batch_id=entry.batch_id,
            selection_index=entry.selection_index,
            source_url=entry.source_url,
            extractor=entry.extractor,
            remote_id=entry.remote_id,
            title=entry.title,
            status=entry.status,
            progress=entry.progress,
            dispatch_attempts=entry.dispatch_attempts,
            failure_category=entry.failure_category,
            error=entry.error,
            details=entry.details_json,
            download_job_id=output.download_job_id if output else None,
            library_item_id=output.library_item_id if output else None,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            finished_at=entry.finished_at,
        ))
    return AcquisitionBatchResponse(
        id=batch.id,
        source_url=batch.source_url,
        source_title=batch.source_title,
        source_provenance=batch.source_provenance,
        status=batch.status,
        selected_count=batch.selected_count,
        queued_count=batch.queued_count,
        duplicate_count=batch.duplicate_count,
        completed_count=batch.completed_count,
        failed_count=batch.failed_count,
        progress=batch.progress,
        format_selection=batch.format_selection,
        output_profile=batch.output_profile,
        entries=serialized_entries,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        finished_at=batch.finished_at,
    )


COLLECTION_ITEMS_PREVIEW_LIMIT = 100


def _collection_entry_response(membership: HouseholdCollectionMembership, item: LibraryItem | None) -> CollectionEntryResponse:
    if membership.library_item_id is not None:
        if item is None:
            # Revoked or deleted: a redacted tombstone the owner can only remove.
            return CollectionEntryResponse(
                id=membership.id, position=membership.position, availability="unavailable",
                ref=CollectionEntryRefResponse(kind="library"), title=None, uploader=None, artwork_url=None, duration=None,
            )
        return CollectionEntryResponse(
            id=membership.id, position=membership.position, availability="available",
            ref=CollectionEntryRefResponse(kind="library", library_item_id=item.id, url=item.webpage_url),
            title=item.title, uploader=item.uploader, artwork_url=f"/api/library/{item.id}/artwork", duration=item.duration,
        )
    return CollectionEntryResponse(
        id=membership.id, position=membership.position, availability="available",
        ref=CollectionEntryRefResponse(kind="remote", provider=membership.provider, remote_id=membership.remote_id, url=membership.source_url),
        title=membership.title, uploader=membership.uploader, artwork_url=membership.artwork_url, duration=membership.duration,
    )


def serialize_household_collection(
    db: Session,
    collection: HouseholdCollection,
    current_user: User,
    *,
    include_items: bool,
    item_count: int | None = None,
) -> HouseholdCollectionResponse:
    """``item_count`` lets list callers pass a pre-aggregated count (see list_household_collections)."""
    HouseholdCollectionService(db).get_visible(member_user_id=current_user.id, collection_id=collection.id)
    if collection.rules is not None:
        # A smart collection: its rule evaluated as this viewer (ADR 0003); the passed item_count is ignored.
        rule = SmartCollectionRule.model_validate(collection.rules)
        rows = smart_collections.evaluate(db, current_user, rule) if include_items else []
        titled = rule.type != "channel_video"
        library = LibraryService(db)
        if not titled:
            library.prime_owners(rows)
        return HouseholdCollectionResponse(
            id=collection.id, owner_user_id=collection.owner_user_id, name=collection.name,
            description=collection.description, visibility=collection.visibility, revision=collection.revision,
            item_count=smart_collections.count_matches(db, current_user, rule),
            items=[] if titled else [library.serialize(item, current_user, summary=True) for item in rows],
            entries=[], rules=rule,
            titles=title_summaries(db, current_user, [row.id for row in rows]) if titled else [],
            created_at=collection.created_at, updated_at=collection.updated_at,
        )
    # Collection membership never grants access to an invisible item.
    visible_membership = db.query(HouseholdCollectionMembership).join(
        LibraryItem, LibraryItem.id == HouseholdCollectionMembership.library_item_id
    ).filter(
        HouseholdCollectionMembership.collection_id == collection.id,
        LibraryService.visible_predicate(current_user),
    )
    if item_count is None:
        item_count = visible_membership.with_entities(func.count()).scalar() or 0
    items: list[LibraryItem] = []
    entries: list[CollectionEntryResponse] = []
    library = LibraryService(db)
    if include_items:
        items = (
            visible_membership.with_entities(LibraryItem)
            .order_by(HouseholdCollectionMembership.created_at, HouseholdCollectionMembership.id)
            .limit(COLLECTION_ITEMS_PREVIEW_LIMIT)
            .all()
        )
        library.prime_owners(items)
        # Mixed-source view: every entry in position order, library refs
        # tombstoned rather than dropped when visibility was revoked.
        _, memberships = HouseholdCollectionService(db).list_entries(member_user_id=current_user.id, collection_id=collection.id)
        library_ids = [membership.library_item_id for membership in memberships if membership.library_item_id]
        visible_items = {
            row.id: row
            for row in db.query(LibraryItem).filter(LibraryItem.id.in_(library_ids), LibraryService.visible_predicate(current_user))
        } if library_ids else {}
        entries = [_collection_entry_response(membership, visible_items.get(membership.library_item_id or "")) for membership in memberships]
    return HouseholdCollectionResponse(
        id=collection.id,
        owner_user_id=collection.owner_user_id,
        name=collection.name,
        description=collection.description,
        visibility=collection.visibility,
        revision=collection.revision,
        item_count=item_count,
        items=[library.serialize(item, current_user, summary=True) for item in items],
        entries=entries,
        rules=None,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )


def resolve_request_user_snapshot(
    request: Request,
    credentials: HTTPBasicCredentials | None = None,
) -> User:
    with SessionLocal() as db:
        current_user = resolve_user_from_request(db, request=request, credentials=credentials)
        snapshot = User(
            id=current_user.id,
            username=current_user.username,
            display_name=current_user.display_name,
            role=current_user.role,
            is_active=current_user.is_active,
        )
        return member_access.carry_access(db, snapshot, current_user)


def _gate_snapshot(user: User, kind: str | None = None, *, url: str | None = None, watch: bool = False, **kwargs: Any) -> None:
    """Streaming gate (and, with ``watch``, the playback-start time check) for the async routes that hold a user snapshot."""
    with SessionLocal() as db:
        if url is not None:
            streaming_gate.check_url(db, user, url, **kwargs)
        if kind is not None:
            streaming_gate.check(db, user, kind, **kwargs)
        if watch:
            screen_time.enforce(db, user, fresh=True)


@app.get("/api/health", response_model=LivenessResponse)
def health() -> LivenessResponse:
    return LivenessResponse(status="ok")


@app.get("/api/runtime-health", response_model=HealthResponse)
def runtime_health(current_user: User = Depends(get_current_user)) -> HealthResponse:
    """Member-safe: no paths or internals; operators read /api/admin/diagnostics."""
    del current_user
    return HealthResponse(status=admin_diagnostics.health_status(maintenance_sweep_health.snapshot()), version=APP_VERSION)


@app.get("/api/bootstrap/status", response_model=BootstrapStatusResponse)
def bootstrap_status(db: Session = Depends(get_db, scope="function")) -> BootstrapStatusResponse:
    return BootstrapStatusResponse(needs_setup=UserService(db).needs_bootstrap())


@app.post("/api/bootstrap/admin", response_model=UserResponse, status_code=201, dependencies=[Depends(rate_limit_dependency("bootstrap_admin"))])
def bootstrap_admin(payload: BootstrapAdminCreateRequest, db: Session = Depends(get_db, scope="function")) -> UserResponse:
    service = UserService(db)
    try:
        user = service.create_initial_admin(payload.username, payload.password, payload.display_name)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.serialize_user(user)


@app.post("/api/session/login", response_model=SessionResponse)
def login_session(payload: SessionLoginRequest, response: Response, request: Request, db: Session = Depends(get_db, scope="function")) -> SessionResponse | JSONResponse:
    user = authenticate_rate_limited(db, request, payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if two_factor.enabled(user) and not two_factor.trusted(request, user):
        # The password alone opens nothing: a short-lived, single-use challenge for this client asks for the code. Each
        # one costs a sign-in attempt from this address, so minting challenges never multiplies the code guesses.
        rate_limiter.check("session_login", resolve_client_key(request))
        challenge = two_factor.create_challenge(user, resolve_client_key(request), remember_on_device=payload.remember_on_device)
        audit_log.info("login.two_factor_challenge user=%s client=%s", user.id, client_ip(request))
        return JSONResponse({"two_factor_required": True, "challenge": challenge}, headers={"Cache-Control": "no-store"})
    rate_limiter.reset(ACCOUNT_LOCK_BUCKET, account_lock_key(user.username))
    return _complete_sign_in(db, request, response, user, remember_on_device=payload.remember_on_device)


@app.post("/api/session/two-factor", response_model=SessionResponse)
def two_factor_sign_in(payload: TwoFactorSignInRequest, response: Response, request: Request, db: Session = Depends(get_db, scope="function")) -> SessionResponse:
    """Step two: the code (or a recovery code) for a password-verified challenge from this same client."""
    ip_key = resolve_client_key(request)
    rate_limiter.check("session_login", ip_key, record=False)
    opened = two_factor.open_challenge(payload.challenge, ip_key, lambda user_id: db.get(User, user_id))
    if opened is None:
        rate_limiter.check("session_login", ip_key)
        raise HTTPException(status_code=410, detail="two_factor_challenge_expired")
    challenge, user = opened
    check_sign_in_limits(request, user.username)
    if not (payload.code or payload.recovery_code) or not two_factor.verify(db, user, code=payload.code, recovery_code=payload.recovery_code):
        audit_log.warning("login.two_factor_fail user=%s client=%s", user.id, ip_key)
        two_factor.fail_challenge(payload.challenge)
        charge_sign_in_failure(request, user.username)
        raise HTTPException(status_code=401, detail="That code didn't work. Check your authenticator app and try again.")
    if not two_factor.consume_challenge(payload.challenge):  # a concurrent answer to the same challenge already won
        raise HTTPException(status_code=410, detail="two_factor_challenge_expired")
    rate_limiter.reset(ACCOUNT_LOCK_BUCKET, account_lock_key(user.username))
    if payload.trust_device:
        two_factor.apply_trust_cookie(response, user)
    return _complete_sign_in(db, request, response, user, remember_on_device=challenge.remember_on_device)


def _complete_sign_in(db: Session, request: Request, response: Response, user: User, *, remember_on_device: bool) -> SessionResponse:
    audit_log.info("login.success user=%s client=%s", user.id, client_ip(request))
    ring = read_ring(request)
    replaced = request.cookies.get(settings.session_cookie_name)
    session, token = create_app_session(db, user, request)
    apply_session_cookie(response, session, token)
    # The session this browser held until now is unreachable once its cookie is overwritten: sign it out unless it is
    # remembered in the device ring.
    if replaced and replaced not in ring:
        revoke_token(db, replaced)
    if remember_on_device:
        # A vault owner joins as a marker that authenticates nothing; everyone else as this session.
        entry = device_ring.owner_marker(user.id) if user.role == "admin" else token
        # One ring entry per member: an older remembered session of this member is replaced and signed out.
        for older in list(ring):
            if _ring_entry_user_id(db, older) == user.id:
                revoke_token(db, older)
                ring = drop_token(ring, older)
        joined = append_token(ring, entry)
        for evicted in ring:  # the oldest falls off a full ring: unreachable, so signed out rather than left live
            if evicted not in joined:
                revoke_token(db, evicted)
        apply_ring_cookie(response, joined)
    return SessionResponse(user=UserService(db).serialize_user(user), csrf_token=csrf_token_for(token))


@app.post("/api/session/logout", status_code=204)
def logout_session(response: Response, request: Request, db: Session = Depends(get_db, scope="function")) -> Response:
    token = request.cookies.get(settings.session_cookie_name)
    ring = read_ring(request)
    leaving = peek_session_user(db, token)
    revoke_session(db, request)
    clear_session_cookie(response)
    # The member signing out leaves the ring: their session, or their owner marker.
    kept = [entry for entry in ring if entry != token and (leaving is None or device_ring.marker_user_id(entry) != leaving.id)]
    if kept != ring:
        apply_ring_cookie(response, kept)
    response.status_code = 204
    return response


def _ring_entry_user_id(db: Session, entry: str) -> str | None:
    """Whose ring entry this is, from an owner marker's signed id or the session row (no validity check)."""
    owner_id = device_ring.marker_user_id(entry)
    if owner_id is not None:
        return owner_id
    record = db.get(AppSession, session_digest(entry))
    return record.user_id if record is not None else None


def _valid_ring(db: Session, request: Request) -> tuple[list[str], list[tuple[str, User]], bool]:
    """The ring's entries that still stand (every session rule, no touch), and whether the cookie needs rewriting.

    An owner marker stands while its member is an active vault owner. An owner *bearer* (an older ring, or a session
    whose member was promoted) is never kept: it becomes a marker and its session is revoked, unless
    it is this browser's active session, which the next switch away signs out."""
    ring = read_ring(request)
    live: list[tuple[str, User]] = []
    seen: set[str] = set()
    active = request.cookies.get(settings.session_cookie_name)
    for entry in ring:
        owner_id = device_ring.marker_user_id(entry)
        if owner_id is not None:
            user = db.get(User, owner_id)
            if user is None or not user.is_active or user.role != "admin":
                continue
        else:
            user = peek_session_user(db, entry)
            if user is None:
                continue
            if user.role == "admin":
                if entry != active:
                    revoke_token(db, entry)
                entry = device_ring.owner_marker(user.id)
        if user.id in seen:
            if entry != active:
                revoke_token(db, entry)  # one entry per member: a dropped duplicate would otherwise stay live unreachably
            continue
        seen.add(user.id)
        live.append((entry, user))
    kept = [entry for entry, _ in live]
    raw = request.cookies.get(device_ring.ring_cookie_name())
    return kept, live, kept != ring or (raw is not None and raw != device_ring.serialize_ring(ring))


def _ring_refusal(status_code: int, detail: str, kept: list[str], changed: bool) -> JSONResponse:
    refusal = JSONResponse(status_code=status_code, content={"detail": detail})
    if changed:
        apply_ring_cookie(refusal, kept)
    return refusal


@app.get("/api/session/device-members", response_model=list[DeviceMember], dependencies=[Depends(rate_limit_dependency("device_members"))])
def device_members(request: Request, response: Response, db: Session = Depends(get_db, scope="function")) -> list[DeviceMember]:
    """Only the members remembered on this browser (app polish 6.2): no ring cookie, no names."""
    kept, live, changed = _valid_ring(db, request)
    if changed:
        apply_ring_cookie(response, kept)
    active = peek_session_user(db, request.cookies.get(settings.session_cookie_name))
    return [
        DeviceMember(
            user_id=user.id, display_name=user.display_name or user.username, username=user.username, role=user.role,
            switch="password" if user.role == "admin" else "instant", active=active is not None and active.id == user.id,
        )
        for _, user in live
    ]


@app.post("/api/session/switch", response_model=SessionResponse)
def switch_session(payload: SessionSwitchRequest, request: Request, response: Response, db: Session = Depends(get_db, scope="function")) -> SessionResponse | JSONResponse:
    # The login IP bucket is checked on every call but charged only on a failure, like authenticate_rate_limited
    #: a successful switch reuses a session already on this browser and is not a guess.
    ip_key = resolve_client_key(request)
    rate_limiter.check("session_login", ip_key, record=False)
    kept, live, changed = _valid_ring(db, request)
    target = next(((entry, user) for entry, user in live if user.id == payload.user_id), None)
    if target is None:
        rate_limiter.check("session_login", ip_key)
        return _ring_refusal(404, "not_in_ring", kept, changed)
    entry, user = target
    current_token = request.cookies.get(settings.session_cookie_name)
    current = peek_session_user(db, current_token)
    if current is not None and current.id == user.id:  # already this member (an owner included): nothing to do
        if changed:
            apply_ring_cookie(response, kept)
        return SessionResponse(user=UserService(db).serialize_user(user), csrf_token=csrf_token_for(current_token))
    if user.role == "admin":  # a vault owner always proves their password; their entry is only a marker
        rate_limiter.check("session_login", ip_key)
        return _ring_refusal(401, "password_required", kept, changed)
    previous = db.get(AppSession, session_digest(entry))
    if previous is None:  # signed out or forgotten between validation and now; the current member stays signed in
        return _ring_refusal(404, "not_in_ring", [t for t in kept if t != entry], True)
    # Every switch rotates (security review I1): a fresh bearer for the member, inheriting the old absolute deadline,
    # so a ring segment copied off this browser dies on the next switch to its member.
    with write_transaction(db, name="session_switch"):  # claim and mint commit together (re-review M-R3)
        if not claim_token(db, entry):  # a simultaneous switch with the same old token won
            return _ring_refusal(404, "not_in_ring", [t for t in kept if t != entry], True)
        session, token = create_app_session(db, user, request, expires_at=previous.expires_at)
    kept = [token if t == entry else t for t in kept]
    if current_token and current_token not in kept:
        revoke_token(db, current_token)  # leaving an un-remembered member (or any vault owner) signs them out
    apply_session_cookie(response, session, token)
    apply_ring_cookie(response, kept)
    return SessionResponse(user=UserService(db).serialize_user(user), csrf_token=csrf_token_for(token))


@app.post("/api/session/forget", status_code=204)
def forget_device_member(payload: DeviceForgetRequest, request: Request, response: Response, db: Session = Depends(get_db, scope="function")) -> Response:
    """Possession of the ring is the credential: revoke and drop every entry of one member (idempotent)."""
    _, live, changed = _valid_ring(db, request)  # no touch; dead, duplicate and owner-bearer entries are already gone
    current_token = request.cookies.get(settings.session_cookie_name)
    kept: list[str] = []
    for entry, user in live:
        if user.id == payload.user_id:
            revoke_token(db, entry)
            if entry == current_token:
                clear_session_cookie(response)
            continue
        kept.append(entry)
    if changed or len(kept) != len(live):
        apply_ring_cookie(response, kept)
    response.status_code = 204
    return response


@app.get("/api/session/me", response_model=SessionResponse)
def get_session(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> SessionResponse:
    session_id = request.cookies.get(settings.session_cookie_name)
    return SessionResponse(
        user=UserService(db).serialize_user(current_user),
        csrf_token=csrf_token_for(session_id) if session_id else None,
    )


@app.put("/api/session/me", response_model=SessionResponse)
def update_session(
    payload: UserSelfUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SessionResponse:
    service = UserService(db)
    updated = service.update_current_user(current_user, payload)
    return SessionResponse(user=service.serialize_user(updated))


@app.post("/api/me/password", response_model=SessionResponse)
def change_my_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SessionResponse:
    enforce_rate_limit("session_login", request, user_id=current_user.id)
    service = UserService(db)
    try:
        service.change_password(current_user, payload.current_password, payload.new_password)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    csrf_token = None
    if request.cookies.get(settings.session_cookie_name):
        # Every session was revoked; the browser that proved the password gets a fresh one.
        session, token = create_app_session(db, current_user, request)
        apply_session_cookie(response, session, token)
        csrf_token = csrf_token_for(token)
    return SessionResponse(user=service.serialize_user(current_user), csrf_token=csrf_token)


@app.post("/api/password-reset/redeem", status_code=204, dependencies=[Depends(rate_limit_dependency("account_token_redeem"))])
def redeem_password_reset(payload: PasswordResetRedeemRequest, db: Session = Depends(get_db, scope="function")) -> Response:
    try:
        UserService(db).redeem_password_reset(payload.token, payload.new_password)
    except InvalidAccountToken as exc:
        raise HTTPException(status_code=410, detail="This reset link is invalid, already used, or expired.") from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=204)


@app.get("/api/settings/me", response_model=UserSettingsResponse)
def get_my_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> UserSettingsResponse:
    service = UserSettingsService(db)
    return service.serialize(service.ensure_for_user(current_user))


@app.put("/api/settings/me", response_model=UserSettingsResponse)
def update_my_settings(
    payload: UserSettingsUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> UserSettingsResponse:
    service = UserSettingsService(db)
    try:
        with write_transaction(db, name="user_settings_update"):
            updated = service.update_for_user(current_user, payload)
            serialized = service.serialize(updated)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload.remote_playback_cache is not None:
        # Cache side effects must reflect durable state, never an uncommitted
        # request-scoped setting that could still roll back.
        preference = serialized.remote_playback_cache
        policy = StreamCachePolicy(
            recent_video_limit=preference.recent_video_limit if preference.enabled else 0,
            max_bytes=preference.storage_limit_mb * 1024 * 1024 if preference.enabled else 0,
        )
        with _remote_stream_cache_policies_lock:
            _remote_stream_cache_policies[current_user.id] = policy
        remote_stream_cache.prune_owner(current_user.id)
    events.publish("settings_updated", {"settings": serialized.model_dump(mode="json"), "user_id": current_user.id})
    return serialized


@app.get("/api/events")
async def events_endpoint(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)] = None,
) -> StreamingResponse:
    current_user = await run_in_threadpool(resolve_request_user_snapshot, request, credentials)
    # Reserve the slot synchronously (no await between count and register) so concurrent opens cannot exceed the cap.
    subscriber = events.reserve(current_user.id, member_access.snapshot_limits_library(current_user))
    if subscriber is None:
        raise HTTPException(status_code=429, detail="Too many open event streams for this account.")

    async def still_authorized() -> bool:
        # Long-lived streams re-run full authentication so revoked sessions and deactivated accounts drop off.
        try:
            user = await run_in_threadpool(resolve_request_user_snapshot, request, credentials)
        except HTTPException:
            return False
        subscriber.restricted = member_access.snapshot_limits_library(user)
        return True

    return StreamingResponse(
        sse_stream(events, current_user.id, still_authorized, subscriber),
        media_type="text/event-stream",
        # Frees the slot even if the client disconnects before the stream starts.
        background=BackgroundTask(events.release, subscriber),
    )


@app.get("/api/artwork/remote/{artwork_id}")
def remote_artwork(
    artwork_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> Response:
    enforce_rate_limit("artwork_serve", request, user_id=current_user.id)

    def charge_upstream_fetch() -> None:
        # Only cold provider fetches consume the amplification budget; warm
        # cache hits and 304 revalidations stay on the cheap serving ceiling.
        enforce_rate_limit("artwork", request, user_id=current_user.id)

    try:
        resolved = artwork.load_remote_artwork(artwork_id, before_upstream_fetch=charge_upstream_fetch)
    except ArtworkNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artwork not found") from exc
    except (ArtworkError, PublicSourcePolicyError) as exc:
        raise HTTPException(status_code=502, detail="Artwork is temporarily unavailable") from exc
    return _remote_artwork_response(request, resolved.content_type, resolved.content)


@app.get("/api/library/{item_id}/artwork")
def library_artwork(
    item_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> Response:
    enforce_rate_limit("artwork_serve", request, user_id=current_user.id)

    def charge_upstream_fetch() -> None:
        enforce_rate_limit("artwork", request, user_id=current_user.id)

    try:
        resolved = artwork.load_library_artwork(current_user.id, item_id, before_upstream_fetch=charge_upstream_fetch)
    except ArtworkNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artwork not found") from exc
    except (ArtworkError, PublicSourcePolicyError) as exc:
        raise HTTPException(status_code=502, detail="Artwork is temporarily unavailable") from exc
    return _library_artwork_response(request, resolved.content_type, resolved.content)


def _remote_artwork_response(request: Request, content_type: str, content: bytes) -> Response:
    """Cache public provider artwork privately in the member's browser."""

    return _cached_artwork_response(
        request,
        content_type,
        content,
        cache_control="private, max-age=3600, stale-while-revalidate=21600",
    )


def _library_artwork_response(request: Request, content_type: str, content: bytes) -> Response:
    """Store Library artwork but recheck its authorization before every reuse."""

    return _cached_artwork_response(
        request,
        content_type,
        content,
        cache_control="private, no-cache",
    )


def _cached_artwork_response(
    request: Request,
    content_type: str,
    content: bytes,
    *,
    cache_control: str,
) -> Response:
    """Build one private, validator-aware artwork response."""

    etag = f'"{hashlib.sha256(content).hexdigest()}"'
    headers = {
        "Cache-Control": cache_control,
        "ETag": etag,
        "Vary": "Cookie, Authorization",
        "X-Content-Type-Options": "nosniff",
    }
    if _if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=content, media_type=content_type, headers=headers)


def _if_none_match_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    expected = etag.removeprefix("W/").strip()
    return any(
        candidate == "*" or candidate.removeprefix("W/").strip() == expected
        for candidate in (part.strip() for part in header.split(","))
    )


def _playback_ceiling(user_id: str) -> int | None:
    with SessionLocal() as db:
        return playback_ceiling(db, user_id)


def _browser_capabilities(supported_profiles: list[str] | None, max_height: int | None) -> BrowserCapabilities:
    return BrowserCapabilities(
        supported_profiles=frozenset(supported_profiles) if supported_profiles is not None else DEFAULT_BROWSER_PROFILES,
        max_height=max_height,
    )


def _playback_registrar(response: PreviewResponse):  # noqa: ANN202
    """The guarded relay owns the one supported segmented-transport case (a Twitch VOD master); the live relay owns
    currently-live YouTube played at the current edge; everything else stays on the progressive/packaged
    remote-streaming path."""
    caps = response.capabilities
    if caps is not None and supports_live_hls_relay(response.raw, caps.provider, caps.lifecycle):
        return live_hls_relay
    if caps is not None and supports_hls_relay(response.raw, caps.provider, caps.lifecycle):
        return hls_relay
    return remote_streams


@app.post("/api/preview", response_model=PreviewResponse)
async def preview(
    payload: PreviewRequest,
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)] = None,
) -> PreviewResponse:
    current_user = await run_in_threadpool(resolve_request_user_snapshot, request, credentials)
    enforce_rate_limit("preview", request, user_id=current_user.id)
    await run_in_threadpool(_gate_snapshot, current_user, url=payload.source_url)

    def run_preview() -> PreviewResponse:
        with SessionLocal() as thread_db:
            return YtDlpService(thread_db).preview(
                payload.source_url,
                payload.lazy_playlist,
                format_selection=payload.format_selection,
                entries_limit=payload.entries_limit,
            )

    started = time.monotonic()
    try:
        response = await asyncio.to_thread(run_preview)
    except yt_dlp.utils.DownloadError as exc:
        raise _download_error_http_exception(exc) from exc
    # Time to first frame, step by step: extraction (or a cache hit / joined prefetch) for this click.
    log_playback("remote.preview", "ready", kind=response.kind, extractor=response.extractor_key, duration_ms=round((time.monotonic() - started) * 1000))
    playback = None
    # Capability derivation owns lifecycle and transport eligibility.  In
    # particular, live and upcoming previews must not create a seekable VOD
    # session merely so the UI can show their metadata.
    playable = response.kind == "video" and (response.capabilities is None or response.capabilities.can_play)
    # Now the channel and lifecycle are known: followed_only and live apply, and a playable video is a playback start.
    await run_in_threadpool(
        _gate_snapshot, current_user, url=response.webpage_url or payload.source_url, watch=playable,
        channels=(response.raw.get("channel_url"), response.raw.get("uploader_url")),
        live=response.capabilities is not None and response.capabilities.lifecycle == "live",
    )
    if playable:
        enforce_rate_limit("remote_stream_register", request, user_id=current_user.id)
        browser_capabilities = _browser_capabilities(payload.supported_profiles, await run_in_threadpool(_playback_ceiling, current_user.id))
        registrar = _playback_registrar(response)
        playback = RemotePlaybackResponse.model_validate(registrar.register(
            owner_user_id=current_user.id,
            source_url=response.webpage_url or payload.source_url,
            info=response.raw,
            browser_capabilities=browser_capabilities,
        ))
        if registrar is remote_streams and playback.transport == "dash":
            # The manifest request comes ~0.4 s after this answer (render, dash.js import): read the indexes meanwhile.
            raw = response.raw
            remote_prefetch.submit(current_user.id, f"dash:{playback.stream_id}", lambda: remote_streams.warm_dash(
                owner_user_id=current_user.id, info=raw, browser_capabilities=browser_capabilities,
            ), household_cap=False)
    preview_artwork_source = (
        YtDlpService.resolve_channel_avatar(response.raw)
        if response.kind == "playlist"
        else YtDlpService.resolve_search_thumbnail(response.raw)
    )
    preview_artwork_url = _remote_artwork_url(preview_artwork_source)
    timeline = normalize_chapters(
        provider_chapters=response.raw.get("chapters"),
        description=response.raw.get("description"),
        duration=response.raw.get("duration"),
    )
    response = response.model_copy(update={
        "artwork_url": preview_artwork_url,
        # model_copy(update=...) skips validation, so build the response models
        # here rather than leaving normalize_chapters()'s plain dicts in typed
        # fields (they'd still serialize correctly, but with a per-item
        # PydanticSerializationUnexpectedValue warning — see #141).
        "chapters": [NormalizedChapterResponse.model_validate(c) for c in timeline["chapters"]],
        "description_timestamps": [
            DescriptionTimestampResponse.model_validate(t) for t in timeline["timestamps"]
        ],
        "entries": [entry.model_copy(update={
            "thumbnail": None,
            "artwork_url": _remote_artwork_url(entry.thumbnail),
        }) for entry in response.entries],
        "raw": redact_preview_info(response.raw),
        "playback": playback,
    })
    events.publish("preview_ready", {**response.model_dump(mode="json"), "user_id": current_user.id})
    return response


@app.post("/api/remote/prefetch", status_code=202)
def prefetch_remote(
    payload: RemotePrefetchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, bool]:
    """Warm the preview a click on this card would run, with the member's own format default so the click
    finds the same cache entry. The prefetcher dedupes per source and caps each member; extra intents are dropped."""
    enforce_rate_limit("remote_prefetch", request, user_id=current_user.id)
    if not streaming_gate.allows_url(db, current_user, payload.source_url, channels=()):  # followed_only: followed sources only
        return {"accepted": False}
    member_settings = UserSettingsService(db)
    selection = member_settings.resolve_download_defaults(
        member_settings.snapshot_for_user(current_user)
    ).format_selection.model_copy(update={"custom_format": None})  # the watch page never sends one

    def warm() -> None:
        # Hover prefetch is the lowest priority and never warms DASH indexes (the play path does): a dropped one is silent.
        with provider_budget.priority("prefetch"), SessionLocal() as thread_db:
            try:
                YtDlpService(thread_db).preview(payload.source_url, True, format_selection=selection)
            except provider_budget.BudgetExhausted:
                logger.debug("Intent prefetch skipped: YouTube budget spent")

    if provider_budget.paused(provider_budget.youtube):
        return {"accepted": False}
    return {"accepted": remote_prefetch.submit(current_user.id, payload.source_url, warm)}


WATCH_TIME = Depends(screen_time.require_watch_time)  # a running stream re-checks viewing hours and the daily limit


def _streaming_response(spec) -> StreamingResponse:  # noqa: ANN001
    return ClosingStreamingResponse(
        spec.body,
        status_code=spec.status_code,
        headers=spec.headers,
        close=spec.close,
    )


@app.get("/api/remote-streams/{stream_id}/content", dependencies=[WATCH_TIME])
def remote_stream_content(
    stream_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    range_header: str | None = Header(default=None, alias="Range"),
) -> StreamingResponse:
    enforce_rate_limit("remote_stream_content", request, user_id=current_user.id)
    return _streaming_response(remote_streams.serve_content(current_user.id, stream_id, range_header))


@app.get("/api/remote-streams/{stream_id}/renditions/{rendition_id}/content", dependencies=[WATCH_TIME])
def remote_stream_rendition_content(
    stream_id: str,
    rendition_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    range_header: str | None = Header(default=None, alias="Range"),
) -> StreamingResponse:
    enforce_rate_limit("remote_stream_content", request, user_id=current_user.id)
    return _streaming_response(remote_streams.serve_content(
        current_user.id,
        stream_id,
        range_header,
        rendition_id=rendition_id,
    ))


@app.post("/api/remote-streams/{stream_id}/renditions/{rendition_id}/select", response_model=RemotePlaybackResponse, dependencies=[WATCH_TIME])
def select_remote_stream_rendition(
    stream_id: str,
    rendition_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RemotePlaybackResponse:
    enforce_rate_limit("remote_stream_select", request, user_id=current_user.id)
    ceiling = _playback_ceiling(current_user.id) if rendition_id == AUTO_RENDITION_ID else None
    return RemotePlaybackResponse.model_validate(
        remote_streams.select_rendition(current_user.id, stream_id, rendition_id, max_height=ceiling)
    )


@app.get("/api/remote-streams/{stream_id}/hls/{generation}/{resource}", dependencies=[WATCH_TIME])
def remote_stream_hls_resource(
    stream_id: str,
    generation: int,
    resource: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    range_header: str | None = Header(default=None, alias="Range"),
) -> StreamingResponse:
    # Only a manifest that starts packaging counts as a package request; a growing playlist is reloaded often.
    if resource == "manifest.m3u8" and not remote_streams.has_presentation(current_user.id, stream_id, generation):
        enforce_rate_limit("remote_stream_package", request, user_id=current_user.id)
    else:
        enforce_rate_limit("remote_stream_content", request, user_id=current_user.id)
    return _streaming_response(remote_streams.serve_hls(current_user.id, stream_id, generation, resource, range_header))


@app.get("/api/remote-streams/{stream_id}/dash/{generation}/{resource}", dependencies=[WATCH_TIME])
def remote_stream_dash_resource(
    stream_id: str,
    generation: int,
    resource: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    range_header: str | None = Header(default=None, alias="Range"),
) -> StreamingResponse:
    if resource == "manifest.mpd":
        enforce_rate_limit("remote_stream_package", request, user_id=current_user.id)
    else:
        enforce_rate_limit("remote_stream_content", request, user_id=current_user.id)
    return _streaming_response(remote_streams.serve_dash(current_user.id, stream_id, generation, resource, range_header))


@app.get("/api/remote-streams/{stream_id}/relay/{generation}/master.m3u8", dependencies=[WATCH_TIME])
def remote_stream_relay_master(
    stream_id: str,
    generation: int,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    enforce_rate_limit("remote_stream_package", request, user_id=current_user.id)
    relay = _relay_service_for(current_user.id, stream_id)
    return _streaming_response(relay.serve_master(current_user.id, stream_id, generation))


@app.get("/api/remote-streams/{stream_id}/relay/{generation}/r/{resource_id}", dependencies=[WATCH_TIME])
def remote_stream_relay_resource(
    stream_id: str,
    generation: int,
    resource_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    range_header: str | None = Header(default=None, alias="Range"),
) -> StreamingResponse:
    enforce_rate_limit("remote_stream_content", request, user_id=current_user.id)
    relay = _relay_service_for(current_user.id, stream_id)
    return _streaming_response(
        relay.serve_resource(current_user.id, stream_id, generation, resource_id, range_header)
    )


@app.post("/api/remote-streams/{stream_id}/refresh", response_model=RemotePlaybackResponse, dependencies=[WATCH_TIME])
def refresh_remote_stream(
    stream_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RemotePlaybackResponse:
    enforce_rate_limit("remote_stream_refresh", request, user_id=current_user.id)
    service = _owning_stream_service(current_user.id, stream_id)
    return RemotePlaybackResponse.model_validate(service.refresh(current_user.id, stream_id))


@app.delete("/api/remote-streams/{stream_id}", status_code=204)
def release_remote_stream(stream_id: str, current_user: User = Depends(get_current_user)) -> Response:
    service = _owning_stream_service(current_user.id, stream_id)
    service.release(current_user.id, stream_id)
    return Response(status_code=204)


def _owning_stream_service(current_user_id: str, stream_id: str):
    if live_hls_relay.has_owned_stream(current_user_id, stream_id):
        return live_hls_relay
    if hls_relay.has_owned_stream(current_user_id, stream_id):
        return hls_relay
    return remote_streams


@app.post("/api/youtube-search", response_model=YouTubeSearchResponse)
async def youtube_search(
    payload: YouTubeSearchRequest,
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)] = None,
) -> YouTubeSearchResponse:
    current_user = await run_in_threadpool(resolve_request_user_snapshot, request, credentials)
    enforce_rate_limit("youtube_search", request, user_id=current_user.id)
    await run_in_threadpool(_gate_snapshot, current_user, "youtube", beyond_follows=True)

    def run_search() -> YouTubeSearchResponse:
        with SessionLocal() as thread_db:
            return YtDlpService(thread_db).youtube_search(payload.query, payload.limit)

    try:
        response = _search_response_with_artwork(await asyncio.to_thread(run_search))
    except SearchBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=400, detail=YtDlpService.explain_download_error(str(exc))) from exc
    return response.model_copy(update={"items": await run_in_threadpool(_annotate_in_thread, current_user, response.items)})


def _reco_policy(db: Session) -> RecommendationPolicy:
    """The 1.9.0 policy over this process's pool refresher and the channel cache."""
    return RecommendationPolicy(db, refresher=reco_refresher, channel_pages=channel_pages)


def _with_remote_artwork(items: list[PopularItemResponse]) -> list[PopularItemResponse]:
    return [item.model_copy(update={"artwork_url": _remote_artwork_url(item.thumbnail)}) for item in items]


@app.get("/api/discovery/popular", response_model=PopularSnapshotResponse, dependencies=[Depends(require_streaming("youtube"))])
def popular_now(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> PopularSnapshotResponse:
    enforce_rate_limit("popular", request, user_id=current_user.id)
    raw = popular_discovery.get_snapshot()
    # Rows are built from every category's items, not the snapshot's 24-item feed cut (which left most rows with one video).
    full = uncut(raw, popular_discovery.candidates())
    if not reco.enabled(db):
        snapshot = PopularSnapshotResponse.model_validate(full)
        hidden = MemberSuppressionService(db).active_keys(current_user)  # the legacy policy's item and channel suppressions
        items = [item.model_copy(update={"artwork_url": _remote_artwork_url(item.thumbnail)}) for item in snapshot.items
                 if _source_key(item.source, item.id, item.webpage_url, item.title, item.uploader) not in hidden.item_keys
                 and (item.uploader or "").strip().casefold() not in hidden.channel_keys]
        return _viable_rows(snapshot.model_copy(update={"items": annotate_remote_entries(db, current_user, items)}))
    # Rails re-ordered for the member, For you (24) and the member's category order.
    policy = _reco_policy(db)
    served, order = policy.explore(current_user, full)
    rails = _with_remote_artwork([PopularItemResponse.model_validate(item) for item in policy.rails(current_user, full)])
    for_you = _with_remote_artwork(policy.remote_entries(served, full))
    annotated = annotate_remote_entries(db, current_user, [*rails, *for_you])  # one call: still two queries
    return _viable_rows(PopularSnapshotResponse.model_validate(uncut(raw, ())).model_copy(update={
        "items": annotated[:len(rails)], "for_you": annotated[len(rails):], "category_order": list(order),
    }))


def _viable_rows(snapshot: PopularSnapshotResponse) -> PopularSnapshotResponse:
    """Omit the rows left with fewer than 4 videos after the member's filters (2.4.0)."""
    items = cap_rows(snapshot.items)  # 20 per row keeps the page light; See all walls load the rest
    keep = viable_category_keys(items, snapshot.categories)
    return snapshot.model_copy(update={"items": items, "categories": [c for c in snapshot.categories if c.key in keep]})


@app.get("/api/discovery/popular/{category_key}", response_model=PopularWallResponse, dependencies=[Depends(require_streaming("youtube"))])
def popular_wall(
    category_key: str,
    request: Request,
    cursor: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=40, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> PopularWallResponse:
    enforce_rate_limit("popular", request, user_id=current_user.id)
    try:
        entries, next_cursor = popular_discovery.wall(category_key, cursor, limit)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown category.") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid cursor.") from None
    hidden = MemberSuppressionService(db).active_keys(current_user)
    items = [PopularItemResponse.model_validate(entry).model_copy(update={"artwork_url": _remote_artwork_url(entry.thumbnail)})
             for entry in entries
             if _source_key(entry.source, entry.id, entry.webpage_url, entry.title, entry.uploader) not in hidden.item_keys
             and (entry.uploader or "").strip().casefold() not in hidden.channel_keys]
    return PopularWallResponse(items=annotate_remote_entries(db, current_user, items), next_cursor=next_cursor)


@app.get("/api/discovery/live", response_model=LiveSnapshotResponse, dependencies=[Depends(require_streaming("live"))])
def live_now(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LiveSnapshotResponse:
    enforce_rate_limit("popular", request, user_id=current_user.id)
    snapshot = LiveSnapshotResponse.model_validate(live_discovery.get_snapshot())
    followed_urls = sorted(MemberFollowService(db).existing_follow_identities(current_user))
    hero = [
        PopularItemResponse.model_validate(entry).model_copy(
            update={"artwork_url": _remote_artwork_url(entry.thumbnail)}
        )
        for entry in followed_live_checker.live_entries(followed_urls)
    ]
    unavailable = followed_live_checker.unavailable_sources(followed_urls)
    items = [item.model_copy(update={"artwork_url": _remote_artwork_url(item.thumbnail)}) for item in snapshot.items]
    annotated = annotate_remote_entries(db, current_user, [*hero, *items])  # one call: still two queries
    hero, items = annotated[: len(hero)], annotated[len(hero):]
    counts = live_search.counts
    categories = [category.model_copy(update={"live_count": counts.get(category.key)}) for category in snapshot.categories]
    known = [category.live_count for category in categories if category.live_count is not None]
    return snapshot.model_copy(
        update={
            "categories": categories,
            "live_total": sum(known) if known else None,
            "twitch_available": live_search.health.available,
            "hero": hero,
            "followed_unavailable": [
                FollowedSourceUnavailable(source=source, checked_at=datetime.fromtimestamp(checked_at, UTC))
                for source, checked_at in sorted(unavailable.items())
            ],
            "items": items,
        }
    )


@app.get("/api/discovery/live/{category_key}", response_model=LiveWallResponse, dependencies=[Depends(require_streaming("live"))])
def live_wall_page(
    category_key: str,
    request: Request,
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=40, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LiveWallResponse:
    enforce_rate_limit("popular", request, user_id=current_user.id)
    try:
        entries, next_cursor, page_count = live_wall.page(category_key, cursor, limit)
    except LiveWallError as error:
        if error.kind == "unavailable":
            raise HTTPException(status_code=503, detail="Live streams are temporarily unavailable.") from error
        raise HTTPException(status_code=404 if error.kind == "unknown" else 400, detail=f"Invalid live {error.kind}.") from error
    items = [
        PopularItemResponse.model_validate(entry).model_copy(update={"artwork_url": _remote_artwork_url(entry.thumbnail)})
        for entry in entries
    ]
    live_count = live_search.counts.get(category_key)
    return LiveWallResponse(
        items=annotate_remote_entries(db, current_user, items),
        next_cursor=next_cursor,
        live_count=live_count if live_count is not None else page_count,
    )


def _channel_live(header, follow: tuple[str, str] | None) -> ChannelLiveResponse | None:  # noqa: ANN001
    """Live now: the follow probe for a followed channel, else a cached streams tab. Never a new probe kind."""
    if follow is not None:
        entries = followed_live_checker.live_entries([follow[1]])
    else:
        streams = channel_pages.peek_tab(header.id, "streams", 60, max_age=TAB_FRESH_SECONDS)
        entries = [entry for entry in (streams.entries if streams else ()) if entry.capabilities and entry.capabilities.lifecycle == "live"]
    entry = next((entry for entry in entries if entry.webpage_url), None)
    if entry is None:
        return None
    return ChannelLiveResponse(webpage_url=entry.webpage_url, title=entry.title, view_count=entry.view_count, artwork_url=_remote_artwork_url(entry.thumbnail))


def _channel_network_unavailable() -> HTTPException:
    """The channel URL is server-built youtube.com, so a policy refusal here is a DNS or network failure: retryable, not "not allowed"."""
    return HTTPException(status_code=503, detail="YouTube could not be reached. Try again in a moment.", headers={"Retry-After": "5"})


@app.get("/api/channels/youtube/{channel_id}", response_model=ChannelPageResponse, dependencies=[Depends(require_streaming("youtube", beyond_follows=False))])
def youtube_channel_page(
    channel_id: str,
    request: Request,
    tab: ChannelTab = Query(default="videos"),
    limit: int = Query(default=60),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> ChannelPageResponse:
    """A YouTube channel's public page, cached; the member's follow and markers joined after."""
    if not YOUTUBE_CHANNEL_ID.fullmatch(channel_id):
        raise HTTPException(status_code=404, detail="Channel not found")
    if limit not in (60, 120):
        raise HTTPException(status_code=422, detail="limit must be 60 or 120")
    enforce_rate_limit("channel_page", request, user_id=current_user.id)
    try:
        header, page, fetched_at, stale = channel_pages.page(channel_id, tab, limit)
    except ChannelUnavailable as exc:
        raise HTTPException(status_code=404, detail="channel_unavailable") from exc
    except ChannelTimeout as exc:
        raise HTTPException(status_code=504, detail="YouTube did not respond in time.") from exc
    except SearchBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PublicSourcePolicyError as exc:
        raise _channel_network_unavailable() from exc
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=502, detail=YtDlpService.explain_download_error(str(exc))) from exc
    follow = channel_follow(db, current_user, header)
    if follow is None:
        streaming_gate.check_url(db, current_user, header.url, channels=())  # followed_only: a followed channel only
    entries = [entry.model_copy(update={"artwork_url": _remote_artwork_url(entry.thumbnail), "thumbnail": None}) for entry in page.entries]
    return ChannelPageResponse(
        channel=ChannelHeaderResponse(
            id=header.id, name=header.name, handle=header.handle, url=header.url,
            avatar_url=_remote_artwork_url(header.avatar), banner_url=_remote_artwork_url(header.banner),
            follower_count=header.follower_count, video_count=header.video_count, description=header.description,
            verified=header.verified, tabs=list(header.tabs), follow_id=follow[0] if follow else None, live=_channel_live(header, follow),
        ),
        tab=tab, entries=annotate_remote_entries(db, current_user, entries), has_more=page.has_more, restricted=page.restricted,
        fetched_at=datetime.fromtimestamp(fetched_at, UTC), stale=stale,
    )


@app.post("/api/channels/resolve", response_model=ChannelResolveResponse, dependencies=[Depends(require_streaming("youtube", beyond_follows=False))])
def resolve_youtube_channel(
    payload: ChannelResolveRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> ChannelResolveResponse:
    """An @handle, /c/ or /user/ address to its channel id; /channel/UC… answers without extracting."""
    enforce_rate_limit("channel_page", request, user_id=current_user.id)
    address = parse_channel_address(payload.url)
    if address is None:
        raise HTTPException(status_code=400, detail="Paste a YouTube channel address.")
    kind, value = address
    if kind == "id":
        return ChannelResolveResponse(channel_id=value)
    try:
        return ChannelResolveResponse(channel_id=channel_pages.resolve(value))
    except PublicSourcePolicyError as exc:
        raise _channel_network_unavailable() from exc
    except ChannelUnavailable as exc:
        raise HTTPException(status_code=404, detail="channel_unavailable") from exc
    except ChannelTimeout as exc:
        raise HTTPException(status_code=504, detail="YouTube did not respond in time.") from exc
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=502, detail=YtDlpService.explain_download_error(str(exc))) from exc



@app.get("/api/discovery/interests", response_model=MemberInterestsResponse)
def get_member_interests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MemberInterestsResponse:
    interests = MemberInterestService(db)
    category_labels = {category.key: category.label for category in POPULAR_CATEGORIES}
    return MemberInterestsResponse(
        categories=[InterestCategoryResponse(key=key, label=label) for key, label in category_labels.items()],
        selected_keys=list(interests.list_for(current_user)),
    )


@app.put("/api/discovery/interests", response_model=MemberInterestsResponse)
def update_member_interests(
    payload: MemberInterestsUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MemberInterestsResponse:
    interests = MemberInterestService(db)
    try:
        with write_transaction(db, name="member_interests_update"):
            selected = interests.replace(current_user, payload.keys)
            queue_member_changed(db, current_user.id, reco_refresher)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    category_labels = {category.key: category.label for category in POPULAR_CATEGORIES}
    return MemberInterestsResponse(
        categories=[InterestCategoryResponse(key=key, label=label) for key, label in category_labels.items()],
        selected_keys=list(selected),
    )


@app.post("/api/onboarding/complete", response_model=MemberOnboardingStateResponse)
def complete_onboarding(
    payload: OnboardingCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MemberOnboardingStateResponse:
    service = MemberOnboardingService(db)
    follows = MemberFollowService(db)
    requests = [
        FollowRequest(source_url=follow.source_url, display_name=follow.display_name) for follow in payload.follows
        if streaming_gate.allows_url(db, current_user, follow.source_url, beyond_follows=True)  # a blocked kind is skipped
    ]
    # Validate follow addresses (DNS/network policy) before the durable write so
    # that resolution never spans the onboarding-complete transaction.
    planned, rejected = follows.validate_and_plan(current_user, requests)
    try:
        with write_transaction(db, name="onboarding_complete"):
            status, selected = service.complete(current_user, payload.keys)
            created = follows.create_planned(current_user, planned)
            queue_member_changed(db, current_user.id, reco_refresher)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    outcomes = created + rejected
    return MemberOnboardingStateResponse(
        status=status,
        selected_keys=list(selected),
        followed=[
            FollowOutcomeResponse(
                channel_key=outcome.channel_key, display_name=outcome.display_name,
                status=outcome.status, automation_id=outcome.automation_id,
            )
            for outcome in outcomes
        ],
    )


@app.post("/api/onboarding/skip", response_model=MemberOnboardingStateResponse)
def skip_onboarding(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MemberOnboardingStateResponse:
    service = MemberOnboardingService(db)
    with write_transaction(db, name="onboarding_skip"):
        status = service.skip(current_user)
    selected = MemberInterestService(db).list_for(current_user)
    return MemberOnboardingStateResponse(status=status, selected_keys=list(selected))


def _legacy_home(current_user: User, db: Session, snapshot: PopularSnapshot) -> MemberRecommendationSnapshotResponse:
    """ADR 0007's Home, unchanged: what the kill switch serves."""
    selected = MemberInterestService(db).list_for(current_user)
    # Follow keys are the member's casefolded channel display names (uploader match).
    followed_channel_keys = MemberFollowService(db).followed_channel_keys(current_user)
    result = MemberRecommendationPolicy(db).home(current_user, selected, snapshot, followed_channel_keys=followed_channel_keys)
    response = MemberRecommendationSnapshotResponse.model_validate(result)
    return response.model_copy(update={"items": _with_remote_artwork(response.items)})


@app.get("/api/discovery/home", response_model=MemberRecommendationSnapshotResponse, dependencies=[Depends(require_streaming("youtube"))])
def home_recommendations(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MemberRecommendationSnapshotResponse:
    enforce_rate_limit("popular", request, user_id=current_user.id)
    snapshot = popular_discovery.get_snapshot()
    if not reco.enabled(db):
        return _legacy_home(current_user, db, snapshot)
    # Picked for you from the member's pool and the uncut Popular items; state stays Popular's.
    policy = _reco_policy(db)
    source = uncut(snapshot, popular_discovery.candidates())
    items = policy.remote_entries(policy.home(current_user, source), source)
    response = MemberRecommendationSnapshotResponse.model_validate(uncut(snapshot, ()))
    return response.model_copy(update={"items": _with_remote_artwork(items)})


def _legacy_up_next(
    payload: UpNextRequest, current_user: User, db: Session, snapshot: PopularSnapshot,
) -> MemberRecommendationSnapshotResponse:
    """ADR 0007's Up Next behind the kill switch, without the provider search it used to run."""
    selected = MemberInterestService(db).list_for(current_user)
    context = PlaybackContext.for_source(
        source=payload.source, item_id=payload.source_id, webpage_url=payload.source_url,
        title=payload.title, uploader=payload.uploader, subject_keys=payload.category_keys,
    )
    result = MemberRecommendationPolicy(db, limit=payload.limit).up_next(
        current_user, selected, snapshot, current=context,
        followed_channel_keys=MemberFollowService(db).followed_channel_keys(current_user),
    )
    response = MemberRecommendationSnapshotResponse.model_validate(result)
    return response.model_copy(update={"items": _with_remote_artwork(response.items)})


@app.post("/api/discovery/up-next", response_model=MemberRecommendationSnapshotResponse, dependencies=[Depends(require_streaming("youtube"))])
def up_next_recommendations(
    payload: UpNextRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> MemberRecommendationSnapshotResponse:
    enforce_rate_limit("up_next", request, user_id=current_user.id)
    snapshot = popular_discovery.get_snapshot()
    if not reco.enabled(db):
        return _legacy_up_next(payload, current_user, db, snapshot)
    # No provider I/O; the channel being watched is read through peek_tab only.
    policy = _reco_policy(db)
    source = uncut(snapshot, popular_discovery.candidates())
    served = policy.up_next(current_user, source, payload)
    items = policy.remote_entries(served, source, channel_id=payload.channel_id)
    response = MemberRecommendationSnapshotResponse.model_validate(uncut(snapshot, ()))
    return response.model_copy(update={"items": _with_remote_artwork(items)})


def _channel_key_of(source: str | None, channel_id: str | None, channel_url: str | None, uploader: str | None) -> str | None:
    """The card's channel key, or None when it would not fit the 255-character column."""
    key = reco_channel_key(source, channel_id, channel_url, uploader)
    return key if key and len(key) <= 255 else None


@app.post("/api/discovery/suppressions", response_model=SuppressionResponse)
def create_suppression(
    payload: SuppressRecommendationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SuppressionResponse:
    """Not interested, Show fewer, Don't recommend, for this member only.

    The write is idempotent (Show fewer again restarts its 140 days). The same transaction writes one feedback event;
    after it commits the member's profile is rebuilt and every cached list of theirs is dropped, so the re-fetch the
    client makes brings in a replacement on every surface.
    """
    title = None
    if payload.scope == "title":
        if not payload.title_id:
            raise HTTPException(status_code=422, detail="A title suppression needs a title id.")
        title = titles.visible_title_or_404(db, payload.title_id, current_user)
    stable = _channel_key_of(payload.source, payload.channel_id, payload.channel_url, None)
    service = MemberSuppressionService(db)
    now = utcnow()
    try:
        with write_transaction(db, name="member_suppression_create"):
            if title is not None:
                record = service.suppress_title(current_user, title_id=title.id, name=title.name)
            elif payload.scope == "channel":
                record = service.suppress_channel(current_user, uploader=payload.uploader, source=payload.source, channel_key=stable)
            elif payload.scope == "fewer":
                record = service.suppress_fewer(current_user, uploader=payload.uploader, source=payload.source, channel_key=stable, at=now)
            else:
                record = service.suppress_item(
                    current_user, source=payload.source, item_id=payload.source_id,
                    webpage_url=payload.source_url, title=payload.title, uploader=payload.uploader, channel_key=stable,
                )
            RecoEventService(db).record_feedback(
                current_user.id, FEEDBACK_EVENTS[record.scope], target_kind="title" if title is not None else "remote",
                item_key=feedback_event_key(record, key=payload.key, source=payload.source, item_id=payload.source_id,
                                            webpage_url=payload.source_url),
                channel_key=None if title is not None else _channel_key_of(payload.source, payload.channel_id, payload.channel_url, payload.uploader),
                list_id=payload.list_id, now=now,
            )
            queue_member_changed(db, current_user.id, reco_refresher)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SuppressionResponse.model_validate(record)


@app.get("/api/discovery/suppressions", response_model=SuppressionListResponse)
def list_suppressions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SuppressionListResponse:
    """The member's four hidden lists for Settings, each restorable."""
    records = MemberSuppressionService(db).list_for(current_user)

    def scoped(scope: str) -> list[SuppressionResponse]:
        return [SuppressionResponse.model_validate(record) for record in records if record.scope == scope]

    return SuppressionListResponse(items=scoped("item"), channels=scoped("channel"), fewer=scoped("fewer"), titles=scoped("title"))


@app.delete("/api/discovery/suppressions/{suppression_id}", status_code=204)
def restore_suppression(
    suppression_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> Response:
    """Restore (remove) one of the member's suppressions and record a restore event.

    Restoring only makes the candidate eligible; it does not force it back into
    the current visible set. A missing or non-owned id is a no-op.
    """
    service = MemberSuppressionService(db)
    with write_transaction(db, name="member_suppression_restore"):
        record = service.get(current_user, suppression_id)
        if record is not None and service.restore(current_user, suppression_id):
            RecoEventService(db).record_feedback(
                current_user.id, "restore", target_kind="title" if record.scope == "title" else "remote",
                item_key=record.id, channel_key=record.channel_key, list_id=None, now=utcnow(),
            )
            queue_member_changed(db, current_user.id, reco_refresher)
    return Response(status_code=204)


def _channel_candidate_response(candidate) -> ChannelCandidateResponse:
    return ChannelCandidateResponse(
        channel_key=candidate.channel_key,
        source_url=candidate.source_url,
        display_name=candidate.display_name,
        source=candidate.source,
        source_label=candidate.source_label,
        # Register any provider artwork through the opaque-id boundary; a channel
        # with no avatar carries None so the UI can show an accessible fallback.
        artwork_url=_remote_artwork_url(candidate.artwork_url),
        category_keys=list(candidate.category_keys),
        following=candidate.following,
    )


@app.get("/api/discovery/channels", response_model=ChannelSuggestionsResponse, dependencies=[Depends(require_streaming("youtube"))])
def channel_suggestions(
    request: Request,
    keys: list[str] = Query(default_factory=list),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> ChannelSuggestionsResponse:
    enforce_rate_limit("popular", request, user_id=current_user.id)
    followed = MemberFollowService(db).existing_follow_identities(current_user)
    suggestions = ChannelDiscoveryService().suggestions_for_categories(
        popular_discovery.get_snapshot(), keys, followed_identities=followed
    )
    return ChannelSuggestionsResponse(
        categories=[
            CategoryChannelSuggestionsResponse(
                key=category.key, label=category.label, state=category.state,
                channels=[_channel_candidate_response(channel) for channel in category.channels],
            )
            for category in suggestions
        ]
    )


@app.post("/api/discovery/channels/search", response_model=ChannelCandidateListResponse, dependencies=[Depends(require_streaming("youtube"))])
def search_channels(
    payload: ChannelSearchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> ChannelCandidateListResponse:
    enforce_rate_limit("source_search", request, user_id=current_user.id)
    try:
        results = YtDlpService(db).youtube_search(payload.query, payload.limit)
    except SearchBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=400, detail=YtDlpService.explain_download_error(str(exc))) from exc
    # A channel search is derived from video results grouped by their channel;
    # only the channel address and name are used, never the raw query (ADR 0007).
    raw = [
        {
            "uploader": item.uploader, "channel_url": item.uploader_url, "channel_id": item.uploader_id,
            "availability": item.availability, "source": item.source,
        }
        for item in results.items
    ]
    followed = MemberFollowService(db).existing_follow_identities(current_user)
    candidates = ChannelDiscoveryService().channels_from_search(raw, followed_identities=followed)
    return ChannelCandidateListResponse(
        query=results.query, channels=[_channel_candidate_response(candidate) for candidate in candidates]
    )


@app.post("/api/source-search", response_model=YouTubeSearchResponse)
async def source_search(
    payload: YouTubeSearchRequest,
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)] = None,
) -> YouTubeSearchResponse:
    current_user = await run_in_threadpool(resolve_request_user_snapshot, request, credentials)
    enforce_rate_limit("source_search", request, user_id=current_user.id)
    await run_in_threadpool(_gate_snapshot, current_user, "open_search", beyond_follows=True)

    def run_search() -> YouTubeSearchResponse:
        with SessionLocal() as thread_db:
            return YtDlpService(thread_db).source_search(payload.query, payload.limit)

    try:
        response = _search_response_with_artwork(await asyncio.to_thread(run_search))
    except SearchBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=400, detail=YtDlpService.explain_download_error(str(exc))) from exc
    return response.model_copy(update={"items": await run_in_threadpool(_annotate_in_thread, current_user, response.items)})


@app.get("/api/jobs", response_model=JobPageResponse)
def list_jobs(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=JobManager.JOB_PAGE_DEFAULT_LIMIT),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> JobPageResponse:
    try:
        records, next_cursor = jobs.list_for_user(db, current_user, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JobPageResponse(
        items=[_job_response_with_artwork(JobManager.serialize(job)) for job in records],
        next_cursor=next_cursor,
    )


@app.post("/api/jobs", response_model=JobResponse, status_code=201)
def create_job(payload: JobCreateRequest, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> JobResponse:
    enforce_rate_limit("create_job", request, user_id=current_user.id)
    streaming_gate.require_download(db, current_user)
    streaming_gate.check_url(db, current_user, payload.source_url, channels=())
    _require_source_acquisition_capability(db, payload.source_url, payload.format_selection, current_user)
    try:
        job = jobs.enqueue(db, payload, current_user)
    except JobAdmissionError as exc:
        raise _admission_http_error(exc) from exc
    return _job_response_with_artwork(JobManager.serialize(job))


def _admission_http_error(exc: JobAdmissionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"reason": exc.reason, "message": str(exc)})


@app.post("/api/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> JobResponse:
    try:
        record = jobs.cancel(db, job_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _job_response_with_artwork(JobManager.serialize(record))


@app.post("/api/jobs/{job_id}/retry", response_model=JobResponse)
def retry_job(job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> JobResponse:
    streaming_gate.require_download(db, current_user)
    prior = db.get(DownloadJob, job_id)
    if prior is not None and prior.user_id == current_user.id:  # anything else is the 404 jobs.retry answers
        streaming_gate.check_url(db, current_user, prior.source_url, channels=())
    try:
        record = jobs.retry(db, job_id, current_user)
    except PublicSourcePolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JobAdmissionError as exc:
        raise _admission_http_error(exc) from exc
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _job_response_with_artwork(JobManager.serialize(record))


@app.delete("/api/jobs/completed")
def clear_completed_jobs(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> dict[str, int]:
    return {"deleted": jobs.clear_completed(db, current_user)}


@app.post("/api/acquisition-batches", response_model=AcquisitionBatchResponse, status_code=201)
def create_acquisition_batch(
    payload: AcquisitionBatchCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> AcquisitionBatchResponse:
    enforce_rate_limit("create_job", request, user_id=current_user.id)
    streaming_gate.require_download(db, current_user)
    streaming_gate.check_url(db, current_user, payload.source_url)
    source_policy = YtDlpService(db)
    try:
        source_url = source_policy.validate_source_url(payload.source_url)
        # One fresh extraction of the container settles every requested entry the
        # extraction can see; entries it cannot settle pass through to the worker's
        # per-entry enforcement. Client-supplied capability claims are never trusted.
        container = source_policy.preview(
            source_url, lazy_playlist=False, format_selection=payload.format_selection,
        )
        streaming_gate.check_url(
            db, current_user, container.webpage_url or source_url, extractor=container.extractor_key,
            channels=(container.raw.get("channel_url"), container.raw.get("uploader_url")),
        )
        by_id, by_url = _settled_entry_capabilities(container)
        entries = []
        for entry in payload.entries:
            validated_url = source_policy.validate_source_url(entry.source_url)
            capabilities, canonical_url = _resolve_settled_entry(by_id, by_url, entry.remote_id, validated_url)
            # Every entry is gated by its own kind; one the container did not list must itself be followed (followed_only).
            settled = (entry.remote_id or "") in by_id or YtDlpService.normalize_source_url(validated_url) in by_url
            streaming_gate.check_url(db, current_user, canonical_url, channels=None if settled else ())
            entries.append(SelectedSourceEntry(
                source_url=_enforce_acquire_capability(capabilities, canonical_url),
                extractor=entry.extractor,
                remote_id=entry.remote_id,
                title=entry.title,
                details=entry.model_dump(
                    include={"thumbnail", "uploader", "duration", "availability"},
                    exclude_none=True,
                ),
            ))
    except PublicSourcePolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError as exc:
        raise _download_error_http_exception(exc) from exc
    service = AcquisitionBatchService(db, jobs.acquisition)
    batch = service.queue_selected(
        user_id=current_user.id,
        source_url=source_url,
        source_title=payload.source_title,
        source_provenance=payload.source_provenance.model_dump(exclude_none=True),
        format_selection=payload.format_selection.model_dump(),
        output_profile=payload.output_profile.model_dump(),
        entries=entries,
    )
    return serialize_acquisition_batches(db, [batch])[0]


@app.get("/api/acquisition-batches", response_model=list[AcquisitionBatchResponse])
def list_acquisition_batches(
    limit: int = Query(default=50, ge=1, le=50),
    include_entries: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[AcquisitionBatchResponse]:
    batches = AcquisitionBatchService(db, jobs.acquisition).list_for_user(current_user.id, limit=limit)
    return serialize_acquisition_batches(db, batches, include_entries=include_entries)


@app.get("/api/acquisition-batches/{batch_id}", response_model=AcquisitionBatchResponse)
def get_acquisition_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> AcquisitionBatchResponse:
    service = AcquisitionBatchService(db, jobs.acquisition)
    try:
        batch = service.get_for_user(user_id=current_user.id, batch_id=batch_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Acquisition batch not found") from exc
    return serialize_acquisition_batches(db, [batch])[0]


@app.post(
    "/api/acquisition-batches/{batch_id}/entries/{entry_id}/retry",
    response_model=AcquisitionBatchResponse,
    status_code=201,
)
def retry_acquisition_batch_entry(
    batch_id: str,
    entry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> AcquisitionBatchResponse:
    service = AcquisitionBatchService(db, jobs.acquisition)
    try:
        entry = db.get(AcquisitionBatchEntry, entry_id)
        if entry is None or entry.user_id != current_user.id or entry.batch_id != batch_id:
            raise LookupError(entry_id)
        prior_batch = service.get_for_user(user_id=current_user.id, batch_id=batch_id)
        streaming_gate.require_download(db, current_user)
        streaming_gate.check_url(db, current_user, prior_batch.source_url or entry.source_url, channels=())
        _require_source_acquisition_capability(
            db, entry.source_url, FormatSelection.model_validate(prior_batch.format_selection or {}), current_user,
        )
        batch = service.retry_entry(user_id=current_user.id, entry_id=entry_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Acquisition entry not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_acquisition_batches(db, [batch])[0]


def _download_error_http_exception(exc: yt_dlp.utils.DownloadError) -> HTTPException:
    """Convert an extractor DownloadError into the shared structured 400 response."""

    category = YtDlpService.classify_download_error(str(exc))
    message = YtDlpService.explain_download_error(str(exc))
    detail = AcquisitionErrorDetail(category=category, message=message).model_dump() if category else message
    return HTTPException(status_code=400, detail=detail)


def _enforce_acquire_capability(
    capabilities: MediaSourceCapabilities | None, canonical_url: str,
) -> str:
    """Return the canonical acquire URL, or 409 when the source is not acquirable.

    A ``None`` capability means the single extraction could not settle this entry;
    it passes through here and the worker's per-entry enforcement stays the backstop.
    """

    if capabilities is None or capabilities.can_acquire:
        return canonical_url
    raise HTTPException(
        status_code=409,
        detail={
            "reason": capabilities.acquire_reason or "no_supported_transport",
            "message": "Lumina cannot acquire this source yet.",
        },
    )


def _require_source_acquisition_capability(
    db: Session,
    source_url: str,
    format_selection: FormatSelection,
    user: User | None = None,
) -> str:
    """Reinspect before queueing so direct requests cannot bypass action gating (nor, with ``user``, a streaming limit
    through a shortener or redirect that resolves to a blocked provider)."""

    try:
        preview = YtDlpService(db).preview(
            source_url,
            lazy_playlist=False,
            format_selection=format_selection,
        )
    except PublicSourcePolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError as exc:
        raise _download_error_http_exception(exc) from exc
    if user is not None:
        streaming_gate.check_url(db, user, preview.webpage_url or source_url, extractor=preview.extractor_key)
    return _enforce_acquire_capability(preview.capabilities, preview.webpage_url or source_url)


@dataclass(frozen=True)
class _LiveRecordIntent:
    canonical_url: str
    source_identity: str
    title: str | None
    extractor: str | None
    remote_id: str | None
    # Scheduling + from-start (issue #98). ``await_schedule`` marks an upcoming
    # broadcast (the recording then starts in the durable waiting phase, whether or
    # not a precise ``scheduled_start_at`` is known). ``from_start_supported`` is the
    # inspected support for a currently-live source; for an upcoming source it is
    # None here and the waiter confirms it at connect.
    await_schedule: bool = False
    scheduled_start_at: datetime | None = None
    from_start_supported: bool | None = None


def _resolve_live_record_intent(
    db: Session,
    source_url: str,
    format_selection: FormatSelection,
) -> _LiveRecordIntent:
    """Reinspect a source and confirm Lumina can record it now or on a schedule.

    Direct requests cannot bypass the capability gate: recording is offered only
    when the source is currently live and capturable through the guarded live-HLS
    transport (record from now), OR it is an upcoming broadcast Lumina can schedule
    and wait for (issue #98). The member-scoped idempotency identity is derived from
    the resolved provider identity so a duplicate submission — or a duplicate
    schedule — collapses onto the same recording and never creates a second waiter.
    """

    try:
        preview = YtDlpService(db).preview(
            source_url,
            lazy_playlist=False,
            format_selection=format_selection,
        )
    except PublicSourcePolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError as exc:
        raise _download_error_http_exception(exc) from exc
    capabilities = preview.capabilities
    can_record = bool(capabilities and capabilities.can_record)
    can_schedule = bool(capabilities and capabilities.can_schedule)
    if not can_record and not can_schedule:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": (capabilities.record_reason if capabilities else None) or "live_record_not_supported",
                "message": "Lumina cannot record or schedule this source.",
            },
        )
    raw = preview.raw or {}
    remote_id = raw.get("id") if isinstance(raw.get("id"), str) else None
    canonical_url = preview.webpage_url or source_url
    if remote_id:
        # source_identity keys the durable recording AND the member-scoped chat
        # asset that persist_captured_chat_asset merges into. For it to stay
        # per-broadcast (so a second recording of the same channel never folds the
        # prior stream's chat), remote_id must be per-broadcast, not channel-scoped.
        # Verified against the pinned yt-dlp Twitch extractor (2026.07.04):
        # TwitchStreamIE._real_extract sets a LIVE stream's ``id`` to the per-live
        # stream-session id (``stream['id']``), with the channel login carried as
        # ``display_id`` — so ``twitch:<stream_id>`` is per-broadcast. (VODs already
        # key by their own ``v<id>``; YouTube by the watch id; Kick live by the
        # per-broadcast ``livestream.slug``, verified for #142.)
        source_identity = f"{capabilities.provider}:{remote_id}"
    else:
        source_identity = f"url:{canonical_url}"
    # An upcoming source is scheduled (durable waiting phase) whenever it cannot be
    # recorded now; its from-start support is confirmed by the waiter at connect, so
    # it stays unknown here. A currently-live source records from now with its
    # inspected from-start support.
    await_schedule = can_schedule and not can_record
    scheduled_start_at = capabilities.scheduled_start if await_schedule else None
    from_start_supported = bool(capabilities.from_start_available) if can_record else None
    return _LiveRecordIntent(
        canonical_url=canonical_url,
        source_identity=source_identity,
        title=preview.title,
        extractor=preview.extractor or (raw.get("extractor") if isinstance(raw.get("extractor"), str) else None),
        remote_id=remote_id,
        await_schedule=await_schedule,
        scheduled_start_at=scheduled_start_at,
        from_start_supported=from_start_supported,
    )


def serialize_live_recording(recording: LiveRecording) -> LiveRecordingResponse:
    return LiveRecordingResponse(
        id=recording.id,
        source_url=recording.source_url,
        title=recording.title,
        extractor=recording.extractor,
        status=recording.status,
        stop_requested=recording.stop_requested,
        cancel_requested=recording.cancel_requested,
        media=LiveRecordingMediaOutput(
            status=recording.media_status,
            library_item_id=recording.library_item_id,
            failure_category=recording.media_failure_category,
            error=recording.media_error,
            end_reason=recording.media_end_reason,
        ),
        chat=LiveRecordingChatOutput(
            status=recording.chat_status,
            chat_asset_id=recording.chat_asset_id,
            failure_category=recording.chat_failure_category,
            error=recording.chat_error,
        ),
        start_intent=recording.start_intent,
        fallback_policy=recording.fallback_policy,
        scheduled_start_at=recording.scheduled_start_at,
        from_start_supported=recording.from_start_supported,
        capture_origin=recording.capture_origin,
        history=recording.history,
        awaiting_fallback_choice=recording.awaiting_fallback_choice,
        waiting_reason=recording.waiting_reason,
        resumed_after_restart=recording.attempts > 1,
        kept=recording.kept,
        max_runtime_seconds=settings.live_recording_max_runtime_seconds,
        max_bytes=settings.live_recording_max_disk_bytes,
        created_at=recording.created_at,
        started_at=recording.started_at,
        recording_started_at=recording.recording_started_at,
        finished_at=recording.finished_at,
    )


def _live_recording_or_404(db: Session, current_user: User, recording_id: str) -> LiveRecording:
    recording = LiveRecordingService(db).get(current_user, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Live recording not found")
    return recording


@app.post("/api/live-recordings", response_model=LiveRecordingResponse, status_code=201)
def create_live_recording(
    payload: LiveRecordingCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LiveRecordingResponse:
    enforce_rate_limit("create_job", request, user_id=current_user.id)
    streaming_gate.require_download(db, current_user)
    streaming_gate.check_url(db, current_user, payload.source_url, channels=(), live=True)
    intent = _resolve_live_record_intent(db, payload.source_url, payload.format_selection)
    streaming_gate.check_url(db, current_user, intent.canonical_url, live=True, extractor=intent.extractor)
    try:
        submission = live_recording_manager.submit(
            user_id=current_user.id,
            source_url=intent.canonical_url,
            source_identity=intent.source_identity,
            extractor=intent.extractor,
            remote_id=intent.remote_id,
            title=intent.title,
            format_selection=payload.format_selection.model_dump(),
            output_profile=payload.output_profile.model_dump(),
            await_schedule=intent.await_schedule,
            scheduled_start_at=intent.scheduled_start_at,
            start_intent=payload.start_intent,
            fallback_policy=payload.fallback_policy,
            from_start_supported=intent.from_start_supported,
        )
    except LiveRecordingLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    # A freshly created recording's worker is already launched by `submit()`; a
    # fresh re-read here would race it (it can finish before this handler reads
    # back its own write) and report a later status than what was just created.
    # Serialize the submission's own snapshot instead. A duplicate submission
    # starts no worker, so re-reading its (possibly further-along) existing row
    # is safe and intended.
    if submission.created:
        return serialize_live_recording(submission.recording)
    return serialize_live_recording(_live_recording_or_404(db, current_user, submission.recording_id))


@app.get("/api/live-recordings", response_model=LiveRecordingPageResponse)
def list_live_recordings(
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=LiveRecordingService.PAGE_DEFAULT_LIMIT),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LiveRecordingPageResponse:
    try:
        recordings, next_cursor = LiveRecordingService(db).list_for_user(current_user, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LiveRecordingPageResponse(
        items=[serialize_live_recording(r) for r in recordings],
        next_cursor=next_cursor,
    )


@app.get("/api/live-recordings/{recording_id}", response_model=LiveRecordingResponse)
def get_live_recording(
    recording_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LiveRecordingResponse:
    return serialize_live_recording(_live_recording_or_404(db, current_user, recording_id))


@app.post("/api/live-recordings/{recording_id}/stop", response_model=LiveRecordingResponse)
def stop_live_recording(
    recording_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LiveRecordingResponse:
    try:
        live_recording_manager.stop(current_user.id, recording_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Live recording not found") from exc
    return serialize_live_recording(_live_recording_or_404(db, current_user, recording_id))


@app.put("/api/live-recordings/{recording_id}/keep", response_model=LiveRecordingResponse)
def keep_live_recording(
    recording_id: str,
    payload: LiveRecordingKeepRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LiveRecordingResponse:
    recording = _live_recording_or_404(db, current_user, recording_id)
    with write_transaction(db, name="live_recording_keep"):
        recording.kept = payload.kept
    return serialize_live_recording(recording)


@app.post("/api/live-recordings/{recording_id}/cancel", response_model=LiveRecordingResponse)
def cancel_live_recording(
    recording_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LiveRecordingResponse:
    try:
        live_recording_manager.cancel(current_user.id, recording_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Live recording not found") from exc
    return serialize_live_recording(_live_recording_or_404(db, current_user, recording_id))


def _settled_entry_capabilities(
    container: PreviewResponse,
) -> tuple[dict[str, tuple[MediaSourceCapabilities | None, str | None]], dict[str, tuple[MediaSourceCapabilities | None, str | None]]]:
    """Index one container extraction so requested entries can be gated without re-extracting.

    Entries are keyed by their extractor id (matched against the request's
    ``remote_id``) and by normalized webpage URL. The container itself is keyed by
    URL so a single-source batch is settled by its own capability.
    """

    by_id: dict[str, tuple[MediaSourceCapabilities | None, str | None]] = {}
    by_url: dict[str, tuple[MediaSourceCapabilities | None, str | None]] = {}

    def register(identity: str | None, url: str | None, capabilities: MediaSourceCapabilities | None) -> None:
        if identity:
            by_id.setdefault(identity, (capabilities, url))
        if isinstance(url, str) and url:
            by_url.setdefault(YtDlpService.normalize_source_url(url), (capabilities, url))

    register(None, container.webpage_url, container.capabilities)
    for entry in container.entries:
        register(entry.id, entry.webpage_url, entry.capabilities)
    return by_id, by_url


def _resolve_settled_entry(
    by_id: dict[str, tuple[MediaSourceCapabilities | None, str | None]],
    by_url: dict[str, tuple[MediaSourceCapabilities | None, str | None]],
    remote_id: str | None,
    validated_url: str,
) -> tuple[MediaSourceCapabilities | None, str]:
    if remote_id and remote_id in by_id:
        capabilities, url = by_id[remote_id]
        return capabilities, url or validated_url
    match = by_url.get(YtDlpService.normalize_source_url(validated_url))
    if match is not None:
        capabilities, url = match
        return capabilities, url or validated_url
    return None, validated_url


def _collection_http_error(error: Exception) -> HTTPException:
    if isinstance(error, SmartCollectionEditError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, smart_collections.RuleError):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, (CollectionNotFoundError, InvisibleLibraryItemError, CollectionEntryNotFoundError)):
        return HTTPException(status_code=404, detail="Collection or entry not found")
    if isinstance(error, CollectionPermissionError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, CollectionConflictError):
        return HTTPException(status_code=409, detail="This collection changed elsewhere. Refresh and try again.")
    if isinstance(error, (DuplicateCollectionItemError, DuplicateCollectionNameError)):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, CollectionFullError):
        return HTTPException(status_code=422, detail="This collection is full.")
    return HTTPException(status_code=400, detail=str(error))


@app.post("/api/collections", response_model=HouseholdCollectionResponse, status_code=201)
async def create_household_collection(
    payload: HouseholdCollectionCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    try:
        # Re-parse the raw body's rules through parse_rule; the lax HouseholdCollectionCreateRequest
        # (a frozen body model) would coerce a stray bool value (e.g. `rating gte true`) to an int.
        raw_rules = (await request.json()).get("rules")
        rules = smart_collections.parse_rule(raw_rules).model_dump() if raw_rules is not None else None
        collection = HouseholdCollectionService(db).create(
            owner_user_id=current_user.id,
            name=payload.name,
            description=payload.description,
            visibility=payload.visibility,
            rules=rules,
        )
    except (ValueError, DuplicateCollectionNameError) as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.get("/api/collections", response_model=list[HouseholdCollectionResponse])
def list_household_collections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[HouseholdCollectionResponse]:
    service = HouseholdCollectionService(db)
    collections = service.shown_to(current_user, service.list_visible(current_user.id))
    # One grouped count instead of a count query per collection; same visibility rule as the serializer.
    counts = dict(
        db.query(HouseholdCollectionMembership.collection_id, func.count())
        .join(LibraryItem, LibraryItem.id == HouseholdCollectionMembership.library_item_id)
        .filter(
            HouseholdCollectionMembership.collection_id.in_([collection.id for collection in collections]),
            LibraryService.visible_predicate(current_user),
        )
        .group_by(HouseholdCollectionMembership.collection_id)
        .all()
    ) if collections else {}
    return [
        serialize_household_collection(db, collection, current_user, include_items=False, item_count=counts.get(collection.id, 0))
        for collection in collections
    ]


@app.get("/api/collections/{collection_id}", response_model=HouseholdCollectionResponse)
def get_household_collection(
    collection_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    try:
        collection = HouseholdCollectionService(db).get_visible(
            member_user_id=current_user.id, collection_id=collection_id,
        )
    except CollectionNotFoundError as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.put("/api/collections/{collection_id}/name", response_model=HouseholdCollectionResponse)
def rename_household_collection(
    collection_id: str,
    payload: HouseholdCollectionRenameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    try:
        collection = HouseholdCollectionService(db).rename(
            member_user_id=current_user.id, collection_id=collection_id, name=payload.name,
        )
    except (CollectionNotFoundError, CollectionPermissionError, DuplicateCollectionNameError, ValueError) as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.put("/api/collections/{collection_id}/visibility", response_model=HouseholdCollectionResponse)
def update_household_collection_visibility(
    collection_id: str,
    payload: HouseholdCollectionVisibilityRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    try:
        collection = HouseholdCollectionService(db).set_visibility(
            member_user_id=current_user.id,
            collection_id=collection_id,
            visibility=payload.visibility,
        )
    except (CollectionNotFoundError, CollectionPermissionError, ValueError) as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.put("/api/collections/{collection_id}/rules", response_model=HouseholdCollectionResponse)
async def set_household_collection_rules(
    collection_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    """Make an empty collection smart or change its rule; evaluated as each viewer (ADR 0003).

    The body is re-parsed strictly through ``parse_rule`` rather than declared as the frozen,
    lax ``SmartCollectionRule`` body model, which would silently coerce a stray bool value to an int.
    """
    try:
        rule = smart_collections.parse_rule(await request.json())
        collection = HouseholdCollectionService(db).set_rules(
            member_user_id=current_user.id, collection_id=collection_id,
            rules=rule.model_dump(),
        )
    except (CollectionNotFoundError, CollectionPermissionError, SmartCollectionEditError, ValueError) as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.delete("/api/collections/{collection_id}", status_code=204)
def delete_household_collection(
    collection_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> Response:
    try:
        HouseholdCollectionService(db).delete(member_user_id=current_user.id, collection_id=collection_id)
    except (CollectionNotFoundError, CollectionPermissionError) as exc:
        raise _collection_http_error(exc) from exc
    return Response(status_code=204)


@app.post("/api/collections/{collection_id}/items/{item_id}", response_model=HouseholdCollectionResponse)
def add_household_collection_item(
    collection_id: str,
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    service = HouseholdCollectionService(db)
    try:
        service.add_item(
            member_user_id=current_user.id,
            collection_id=collection_id,
            library_item_id=item_id,
        )
        collection = service.get_visible(member_user_id=current_user.id, collection_id=collection_id)
    except (CollectionNotFoundError, CollectionPermissionError, InvisibleLibraryItemError, DuplicateCollectionItemError, CollectionFullError, SmartCollectionEditError) as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.post("/api/collections/{collection_id}/remote-items", response_model=HouseholdCollectionResponse, status_code=201)
def add_household_collection_remote_item(
    collection_id: str,
    payload: CollectionRemoteRefRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    """Adds a public source ref (S45 mixed collections). Never admits a download; Save to vault is a separate action."""
    service = HouseholdCollectionService(db)
    remote = RemoteRef(
        provider=payload.provider, remote_id=payload.remote_id, url=payload.url, title=payload.title,
        uploader=payload.uploader, artwork_url=payload.artwork_url, duration=payload.duration,
    )
    try:
        service.add_remote(member_user_id=current_user.id, collection_id=collection_id, remote=remote, expected_revision=payload.expected_revision)
        collection = service.get_visible(member_user_id=current_user.id, collection_id=collection_id)
    except (CollectionNotFoundError, CollectionPermissionError, DuplicateCollectionItemError, CollectionFullError, CollectionConflictError, ValueError) as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.delete("/api/collections/{collection_id}/entries/{entry_id}", response_model=HouseholdCollectionResponse)
def remove_household_collection_entry(
    collection_id: str,
    entry_id: str,
    expected_revision: int | None = Query(default=None, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    """Removes any entry (library or remote) by membership id. Idempotent; never deletes the underlying artifact."""
    service = HouseholdCollectionService(db)
    try:
        service.remove_entry(member_user_id=current_user.id, collection_id=collection_id, entry_id=entry_id, expected_revision=expected_revision)
        collection = service.get_visible(member_user_id=current_user.id, collection_id=collection_id)
    except (CollectionNotFoundError, CollectionPermissionError, CollectionConflictError) as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.patch("/api/collections/{collection_id}/entries/{entry_id}", response_model=HouseholdCollectionResponse)
def move_household_collection_entry(
    collection_id: str,
    entry_id: str,
    payload: CollectionEntryMoveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> HouseholdCollectionResponse:
    """Reorders one entry to a new position; a stale expected_revision conflicts rather than losing the other client's order."""
    service = HouseholdCollectionService(db)
    try:
        service.move(member_user_id=current_user.id, collection_id=collection_id, entry_id=entry_id, index=payload.position, expected_revision=payload.expected_revision)
        collection = service.get_visible(member_user_id=current_user.id, collection_id=collection_id)
    except (CollectionNotFoundError, CollectionPermissionError, CollectionEntryNotFoundError, CollectionConflictError) as exc:
        raise _collection_http_error(exc) from exc
    return serialize_household_collection(db, collection, current_user, include_items=True)


@app.get("/api/library", response_model=LibraryPageResponse)
def list_library(
    search: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=LIBRARY_PAGE_DEFAULT_LIMIT),
    kind: LibraryViewKind | None = Query(default=None),
    source: str | None = Query(default=None, max_length=64),
    group: str | None = Query(default=None, max_length=1000),
    status: Literal["available", "missing"] | None = Query(default=None),
    sort: Literal["recent", "title"] = Query(default="recent"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LibraryPageResponse:
    service = LibraryService(db)
    bounded_limit = max(1, min(LIBRARY_PAGE_MAX_LIMIT, limit))
    try:
        if search and search.strip():
            # A blank-after-strip query is not a search: fall through to the
            # bounded keyset list path rather than the FTS search path (whose
            # empty-query fallback would load the whole library).
            # Indexed FTS results are paged by opaque offset so every page keeps
            # the same {items, next_cursor} shape.
            offset = 0
            if cursor is not None:
                offset = decode_library_cursor(cursor).get("o")
                if not isinstance(offset, int) or offset < 0:
                    raise ValueError("Invalid library page cursor")
            items, has_more = service.search_page(search, current_user, offset=offset, limit=bounded_limit)
            next_cursor = encode_library_cursor({"o": offset + bounded_limit}) if has_more else None
        else:
            items, next_cursor = service.list_items_page(
                current_user, cursor=cursor, limit=bounded_limit, kind=kind, source=source, group=group, status=status, sort=sort
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    service.prime_owners(items)
    service.prime_media_states(items)
    responses = [service.serialize(item, current_user, summary=True) for item in items]
    if responses:  # The member's progress on the page, in one query
        progress = {row.item_id: row for row in db.scalars(select(PlaybackProgress).where(
            PlaybackProgress.user_id == current_user.id, PlaybackProgress.item_id.in_([item.id for item in items]),
        ))}
        for response in responses:
            if (row := progress.get(response.id)) is not None:
                response.progress = ItemProgress(position_seconds=row.position_seconds or 0, duration_seconds=row.duration_seconds,
                                                 completed=bool(row.completed))
    return LibraryPageResponse(items=responses, next_cursor=next_cursor)


@app.get("/api/library/groups", response_model=list[LibraryGroupResponse])
def list_library_groups(
    kind: Literal["episode", "music"],
    source: str | None = Query(default=None, max_length=64),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[LibraryGroupResponse]:
    return LibraryService(db).list_groups(current_user, kind=kind, source=source)


@app.get("/api/library/channels", response_model=list[LibraryChannelResponse])
def list_library_channels(
    source: Literal["youtube"] = Query(default="youtube"),
    sort: Literal["recent", "name"] = Query(default="recent"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[LibraryChannelResponse]:
    """Saved YouTube grouped by channel, as Infuse groups it."""
    return LibraryService(db).list_channels(current_user, source=source, sort=sort, artwork_url=_remote_artwork_url)


# The title and moment kinds each wall's search asks for; the title's category
# then decides (a moment belongs to its movie's or episode's category).
SEARCH_SCOPE_TYPES = {
    "movies": ("movie", "moment"), "shows": ("series", "episode", "moment"), "anime": ("movie", "series", "episode", "moment"),
}


@app.get("/api/search", response_model=SearchResultResponse)
def search_library(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=12, ge=1, le=30),
    scope: SearchScope | None = None,  # a wall's "Find the one where…"
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SearchResultResponse:
    service = LibraryService(db)
    types = SEARCH_SCOPE_TYPES[scope] if scope else None
    discovery = local_discovery.search(db, current_user, q, limit=limit, types=types)
    service.prime_owners(match.library_item for match in discovery.matches if match.library_item)
    title_ids = [title_id for title_id in map(match_title_id, discovery.matches) if title_id]
    summaries = {summary.id: summary for summary in title_summaries(db, current_user, title_ids)} if title_ids else {}
    blocked = set(streaming_gate.blocked_kinds(db, current_user))  # a blocked kind's followed sources are left out, not an error
    found = tuple(match for match in discovery.matches
                  if not (match.automation and streaming_gate.url_kind(match.automation.source_url) in blocked))
    if scope is not None:  # a match belongs to the wall when its title (a moment's movie or episode) is in the wall's category
        found = tuple(match for match in found if getattr(summaries.get(match_title_id(match) or ""), "category", None) == scope)
    matches = [
        LocalSearchMatchResponse(
            kind=match.kind,
            id=match.record_id,
            title=match.title,
            subtitle=match.subtitle,
            score=match.score,
            lexical_score=match.lexical_score,
            semantic_score=match.semantic_score,
            match_mode=match.match_mode,
            item=service.serialize(match.library_item, current_user, summary=True) if match.library_item else None,
            source_url=match.automation.source_url if match.automation else None,
            source_type=match.automation.source_type if match.automation else None,
            title_id=match_title_id(match),
            start_ms=match.start_ms,
            media_title=summaries.get(match_title_id(match) or ""),
        )
        for match in found
    ]
    return SearchResultResponse(
        query=discovery.query,
        mode=discovery.mode,
        matches=matches,
        items=[match.item for match in matches if match.item is not None],
        index_generation=discovery.index_generation,
    )


@app.get("/api/library/{item_id}", response_model=LibraryItemResponse)
def get_library_item(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> LibraryItemResponse:
    service = LibraryService(db)
    item = visible_item_or_404(db, item_id, current_user)
    service.prime_media_states([item])
    return service.serialize(item, current_user)


@app.get("/api/playback/continue", response_model=list[PlaybackProgressResponse])
def list_continue_watching(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[PlaybackProgressResponse]:
    service = PlaybackProgressService(db)
    return service.serialize_continue_watching(service.list_continue_watching(current_user), current_user)


@app.get("/api/playback/remote/{source_identity:path}", response_model=RemotePlaybackProgressResponse | None)
def get_remote_playback_progress(
    source_identity: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> RemotePlaybackProgressResponse | None:
    service = RemotePlaybackProgressService(db)
    try:
        progress = service.get(source_identity, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.serialize(progress) if progress is not None else None


@app.put("/api/playback/remote/{source_identity:path}", response_model=RemotePlaybackProgressResponse)
def update_remote_playback_progress(
    source_identity: str,
    payload: RemotePlaybackProgressUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> RemotePlaybackProgressResponse:
    enforce_rate_limit("remote_playback_mutation", request, user_id=current_user.id)
    service = RemotePlaybackProgressService(db)
    try:
        with write_transaction(db, name="remote_playback_update"):
            progress = service.update(source_identity, payload, current_user)
            serialized = service.serialize(progress)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    screen_time.heartbeat(db, current_user)
    return serialized


@app.delete("/api/playback/remote/{source_identity:path}", response_model=RemotePlaybackProgressResponse)
def clear_remote_playback_progress(
    source_identity: str,
    request: Request,
    checkpoint_client_id: str = Query(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$",
    ),
    checkpoint_sequence: int = Query(ge=1, le=9_007_199_254_740_991),
    expected_revision: int = Query(ge=0, le=9_007_199_254_740_991),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> RemotePlaybackProgressResponse:
    enforce_rate_limit("remote_playback_mutation", request, user_id=current_user.id)
    service = RemotePlaybackProgressService(db)
    try:
        with write_transaction(db, name="remote_playback_clear"):
            progress = service.clear(
                source_identity,
                current_user,
                checkpoint_client_id,
                checkpoint_sequence,
                expected_revision,
            )
            serialized = service.serialize(progress)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return serialized


@app.get("/api/live-chat")
def read_live_chat(
    source_url: str, after: int = 0, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Current live chat for a playing stream: normalized events after ``after``, from one shared per-source poll."""
    streaming_gate.check_url(db, current_user, source_url, live=True)
    return live_chat_viewer.read(source_url, current_user.id, after=max(0, after))


@app.get("/api/chat-replay/{source_identity:path}", response_model=ChatReplayAssetResponse | None)
def get_chat_replay_asset(
    source_identity: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> ChatReplayAssetResponse | None:
    service = TimedChatAssetService(db)
    try:
        asset = service.get(source_identity, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.serialize(asset) if asset is not None else None


@app.post("/api/chat-replay/{source_identity:path}", response_model=ChatReplayAssetResponse)
async def load_chat_replay_asset(
    source_identity: str,
    payload: ChatReplayLoadRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> ChatReplayAssetResponse:
    # A deliberate member Load action. Anonymous callers are rejected before any
    # build work, and the whole flow is member-scoped to the requesting member.
    enforce_rate_limit("chat_replay_load", request, user_id=current_user.id)
    await run_in_threadpool(_gate_snapshot, current_user, url=payload.source_url, channels=())

    def claim_build() -> BuildTicket:
        with SessionLocal() as db:
            service = TimedChatAssetService(db)
            try:
                with write_transaction(db, name="chat_replay_begin"):
                    return service.begin_build(
                        source_identity,
                        payload.source_url,
                        current_user,
                        refresh=payload.refresh,
                    )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except OperationalError as exc:
                raise HTTPException(status_code=503, detail="Chat replay is briefly busy. Please try again.") from exc

    ticket = await asyncio.to_thread(claim_build)
    if ticket.build_id is None:
        # Nothing to build: a fresh asset already exists or a build is in flight.
        return ticket.response

    def run_and_store() -> ChatReplayAssetResponse:
        with SessionLocal() as db:
            fetch = YtDlpService(db).download_replay_chat(payload.source_url)
        result = normalize_youtube_replay_chat(fetch.lines, budget=TimedChatBudget())
        with SessionLocal() as db:
            service = TimedChatAssetService(db)
            try:
                with write_transaction(db, name="chat_replay_finalize"):
                    asset = service.store_result(
                        ticket.canonical,
                        payload.source_url,
                        current_user,
                        ticket.build_id,
                        fetch,
                        result,
                    )
                    return service.serialize(asset)
            except OperationalError as exc:
                raise HTTPException(status_code=503, detail="Chat replay is briefly busy. Please try again.") from exc

    return await asyncio.to_thread(run_and_store)


@app.get("/api/library/{item_id}/playback", response_model=PlaybackProgressResponse | None)
def get_playback_progress(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> PlaybackProgressResponse | None:
    service = PlaybackProgressService(db)
    try:
        progress = service.get(item_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service.serialize(progress, current_user) if progress is not None else None


@app.put("/api/library/{item_id}/playback", response_model=PlaybackProgressResponse)
def update_playback_progress(
    item_id: str,
    payload: PlaybackProgressUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> PlaybackProgressResponse:
    service = PlaybackProgressService(db)
    try:
        with write_transaction(db, name="playback_update"):
            progress = service.update(item_id, payload, current_user)
            serialized = service.serialize(progress, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    activity.touch(current_user.id, item_id, getattr(request.state, "connected_app_id", None) or "web", position=payload.position_seconds, duration=payload.duration_seconds)
    screen_time.heartbeat(db, current_user)
    return serialized


@app.delete("/api/library/{item_id}/playback", status_code=204)
def clear_playback_progress(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> None:
    try:
        with write_transaction(db, name="playback_clear"):
            PlaybackProgressService(db).clear(item_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/library/refresh", response_model=LibraryPageResponse)
def refresh_library(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> LibraryPageResponse:
    service = LibraryService(db, events)
    # A refresh nudges the shared resumable sweep — at most two bounded,
    # seam-managed batches — and returns the first library page immediately in
    # the same shape GET /api/library serves. The response therefore reflects
    # partially reconciled state; the 60 s maintenance loop completes the
    # sweep. The shared cursor is never reset here, so concurrent member
    # refreshes continue each other's progress instead of restarting it.
    for _ in range(2):
        _, completed = service.reconcile_files_step()
        if completed:
            break
    items, next_cursor = service.list_items_page(current_user)
    service.prime_owners(items)
    return LibraryPageResponse(
        items=[service.serialize(item, current_user, summary=True) for item in items],
        next_cursor=next_cursor,
    )


@app.get("/api/library/{item_id}/media")
@app.head("/api/library/{item_id}/media")
def stream_library_item(
    item_id: str,
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)] = None,
) -> MediaFileResponse:
    current_user = resolve_request_user_snapshot(request, credentials=credentials)
    with SessionLocal() as db:
        service = LibraryService(db)
        item = visible_item_or_404(db, item_id, current_user)
        try:
            path = service.resolve_media_path(item)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        screen_time.enforce(db, current_user)  # every Range request: at most one real check a minute
    device = getattr(request.state, "connected_app_id", None) or "web"
    activity.guard(current_user.id, item.id, device)
    if request.method == "GET":
        activity.touch(current_user.id, item.id, device)
    return MediaFileResponse(path, filename=path.name, content_disposition_type="inline")


@app.post("/api/library/{item_id}/delete-file", response_model=LibraryItemResponse)
def delete_library_item_file(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> LibraryItemResponse:
    service = LibraryService(db, events)
    item = visible_item_or_404(db, item_id, current_user)
    if not service.can_manage(item, current_user):
        raise HTTPException(status_code=403, detail="Only the owner or an admin can delete this file")
    try:
        updated = service.delete_file(item)
    except (FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    service.prime_media_states([updated])
    return service.serialize(updated, current_user)


@app.post("/api/library/{item_id}/restore-file", response_model=LibraryItemResponse)
def restore_library_item_file(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> LibraryItemResponse:
    service = LibraryService(db, events)
    item = visible_item_or_404(db, item_id, current_user)
    if not service.can_manage(item, current_user):
        raise HTTPException(status_code=403, detail="Only the owner or an admin can restore this file")
    try:
        restored = service.restore_file(item)
    except (FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    service.prime_media_states([restored])
    return service.serialize(restored, current_user)


@app.put("/api/library/{item_id}/visibility", response_model=LibraryItemResponse)
def update_library_item_visibility(
    item_id: str,
    payload: LibraryItemVisibilityUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LibraryItemResponse:
    service = LibraryService(db, events)
    item = visible_item_or_404(db, item_id, current_user)
    try:
        updated = service.set_visibility(item, payload.visibility, current_user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.serialize(updated)


def _note_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc))


@app.get("/api/library/{item_id}/notes", response_model=list[LibraryNoteResponse])
def list_library_notes(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[LibraryNoteResponse]:
    service = MediaNotesService(db)
    try:
        notes = service.list_notes(item_id, current_user)
    except ValueError as exc:
        raise _note_error(exc) from exc
    return [service.serialize(note, current_user) for note in notes]


@app.post("/api/library/{item_id}/notes", response_model=LibraryNoteResponse, status_code=201)
def add_library_note(
    item_id: str,
    payload: LibraryNoteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LibraryNoteResponse:
    service = MediaNotesService(db)
    try:
        note = service.add_note(item_id, payload.body, payload.visibility, current_user, payload.timestamp_ms)
    except ValueError as exc:
        raise _note_error(exc) from exc
    return service.serialize(note, current_user)


@app.put("/api/library/notes/{note_id}", response_model=LibraryNoteResponse)
def update_library_note(
    note_id: str,
    payload: LibraryNoteUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LibraryNoteResponse:
    service = MediaNotesService(db)
    try:
        note = service.update_note(note_id, payload.body, payload.visibility, current_user)
    except (PermissionError, ValueError) as exc:
        raise _note_error(exc) from exc
    return service.serialize(note, current_user)


@app.delete("/api/library/notes/{note_id}", status_code=204)
def delete_library_note(
    note_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> None:
    try:
        MediaNotesService(db).delete_note(note_id, current_user)
    except (PermissionError, ValueError) as exc:
        raise _note_error(exc) from exc


@app.get("/api/library/{item_id}/tags", response_model=list[LibraryTagResponse])
def list_library_tags(item_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[LibraryTagResponse]:
    service = LibraryService(db)
    visible_item_or_404(db, item_id, current_user)
    return [service.serialize_tag(tag) for tag in service.list_tags(item_id, current_user)]


@app.post("/api/library/{item_id}/tags", response_model=LibraryTagResponse, status_code=201)
def add_library_tag(
    item_id: str,
    payload: LibraryTagCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> LibraryTagResponse:
    service = LibraryService(db)
    visible_item_or_404(db, item_id, current_user)
    try:
        tag = service.add_tag(item_id, payload.tag, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.serialize_tag(tag)


@app.delete("/api/library/{item_id}/tags/{tag_value}", status_code=204)
def delete_library_tag(item_id: str, tag_value: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> None:
    service = LibraryService(db)
    visible_item_or_404(db, item_id, current_user)
    try:
        service.delete_tag(item_id, tag_value, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/automations", response_model=list[SourceAutomationResponse])
def list_automations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> list[SourceAutomationResponse]:
    service = _source_automation_service(db)
    return _annotated_follows(db, current_user, [service.serialize(automation) for automation in service.list_automations(current_user)])


@app.post("/api/automations", response_model=SourceAutomationResponse, status_code=201)
def create_automation(
    payload: SourceAutomationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SourceAutomationResponse:
    streaming_gate.check_url(db, current_user, payload.source_url, beyond_follows=True)
    if payload.auto_download:
        streaming_gate.require_download(db, current_user)
    service = _source_automation_service(db)
    try:
        automation = service.create_automation(payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if automation.source_type == "channel":
        # Following a channel removes its Show fewer and Don't recommend rows.
        with write_transaction(db, name="follow_clears_channel_feedback"):
            MemberSuppressionService(db).clear_channel_feedback(
                current_user, channel_key=_channel_key_of(None, None, automation.source_url, None), name=automation.label,
            )
            queue_member_changed(db, current_user.id, reco_refresher)
    return service.serialize(automation)


@app.post("/api/follows/refresh", response_model=list[SourceAutomationResponse])
def refresh_follows(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> list[SourceAutomationResponse]:
    enforce_rate_limit("popular", request, user_id=current_user.id)
    service = _source_automation_service(db)
    with write_transaction(db, name="follow_refresh_request"):
        follows = service.request_follow_refresh(current_user)
    return _annotated_follows(db, current_user, [service.serialize(automation) for automation in follows])


@app.patch("/api/automations/{automation_id}/auto-download", response_model=SourceAutomationResponse)
def set_automation_auto_download(
    automation_id: str,
    payload: SourceAutomationAutoDownloadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> SourceAutomationResponse:
    if payload.enabled:
        streaming_gate.require_download(db, current_user)
    service = _source_automation_service(db)
    try:
        automation = service.set_auto_download(automation_id, payload.enabled, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)) from exc
    return service.serialize(automation)


def _gate_automation(db: Session, user: User, service: SourceAutomationService, automation_id: str) -> None:
    """A follow/automation whose kind is now blocked neither runs nor resumes (pausing or deleting it stays allowed)."""
    if (automation := service.get_automation(automation_id, user)) is not None:
        streaming_gate.check_url(db, user, automation.source_url)


@app.post("/api/automations/{automation_id}/run", response_model=AutomationRunResponse)
def run_automation(
    automation_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> AutomationRunResponse:
    enforce_rate_limit("source_automation_run", request, user_id=current_user.id)
    service = _source_automation_service(db)
    _gate_automation(db, current_user, service, automation_id)
    try:
        run = service.run_automation(automation_id, current_user)
    except AutomationAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PublicSourcePolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Automation run failed.") from exc
    return service.serialize_run(run, include_decisions=True)


@app.post("/api/automations/{automation_id}/pause", response_model=SourceAutomationResponse)
def pause_automation(automation_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> SourceAutomationResponse:
    service = _source_automation_service(db)
    try:
        automation = service.pause_automation(automation_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service.serialize(automation)


@app.post("/api/automations/{automation_id}/resume", response_model=SourceAutomationResponse)
def resume_automation(automation_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> SourceAutomationResponse:
    service = _source_automation_service(db)
    _gate_automation(db, current_user, service, automation_id)
    try:
        automation = service.resume_automation(automation_id, current_user)
    except PublicSourcePolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service.serialize(automation)


@app.delete("/api/automations/{automation_id}", status_code=204)
def delete_automation(automation_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")) -> None:
    service = _source_automation_service(db)
    try:
        service.delete_automation(automation_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/admin/users", response_model=list[AdminUserResponse])
def list_users(current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> list[AdminUserResponse]:
    del current_user
    service = UserService(db)
    users = service.list_users()
    extra = member_access.admin_view(db, [user.id for user in users])  # member access spec: access + today's screen time
    rows = []
    for user in users:
        access = extra[user.id]["access"]
        rows.append(AdminUserResponse(
            **service.serialize_user(user).model_dump(),
            access=None if access is None else {**access, "restricted": True},
            screen_time_today_seconds=extra[user.id]["screen_time_today_seconds"],
            sign_in_locked=rate_limiter.is_full(ACCOUNT_LOCK_BUCKET, account_lock_key(user.username)),
        ))
    return rows


@app.post("/api/admin/users/{user_id}/unlock", status_code=204)
def unlock_user_sign_in(user_id: str, current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> Response:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    rate_limiter.reset(ACCOUNT_LOCK_BUCKET, account_lock_key(user.username))
    audit_log.info("login.unlock actor=%s user=%s", current_user.id, user.id)
    return Response(status_code=204)


@app.post("/api/admin/invitations", response_model=InvitationResponse, status_code=201)
def create_invitation(
    payload: InvitationCreateRequest,
    current_user: User = Depends(get_owner_session_user),
    db: Session = Depends(get_db, scope="function"),
) -> InvitationResponse:
    record, token = UserService(db).issue_account_token(
        current_user, "invite", role=payload.role, expires_in_hours=payload.expires_in_hours
    )
    # The secret rides in the URL fragment: never sent to the server, logs or Referer.
    return InvitationResponse(
        id=record.id,
        role=payload.role,
        expires_at=record.expires_at,
        invitation_url=f"{settings.resolved_frontend_public_url}/#invite={token}",
    )


@app.get("/api/admin/invitations", response_model=list[InvitationSummaryResponse])
def list_invitations(current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> list[InvitationSummaryResponse]:
    del current_user
    return UserService(db).list_invitations()


@app.post("/api/admin/invitations/{invitation_id}/revoke", response_model=InvitationSummaryResponse)
def revoke_invitation(invitation_id: str, current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> InvitationSummaryResponse:
    try:
        return UserService(db).revoke_invitation(current_user, invitation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Invitation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/invitations/redeem", response_model=UserResponse, status_code=201, dependencies=[Depends(rate_limit_dependency("account_token_redeem"))])
def redeem_invitation(payload: InvitationRedeemRequest, db: Session = Depends(get_db, scope="function")) -> UserResponse:
    service = UserService(db)
    try:
        user = service.redeem_invitation(payload)
    except InvalidAccountToken as exc:
        raise HTTPException(status_code=410, detail="This invitation link is invalid, already used, or expired.") from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.serialize_user(user)


@app.put("/api/admin/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    current_user: User = Depends(get_owner_session_user),
    db: Session = Depends(get_db, scope="function"),
) -> UserResponse:
    service = UserService(db)
    try:
        user = service.manage_user(current_user, user_id, payload)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 409, detail=str(exc)) from exc
    if payload.is_active is False:
        # Sessions are already revoked; stop the member's in-flight downloads too (history is kept).
        active = db.query(DownloadJob.id).filter(DownloadJob.user_id == user.id, DownloadJob.status.in_(["queued", "running", "postprocessing"]))
        for (job_id,) in active.all():
            jobs.cancel(db, job_id, user)
        # ...and finish their live recordings (what was captured is kept, privately).
        recordings = db.query(LiveRecording.id).filter(LiveRecording.user_id == user.id, LiveRecording.status.notin_(tuple(TERMINAL_STATUSES)))
        for (recording_id,) in recordings.all():
            live_recording_manager.stop(user.id, recording_id, reason="owner_disabled")
    return service.serialize_user(user)


@app.post("/api/admin/users/{user_id}/reset-link", response_model=PasswordResetLinkResponse, status_code=201)
def create_password_reset_link(
    user_id: str,
    current_user: User = Depends(get_owner_session_user),
    db: Session = Depends(get_db, scope="function"),
) -> PasswordResetLinkResponse:
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    record, token = UserService(db).issue_account_token(current_user, "reset", user_id=user_id, expires_in_hours=RESET_LINK_HOURS)
    return PasswordResetLinkResponse(expires_at=record.expires_at, reset_url=reset_link_url(token))


@app.get("/api/admin/settings", response_model=AppSettingsResponse)
def get_admin_settings(current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> AppSettingsResponse:
    del current_user
    service = UserService(db)
    record = YtDlpService(db).get_app_settings()
    return service.serialize_settings(record)


@app.put("/api/admin/settings", response_model=AppSettingsResponse)
def update_admin_settings(
    payload: AppSettingsUpdateRequest,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db, scope="function"),
) -> AppSettingsResponse:
    if payload.require_owner_two_factor and not two_factor.enabled(current_user):
        raise HTTPException(status_code=409, detail="Turn on two-step verification for yourself first (Settings → You).")
    service = UserService(db)
    record = YtDlpService(db).ensure_app_settings()
    before = bool(record.require_owner_two_factor)
    updated = service.update_settings(record, payload)
    if bool(updated.require_owner_two_factor) != before:
        audit_log.info("two_factor.require_owners actor=%s value=%s", current_user.id, updated.require_owner_two_factor)
    return service.serialize_settings(updated)


@app.post("/api/admin/settings/webhook/test", response_model=WebhookTestResponse)
def test_admin_webhook(request: Request, current_user: User = Depends(get_admin_user), db: Session = Depends(get_db, scope="function")) -> WebhookTestResponse:
    enforce_rate_limit("webhook_test", request, user_id=current_user.id)
    del current_user
    ok, message = WebhookService(db).send_test()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return WebhookTestResponse(ok=ok, message=message)


frontend_dist = resolve_frontend_dist()
if frontend_dist is not None:
    app.mount("/", SpaStaticFiles(directory=frontend_dist, html=True), name="frontend")
