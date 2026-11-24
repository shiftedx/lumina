import type * as Types from '../types';


export interface MobileConnectStatus {
  enabled: boolean;
  email_configured: boolean;
}

export interface MobileConnectCode {
  id: string;
  server_url: string;
  expires_at: string;
  qr_payload: string;
  qr_size: number;
  qr_path: string;
  email_sent: boolean | null;
}

export interface MobileConnectCodeStatus {
  status: 'pending' | 'used' | 'expired' | 'revoked';
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
  app: Types.ConnectedApp;
  token: string;
}

export interface MediaServerSettings {
  jellyfin_enabled: boolean;
  native_streaming_enabled: boolean;
  jellyfin_url?: string | null;
  jellyfin_public_url?: string | null;
  has_tmdb_key: boolean;
  has_omdb_key?: boolean;
  metadata_language: string;
  introdb_enabled: boolean;
  hwaccel: Types.HwaccelMode;
  max_playback_sessions: number;
  transcode_cache_gb: number;
  jellyfin_import_url?: string | null;
  plex_import_url: string | null;
  anime_folders?: string[];
  recategorising?: boolean;
  members_edit_metadata?: boolean;
  autoplay_presence?: AutoplayPresencePolicy;
}

/** Household autoplay-presence configuration returned by media-server settings. */
export interface AutoplayPresencePolicy {
  enabled: boolean;
  episode_limit: number;
  timeout_seconds: number;
}
/** Read-only member playback policy. This is deliberately the same server contract. */
export type PlaybackPresencePolicy = AutoplayPresencePolicy;

export interface MediaServerSettingsUpdate {
  jellyfin_enabled?: boolean | null;
  native_streaming_enabled?: boolean | null;
  tmdb_api_key?: string | null;
  omdb_api_key?: string | null;
  metadata_language?: string | null;
  introdb_enabled?: boolean | null;
  hwaccel?: Types.HwaccelMode | null;
  max_playback_sessions?: number | null;
  transcode_cache_gb?: number | null;
  jellyfin_import_url?: string | null;
  plex_import_url?: string | null;
  anime_folders?: string[] | null;
  members_edit_metadata?: boolean | null;
  autoplay_presence?: AutoplayPresencePolicy | null;
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

export interface PlexImportRequest {
  token: string;
}

export interface PlexHouseholdRequest extends PlexImportRequest {
  plex_ids: string[];
}

export interface PlexImportStatus {
  server: string | null;
}

export interface PlexHouseholdMember {
  plex_id: string;
  plex_name: string;
  disabled: boolean;
  action: 'import' | 'create' | 'skip';
  lumina_username?: string | null;
  reason?: string | null;
  error?: string | null;
  summary?: JellyfinImportSummary | null;
  reset_url?: string | null;
  reset_expires_at?: string | null;
}

export interface PlexHousehold {
  members: PlexHouseholdMember[];
}

export type HouseholdTransferProvider = 'plex' | 'jellyfin';

export interface HouseholdTransferRequest {
  username?: string;
  password?: string;
  token?: string;
  member_ids?: string[];
}

export interface HouseholdTransferMember {
  source_id: string;
  source_name: string;
  disabled: boolean;
  action: 'import' | 'create' | 'skip';
  lumina_username?: string | null;
  reason?: string | null;
  error?: string | null;
  summary?: JellyfinImportSummary | null;
  reset_url?: string | null;
  reset_expires_at?: string | null;
}

export interface HouseholdTransfer {
  members: HouseholdTransferMember[];
}

export type HouseholdTransferEvent =
  | { type: 'connecting' }
  | { type: 'progress'; source_id: string; source_name: string; completed: number; total: number }
  | { type: 'member'; member: HouseholdTransferMember; completed: number; total: number }
  | { type: 'complete'; completed: number; total: number }
  | { type: 'error'; message: string };

export interface TranscodeDiagnostics {
  hwaccel: Types.HwaccelMode;
  active: Types.HwaccelActive;
  probe_ok: boolean;
  probe_error?: string | null;
  tonemap: Types.TonemapMethod;
  encode_codecs?: Array<'h264' | 'hevc' | 'av1'>;
  encode_errors?: Record<string, string>;
  hdr_encode_ranges?: Record<string, Array<'HDR10' | 'HLG'>>;
  hdr_encode_errors?: Record<string, string>;
  hdr_decode_pairs?: string[];
  hdr_decode_errors?: Record<string, string>;
  zero_copy_pairs?: string[];
  zero_copy_errors?: Record<string, string>;
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
  origin: Types.SubtitleOrigin;
  format: Types.SubtitleFormat;
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
  kind: Types.EnrichmentJobKind;
  state: Types.EnrichmentJobState;
  transcript_id?: string | null;
  error?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface EnrichmentBulkRequest {
  title_id?: string | null;
  root_id?: string | null;
  kinds: Types.EnrichmentJobKind[];
}

export interface EnrichmentBulkResponse {
  queued: number;
  skipped: number;
}

export interface MediaSegmentInput {
  type: Types.SegmentType;
  start_seconds: number;
  end_seconds: number;
}

export interface MediaSegment {
  type: Types.SegmentType;
  start_seconds: number;
  end_seconds: number;
  source: Types.SegmentSource;
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
  field: Types.SmartRuleField;
  op: Types.SmartRuleOp;
  value: string | number | string[];
}

export interface SmartRuleSort {
  field: Types.SmartRuleSortField;
  order: Types.SortOrder;
}

export interface SmartCollectionRule {
  type: Types.SmartRuleType;
  match: Types.SmartRuleMatch;
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
export type ImageOrigin = 'tmdb' | 'musicbrainz' | 'upload' | 'local' | 'embedded' | 'generated';
export type RefreshOutcome = 'update' | 'same' | 'kept_edit' | 'kept_lock' | 'kept_higher_source' | 'new';
export type BulkOpName = 'add' | 'remove' | 'set' | 'lock' | 'unlock' | 'lock_item' | 'unlock_item';
export type BulkSkipReason = 'not_found' | 'locked_item' | 'wrong_type';
export type UndoSkipReason = 'changed_since' | 'not_visible' | 'owner_only';
export type VocabularyField = 'genres' | 'tags' | 'studios' | 'official_rating';

export interface KeptValue {
  source: Types.FieldSource | null;
  value: unknown;
}

export interface FieldState {
  value: unknown;
  source: Types.FieldSource | null;
  locked: boolean;
  kept: KeptValue | null;
}

export interface TitleParentRef {
  id: string;
  type: Types.TitleType;
  name: string;
}

export interface TitleImageEntry {
  type: EditableImageType;
  index: number;
  url: string | null;
  tag: string | null;
  origin: ImageOrigin | null;
  source: Types.FieldSource | null;
  locked: boolean;
  width: number | null;
  height: number | null;
}

export interface TitleMetadataDoc {
  title_id: string;
  type: Types.TitleType;
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
  before_source: Types.FieldSource | null;
  after_source: Types.FieldSource | null;
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
  type: Types.PersonType;
}

/** The JSON body of an editor error (ApiRequestError.body): 422 invalid_field carries title_id/field/reason; 409 no_match carries match. */
export interface MetadataErrorBody {
  detail: string;
  title_id?: string;
  field?: string;
  reason?: string;
  match?: Record<string, unknown>;
}
