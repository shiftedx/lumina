import type * as Types from '../types';


// ---------------------------------------------------------------------------
// Media vault (frozen 2026-09-25). Mirrors backend/app/media_schemas.py
// field-for-field; backend/tests/test_media_contract.py fails on drift. Change
// both sides together, and only by adding optional fields.
// ---------------------------------------------------------------------------

export type TitleType = 'series' | 'season' | 'episode' | 'movie' | 'boxset' | 'album' | 'artist';
export type ExtraType = 'trailer' | 'featurette' | 'behindthescenes' | 'deletedscene' | 'interview' | 'scene' | 'short' | 'clip' | 'other';
export type FieldSource = 'user' | 'nfo' | 'tmdb' | 'omdb' | 'musicbrainz' | 'path';
export type ImageType = 'Primary' | 'Backdrop' | 'Logo' | 'Thumb' | 'Banner';
export type MatchMethod = 'id' | 'find' | 'search' | 'llm' | 'user';
export type TitleSort = 'name' | 'created' | 'year' | 'rating';
export type PersonType = 'Actor' | 'Director' | 'Writer' | 'Creator' | 'GuestStar' | 'Producer' | 'Composer';
export type ConnectedAppKind = 'jellyfin' | 'agent' | 'app_password';
export type ConnectedAppScope = 'read' | 'write';
export type HwaccelMode = 'auto' | 'off' | 'qsv' | 'vaapi' | 'nvenc' | 'videotoolbox' | 'amf' | 'rkmpp';
export type HwaccelActive = 'qsv' | 'vaapi' | 'nvenc' | 'videotoolbox' | 'amf' | 'rkmpp' | 'none';
export type TonemapMethod = 'opencl' | 'vpp_qsv' | 'cuda' | 'software' | 'none';
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
export type SearchScope = 'movies' | 'shows' | 'anime' | 'music';
export type TitleCategory = 'movies' | 'shows' | 'anime';
export type ClientMetricName = 'wall_first_screen_ms' | 'wall_sharp_ms' | 'detail_hero_ms' | 'home_first_screen_ms' | 'home_hero_ms' | 'image_load_ms' | 'image_failed' | 'ttff_ms' | 'long_tasks';

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

export interface FileAudioQuality {
  codec?: string | null;
  bitrate_kbps?: number | null;
  bit_depth?: number | null;
  sample_rate?: number | null;
}

export interface AlbumAudioQuality {
  track_count: number;
  codecs: string[];
  bit_depths: number[];
  sample_rates: number[];
  bitrate_kbps?: number | null;
  formats_complete: boolean;
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
  imdb_rating?: number | null;
  rotten_tomatoes_rating?: number | null;
  metacritic_rating?: number | null;
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
  audio_quality?: AlbumAudioQuality | null;
  /** Set only by /api/home/title-rows and /api/titles/{id}/similar. */
  reco?: Types.RecoAnnotation | null;
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
  audio_quality?: FileAudioQuality | null;
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
  music?: import('../features/music/musicTypes').MusicTitleInfo | null;
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
  pending: number;
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
  pending_episode_stills?: number;
  missing_episode_stills?: number;
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
