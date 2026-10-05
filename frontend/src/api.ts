import type {
  ChannelPageResponse,
  ChannelResolveResponse,
  ChannelTab,
  LibraryChannelResponse,
  LiveRecording,
  LiveRecordingPage,
  LiveRecordingStartIntent,
  LiveRecordingFallbackPolicy,
  AcquisitionErrorCategory,
  AcquisitionErrorDetail,
  AcquisitionBatch,
  AcquisitionBatchCreateRequest,
  AdminOverview,
  AdminSettings,
  ChatReplayAsset,
  TimedChatEvent,
  AutomationRun,
  AppEvent,
  BootstrapStatus,
  DownloadJob,
  HealthState,
  HouseholdCollection,
  CollectionVisibility,
  JobPage,
  LibraryItem,
  LibraryPage,
  LibraryViewKind,
  LocalPlaybackOptions,
  LocalPlaybackSession,
  LocalSearchResponse,
  PlaybackProgress,
  PlaybackSessionRequest,
  RemotePlayback,
  RemotePlaybackProgress,
  PreviewRequest,
  PreviewResponse,
  PopularSnapshot,
  SearchHistoryEntry,
  LiveSnapshot,
  WallPage,
  MemberInterests,
  MemberOnboardingState,
  ChannelSuggestions,
  ChannelCandidateList,
  FollowChannelRequest,
  MemberRecommendationSnapshot,
  SuppressRecommendationInput,
  TranscodeDiagnostics,
  Suppression,
  SuppressionList,
  SessionState,
  DeviceMember,
  SourceAutomation,
  SourceAutomationCreateRequest,
  SourceAutomationAutoDownloadRequest,
  UpNextRequest,
  YouTubeSearchRequest,
  YouTubeSearchResponse,
  LibraryNote,
  NoteVisibility,
  UserProfile,
  UserSettings,
  UserSettingsUpdateRequest,
  UserTag,
  WatchQueue,
  WatchQueueRef,
  AppPasswordCreated,
  ConnectedApp,
  ConnectedAppCreateRequest,
  ConnectedAppCreated,
  TwoFactorChallenge,
  TwoFactorSetup,
  TwoFactorStatus,
  ConnectionTestResponse,
  EnrichmentJob,
  IdentifyCandidate,
  IdentifyRequest,
  JellyfinHousehold,
  JellyfinImportStatus,
  JellyfinImportSummary,
  MediaSegmentList,
  MediaServerSettings,
  MediaServerSettingsUpdate,
  MetadataBulkRefreshResponse,
  MetadataRefreshResponse,
  MuteRange,
  RecapResponse,
  SmartCollectionRule,
  SmartRuleDraft,
  SmartRuleDraftRequest,
  SmartRulePreview,
  SubtitleTrack,
  SubtitleTranslateRequest,
  TitleDetail,
  TitlePage,
  TitleRowsResponse,
  TitleSort,
  TitleSummary,
  TitleType,
  TitleUserData,
  TitleWatchedRequest,
} from './types';
import { accessStopCode, reportAccessStop } from './features/access/accessEvents';
import { backgroundReady } from './backgroundGate';
import type { AutomationRoot, AutomationRootPatch, BackdropOrder, BulkRequest, BulkResult, EditableImageType, EditEntry, EditRequest, EditResult, EpisodeGroup, EpisodeTable, HistoryPage, ImageCandidate, ImagePutTmdb, LibraryAutomation, LibraryAutomationDiagnostics, PersonDoc, PersonEditResult, PersonNameEdit, PersonSuggestion, RefreshPreview, RevertRequest, RevertResult, ScanLibrariesResult, TitleImageEntry, TitleMetadataDoc, UndoResult, VocabularyEntry, VocabularyField } from './types';
import type { ActivityHistoryPage, AdminActivity, ArtworkProgress, ClientMetricSample, ClientMetricsBatch, EpisodeSummaries, KeyScenes, LibrarySections, LibraryUpNext, MediaLoading, RecoDiagnostics, RecoEventBatch, RecoEventIn, SearchScope, TitleCategory, TitleFacets, TitleResolution } from './types';

const trimTrailingSlash = (value: string) => value.replace(/\/+$/, '');

export const resolveApiBaseUrl = (configuredValue?: string) => trimTrailingSlash(configuredValue?.trim() || '');

export const apiBaseUrl = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

export class ApiRequestError extends Error {
  status: number;
  retryAfterSeconds: number | null;
  category: AcquisitionErrorCategory | null;
  body: unknown;

  constructor(
    message: string,
    status: number,
    retryAfterSeconds: number | null = null,
    category: AcquisitionErrorCategory | null = null,
    body: unknown = null,
  ) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
    this.body = body;
    this.category = category;
  }
}

const BACKGROUND = { background: true } as const;
type RequestJsonOptions = {
  timeoutMs?: number;
  signal?: AbortSignal;
  /** A side request of the watch page: waits for the Play's first frame (backgroundGate.ts). */
  background?: boolean;
};

// Session-bound CSRF token: learned from login / session reads, sent on
// every mutation. Cookie sessions without it are rejected server-side.
let csrfToken: string | null = null;
let sessionUserId: string | null = null;
/** The signed-in user id, for per-user client caches (null before sign-in). */
export const getSessionUserId = () => sessionUserId;
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export function csrfHeaders(): Record<string, string> {
  return csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
}

function rememberCsrf(session: SessionState): SessionState {
  csrfToken = (session as SessionState & { csrf_token?: string | null }).csrf_token ?? null;
  sessionUserId = session.user?.id ?? null;
  return session;
}

export async function requestJson<T>(path: string, init?: RequestInit, options?: RequestJsonOptions, csrfRefreshed = false): Promise<T> {
  if (options?.background) await backgroundReady();
  const isMutation = !SAFE_METHODS.has((init?.method || 'GET').toUpperCase());
  const isFormData = typeof FormData !== 'undefined' && init?.body instanceof FormData;
  const controller = new AbortController();
  const cancelFromCaller = () => controller.abort();
  if (options?.signal?.aborted) controller.abort();
  else options?.signal?.addEventListener('abort', cancelFromCaller, { once: true });
  const timeoutMs = options?.timeoutMs ?? 20000;
  const timeoutHandle = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;

  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      credentials: 'include',
      signal: controller.signal,
      headers: {
        ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
        ...(isMutation ? csrfHeaders() : {}),
        ...(init?.headers || {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (options?.signal?.aborted) throw new DOMException('Request cancelled.', 'AbortError');
      throw new ApiRequestError('Request timed out. Please try again.', 0);
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeoutHandle);
    options?.signal?.removeEventListener('abort', cancelFromCaller);
  }

  if (!response.ok) {
    let message = `Request failed with ${response.status}`;
    let category: AcquisitionErrorCategory | null = null;
    let errorBody: unknown = null;
    const retryAfterHeader = response.headers.get('Retry-After');
    const retryAfterSeconds = retryAfterHeader ? Number.parseInt(retryAfterHeader, 10) || null : null;
    try {
      const body = (await response.json()) as { detail?: string | AcquisitionErrorDetail; message?: string };
      errorBody = body;
      if (Array.isArray(body?.detail)) {
        // FastAPI validation errors: surface the field messages instead of a bare status code.
        message = (body.detail as { msg?: string }[]).map((item) => (item.msg || '').replace(/^Value error, /, '')).filter(Boolean).join(' ') || message;
      } else if (body?.detail && typeof body.detail === 'object') {
        message = body.detail.message || message;
        category = body.detail.category || null;
      } else if (typeof body?.detail === 'string') {
        message = body.detail;
      } else if (body?.message) {
        message = body.message;
      }
    } catch {
      // Ignore JSON parsing failures and use the default message.
    }
    if (response.status === 403 && isMutation && !csrfRefreshed && message.includes('CSRF')) {
      // The server rejected before running the handler, so one retry is safe.
      // An expired session surfaces as getSession's 401 (sign-in), never a loop.
      await getSession();
      return requestJson<T>(path, init, options, true);
    }
    if (response.status === 429 && retryAfterSeconds) {
      message = `${message} Try again in about ${retryAfterSeconds} second${retryAfterSeconds === 1 ? '' : 's'}.`;
    }
    const stop = accessStopCode(response.status, message);
    if (stop) reportAccessStop(stop);
    throw new ApiRequestError(message, response.status, retryAfterSeconds, category, errorBody);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

const enc = encodeURIComponent;
const json = (payload: unknown) => (payload === undefined ? undefined : JSON.stringify(payload));
export const post = <T>(path: string, payload?: unknown, options?: RequestJsonOptions) => requestJson<T>(path, { method: 'POST', body: json(payload) }, options);
export const put = <T>(path: string, payload: unknown) => requestJson<T>(path, { method: 'PUT', body: json(payload) });
const patch = <T>(path: string, payload: unknown) => requestJson<T>(path, { method: 'PATCH', body: json(payload) });
export const del = <T = void>(path: string) => requestJson<T>(path, { method: 'DELETE' });

/** `?a=1&b=2` from the set values (null/undefined skipped, arrays repeat the key); '' when empty. */
function qs(params: Record<string, string | number | boolean | null | undefined | readonly string[]>): string {
  const pairs = Object.entries(params).flatMap(([key, value]) => (value === undefined || value === null ? [] : Array.isArray(value) ? value : [value]).map((entry) => `${enc(key)}=${enc(String(entry))}`));
  return pairs.length ? `?${pairs.join('&')}` : '';
}

export const getHealth = (options?: RequestJsonOptions): Promise<HealthState> => requestJson('/api/health', undefined, options);
export const getRuntimeHealth = (options?: RequestJsonOptions): Promise<HealthState> => requestJson('/api/runtime-health', undefined, options);
export const getBootstrapStatus = (options?: RequestJsonOptions): Promise<BootstrapStatus> => requestJson('/api/bootstrap/status', undefined, options);
export const createInitialAdmin = (payload: { username: string; password: string; display_name?: string | null }): Promise<UserProfile> => post('/api/bootstrap/admin', payload);
/** A password sign-in answers a session, or a two-step challenge (no cookie yet) for an account with two-step verification. */
export const loginSession = (payload: { username: string; password: string; remember_on_device?: boolean }): Promise<SessionState | TwoFactorChallenge> =>
  post<SessionState | TwoFactorChallenge>('/api/session/login', payload).then((result) => ('two_factor_required' in result ? result : rememberCsrf(result)));
export const verifyTwoFactor = (payload: { challenge: string; code?: string; recovery_code?: string; trust_device?: boolean }): Promise<SessionState> => post<SessionState>('/api/session/two-factor', payload).then(rememberCsrf);
export const logoutSession = (): Promise<void> => post<void>('/api/session/logout').finally(() => { csrfToken = null; sessionUserId = null; });
export function getSession(options?: RequestJsonOptions): Promise<SessionState> {
  return requestJson<SessionState>('/api/session/me', undefined, options).then(rememberCsrf);
}
/** "Who's watching?": the members remembered on this browser; [] without a ring. */
/** GET /api/public/showcase: the sign-in page's public release slides (no session; empty when Requests is off). */
export interface ShowcaseSlide { backdrop_url: string; logo_url?: string; title: string; caption: string; kind: 'movie' | 'show' | 'anime' }
export const getShowcase = (): Promise<{ slides: ShowcaseSlide[] }> => requestJson('/api/public/showcase', undefined, { timeoutMs: 8000 });
export const getDeviceMembers = (options?: RequestJsonOptions): Promise<DeviceMember[]> => requestJson<DeviceMember[]>('/api/session/device-members', undefined, options);
export const switchMember = (userId: string): Promise<SessionState> => post<SessionState>('/api/session/switch', { user_id: userId }).then(rememberCsrf);
export const forgetDeviceMember = (userId: string): Promise<void> => post<void>('/api/session/forget', { user_id: userId });
export const updateDisplayName = (displayName: string): Promise<SessionState> => put('/api/session/me', { display_name: displayName });

/** GET /api/me/export: a bounded JSON bundle of the signed-in member's own portable data. */
/** GET /api/me/access (2.8.0): what this member may see and when. */
export const getMyAccess = (): Promise<import('./features/access/access').MyAccess> => requestJson('/api/me/access');
export const exportMyData = (): Promise<Record<string, unknown>> => requestJson('/api/me/export');
export const getJellyfinImport = (): Promise<JellyfinImportStatus> => requestJson('/api/me/jellyfin-import');
// A large Jellyfin library takes a while to read, and the server finishes an import even if this request gives up.
const JELLYFIN_IMPORT_TIMEOUT = { timeoutMs: 180000 };
export const previewJellyfinImport = (username: string, password: string): Promise<JellyfinImportSummary> => post('/api/me/jellyfin-import/preview', { username, password }, JELLYFIN_IMPORT_TIMEOUT);
export const runJellyfinImport = (username: string, password: string): Promise<JellyfinImportSummary> => post('/api/me/jellyfin-import', { username, password }, JELLYFIN_IMPORT_TIMEOUT);
// Every chosen user's history is read, then written member by member: a few minutes for a large household.
const JELLYFIN_HOUSEHOLD_TIMEOUT = { timeoutMs: 600000 };
export const previewJellyfinHousehold = (username: string, password: string): Promise<JellyfinHousehold> => post('/api/admin/jellyfin-household/preview', { username, password }, JELLYFIN_HOUSEHOLD_TIMEOUT);
export const runJellyfinHousehold = (username: string, password: string, jellyfinIds: string[]): Promise<JellyfinHousehold> => post('/api/admin/jellyfin-household', { username, password, jellyfin_ids: jellyfinIds }, JELLYFIN_HOUSEHOLD_TIMEOUT);

/** Warm the preview a click on this source would run. */
export const prefetchRemote = (sourceUrl: string): Promise<{ accepted: boolean }> => post('/api/remote/prefetch', { source_url: sourceUrl, supported_profiles: browserSupportedPlaybackProfiles() });

export function previewUrl(payload: PreviewRequest, options?: RequestJsonOptions): Promise<PreviewResponse> {
  return post('/api/preview', { ...payload, supported_profiles: payload.supported_profiles ?? browserSupportedPlaybackProfiles() }, options);
}

const HEVC_FAILED_KEY = 'lumina.hevcFailed';
/** Set when HEVC playback failed here, so this browser asks for H.264 from then on (localPlayer.tsx). */
export function markHevcFailed(): void {
  try { localStorage.setItem(HEVC_FAILED_KEY, '1'); } catch { /* storage blocked: this play still falls back */ }
}
function hevcFailed(): boolean {
  try { return localStorage.getItem(HEVC_FAILED_KEY) === '1'; } catch { return false; }
}
// Async, so it is asked once and counts from the next request on; until then MediaSource alone decides.
let hevcDecodes: boolean | undefined;
function probeHevcDecode(): void {
  if (hevcDecodes !== undefined || typeof navigator === 'undefined' || !navigator.mediaCapabilities?.decodingInfo) return;
  hevcDecodes = true;
  navigator.mediaCapabilities.decodingInfo({
    type: 'media-source',
    video: { contentType: 'video/mp4; codecs="hvc1.1.6.L93.B0"', width: 1920, height: 1080, bitrate: 8_000_000, framerate: 24 },
  }).then((info) => { hevcDecodes = info.supported; }, () => undefined);
}
/** HEVC counts only when the path that will play it can: hls.js (MSE) for converted playback, the element for
 * native HLS (no MediaSource, as on iPhone). canPlayType alone says yes on browsers that then never decode a frame. */
function hevcPlays(mime: string): boolean {
  if (hevcFailed()) return false;
  probeHevcDecode();
  const mediaSource = globalThis.MediaSource;
  return hevcDecodes !== false && (typeof mediaSource?.isTypeSupported !== 'function' || mediaSource.isTypeSupported(mime));
}

export function browserSupportedPlaybackProfiles(): string[] {
  if (typeof document === 'undefined') return [];
  const media = document.createElement('video');
  const supported = (mime: string) => media.canPlayType(mime) !== '' && (!mime.includes('hvc1') || hevcPlays(mime));
  const profiles = [
    ["mp4-avc-aac", 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"'],
    ["mp4-av1-aac", 'video/mp4; codecs="av01.0.05M.08, mp4a.40.2"'],
    ["webm-vp8-vorbis", 'video/webm; codecs="vp8, vorbis"'],
    ["webm-vp8-opus", 'video/webm; codecs="vp8, opus"'],
    ["webm-vp9-vorbis", 'video/webm; codecs="vp9, vorbis"'],
    ["webm-vp9-opus", 'video/webm; codecs="vp9, opus"'],
    ["webm-av1-vorbis", 'video/webm; codecs="av01.0.05M.08, vorbis"'],
    ["webm-av1-opus", 'video/webm; codecs="av01.0.05M.08, opus"'],
    ["m4a-aac", 'audio/mp4; codecs="mp4a.40.2"'],
    ["webm-opus", 'audio/webm; codecs="opus"'],
    ["webm-vorbis", 'audio/webm; codecs="vorbis"'],
    ["mp3-mp3", 'audio/mpeg'],
    ["mp4-hevc-aac", 'video/mp4; codecs="hvc1.1.6.L93.B0, mp4a.40.2"'],
    ["mp4-hevc10-aac", 'video/mp4; codecs="hvc1.2.4.L153.B0, mp4a.40.2"'],
    ["mp4-ac3", 'audio/mp4; codecs="ac-3"'],
    ["mp4-eac3", 'audio/mp4; codecs="ec-3"'],
  ].filter((entry) => supported(entry[1])).map((entry) => entry[0]);
  const mediaSource = globalThis.MediaSource;
  const dashVideoSupported = (
    profiles.includes('mp4-avc-aac') && mediaSource?.isTypeSupported('video/mp4; codecs="avc1.42E01E"')
  ) || (
    profiles.includes('mp4-av1-aac') && mediaSource?.isTypeSupported('video/mp4; codecs="av01.0.05M.08"')
  );
  if (
    typeof mediaSource?.isTypeSupported === 'function'
    && profiles.includes('m4a-aac')
    && mediaSource.isTypeSupported('audio/mp4; codecs="mp4a.40.2"')
    && dashVideoSupported
  ) profiles.push('dash-segment-base');
  // WebM VP9 carries most 1440p/2160p ladders that lack AV1; it needs MSE, not just canPlayType.
  if (profiles.includes('dash-segment-base') && mediaSource.isTypeSupported('video/webm; codecs="vp9"')) profiles.push('dash-webm-vp9');
  // An HDR display lets the server skip tone-mapping for HDR10/HLG sources.
  if (typeof globalThis.matchMedia === 'function' && globalThis.matchMedia('(dynamic-range: high)').matches) profiles.push('hdr');
  return profiles;
}

export const youtubeSearch = (payload: YouTubeSearchRequest): Promise<YouTubeSearchResponse> => post('/api/youtube-search', payload);
/** Multi-source keyword search (YouTube + SoundCloud); a failed provider is reported in `errors`. */
export const sourceSearch = (payload: YouTubeSearchRequest): Promise<YouTubeSearchResponse> => post('/api/source-search', payload);

export const getPopularDiscovery = (options?: RequestJsonOptions): Promise<PopularSnapshot> => requestJson('/api/discovery/popular', undefined, options);
export const getLiveDiscovery = (options?: RequestJsonOptions): Promise<LiveSnapshot> => requestJson('/api/discovery/live', undefined, options);
const wallQuery = (cursor: string | null) => `?limit=40${cursor ? `&cursor=${enc(cursor)}` : ''}`;
/** 2.4.0 See all walls: the next page of one live or popular category (cursor null = first page). */
export const getLiveWall = (category: string, cursor: string | null = null, signal?: AbortSignal): Promise<WallPage> => requestJson(`/api/discovery/live/${enc(category)}${wallQuery(cursor)}`, undefined, { signal });
export const getPopularWall = (category: string, cursor: string | null = null, signal?: AbortSignal): Promise<WallPage> => requestJson(`/api/discovery/popular/${enc(category)}${wallQuery(cursor)}`, undefined, { signal });
/** GET /api/channels/youtube/{id}. A cold miss can take up to 15 s on the server, so the client waits 20 s. */
export const getChannelPage = (channelId: string, query: { tab?: ChannelTab; limit?: 60 | 120 } = {}, options?: RequestJsonOptions): Promise<ChannelPageResponse> =>
  requestJson(`/api/channels/youtube/${enc(channelId)}${qs({ tab: query.tab, limit: query.limit })}`, undefined, { timeoutMs: 20_000, ...options });
/** POST /api/channels/resolve: an @handle, /c/ or /user/ address to its UC… id. */
export const resolveChannel = (url: string, options?: RequestJsonOptions): Promise<ChannelResolveResponse> => post('/api/channels/resolve', { url }, { timeoutMs: 20_000, ...options });
export const getMemberInterests = (options?: RequestJsonOptions): Promise<MemberInterests> => requestJson('/api/discovery/interests', undefined, options);
export const updateMemberInterests = (keys: string[]): Promise<MemberInterests> => put('/api/discovery/interests', { keys });
export const getHomeRecommendations = (options?: RequestJsonOptions): Promise<MemberRecommendationSnapshot> => requestJson('/api/discovery/home', undefined, options);
export const getChannelSuggestions = (keys: string[], options?: RequestJsonOptions): Promise<ChannelSuggestions> => requestJson(`/api/discovery/channels${qs({ keys })}`, undefined, options);
export const searchChannels = (query: string, options?: RequestJsonOptions): Promise<ChannelCandidateList> => post('/api/discovery/channels/search', { query }, options);
export const completeOnboarding = (keys: string[], follows: FollowChannelRequest[] = []): Promise<MemberOnboardingState> => post('/api/onboarding/complete', { keys, follows });
export const skipOnboarding = (): Promise<MemberOnboardingState> => post('/api/onboarding/skip');
export const getUpNext = (payload: UpNextRequest, options?: RequestJsonOptions): Promise<MemberRecommendationSnapshot> => post('/api/discovery/up-next', payload, options);
export const suppressRecommendation = (input: SuppressRecommendationInput): Promise<Suppression> => post('/api/discovery/suppressions', input);
export const getSuppressions = (options?: RequestJsonOptions): Promise<SuppressionList> => requestJson('/api/discovery/suppressions', undefined, options);
export const restoreSuppression = (id: string): Promise<void> => del(`/api/discovery/suppressions/${enc(id)}`);
/** Impressions and opens, posted every 30 s by features/reco/recoEvents.ts. */
export const sendRecoEvents = (events: RecoEventIn[]): Promise<void> => post('/api/reco/events', { events } satisfies RecoEventBatch);
/** pagehide flush: sendBeacon cannot set headers, so the CSRF token travels in the body. False when the browser refused it. */
export function beaconRecoEvents(events: RecoEventIn[]): boolean {
  const body: RecoEventBatch = { csrf: csrfToken, events };
  return navigator.sendBeacon?.(`${apiBaseUrl}/api/reco/events`, new Blob([JSON.stringify(body)], { type: 'application/json' })) ?? false;
}
/** Settings → You → Clear recommendation history. */
export const clearRecoHistory = (): Promise<void> => del('/api/reco/history');

export const getSearchHistory = (options?: RequestJsonOptions): Promise<SearchHistoryEntry[]> => requestJson('/api/search/history', undefined, options);
// Fire-and-forget from the caller: a rejected promise here must never block or
// abort the search flow.
export const recordSearchHistory = (query: string): Promise<SearchHistoryEntry | null> => post('/api/search/history', { query });
export const deleteSearchHistoryEntry = (id: string): Promise<void> => del(`/api/search/history/${enc(id)}`);
export const clearSearchHistory = (): Promise<{ deleted: number }> => del('/api/search/history');

export type CollectionPageQuery = {
  cursor?: string | null;
  limit?: number;
};

export type LibraryPageQuery = CollectionPageQuery & {
  search?: string;
  kind?: LibraryViewKind;
  source?: string;
  group?: string;
  status?: 'available' | 'missing';
  sort?: 'recent' | 'title';
};

// group '' is meaningful (the ungrouped bucket), so only undefined is skipped.
const pageQuery = ({ search, cursor, limit, ...filters }: LibraryPageQuery) => qs({ search: search?.trim() || undefined, ...filters, cursor: cursor || undefined, limit: limit || undefined });

export const listJobs = (query: CollectionPageQuery = {}): Promise<JobPage> => requestJson(`/api/jobs${pageQuery(query)}`);
export const createJob = (payload: Record<string, unknown>): Promise<DownloadJob> => post('/api/jobs', payload);
export const cancelJob = (id: string): Promise<DownloadJob> => post(`/api/jobs/${enc(id)}/cancel`);
export const retryJob = (id: string): Promise<DownloadJob> => post(`/api/jobs/${enc(id)}/retry`);
export const clearCompletedJobs = (): Promise<{ deleted: number }> => del('/api/jobs/completed');

export const createAcquisitionBatch = (payload: AcquisitionBatchCreateRequest): Promise<AcquisitionBatch> => post('/api/acquisition-batches', payload);
export const listAcquisitionBatches = (): Promise<AcquisitionBatch[]> => requestJson('/api/acquisition-batches?limit=8&include_entries=true');
export const retryAcquisitionEntry = (batchId: string, entryId: string): Promise<AcquisitionBatch> => post(`/api/acquisition-batches/${enc(batchId)}/entries/${enc(entryId)}/retry`);

export const listLibrary = (query: LibraryPageQuery = {}, options?: RequestJsonOptions): Promise<LibraryPage> => requestJson(`/api/library${pageQuery(query)}`, undefined, options);
export const deleteLibraryFile = (id: string): Promise<LibraryItem> => post(`/api/library/${enc(id)}/delete-file`);
export const restoreLibraryFile = (id: string): Promise<LibraryItem> => post(`/api/library/${enc(id)}/restore-file`);
export const refreshLibrary = (): Promise<LibraryPage> => post('/api/library/refresh');
export const getLibraryItem = (id: string): Promise<LibraryItem> => requestJson(`/api/library/${enc(id)}`);

export const listHouseholdCollections = (): Promise<HouseholdCollection[]> => requestJson('/api/collections');
export const getHouseholdCollection = (id: string): Promise<HouseholdCollection> => requestJson(`/api/collections/${enc(id)}`);
export const createHouseholdCollection = (payload: { name: string; description?: string | null; visibility: CollectionVisibility; rules?: SmartCollectionRule | null }): Promise<HouseholdCollection> => post('/api/collections', payload);
export const renameHouseholdCollection = (id: string, name: string): Promise<HouseholdCollection> => put(`/api/collections/${enc(id)}/name`, { name });
export const setHouseholdCollectionVisibility = (id: string, visibility: CollectionVisibility): Promise<HouseholdCollection> => put(`/api/collections/${enc(id)}/visibility`, { visibility });
export const deleteHouseholdCollection = (id: string): Promise<void> => del(`/api/collections/${enc(id)}`);
export const addHouseholdCollectionItem = (collectionId: string, itemId: string): Promise<HouseholdCollection> => post(`/api/collections/${enc(collectionId)}/items/${enc(itemId)}`);

export type CollectionRemoteRef = { provider?: string | null; remote_id?: string | null; url: string; title?: string | null; uploader?: string | null; artwork_url?: string | null; duration?: number | null };

export const addHouseholdCollectionRemoteItem = (collectionId: string, ref: CollectionRemoteRef, expectedRevision?: number): Promise<HouseholdCollection> =>
  post(`/api/collections/${enc(collectionId)}/remote-items`, { ...ref, expected_revision: expectedRevision ?? null });
export const removeHouseholdCollectionEntry = (collectionId: string, entryId: string, expectedRevision?: number): Promise<HouseholdCollection> =>
  del(`/api/collections/${enc(collectionId)}/entries/${enc(entryId)}${qs({ expected_revision: expectedRevision })}`);
export const moveHouseholdCollectionEntry = (collectionId: string, entryId: string, position: number, expectedRevision: number): Promise<HouseholdCollection> =>
  patch(`/api/collections/${enc(collectionId)}/entries/${enc(entryId)}`, { position, expected_revision: expectedRevision });

export const searchLibrary = (query: string, limit = 12, scope?: SearchScope, options?: RequestJsonOptions): Promise<LocalSearchResponse> => requestJson(`/api/search${qs({ q: query, limit, scope })}`, undefined, options);

export const libraryMediaUrl = (id: string): string => `${apiBaseUrl}/api/library/${enc(id)}/media`;
const localPlaybackProfiles = (): string | undefined => browserSupportedPlaybackProfiles().join(',') || undefined;
export const getLocalPlaybackOptions = (id: string): Promise<LocalPlaybackOptions> =>
  requestJson(`/api/library/${enc(id)}/playback-options${qs({ profiles: localPlaybackProfiles() })}`, undefined, { timeoutMs: 30000 });
export const startLocalPlaybackSession = (id: string, start = 0, request?: PlaybackSessionRequest): Promise<LocalPlaybackSession> =>
  post(`/api/library/${enc(id)}/playback-sessions${qs({ start: start || undefined, profiles: localPlaybackProfiles() })}`, request, { timeoutMs: 30000 });
export const stopLocalPlaybackSession = (sessionId: string): Promise<void> => del(`/api/playback-sessions/${enc(sessionId)}`);

export const listContinueWatching = (): Promise<PlaybackProgress[]> => requestJson('/api/playback/continue');
export const getPlaybackProgress = (id: string): Promise<PlaybackProgress | null> => requestJson(`/api/library/${enc(id)}/playback`, undefined, { timeoutMs: 2500 });
export const updatePlaybackProgress = (id: string, payload: { position_seconds: number; duration_seconds?: number | null; completed?: boolean }): Promise<PlaybackProgress> =>
  put(`/api/library/${enc(id)}/playback`, payload);
export const clearPlaybackProgress = (id: string): Promise<void> => del(`/api/library/${enc(id)}/playback`);

export const getRemotePlaybackProgress = (sourceIdentity: string, signal?: AbortSignal): Promise<RemotePlaybackProgress | null> =>
  requestJson(`/api/playback/remote/${enc(sourceIdentity)}`, undefined, { timeoutMs: 2500, signal });
const remoteStreamPath = (streamId: string) => `/api/remote-streams/${encodeURIComponent(streamId)}`;

// Refresh re-resolves the source upstream, so it gets the same budget as local conversion.
export function refreshRemoteStream(streamId: string, signal: AbortSignal): Promise<RemotePlayback> {
  return requestJson(`${remoteStreamPath(streamId)}/refresh`, { method: 'POST' }, { signal, timeoutMs: 30000 });
}

export function selectRemoteRendition(streamId: string, renditionId: string, signal: AbortSignal): Promise<RemotePlayback> {
  return requestJson(`${remoteStreamPath(streamId)}/renditions/${encodeURIComponent(renditionId)}/select`, { method: 'POST' }, { signal, timeoutMs: 30000 });
}

// keepalive writes (a player's last checkpoint, a stream release) are fire-and-forget for their callers; a member change
// waits for them so the leaving member's last writes go out under their own cookie.
const keepaliveWrites = new Set<Promise<unknown>>();
function trackKeepalive<T>(request: Promise<T>): Promise<T> {
  keepaliveWrites.add(request);
  void request.catch(() => undefined).finally(() => keepaliveWrites.delete(request));
  return request;
}
/** Settles once every keepalive write already sent has its answer (or failed). */
export function keepaliveWritesSettled(): Promise<void> {
  return Promise.allSettled([...keepaliveWrites]).then(() => undefined);
}

/** keepalive lets the release outlive a page unload. */
export function releaseRemoteStream(streamId: string): Promise<void> {
  return trackKeepalive(requestJson(remoteStreamPath(streamId), { method: 'DELETE', keepalive: true }));
}

export function updateRemotePlaybackProgress(
  sourceIdentity: string,
  payload: {
    source_identity: string;
    source_url: string;
    title?: string | null;
    uploader?: string | null;
    artwork_url?: string | null;
    position_seconds: number;
    duration_seconds?: number | null;
    completed?: boolean;
    checkpoint_client_id: string;
    checkpoint_sequence: number;
    expected_revision: number;
    /** The entry's channel, when the entry carries it. */
    channel_id?: string | null;
    channel_url?: string | null;
  },
  options?: { keepalive?: boolean },
): Promise<RemotePlaybackProgress> {
  const request = requestJson<RemotePlaybackProgress>(`/api/playback/remote/${enc(sourceIdentity)}`, { method: 'PUT', body: json(payload), keepalive: options?.keepalive });
  return options?.keepalive ? trackKeepalive(request) : request;
}

export const clearRemotePlaybackProgress = (sourceIdentity: string, checkpointClientId: string, checkpointSequence: number, expectedRevision: number): Promise<RemotePlaybackProgress> =>
  del(`/api/playback/remote/${enc(sourceIdentity)}${qs({ checkpoint_client_id: checkpointClientId, checkpoint_sequence: checkpointSequence, expected_revision: expectedRevision })}`);

export const getChatReplay = (sourceIdentity: string, signal?: AbortSignal): Promise<ChatReplayAsset | null> =>
  requestJson(`/api/chat-replay/${enc(sourceIdentity)}`, undefined, { timeoutMs: 4000, signal });
export const loadChatReplay = (sourceIdentity: string, payload: { source_url: string; refresh?: boolean }, signal?: AbortSignal): Promise<ChatReplayAsset> =>
  post(`/api/chat-replay/${enc(sourceIdentity)}`, payload, { timeoutMs: 60000, signal });

export interface LiveChatRead { status: 'active' | 'ended' | 'unavailable'; events: TimedChatEvent[]; cursor: number; poll_after_ms: number }
export const getLiveChat = (sourceUrl: string, after: number, signal?: AbortSignal): Promise<LiveChatRead> =>
  requestJson(`/api/live-chat?source_url=${enc(sourceUrl)}&after=${after}`, undefined, { timeoutMs: 30000, signal });

export function createLiveRecording(
  payload: {
    source_url: string;
    format_selection?: Record<string, unknown>;
    output_profile?: Record<string, unknown>;
    // From-start member intent + scheduling (issue #98) — never raw yt-dlp flags.
    start_intent?: LiveRecordingStartIntent;
    fallback_policy?: LiveRecordingFallbackPolicy;
  },
  signal?: AbortSignal,
): Promise<LiveRecording> {
  return post('/api/live-recordings', payload, { timeoutMs: 60000, signal });
}

export const listLiveRecordings = (query: CollectionPageQuery = {}, signal?: AbortSignal): Promise<LiveRecordingPage> => requestJson(`/api/live-recordings${pageQuery(query)}`, undefined, { signal });
export const getLiveRecording = (id: string, signal?: AbortSignal): Promise<LiveRecording> => requestJson(`/api/live-recordings/${enc(id)}`, undefined, { signal });
export const stopLiveRecording = (id: string): Promise<LiveRecording> => post(`/api/live-recordings/${enc(id)}/stop`);
export const cancelLiveRecording = (id: string): Promise<LiveRecording> => post(`/api/live-recordings/${enc(id)}/cancel`);

export const listLibraryNotes = (id: string): Promise<LibraryNote[]> => requestJson(`/api/library/${enc(id)}/notes`, undefined, BACKGROUND);
export const createLibraryNote = (id: string, note: { body: string; visibility: NoteVisibility; timestamp_ms?: number | null }): Promise<LibraryNote> => post(`/api/library/${enc(id)}/notes`, note);
export const updateLibraryNote = (id: string, note: { body: string; visibility: NoteVisibility }): Promise<LibraryNote> => put(`/api/library/notes/${enc(id)}`, note);
export const deleteLibraryNote = (id: string): Promise<void> => del(`/api/library/notes/${enc(id)}`);

export const listLibraryTags = (id: string): Promise<UserTag[]> => requestJson(`/api/library/${enc(id)}/tags`, undefined, BACKGROUND);
export const createLibraryTag = (id: string, tag: string): Promise<UserTag> => post(`/api/library/${enc(id)}/tags`, { tag });
export const deleteLibraryTag = (id: string, tag: string): Promise<void> => del(`/api/library/${enc(id)}/tags/${enc(tag)}`);

export const getMySettings = (): Promise<UserSettings> => requestJson('/api/settings/me');
export const updateMySettings = (payload: UserSettingsUpdateRequest): Promise<UserSettings> => put('/api/settings/me', payload);
export function keepLiveRecording(id: string, kept: boolean): Promise<LiveRecording> {
  return requestJson(`/api/live-recordings/${encodeURIComponent(id)}/keep`, { method: 'PUT', body: JSON.stringify({ kept }) });
}

export interface RecordingRetention { keep_days: number; max_gb: number }

export function getRecordingRetention(): Promise<RecordingRetention> {
  return requestJson('/api/admin/recording-retention');
}

export function updateRecordingRetention(policy: RecordingRetention): Promise<RecordingRetention> {
  return requestJson('/api/admin/recording-retention', { method: 'PUT', body: JSON.stringify(policy) });
}

export const listAutomations = (): Promise<SourceAutomation[]> => requestJson('/api/automations');
/** Ask the server to refresh this member's follows; returns the follows marked due. */
export const refreshFollows = (): Promise<SourceAutomation[]> => post('/api/follows/refresh');
export const createAutomation = (payload: SourceAutomationCreateRequest): Promise<SourceAutomation> => post('/api/automations', payload);
export const setAutomationAutoDownload = (id: string, payload: SourceAutomationAutoDownloadRequest): Promise<SourceAutomation> => patch(`/api/automations/${enc(id)}/auto-download`, payload);
export const runAutomation = (id: string): Promise<AutomationRun> => post(`/api/automations/${enc(id)}/run`, undefined, { timeoutMs: 120000 });
export const pauseAutomation = (id: string): Promise<SourceAutomation> => post(`/api/automations/${enc(id)}/pause`);
export const resumeAutomation = (id: string): Promise<SourceAutomation> => post(`/api/automations/${enc(id)}/resume`);
export const deleteAutomation = (id: string): Promise<void> => del(`/api/automations/${enc(id)}`);

export const updateLibraryItemVisibility = (id: string, visibility: 'private' | 'shared'): Promise<LibraryItem> => put(`/api/library/${enc(id)}/visibility`, { visibility });

export const listUsers = (): Promise<UserProfile[]> => requestJson('/api/admin/users');
export const updateUser = (id: string, payload: Record<string, unknown>): Promise<UserProfile> => put(`/api/admin/users/${enc(id)}`, payload);
export const createInvitation = (role: 'admin' | 'viewer'): Promise<{ id: string; role: string; expires_at: string; invitation_url: string }> => post('/api/admin/invitations', { role });

export interface InvitationSummary { id: string; role: 'admin' | 'viewer'; status: 'pending' | 'used' | 'revoked' | 'expired'; created_at: string; expires_at: string }

export const listInvitations = (): Promise<InvitationSummary[]> => requestJson('/api/admin/invitations');
export const revokeInvitation = (id: string): Promise<InvitationSummary> => post(`/api/admin/invitations/${enc(id)}/revoke`);
export const redeemInvitation = (payload: { token: string; username: string; display_name: string; password: string }): Promise<UserProfile> => post('/api/invitations/redeem', payload);
export const unlockUserSignIn = (userId: string): Promise<void> => post(`/api/admin/users/${enc(userId)}/unlock`);
export const createPasswordResetLink = (userId: string): Promise<{ expires_at: string; reset_url: string }> => post(`/api/admin/users/${enc(userId)}/reset-link`);
export const redeemPasswordReset = (token: string, newPassword: string): Promise<void> => post('/api/password-reset/redeem', { token, new_password: newPassword });

/** Reauthenticates with the current password; the server rotates this browser's session and signs out all others. */
export const changePassword = (currentPassword: string, newPassword: string): Promise<SessionState> =>
  post<SessionState>('/api/me/password', { current_password: currentPassword, new_password: newPassword }).then(rememberCsrf);

export const getAdminSettings = (): Promise<AdminSettings> => requestJson('/api/admin/settings');
export const updateAdminSettings = (payload: Partial<Pick<AdminSettings, 'concurrency' | 'max_active_jobs_per_user' | 'min_free_disk_mb' | 'extra_source_ports' | 'require_owner_two_factor'>>): Promise<AdminSettings> => put('/api/admin/settings', payload);
export const getAdminOverview = (): Promise<AdminOverview> => requestJson('/api/admin/overview');

export type StorageSource = 'youtube' | 'twitch' | 'kick' | 'soundcloud' | 'generic';
export type StorageMediaKind = 'video' | 'audio' | 'recording';
export interface StorageRootRecord {
  id: string; label: string; path: string; mode: 'managed' | 'external'; enabled: boolean; minimum_free_bytes: number; artifact_count: number;
  observation: { state?: string; checked_at?: string; free_bytes?: number | null; total_bytes?: number | null };
}
export interface StorageRule {
  id: string; enabled: boolean; priority: number; sources: StorageSource[]; media_kinds: StorageMediaKind[];
  min_height: number | null; max_height: number | null; target_root_id: string; relative_template: string;
}
export interface StorageRuleSet { revision: number; default_root_id: string | null; rules: StorageRule[] }
export interface StorageDecision { rule_id: string | null; rule_revision: number; root_id: string; root_label: string; relative_path: string; reason: string; can_admit: boolean }

const STORAGE = '/api/admin/storage';
export const listStorageRoots = (): Promise<StorageRootRecord[]> => requestJson(`${STORAGE}/roots`);
export const createStorageRoot = (payload: { label: string; container_path: string; mode: 'managed' | 'external'; minimum_free_bytes: number }): Promise<StorageRootRecord> => post(`${STORAGE}/roots`, payload);
export const updateStorageRoot = (id: string, payload: { enabled?: boolean; label?: string; minimum_free_bytes?: number }): Promise<StorageRootRecord> => patch(`${STORAGE}/roots/${enc(id)}`, payload);
export const probeStorageRoot = (id: string): Promise<StorageRootRecord> => post(`${STORAGE}/roots/${enc(id)}/probe`);
export const deleteStorageRoot = (id: string): Promise<void> => del(`${STORAGE}/roots/${enc(id)}`);
export const getStorageRules = (): Promise<StorageRuleSet> => requestJson(`${STORAGE}/rules`);
export const saveStorageRules = (payload: { expected_revision: number; default_root_id: string | null; rules: StorageRule[] }): Promise<StorageRuleSet> => put(`${STORAGE}/rules`, payload);
export const resolveStorage = (payload: { source: StorageSource; media_kind: StorageMediaKind; height: number | null }): Promise<StorageDecision> => post(`${STORAGE}/resolve`, payload);

export interface BackupManifest { name: string; kind: 'manual' | 'scheduled'; created_at: string; app_version: string; schema_version: number; size: number; sha256: string; counts: Record<string, number> }
export interface BackupSchedule { daily: boolean; keep: number }

export const listBackups = (): Promise<{ backups: BackupManifest[]; schedule: BackupSchedule }> => requestJson('/api/admin/backups');
export const createBackup = (): Promise<BackupManifest> => post('/api/admin/backups');
export const verifyBackup = (name: string): Promise<{ ok: boolean; problems: string[] }> => post(`/api/admin/backups/${enc(name)}/verify`);
export const deleteBackup = (name: string): Promise<void> => del(`/api/admin/backups/${enc(name)}`);
export const updateBackupSchedule = (schedule: BackupSchedule): Promise<BackupSchedule> => put('/api/admin/backups/schedule', schedule);
// the copy is buffered in memory as a Blob; stream to disk if databases grow past a few hundred MB.
export async function downloadBackup(name: string, password: string): Promise<Blob> {
  const response = await fetch(`${apiBaseUrl}/api/admin/backups/${enc(name)}/download`, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', ...csrfHeaders() }, body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new ApiRequestError(typeof body?.detail === 'string' ? body.detail : `Download failed with ${response.status}`, response.status);
  }
  return response.blob();
}

export interface AdminDiagnostics {
  generated_at: string;
  status: 'ok' | 'degraded';
  versions: Record<string, string | null>;
  runtime: Record<string, boolean>;
  storage_roots: { label: string; mode: string; enabled: boolean; state: string | null; checked_at: string | null }[];
  queue: { jobs_by_status: Record<string, number>; concurrency: number; event_streams: number };
  maintenance_sweeps: { consecutive_failures: number; last_error: string | null; last_success_at: string | null; last_failure_at: string | null };
  persistence: Record<string, Record<string, number>>;
  recent_errors: { source: string; at: string | null; message: string }[];
  ai: { enabled: boolean; ok?: boolean; model_available?: boolean; error?: string | null; asr_configured: boolean };
  playback?: TranscodeDiagnostics | null;
  artwork?: ArtworkProgress | null;
  media_loading?: MediaLoading | null;
  recommendations?: RecoDiagnostics | null;
  library_automation?: LibraryAutomationDiagnostics | null;
}

/** Admin-only, already redacted server-side (no paths, tokens or keys). */
export const getAdminActivity = (signal?: AbortSignal): Promise<AdminActivity> => requestJson('/api/admin/activity', undefined, { signal });
export const stopActivitySession = (sessionId: string): Promise<void> => post(`/api/admin/activity/${enc(sessionId)}/stop`);
export const getActivityHistory = (params: { userId?: string; q?: string; before?: string | null; limit?: number } = {}): Promise<ActivityHistoryPage> => {
  const query = new URLSearchParams();
  if (params.userId) query.set('user_id', params.userId);
  if (params.q) query.set('q', params.q);
  if (params.before) query.set('before', params.before);
  query.set('limit', String(params.limit ?? 50));
  return requestJson(`/api/admin/activity/history?${query}`);
};
export const getAdminDiagnostics = (): Promise<AdminDiagnostics> => requestJson('/api/admin/diagnostics');

export type ImportVisibility = 'private' | 'shared';
export type ImportOutcome = 'failed' | 'skipped' | 'review';
export interface ImportRun {
  id: string; root_id: string; state: string; visibility: ImportVisibility; counters: Record<string, number>;
  coverage: 'complete' | 'incomplete'; error: string | null; created_at: string; finished_at: string | null;
}
export interface ImportEntry { relative_path: string; outcome: ImportOutcome; error: string | null; library_item_id: string | null }
const IMPORTS = '/api/admin/imports';
export const listImports = (): Promise<ImportRun[]> => requestJson(IMPORTS);
export const getImport = (id: string): Promise<ImportRun> => requestJson(`${IMPORTS}/${enc(id)}`);
export const startImport = (payload: { root_id: string; visibility: ImportVisibility }): Promise<ImportRun> => post(IMPORTS, payload);
export const listImportEntries = (id: string, outcome?: ImportOutcome): Promise<ImportEntry[]> => requestJson(`${IMPORTS}/${enc(id)}/entries${qs({ outcome })}`);
export const cancelImport = (id: string): Promise<ImportRun> => post(`${IMPORTS}/${enc(id)}/cancel`);
export const resumeImport = (id: string): Promise<ImportRun> => post(`${IMPORTS}/${enc(id)}/resume`);

export function connectEvents(onEvent: (event: AppEvent) => void): EventSource {
  const source = new EventSource(`${apiBaseUrl}/api/events`, { withCredentials: true });
  const eventTypes: AppEvent['type'][] = [
    'preview_ready',
    'job_queued',
    'job_started',
    'job_progress',
    'job_postprocess',
    'job_completed',
    'job_failed',
    'library_item_upserted',
    'library_item_missing',
    'settings_updated',
    'automation_checked',
    'automation_item_decided',
    'automation_failed',
    'model_state',
  ];
  for (const eventType of eventTypes) {
    source.addEventListener(eventType, (event) => {
      try {
        onEvent({
          type: eventType,
          payload: JSON.parse((event as MessageEvent).data) as unknown,
        });
      } catch {
        // Ignore malformed event payloads so the stream stays alive.
      }
    });
  }
  return source;
}

const WATCH_QUEUE_URL = '/api/me/watch-queue';
export const getWatchQueue = (): Promise<WatchQueue> => requestJson(WATCH_QUEUE_URL, undefined, BACKGROUND);
export const addToWatchQueue = (ref: WatchQueueRef, position: 'next' | 'end'): Promise<WatchQueue> => post(`${WATCH_QUEUE_URL}/entries`, { ref, position });
export const moveWatchQueueEntry = (id: string, position: number, expectedRevision: number): Promise<WatchQueue> => patch(`${WATCH_QUEUE_URL}/entries/${enc(id)}`, { position, expected_revision: expectedRevision });
export const removeWatchQueueEntry = (id: string): Promise<WatchQueue> => del(`${WATCH_QUEUE_URL}/entries/${enc(id)}`);
export const clearWatchQueue = (): Promise<WatchQueue> => del(`${WATCH_QUEUE_URL}?confirm=true`);

// Transcripts — immutable revisions, cues paged by ordinal cursor.
export type Transcript = { id: string; library_item_id: string; language: string; source_kind: string; revision: number; cue_count: number; model_label: string | null; created_at: string };
export type TranscriptCue = { ordinal: number; start_ms: number; end_ms: number; text: string };

export const listTranscripts = (itemId: string, signal?: AbortSignal): Promise<Transcript[]> => requestJson(`/api/library/${enc(itemId)}/transcripts`, undefined, { signal, background: true });
export const listTranscriptCues = (id: string, cursor: string | null, limit = 100): Promise<{ items: TranscriptCue[]; next_cursor: string | null }> =>
  requestJson(`/api/transcripts/${enc(id)}/cues${qs({ limit, cursor: cursor || undefined })}`);
export const searchTranscript = (id: string, q: string): Promise<TranscriptCue[]> => requestJson(`/api/transcripts/${enc(id)}/search${qs({ limit: 100, q })}`);

// Local summaries: evidence-grounded, generated on request only.
export type Summary = {
  id: string; library_item_id: string; transcript_id: string; transcript_revision: number; model_id: string;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'interrupted';
  overview: string | null; key_points: Array<{ text: string; cue_ordinals: number[]; start_ms: number }>;
  chapters: Array<{ title: string; cue_ordinal: number; start_ms: number }>; dropped_points: number; error: string | null;
  created_at: string; completed_at: string | null;
};

export const getLatestSummary = (itemId: string, signal?: AbortSignal): Promise<Summary> => requestJson(`/api/library/${enc(itemId)}/summary`, undefined, { signal, background: true });
export const requestSummary = (itemId: string): Promise<Summary> => post(`/api/library/${enc(itemId)}/summaries`, {});
export const getSummary = (id: string): Promise<Summary> => requestJson(`/api/summaries/${enc(id)}`);

// What a member may request from local enrichment; endpoints and models stay admin-only.
/** `disabled_features`: the vault owner's per-feature Off switches (AppSettings.ai_features_disabled). */
export type EnrichmentCapabilities = { ai_summaries: boolean; asr: boolean; disabled_features?: string[] };
export const getEnrichmentCapabilities = (signal?: AbortSignal): Promise<EnrichmentCapabilities> => requestJson('/api/enrichment', undefined, { signal, background: true });

// Local ASR jobs.
export type AsrJob = { id: string; library_item_id: string; model_id: string; state: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'interrupted'; transcript_id: string | null; error: string | null; created_at: string; completed_at: string | null };
export const getLatestAsrJob = (itemId: string, signal?: AbortSignal): Promise<AsrJob> => requestJson(`/api/library/${enc(itemId)}/transcripts/asr`, undefined, { signal });
export const requestAsrTranscript = (itemId: string): Promise<AsrJob> => post(`/api/library/${enc(itemId)}/transcripts/asr`, {});

// Admin local AI configuration and task view.
export type AiRequirement = 'search_model' | 'speech_model' | 'assistant';
export type AiFeatureReadiness = { requires: AiRequirement | null; ready: boolean; reason: string | null };
export type AiConfig = {
  base_url: string; model: string; has_api_key: boolean; max_concurrency: number; context_tokens: number;
  asr_base_url: string; asr_model: string; embedding_model?: string; enabled: boolean; asr_available: boolean;
  ai_features_disabled: string[];
  /** Per AI feature key: what it needs and whether that is available now (the Off switch is ai_features_disabled). */
  features: Record<string, AiFeatureReadiness>;
  /** The admin's cap on on-device model threads; null = automatic (model_threads_auto). */
  model_threads: number | null;
  model_threads_auto: number;
};
/** model_threads: 0 = automatic. */
export type AiConfigUpdate = Partial<Omit<AiConfig, 'has_api_key' | 'enabled' | 'asr_available' | 'features' | 'model_threads' | 'model_threads_auto'>> & { api_key?: string; model_threads?: number };
export const getAiConfig = (): Promise<AiConfig> => requestJson('/api/admin/ai/config');
export const updateAiConfig = (payload: AiConfigUpdate): Promise<AiConfig> => put('/api/admin/ai/config', payload);
export type LocalModelRole = 'search' | 'speech';
export type LocalModelState = 'absent' | 'downloading' | 'verifying' | 'ready' | 'failed';
/** One on-device model row (GET /api/admin/models and the `model_state` event); admin-only. */
export type LocalModel = {
  id: string; role: LocalModelRole; name: string; description: string; licence: string;
  size_bytes: number; ram_bytes: number; default: boolean; active: boolean;
  state: LocalModelState; bytes_done: number | null; bytes_total: number | null; reason: string | null;
  running: boolean; features: string[];
};
export type LocalModelRemoval = { model: LocalModel; disabled_features: string[] };
/** Payload of the `model_state` server event (sent to admins only). */
export type ModelStateEvent = { model: LocalModel };
const MODELS = '/api/admin/models';
export const listLocalModels = (): Promise<{ models: LocalModel[] }> => requestJson(MODELS);
/** Download, or Retry after a failure. */
export const downloadLocalModel = (id: string): Promise<LocalModel> => post(`${MODELS}/${enc(id)}/download`);
export const cancelLocalModelDownload = (id: string): Promise<LocalModel> => post(`${MODELS}/${enc(id)}/cancel`);
export const activateLocalModel = (id: string): Promise<LocalModel> => post(`${MODELS}/${enc(id)}/activate`);
export const removeLocalModel = (id: string): Promise<LocalModelRemoval> => del(`${MODELS}/${enc(id)}`);
export const testAiConnection = (): Promise<{ ok: boolean; models: string[]; model_available: boolean; error: string | null }> => post('/api/admin/ai/test', {});

export type TaskKind = 'download' | 'asr' | 'summary' | 'import';
export type TaskFilter = 'all' | 'active' | 'failed' | 'finished';
export type AdminTask = {
  kind: TaskKind; id: string; status: string; title: string | null; detail: string | null; owner: string | null; error: string | null;
  attempts: number; library_item_id: string | null; created_at: string; finished_at: string | null; can_cancel: boolean; can_retry: boolean;
};
export type AdminTaskPage = { items: AdminTask[]; next_cursor: string | null; counts: Record<TaskKind, Record<string, number>> };
export const listAdminTasks = (kind: TaskKind, status: TaskFilter, cursor?: string | null): Promise<AdminTaskPage> =>
  requestJson(`/api/admin/tasks${qs({ kind, status, limit: 50, cursor: cursor || undefined })}`);
export const cancelAdminTask = (kind: TaskKind, id: string): Promise<{ status: string }> => post(`/api/admin/tasks/${kind}/${enc(id)}/cancel`, {});

// Idea graph: bounded concepts/relations of one summary, each citing transcript cues.
type IdeaGraphNode = { id: string; label: string; cue_ordinals: number[]; start_ms: number };
type IdeaGraphEdge = { source: string; target: string; label: string; cue_ordinals: number[]; start_ms: number };
export type IdeaGraph = {
  id: string; library_item_id: string; summary_id: string; transcript_id: string; transcript_revision: number; model_id: string;
  state: Summary['state']; nodes: IdeaGraphNode[]; edges: IdeaGraphEdge[]; dropped: number; error: string | null; created_at: string; completed_at: string | null;
};

export const getLatestIdeaGraph = (itemId: string, signal?: AbortSignal): Promise<IdeaGraph> => requestJson(`/api/library/${enc(itemId)}/idea-graph`, undefined, { signal });
export const requestIdeaGraph = (itemId: string): Promise<IdeaGraph> => post(`/api/library/${enc(itemId)}/idea-graphs`, {});
export const getIdeaGraph = (id: string): Promise<IdeaGraph> => requestJson(`/api/idea-graphs/${enc(id)}`);

// Source provenance: stored facts only; addresses are canonical public pages, never paths.
export type Provenance = {
  origin: 'saved' | 'imported' | 'recording'; provider: string | null; original_url: string | null; channel: string | null; channel_url: string | null;
  uploaded_on: string | null; saved_at: string | null; storage_label: string | null; storage_mode: 'managed' | 'external' | null; media_state: string | null;
  file_size: number | null; format: { container?: string; video_codec?: string; audio_codec?: string; width?: number; height?: number } | null;
  notes_count: number; related: Array<{ reason: 'same_channel' | 'same_series'; name: string; count: number; items: Array<{ id: string; title: string }> }>;
};

export const getProvenance = (itemId: string, signal?: AbortSignal): Promise<Provenance> => requestJson(`/api/library/${enc(itemId)}/provenance`, undefined, { signal, background: true });

// ---- Media vault ----

/** GET /api/titles: `category` or `type` picks the wall. Unset, false and empty filters are omitted. */
export type TitleListQuery = {
  type?: TitleType; category?: TitleCategory; sort?: TitleSort; cursor?: string | null; limit?: number; letter?: string | null;
  unwatched?: boolean; in_progress?: boolean; favorites?: boolean; genre?: readonly string[];
  year_from?: number | null; year_to?: number | null; resolution?: readonly TitleResolution[];
};
export const listTitles = ({ type, category, sort, cursor, limit, letter, unwatched, in_progress, favorites, genre, year_from, year_to, resolution }: TitleListQuery, options?: RequestJsonOptions): Promise<TitlePage> =>
  requestJson(`/api/titles${qs({
    type, category, sort, cursor: cursor || undefined, limit, letter: letter || undefined,
    unwatched: unwatched || undefined, in_progress: in_progress || undefined, favorites: favorites || undefined,
    genre: genre?.length ? genre : undefined, year_from, year_to, resolution: resolution?.length ? resolution : undefined,
  })}`, undefined, options);
/** GET /api/titles/facets: exactly one of `type` or `category`. */
export type TitleFacetsQuery = { type: 'movie' | 'series' | 'album'; category?: undefined } | { category: TitleCategory; type?: undefined };
export const getTitleFacets = (query: TitleFacetsQuery, options?: RequestJsonOptions): Promise<TitleFacets> =>
  requestJson(`/api/titles/facets${qs({ type: query.type, category: query.category })}`, undefined, options);
/** GET /api/library/sections: counts per Library tab for the member. */
export const getLibrarySections = (options?: RequestJsonOptions): Promise<LibrarySections> => requestJson('/api/library/sections', undefined, options);
/** GET /api/library/channels: saved YouTube grouped by channel, as Infuse groups it. */
export const listLibraryChannels = (sort: 'recent' | 'name' = 'recent', options?: RequestJsonOptions): Promise<LibraryChannelResponse[]> =>
  requestJson(`/api/library/channels${qs({ source: 'youtube', sort })}`, undefined, options);
export const getEpisodeSummaries = (seriesId: string, season: number, options?: RequestJsonOptions): Promise<EpisodeSummaries> =>
  requestJson(`/api/titles/${enc(seriesId)}/episode-summaries${qs({ season })}`, undefined, options);
export const getKeyScenes = (titleId: string, options?: RequestJsonOptions): Promise<KeyScenes> => requestJson(`/api/titles/${enc(titleId)}/key-scenes`, undefined, options);
export const getArtworkProgress = (): Promise<ArtworkProgress> => requestJson('/api/admin/artwork');
export const sendClientMetrics = (samples: ClientMetricSample[]): Promise<void> => post('/api/metrics/client', { samples } satisfies ClientMetricsBatch);
/** pagehide flush: sendBeacon cannot set headers, so the CSRF token travels in the body. False when the browser refused it. */
export function beaconClientMetrics(samples: ClientMetricSample[]): boolean {
  const body: ClientMetricsBatch = { csrf: csrfToken, samples };
  return navigator.sendBeacon?.(`${apiBaseUrl}/api/metrics/client`, new Blob([JSON.stringify(body)], { type: 'application/json' })) ?? false;
}
export const getTitle = (id: string): Promise<TitleDetail> => requestJson(`/api/titles/${enc(id)}`, undefined, BACKGROUND);
export const listSeasonEpisodes = (seriesId: string, season: number): Promise<TitleSummary[]> => requestJson(`/api/titles/${enc(seriesId)}/episodes${qs({ season })}`, undefined, BACKGROUND);
export const getLibraryUpNext = (itemId: string): Promise<LibraryUpNext> => requestJson(`/api/library/${enc(itemId)}/up-next`, undefined, BACKGROUND);
export const listNextUp = (limit = 12): Promise<TitleSummary[]> => requestJson(`/api/titles/next-up${qs({ limit })}`);
export const setTitleWatched = (id: string, watched: boolean): Promise<TitleUserData> => put(`/api/titles/${enc(id)}/watched`, { watched } satisfies TitleWatchedRequest);
export const setFavorite = (targetId: string, favorite: boolean): Promise<void> => (favorite ? put(`/api/me/favorites/${enc(targetId)}`, undefined) : del(`/api/me/favorites/${enc(targetId)}`));
export const dismissNextUp = (seriesId: string): Promise<void> => post(`/api/titles/${enc(seriesId)}/next-up/dismiss`);
export const hideContinueWatching = (itemId: string): Promise<void> => post(`/api/library/${enc(itemId)}/playback/dismiss`);
export const unhideContinueWatching = (itemId: string): Promise<void> => del(`/api/library/${enc(itemId)}/playback/dismiss`);
export const listSimilarTitles = (id: string, limit = 12): Promise<TitleSummary[]> => requestJson(`/api/titles/${enc(id)}/similar${qs({ limit })}`);
export const getHomeTitleRows = (): Promise<TitleRowsResponse> => requestJson('/api/home/title-rows');
export const getRecap = (episodeId: string): Promise<RecapResponse> => requestJson(`/api/titles/${enc(episodeId)}/recap`, undefined, BACKGROUND);
export const requestRecap = (episodeId: string): Promise<RecapResponse> => post(`/api/titles/${enc(episodeId)}/recap`);
export const buildSmartCollectionRules = (prompt: string): Promise<SmartRuleDraft> => post('/api/collections/rules/draft', { prompt } satisfies SmartRuleDraftRequest, { timeoutMs: 60000 });
export const previewSmartCollectionRules = (rule: SmartCollectionRule): Promise<SmartRulePreview> => post('/api/collections/rules/preview', rule);
export const setSmartCollectionRules = (id: string, rule: SmartCollectionRule): Promise<HouseholdCollection> => put(`/api/collections/${enc(id)}/rules`, rule);

export const listUnmatchedTitles = (): Promise<TitleSummary[]> => requestJson('/api/admin/metadata/unmatched');
export const searchTitleMatches = (id: string, q: string, year?: number | null): Promise<IdentifyCandidate[]> => requestJson(`/api/admin/titles/${enc(id)}/identify${qs({ q, year })}`, undefined, { timeoutMs: 30000 });
export const identifyTitle = (id: string, tmdbId: number): Promise<MetadataRefreshResponse> => post(`/api/admin/titles/${enc(id)}/identify`, { tmdb_id: tmdbId } satisfies IdentifyRequest);
export const unmatchTitle = (id: string): Promise<MetadataRefreshResponse> => post(`/api/admin/titles/${enc(id)}/unmatch`);
export const refreshTitleMetadata = (id: string): Promise<MetadataRefreshResponse> => post(`/api/admin/titles/${enc(id)}/refresh`);
export const refreshAllMetadata = (): Promise<MetadataBulkRefreshResponse> => post('/api/admin/metadata/refresh-all');
export const testTmdbKey = (): Promise<ConnectionTestResponse> => post('/api/admin/media-server/tmdb-test', undefined, { timeoutMs: 30000 });

export const listConnectedApps = (): Promise<ConnectedApp[]> => requestJson('/api/connected-apps');
export const createAgentToken = (payload: ConnectedAppCreateRequest): Promise<ConnectedAppCreated> => post('/api/connected-apps', payload);
export const revokeConnectedApp = (id: string): Promise<void> => del(`/api/connected-apps/${enc(id)}`);
export const createAppPassword = (name: string): Promise<AppPasswordCreated> => post('/api/connected-apps/app-passwords', { name });
export const signOutAllApps = (): Promise<{ revoked: number }> => post('/api/connected-apps/sign-out-all');
export const getTwoFactor = (): Promise<TwoFactorStatus> => requestJson('/api/me/two-factor');
export const setupTwoFactor = (password: string): Promise<TwoFactorSetup> => post('/api/me/two-factor/setup', { password });
export const enableTwoFactor = (code: string): Promise<{ recovery_codes: string[] }> => post('/api/me/two-factor/enable', { code });
export const disableTwoFactor = (password: string, code: string): Promise<void> => post('/api/me/two-factor/disable', { password, code });
export const regenerateRecoveryCodes = (password: string, code: string): Promise<{ recovery_codes: string[] }> => post('/api/me/two-factor/recovery-codes', { password, code });
export const resetMemberTwoFactor = (userId: string): Promise<void> => del(`/api/admin/users/${enc(userId)}/two-factor`);
export const getMediaServerSettings = (): Promise<MediaServerSettings> => requestJson('/api/admin/media-server');
export const updateMediaServerSettings = (payload: MediaServerSettingsUpdate): Promise<MediaServerSettings> => put('/api/admin/media-server', payload);
export const runTranscodeDiagnostics = (): Promise<TranscodeDiagnostics> => post('/api/admin/media-server/transcode-diagnostics', undefined, { timeoutMs: 60000 });

export const listSubtitleTracks = (itemId: string): Promise<SubtitleTrack[]> => requestJson(`/api/library/${enc(itemId)}/subtitle-tracks`, undefined, BACKGROUND);
export type SubtitleJobRequest = { kind: 'generate' } | { kind: 'sync'; trackId: string } | { kind: 'translate'; trackId: string; targetLanguage: string };
export function requestSubtitleJob(itemId: string, request: SubtitleJobRequest): Promise<EnrichmentJob> {
  const base = `/api/library/${enc(itemId)}/subtitle-tracks`;
  if (request.kind === 'generate') return post(`${base}/generate`);
  if (request.kind === 'sync') return post(`${base}/${enc(request.trackId)}/sync`);
  return post(`${base}/${enc(request.trackId)}/translate`, { target_language: request.targetLanguage } satisfies SubtitleTranslateRequest);
}
export const getEnrichmentJob = (jobId: string): Promise<EnrichmentJob> => requestJson(`/api/enrichment/jobs/${enc(jobId)}`);
export const getMediaSegments = (itemId: string): Promise<MediaSegmentList> => requestJson(`/api/library/${enc(itemId)}/segments`, undefined, BACKGROUND);
export const getMuteRanges = (itemId: string): Promise<MuteRange[]> => requestJson(`/api/library/${enc(itemId)}/mute-ranges`, undefined, BACKGROUND);
export const confirmImport = (runId: string): Promise<ImportRun> => post(`${IMPORTS}/${enc(runId)}/confirm`);

// ── 2.1.0 metadata editor ──
const META = '/api/metadata';
export const getTitleMetadata = (titleId: string): Promise<TitleMetadataDoc> => requestJson(`/api/titles/${enc(titleId)}/metadata`);
/** Resolves the all-conflicts 409 with its EditResult body; rejects 422 with ApiRequestError whose body is a MetadataErrorBody. */
export const saveMetadataEdits = (edits: EditEntry[]): Promise<EditResult> =>
  post<EditResult>(`${META}/edits`, { edits } satisfies EditRequest).catch((error) => {
    if (error instanceof ApiRequestError && error.status === 409 && Array.isArray((error.body as EditResult | null)?.conflicts)) return error.body as EditResult;
    throw error;
  });
export const revertMetadata = (titleId: string, fields: string[]): Promise<RevertResult> => post(`/api/titles/${enc(titleId)}/metadata/revert`, { fields } satisfies RevertRequest);
export const getMetadataHistory = (titleId: string, cursor?: string | null, limit?: number): Promise<HistoryPage> => requestJson(`/api/titles/${enc(titleId)}/metadata/history${qs({ cursor, limit })}`);
export const undoMetadataBatch = (batchId: string): Promise<UndoResult> => post(`${META}/batches/${enc(batchId)}/undo`);
export const previewMetadataRefresh = (titleId: string): Promise<RefreshPreview> => post(`/api/titles/${enc(titleId)}/metadata/refresh-preview`, undefined, { timeoutMs: 30000 });
export const listMetadataEpisodes = (seriesId: string, seasonId: string): Promise<EpisodeTable> => requestJson(`/api/titles/${enc(seriesId)}/metadata/episodes${qs({ season_id: seasonId })}`);
export const getMetadataVocabulary = (field: VocabularyField): Promise<VocabularyEntry[]> => requestJson(`${META}/vocabulary${qs({ field })}`);
export const searchMetadataPeople = (q: string, limit?: number): Promise<PersonSuggestion[]> => requestJson(`${META}/people${qs({ q, limit })}`);
export const listEpisodeGroups = (seriesId: string): Promise<EpisodeGroup[]> => requestJson(`/api/titles/${enc(seriesId)}/metadata/episode-groups`);
// ── #164 household people editor ──
const person = (personId: string) => `${META}/people/${enc(personId)}`;
export const getPerson = (personId: string): Promise<PersonDoc> => requestJson(person(personId));
/** null goes back to the name TMDB or the NFO files give. */
export const renamePerson = (personId: string, name: string | null): Promise<PersonEditResult> => put(person(personId), { name } satisfies PersonNameEdit);
export const uploadPersonPhoto = (personId: string, file: Blob): Promise<PersonEditResult> =>
  requestJson(`${person(personId)}/photo`, { method: 'PUT', body: file, headers: { 'Content-Type': file.type } }, { timeoutMs: 60000 });
export const removePersonPhoto = (personId: string): Promise<PersonEditResult> => del(`${person(personId)}/photo`);

// ── 2.1.0 artwork ──
const slot = (titleId: string, type: EditableImageType, index: number) => `/api/titles/${enc(titleId)}/images/${type}/${index}`;
export const listImageCandidates = (titleId: string, type: EditableImageType, index?: number): Promise<ImageCandidate[]> => requestJson(`/api/titles/${enc(titleId)}/images/${type}/candidates${qs({ index })}`);
export const chooseTitleImage = (titleId: string, type: EditableImageType, index: number, body: ImagePutTmdb): Promise<TitleImageEntry[]> => put(slot(titleId, type, index), body);
/** Sends the file as the raw body with Content-Type = file.type and a 60 s timeout. */
export const uploadTitleImage = (titleId: string, type: EditableImageType, index: number, file: Blob, baseTag: string | null): Promise<TitleImageEntry[]> =>
  requestJson(`${slot(titleId, type, index)}${qs({ base_tag: baseTag })}`, { method: 'PUT', body: file, headers: { 'Content-Type': file.type } }, { timeoutMs: 60000 });
export const removeTitleImage = (titleId: string, type: EditableImageType, index: number, baseTag: string | null): Promise<TitleImageEntry[]> => del(`${slot(titleId, type, index)}${qs({ base_tag: baseTag })}`);
export const reorderBackdrops = (titleId: string, tags: string[]): Promise<TitleImageEntry[]> => post(`/api/titles/${enc(titleId)}/images/Backdrop/order`, { tags } satisfies BackdropOrder);

// ── 2.1.0 bulk edit ──
export const bulkEditMetadata = (request: BulkRequest): Promise<BulkResult> => post(`${META}/bulk`, request);

// ── 2.1.0 library automation ──
const AUTOMATION = '/api/admin/library/automation';
export const getLibraryAutomation = (): Promise<LibraryAutomation> => requestJson(AUTOMATION);
export const updateRootAutomation = (rootId: string, payload: AutomationRootPatch): Promise<AutomationRoot> => patch(`${AUTOMATION}/roots/${enc(rootId)}`, payload);
export const setNightHour = (night_hour: number): Promise<LibraryAutomation> => patch(AUTOMATION, { night_hour });
export const scanLibraries = (rootId?: string): Promise<ScanLibrariesResult> => post(`${AUTOMATION}/scan`, rootId ? { root_id: rootId } : {});
export const retryAdminTask = (kind: 'download' | 'import', id: string): Promise<{ status: string }> => post(`/api/admin/tasks/${kind}/${enc(id)}/retry`, {});
export const retryAdminDownload = (id: string) => retryAdminTask('download', id);
