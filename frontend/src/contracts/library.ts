import type * as Types from '../types';


export interface DownloadJob {
  id: string;
  user_id?: string | null;
  source_url: string;
  status: Types.JobStatus;
  queue_position?: number | null;
  title?: string | null;
  artwork_url?: string | null;
  extractor?: string | null;
  format_selection?: Record<string, unknown> | null;
  /** Set when the saved file differs from what the preset asked for (e.g. no H.264 for Editable). */
  format_resolution?: { fallback_reason?: string | null } | null;
  output_profile?: Record<string, unknown> | null;
  output_container?: Types.OutputContainer | string | null;
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
  extra_type?: Types.ExtraType | null;
  /** The member's progress; /api/library list pages only. */
  progress?: Types.ItemProgress | null;
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
  items: Types.LiveRecording[];
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
  rules?: Types.SmartCollectionRule | null;
  titles?: Types.TitleSummary[];
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
  source_type?: Types.SourceAutomationType | null;
  title_id?: string | null;
  start_ms?: number | null;
  media_title?: Types.TitleSummary | null;
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
  title?: Types.TitleSummary | null;
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
  visibility: Types.NoteVisibility;
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
  preset: Types.FormatPreset;
  output_container: Types.OutputContainer;
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
  media_kind: Types.AutomationMediaKind;
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
  duplicate_policy: Types.AutomationDuplicatePolicy;
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
  source_type: Types.SourceAutomationType;
  artwork_url?: string | null;
  cron_expression: string;
  active: boolean;
  auto_download: boolean;
  format_selection: Record<string, unknown>;
  output_profile: Record<string, unknown>;
  rules: AutomationRuleSet;
  duplicate_policy: Types.AutomationDuplicatePolicy;
  max_items_per_run?: number | null;
  max_items_per_day?: number | null;
  backfill_limit?: number | null;
  last_checked_at?: string | null;
  next_check_at?: string | null;
  last_error?: string | null;
  last_run_summary: Record<string, unknown>;
  /** Last-known newest entries from the latest successful check (kept through failures). */
  feed_entries?: Types.PreviewEntry[];
  created_at: string;
  updated_at: string;
}

export interface SourceAutomationCreateRequest {
  label: string;
  source_url: string;
  source_type: Types.SourceAutomationType;
  cron_expression: string;
  active: boolean;
  auto_download: boolean;
  format_selection?: FormatSelection | null;
  output_profile?: OutputProfile | null;
  rules: AutomationRuleSet;
  duplicate_policy: Types.AutomationDuplicatePolicy;
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
  role: Types.UserRole;
  is_active: boolean;
  onboarding_status?: Types.OnboardingStatus;
  /** Only the first vault owner connects media after account creation. */
  vault_setup_pending?: boolean;
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
  role: Types.UserRole;
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

export interface AdminUpdateStatus {
  current_version: string;
  latest_release: {
    version: string;
    url: string;
    published_at: string | null;
  } | null;
  status: 'not_checked' | 'up_to_date' | 'update_available';
  checked_at: string | null;
  error: string | null;
  checking: boolean;
  automatic_checks: boolean;
  email_notifications: boolean;
  email_configured: boolean;
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
  missing_media?: {
    retired_versions: number;
    unreplaced_items: number;
  };
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
  updates?: AdminUpdateStatus;
  event_streams: number;
  persistence: Record<string, { count: number; errors: number; busy_timeouts: number; rollbacks?: number; total_ms: number; max_wait_ms: number }>;
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
  audio_tracks?: Types.AudioTrack[];
  quality_heights?: number[];
  loudness_gain_db?: number | null;
  free_video_slots?: number;
  video_codec?: string | null;
  video_range?: string | null;
};

export type LocalPlaybackSession = { session_id: string; mode: 'remux' | 'transcode'; playback_url: string; start: number; kind?: 'remux' | 'audio' | 'video_sw' | 'video_hw'; video_codec?: string | null; video_range?: string | null };
