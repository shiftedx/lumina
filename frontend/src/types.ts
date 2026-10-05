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

export interface TimedChatEvent {
  id: string;
  offset_ms: number | null;
  kind: TimedChatEventKind;
  text: string;
  author?: TimedChatAuthor | null;
  moderation: TimedChatModeration;
  amount?: string | null;
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
  format_selection?: FormatSelection;
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
}

export interface YouTubeSearchResult {
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
  format_selection: FormatSelection;
  output_profile: OutputProfile;
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

export interface DownloadJob {
  id: string;
  user_id?: string | null;
  source_url: string;
  status: JobStatus;
  queue_position?: number | null;
  title?: string | null;
  artwork_url?: string | null;
  extractor?: string | null;
  format_selection?: Record<string, unknown> | null;
  /** Set when the saved file differs from what the preset asked for (e.g. no H.264 for Editable). */
  format_resolution?: { fallback_reason?: string | null } | null;
  output_profile?: Record<string, unknown> | null;
  output_container?: OutputContainer | string | null;
  progress?: number | null;
  progress_status?: string | null;
  downloaded_bytes?: number | null;
  total_bytes?: number | null;
  eta?: number | null;
  speed?: number | null;
  filename?: string | null;
  postprocessor?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  preview_snapshot?: Record<string, unknown> | null;
  /** Earlier attempts' outcomes; a retry never erases them. */
  attempts?: Array<{ status: string; error?: string | null; started_at?: string | null; finished_at?: string | null }>;
  /** Where each published file went: root label + folder, never a path. */
  outputs?: Array<{ library_item_id?: string | null; root_label?: string | null; folder: string }>;
  /** Set when a batch save dispatched this job; it retries through that batch entry. */
  acquisition_batch_id?: string | null;
  acquisition_entry_id?: string | null;
}

export interface LibraryItem {
  id: string;
  user_id?: string | null;
  visibility?: 'private' | 'shared';
  owner_username?: string | null;
  owner_display_name?: string | null;
  extractor?: string | null;
  remote_id?: string | null;
  title: string;
  uploader?: string | null;
  playlist_name?: string | null;
  duration?: number | null;
  thumbnail_url?: string | null;
  artwork_url?: string | null;
  chapters?: Array<{ start_time: number; end_time?: number | null; title: string }>;
  description_timestamps?: Array<{ start: number; end: number; seconds: number; label: string }>;
  webpage_url?: string | null;
  file_size?: number | null;
  downloaded_at?: string | null;
  availability?: string | null;
  status?: 'available' | 'missing' | 'partial';
  kind?: 'video' | 'audio' | 'movie' | 'episode' | 'track' | 'recording' | 'extra';
  /** Where the bytes stand: offline = the root's last observation was not online; null = unknown / no file. */
  media_state?: 'available' | 'quarantined' | 'missing' | 'offline' | null;
  metadata_json?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
  title_id?: string | null;
  extra_type?: ExtraType | null;
  /** The member's progress; /api/library list pages only. */
  progress?: ItemProgress | null;
}

export type LibraryViewKind = 'video' | 'audio' | 'movie' | 'episode' | 'track' | 'recording' | 'music';

export interface LibraryPage {
  items: LibraryItem[];
  next_cursor: string | null;
}

export interface JobPage {
  items: DownloadJob[];
  next_cursor: string | null;
}

export interface LiveRecordingPage {
  items: LiveRecording[];
  next_cursor: string | null;
}

export type CollectionVisibility = 'private' | 'shared';

// A mixed-source collection entry: a library item id, or a remote source
// snapshot (same shape as WatchQueueEntry). Never a download request.
export type CollectionEntryRef = { kind: 'library' | 'remote'; library_item_id: string | null; provider: string | null; remote_id: string | null; url: string | null };

export type CollectionEntry = {
  id: string;
  position: number;
  availability: 'available' | 'unavailable';
  ref: CollectionEntryRef;
  title: string | null;
  uploader: string | null;
  artwork_url: string | null;
  duration: number | null;
};

export interface HouseholdCollection {
  id: string;
  owner_user_id: string;
  name: string;
  description?: string | null;
  visibility: CollectionVisibility;
  revision: number;
  item_count: number;
  items: LibraryItem[];
  entries: CollectionEntry[];
  rules?: SmartCollectionRule | null;
  titles?: TitleSummary[];
  created_at: string;
  updated_at: string;
}

export type LocalSearchMode = 'hybrid' | 'lexical';
export type LocalSearchMatchMode = 'hybrid' | 'lexical' | 'semantic';

export interface LocalSearchMatch {
  kind: 'library' | 'channel' | 'automation' | 'title' | 'moment';
  id: string;
  title: string;
  subtitle: string;
  score: number;
  lexical_score: number;
  semantic_score: number;
  match_mode: LocalSearchMatchMode;
  item?: LibraryItem | null;
  source_url?: string | null;
  source_type?: SourceAutomationType | null;
  title_id?: string | null;
  start_ms?: number | null;
  media_title?: TitleSummary | null;
}

export interface LocalSearchResponse {
  query: string;
  mode: LocalSearchMode;
  matches: LocalSearchMatch[];
  items: LibraryItem[];
  index_generation: number;
}

export interface PlaybackProgress {
  id: string;
  user_id: string;
  item_id: string;
  position_seconds: number;
  duration_seconds?: number | null;
  completed: boolean;
  last_watched_at: string;
  created_at: string;
  updated_at: string;
  item: LibraryItem;
  title?: TitleSummary | null;
}

export interface RemotePlaybackProgress {
  id: string;
  user_id: string;
  source_identity: string;
  source_url: string;
  title?: string | null;
  uploader?: string | null;
  artwork_url?: string | null;
  position_seconds: number;
  duration_seconds?: number | null;
  completed: boolean;
  checkpoint_client_id: string;
  checkpoint_sequence: number;
  checkpoint_revision: number;
  cleared: boolean;
  last_watched_at: string;
  created_at: string;
  updated_at: string;
}

export interface LibraryNote {
  id: string;
  item_id: string;
  user_id?: string | null;
  visibility: NoteVisibility;
  /** Playback position in milliseconds; null for a general note. */
  timestamp_ms?: number | null;
  body: string;
  author_username?: string | null;
  author_display_name?: string | null;
  is_owner?: boolean;
  can_delete?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface UserTag {
  id: string;
  item_id: string;
  user_id?: string | null;
  tag: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface FormatSelection {
  preset: FormatPreset;
  output_container: OutputContainer;
  custom_format?: string | null;
  extract_audio?: boolean;
  audio_format?: string | null;
  embed_thumbnail?: boolean;
  embed_metadata?: boolean;
  subtitles?: boolean;
}

export interface OutputProfile {
  base_path?: string | null;
  subdir?: string | null;
  template?: string;
  organize_by?: 'downloads' | 'playlist' | 'uploader';
}

export interface AutomationRuleSet {
  include_title: string[];
  exclude_title: string[];
  include_uploader: string[];
  exclude_uploader: string[];
  include_source: string[];
  exclude_source: string[];
  media_kind: AutomationMediaKind;
  min_duration?: number | null;
  max_duration?: number | null;
  max_age_days?: number | null;
}

export interface UserDownloadDefaults {
  format_selection: FormatSelection;
  output_profile: OutputProfile;
}

export interface UserAutomationDefaults {
  cron_expression: string;
  auto_download: boolean;
  format_selection: FormatSelection;
  output_profile: OutputProfile;
  rules: AutomationRuleSet;
  duplicate_policy: AutomationDuplicatePolicy;
  max_items_per_run?: number | null;
  max_items_per_day?: number | null;
  backfill_limit?: number | null;
}

export interface UserSettings {
  id: string;
  user_id: string;
  download_defaults: UserDownloadDefaults;
  automation_defaults: UserAutomationDefaults;
  ui_prefs: Record<string, unknown>;
  notification_prefs: Record<string, unknown>;
  remote_playback_cache: RemotePlaybackCachePreferences;
  resolved_download_defaults: UserDownloadDefaults;
  resolved_automation_defaults: UserAutomationDefaults;
  created_at: string;
  updated_at: string;
}

export interface RemotePlaybackCachePreferences {
  enabled: boolean;
  recent_video_limit: 3 | 5 | 10 | 20;
  storage_limit_mb: 512 | 2048 | 5120 | 10240 | 25600;
}

export interface UserSettingsUpdateRequest {
  download_defaults?: UserDownloadDefaults;
  automation_defaults?: UserAutomationDefaults;
  ui_prefs?: Record<string, unknown>;
  notification_prefs?: Record<string, unknown>;
  remote_playback_cache?: RemotePlaybackCachePreferences;
}

export interface SourceAutomation {
  id: string;
  user_id: string;
  label: string;
  source_url: string;
  source_type: SourceAutomationType;
  artwork_url?: string | null;
  cron_expression: string;
  active: boolean;
  auto_download: boolean;
  format_selection: Record<string, unknown>;
  output_profile: Record<string, unknown>;
  rules: AutomationRuleSet;
  duplicate_policy: AutomationDuplicatePolicy;
  max_items_per_run?: number | null;
  max_items_per_day?: number | null;
  backfill_limit?: number | null;
  last_checked_at?: string | null;
  next_check_at?: string | null;
  last_error?: string | null;
  last_run_summary: Record<string, unknown>;
  /** Last-known newest entries from the latest successful check (kept through failures). */
  feed_entries?: PreviewEntry[];
  created_at: string;
  updated_at: string;
}

export interface SourceAutomationCreateRequest {
  label: string;
  source_url: string;
  source_type: SourceAutomationType;
  cron_expression: string;
  active: boolean;
  auto_download: boolean;
  format_selection?: FormatSelection | null;
  output_profile?: OutputProfile | null;
  rules: AutomationRuleSet;
  duplicate_policy: AutomationDuplicatePolicy;
  max_items_per_run?: number | null;
  max_items_per_day?: number | null;
  backfill_limit?: number | null;
}

export interface SourceAutomationAutoDownloadRequest { enabled: boolean }

export interface AutomationDecision {
  id?: string | null;
  run_id?: string | null;
  automation_id: string;
  remote_id?: string | null;
  source_url?: string | null;
  title?: string | null;
  action: 'queued' | 'would_queue' | 'manual' | 'skipped_duplicate' | 'skipped_rule' | 'skipped_limit' | 'failed';
  reason: string;
  details: Record<string, unknown>;
  created_at?: string | null;
}

export interface AutomationRun {
  id: string;
  automation_id: string;
  user_id: string;
  status: 'running' | 'completed' | 'failed' | 'preview';
  started_at: string;
  finished_at?: string | null;
  discovered_count: number;
  matched_count: number;
  queued_count: number;
  manual_count: number;
  skipped_count: number;
  failed_count: number;
  error?: string | null;
  summary_json: Record<string, unknown>;
  decisions: AutomationDecision[];
}

/** Member-safe health: liveness (`/api/health`) or runtime status + version (`/api/runtime-health`). */
export interface HealthState {
  status: 'ok' | 'degraded';
  version?: string;
}

export interface UserProfile {
  id: string;
  username: string;
  display_name: string;
  role: UserRole;
  is_active: boolean;
  onboarding_status?: OnboardingStatus;
  has_local_password?: boolean;
  two_factor_enabled?: boolean;
  /** Vault owner, the household requires two-step for owners, and they have not enrolled: admin calls answer 403 two_factor_setup_required. */
  two_factor_setup_required?: boolean;
  can_edit_details?: boolean;
  bio?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SessionState {
  user: UserProfile;
}

export interface DeviceMember {
  user_id: string;
  display_name: string;
  username: string;
  role: UserRole;
  switch: 'instant' | 'password';
  active: boolean;
}

export interface BootstrapStatus {
  needs_setup: boolean;
}

export interface AdminSettings {
  temp_root: string;
  archive_path: string;
  concurrency: number;
  max_active_jobs_per_user: number;
  min_free_disk_mb: number;
  /** When a saved change applies: next process start, or the next admitted download. */
  setting_effects: Record<'concurrency' | 'max_active_jobs_per_user' | 'min_free_disk_mb', 'restart' | 'new_jobs'>;
  ffmpeg_path?: string | null;
  yt_dlp_defaults: Record<string, unknown>;
  ui_prefs: Record<string, unknown>;
  webhook_url?: string | null;
  webhook_enabled?: boolean;
  webhook_notify_new_videos?: boolean;
  webhook_notify_failures?: boolean;
  /** Ports beyond 80/443 that sources may use (public addresses only). */
  extra_source_ports?: number[];
  require_owner_two_factor?: boolean;
  updated_at?: string | null;
}

/** GET /api/admin/overview: aggregates only; `null` means the sensor is unavailable, not zero. */
export interface AdminOverview {
  generated_at: string;
  jobs_by_status: Record<string, number>;
  recent_failures: { reason: string; count: number; last_at: string | null }[];
  failure_window_days: number;
  concurrency: number;
  max_active_jobs_per_user: number;
  min_free_disk_mb: number;
  library_free_bytes: number | null;
  library_items_by_status: Record<string, number>;
  roots: {
    id: string;
    label: string;
    mode: 'managed' | 'external';
    enabled: boolean;
    state: string | null;
    checked_at: string | null;
    free_bytes: number | null;
    total_bytes: number | null;
    minimum_free_bytes: number;
    artifact_count: number;
    artifact_bytes: number;
  }[];
  event_streams: number;
  persistence: Record<string, { count: number; errors: number; busy_timeouts: number; total_ms: number; max_wait_ms: number }>;
}

export interface AppEvent {
  type:
    | 'preview_ready'
    | 'job_queued'
    | 'job_started'
    | 'job_progress'
    | 'job_postprocess'
    | 'job_completed'
    | 'job_failed'
    | 'library_item_upserted'
    | 'library_item_missing'
    | 'settings_updated'
    | 'automation_checked'
    | 'automation_item_decided'
    | 'automation_failed'
    | 'model_state';
  payload: unknown;
}

export type WatchQueueRef =
  | { kind: 'library'; library_item_id: string }
  | { kind: 'remote'; provider?: string | null; remote_id?: string | null; url: string; title?: string | null; uploader?: string | null; artwork_url?: string | null; duration?: number | null };

export type WatchQueueEntry = {
  id: string;
  position: number;
  availability: 'available' | 'unavailable';
  ref: { kind: 'library' | 'remote'; library_item_id: string | null; provider: string | null; remote_id: string | null; url: string | null };
  title: string | null;
  uploader: string | null;
  artwork_url: string | null;
  duration: number | null;
};

export type WatchQueue = { revision: number; limit: number; entries: WatchQueueEntry[] };

export type LocalPlaybackOptions = {
  mode: 'direct' | 'remux' | 'transcode' | 'unavailable';
  reason: string | null;
  facts: { container: string; video_codec: string | null; audio_codec: string | null; width: number | null; height: number | null; duration: number | null } | null;
  audio_tracks?: AudioTrack[];
  quality_heights?: number[];
  loudness_gain_db?: number | null;
  free_video_slots?: number;
};

export type LocalPlaybackSession = { session_id: string; mode: 'remux' | 'transcode'; playback_url: string; start: number; kind?: 'remux' | 'audio' | 'video_sw' | 'video_hw' };

// ---------------------------------------------------------------------------
// Media vault (frozen 2026-09-25). Mirrors backend/app/media_schemas.py
// field-for-field; backend/tests/test_media_contract.py fails on drift. Change
// both sides together, and only by adding optional fields.
// ---------------------------------------------------------------------------

export type TitleType = 'series' | 'season' | 'episode' | 'movie' | 'boxset' | 'album' | 'artist';
export type ExtraType = 'trailer' | 'featurette' | 'behindthescenes' | 'deletedscene' | 'interview' | 'scene' | 'short' | 'clip' | 'other';
export type FieldSource = 'user' | 'nfo' | 'tmdb' | 'path';
export type ImageType = 'Primary' | 'Backdrop' | 'Logo' | 'Thumb' | 'Banner';
export type MatchMethod = 'id' | 'find' | 'search' | 'llm' | 'user';
export type TitleSort = 'name' | 'created' | 'year' | 'rating';
export type PersonType = 'Actor' | 'Director' | 'Writer' | 'Creator' | 'GuestStar' | 'Producer' | 'Composer';
export type ConnectedAppKind = 'jellyfin' | 'agent' | 'app_password';
export type ConnectedAppScope = 'read' | 'write';
export type HwaccelMode = 'auto' | 'off' | 'qsv' | 'vaapi';
export type HwaccelActive = 'qsv' | 'vaapi' | 'none';
export type TonemapMethod = 'opencl' | 'vpp_qsv' | 'software' | 'none';
export type SubtitleOrigin = 'embedded' | 'sidecar' | 'caption' | 'generated' | 'synced' | 'translated';
export type SubtitleFormat = 'text' | 'image';
export type EnrichmentJobKind = 'asr' | 'sync' | 'translate' | 'segments' | 'captions';
export type EnrichmentJobState = 'pending' | 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'interrupted';
export type SegmentType = 'intro' | 'credits' | 'recap' | 'preview' | 'commercial';
export type SegmentSource = 'user' | 'fingerprint' | 'introdb' | 'heuristic';
export type RecapState = 'none' | 'queued' | 'running' | 'succeeded' | 'failed' | 'fallback';
export type TitleRowKind = 'because_you_watched' | 'recommended';
export type SmartRuleType = 'movie' | 'series' | 'episode' | 'channel_video';
export type SmartRuleMatch = 'all' | 'any';
export type SmartRuleField = 'genre' | 'year' | 'rating' | 'official_rating' | 'people' | 'provider' | 'watched' | 'added' | 'runtime' | 'channel' | 'tags';
export type SmartRuleOp = 'is' | 'is_not' | 'in' | 'not_in' | 'gte' | 'lte' | 'within_days';
export type SmartRuleSortField = 'name' | 'year' | 'rating' | 'added' | 'runtime';
export type SortOrder = 'asc' | 'desc';
export type TitleResolution = '4k' | '1080p' | '720p' | 'sd';
export type SearchScope = 'movies' | 'shows' | 'anime';
export type TitleCategory = 'movies' | 'shows' | 'anime';
export type ClientMetricName = 'wall_first_screen_ms' | 'wall_sharp_ms' | 'detail_hero_ms' | 'home_first_screen_ms' | 'home_hero_ms' | 'image_load_ms' | 'image_failed' | 'ttff_ms' | 'long_tasks';

/** e:{stream} embedded text | s:{n} sidecar | t:{transcript id} | i:{stream} image (burned in). */
export const SUBTITLE_TRACK_ID_PATTERN = /^(?:[esi]:\d{1,4}|t:[0-9a-f-]{36})$/;

export interface TitleUserData {
  played: boolean;
  is_favorite: boolean;
  position_seconds: number;
  duration_seconds?: number | null;
  last_watched_at?: string | null;
  resume_item_id?: string | null;
  unplayed_count?: number | null;
}

/** One title image. `rendition` holds a literal `{w}`; substitute one of `widths`. */
export interface TitleArt {
  url: string;
  rendition?: string | null;
  widths: number[];
  width?: number | null;
  height?: number | null;
  preview?: string | null;
  dominant?: string | null;
  accent?: string | null;
}

export interface TitleSummary {
  id: string;
  type: TitleType;
  name: string;
  sort_name?: string | null;
  year?: number | null;
  index_number?: number | null;
  index_number_end?: number | null;
  parent_id?: string | null;
  series_id?: string | null;
  series_name?: string | null;
  season_number?: number | null;
  overview?: string | null;
  genres: string[];
  official_rating?: string | null;
  community_rating?: number | null;
  runtime_seconds?: number | null;
  poster_url?: string | null;
  backdrop_url?: string | null;
  play_item_id?: string | null;
  added_at: string;
  user_data: TitleUserData;
  poster?: TitleArt | null;
  backdrop?: TitleArt | null;
  category?: TitleCategory | null;
  artist_name?: string | null;
  child_count?: number | null;
  /** Set only by /api/home/title-rows and /api/titles/{id}/similar. */
  reco?: RecoAnnotation | null;
}

/** The watch page's Up next for a Library item: following episodes, a movie's collection, else none. */
export interface LibraryUpNext {
  kind: 'episodes' | 'collection' | 'none';
  title?: string | null;
  title_id?: string | null;
  current_id?: string | null;
  items: TitleSummary[];
}

export interface TitleLetter {
  letter: string;
  index: number;
}

export interface TitlePage {
  items: TitleSummary[];
  next_cursor?: string | null;
  total?: number | null;
  letters?: TitleLetter[] | null;
  start_index?: number;
}

export interface GenreFacet {
  name: string;
  count: number;
}

export interface YearRange {
  min: number;
  max: number;
}

export interface ResolutionFacet {
  value: TitleResolution;
  count: number;
}

export interface TitleFacets {
  genres: GenreFacet[];
  years?: YearRange | null;
  resolutions: ResolutionFacet[];
}

export interface TitlePerson {
  id?: string | null;
  name: string;
  role?: string | null;
  type: PersonType;
  image_url?: string | null;
}

export interface TitleVersion {
  item_id: string;
  label?: string | null;
  container?: string | null;
  video_codec?: string | null;
  audio_codec?: string | null;
  width?: number | null;
  height?: number | null;
  hdr: boolean;
  file_size?: number | null;
  duration_seconds?: number | null;
  media_state?: string | null;
}

export interface TitleExtra {
  item_id: string;
  extra_type: ExtraType;
  name: string;
  duration_seconds?: number | null;
  artwork_url?: string | null;
}

export interface TitleMatch {
  method: MatchMethod;
  score?: number | null;
  at?: string | null;
}

/** One album track. `artist` only when it differs from the album artist. */
export interface AlbumTrack {
  item_id: string;
  disc?: number | null;
  number?: number | null;
  name: string;
  artist?: string | null;
  duration_seconds?: number | null;
  user_data: TitleUserData;
}

export interface TitleDetail extends TitleSummary {
  tagline?: string | null;
  studios: string[];
  premiered?: string | null;
  end_date?: string | null;
  status?: string | null;
  logo_url?: string | null;
  provider_ids: Record<string, string>;
  people: TitlePerson[];
  versions: TitleVersion[];
  extras: TitleExtra[];
  children: TitleSummary[];
  boxset?: TitleSummary | null;
  aired_episode_count?: number | null;
  play_next?: TitleSummary | null;
  match?: TitleMatch | null;
  has_recap: boolean;
  logo?: TitleArt | null;
  episode_count?: number | null;
  best_height?: number | null;
  tracks?: AlbumTrack[] | null;
}

/** GET /api/library/sections. */
export interface LibrarySections {
  movies: number;
  shows: number;
  anime: number;
  albums: number;
  artists: number;
  saved_audio: number;
  youtube: number;
  recordings: number;
  deleted: number;
}

export interface ItemProgress {
  position_seconds: number;
  duration_seconds?: number | null;
  completed: boolean;
}

export interface TitleWatchedRequest {
  watched: boolean;
}

export interface TitleRow {
  id: string;
  kind: TitleRowKind;
  title: string;
  anchor_title_id?: string | null;
  items: TitleSummary[];
}

export interface TitleRowsResponse {
  rows: TitleRow[];
}

export interface RecapCitation {
  episode_id: string;
  cue_ordinal: number;
  start_ms?: number | null;
}

export interface RecapPoint {
  text: string;
  citations: RecapCitation[];
}

export interface RecapFallbackEpisode {
  episode_id: string;
  name: string;
  season_number?: number | null;
  index_number?: number | null;
  overview?: string | null;
}

export interface RecapResponse {
  episode_title_id: string;
  state: RecapState;
  points: RecapPoint[];
  fallback: RecapFallbackEpisode[];
  suggest_preroll: boolean;
  error?: string | null;
}

export interface EpisodeSummaryText {
  episode_id: string;
  overview: string;
}

export interface EpisodeSummaries {
  available: boolean;
  items: EpisodeSummaryText[];
}

export interface KeyScene {
  start_ms: number;
  quote: string;
  caption: string;
}

export interface KeyScenes {
  available: boolean;
  title_id?: string | null;
  item_id?: string | null;
  scenes: KeyScene[];
}

export interface ArtServing {
  hits_memory: number;
  hits_disk: number;
  misses: number;
  generated: number;
  fallback_original: number;
  fallback_unavailable: number;
  failures: number;
}

export interface ArtworkProgress {
  total: number;
  prepared: number;
  failed: number;
  unsupported: number;
  cache_bytes: number;
  running: boolean;
  paused_for_playback: boolean;
  failure_reasons: Record<string, number>;
  serving: ArtServing;
  updated_at?: string | null;
}

export interface ClientMetricSample {
  metric: ClientMetricName;
  label: string;
  value: number;
}

export interface ClientMetricsBatch {
  csrf?: string | null;
  samples: ClientMetricSample[];
}

export interface MetricSummary {
  metric: ClientMetricName;
  label: string;
  today_count: number;
  today_p50?: number | null;
  today_p95?: number | null;
  week_count: number;
  week_p50?: number | null;
  week_p95?: number | null;
  budget_p50?: number | null;
  budget_p95?: number | null;
  within_budget?: boolean | null;
}

export interface MediaLoading {
  metrics: MetricSummary[];
  image_failure_rate_today?: number | null;
  image_failure_rate_week?: number | null;
}

export interface IdentifyCandidate {
  tmdb_id: number;
  name: string;
  original_name?: string | null;
  year?: number | null;
  overview?: string | null;
  poster_url?: string | null;
  score: number;
}

export interface IdentifyRequest {
  tmdb_id: number;
}

export interface MetadataRefreshResponse {
  title_id: string;
  metadata_due_at?: string | null;
}

export interface MetadataBulkRefreshResponse {
  queued: number;
}

export interface ConnectedApp {
  id: string;
  user_id: string;
  owner_display_name?: string | null;
  kind: ConnectedAppKind;
  scope: ConnectedAppScope;
  device_name: string;
  client?: string | null;
  client_version?: string | null;
  created_at: string;
  last_seen_at?: string | null;
}

export interface ConnectedAppCreateRequest {
  name: string;
  scope: ConnectedAppScope;
}

export interface AppPasswordCreateRequest {
  name: string;
}

export interface SignOutAllAppsResponse {
  revoked: number;
}

export interface AppPasswordCreated {
  app: ConnectedApp;
  password: string;
  username: string;
  server_address: string | null;
  /** The local address, for apps at home; shown beside server_address when they differ. */
  local_server_address?: string | null;
}

export interface TwoFactorChallenge {
  two_factor_required: true;
  challenge: string;
}

export interface TwoFactorStatus {
  enabled: boolean;
  recovery_codes_left: number;
  required: boolean;
}

export interface TwoFactorSetup {
  secret: string;
  otpauth_uri: string;
  qr_size: number;
  qr_path: string;
}

export interface ConnectedAppCreated {
  app: ConnectedApp;
  token: string;
}

export interface MediaServerSettings {
  jellyfin_enabled: boolean;
  jellyfin_url?: string | null;
  jellyfin_public_url?: string | null;
  has_tmdb_key: boolean;
  metadata_language: string;
  introdb_enabled: boolean;
  hwaccel: HwaccelMode;
  max_playback_sessions: number;
  transcode_cache_gb: number;
  jellyfin_import_url?: string | null;
  anime_folders?: string[];
  recategorising?: boolean;
  members_edit_metadata?: boolean;
}

export interface MediaServerSettingsUpdate {
  jellyfin_enabled?: boolean | null;
  tmdb_api_key?: string | null;
  metadata_language?: string | null;
  introdb_enabled?: boolean | null;
  hwaccel?: HwaccelMode | null;
  max_playback_sessions?: number | null;
  transcode_cache_gb?: number | null;
  jellyfin_import_url?: string | null;
  anime_folders?: string[] | null;
  members_edit_metadata?: boolean | null;
}

export interface ConnectionTestResponse {
  ok: boolean;
  error?: string | null;
}

export interface JellyfinImportStatus {
  server?: string | null;
}

export interface JellyfinImportRequest {
  username: string;
  password: string;
}

export interface JellyfinImportSummary {
  watched: number;
  in_progress: number;
  favorites: number;
  up_to_date: number;
  unmatched: number;
  unmatched_names: string[];
}

export interface JellyfinHouseholdRequest extends JellyfinImportRequest {
  jellyfin_ids: string[];
}

export interface JellyfinHouseholdMember {
  jellyfin_id: string;
  jellyfin_name: string;
  disabled: boolean;
  action: 'import' | 'create' | 'skip';
  lumina_username?: string | null;
  reason?: string | null;
  error?: string | null;
  summary?: JellyfinImportSummary | null;
  reset_url?: string | null;
  reset_expires_at?: string | null;
}

export interface JellyfinHousehold {
  members: JellyfinHouseholdMember[];
}

export interface TranscodeDiagnostics {
  hwaccel: HwaccelMode;
  active: HwaccelActive;
  probe_ok: boolean;
  probe_error?: string | null;
  tonemap: TonemapMethod;
  hardware_disabled: boolean;
  software_fallbacks: number;
  sessions: Record<string, number>;
  throttled: number;
  cache_bytes: number;
  cache_cap_bytes: number;
  speeds: Record<string, number>;
  recent_errors: string[];
}

export interface AudioTrack {
  index: number;
  language?: string | null;
  label: string;
  codec?: string | null;
  channels?: number | null;
  default: boolean;
}

export interface PlaybackSessionRequest {
  version_id?: string | null;
  audio_index?: number | null;
  subtitle?: string | null;
  max_height?: number | null;
}

export interface SubtitleTrack {
  id: string;
  label: string;
  language?: string | null;
  origin: SubtitleOrigin;
  format: SubtitleFormat;
  forced: boolean;
  default: boolean;
  hearing_impaired: boolean;
  url?: string | null;
}

export interface SubtitleTranslateRequest {
  target_language: string;
}

export interface EnrichmentJob {
  id: string;
  library_item_id: string;
  kind: EnrichmentJobKind;
  state: EnrichmentJobState;
  transcript_id?: string | null;
  error?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface EnrichmentBulkRequest {
  title_id?: string | null;
  root_id?: string | null;
  kinds: EnrichmentJobKind[];
}

export interface EnrichmentBulkResponse {
  queued: number;
  skipped: number;
}

export interface MediaSegmentInput {
  type: SegmentType;
  start_seconds: number;
  end_seconds: number;
}

export interface MediaSegment {
  type: SegmentType;
  start_seconds: number;
  end_seconds: number;
  source: SegmentSource;
  confidence: number;
}

export interface MediaSegmentsUpdate {
  segments: MediaSegmentInput[];
}

export interface MediaSegmentList {
  item_id: string;
  segments: MediaSegment[];
}

export interface MuteRange {
  start_seconds: number;
  end_seconds: number;
}

export interface SmartRuleCondition {
  field: SmartRuleField;
  op: SmartRuleOp;
  value: string | number | string[];
}

export interface SmartRuleSort {
  field: SmartRuleSortField;
  order: SortOrder;
}

export interface SmartCollectionRule {
  type: SmartRuleType;
  match: SmartRuleMatch;
  conditions: SmartRuleCondition[];
  sort?: SmartRuleSort | null;
  limit: number;
}

export interface SmartRuleDraftRequest {
  prompt: string;
}

export interface SmartRuleSample {
  id: string;
  name: string;
  poster_url?: string | null;
}

export interface SmartRulePreview {
  count: number;
  sample: SmartRuleSample[];
}

export interface SmartRuleDraft {
  rule: SmartCollectionRule;
  preview: SmartRulePreview;
  description: string;
}

export interface AutoSkipPref {
  intro: boolean;
  credits: boolean;
  recap: boolean;
}

export interface ProfanityPref {
  enabled: boolean;
  words: string[];
}

// ── 2.1.0: media_schemas.py mirrors every type below except PersonRef, MetadataErrorBody, WatchIntervalS ──
export type EditKind = 'edit' | 'lock' | 'revert' | 'bulk' | 'undo' | 'item_lock' | 'image';
export type EditableImageType = 'Primary' | 'Backdrop' | 'Logo';
export type ImageOrigin = 'tmdb' | 'upload' | 'local' | 'embedded';
export type RefreshOutcome = 'update' | 'same' | 'kept_edit' | 'kept_lock' | 'kept_higher_source' | 'new';
export type BulkOpName = 'add' | 'remove' | 'set' | 'lock' | 'unlock' | 'lock_item' | 'unlock_item';
export type BulkSkipReason = 'not_found' | 'locked_item' | 'wrong_type';
export type UndoSkipReason = 'changed_since' | 'not_visible' | 'owner_only';
export type VocabularyField = 'genres' | 'tags' | 'studios' | 'official_rating';

export interface KeptValue {
  source: FieldSource | null;
  value: unknown;
}

export interface FieldState {
  value: unknown;
  source: FieldSource | null;
  locked: boolean;
  kept: KeptValue | null;
}

export interface TitleParentRef {
  id: string;
  type: TitleType;
  name: string;
}

export interface TitleImageEntry {
  type: EditableImageType;
  index: number;
  url: string | null;
  tag: string | null;
  origin: ImageOrigin | null;
  source: FieldSource | null;
  locked: boolean;
  width: number | null;
  height: number | null;
}

export interface TitleMetadataDoc {
  title_id: string;
  type: TitleType;
  name: string;
  locked: boolean;
  parent: TitleParentRef | null;
  fields: Record<string, FieldState>;
  images: TitleImageEntry[];
  can_identify: boolean;
  tmdb_configured: boolean;
  history_count: number;
  /** An episode's: every season of its series, for moving it (#164). */
  seasons?: SeasonChoice[];
}

export interface SeasonChoice {
  id: string;
  index_number: number | null;
  name: string;
}

/** A TMDB episode group a series' display order can read through (#164). */
export interface EpisodeGroup {
  id: string;
  name: string;
  type: number | null;
  episode_count: number | null;
  group_count: number | null;
}

/** One person in the household people editor (#164). */
export interface PersonDoc {
  person_id: string;
  name: string;
  source_name: string;
  name_edited: boolean;
  photo_edited: boolean;
  image_url: string | null;
  title_count: number;
}

export interface PersonNameEdit {
  name?: string | null;
}

export interface PersonEditResult {
  batch_id: string | null;
  person: PersonDoc;
}

export interface FieldChange {
  value: unknown;
  base: unknown;
}

export interface EditEntry {
  title_id: string;
  changes: Record<string, FieldChange>;
  pin?: string[];
  locked?: boolean | null;
}

export interface EditRequest {
  edits: EditEntry[];
}

export interface EditConflict {
  title_id: string;
  fields: string[];
  current: Record<string, FieldState>;
}

export interface EditResult {
  batch_id: string | null;
  titles: TitleMetadataDoc[];
  conflicts: EditConflict[];
}

export interface RevertRequest {
  fields: string[];
}

export interface RevertResult {
  batch_id: string | null;
  title: TitleMetadataDoc;
}

export interface HistoryUser {
  id: string;
  display_name: string;
}

export interface HistoryChange {
  field: string;
  before: unknown;
  after: unknown;
  before_source: FieldSource | null;
  after_source: FieldSource | null;
}

export interface HistoryBatch {
  batch_id: string;
  kind: EditKind;
  user: HistoryUser | null;
  created_at: string;
  changes: HistoryChange[];
  undone: boolean;
  title_count: number;
}

export interface HistoryPage {
  batches: HistoryBatch[];
  next_cursor: string | null;
}

export interface UndoSkip {
  title_id: string;
  field: string;
  reason: UndoSkipReason;
}

export interface UndoResult {
  batch_id: string;
  restored: number;
  skipped: UndoSkip[];
}

export interface BulkOp {
  op: BulkOpName;
  field?: string | null;
  fields?: string[] | null;
  values?: string[] | null;
  value?: string | null;
}

export interface BulkRequest {
  title_ids: string[];
  ops: BulkOp[];
}

export interface BulkSkip {
  title_id: string;
  reason: BulkSkipReason;
}

export interface BulkResult {
  batch_id: string | null;
  applied: number;
  skipped: BulkSkip[];
}

export interface VocabularyEntry {
  value: string;
  count: number;
}

export interface PersonSuggestion {
  person_id: string;
  name: string;
  image_url: string | null;
}

export interface RefreshPreviewField {
  field: string;
  current: unknown;
  incoming: unknown;
  outcome: RefreshOutcome;
}

export interface RefreshPreviewImage {
  type: EditableImageType;
  current_url: string | null;
  incoming_preview_url: string | null;
  outcome: RefreshOutcome;
}

export interface RefreshPreview {
  tmdb_id: number | null;
  match: Record<string, unknown>;
  fields: RefreshPreviewField[];
  images: RefreshPreviewImage[];
}

export interface EpisodeTableSeason {
  id: string;
  name: string;
  index_number: number | null;
  episode_count: number;
}

export interface EpisodeTableRow {
  title_id: string;
  index_number: number | null;
  index_number_end: number | null;
  name: string;
  premiered: string | null;
  runtime_minutes: number | null;
  overview: string | null;
  community_rating: number | null;
  locked_fields: string[];
  locked: boolean;
  still_url: string | null;
}

export interface EpisodeTable {
  season: EpisodeTableSeason;
  seasons: EpisodeTableSeason[];
  episodes: EpisodeTableRow[];
}

export interface ImageCandidate {
  tmdb_path: string;
  width: number;
  height: number;
  language: string | null;
  vote: number;
  preview_url: string;
}

export interface ImagePutTmdb {
  tmdb_path: string;
  base_tag?: string | null;
}

export interface BackdropOrder {
  tags: string[];
}

/** A credit in the People tab's value (the `people` field). TS-only: the server validates it in the catalogue. */
export interface PersonRef {
  person_id: string | null;
  name: string;
  role: string;
  type: PersonType;
}

/** The JSON body of an editor error (ApiRequestError.body): 422 invalid_field carries title_id/field/reason; 409 no_match carries match. */
export interface MetadataErrorBody {
  detail: string;
  title_id?: string;
  field?: string;
  reason?: string;
  match?: Record<string, unknown>;
}

export type ScanSchedule = 'off' | '15m' | '1h' | '6h' | 'nightly';
export type ScanTrigger = 'manual' | 'scheduled' | 'watch';
export type AutomationState = 'idle' | 'scanning' | 'preparing' | 'waiting_files' | 'waiting_confirmation' | 'offline' | 'unresponsive' | 'too_large' | 'needs_first_import';
export type AutomationSkipReason = 'scanning' | 'offline' | 'unresponsive' | 'waiting_confirmation' | 'needs_first_import';
export type ImportRunErrorCode = 'offline' | 'permission_denied' | 'identity_mismatch' | 'root_removed' | 'root_empty';
export type WatchIntervalS = 60 | 300 | 900;

export interface AutomationPoller {
  heartbeat_at: string | null;
  stalled: boolean;
}

export interface AutomationStateDetail {
  pending_files: number;
  dirs_listed: number;
  dirs_total: number | null;
  since: string | null;
}

export interface AutomationActiveRun {
  id: string;
  trigger: ScanTrigger;
  scope_dirs: number | null;
  inspected: number;
}

export interface AutomationRunCounters {
  indexed: number;
  updated: number;
  relinked: number;
  missing: number;
}

export interface AutomationLastRun {
  id: string;
  trigger: ScanTrigger;
  state: string;
  finished_at: string | null;
  scope_dirs: number | null;
  counters: AutomationRunCounters;
  error: string | null;
}

export interface AutomationSkip {
  at: string;
  reason: AutomationSkipReason;
}

export interface AutomationRoot {
  root_id: string;
  label: string;
  schedule: ScanSchedule;
  watch: boolean;
  watch_interval_s: WatchIntervalS;
  state: AutomationState;
  state_detail: AutomationStateDetail;
  active_run: AutomationActiveRun | null;
  last_run: AutomationLastRun | null;
  last_full_run_at: string | null;
  next_scan_at: string | null;
  last_skip: AutomationSkip | null;
}

export interface LibraryAutomation {
  server_timezone: string;
  night_hour: number;
  poller: AutomationPoller;
  roots: AutomationRoot[];
}

export interface AutomationRootPatch {
  schedule?: ScanSchedule | null;
  watch?: boolean | null;
  watch_interval_s?: WatchIntervalS | null;
}

export interface NightHourPatch {
  night_hour: number;
}

export interface ScanRequest {
  root_id?: string | null;
}

export interface ScanStarted {
  root_id: string;
  run_id: string;
}

export interface ScanSkipped {
  root_id: string;
  reason: AutomationSkipReason;
}

export interface ScanLibrariesResult {
  started: ScanStarted[];
  queued: string[];
  skipped: ScanSkipped[];
}

export interface AutomationDiagnosticsPoller {
  heartbeat_at: string | null;
  stalled: boolean;
  last_error: string | null;
}

export interface AutomationDriver {
  active_run_id: string | null;
  queued: number;
}

export interface AutomationRuns24h {
  manual: number;
  scheduled: number;
  watch: number;
  needs_confirmation: number;
  failed: number;
}

export interface AutomationRootDiagnostics {
  label: string;
  schedule: ScanSchedule;
  watch: boolean;
  state: AutomationState;
  dirs_watched: number;
  pending_files: number;
  last_pass_at: string | null;
  last_pass_seconds: number | null;
  stats_last_pass: number;
  watch_errors_last_pass: number;
  last_change_at: string | null;
  last_dispatch_at: string | null;
  last_skip: AutomationSkip | null;
  next_scan_at: string | null;
}

export interface LibraryAutomationDiagnostics {
  poller: AutomationDiagnosticsPoller;
  driver: AutomationDriver;
  runs_24h: AutomationRuns24h;
  roots: AutomationRootDiagnostics[];
}

/** Admin Activity dashboard. */
export interface ActivityUser { id: string; name: string }
export type ActivityMethod = 'direct' | 'remux' | 'transcode' | 'relay';
export type ActivityHardware = 'qsv' | 'vaapi' | 'software';
export interface ActivityClient { kind: 'web' | 'app'; name: string; device: string | null }
export interface ActivityVideo { from: string | null; to: string | null; height: number | null; tonemap: boolean }
export interface ActivitySession {
  id: string;
  source: 'library' | 'remote';
  user: ActivityUser;
  title: string;
  subtitle: string | null;
  item_id: string | null;
  artwork_url: string | null;
  client: ActivityClient;
  method: ActivityMethod;
  video: ActivityVideo | null;
  audio: { from: string | null; to: string | null } | null;
  hardware: ActivityHardware | null;
  speed: number | null;
  throttled: boolean;
  position_seconds: number | null;
  duration_seconds: number | null;
  started_at: string;
  last_seen_at: string;
  stoppable: boolean;
}
export interface ActivityDownload { id: string; title: string | null; user: ActivityUser; progress: number | null; speed_bytes: number | null; started_at: string | null }
export interface ActivityRecording { id: string; title: string | null; user: ActivityUser; status: 'queued' | 'live' | 'stopping' | 'finalizing'; started_at: string | null }
export interface ActivityServer {
  cpu_percent: number | null;
  memory: { used_bytes: number; total_bytes: number } | null;
  load_average: [number, number, number] | null;
  uptime_seconds: number | null;
  transcoder_cpu_percent: number | null;
  ffmpeg_processes: number;
  hardware: { mode: 'auto' | 'off' | 'qsv' | 'vaapi'; active: 'qsv' | 'vaapi' | null; disabled: boolean; failures: number; fallbacks: number };
}
export interface AdminActivity { generated_at: string; sessions: ActivitySession[]; downloads: ActivityDownload[]; recordings: ActivityRecording[]; server: ActivityServer }
export interface ActivityHistoryRow {
  id: string;
  source: 'library' | 'remote';
  user: ActivityUser;
  title: string;
  subtitle: string | null;
  item_id: string | null;
  client: ActivityClient;
  method: ActivityMethod;
  hardware: ActivityHardware | null;
  video: ActivityVideo | null;
  started_at: string;
  ended_at: string;
  watched_seconds: number;
  stopped_by_admin: boolean;
}
export interface ActivityHistoryPage { items: ActivityHistoryRow[]; next_before: string | null }
