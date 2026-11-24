import type * as Types from '../types';


export type ScanSchedule = 'off' | '15m' | '1h' | '6h' | 'nightly';
export type ScanTrigger = 'manual' | 'scheduled' | 'watch' | 'manager';
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

export interface LibraryActivity {
  scanning_roots: number;
  discovering_roots: number;
  indexing_roots: number;
  preparing_roots: number;
  stalled_roots: number;
  waiting_files: number;
  waiting_error_files: number;
  discovered_files: number;
  completed_files: number;
  total_files: number | null;
  progress: number | null;
  inspected_files: number;
  directories_listed: number;
  directories_total: number | null;
  artwork_pending?: number;
  artwork_failed?: number;
  artwork_missing?: number;
  artwork_paused?: boolean;
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
export type ActivityHardware = Exclude<Types.HwaccelMode, 'auto' | 'off'> | 'software';
export interface ActivityClient { kind: 'web' | 'app'; name: string; device: string | null }
export interface ActivityVideo { from: string | null; to: string | null; height: number | null; tonemap: boolean; range?: string; decode?: 'hardware' | 'software' }
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
  hardware: { mode: Types.HwaccelMode; active: Exclude<ActivityHardware, 'software'> | null; disabled: boolean; failures: number; fallbacks: number };
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

/** Household resource budgets use integer bits/second; zero member values mean unlimited. */
export interface ResourceAllowances {
  remote_stream_bps: number;
  remote_member_bps: number;
  playback_sessions: number;
  video_encodes: number;
  download_running: number;
  download_queued: number;
  ai_running: number;
  ai_queued: number;
}
export interface HouseholdResources {
  upload_bps: number | null;
  lan_networks: string[];
  defaults: ResourceAllowances;
}
export type ResourceOverrides = Partial<ResourceAllowances>;
export interface MemberResources { overrides: ResourceOverrides; effective: ResourceAllowances }
export interface ResourceJob { id: string; kind: string; state: string }

export interface ServerNavigation {
  categories: Array<'movies' | 'tv' | 'anime' | 'music' | 'books' | 'youtube' | 'recordings' | 'photos'>;
  features: { streaming: boolean; requests: boolean };
}
