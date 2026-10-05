from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.media_schemas import ItemProgress, RecoAnnotation, SmartCollectionRule, TitleSummary
from app.services.network_policy import check_extra_ports

JobStatus = Literal["draft", "previewing", "ready", "queued", "running", "postprocessing", "completed", "failed", "cancelled", "interrupted"]
AcquisitionErrorCategory = Literal["format_unavailable", "sign_in_required", "region_blocked", "removed", "rate_limited", "unsupported"]
UserRole = Literal["admin", "viewer"]
NoteVisibility = Literal["private", "household"]
NOTE_BODY_MAX_CHARS = 4000
LibraryItemVisibility = Literal["private", "shared"]
CollectionVisibility = Literal["private", "shared"]
AcquisitionBatchStatus = Literal["dispatching", "queued", "partial", "failed", "completed", "duplicate"]
AcquisitionEntryStatus = Literal["pending", "dispatching", "queued", "running", "completed", "failed", "cancelled", "duplicate"]
SearchSource = Literal["youtube", "soundcloud", "twitch", "kick"]
SourceAutomationType = Literal["playlist", "channel", "search", "generic_url"]
AutomationMediaKind = Literal["any", "video", "audio"]
AutomationDuplicatePolicy = Literal["skip_same_source", "allow_media_variants"]
OUTPUT_TEMPLATE_FIELD_PATTERN = re.compile(r"%\(([^)]+)\)s")
SAFE_OUTPUT_TEMPLATE_FIELDS = {
    "album",
    "artist",
    "channel",
    "ext",
    "id",
    "playlist",
    "playlist_title",
    "title",
    "track",
    "uploader",
}


class LivenessResponse(BaseModel):
    status: str


class HealthResponse(BaseModel):
    """Member-visible runtime health: no paths, versions of tools, or internals."""

    status: Literal["ok", "degraded"]
    version: str


class BootstrapStatusResponse(BaseModel):
    needs_setup: bool


class BootstrapAdminCreateRequest(BaseModel):
    username: str
    password: str
    display_name: str | None = None


class SessionLoginRequest(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(max_length=256)
    remember_on_device: bool = False  # Join this browser's "Who's watching?" ring


class TwoFactorSignInRequest(BaseModel):
    challenge: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=16)
    recovery_code: str | None = Field(default=None, max_length=40)
    trust_device: bool = False


class TwoFactorStatusResponse(BaseModel):
    enabled: bool
    recovery_codes_left: int
    required: bool  # the household requires it for this vault owner


class TwoFactorPasswordRequest(BaseModel):
    password: str = Field(max_length=256)


class TwoFactorSetupResponse(BaseModel):
    secret: str  # base32, for typing into an authenticator by hand
    otpauth_uri: str
    qr_size: int  # SVG viewBox side, quiet zone included
    qr_path: str  # SVG path data of the dark modules


class TwoFactorCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class TwoFactorConfirmRequest(BaseModel):
    password: str = Field(max_length=256)
    code: str = Field(min_length=1, max_length=40)  # a 6-digit code or a recovery code


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


class FormatSelection(BaseModel):
    preset: Literal["best", "best_1080p", "best_editable", "audio_only", "source", "custom"] = "best"
    output_container: Literal["mp4", "webm", "mkv"] = "mp4"
    custom_format: str | None = None
    extract_audio: bool = False
    audio_format: str | None = None
    embed_thumbnail: bool = False
    embed_metadata: bool = True
    subtitles: bool = True

    @model_validator(mode="after")
    def validate_custom_selector(self) -> "FormatSelection":
        if self.preset == "custom" and not (self.custom_format or "").strip():
            raise ValueError("custom_format is required when preset is 'custom'")
        return self


class FormatResolutionState(BaseModel):
    requested_selector: str
    selected_format_id: str | None = None
    fallback_reason: str | None = None


class OutputProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_path: str | None = None
    subdir: str | None = None
    template: str = "%(title)s - %(uploader)s [%(id)s].%(ext)s"
    organize_by: Literal["downloads", "playlist", "uploader"] = "downloads"

    @field_validator("base_path", "subdir")
    @classmethod
    def validate_relative_output_directory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        path = PurePosixPath(normalized)
        if len(normalized) > 240 or path.is_absolute() or re.match(r"^[a-zA-Z]:", normalized) or "\\" in normalized or "%" in normalized or ".." in path.parts or any(ord(char) < 32 for char in normalized):
            raise ValueError("Output folders must be safe relative paths inside the Library.")
        return normalized

    @field_validator("template")
    @classmethod
    def validate_output_template(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 240:
            raise ValueError("Output template must be between 1 and 240 characters.")
        if "/" in normalized or "\\" in normalized or ":" in normalized or ".." in normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("Output template must produce one safe filename.")
        fields = OUTPUT_TEMPLATE_FIELD_PATTERN.findall(normalized)
        if any(field not in SAFE_OUTPUT_TEMPLATE_FIELDS for field in fields) or "%(" in OUTPUT_TEMPLATE_FIELD_PATTERN.sub("", normalized):
            raise ValueError("Output template uses an unsupported metadata field.")
        return normalized


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str
    lazy_playlist: bool = True
    format_selection: FormatSelection = Field(default_factory=FormatSelection)
    supported_profiles: list[str] | None = Field(default=None, max_length=32)
    # Bounds how many playlist/channel entries extraction materializes upstream, on
    # top of the service's own hard cap — the response never serves more than 100.
    entries_limit: int | None = Field(default=None, ge=1, le=100)

    @field_validator("supported_profiles")
    @classmethod
    def validate_supported_profiles(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip().lower() for value in values]
        if any(not value or len(value) > 64 for value in normalized):
            raise ValueError("Playback profiles must be non-empty and at most 64 characters.")
        return list(dict.fromkeys(normalized))


class RemotePrefetchRequest(BaseModel):
    """A card the member is resting on: what a click on it would preview, in the same browser."""

    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(max_length=2048)
    supported_profiles: list[str] | None = Field(default=None, max_length=32)

    validate_supported_profiles = field_validator("supported_profiles")(classmethod(PreviewRequest.validate_supported_profiles.__func__))


class YouTubeSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=24)


class UpNextRequest(BaseModel):
    """The currently playing Media source, as Up Next's playback context.

    ``category_keys`` are the current source's canonical subjects when the caller
    knows them (a Media source opened from a recommendation surface carries them);
    unknown keys are dropped by the policy and creator affinity still applies.
    """

    source_url: str
    source_id: str | None = None
    source: SearchSource = "youtube"
    title: str | None = None
    uploader: str | None = None
    category_keys: list[str] = Field(default_factory=list, max_length=25)
    limit: int = Field(default=12, ge=1, le=24)
    # The current video's channel, so Up Next can read its cached listing (no provider call).
    channel_id: str | None = Field(default=None, pattern=r"^UC[0-9A-Za-z_-]{22}$")
    channel_url: str | None = Field(default=None, max_length=2_048)


# The member's own watch state on a remote entry, set per request after any cache.
class RemoteProgressAnnotation(BaseModel):
    position_seconds: float
    duration_seconds: float | None = None
    completed: bool = False


# A YouTube channel page tab as the extractor names it; the UI's "Live" tab is `streams`.
ChannelTab = Literal["videos", "streams", "shorts", "playlists"]


class YouTubeSearchResult(BaseModel):
    id: str | None = None
    title: str | None = None
    uploader: str | None = None
    uploader_url: str | None = None
    uploader_id: str | None = None
    duration: int | None = None
    thumbnail: str | None = None
    artwork_url: str | None = None
    webpage_url: str | None = None
    view_count: int | None = None
    availability: str | None = None
    published_at: datetime | None = None
    source: SearchSource = "youtube"
    source_label: str = "YouTube"
    kind: Literal["video", "short", "live", "channel", "playlist"] | None = None
    capabilities: MediaSourceCapabilities | None = None
    # Set by remote_annotation.annotate_remote_entries for the requesting member; never cached.
    saved_item_id: str | None = None
    progress: RemoteProgressAnnotation | None = None


class SearchSourceError(BaseModel):
    """One provider's failure inside an otherwise successful multi-source search."""

    source: SearchSource
    message: str
    retryable: bool = True


class YouTubeSearchResponse(BaseModel):
    query: str
    items: list[YouTubeSearchResult] = Field(default_factory=list)
    errors: list[SearchSourceError] = Field(default_factory=list)


class PreviewEntry(BaseModel):
    id: str | None = None
    title: str | None = None
    duration: int | None = None
    thumbnail: str | None = None
    artwork_url: str | None = None
    webpage_url: str | None = None
    uploader: str | None = None
    availability: str | None = None
    published_at: datetime | None = None
    media_kind: Literal["video", "audio"] | None = None
    capabilities: MediaSourceCapabilities | None = None
    # The channel of a flat entry (channel_id, else uploader_id; channel_url, else uploader_url), its view count,
    # and the member's markers (set per request by remote_annotation).
    channel_id: str | None = None
    channel_url: str | None = None
    view_count: int | None = None
    saved_item_id: str | None = None
    progress: RemoteProgressAnnotation | None = None


class MediaSourceChatCapabilities(BaseModel):
    """The currently supported chat modes for an inspected Media source."""

    live: Literal["unavailable", "available"] = "unavailable"
    replay: Literal["unavailable", "available"] = "unavailable"
    # Why live chat is unavailable when the provider only serves it to a signed-in
    # identity (Twitch/Kick). Lumina never asks for one, so the UI explains instead.
    live_reason: Literal["authentication_required"] | None = None


class MediaSourceCapabilities(BaseModel):
    """Safe, provider-normalized actions and lifecycle for an inspected source."""

    provider: Literal["youtube", "twitch", "kick", "soundcloud", "generic", "unknown"]
    lifecycle: Literal["vod", "live", "upcoming", "post_live", "completed_live"]
    can_play: bool
    # ``provider_not_supported`` marks a recognized provider whose public adapter has
    # not landed (PUBLIC_PROVIDERS in hls_relay_support.py); every other reason is
    # about the source's lifecycle or transport, never a provider guess.
    play_reason: Literal[
        "live_playback_not_supported",
        "subscriber_only",
        "sign_in_required",
        "upcoming_not_started",
        "post_live_processing",
        "segmented_transport_not_supported",
        "provider_not_supported",
        "no_supported_transport",
    ] | None = None
    can_acquire: bool
    acquire_reason: Literal[
        "live_acquisition_not_supported",
        "subscriber_only",
        "sign_in_required",
        "upcoming_not_started",
        "post_live_processing",
        "segmented_transport_not_supported",
        "provider_not_supported",
        "no_supported_transport",
    ] | None = None
    # "Record from now" is a distinct deliberate action from ordinary acquisition,
    # offered only for a currently-live source Lumina can capture through the
    # guarded live-HLS transport. It is never a substitute for can_acquire.
    can_record: bool = False
    record_reason: Literal["live_record_not_supported", "provider_not_supported"] | None = None
    # "Schedule this broadcast" (issue #98) is offered for an UPCOMING source Lumina
    # can wait for and record. ``scheduled_start`` is the best-available provider
    # start time (may be absent). ``from_start_available`` advertises the
    # best-effort/experimental "record from the beginning" INTENT — never a raw
    # yt-dlp flag — for a source that can provide it.
    can_schedule: bool = False
    schedule_reason: Literal["upcoming_schedule_not_supported", "provider_not_supported"] | None = None
    scheduled_start: datetime | None = None
    from_start_available: bool = False
    chat: MediaSourceChatCapabilities = Field(default_factory=MediaSourceChatCapabilities)
    # True for a never-inspected flat search/discovery entry: its actions are
    # rechecked from the fully extracted source when it is opened.
    provisional: bool = False


TimedChatEventKind = Literal["message", "paid_message", "paid_sticker", "membership", "system"]
TimedChatModeration = Literal["visible", "deleted", "author_removed"]
ChatReplayAssetStatus = Literal[
    "building", "ready", "empty", "partial", "oversized", "unavailable", "malformed", "failed"
]


class TimedChatAuthorResponse(BaseModel):
    """Safe author presentation for one timed chat event."""

    name: str
    channel_id: str | None = None
    badges: list[str] = Field(default_factory=list)


class TimedChatEventResponse(BaseModel):
    """One normalized timed chat event as exposed on the public API.

    Carries only display text, safe author presentation, a validated media
    offset, event kind, and moderation state — never provider continuations,
    request headers, cookies, or upstream addresses.
    """

    id: str
    offset_ms: int | None = None
    kind: TimedChatEventKind
    text: str
    author: TimedChatAuthorResponse | None = None
    moderation: TimedChatModeration = "visible"
    amount: str | None = None
    # The origin channel's display name when a provider relays a message from
    # another channel's chat (Twitch shared chat); null for an ordinary message.
    source_channel: str | None = None


class ChatReplayLoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=1, max_length=4096)
    refresh: bool = False


class ChatReplayAssetResponse(BaseModel):
    """A member's bounded timed chat asset for one completed-live source."""

    source_identity: str
    status: ChatReplayAssetStatus
    event_count: int = 0
    truncated: bool = False
    dropped_malformed: int = 0
    events: list[TimedChatEventResponse] = Field(default_factory=list)
    updated_at: datetime | None = None


LiveRecordingStatus = Literal[
    "waiting", "queued", "live", "stopping", "finalizing", "completed", "partial", "failed", "cancelled"
]
LiveRecordingMediaStatus = Literal["pending", "recording", "finalizing", "completed", "partial", "failed"]
LiveRecordingChatStatus = Literal["pending", "capturing", "completed", "unavailable", "failed"]
# From-start (issue #98) is expressed as member INTENT, never a raw yt-dlp flag.
LiveRecordingStartIntent = Literal["from_start", "live_edge"]
LiveRecordingFallbackPolicy = Literal["allow_live_edge", "require_choice"]
LiveRecordingCaptureOrigin = Literal["pending", "source_beginning", "live_edge"]
LiveRecordingHistory = Literal["pending", "complete", "partial", "from_edge"]


class LiveRecordingCreateRequest(BaseModel):
    """A deliberate record action on a currently-live or upcoming source.

    ``start_intent`` and ``fallback_policy`` are the from-start member intent
    (issue #98): whether to begin at the beginning if available, and whether an
    unavailable from-start may silently fall back to the edge. They are never raw
    yt-dlp option names. An upcoming source is scheduled (durable waiting phase);
    a live source records from now.
    """

    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=1, max_length=4096)
    format_selection: FormatSelection = Field(default_factory=FormatSelection)
    output_profile: OutputProfile = Field(default_factory=OutputProfile)
    start_intent: LiveRecordingStartIntent = "live_edge"
    fallback_policy: LiveRecordingFallbackPolicy = "allow_live_edge"


class LiveRecordingMediaOutput(BaseModel):
    """The media sibling output: a recording published as a Library item."""

    status: LiveRecordingMediaStatus
    library_item_id: str | None = None
    failure_category: str | None = None
    error: str | None = None
    # Why capture ended: source_ended | stopped | time_limit | size_limit |
    # disk_low | owner_disabled | shutdown | interrupted.
    end_reason: str | None = None


class LiveRecordingChatOutput(BaseModel):
    """The chat sibling output: a forward-only capture published as a timed chat asset."""

    status: LiveRecordingChatStatus
    chat_asset_id: str | None = None
    failure_category: str | None = None
    error: str | None = None


class LiveRecordingResponse(BaseModel):
    """One durable multi-output live acquisition with explicit partial outcomes.

    ``status`` never pretends a live source has a known total duration. It is only
    ``completed`` when both sibling outputs reached a documented terminal state;
    ``partial`` means exactly one usable output was published (media-ok+chat-fail
    or chat-ok+media-fail), and a deliberate stop is distinct from cancel and from
    failure.
    """

    id: str
    source_url: str
    title: str | None = None
    extractor: str | None = None
    status: LiveRecordingStatus
    stop_requested: bool = False
    cancel_requested: bool = False
    media: LiveRecordingMediaOutput
    chat: LiveRecordingChatOutput
    # Scheduling + from-start (issue #98). ``scheduled_start_at`` is the best
    # available provider start time while waiting; ``capture_origin`` and
    # ``history`` are the honest answer to where capture began and whether the
    # from-start history was complete (``partial`` is the explicit partial-history
    # condition). ``awaiting_fallback_choice`` marks a from-start-unavailable result
    # a member can resolve by recording from the edge; ``waiting_reason`` explains a
    # scheduled acquisition that ended before it could connect.
    start_intent: LiveRecordingStartIntent = "live_edge"
    fallback_policy: LiveRecordingFallbackPolicy = "allow_live_edge"
    scheduled_start_at: datetime | None = None
    from_start_supported: bool | None = None
    capture_origin: LiveRecordingCaptureOrigin = "pending"
    history: LiveRecordingHistory = "pending"
    awaiting_fallback_choice: bool = False
    waiting_reason: str | None = None
    # Capture resumed after a restart (the downtime is a gap), the member's
    # keep flag (exempt from retention), and the capture ceilings in force.
    resumed_after_restart: bool = False
    kept: bool = False
    max_runtime_seconds: float | None = None
    max_bytes: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    recording_started_at: datetime | None = None
    finished_at: datetime | None = None


class LiveRecordingKeepRequest(BaseModel):
    kept: bool


class LiveRecordingPageResponse(BaseModel):
    items: list[LiveRecordingResponse] = Field(default_factory=list)
    next_cursor: str | None = None


class RemoteRenditionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rendition_id: str
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    bitrate_kbps: float | None = None
    video_codec: str
    audio_codec: str
    container: str
    content_type: str
    display_label: str


class RemotePlaybackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: Literal["ready", "unsupported"]
    stream_id: str
    transport: Literal["progressive", "hls", "dash"] | None = None
    media_kind: Literal["video", "audio"] | None = None
    playback_url: str | None = None
    content_type: str | None = None
    has_video: bool
    has_audio: bool
    seekable: bool
    live: bool = False
    fallback_code: str | None = None
    fallback_message: str | None = None
    renditions: list[RemoteRenditionResponse] = Field(default_factory=list)
    # "auto" while adaptive playback is active; otherwise one opaque rendition id.
    selected_rendition_id: str | None = None
    auto_available: bool = False


class PopularCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    state: Literal["pending", "ready", "stale", "empty", "failed"]
    last_success_at: datetime | None = None
    next_refresh_at: datetime | None = None


class PopularItemResponse(YouTubeSearchResult):
    model_config = ConfigDict(from_attributes=True)

    category_keys: list[str] = Field(default_factory=list)
    # Set on items of a served recommendation list only.
    reco: RecoAnnotation | None = None


class PopularSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[PopularItemResponse] = Field(default_factory=list)
    categories: list[PopularCategoryResponse] = Field(default_factory=list)
    state: Literal["loading", "ready", "partial", "stale", "empty", "failed"]
    refreshing: bool
    stale: bool
    last_success_at: datetime | None = None
    refreshed_at: datetime | None = None
    next_refresh_at: datetime | None = None
    error: str | None = None
    # Only /api/discovery/popular sets these (Explore's For you rail and the member's category order).
    for_you: list[PopularItemResponse] = Field(default_factory=list)
    category_order: list[str] = Field(default_factory=list)


class FollowedSourceUnavailable(BaseModel):
    source: SearchSource
    checked_at: datetime


class LiveCategoryResponse(PopularCategoryResponse):
    # Provider-reported live channels in this category (Twitch directory totals, YouTube when known); null if unknown.
    live_count: int | None = None


class LiveWallResponse(BaseModel):
    items: list[PopularItemResponse] = Field(default_factory=list)
    next_cursor: str | None = None  # opaque server token, never an upstream URL or continuation
    live_count: int | None = None


class PopularWallResponse(BaseModel):
    items: list[PopularItemResponse] = Field(default_factory=list)
    next_cursor: str | None = None


class LiveSnapshotResponse(PopularSnapshotResponse):
    model_config = ConfigDict(from_attributes=True)

    categories: list[LiveCategoryResponse] = Field(default_factory=list)
    live_total: int | None = None

    twitch_available: bool = True
    hero: list[PopularItemResponse] = Field(default_factory=list)
    # Followed channels whose live status could not be checked (e.g. Kick refused the
    # anonymous request); their last-known state is kept, never guessed.
    followed_unavailable: list[FollowedSourceUnavailable] = Field(default_factory=list)


# ---- YouTube channel pages and Library channels ----
class ChannelLiveResponse(BaseModel):
    webpage_url: str
    title: str | None = None
    view_count: int | None = None
    artwork_url: str | None = None


class ChannelHeaderResponse(BaseModel):
    id: str
    name: str
    handle: str | None = None
    url: str
    avatar_url: str | None = None
    banner_url: str | None = None
    follower_count: int | None = None
    video_count: int | None = None
    description: str | None = None
    verified: bool = False
    tabs: list[ChannelTab] = Field(default_factory=list)
    follow_id: str | None = None
    live: ChannelLiveResponse | None = None


class ChannelPageResponse(BaseModel):
    channel: ChannelHeaderResponse
    tab: ChannelTab
    entries: list[YouTubeSearchResult] = Field(default_factory=list)
    has_more: bool = False
    # An authentication-required tab (members-only, age-gated): 200 with no entries.
    restricted: bool = False
    fetched_at: datetime
    stale: bool = False


class ChannelResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


class ChannelResolveResponse(BaseModel):
    provider: Literal["youtube"] = "youtube"
    channel_id: str


class LibraryChannelResponse(BaseModel):
    key: str
    extractor: str
    name: str
    # The raw grouping name ('' = no uploader): the Videos wall's `channel=` filter value.
    uploader: str
    count: int
    unwatched_count: int
    newest_item_id: str
    newest_at: datetime
    channel_id: str | None = None
    avatar_url: str | None = None


class InterestCategoryResponse(BaseModel):
    key: str
    label: str


class MemberInterestsResponse(BaseModel):
    categories: list[InterestCategoryResponse]
    selected_keys: list[str] = Field(default_factory=list)


class MemberInterestsUpdateRequest(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=25)


class FollowChannelRequest(BaseModel):
    source_url: str = Field(max_length=2_048)
    display_name: str = Field(max_length=255)


class OnboardingCompleteRequest(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=25)
    follows: list[FollowChannelRequest] = Field(default_factory=list, max_length=100)


class FollowOutcomeResponse(BaseModel):
    channel_key: str
    display_name: str
    status: Literal["created", "existing", "invalid"]
    automation_id: str | None = None


class MemberOnboardingStateResponse(BaseModel):
    status: str
    selected_keys: list[str] = Field(default_factory=list)
    followed: list[FollowOutcomeResponse] = Field(default_factory=list)


class ChannelCandidateResponse(BaseModel):
    channel_key: str
    source_url: str
    display_name: str
    source: str = "youtube"
    source_label: str = "YouTube"
    artwork_url: str | None = None
    category_keys: list[str] = Field(default_factory=list)
    following: bool = False


class CategoryChannelSuggestionsResponse(BaseModel):
    key: str
    label: str
    state: Literal["ranked", "curated", "empty"]
    channels: list[ChannelCandidateResponse] = Field(default_factory=list)


class ChannelSuggestionsResponse(BaseModel):
    categories: list[CategoryChannelSuggestionsResponse] = Field(default_factory=list)


class ChannelSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=8, ge=1, le=12)


class ChannelCandidateListResponse(BaseModel):
    query: str
    channels: list[ChannelCandidateResponse] = Field(default_factory=list)


class SuppressRecommendationRequest(BaseModel):
    """A member's request to suppress one recommended item or source channel.

    Only recommended-candidate identity fields are accepted; no raw search text
    is stored. For ``scope="item"`` at least one of ``source_id``,
    ``source_url``, or ``title`` identifies the source; for ``scope="channel"``
    ``uploader`` names the channel. Validation of the required fields is enforced
    by the service so a degenerate identity is never persisted.
    """

    scope: Literal["item", "channel", "fewer", "title"]
    source: SearchSource = "youtube"
    source_id: str | None = Field(default=None, max_length=512)
    source_url: str | None = Field(default=None, max_length=2_048)
    title: str | None = Field(default=None, max_length=512)
    uploader: str | None = Field(default=None, max_length=512)
    # The stable channel ("channel", "fewer"), the title ("title"), and the served
    # list the control was used on (attribution only).
    channel_id: str | None = Field(default=None, pattern=r"^UC[0-9A-Za-z_-]{22}$")
    channel_url: str | None = Field(default=None, max_length=2_048)
    title_id: str | None = Field(default=None, max_length=36)
    list_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    key: str | None = Field(default=None, max_length=64)


class SuppressionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: Literal["item", "channel", "fewer", "title"]
    target_key: str
    title: str | None = None
    channel_name: str | None = None
    source: str | None = None
    created_at: datetime
    recovers_at: datetime | None = None  # scope "fewer": created_at + 140 days


class SuppressionListResponse(BaseModel):
    """Suppressed items and channels for the Settings review/restore surface."""

    items: list[SuppressionResponse] = Field(default_factory=list)
    channels: list[SuppressionResponse] = Field(default_factory=list)
    fewer: list[SuppressionResponse] = Field(default_factory=list)
    titles: list[SuppressionResponse] = Field(default_factory=list)


class MemberRecommendationSnapshotResponse(PopularSnapshotResponse):
    """The shared member-scoped recommendation result consumed by Home."""


class NormalizedChapterResponse(BaseModel):
    start_time: float
    end_time: float | None = None
    title: str


class DescriptionTimestampResponse(BaseModel):
    start: int
    end: int
    seconds: float
    label: str


class PreviewResponse(BaseModel):
    kind: Literal["video", "playlist"]
    title: str | None = None
    extractor: str | None = None
    extractor_key: str | None = None
    webpage_url: str | None = None
    availability: str | None = None
    published_at: datetime | None = None
    media_kind: Literal["video", "audio"] | None = None
    capabilities: MediaSourceCapabilities | None = None
    artwork_url: str | None = None
    chapters: list[NormalizedChapterResponse] = Field(default_factory=list)
    description_timestamps: list[DescriptionTimestampResponse] = Field(default_factory=list)
    format_resolution: FormatResolutionState | None = None
    entries: list[PreviewEntry] = Field(default_factory=list)
    playback: RemotePlaybackResponse | None = None
    raw: dict[str, Any]


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str | None = None
    source_url: str
    format_selection: FormatSelection = Field(default_factory=FormatSelection)
    output_profile: OutputProfile = Field(default_factory=OutputProfile)
    preview_snapshot: dict[str, Any] | None = None


class JobAttempt(BaseModel):
    status: str
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobOutput(BaseModel):
    """Where one published file went: root label + routed folder, never an absolute path."""

    library_item_id: str | None = None
    root_label: str | None = None
    folder: str = ""


class JobResponse(BaseModel):
    id: str
    user_id: str | None = None
    source_url: str
    status: JobStatus
    queue_position: int | None
    title: str | None = None
    artwork_url: str | None = None
    extractor: str | None = None
    format_selection: dict[str, Any]
    format_resolution: FormatResolutionState | None = None
    output_profile: dict[str, Any]
    preview_snapshot: dict[str, Any] | None
    error: str | None
    attempts: list[JobAttempt] = Field(default_factory=list)
    outputs: list[JobOutput] = Field(default_factory=list)
    acquisition_batch_id: str | None = None
    acquisition_entry_id: str | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobPageResponse(BaseModel):
    items: list[JobResponse] = Field(default_factory=list)
    next_cursor: str | None = None


class AcquisitionSourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extractor: str | None = Field(default=None, max_length=120)
    extractor_key: str | None = Field(default=None, max_length=120)
    playlist_id: str | None = Field(default=None, max_length=255)
    playlist_title: str | None = Field(default=None, max_length=500)
    uploader: str | None = Field(default=None, max_length=500)
    channel: str | None = Field(default=None, max_length=500)
    webpage_url: str | None = Field(default=None, max_length=2048)
    thumbnail: str | None = Field(default=None, max_length=2048)


class AcquisitionEntryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=1, max_length=2048)
    extractor: str | None = Field(default=None, max_length=120)
    remote_id: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=500)
    thumbnail: str | None = Field(default=None, max_length=2048)
    uploader: str | None = Field(default=None, max_length=500)
    duration: int | None = Field(default=None, ge=0)
    availability: str | None = Field(default=None, max_length=64)


class AcquisitionBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=1, max_length=2048)
    source_title: str | None = Field(default=None, max_length=500)
    source_provenance: AcquisitionSourceProvenance = Field(default_factory=AcquisitionSourceProvenance)
    format_selection: FormatSelection = Field(default_factory=FormatSelection)
    output_profile: OutputProfile = Field(default_factory=OutputProfile)
    entries: list[AcquisitionEntryCreateRequest] = Field(min_length=1, max_length=500)


class AcquisitionBatchEntryResponse(BaseModel):
    id: str
    batch_id: str
    selection_index: int
    source_url: str
    extractor: str | None
    remote_id: str | None
    title: str | None
    status: AcquisitionEntryStatus
    progress: int
    dispatch_attempts: int
    failure_category: str | None
    error: str | None
    details: dict[str, Any]
    download_job_id: str | None = None
    library_item_id: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class AcquisitionBatchResponse(BaseModel):
    id: str
    source_url: str
    source_title: str | None
    source_provenance: AcquisitionSourceProvenance
    status: AcquisitionBatchStatus
    selected_count: int
    queued_count: int
    duplicate_count: int
    completed_count: int
    failed_count: int
    progress: int
    format_selection: dict[str, Any]
    output_profile: dict[str, Any]
    entries: list[AcquisitionBatchEntryResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class LibraryItemResponse(BaseModel):
    id: str
    user_id: str | None = None
    visibility: LibraryItemVisibility = "private"
    owner_username: str | None = None
    owner_display_name: str | None = None
    extractor: str | None
    remote_id: str | None
    title: str
    uploader: str | None
    playlist_name: str | None
    duration: int | None
    thumbnail_url: str | None
    artwork_url: str | None = None
    chapters: list[NormalizedChapterResponse] = Field(default_factory=list)
    description_timestamps: list[DescriptionTimestampResponse] = Field(default_factory=list)
    webpage_url: str | None
    file_size: int | None
    downloaded_at: datetime | None
    availability: str | None
    metadata_json: dict[str, Any]
    status: str
    kind: str = "video"
    # available | quarantined | missing | offline (root's last observation); None = no registered file.
    media_state: str | None = None
    title_id: str | None = None  # Media title (ADR 0009); the watch byline links back to it
    extra_type: str | None = None  # media_schemas.ExtraType; None = a version
    progress: ItemProgress | None = None  # the member's progress; /api/library list pages only
    created_at: datetime
    updated_at: datetime


class LibraryPageResponse(BaseModel):
    items: list[LibraryItemResponse] = Field(default_factory=list)
    next_cursor: str | None = None


class LibraryGroupResponse(BaseModel):
    """A series or artist: its name (None = ungrouped), item count and distinct seasons/albums."""
    name: str | None
    items: int
    subgroups: int
    artwork_url: str


class HouseholdCollectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    visibility: CollectionVisibility = "private"
    rules: SmartCollectionRule | None = None  # a smart collection; None = manual


class HouseholdCollectionRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)


class HouseholdCollectionVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: CollectionVisibility


class CollectionRemoteRefRequest(BaseModel):
    """A public source ref outside the vault (same shape as the S47 watch-queue remote snapshot)."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=120)
    remote_id: str | None = Field(default=None, max_length=255)
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=1000)
    uploader: str | None = Field(default=None, max_length=500)
    artwork_url: str | None = Field(default=None, max_length=2048)
    duration: int | None = Field(default=None, ge=0)
    expected_revision: int | None = Field(default=None, ge=0)


class CollectionEntryMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0)
    expected_revision: int = Field(ge=0)


class CollectionEntryRefResponse(BaseModel):
    kind: Literal["library", "remote"]
    library_item_id: str | None = None
    provider: str | None = None
    remote_id: str | None = None
    url: str | None = None


class CollectionEntryResponse(BaseModel):
    """One ordered collection entry: a library item (re-checked for visibility) or a remote ref."""

    id: str
    position: int
    availability: Literal["available", "unavailable"]
    ref: CollectionEntryRefResponse
    title: str | None
    uploader: str | None
    artwork_url: str | None
    duration: int | None


class HouseholdCollectionResponse(BaseModel):
    id: str
    owner_user_id: str
    name: str
    description: str | None
    visibility: CollectionVisibility
    revision: int
    item_count: int
    items: list[LibraryItemResponse] = Field(default_factory=list)
    entries: list[CollectionEntryResponse] = Field(default_factory=list)
    rules: SmartCollectionRule | None = None
    titles: list[TitleSummary] = Field(default_factory=list)  # smart movie/series/episode results, evaluated as the viewer
    created_at: datetime
    updated_at: datetime


class PlaybackProgressUpdateRequest(BaseModel):
    position_seconds: int = Field(ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    completed: bool = False


class PlaybackProgressResponse(BaseModel):
    id: str
    user_id: str
    item_id: str
    position_seconds: int
    duration_seconds: int | None
    completed: bool
    last_watched_at: datetime
    created_at: datetime
    updated_at: datetime
    item: LibraryItemResponse
    title: TitleSummary | None = None  # Continue watching collapses by title and shows "S1 · E3"


REMOTE_PLAYBACK_RECENT_VIDEO_LIMITS = (3, 5, 10, 20)
REMOTE_PLAYBACK_STORAGE_LIMITS_MB = (512, 2048, 5120, 10_240, 25_600)
REMOTE_PLAYBACK_RECENT_VIDEO_LIMIT_MIN = min(REMOTE_PLAYBACK_RECENT_VIDEO_LIMITS)
REMOTE_PLAYBACK_RECENT_VIDEO_LIMIT_MAX = max(REMOTE_PLAYBACK_RECENT_VIDEO_LIMITS)
REMOTE_PLAYBACK_STORAGE_LIMIT_MB_MIN = min(REMOTE_PLAYBACK_STORAGE_LIMITS_MB)
REMOTE_PLAYBACK_STORAGE_LIMIT_MB_MAX = max(REMOTE_PLAYBACK_STORAGE_LIMITS_MB)


class RemotePlaybackCacheSettings(BaseModel):
    """Bounded member policy for retaining recently streamed media bytes."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, strict=True)
    recent_video_limit: int = Field(
        default=5,
        ge=REMOTE_PLAYBACK_RECENT_VIDEO_LIMIT_MIN,
        le=REMOTE_PLAYBACK_RECENT_VIDEO_LIMIT_MAX,
        strict=True,
    )
    storage_limit_mb: int = Field(
        default=2048,
        ge=REMOTE_PLAYBACK_STORAGE_LIMIT_MB_MIN,
        le=REMOTE_PLAYBACK_STORAGE_LIMIT_MB_MAX,
        strict=True,
    )

    @field_validator("recent_video_limit")
    @classmethod
    def validate_recent_video_limit(cls, value: int) -> int:
        if value not in REMOTE_PLAYBACK_RECENT_VIDEO_LIMITS:
            raise ValueError("Choose a supported recent-video cache limit")
        return value

    @field_validator("storage_limit_mb")
    @classmethod
    def validate_storage_limit(cls, value: int) -> int:
        if value not in REMOTE_PLAYBACK_STORAGE_LIMITS_MB:
            raise ValueError("Choose a supported stream-cache storage limit")
        return value


class RemotePlaybackProgressUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_identity: str = Field(min_length=1, max_length=4096)
    source_url: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=1000)
    uploader: str | None = Field(default=None, max_length=1000)
    artwork_url: str | None = Field(default=None, max_length=4096)
    position_seconds: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    completed: bool = False
    selected_rendition_id: str | None = Field(default=None, max_length=255)
    checkpoint_client_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$",
    )
    checkpoint_sequence: int = Field(ge=1, le=9_007_199_254_740_991, strict=True)
    expected_revision: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    # The entry's channel, for remote_playback_progress.channel_key.
    channel_id: str | None = Field(default=None, pattern=r"^UC[0-9A-Za-z_-]{22}$")
    channel_url: str | None = Field(default=None, max_length=2_048)


class RemotePlaybackProgressResponse(BaseModel):
    id: str
    user_id: str
    source_identity: str
    source_url: str
    extractor: str | None
    remote_id: str | None
    title: str | None
    uploader: str | None
    artwork_url: str | None
    position_seconds: float
    duration_seconds: float | None
    completed: bool
    selected_rendition_id: str | None
    checkpoint_client_id: str
    checkpoint_sequence: int
    checkpoint_revision: int
    cleared: bool
    last_watched_at: datetime
    created_at: datetime
    updated_at: datetime


class LibraryItemVisibilityUpdateRequest(BaseModel):
    visibility: LibraryItemVisibility


class LibraryNoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(max_length=NOTE_BODY_MAX_CHARS)
    visibility: NoteVisibility = "private"
    timestamp_ms: int | None = Field(default=None, ge=0)


class LibraryNoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(max_length=NOTE_BODY_MAX_CHARS)
    visibility: NoteVisibility


class LibraryNoteResponse(BaseModel):
    id: str
    item_id: str
    user_id: str | None
    visibility: NoteVisibility
    timestamp_ms: int | None = None
    body: str
    author_username: str | None = None
    author_display_name: str | None = None
    is_owner: bool = False
    can_delete: bool = False
    created_at: datetime
    updated_at: datetime


class LibraryTagCreateRequest(BaseModel):
    tag: str


class LibraryTagResponse(BaseModel):
    id: str
    item_id: str
    user_id: str | None
    tag: str
    created_at: datetime
    updated_at: datetime


class LocalSearchMatchResponse(BaseModel):
    kind: Literal["library", "channel", "automation", "title", "moment"]
    id: str
    title: str
    subtitle: str
    score: float
    lexical_score: float
    semantic_score: float
    match_mode: Literal["hybrid", "lexical", "semantic"]
    item: LibraryItemResponse | None = None
    source_url: str | None = None
    source_type: SourceAutomationType | None = None
    title_id: str | None = None
    start_ms: int | None = None  # moment hits: "the one where…"
    media_title: TitleSummary | None = None


class SearchResultResponse(BaseModel):
    query: str = ""
    mode: Literal["hybrid", "lexical"] = "lexical"
    matches: list[LocalSearchMatchResponse] = Field(default_factory=list)
    items: list[LibraryItemResponse] = Field(default_factory=list)
    index_generation: int = 0


class AutomationRuleSet(BaseModel):
    include_title: list[str] = Field(default_factory=list)
    exclude_title: list[str] = Field(default_factory=list)
    include_uploader: list[str] = Field(default_factory=list)
    exclude_uploader: list[str] = Field(default_factory=list)
    include_source: list[str] = Field(default_factory=list)
    exclude_source: list[str] = Field(default_factory=list)
    media_kind: AutomationMediaKind = "any"
    min_duration: int | None = Field(default=None, ge=0)
    max_duration: int | None = Field(default=None, ge=0)
    max_age_days: int | None = Field(default=None, ge=1)


class UserDownloadDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_selection: FormatSelection = Field(default_factory=FormatSelection)
    output_profile: OutputProfile = Field(default_factory=OutputProfile)


class UserAutomationDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cron_expression: str = "*/30 * * * *"
    auto_download: bool = False
    format_selection: FormatSelection = Field(default_factory=FormatSelection)
    output_profile: OutputProfile = Field(default_factory=lambda: OutputProfile(organize_by="playlist"))
    rules: AutomationRuleSet = Field(default_factory=AutomationRuleSet)
    duplicate_policy: AutomationDuplicatePolicy = "skip_same_source"
    max_items_per_run: int | None = Field(default=25, ge=1)
    max_items_per_day: int | None = Field(default=None, ge=1)
    backfill_limit: int | None = Field(default=50, ge=1)

    @model_validator(mode="before")
    @classmethod
    def reject_retention(cls, value: Any) -> Any:
        if isinstance(value, dict) and "retention" in value:
            raise ValueError("Source automation retention is not supported.")
        return value


class UserSettingsResponse(BaseModel):
    id: str
    user_id: str
    download_defaults: UserDownloadDefaults
    automation_defaults: UserAutomationDefaults
    ui_prefs: dict[str, Any]
    notification_prefs: dict[str, Any]
    remote_playback_cache: RemotePlaybackCacheSettings
    resolved_download_defaults: UserDownloadDefaults
    resolved_automation_defaults: UserAutomationDefaults
    created_at: datetime
    updated_at: datetime


class UserSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    download_defaults: UserDownloadDefaults | None = None
    automation_defaults: UserAutomationDefaults | None = None
    ui_prefs: dict[str, Any] | None = None
    notification_prefs: dict[str, Any] | None = None
    remote_playback_cache: RemotePlaybackCacheSettings | None = None


class SourceAutomationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    source_url: str
    source_type: SourceAutomationType | None = None
    cron_expression: str = "*/30 * * * *"
    active: bool = True
    auto_download: bool = False
    format_selection: FormatSelection | None = None
    output_profile: OutputProfile | None = None
    rules: AutomationRuleSet = Field(default_factory=AutomationRuleSet)
    duplicate_policy: AutomationDuplicatePolicy = "skip_same_source"
    max_items_per_run: int | None = Field(default=25, ge=1)
    max_items_per_day: int | None = Field(default=None, ge=1)
    backfill_limit: int | None = Field(default=50, ge=1)

    @model_validator(mode="before")
    @classmethod
    def reject_retention(cls, value: Any) -> Any:
        if isinstance(value, dict) and "retention" in value:
            raise ValueError("Source automation retention is not supported.")
        return value


class SourceAutomationCreateRequest(SourceAutomationBase):
    pass


class SourceAutomationAutoDownloadRequest(BaseModel):
    enabled: bool


class SourceAutomationResponse(BaseModel):
    id: str
    user_id: str
    label: str
    source_url: str
    source_type: SourceAutomationType
    artwork_url: str | None = None
    cron_expression: str
    active: bool
    auto_download: bool
    format_selection: dict[str, Any]
    output_profile: dict[str, Any]
    rules: AutomationRuleSet
    duplicate_policy: AutomationDuplicatePolicy
    max_items_per_run: int | None = None
    max_items_per_day: int | None = None
    backfill_limit: int | None = None
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None
    last_error: str | None = None
    last_run_summary: dict[str, Any]
    feed_entries: list[PreviewEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AutomationDecisionResponse(BaseModel):
    id: str | None = None
    run_id: str | None = None
    automation_id: str
    remote_id: str | None = None
    source_url: str | None = None
    title: str | None = None
    action: Literal["queued", "would_queue", "manual", "skipped_duplicate", "skipped_rule", "skipped_limit", "failed"]
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class AutomationRunResponse(BaseModel):
    id: str
    automation_id: str
    user_id: str
    status: Literal["running", "completed", "failed", "preview"]
    started_at: datetime
    finished_at: datetime | None = None
    discovered_count: int
    matched_count: int
    queued_count: int
    manual_count: int
    skipped_count: int
    failed_count: int
    error: str | None = None
    summary_json: dict[str, Any]
    decisions: list[AutomationDecisionResponse] = Field(default_factory=list)


class AutomationPreviewResult(BaseModel):
    automation: SourceAutomationResponse
    run: AutomationRunResponse


class AcquisitionErrorDetail(BaseModel):
    category: AcquisitionErrorCategory
    message: str


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    onboarding_status: str = "completed"
    has_local_password: bool = True
    bio: str | None = None
    can_edit_details: bool = False  # Owners always; members while the household switch is on
    two_factor_enabled: bool = False
    two_factor_setup_required: bool = False  # an owner the household requires to turn it on (owner settings refuse until then)
    created_at: datetime
    updated_at: datetime


class SessionResponse(BaseModel):
    user: UserResponse
    csrf_token: str | None = None


class DeviceMember(BaseModel):
    user_id: str
    display_name: str
    username: str
    role: str
    switch: Literal["instant", "password"]
    active: bool


class SessionSwitchRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)


class DeviceForgetRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)


class UserCreateRequest(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    role: UserRole = "viewer"
    is_active: bool = True
    bio: str | None = None


class UserUpdateRequest(BaseModel):
    # No password field: admins issue a single-use reset link instead.
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    bio: str | None = None


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: UserRole = "viewer"
    expires_in_hours: int = Field(default=48, ge=1, le=168)


class InvitationResponse(BaseModel):
    id: str
    role: UserRole
    expires_at: datetime
    invitation_url: str


class InvitationSummaryResponse(BaseModel):
    """An issued invitation without its secret: the link is shown once, at issuance."""

    id: str
    role: UserRole
    status: Literal["pending", "used", "revoked", "expired"]
    created_at: datetime
    expires_at: datetime


class InvitationRedeemRequest(BaseModel):
    # extra="forbid": the recipient can never choose or tamper with the bound role.
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=256)
    username: str = Field(min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    password: str = Field(max_length=256)


class UserSelfUpdateRequest(BaseModel):
    # Password changes go through POST /api/me/password with the current password.
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    bio: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class PasswordResetLinkResponse(BaseModel):
    expires_at: datetime
    reset_url: str


class PasswordResetRedeemRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(max_length=256)


class AppSettingsResponse(BaseModel):
    temp_root: str
    archive_path: str
    concurrency: int
    max_active_jobs_per_user: int
    min_free_disk_mb: int
    max_playback_sessions: int = 2
    # When a saved change takes effect: download workers start with the process.
    setting_effects: dict[str, Literal["restart", "new_jobs"]] = Field(
        default_factory=lambda: {"concurrency": "restart", "max_active_jobs_per_user": "new_jobs", "min_free_disk_mb": "new_jobs"}
    )
    ffmpeg_path: str | None = None
    yt_dlp_defaults: dict[str, Any]
    ui_prefs: dict[str, Any]
    webhook_url: str | None = None
    webhook_enabled: bool = False
    webhook_notify_new_videos: bool = True
    webhook_notify_failures: bool = True
    extra_source_ports: list[int] = Field(default_factory=list)  # beyond 80/443, for sources such as Icecast on :8000
    require_owner_two_factor: bool = False
    updated_at: datetime


class AppSettingsUpdateRequest(BaseModel):
    temp_root: str | None = None
    archive_path: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=8)
    max_active_jobs_per_user: int | None = Field(default=None, ge=1, le=500)
    min_free_disk_mb: int | None = Field(default=None, ge=0, le=1_048_576)
    max_playback_sessions: int | None = Field(default=None, ge=1, le=16)
    ffmpeg_path: str | None = None
    yt_dlp_defaults: dict[str, Any] | None = None
    ui_prefs: dict[str, Any] | None = None
    webhook_url: str | None = None
    webhook_enabled: bool | None = None
    webhook_notify_new_videos: bool | None = None
    webhook_notify_failures: bool | None = None
    extra_source_ports: list[int] | None = Field(default=None, max_length=32)

    @field_validator("extra_source_ports")
    @classmethod
    def validate_extra_source_ports(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else check_extra_ports(value)
    require_owner_two_factor: bool | None = None

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return value
        try:
            parsed = urlsplit(normalized)
        except ValueError as exc:
            raise ValueError("Webhooks require an HTTPS Discord webhook URL.") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != "discord.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or not parsed.path.startswith("/api/webhooks/")
            or parsed.fragment
        ):
            raise ValueError("Webhooks require an HTTPS Discord webhook URL.")
        return normalized


class WebhookTestResponse(BaseModel):
    ok: bool
    message: str


# ---- Member access (2.8.0, ADR 0019). Also an invite's preset (account_tokens.access). ------------------------------
MovieRating = Literal["G", "PG", "PG-13", "R", "NC-17"]
TvRating = Literal["TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14", "TV-MA"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CLOCK = r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$"
SECTION_ID = re.compile(r"^(?:movies|shows|anime|music|vault|root:[0-9a-f-]{1,36})$")


class StreamingAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    youtube: bool = True
    twitch: bool = True
    kick: bool = True
    live: bool = True
    open_search: bool = True
    followed_only: bool = False


class MemberAccessIn(BaseModel):
    """What an admin sets for a member (or an invite carries). sections None = every library; schedule None = any time."""

    model_config = ConfigDict(extra="forbid")
    sections: list[str] | None = Field(default=None, max_length=64)
    movie_rating_max: MovieRating | None = None
    tv_rating_max: TvRating | None = None
    unrated: Literal["allow", "hide"] = "allow"
    streaming: StreamingAccess = Field(default_factory=StreamingAccess)
    schedule: dict[Weekday, list[tuple[str, str]]] | None = None
    daily_limit_minutes: int | None = Field(default=None, ge=1, le=1440)
    can_download: bool = True

    @field_validator("sections")
    @classmethod
    def _sections(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (bad := [s for s in value if not SECTION_ID.match(s)]):
            raise ValueError(f"Unknown library: {bad[0]}")
        return None if value is None else sorted(set(value))

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, value: dict[str, list[tuple[str, str]]] | None) -> dict[str, list[tuple[str, str]]] | None:
        for windows in (value or {}).values():
            if len(windows) > 8:
                raise ValueError("At most 8 viewing windows a day.")
            for start, end in windows:
                if not (re.match(CLOCK, start) and re.match(CLOCK, end)) or start >= end:
                    raise ValueError(f"{start}–{end} is not a viewing window.")
        return value


class MemberAccessOut(MemberAccessIn):
    restricted: bool  # False = no row: every library, no limits
    bonus_date: date | None = None
    bonus_minutes: int = 0
    bonus_minutes_today: int = 0
    screen_time_today_seconds: int = 0


class AdminUserResponse(UserResponse):
    """GET /api/admin/users rows: plus the member's access (None = every library, no limits) and today's screen time."""

    access: MemberAccessOut | None = None
    screen_time_today_seconds: int = 0
    sign_in_locked: bool = False  # too many wrong passwords: public sign-in paused until it drains or an owner unlocks


class MyAccessResponse(BaseModel):
    """GET /api/me/access: the signed-in member's own effective access, for hiding what they cannot use."""

    restricted: bool
    sections: list[str] | None
    blocked_streaming: list[str]
    followed_only: bool
    can_download: bool
    # Filled with {allowed_now, until, remaining_minutes}; None = no schedule or daily limit.
    schedule_state: dict[str, Any] | None = None
    allowed_now: bool = True  # flat copies of schedule_state for the member UI
    until: datetime | None = None
    remaining_minutes: int | None = None


class MemberFollowIn(BaseModel):
    """An admin follows a channel on a member's behalf (the way past followed_only, which refuses the member's own new follows)."""

    model_config = ConfigDict(extra="forbid")
    source_url: str = Field(min_length=1, max_length=2048)
    display_name: str = Field(default="", max_length=255)  # blank = the channel address


class MemberFollowOut(BaseModel):
    id: str
    label: str
    source_url: str


class SectionInfo(BaseModel):
    id: str
    label: str
    count: int
