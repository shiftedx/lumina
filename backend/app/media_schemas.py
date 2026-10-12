"""Frozen API contract for the media vault.

Every class and Literal alias here has a same-named export in frontend/src/types.ts;
tests/test_media_contract.py fails when field names or values drift. Change both sides
together, and only by adding optional fields. Endpoints live with their routers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.services.local_ai import validate_endpoint_url

TitleType = Literal["series", "season", "episode", "movie", "boxset", "album", "artist"]
ExtraType = Literal["trailer", "featurette", "behindthescenes", "deletedscene", "interview", "scene", "short", "clip", "other"]
FieldSource = Literal["user", "nfo", "tmdb", "path"]
ImageType = Literal["Primary", "Backdrop", "Logo", "Thumb", "Banner"]
MatchMethod = Literal["id", "find", "search", "llm", "user"]
TitleSort = Literal["name", "created", "year", "rating"]
PersonType = Literal["Actor", "Director", "Writer", "Creator", "GuestStar", "Producer", "Composer"]
ConnectedAppKind = Literal["jellyfin", "agent", "app_password"]
ConnectedAppScope = Literal["read", "write"]
HwaccelMode = Literal["auto", "off", "qsv", "vaapi"]
HwaccelActive = Literal["qsv", "vaapi", "none"]
TonemapMethod = Literal["opencl", "vpp_qsv", "software", "none"]
SubtitleOrigin = Literal["embedded", "sidecar", "caption", "generated", "synced", "translated"]
SubtitleFormat = Literal["text", "image"]
EnrichmentJobKind = Literal["asr", "sync", "translate", "segments", "captions"]
EnrichmentJobState = Literal["pending", "queued", "running", "succeeded", "failed", "canceled", "interrupted"]
SegmentType = Literal["intro", "credits", "recap", "preview", "commercial"]
SegmentSource = Literal["user", "fingerprint", "introdb", "heuristic"]
RecapState = Literal["none", "queued", "running", "succeeded", "failed", "fallback"]
TitleRowKind = Literal["because_you_watched", "recommended"]
SmartRuleType = Literal["movie", "series", "episode", "channel_video"]
SmartRuleMatch = Literal["all", "any"]
SmartRuleField = Literal["genre", "year", "rating", "official_rating", "people", "provider", "watched", "added", "runtime", "channel", "tags"]
SmartRuleOp = Literal["is", "is_not", "in", "not_in", "gte", "lte", "within_days"]
SmartRuleSortField = Literal["name", "year", "rating", "added", "runtime"]
SortOrder = Literal["asc", "desc"]
TitleResolution = Literal["4k", "1080p", "720p", "sd"]  # buckets, in display order
SearchScope = Literal["movies", "shows", "anime"]
TitleCategory = Literal["movies", "shows", "anime"]  # Which Library tab a movie or show belongs to
ClientMetricName = Literal[
    "wall_first_screen_ms", "wall_sharp_ms", "detail_hero_ms", "home_first_screen_ms", "home_hero_ms",
    "image_load_ms", "image_failed", "ttff_ms", "long_tasks",
]

# e:{ffprobe stream} embedded text | s:{n} nth scanner sidecar | t:{transcript id} | i:{ffprobe stream} image (burn-in)
SUBTITLE_TRACK_ID_PATTERN = r"^(?:[esi]:\d{1,4}|t:[0-9a-f-]{36})$"
# Member prefs in UserSettings.ui_prefs (no DDL).
UI_PREF_KEYS = ("normalize_loudness", "auto_skip", "profanity", "playback_max_height", "autoplay_up_next")
PROFANITY_MAX_WORDS = 500
PROFANITY_MAX_WORD_CHARS = 40
# One Settings "Anime folders" name: a folder name, never a path.
AnimeFolder = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[^/\\]+$")]
ANIME_FOLDERS_MAX = 20


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- Recommendations -----------------------------
RecoSurface = Literal["home_picked", "home_recommended", "home_because", "explore_for_you", "explore_popular", "up_next", "title_similar"]
RecoReasonCode = Literal[
    "follow_new", "channel", "finished", "interest", "popular", "new_arrival", "well_rated",
    "like_current", "same_channel", "like_anchor", "next_part", "next_episode", "explore",
]
RecoSlot = Literal["exploit", "explore", "pinned"]
RecoClientEventKind = Literal["impression", "open"]
RECO_LIST_ID = r"^[0-9a-f]{16}$"
RECO_KEY = r"^(?:[0-9a-f]{64}|[0-9a-f-]{36})$"  # remote_media.key, or a media title id


class RecoAnnotation(BaseModel):
    """Why one item is in one served list. Set per request by the policy; never cached by clients."""

    list_id: str = Field(pattern=RECO_LIST_ID)
    key: str = Field(pattern=RECO_KEY)  # echoed back in events
    position: int = Field(ge=0, le=99)
    slot: RecoSlot
    reason_code: RecoReasonCode
    reason: str = Field(max_length=80)


class RecoEventIn(_Body):
    kind: RecoClientEventKind
    list_id: str = Field(pattern=RECO_LIST_ID)
    key: str = Field(pattern=RECO_KEY)
    age_ms: int = Field(ge=0, le=900_000)  # how long ago the client saw it happen


class RecoEventBatch(_Body):
    csrf: str | None = Field(default=None, max_length=128)  # sendBeacon cannot set X-CSRF-Token
    events: list[RecoEventIn] = Field(max_length=200)


class RecoSurfaceStats(BaseModel):
    """One surface's household totals over the window. Rates are None under 50 in their denominator."""

    surface: RecoSurface
    impressions: int
    opens: int
    ctr: float | None = None
    plays: int
    play_through_median: float | None = None
    completion_rate: float | None = None
    negative_rate: float | None = None
    explore_play_rate: float | None = None
    exploit_play_rate: float | None = None


class RecoPoolHealth(BaseModel):
    members_with_pool: int
    median_pool_size: int
    oldest_refresh_age_minutes: int | None = None
    provider_calls_24h: int
    budget_hits_24h: int
    remote_vector_coverage: float | None = None  # share of pooled remote_media rows with a current vector
    dropped_events_24h: int


class RecoDiagnostics(BaseModel):
    """Diagnostics' recommendations section: household totals only, never a member."""

    enabled: bool
    window_days: int = 28
    surfaces: list[RecoSurfaceStats] = Field(default_factory=list)
    reco_share_of_remote_plays: float | None = None
    pool: RecoPoolHealth


# ---- Titles -----------------------------------------------------------------

class TitleUserData(BaseModel):
    played: bool = False
    is_favorite: bool = False
    position_seconds: int = 0
    duration_seconds: int | None = None
    last_watched_at: datetime | None = None
    resume_item_id: str | None = None  # the version to resume (most recently watched)
    unplayed_count: int | None = None  # series/season only


class TitleArt(BaseModel):
    """One title image for the gallery. Built only by app.services.art_urls.title_art."""

    url: str  # the original: /api/titles/{id}/images/{type}?tag=… (same as the *_url field)
    rendition: str | None = None  # "/api/art/{sig}/{title_id}/{type}/{source_key}-{w}.webp"; the client substitutes {w}
    widths: list[int] = Field(default_factory=list)  # rendition widths for this image; [] when rendition is None
    width: int | None = None  # source pixels, when analysed
    height: int | None = None
    preview: str | None = None  # "data:image/webp;base64,…" or "data:image/jpeg;base64,…"
    dominant: str | None = None  # "#rrggbb"
    accent: str | None = None  # "#rrggbb"


class TitleSummary(BaseModel):
    id: str
    type: TitleType
    name: str
    sort_name: str | None = None
    year: int | None = None
    index_number: int | None = None
    index_number_end: int | None = None
    parent_id: str | None = None
    series_id: str | None = None
    series_name: str | None = None
    season_number: int | None = None
    overview: str | None = None
    genres: list[str] = Field(default_factory=list)
    official_rating: str | None = None
    community_rating: float | None = None
    runtime_seconds: int | None = None
    poster_url: str | None = None  # /api/titles/{id}/images/Primary?tag=…
    backdrop_url: str | None = None
    play_item_id: str | None = None  # preferred version (movie/episode)
    added_at: datetime
    user_data: TitleUserData = Field(default_factory=TitleUserData)
    poster: TitleArt | None = None  # Primary (episodes: the still); preview always set when known
    backdrop: TitleArt | None = None  # Backdrop; preview only on TitleDetail's own backdrop
    category: TitleCategory | None = None  # movie, series, season, episode; None for boxset, album, artist
    artist_name: str | None = None  # album: its artist title's name
    child_count: int | None = None  # album: visible tracks; artist: visible albums
    reco: RecoAnnotation | None = None  # set only by title-rows and similar


class LibraryUpNext(BaseModel):
    """The watch page's Up next for a Library item: the following episodes, or the movie's collection, else none."""

    kind: Literal["episodes", "collection", "none"] = "none"
    title: str | None = None  # the series or collection name
    title_id: str | None = None  # the series or boxset
    current_id: str | None = None  # the playing movie or episode (marked in a collection)
    items: list[TitleSummary] = Field(default_factory=list)


class TitleLetter(BaseModel):
    letter: str  # "#" or "A".."Z"
    index: int  # position of the group's first title in the full filtered list


class TitlePage(BaseModel):
    items: list[TitleSummary] = Field(default_factory=list)
    next_cursor: str | None = None
    total: int | None = None  # filtered count; only when the request has neither cursor nor letter
    letters: list[TitleLetter] | None = None  # same condition, and sort=name; present groups only, in list order
    start_index: int = 0  # position of items[0] in the full filtered list


class GenreFacet(BaseModel):
    name: str
    count: int


class YearRange(BaseModel):
    min: int
    max: int


class ResolutionFacet(BaseModel):
    value: TitleResolution
    count: int


class TitleFacets(BaseModel):
    genres: list[GenreFacet] = Field(default_factory=list)  # by name, case-insensitive
    years: YearRange | None = None  # None when no title has a year
    resolutions: list[ResolutionFacet] = Field(default_factory=list)  # count > 0 only, 4k -> sd order


class TitlePerson(BaseModel):
    id: str | None = None  # people.id (TMDB) or nfo_people.id (NFO); None only on refs scanned before 1.6.0
    name: str
    role: str | None = None
    type: PersonType
    image_url: str | None = None  # /api/people/{id}/image (TMDB) or a signed /api/art portrait (NFO photo); None = no photo


class TitleVersion(BaseModel):
    item_id: str
    label: str | None = None  # "4K HDR"
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    hdr: bool = False
    file_size: int | None = None
    duration_seconds: int | None = None
    media_state: str | None = None


class TitleExtra(BaseModel):
    item_id: str
    extra_type: ExtraType
    name: str
    duration_seconds: int | None = None
    artwork_url: str | None = None


class TitleMatch(BaseModel):
    method: MatchMethod
    score: float | None = None
    at: datetime | None = None


class AlbumTrack(BaseModel):
    """One track of an album page: a visible, non-missing Library item of the album."""

    item_id: str
    disc: int | None = None
    number: int | None = None
    name: str
    artist: str | None = None  # only when it differs (case-insensitively) from the album artist
    duration_seconds: int | None = None
    user_data: TitleUserData  # from the member's progress row on this item


class TitleDetail(TitleSummary):
    tagline: str | None = None
    studios: list[str] = Field(default_factory=list)
    premiered: str | None = None  # YYYY-MM-DD
    end_date: str | None = None
    status: str | None = None
    logo_url: str | None = None
    provider_ids: dict[str, str] = Field(default_factory=dict)
    people: list[TitlePerson] = Field(default_factory=list)
    versions: list[TitleVersion] = Field(default_factory=list)
    extras: list[TitleExtra] = Field(default_factory=list)
    children: list[TitleSummary] = Field(default_factory=list)  # series: seasons (Specials last); boxset: movies by release
    boxset: TitleSummary | None = None
    aired_episode_count: int | None = None
    play_next: TitleSummary | None = None  # series CTA: in-progress episode, else Next Up, else first episode
    match: TitleMatch | None = None  # admins only; None for members
    has_recap: bool = False
    logo: TitleArt | None = None  # Logo; preview, dominant and accent always None
    episode_count: int | None = None  # series/season: visible episodes
    best_height: int | None = None  # tallest probe height across visible versions (series: across episodes)
    tracks: list[AlbumTrack] | None = None  # album only: (disc NULLS LAST, number NULLS LAST, name NOCASE, id)


class TitleWatchedRequest(_Body):
    watched: bool


class TitleRow(BaseModel):
    id: str
    kind: TitleRowKind
    title: str  # rendered heading, e.g. "Because you watched Arrival"
    anchor_title_id: str | None = None
    items: list[TitleSummary] = Field(default_factory=list)


class TitleRowsResponse(BaseModel):
    rows: list[TitleRow] = Field(default_factory=list)


# ---- Library gallery ------------------------------

class LibrarySections(BaseModel):
    """GET /api/library/sections: what the member can see per Library tab (tab row, All kicker, chapters)."""

    movies: int  # visible wall titles by category
    shows: int
    anime: int
    albums: int  # visible album / artist titles
    artists: int
    saved_audio: int  # visible, non-missing Library items of kind audio
    youtube: int  # … kind video
    recordings: int  # … kind recording
    deleted: int  # visible items with status missing (the Deleted list)


class ItemProgress(BaseModel):
    """The member's progress on one Library item, on /api/library list pages only."""

    position_seconds: int = 0
    duration_seconds: int | None = None
    completed: bool = False


# ---- Recaps -------------------------------------------------------------------

class RecapCitation(BaseModel):
    episode_id: str
    cue_ordinal: int
    start_ms: int | None = None


class RecapPoint(BaseModel):
    text: str
    citations: list[RecapCitation] = Field(default_factory=list)


class RecapFallbackEpisode(BaseModel):
    episode_id: str
    name: str
    season_number: int | None = None
    index_number: int | None = None
    overview: str | None = None


class RecapResponse(BaseModel):
    episode_title_id: str
    state: RecapState
    points: list[RecapPoint] = Field(default_factory=list)
    fallback: list[RecapFallbackEpisode] = Field(default_factory=list)
    suggest_preroll: bool = False  # last played this series > 14 days ago and not the first episode
    error: str | None = None


class EpisodeSummaryText(BaseModel):
    episode_id: str
    overview: str  # whitespace-collapsed, <= 600 chars (word boundary + "…")


class EpisodeSummaries(BaseModel):
    available: bool
    items: list[EpisodeSummaryText] = Field(default_factory=list)  # completed episodes of the season only


class KeyScene(BaseModel):
    start_ms: int
    quote: str  # cue text: tags stripped, whitespace-collapsed, <= 160 chars
    caption: str  # key point text, <= 200 chars


class KeyScenes(BaseModel):
    available: bool
    title_id: str | None = None  # the source title (the episode, for a series)
    item_id: str | None = None  # the Library item to play
    scenes: list[KeyScene] = Field(default_factory=list)  # <= 3, stored order


# ---- Gallery: artwork preparation and client metrics (admin) -------------------

class ArtServing(BaseModel):
    """/api/art and Jellyfin rendition outcomes since the process started."""

    hits_memory: int = 0
    hits_disk: int = 0
    misses: int = 0
    generated: int = 0  # on-demand generations that finished
    fallback_original: int = 0  # budget exceeded, original served with no-store
    fallback_unavailable: int = 0  # budget exceeded, 503 + Retry-After
    failures: int = 0  # generation failed or timed out


class ArtworkProgress(BaseModel):
    total: int = 0  # (title, image type) pairs with a supported source
    prepared: int = 0  # ... whose row is ready for the current source_key
    failed: int = 0
    unsupported: int = 0
    cache_bytes: int = 0  # renditions/ on disk
    running: bool = False  # the background pass thread is alive
    paused_for_playback: bool = False  # waiting while a video transcode runs
    failure_reasons: dict[str, int] = Field(default_factory=dict)  # stable reason -> count; never file names
    serving: ArtServing = Field(default_factory=ArtServing)
    updated_at: datetime | None = None  # when the counters were last recomputed


IMAGE_KINDS = ("poster", "backdrop", "still", "logo", "square")
# The only labels each client metric accepts; anything else rejects the whole batch (422).
CLIENT_METRIC_LABELS: dict[str, frozenset[str]] = {
    "wall_first_screen_ms": frozenset({"movies", "shows", "all", "anime", "albums", "artists", "youtube", "recordings"}),
    "wall_sharp_ms": frozenset({"movies", "shows", "anime", "albums", "artists"}),
    "detail_hero_ms": frozenset({"click", "deep_link"}),
    "home_first_screen_ms": frozenset({"home"}),
    "home_hero_ms": frozenset({"home"}),
    "image_load_ms": frozenset(f"{kind}:{cache}" for kind in IMAGE_KINDS for cache in ("hit", "net")),
    "image_failed": frozenset(f"{kind}:{status}" for kind in IMAGE_KINDS for status in ("404", "transient", "exhausted")),
    "ttff_ms": frozenset({"direct", "direct_resume", "transcode_hw", "transcode_sw", "remux"}),
    "long_tasks": frozenset({"wall_scroll"}),
}


class ClientMetricSample(_Body):
    metric: ClientMetricName
    label: str = Field(max_length=40)
    value: float = Field(ge=0, le=3_600_000)  # ms for *_ms, 1 for image_failed, a count for long_tasks

    @model_validator(mode="after")
    def _known_label(self) -> ClientMetricSample:
        if self.label not in CLIENT_METRIC_LABELS[self.metric]:
            raise ValueError("unknown metric label")
        return self


class ClientMetricsBatch(_Body):
    csrf: str | None = Field(default=None, max_length=128)  # sendBeacon cannot set X-CSRF-Token
    samples: list[ClientMetricSample] = Field(max_length=500)


class MetricSummary(BaseModel):
    metric: ClientMetricName
    label: str
    today_count: int = 0
    today_p50: float | None = None
    today_p95: float | None = None
    week_count: int = 0  # the last 7 UTC days including today
    week_p50: float | None = None
    week_p95: float | None = None
    budget_p50: float | None = None
    budget_p95: float | None = None
    within_budget: bool | None = None  # None when there is no budget or no week samples


class MediaLoading(BaseModel):
    metrics: list[MetricSummary] = Field(default_factory=list)
    image_failure_rate_today: float | None = None  # image_failed / (image_failed + image_load_ms), 0..1
    image_failure_rate_week: float | None = None


# ---- Metadata (admin) ---------------------------------------------------------

class IdentifyCandidate(BaseModel):
    tmdb_id: int
    name: str
    original_name: str | None = None
    year: int | None = None
    overview: str | None = None
    poster_url: str | None = None
    score: float


class IdentifyRequest(_Body):
    tmdb_id: int = Field(gt=0)


class MetadataRefreshResponse(BaseModel):
    title_id: str
    metadata_due_at: datetime | None = None


class MetadataBulkRefreshResponse(BaseModel):
    queued: int


# ---- Connected apps -----------------------------------------------------------

class ConnectedApp(BaseModel):
    id: str
    user_id: str
    owner_display_name: str | None = None
    kind: ConnectedAppKind
    scope: ConnectedAppScope
    device_name: str
    client: str | None = None
    client_version: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None


class ConnectedAppCreateRequest(_Body):
    name: str = Field(min_length=1, max_length=120)
    scope: ConnectedAppScope


class ConnectedAppCreated(BaseModel):
    app: ConnectedApp
    token: str  # shown once; only its digest is stored


class AppPasswordCreateRequest(_Body):
    name: str = Field(min_length=1, max_length=120)


class AppPasswordCreated(BaseModel):
    app: ConnectedApp
    password: str  # shown once; only its digest is stored
    username: str
    server_address: str | None = None  # what to type into the app: the public address, else the local one, else the app's own URL
    local_server_address: str | None = None  # the local address, for apps at home (shown beside a different server_address)


class SignOutAllAppsResponse(BaseModel):
    revoked: int


# ---- Media server settings, transcoding ---------------------------------------

class MediaServerSettings(BaseModel):
    jellyfin_enabled: bool
    jellyfin_url: str | None = None  # the local address, else the app public URL (the API is served at the root); None without either
    jellyfin_public_url: str | None = None  # the public address, for apps away from home
    has_tmdb_key: bool
    metadata_language: str
    introdb_enabled: bool
    hwaccel: HwaccelMode
    max_playback_sessions: int
    transcode_cache_gb: int
    jellyfin_import_url: str | None = None  # members import watch history from here; None = off
    anime_folders: list[str] = Field(default_factory=list)  # ["Anime"] on a fresh server
    recategorising: bool = False  # a re-sort after an anime_folders change is running or queued
    members_edit_metadata: bool = False  # Members may edit details


class MediaServerSettingsUpdate(_Body):
    """Omitted fields are unchanged; ``tmdb_api_key: ""`` removes the key (write-only)."""

    jellyfin_enabled: bool | None = None
    tmdb_api_key: str | None = Field(default=None, max_length=512)
    metadata_language: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    introdb_enabled: bool | None = None
    hwaccel: HwaccelMode | None = None
    max_playback_sessions: int | None = Field(default=None, ge=1, le=16)
    transcode_cache_gb: int | None = Field(default=None, ge=1, le=1000)
    jellyfin_import_url: str | None = Field(default=None, max_length=2048)  # "" turns importing off
    anime_folders: list[AnimeFolder] | None = Field(default=None, max_length=ANIME_FOLDERS_MAX)
    members_edit_metadata: bool | None = None

    @field_validator("jellyfin_import_url")
    @classmethod
    def _import_url(cls, value: str | None) -> str | None:
        return None if value is None else validate_endpoint_url(value)

    @field_validator("anime_folders")
    @classmethod
    def _anime_folders(cls, value: list[str] | None) -> list[str] | None:
        """Never "." or "..", and one entry per name whatever its case (the first spelling stays)."""
        if value is None:
            return None
        if any(name in (".", "..") for name in value):
            raise ValueError("Choose a real folder name.")
        kept: dict[str, str] = {}
        for name in value:
            kept.setdefault(name.casefold(), name)
        return list(kept.values())


class ConnectionTestResponse(BaseModel):
    ok: bool
    error: str | None = None


# ---- Jellyfin history import (ADR 0010 amendment) ---------------------------


class JellyfinImportStatus(BaseModel):
    server: str | None = None  # the admin's Jellyfin address; None = importing is off


class JellyfinImportRequest(_Body):
    """The member's own Jellyfin sign-in, used for this one request and never stored. No address: members never choose it."""

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(default="", max_length=1024)  # Jellyfin allows an empty password


class JellyfinImportSummary(BaseModel):
    """Per Lumina title (or untitled item): what an import would change (preview) or changed (import)."""

    watched: int
    in_progress: int
    favorites: int
    up_to_date: int
    unmatched: int
    unmatched_names: list[str] = Field(default_factory=list)  # Jellyfin's own names, sorted, at most 200


class JellyfinHouseholdRequest(JellyfinImportRequest):
    """The admin's own Jellyfin administrator sign-in, plus the Jellyfin users they ticked in the preview."""

    jellyfin_ids: list[Annotated[str, Field(max_length=64)]] = Field(max_length=50)


class JellyfinHouseholdMember(BaseModel):
    """One Jellyfin user: where they land in Lumina, and what the preview would change or the import changed."""

    jellyfin_id: str
    jellyfin_name: str
    disabled: bool = False  # disabled in Jellyfin; the preview leaves them unticked
    action: Literal["import", "create", "skip"]
    lumina_username: str | None = None
    reason: str | None = None  # why "skip"
    error: str | None = None  # this user's history could not be read or saved
    summary: JellyfinImportSummary | None = None
    reset_url: str | None = None  # import only, new members only: the one time it is shown
    reset_expires_at: datetime | None = None


class JellyfinHousehold(BaseModel):
    members: list[JellyfinHouseholdMember]


class TranscodeDiagnostics(BaseModel):
    hwaccel: HwaccelMode
    active: HwaccelActive
    probe_ok: bool
    probe_error: str | None = None
    tonemap: TonemapMethod
    hardware_disabled: bool = False  # 3 consecutive hardware failures: software until restart
    software_fallbacks: int = 0
    sessions: dict[str, int] = Field(default_factory=dict)  # remux | audio | video_sw | video_hw -> count
    throttled: int = 0
    cache_bytes: int = 0
    cache_cap_bytes: int = 0
    speeds: dict[str, float] = Field(default_factory=dict)  # session id -> latest ffmpeg speed=
    recent_errors: list[str] = Field(default_factory=list)


# ---- Playback -----------------------------------------------------------------

class AudioTrack(BaseModel):
    index: int  # ffprobe stream index
    language: str | None = None
    label: str
    codec: str | None = None
    channels: int | None = None
    default: bool = False


class PlaybackSessionRequest(_Body):
    version_id: str | None = None
    audio_index: int | None = Field(default=None, ge=0)
    subtitle: str | None = Field(default=None, pattern=SUBTITLE_TRACK_ID_PATTERN)  # only i: tracks are burned in
    max_height: int | None = Field(default=None, ge=144, le=4320)


# ---- Subtitles and enrichment -------------------------------------------------

class SubtitleTrack(BaseModel):
    id: str  # SUBTITLE_TRACK_ID_PATTERN
    label: str  # "English (generated)", "English · SDH"
    language: str | None = None  # ISO-639-2 when known
    origin: SubtitleOrigin
    format: SubtitleFormat
    forced: bool = False
    default: bool = False
    hearing_impaired: bool = False
    url: str | None = None  # /api/library/{id}/subtitle-tracks/{track_id}.vtt; None for image tracks


class SubtitleTranslateRequest(_Body):
    target_language: str = Field(pattern=r"^[a-z]{2,3}$")


class EnrichmentJob(BaseModel):
    id: str
    library_item_id: str
    kind: EnrichmentJobKind
    state: EnrichmentJobState
    transcript_id: str | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class EnrichmentBulkRequest(_Body):
    title_id: str | None = None
    root_id: str | None = None
    kinds: list[EnrichmentJobKind] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_target(self) -> EnrichmentBulkRequest:
        if (self.title_id is None) == (self.root_id is None):
            raise ValueError("Give exactly one of title_id or root_id.")
        return self


class EnrichmentBulkResponse(BaseModel):
    queued: int
    skipped: int


# ---- Media segments and mute ranges -------------------------------------------

class MediaSegmentInput(_Body):
    type: SegmentType
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> MediaSegmentInput:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("A segment must end after it starts.")
        return self


class MediaSegment(BaseModel):
    type: SegmentType
    start_seconds: float
    end_seconds: float
    source: SegmentSource
    confidence: float


class MediaSegmentsUpdate(_Body):
    segments: list[MediaSegmentInput] = Field(max_length=50)


class MediaSegmentList(BaseModel):
    item_id: str
    segments: list[MediaSegment] = Field(default_factory=list)


class MuteRange(BaseModel):
    start_seconds: float
    end_seconds: float


# ---- Smart collections --------------------------------------------------------

class SmartRuleCondition(_Body):
    field: SmartRuleField
    op: SmartRuleOp
    value: str | int | float | list[str]


class SmartRuleSort(_Body):
    field: SmartRuleSortField
    order: SortOrder = "asc"


class SmartCollectionRule(_Body):
    """Structure only; per-field ops/value types are validated by services/smart_collections.py."""

    type: SmartRuleType
    match: SmartRuleMatch = "all"
    conditions: list[SmartRuleCondition] = Field(default_factory=list, max_length=20)
    sort: SmartRuleSort | None = None
    limit: int = Field(default=100, ge=1, le=500)


class SmartRuleDraftRequest(_Body):
    prompt: str = Field(min_length=1, max_length=500)


class SmartRuleSample(BaseModel):
    id: str
    name: str
    poster_url: str | None = None


class SmartRulePreview(BaseModel):
    count: int
    sample: list[SmartRuleSample] = Field(default_factory=list)


class SmartRuleDraft(BaseModel):
    rule: SmartCollectionRule
    preview: SmartRulePreview
    description: str


# ---- Member prefs (ui_prefs) --------------------------------------------------

class AutoSkipPref(BaseModel):
    intro: bool = False
    credits: bool = False
    recap: bool = False


class ProfanityPref(BaseModel):
    enabled: bool = False
    words: list[str] = Field(default_factory=list, max_length=PROFANITY_MAX_WORDS)

    @field_validator("words")
    @classmethod
    def _words(cls, words: list[str]) -> list[str]:
        cleaned = [word.strip() for word in words if word.strip()]
        if any(len(word) > PROFANITY_MAX_WORD_CHARS for word in cleaned):
            raise ValueError(f"Each word is at most {PROFANITY_MAX_WORD_CHARS} characters.")
        return cleaned


# ---- 2.1.0 metadata editor --------------------------
EditKind = Literal["edit", "lock", "revert", "bulk", "undo", "item_lock", "image"]
EditableImageType = Literal["Primary", "Backdrop", "Logo"]  # Thumb and Banner are out of 2.1.0
ImageOrigin = Literal["tmdb", "upload", "local", "embedded"]
RefreshOutcome = Literal["update", "same", "kept_edit", "kept_lock", "kept_higher_source", "new"]
BulkOpName = Literal["add", "remove", "set", "lock", "unlock", "lock_item", "unlock_item"]
BulkSkipReason = Literal["not_found", "locked_item", "wrong_type"]
UndoSkipReason = Literal["changed_since", "not_visible", "owner_only"]
VocabularyField = Literal["genres", "tags", "studios", "official_rating"]
EDIT_MAX_TITLES = 500
EDIT_MAX_CHANGES = 40
FieldKey = Annotated[str, StringConstraints(min_length=1, max_length=40)]
EditTitleId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ImageTag = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class KeptValue(BaseModel):
    source: FieldSource | None = None  # never "user"
    value: Any = None


class FieldState(BaseModel):
    value: Any = None  # a cleared field or removed image reads as None
    source: FieldSource | None = None
    locked: bool  # source "user", or the item is locked
    kept: KeptValue | None = None  # only when a kept source value exists


class TitleParentRef(BaseModel):
    id: str
    type: TitleType
    name: str


class TitleImageEntry(BaseModel):
    type: EditableImageType
    index: int  # 0; Backdrop 0-4
    url: str | None = None  # None for an empty slot
    tag: str | None = None  # the base_tag for writes; None for an empty slot
    origin: ImageOrigin | None = None
    source: FieldSource | None = None
    locked: bool = False
    width: int | None = None
    height: int | None = None


class EpisodeGroup(BaseModel):
    """A TMDB episode group (type 2 absolute, 3 DVD, ...), #164."""
    id: str
    name: str
    type: int | None = None
    episode_count: int | None = None
    group_count: int | None = None


class SeasonChoice(BaseModel):
    id: str
    index_number: int | None = None
    name: str


class TitleMetadataDoc(BaseModel):
    title_id: str
    type: TitleType
    name: str
    locked: bool
    parent: TitleParentRef | None = None
    fields: dict[str, FieldState]  # only the keys valid for this type, always present
    images: list[TitleImageEntry]
    can_identify: bool
    tmdb_configured: bool
    history_count: int
    seasons: list[SeasonChoice] = []  # an episode's: every season of its series, for the move picker (#164)


class FieldChange(_Body):
    value: Any = None  # None = clear
    base: Any = None  # the value the client loaded (optimistic concurrency)


class EditEntry(_Body):
    title_id: EditTitleId
    changes: dict[FieldKey, FieldChange] = Field(default_factory=dict, max_length=EDIT_MAX_CHANGES)
    pin: list[FieldKey] = Field(default_factory=list, max_length=EDIT_MAX_CHANGES)  # lock at the current value
    locked: bool | None = None  # the item lock; None = unchanged


class EditRequest(_Body):
    edits: list[EditEntry] = Field(min_length=1, max_length=EDIT_MAX_TITLES)


class EditConflict(BaseModel):
    title_id: str
    fields: list[str]
    current: dict[str, FieldState]


class EditResult(BaseModel):
    batch_id: str | None = None  # None when nothing was applied
    titles: list[TitleMetadataDoc]
    conflicts: list[EditConflict] = Field(default_factory=list)


class RevertRequest(_Body):
    fields: list[FieldKey] = Field(min_length=1, max_length=EDIT_MAX_CHANGES)


class RevertResult(BaseModel):
    batch_id: str | None = None  # None when no field was locked (nothing to undo)
    title: TitleMetadataDoc


class HistoryUser(BaseModel):
    id: str
    display_name: str  # "Former member" for a deactivated member


class HistoryChange(BaseModel):
    field: str
    before: Any = None  # values over 300 characters are cut to 300
    after: Any = None
    before_source: FieldSource | None = None
    after_source: FieldSource | None = None


class HistoryBatch(BaseModel):
    batch_id: str
    kind: EditKind
    user: HistoryUser | None = None
    created_at: datetime
    changes: list[HistoryChange]
    undone: bool
    title_count: int


class HistoryPage(BaseModel):
    batches: list[HistoryBatch]
    next_cursor: str | None = None


class UndoSkip(BaseModel):
    title_id: str
    field: str
    reason: UndoSkipReason


class UndoResult(BaseModel):
    batch_id: str
    restored: int
    skipped: list[UndoSkip]


class BulkOp(_Body):
    """add/remove: ``field`` in genres|tags|studios with ``values``; set: ``field`` in official_rating|custom_rating|status
    with ``value``; lock/unlock: ``fields``; lock_item/unlock_item: nothing else. Other shapes are refused with 422."""

    op: BulkOpName
    field: FieldKey | None = None
    fields: list[FieldKey] | None = Field(default=None, max_length=EDIT_MAX_CHANGES)
    values: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]] | None = Field(default=None, max_length=50)
    value: str | None = Field(default=None, max_length=120)


class BulkRequest(_Body):
    title_ids: list[EditTitleId] = Field(min_length=1, max_length=EDIT_MAX_TITLES)
    ops: list[BulkOp] = Field(default_factory=list, max_length=20)


class BulkSkip(BaseModel):
    title_id: str
    reason: BulkSkipReason


class BulkResult(BaseModel):
    batch_id: str | None = None
    applied: int
    skipped: list[BulkSkip]


class VocabularyEntry(BaseModel):
    value: str
    count: int


class PersonDoc(BaseModel):
    """One person in the household people editor (#164)."""
    person_id: str
    name: str  # what every title shows
    source_name: str  # what TMDB / the NFO files call them
    name_edited: bool
    photo_edited: bool
    image_url: str | None = None
    title_count: int  # titles the caller can see that credit them


class PersonNameEdit(_Body):
    name: str | None = Field(default=None, max_length=400)  # None = back to the source name


class PersonEditResult(BaseModel):
    batch_id: str | None
    person: PersonDoc


class PersonSuggestion(BaseModel):
    person_id: str
    name: str
    image_url: str | None = None


class RefreshPreviewField(BaseModel):
    field: str
    current: Any = None
    incoming: Any = None
    outcome: RefreshOutcome


class RefreshPreviewImage(BaseModel):
    type: EditableImageType
    current_url: str | None = None
    incoming_preview_url: str | None = None
    outcome: RefreshOutcome


class RefreshPreview(BaseModel):
    tmdb_id: int | None = None
    match: dict[str, Any] = Field(default_factory=dict)
    fields: list[RefreshPreviewField]
    images: list[RefreshPreviewImage]


class EpisodeTableSeason(BaseModel):
    id: str
    name: str
    index_number: int | None = None
    episode_count: int = 0


class EpisodeTableRow(BaseModel):
    title_id: str
    index_number: int | None = None
    index_number_end: int | None = None
    name: str
    premiered: str | None = None
    runtime_minutes: int | None = None
    overview: str | None = None
    community_rating: float | None = None
    locked_fields: list[str] = Field(default_factory=list)
    locked: bool = False
    still_url: str | None = None


class EpisodeTable(BaseModel):
    season: EpisodeTableSeason
    seasons: list[EpisodeTableSeason]  # every season of the series, for the season switcher
    episodes: list[EpisodeTableRow]  # by index_number, at most 500


class ImageCandidate(BaseModel):
    tmdb_path: str
    width: int
    height: int
    language: str | None = None
    vote: float = 0
    preview_url: str


class ImagePutTmdb(_Body):
    tmdb_path: str = Field(min_length=2, max_length=200)  # Checked against tmdb._IMAGE_PATH
    base_tag: ImageTag | None = None


class BackdropOrder(_Body):
    tags: list[ImageTag] = Field(min_length=1, max_length=5)  # the occupied backdrops' current tags, in the new order


# ---- 2.1.0 library automation ------------------------
ScanSchedule = Literal["off", "15m", "1h", "6h", "nightly"]
ScanTrigger = Literal["manual", "scheduled", "watch"]
AutomationState = Literal[
    "idle", "scanning", "preparing", "waiting_files", "waiting_confirmation", "offline", "unresponsive", "too_large", "needs_first_import",
]
AutomationSkipReason = Literal["scanning", "offline", "unresponsive", "waiting_confirmation", "needs_first_import"]
ImportRunErrorCode = Literal["offline", "permission_denied", "identity_mismatch", "root_removed", "root_empty"]
# watch_interval_s stays an inline Literal[60, 300, 900]: the contract test compares string literals only (TS: WatchIntervalS).


class AutomationPoller(BaseModel):
    heartbeat_at: datetime | None = None
    stalled: bool


class AutomationStateDetail(BaseModel):
    pending_files: int = 0
    dirs_listed: int = 0
    dirs_total: int | None = None
    since: datetime | None = None


class AutomationActiveRun(BaseModel):
    id: str
    trigger: ScanTrigger
    scope_dirs: int | None = None  # None = a full run
    inspected: int = 0


class AutomationRunCounters(BaseModel):
    indexed: int = 0
    updated: int = 0
    relinked: int = 0
    missing: int = 0


class AutomationLastRun(BaseModel):
    id: str
    trigger: ScanTrigger
    state: str
    finished_at: datetime | None = None
    scope_dirs: int | None = None
    counters: AutomationRunCounters
    error: str | None = None  # an ImportRunErrorCode, or anything else (shown raw)


class AutomationSkip(BaseModel):
    at: datetime
    reason: AutomationSkipReason


class AutomationRoot(BaseModel):
    root_id: str
    label: str
    schedule: ScanSchedule
    watch: bool
    watch_interval_s: Literal[60, 300, 900]
    state: AutomationState
    state_detail: AutomationStateDetail
    active_run: AutomationActiveRun | None = None
    last_run: AutomationLastRun | None = None
    last_full_run_at: datetime | None = None
    next_scan_at: datetime | None = None
    last_skip: AutomationSkip | None = None


class LibraryAutomation(BaseModel):
    server_timezone: str
    night_hour: int
    poller: AutomationPoller
    roots: list[AutomationRoot]


class AutomationRootPatch(_Body):
    schedule: ScanSchedule | None = None
    watch: bool | None = None
    watch_interval_s: Literal[60, 300, 900] | None = None


class NightHourPatch(_Body):
    night_hour: int = Field(ge=0, le=23)


class ScanRequest(_Body):
    root_id: str | None = Field(default=None, max_length=64)


class ScanStarted(BaseModel):
    root_id: str
    run_id: str


class ScanSkipped(BaseModel):
    root_id: str
    reason: AutomationSkipReason


class ScanLibrariesResult(BaseModel):
    started: list[ScanStarted]
    queued: list[str]
    skipped: list[ScanSkipped]


class AutomationDiagnosticsPoller(BaseModel):
    heartbeat_at: datetime | None = None
    stalled: bool
    last_error: str | None = None  # redacted


class AutomationDriver(BaseModel):
    active_run_id: str | None = None
    queued: int = 0


class AutomationRuns24h(BaseModel):
    manual: int = 0
    scheduled: int = 0
    watch: int = 0
    needs_confirmation: int = 0
    failed: int = 0


class AutomationRootDiagnostics(BaseModel):
    label: str
    schedule: ScanSchedule
    watch: bool
    state: AutomationState
    dirs_watched: int = 0
    pending_files: int = 0
    last_pass_at: datetime | None = None
    last_pass_seconds: float | None = None
    stats_last_pass: int = 0
    watch_errors_last_pass: int = 0
    last_change_at: datetime | None = None
    last_dispatch_at: datetime | None = None
    last_skip: AutomationSkip | None = None
    next_scan_at: datetime | None = None


class LibraryAutomationDiagnostics(BaseModel):
    poller: AutomationDiagnosticsPoller
    driver: AutomationDriver
    runs_24h: AutomationRuns24h
    roots: list[AutomationRootDiagnostics]
