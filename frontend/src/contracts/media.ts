import type * as Types from '../types';


export type JobStatus =
  | 'draft'
  | 'previewing'
  | 'ready'
  | 'queued'
  | 'running'
  | 'postprocessing'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';

export type OutputContainer = 'mp4' | 'webm' | 'mkv';
export type NoteVisibility = 'private' | 'household';
export type UserRole = 'admin' | 'viewer';
export type SearchSource = 'youtube' | 'soundcloud' | 'twitch' | 'kick';
export type MediaProvider = 'youtube' | 'twitch' | 'kick' | 'soundcloud' | 'generic' | 'unknown';
export type MediaLifecycle = 'vod' | 'live' | 'upcoming' | 'post_live' | 'completed_live';
// 'provider_not_supported': a recognized provider (e.g. Kick) whose public adapter
// has not landed yet; mirrors MediaSourceCapabilities in backend/app/schemas.py.
export type MediaCapabilityReason = 'live_playback_not_supported' | 'subscriber_only' | 'sign_in_required' | 'live_acquisition_not_supported' | 'live_record_not_supported' | 'upcoming_not_started' | 'upcoming_schedule_not_supported' | 'post_live_processing' | 'segmented_transport_not_supported' | 'provider_not_supported' | 'no_supported_transport';

export interface MediaSourceCapabilities {
  provider: MediaProvider;
  lifecycle: MediaLifecycle;
  can_play: boolean;
  play_reason?: MediaCapabilityReason | null;
  can_acquire: boolean;
  acquire_reason?: MediaCapabilityReason | null;
  // "Record from now" is a distinct deliberate action from ordinary acquisition,
  // offered only for a currently-live source Lumina can capture through the
  // guarded live-HLS transport.
  can_record?: boolean;
  record_reason?: MediaCapabilityReason | null;
  // "Schedule this broadcast" (issue #98) is offered for an upcoming source Lumina
  // can wait for and record. scheduled_start is the best-available provider start
  // time (may be absent); from_start_available advertises the best-effort/
  // experimental "record from the beginning" intent — never a raw yt-dlp flag.
  can_schedule?: boolean;
  schedule_reason?: MediaCapabilityReason | null;
  scheduled_start?: string | null;
  from_start_available?: boolean;
  chat: { live: 'unavailable' | 'available'; replay: 'unavailable' | 'available'; live_reason?: 'authentication_required' | null };
  // A never-inspected flat entry: actions are rechecked when the source is opened.
  provisional?: boolean;
}

export type LiveRecordingStatus =
  | 'waiting'
  | 'queued'
  | 'live'
  | 'stopping'
  | 'finalizing'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled';
// From-start is member INTENT, never a raw yt-dlp flag (issue #98).
export type LiveRecordingStartIntent = 'from_start' | 'live_edge';
export type LiveRecordingFallbackPolicy = 'allow_live_edge' | 'require_choice';
export type LiveRecordingCaptureOrigin = 'pending' | 'source_beginning' | 'live_edge';
export type LiveRecordingHistory = 'pending' | 'complete' | 'partial' | 'from_edge';
export type LiveRecordingMediaStatus =
  | 'pending'
  | 'recording'
  | 'finalizing'
  | 'completed'
  | 'partial'
  | 'failed';
export type LiveRecordingChatStatus = 'pending' | 'capturing' | 'completed' | 'unavailable' | 'failed';

export type LiveRecordingEndReason =
  | 'source_ended'
  | 'stopped'
  | 'time_limit'
  | 'size_limit'
  | 'disk_low'
  | 'owner_disabled'
  | 'shutdown'
  | 'interrupted';

export interface LiveRecordingMediaOutput {
  status: LiveRecordingMediaStatus;
  library_item_id?: string | null;
  failure_category?: string | null;
  error?: string | null;
  end_reason?: LiveRecordingEndReason | null;
}

export interface LiveRecordingChatOutput {
  status: LiveRecordingChatStatus;
  chat_asset_id?: string | null;
  failure_category?: string | null;
  error?: string | null;
}

export interface LiveRecording {
  id: string;
  source_url: string;
  title?: string | null;
  extractor?: string | null;
  status: LiveRecordingStatus;
  stop_requested: boolean;
  cancel_requested: boolean;
  media: LiveRecordingMediaOutput;
  chat: LiveRecordingChatOutput;
  // Scheduling + from-start (issue #98). capture_origin / history are the honest
  // answer to where capture began and whether the from-start history was complete
  // ('partial' is the explicit partial-history condition). awaiting_fallback_choice
  // marks a from-start-unavailable result a member can resolve from the edge;
  // waiting_reason explains a schedule that ended before it could connect.
  start_intent?: LiveRecordingStartIntent;
  fallback_policy?: LiveRecordingFallbackPolicy;
  scheduled_start_at?: string | null;
  from_start_supported?: boolean | null;
  capture_origin?: LiveRecordingCaptureOrigin;
  history?: LiveRecordingHistory;
  awaiting_fallback_choice?: boolean;
  waiting_reason?: string | null;
  // Capture resumed after a restart (a gap), the keep flag that exempts it
  // from retention, and the capture ceilings in force.
  resumed_after_restart?: boolean;
  kept?: boolean;
  max_runtime_seconds?: number | null;
  max_bytes?: number | null;
  created_at: string;
  started_at?: string | null;
  recording_started_at?: string | null;
  finished_at?: string | null;
}
export type TimedChatEventKind = 'message' | 'paid_message' | 'paid_sticker' | 'membership' | 'system';
export type TimedChatModeration = 'visible' | 'deleted' | 'author_removed';
export type ChatReplayAssetStatus =
  | 'building'
  | 'ready'
  | 'empty'
  | 'partial'
  | 'oversized'
  | 'unavailable'
  | 'malformed'
  | 'failed';

export interface TimedChatAuthor {
  name: string;
  channel_id?: string | null;
  badges: string[];
}

export interface TimedChatEmote {
  /** Unicode code-point offsets into the event's fallback text; end is exclusive. */
  start: number;
  end: number;
  label: string;
  artwork_url: string;
}

export interface TimedChatEvent {
  id: string;
  offset_ms: number | null;
  kind: TimedChatEventKind;
  text: string;
  author?: TimedChatAuthor | null;
  moderation: TimedChatModeration;
  amount?: string | null;
  emotes?: TimedChatEmote[];
  /** Origin channel display name for a relayed (Twitch shared-chat) message. */
  source_channel?: string | null;
}

export interface ChatReplayAsset {
  source_identity: string;
  status: ChatReplayAssetStatus;
  event_count: number;
  truncated: boolean;
  dropped_malformed: number;
  events: TimedChatEvent[];
  updated_at?: string | null;
}

export type FormatPreset = 'best' | 'best_1080p' | 'best_editable' | 'audio_only' | 'source' | 'custom';
export type SourceAutomationType = 'playlist' | 'channel' | 'search' | 'generic_url';
export type AutomationMediaKind = 'any' | 'video' | 'audio';
export type AutomationDuplicatePolicy = 'skip_same_source' | 'allow_media_variants';
export type AcquisitionErrorCategory = 'format_unavailable' | 'sign_in_required' | 'region_blocked' | 'removed' | 'rate_limited' | 'unsupported';

export interface AcquisitionErrorDetail {
  category: AcquisitionErrorCategory;
  message: string;
}

export interface PreviewRequest {
  source_url: string;
  lazy_playlist?: boolean;
  format_selection?: Types.FormatSelection;
  supported_profiles?: string[];
  entries_limit?: number;
}

export interface YouTubeSearchRequest {
  query: string;
  limit?: number;
}

export interface UpNextRequest {
  source_url: string;
  source_id?: string | null;
  source?: SearchSource;
  title?: string | null;
  uploader?: string | null;
  category_keys?: string[];
  limit?: number;
  /** The current video's channel, so Up Next reads its cached listing. */
  channel_id?: string | null;
  channel_url?: string | null;
  /** Recent sources in this playback chain, in canonical remote identity form. */
  excluded_sources?: string[];
}

export interface YouTubeSearchResult {
  language?: string | null;
  id?: string | null;
  title?: string | null;
  uploader?: string | null;
  duration?: number | null;
  thumbnail?: string | null;
  artwork_url?: string | null;
  channel_artwork_url?: string | null;
  webpage_url?: string | null;
  view_count?: number | null;
  availability?: string | null;
  published_at?: string | null;
  source?: SearchSource;
  source_label?: string | null;
  kind?: 'video' | 'short' | 'live' | 'channel' | 'playlist' | null;
  capabilities?: MediaSourceCapabilities | null;
  uploader_url?: string | null;
  uploader_id?: string | null;
  /** The member's saved copy and watch state; absent from responses the server has not annotated yet. */
  saved_item_id?: string | null;
  progress?: RemoteProgressAnnotation | null;
  /** Set on items of a served recommendation list only. */
  reco?: RecoAnnotation | null;
}

export interface SearchSourceError {
  source: SearchSource;
  message: string;
  retryable: boolean;
}

export interface YouTubeSearchResponse {
  query?: string;
  items: YouTubeSearchResult[];
  errors?: SearchSourceError[];
}

export interface PreviewEntry {
  id?: string | null;
  title?: string | null;
  uploader?: string | null;
  duration?: number | null;
  thumbnail?: string | null;
  artwork_url?: string | null;
  webpage_url?: string | null;
  availability?: string | null;
  capabilities?: MediaSourceCapabilities | null;
  published_at?: string | null;
  media_kind?: 'video' | 'audio' | null;
  channel_id?: string | null;
  channel_url?: string | null;
  view_count?: number | null;
  /** Optional size estimate for the playlist summary; absent when the provider does not say. */
  size_bytes?: number | null;
  saved_item_id?: string | null;
  progress?: RemoteProgressAnnotation | null;
}

export interface RemotePlayback {
  status: 'ready' | 'unsupported';
  stream_id: string;
  transport: 'progressive' | 'hls' | 'dash' | null;
  media_kind: 'video' | 'audio' | null;
  playback_url: string | null;
  content_type: string | null;
  has_video: boolean;
  has_audio: boolean;
  seekable: boolean;
  /** A currently-live edge: show a Live state, omit VOD duration and seeking. */
  live?: boolean;
  fallback_code?: string | null;
  fallback_message?: string | null;
  renditions?: RemoteRendition[];
  /** `'auto'` while adaptive playback is active; otherwise one opaque rendition id. */
  selected_rendition_id?: string | null;
  auto_available?: boolean;
}

export interface RemoteRendition {
  rendition_id: string;
  width?: number | null;
  height?: number | null;
  frame_rate?: number | null;
  bitrate_kbps?: number | null;
  video_codec: string;
  audio_codec: string;
  container: string;
  content_type: string;
  display_label: string;
}

export interface PopularSnapshot {
  items: Array<YouTubeSearchResult & { category_keys?: string[] }>;
  categories: Array<{ key: string; label: string; state: 'pending' | 'ready' | 'stale' | 'empty' | 'failed'; /** Provider-reported live channels in the category (2.4.0); null/absent when unknown. */ live_count?: number | null; last_success_at?: string | null; next_refresh_at?: string | null }>;
  state: 'loading' | 'ready' | 'partial' | 'stale' | 'empty' | 'failed';
  refreshing: boolean;
  stale: boolean;
  last_success_at?: string | null;
  refreshed_at?: string | null;
  next_refresh_at?: string | null;
  error?: string | null;
  /** Only /api/discovery/popular sets these (Explore's For you rail and category order). */
  for_you?: RemoteEntry[];
  category_order?: string[];
}

export interface LiveSnapshot extends PopularSnapshot {
  language_pending?: boolean;
  twitch_available: boolean;
  /** Provider-reported live channels overall (2.4.0); null/absent when unknown. */
  live_total?: number | null;
  hero: Array<YouTubeSearchResult & { category_keys?: string[] }>;
  followed_unavailable?: Array<{ source: SearchSource; checked_at: string }>;
}

/** One page of a See all wall (2.4.0): opaque cursor, null at the end. */
export interface WallPage {
  items: RemoteEntry[];
  next_cursor: string | null;
  live_count?: number | null;
}

/** The member's own position in a remote entry, or in its saved copy. */
export interface RemoteProgressAnnotation {
  position_seconds: number;
  duration_seconds?: number | null;
  completed: boolean;
}

/** A remote entry as every live and YouTube surface lists it: a search result, possibly from a live or popular snapshot. */
export type RemoteEntry = YouTubeSearchResult & { category_keys?: string[] };

/** A channel page tab in the API's words. */
export type ChannelTab = 'videos' | 'streams' | 'shorts' | 'playlists';

export interface ChannelLiveResponse {
  webpage_url: string;
  title?: string | null;
  view_count?: number | null;
  artwork_url?: string | null;
}

export interface ChannelHeaderResponse {
  id: string;
  name: string;
  handle?: string | null;
  url: string;
  avatar_url?: string | null;
  banner_url?: string | null;
  follower_count?: number | null;
  video_count?: number | null;
  description?: string | null;
  verified: boolean;
  tabs: ChannelTab[];
  follow_id?: string | null;
  live?: ChannelLiveResponse | null;
}

export interface ChannelPageResponse {
  channel: ChannelHeaderResponse;
  tab: ChannelTab;
  entries: YouTubeSearchResult[];
  has_more: boolean;
  restricted: boolean;
  fetched_at: string;
  stale: boolean;
}

export interface ChannelResolveRequest {
  url: string;
}

export interface ChannelResolveResponse {
  provider: 'youtube';
  channel_id: string;
}

export interface LibraryChannelResponse {
  key: string;
  extractor: string;
  name: string;
  uploader: string;
  count: number;
  unwatched_count: number;
  newest_item_id: string;
  newest_at: string;
  channel_id?: string | null;
  avatar_url?: string | null;
}

export interface InterestCategory {
  key: string;
  label: string;
}

export interface MemberInterests {
  categories: InterestCategory[];
  selected_keys: string[];
}

export type OnboardingStatus = 'pending' | 'completed' | 'skipped';

export interface ChannelCandidate {
  channel_key: string;
  source_url: string;
  display_name: string;
  source: string;
  source_label: string;
  artwork_url: string | null;
  preview_artwork_url?: string | null;
  preview_title?: string | null;
  category_keys: string[];
  following: boolean;
}

export interface CategoryChannelSuggestions {
  key: string;
  label: string;
  state: 'ranked' | 'curated' | 'empty';
  channels: ChannelCandidate[];
}

export interface ChannelSuggestions {
  categories: CategoryChannelSuggestions[];
}

export interface ChannelCandidateList {
  query: string;
  channels: ChannelCandidate[];
}

export interface FollowChannelRequest {
  source_url: string;
  display_name: string;
}

export type FollowOutcomeStatus = 'created' | 'existing' | 'invalid';

export interface FollowOutcome {
  channel_key: string;
  display_name: string;
  status: FollowOutcomeStatus;
  automation_id: string | null;
}

export interface MemberOnboardingState {
  status: OnboardingStatus;
  selected_keys: string[];
  followed: FollowOutcome[];
}

export interface MemberRecommendationSnapshot extends PopularSnapshot {}

export type SuppressionScope = 'item' | 'channel' | 'fewer' | 'title';

export interface SuppressRecommendationInput {
  scope: SuppressionScope;
  source?: string;
  source_id?: string | null;
  source_url?: string | null;
  title?: string | null;
  uploader?: string | null;
  /** The stable channel (channel, fewer), the title (title), and the list it was used on. */
  channel_id?: string | null;
  channel_url?: string | null;
  title_id?: string | null;
  list_id?: string | null;
  key?: string | null;
}

export interface Suppression {
  id: string;
  scope: SuppressionScope;
  target_key: string;
  title: string | null;
  channel_name: string | null;
  source: string | null;
  created_at: string;
  /** Scope "fewer": when the channel is back to normal (created_at + 140 days). */
  recovers_at?: string | null;
}

export interface SuppressionList {
  items: Suppression[];
  channels: Suppression[];
  /** The server always sends both; optional so existing literals compile. */
  fewer?: Suppression[];
  titles?: Suppression[];
}

/** Where a served list appears, why an item is in it, and which slot it holds. */
export type RecoSurface = 'home_picked' | 'home_recommended' | 'home_because' | 'explore_for_you' | 'explore_popular' | 'up_next' | 'title_similar';
export type RecoReasonCode = 'follow_new' | 'channel' | 'finished' | 'interest' | 'popular' | 'new_arrival' | 'well_rated' | 'like_current' | 'same_channel' | 'like_anchor' | 'next_part' | 'next_episode' | 'explore';
export type RecoSlot = 'exploit' | 'explore' | 'pinned';
export type RecoClientEventKind = 'impression' | 'open';

/** Why one item is in one served list. `key` is opaque: echo it back in events, never parse it. */
export interface RecoAnnotation {
  list_id: string;
  key: string;
  position: number;
  slot: RecoSlot;
  reason_code: RecoReasonCode;
  reason: string;
}

/** What the client may report: the server fills surface, position, slot and reason from the list it served. */
export interface RecoEventIn {
  kind: RecoClientEventKind;
  list_id: string;
  key: string;
  age_ms: number;
}

export interface RecoEventBatch {
  csrf?: string | null;
  events: RecoEventIn[];
}

/** Diagnostics' recommendations section: household totals; a rate is null under 50 in its denominator. */
export interface RecoSurfaceStats {
  surface: RecoSurface;
  impressions: number;
  opens: number;
  ctr?: number | null;
  plays: number;
  play_through_median?: number | null;
  completion_rate?: number | null;
  negative_rate?: number | null;
  explore_play_rate?: number | null;
  exploit_play_rate?: number | null;
}

export interface RecoPoolHealth {
  members_with_pool: number;
  median_pool_size: number;
  oldest_refresh_age_minutes?: number | null;
  provider_calls_24h: number;
  budget_hits_24h: number;
  remote_vector_coverage?: number | null;
  dropped_events_24h: number;
}

export interface RecoDiagnostics {
  enabled: boolean;
  window_days: number;
  surfaces: RecoSurfaceStats[];
  reco_share_of_remote_plays?: number | null;
  pool: RecoPoolHealth;
}

export interface SearchHistoryEntry {
  id: string;
  query: string;
  searched_at: string;
}

export interface PreviewResponse {
  kind: 'video' | 'playlist';
  title?: string | null;
  extractor?: string | null;
  extractor_key?: string | null;
  webpage_url?: string | null;
  availability?: string | null;
  capabilities?: MediaSourceCapabilities | null;
  artwork_url?: string | null;
  chapters?: Array<{ start_time: number; end_time?: number | null; title: string }>;
  description_timestamps?: Array<{ start: number; end: number; seconds: number; label: string }>;
  entries: PreviewEntry[];
  playback?: RemotePlayback | null;
  raw: Record<string, unknown>;
}

export type AcquisitionBatchStatus = 'dispatching' | 'queued' | 'partial' | 'failed' | 'completed' | 'duplicate';
export type AcquisitionEntryStatus = 'pending' | 'dispatching' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'duplicate';

export interface AcquisitionBatchEntryRequest {
  source_url: string;
  extractor?: string | null;
  remote_id?: string | null;
  title?: string | null;
  thumbnail?: string | null;
  uploader?: string | null;
  duration?: number | null;
  availability?: string | null;
}

export interface AcquisitionBatchCreateRequest {
  source_url: string;
  source_title?: string | null;
  source_provenance?: {
    extractor?: string | null;
    extractor_key?: string | null;
    playlist_id?: string | null;
    playlist_title?: string | null;
    uploader?: string | null;
    channel?: string | null;
    webpage_url?: string | null;
    thumbnail?: string | null;
  };
  format_selection: Types.FormatSelection;
  output_profile: Types.OutputProfile;
  entries: AcquisitionBatchEntryRequest[];
}

export interface AcquisitionBatchEntry extends AcquisitionBatchEntryRequest {
  id: string;
  batch_id: string;
  selection_index: number;
  status: AcquisitionEntryStatus;
  progress: number;
  dispatch_attempts: number;
  failure_category?: string | null;
  error?: string | null;
  details: Record<string, unknown>;
  download_job_id?: string | null;
  library_item_id?: string | null;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
}

export interface AcquisitionBatch {
  id: string;
  source_url: string;
  source_title?: string | null;
  source_provenance: AcquisitionBatchCreateRequest['source_provenance'];
  status: AcquisitionBatchStatus;
  selected_count: number;
  queued_count: number;
  duplicate_count: number;
  completed_count: number;
  failed_count: number;
  progress: number;
  format_selection: Record<string, unknown>;
  output_profile: Record<string, unknown>;
  entries: AcquisitionBatchEntry[];
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
}
