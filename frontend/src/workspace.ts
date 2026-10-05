import { startTransition, useCallback, useEffect, useReducer, useRef, useState, type Dispatch } from 'react';

import {
  ApiRequestError,
  connectEvents,
  getBootstrapStatus,
  getHealth,
  getRuntimeHealth,
  getMySettings,
  getSession,
  listAutomations,
  listContinueWatching,
  listJobs,
  listLibrary,
  refreshLibrary,
  updateMySettings,
} from './api';
import {
  acquisitionPlanForSource,
  normalizeOutputFolder,
  outputFolderProblem,
} from './sourcePreferences';
import {
  resolveSidebarPreference,
  sidebarCollapsedFromStorageValue,
  sidebarCollapsedPreferenceFromUiPrefs,
  sidebarCollapsedStorageValue,
  SIDEBAR_COLLAPSED_STORAGE_KEY,
  withSidebarCollapsedPreference,
} from './sidebarModel';
import {
  resolveTheaterModePreference,
  theaterModePreferenceFromUiPrefs,
  withTheaterModePreference,
} from './theaterMode';
import { DEFAULT_OPTIONAL_PROVIDERS, optionalProvidersFromUiPrefs, type OptionalProvider, withStreamingProviders } from './features/streaming/providers';
import {
  autoplayUpNextPreferenceFromUiPrefs,
  withAutoplayUpNextPreference,
} from './autoplayUpNext';
import { storedThemePreference, themePreferenceFromUiPrefs, THEME_UI_PREF_KEY, type ThemePreference } from './theme';
import { captionPrefsFromUiPrefs, DEFAULT_CAPTIONS, type CaptionPrefs } from './features/watch/captionPrefs';
import type { HomeShelfPref } from './features/home/homeShelves';
import type {
  AppEvent,
  AutoSkipPref,
  BootstrapStatus,
  DownloadJob,
  LibraryItem,
  OutputContainer,
  PlaybackProgress,
  PreviewResponse,
  ProfanityPref,
  SourceAutomation,
  UserProfile,
  UserDownloadDefaults,
  RemotePlaybackCachePreferences,
  UserSettings,
} from './types';

export type AuthStage = 'loading' | 'login' | 'setup' | 'ready';
export type CollectionLoadState = 'loading' | 'empty' | 'ready' | 'stale' | 'offline' | 'failed';
export type CollectionLoadProblem = {
  kind: 'timeout' | 'offline' | 'unauthorized' | 'server';
  message: string;
  requiresSignIn: boolean;
  retryOperation: 'load' | 'refresh';
};
export type WorkspaceFormatPreset = 'best' | 'best_1080p' | 'best_editable' | 'audio_only';

const BOOTSTRAP_REQUEST_TIMEOUT_MS = 3500;
export type WorkspacePreferences = {
  outputFolder: string;
  outputContainer: OutputContainer;
  formatPreset: WorkspaceFormatPreset;
  downloadSubtitles: boolean;
  sidebarCollapsed: boolean;
  theaterMode: boolean;
  /** Optional providers the member sees, persisted as `ui_prefs.streaming_providers`. */
  streamingProviders: OptionalProvider[];
  autoplayUpNext: boolean;
  theme: ThemePreference;
  /** Player volume 0–1, persisted as `ui_prefs.player_volume`. */
  playerVolume: number;
  /** Remote playback ceiling in pixels (null = Best available), persisted as `ui_prefs.playback_max_height`. */
  playbackMaxHeight: PlaybackMaxHeight;
  /** Even out loudness in the web player (`ui_prefs.normalize_loudness`, default on). */
  normalizeLoudness: boolean;
  /** Auto-skip intro/credits/recap segments (`ui_prefs.auto_skip`). */
  autoSkip: AutoSkipPref;
  /** Caption size and background (`ui_prefs.captions`, app polish 10.2). */
  captions: CaptionPrefs;
  /** Mute strong language in the web player (`ui_prefs.profanity`). */
  profanity: ProfanityPref;
  /** Home layout, the raw `ui_prefs.home_shelves` value; null = no preference. Read through normalizeHomeShelves. */
  homeShelves: HomeShelfPref[] | null;
  /** Show advanced settings in Settings (`ui_prefs.settings_advanced`, default off). */
  settingsAdvanced: boolean;
};

export const PLAYBACK_MAX_HEIGHTS = [1440, 1080, 720, 480] as const;
export type PlaybackMaxHeight = (typeof PLAYBACK_MAX_HEIGHTS)[number] | null;

function playerVolumeFromUiPrefs(uiPrefs: Record<string, unknown> | undefined): number | undefined {
  const value = uiPrefs?.player_volume;
  return typeof value === 'number' && value >= 0 && value <= 1 ? value : undefined;
}

function playbackMaxHeightFromUiPrefs(uiPrefs: Record<string, unknown> | undefined): PlaybackMaxHeight {
  const value = uiPrefs?.playback_max_height;
  return PLAYBACK_MAX_HEIGHTS.find((height) => height === value) ?? null;
}

export type PlaybackPrefs = Pick<WorkspacePreferences, 'normalizeLoudness' | 'autoSkip' | 'profanity'>;
export const DEFAULT_AUTO_SKIP: AutoSkipPref = { intro: false, credits: false, recap: false };
export const DEFAULT_PROFANITY: ProfanityPref = { enabled: false, words: [] };
export const PROFANITY_MAX_WORDS = 500;
export const PROFANITY_MAX_WORD_CHARS = 40;

const sameShape = (value: unknown, fallback: unknown) => value !== null && value !== undefined && typeof value === typeof fallback && Array.isArray(value) === Array.isArray(fallback);

/** A member preference from `ui_prefs`, or `fallback` when missing or malformed; object values merge key by key. */
export function uiPref<T>(uiPrefs: Record<string, unknown> | null | undefined, key: string, fallback: T): T {
  const value = uiPrefs?.[key];
  if (!sameShape(value, fallback)) return fallback;
  if (typeof fallback !== 'object' || fallback === null || Array.isArray(fallback)) return value as T;
  const stored = value as Record<string, unknown>;
  return Object.fromEntries(Object.entries(fallback).map(([name, standard]) => [name, sameShape(stored[name], standard) ? stored[name] : standard])) as T;
}

function profanityFromUiPrefs(uiPrefs: Record<string, unknown> | undefined): ProfanityPref {
  const stored = uiPref(uiPrefs, 'profanity', DEFAULT_PROFANITY);
  return { enabled: stored.enabled, words: stored.words.filter((word): word is string => typeof word === 'string').slice(0, PROFANITY_MAX_WORDS) };
}

/** The raw ui_prefs.home_shelves list; anything else (absent, null, malformed) is no preference. Home normalises it. */
function homeShelvesFromUiPrefs(uiPrefs: Record<string, unknown> | undefined): HomeShelfPref[] | null {
  const value = uiPrefs?.home_shelves;
  return Array.isArray(value) ? (value as HomeShelfPref[]) : null;
}

export type WorkspaceState = {
  health: 'unknown' | 'ok' | 'offline';
  authStage: AuthStage;
  sessionProblem: CollectionLoadProblem | null;
  currentUser: UserProfile | null;
  bootstrapStatus: BootstrapStatus | null;
  jobs: DownloadJob[];
  jobsState: CollectionLoadState;
  jobsProblem: CollectionLoadProblem | null;
  jobsRetrying: boolean;
  jobsNextCursor: string | null;
  jobsLoadingMore: boolean;
  jobsLoadMoreError: string | null;
  library: LibraryItem[];
  /**
   * Page-one snapshot the Home surface reads for its "Recently saved" shelf. It
   * is never head-dropped by infinite scroll, so scrolling deep into the Library
   * window can't make Home present stale rows as the newest.
   */
  libraryRecent: LibraryItem[];
  libraryState: CollectionLoadState;
  libraryProblem: CollectionLoadProblem | null;
  libraryRetrying: boolean;
  libraryNextCursor: string | null;
  libraryLoadingMore: boolean;
  libraryLoadMoreError: string | null;
  /**
   * Bumped whenever the library window is replaced wholesale (initial load,
   * refresh, retry) — never on append or realtime upsert. Surfaces fold it into
   * their infinite-scroll reset key so a refresh restarts progress tracking
   * cleanly, even when it returns the identical rows.
   */
  libraryGeneration: number;
  userSettings: UserSettings | null;
  settingsHydrated: boolean;
  sourceAutomations: SourceAutomation[];
  selectedLibraryId: string | null;
  selectedJobId: string | null;
  continueWatching: PlaybackProgress[];
  realtimePreview: PreviewResponse | null;
  preferences: WorkspacePreferences;
};

export function workspaceDownloadDefaults(preferences: WorkspacePreferences): UserDownloadDefaults {
  const plan = acquisitionPlanForSource({
    formatPreset: preferences.formatPreset,
    outputContainer: preferences.outputContainer,
    downloadSubtitles: preferences.downloadSubtitles,
    outputFolder: preferences.outputFolder,
  });
  return {
    format_selection: plan.formatSelection,
    output_profile: plan.outputProfile,
  };
}

export function workspaceSettingsUpdate(
  state: Pick<WorkspaceState, 'preferences' | 'userSettings'>,
) {
  const preferences = {
    ...state.preferences,
    outputFolder: normalizeOutputFolder(state.preferences.outputFolder),
  };
  return {
    download_defaults: workspaceDownloadDefaults(preferences),
    ui_prefs: {
      ...withAutoplayUpNextPreference(
        withTheaterModePreference(
          withStreamingProviders(
            withSidebarCollapsedPreference(state.userSettings?.ui_prefs, preferences.sidebarCollapsed),
            preferences.streamingProviders,
          ),
          preferences.theaterMode,
        ),
        preferences.autoplayUpNext,
      ),
      [THEME_UI_PREF_KEY]: preferences.theme,
      player_volume: preferences.playerVolume,
      playback_max_height: preferences.playbackMaxHeight,
      normalize_loudness: preferences.normalizeLoudness,
      auto_skip: preferences.autoSkip,
      captions: preferences.captions,
      profanity: preferences.profanity,
      home_shelves: preferences.homeShelves,
      settings_advanced: preferences.settingsAdvanced,
    },
  };
}

/** The autosave body: only the ui_prefs keys this browser changed, so an unchanged key (another browser's Home layout) is never written back over the server's newer value. The server merges ui_prefs key by key. */
export function workspaceSettingsChanges(state: Pick<WorkspaceState, 'preferences' | 'userSettings'>) {
  const update = workspaceSettingsUpdate(state);
  const saved = (state.userSettings?.ui_prefs ?? {}) as Record<string, unknown>;
  const ui_prefs = Object.fromEntries(Object.entries(update.ui_prefs).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(saved[key])));
  return { ...update, ui_prefs };
}

type HealthPayload = Awaited<ReturnType<typeof getHealth>>;

export type WorkspaceAction =
  | { type: 'health/resolved'; health: HealthPayload }
  | { type: 'health/online' }
  | { type: 'health/offline' }
  | { type: 'public/resolved'; bootstrapStatus: BootstrapStatus; preserveAuthStage?: boolean }
  | {
      type: 'authenticated/resolved';
      currentUser: UserProfile;
      jobs: DownloadJob[] | null;
      jobsProblem: CollectionLoadProblem | null;
      jobsNextCursor?: string | null;
      library: LibraryItem[] | null;
      libraryProblem: CollectionLoadProblem | null;
      libraryNextCursor?: string | null;
      userSettings: UserSettings | null;
      sourceAutomations: SourceAutomation[];
      continueWatching?: PlaybackProgress[] | null;
    }
  | { type: 'authenticated/reset' }
  | { type: 'session/retrying' }
  | { type: 'session/loadFailed'; problem: CollectionLoadProblem }
  | { type: 'currentUser/replace'; user: UserProfile }
  | { type: 'jobs/upsert'; job: Partial<DownloadJob> & { id: string }; select?: boolean }
  | { type: 'jobs/replace'; jobs: DownloadJob[]; nextCursor?: string | null }
  | { type: 'jobs/retrying' }
  | { type: 'jobs/loadSucceeded'; jobs: DownloadJob[]; nextCursor?: string | null }
  | { type: 'jobs/loadFailed'; problem: CollectionLoadProblem }
  | { type: 'jobs/removeCompleted' }
  | { type: 'jobs/pageLoading' }
  | { type: 'jobs/pageAppended'; jobs: DownloadJob[]; nextCursor: string | null }
  | { type: 'jobs/pageFailed'; message: string }
  | { type: 'library/upsert'; item: Partial<LibraryItem> & { id: string }; select?: boolean }
  | { type: 'library/replace'; library: LibraryItem[]; nextCursor?: string | null }
  | { type: 'library/retrying' }
  | { type: 'library/loadSucceeded'; library: LibraryItem[]; nextCursor?: string | null }
  | { type: 'library/loadFailed'; problem: CollectionLoadProblem }
  | { type: 'library/pageLoading' }
  | { type: 'library/pageAppended'; items: LibraryItem[]; nextCursor: string | null }
  | { type: 'library/pageFailed'; message: string }
  | { type: 'selection/library'; id: string | null }
  | { type: 'selection/job'; id: string | null }
  | { type: 'continueWatching/checkpoint'; progress: PlaybackProgress }
  | { type: 'continueWatching/remove'; itemId: string }
  | { type: 'settings/replace'; settings: UserSettings | null }
  | { type: 'settings/remotePlaybackCache'; value: RemotePlaybackCachePreferences }
  | { type: 'automations/replace'; automations: SourceAutomation[] }
  | { type: 'automations/upsert'; automation: SourceAutomation }
  | { type: 'automations/remove'; id: string }
  | { type: 'preferences/patch'; patch: Partial<WorkspacePreferences> }
  | { type: 'realtime/disconnected'; problem: CollectionLoadProblem }
  | { type: 'realtime/event'; event: AppEvent }
  | { type: 'realtime/eventsBatch'; events: AppEvent[] }
  | { type: 'realtime/previewConsumed' };

function initialSidebarCollapsed(): boolean {
  let localFallback: boolean | undefined;
  try {
    const storage = typeof window === 'undefined' ? undefined : window.localStorage;
    if (typeof storage?.getItem === 'function') localFallback = sidebarCollapsedFromStorageValue(storage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY));
  } catch {
    // Storage denied (e.g. private browsing): fall through to the server preference.
  }
  return resolveSidebarPreference({ localFallback }).collapsed;
}

export function createInitialWorkspaceState(): WorkspaceState {
  return {
    health: 'unknown',
    authStage: 'loading',
    sessionProblem: null,
    currentUser: null,
    bootstrapStatus: null,
    jobs: [],
    jobsState: 'loading',
    jobsProblem: null,
    jobsRetrying: false,
    jobsNextCursor: null,
    jobsLoadingMore: false,
    jobsLoadMoreError: null,
    library: [],
    libraryRecent: [],
    libraryState: 'loading',
    libraryProblem: null,
    libraryRetrying: false,
    libraryNextCursor: null,
    libraryLoadingMore: false,
    libraryLoadMoreError: null,
    libraryGeneration: 0,
    userSettings: null,
    settingsHydrated: false,
    sourceAutomations: [],
    selectedLibraryId: null,
    selectedJobId: null,
    continueWatching: [],
    realtimePreview: null,
    preferences: {
      outputFolder: '',
      outputContainer: 'mp4',
      formatPreset: 'best',
      downloadSubtitles: true,
      sidebarCollapsed: initialSidebarCollapsed(),
      theaterMode: false,
      streamingProviders: [...DEFAULT_OPTIONAL_PROVIDERS],
      autoplayUpNext: true,
      theme: storedThemePreference(),
      playerVolume: 1,
      playbackMaxHeight: null,
      normalizeLoudness: true,
      autoSkip: DEFAULT_AUTO_SKIP,
      captions: DEFAULT_CAPTIONS,
      profanity: DEFAULT_PROFANITY,
      homeShelves: null,
      settingsAdvanced: false,
    },
  };
}

function formatPresetFromSettings(value: unknown): WorkspaceFormatPreset {
  return value === 'best_1080p' || value === 'best_editable' || value === 'audio_only' ? value : 'best';
}

function outputContainerFromSettings(value: unknown): OutputContainer {
  return value === 'webm' || value === 'mkv' ? value : 'mp4';
}

function firstAvailableId(items: LibraryItem[]): string | null {
  return items.find((item) => item.status !== 'missing')?.id || null;
}

function upsert<T extends { id: string }>(current: T[], next: Partial<T> & { id: string }): T[] {
  const index = current.findIndex((entry) => entry.id === next.id);
  const merged = { ...(current[index] || {}), ...next } as T;
  return index === -1 ? [merged, ...current] : [...current.slice(0, index), merged, ...current.slice(index + 1)];
}

/** First page size the UI requests for each collection. Kept below the backend clamp of 100. */
export const LIBRARY_PAGE_SIZE = 60;
export const JOBS_PAGE_SIZE = 50;
/**
 * The library grid keeps at most five pages (300 rows) mounted. Appending past
 * this drops from the head so the DOM stays bounded on any library tier; a head
 * drop trims the top of the list, so returning to the newest rows is a Refresh.
 */
export const MAX_LIBRARY_WINDOW_ITEMS = 300;

function appendUniqueById<T extends { id: string }>(current: T[], incoming: T[]): T[] {
  const seen = new Set(current.map((entry) => entry.id));
  const merged = [...current];
  for (const entry of incoming) {
    if (seen.has(entry.id)) continue;
    seen.add(entry.id);
    merged.push(entry);
  }
  return merged;
}

/**
 * Ranks two rows in the backend's library ordering: downloaded_at DESC NULLS
 * LAST, created_at DESC, id DESC. Returns a negative number when `a` ranks
 * ahead of (sorts before) `b`.
 */
function compareRecencyDesc(a: string | null | undefined, b: string | null | undefined): number {
  const left = a ?? null;
  const right = b ?? null;
  if (left === right) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return left < right ? 1 : -1;
}

export function compareLibraryOrder(a: LibraryItem, b: LibraryItem): number {
  const byDownloaded = compareRecencyDesc(a.downloaded_at, b.downloaded_at);
  if (byDownloaded !== 0) return byDownloaded;
  const byCreated = compareRecencyDesc(a.created_at, b.created_at);
  if (byCreated !== 0) return byCreated;
  if (a.id === b.id) return 0;
  return a.id < b.id ? 1 : -1;
}

/**
 * Ranks two Download jobs in the backend's paged Downloads-feed ordering:
 * created_at DESC, id DESC. Returns a negative number when `a` ranks ahead of
 * (sorts before) `b`.
 */
export function compareJobOrder(a: DownloadJob, b: DownloadJob): number {
  const byCreated = compareRecencyDesc(a.created_at, b.created_at);
  if (byCreated !== 0) return byCreated;
  if (a.id === b.id) return 0;
  return a.id < b.id ? 1 : -1;
}

/**
 * Applies the partial-window reconciliation rule to the paged Downloads feed:
 * update a job already present in place; prepend a job that sorts at or before
 * the head (a freshly queued job, or any job when the feed is empty); otherwise
 * return the same reference because the job lives beyond the loaded window and
 * a later page will carry it — prepending it would surface an older running job
 * above newer rows.
 */
function reconcileJobsWindow(list: DownloadJob[], job: Partial<DownloadJob> & { id: string }): DownloadJob[] {
  const index = list.findIndex((entry) => entry.id === job.id);
  if (index !== -1) {
    const merged = { ...list[index], ...job } as DownloadJob;
    return [...list.slice(0, index), merged, ...list.slice(index + 1)];
  }
  const head = list[0];
  if (head && compareJobOrder(job as DownloadJob, head) > 0) return list;
  return [{ ...job } as DownloadJob, ...list];
}

/**
 * Applies the partial-window reconciliation rule to one ordered list: update an
 * item already present at its ordered position (a re-download refreshes
 * downloaded_at, so the merged row re-sorts within the window instead of
 * staying mid-window until refresh); prepend a row that sorts at or before the
 * head (a fresh download, or any row when the list is empty) and re-bound so
 * the list never grows past `bound`; otherwise return the same reference
 * because the row lives beyond the loaded window and a later page will carry
 * it. Returning the same reference lets callers detect a no-op without a deep
 * compare.
 */
function reconcileOrderedWindow(
  list: LibraryItem[],
  item: Partial<LibraryItem> & { id: string },
  bound: number,
): LibraryItem[] {
  const index = list.findIndex((entry) => entry.id === item.id);
  if (index !== -1) {
    const merged = { ...list[index], ...item } as LibraryItem;
    const rest = [...list.slice(0, index), ...list.slice(index + 1)];
    const sortedIndex = rest.findIndex((entry) => compareLibraryOrder(merged, entry) < 0);
    const at = sortedIndex === -1 ? rest.length : sortedIndex;
    return [...rest.slice(0, at), merged, ...rest.slice(at)];
  }
  const head = list[0];
  if (head && compareLibraryOrder(item as LibraryItem, head) > 0) return list;
  const prepended = [{ ...item } as LibraryItem, ...list];
  return prepended.length > bound ? prepended.slice(0, bound) : prepended;
}

/**
 * Reconciles a realtime `library_item_upserted` against both the scroll window
 * (bounded to {@link MAX_LIBRARY_WINDOW_ITEMS}) and the page-one Home snapshot
 * (bounded to {@link LIBRARY_PAGE_SIZE}). Avoids full-library refetch storms on
 * every upsert while keeping Home's newest-first shelf correct.
 */
function reconcileLibraryUpsert(state: WorkspaceState, item: Partial<LibraryItem> & { id: string }): WorkspaceState {
  const library = reconcileOrderedWindow(state.library, item, MAX_LIBRARY_WINDOW_ITEMS);
  const libraryRecent = reconcileOrderedWindow(state.libraryRecent, item, LIBRARY_PAGE_SIZE);
  if (library === state.library && libraryRecent === state.libraryRecent) return state;
  const selectedLibraryId = item.status === 'missing' && state.selectedLibraryId === item.id
    ? firstAvailableId(library)
    : state.selectedLibraryId || firstAvailableId(library);
  return {
    ...state,
    library,
    libraryRecent,
    libraryState: state.libraryState === 'ready' || state.libraryState === 'empty' ? successfulLibraryState(library) : 'stale',
    selectedLibraryId,
  };
}

function successfulCollectionState(items: unknown[]): CollectionLoadState {
  return items.length ? 'ready' : 'empty';
}

function successfulLibraryState(items: LibraryItem[]): CollectionLoadState {
  return items.some((item) => item.status !== 'missing') ? 'ready' : 'empty';
}

function availableLibraryCount(items: LibraryItem[]): number {
  return items.filter((item) => item.status !== 'missing').length;
}

function failedCollectionState(itemCount: number, problem: CollectionLoadProblem): CollectionLoadState {
  if (itemCount > 0) return 'stale';
  return problem.kind === 'offline' || problem.kind === 'timeout' ? 'offline' : 'failed';
}

export function classifyCollectionLoadProblem(error: unknown, retryOperation: 'load' | 'refresh' = 'load'): CollectionLoadProblem {
  if (error instanceof ApiRequestError && error.status === 401) {
    return {
      kind: 'unauthorized',
      message: 'Your session expired before this collection could load. Sign in again to continue.',
      requiresSignIn: true,
      retryOperation,
    };
  }
  if (error instanceof ApiRequestError && error.status === 0) {
    return {
      kind: 'timeout',
      message: 'Lumina timed out while loading this collection. Your saved data has not been replaced.',
      requiresSignIn: false,
      retryOperation,
    };
  }
  if (error instanceof TypeError) {
    return {
      kind: 'offline',
      message: 'Lumina could not reach the vault. Check the connection and try again.',
      requiresSignIn: false,
      retryOperation,
    };
  }
  return {
    kind: 'server',
    message: 'The vault could not load this collection. Existing data is being kept where available.',
    requiresSignIn: false,
    retryOperation,
  };
}

export function classifySessionLoadProblem(error: unknown): CollectionLoadProblem {
  const problem = classifyCollectionLoadProblem(error);
  if (problem.kind === 'timeout') {
    return { ...problem, message: 'Lumina did not hear back from the vault while restoring your session. Try opening it again.' };
  }
  if (problem.kind === 'offline') {
    return { ...problem, message: 'Lumina could not reach the vault to restore your session. Check the connection and try again.' };
  }
  return { ...problem, message: 'Lumina could not restore your session. Try opening the vault again.' };
}

export type CollectionResource = 'jobs' | 'library';
export type CollectionRequestToken = { generation: number; request: number; resource: CollectionResource };

export class CollectionRequestGate {
  private generation = 0;
  private requests: Record<CollectionResource, number> = { jobs: 0, library: 0 };

  beginSession(): number {
    this.generation += 1;
    return this.generation;
  }

  invalidateSession(): void {
    this.generation += 1;
  }

  isSessionCurrent(generation: number): boolean {
    return generation === this.generation;
  }

  currentSession(): number {
    return this.generation;
  }

  begin(resource: CollectionResource): CollectionRequestToken {
    const request = this.requests[resource] + 1;
    this.requests[resource] = request;
    return { generation: this.generation, request, resource };
  }

  invalidate(resource: CollectionResource): void {
    this.requests[resource] += 1;
  }

  isCurrent(token: CollectionRequestToken): boolean {
    return token.generation === this.generation && token.request === this.requests[token.resource];
  }
}

export function workspaceReducer(state: WorkspaceState, action: WorkspaceAction): WorkspaceState {
  switch (action.type) {
    case 'health/resolved':
      return {
        ...state,
        health: 'ok', // a degraded server is still reachable
        preferences: state.preferences,
      };
    case 'health/offline':
      return { ...state, health: 'offline' };
    case 'health/online':
      return { ...state, health: 'ok' };
    case 'public/resolved':
      return {
        ...state,
        bootstrapStatus: action.bootstrapStatus,
        authStage: action.preserveAuthStage ? state.authStage : action.bootstrapStatus.needs_setup ? 'setup' : 'login',
      };
    case 'authenticated/resolved': {
      const settings = action.userSettings;
      const sameUser = state.currentUser?.id === action.currentUser.id;
      const previousJobs = sameUser ? state.jobs : [];
      const previousLibrary = sameUser ? state.library : [];
      const previousLibraryRecent = sameUser ? state.libraryRecent : [];
      const previousContinueWatching = sameUser ? state.continueWatching : [];
      const defaults = settings?.resolved_download_defaults;
      const savedOutputFolder = defaults?.output_profile?.subdir;
      const fallbackOutputFolder = !sameUser || outputFolderProblem(state.preferences.outputFolder)
        ? ''
        : normalizeOutputFolder(state.preferences.outputFolder);
      const outputFolder = typeof savedOutputFolder === 'string' && !outputFolderProblem(savedOutputFolder)
        ? normalizeOutputFolder(savedOutputFolder)
        : fallbackOutputFolder;
      return {
        ...state,
        authStage: 'ready',
        sessionProblem: null,
        currentUser: action.currentUser,
        jobs: action.jobs ?? previousJobs,
        jobsState: action.jobs
          ? successfulCollectionState(action.jobs)
          : failedCollectionState(previousJobs.length, action.jobsProblem || classifyCollectionLoadProblem(null)),
        jobsProblem: action.jobs ? null : action.jobsProblem,
        jobsRetrying: false,
        jobsNextCursor: action.jobs ? action.jobsNextCursor ?? null : state.jobsNextCursor,
        jobsLoadingMore: false,
        jobsLoadMoreError: null,
        library: action.library ?? previousLibrary,
        libraryRecent: action.library ?? previousLibraryRecent,
        libraryGeneration: action.library ? state.libraryGeneration + 1 : state.libraryGeneration,
        libraryState: action.library
          ? successfulLibraryState(action.library)
          : failedCollectionState(availableLibraryCount(previousLibrary), action.libraryProblem || classifyCollectionLoadProblem(null)),
        libraryProblem: action.library ? null : action.libraryProblem,
        libraryRetrying: false,
        libraryNextCursor: action.library ? action.libraryNextCursor ?? null : state.libraryNextCursor,
        libraryLoadingMore: false,
        libraryLoadMoreError: null,
        selectedLibraryId: sameUser ? state.selectedLibraryId || (action.library ? firstAvailableId(action.library) : null) : action.library ? firstAvailableId(action.library) : null,
        continueWatching: action.continueWatching ?? previousContinueWatching,
        userSettings: settings,
        settingsHydrated: settings !== null,
        sourceAutomations: action.sourceAutomations,
        preferences: {
          ...state.preferences,
          formatPreset: formatPresetFromSettings(defaults?.format_selection?.preset),
          outputContainer: outputContainerFromSettings(defaults?.format_selection?.output_container),
          downloadSubtitles: defaults?.format_selection?.subtitles !== false,
          outputFolder,
          sidebarCollapsed:
            resolveSidebarPreference({
              memberPreference: sidebarCollapsedPreferenceFromUiPrefs(settings?.ui_prefs),
              memberSettingsAvailable: settings !== null,
              localFallback: state.preferences.sidebarCollapsed,
            }).collapsed,
          theaterMode: resolveTheaterModePreference({
            memberPreference: theaterModePreferenceFromUiPrefs(settings?.ui_prefs),
            memberSettingsAvailable: settings !== null,
            localFallback: state.preferences.theaterMode,
          }).theaterMode,
          streamingProviders: optionalProvidersFromUiPrefs(settings?.ui_prefs) ?? (settings !== null ? [...DEFAULT_OPTIONAL_PROVIDERS] : state.preferences.streamingProviders),
          autoplayUpNext: autoplayUpNextPreferenceFromUiPrefs(settings?.ui_prefs)
            ?? (settings !== null ? true : state.preferences.autoplayUpNext),
          theme: themePreferenceFromUiPrefs(settings?.ui_prefs) ?? state.preferences.theme,
          playerVolume: playerVolumeFromUiPrefs(settings?.ui_prefs) ?? state.preferences.playerVolume,
          playbackMaxHeight: settings ? playbackMaxHeightFromUiPrefs(settings.ui_prefs) : state.preferences.playbackMaxHeight,
          normalizeLoudness: settings ? uiPref(settings.ui_prefs, 'normalize_loudness', true) : state.preferences.normalizeLoudness,
          autoSkip: settings ? uiPref(settings.ui_prefs, 'auto_skip', DEFAULT_AUTO_SKIP) : state.preferences.autoSkip,
          captions: settings ? captionPrefsFromUiPrefs(settings.ui_prefs) : state.preferences.captions,
          profanity: settings ? profanityFromUiPrefs(settings.ui_prefs) : state.preferences.profanity,
          // Home layout: the member's own, or nothing; never the previous member's on this browser.
          homeShelves: settings ? homeShelvesFromUiPrefs(settings.ui_prefs) : sameUser ? state.preferences.homeShelves : null,
          settingsAdvanced: settings ? uiPref(settings.ui_prefs, 'settings_advanced', false) : sameUser && state.preferences.settingsAdvanced,
        },
      };
    }
    case 'authenticated/reset':
      return {
        ...createInitialWorkspaceState(),
        health: state.health,
        bootstrapStatus: state.bootstrapStatus,
        preferences: {
          ...createInitialWorkspaceState().preferences,
          outputFolder: state.preferences.outputFolder,
          sidebarCollapsed: state.preferences.sidebarCollapsed,
        },
      };
    case 'session/retrying':
      return state;
    case 'session/loadFailed':
      return { ...state, sessionProblem: action.problem };
    case 'currentUser/replace':
      return { ...state, currentUser: action.user };
    case 'jobs/upsert': {
      const jobs = reconcileJobsWindow(state.jobs, action.job);
      if (jobs === state.jobs) {
        return action.select ? { ...state, selectedJobId: action.job.id } : state;
      }
      return {
        ...state,
        jobs,
        jobsState: state.jobsState === 'ready' || state.jobsState === 'empty' ? 'ready' : 'stale',
        selectedJobId: action.select ? action.job.id : state.selectedJobId || action.job.id,
      };
    }
    case 'jobs/replace':
    case 'jobs/loadSucceeded':
      return {
        ...state,
        jobs: action.jobs,
        jobsState: successfulCollectionState(action.jobs),
        jobsProblem: null,
        jobsRetrying: false,
        jobsNextCursor: action.nextCursor ?? null,
        jobsLoadingMore: false,
        jobsLoadMoreError: null,
      };
    case 'jobs/pageLoading':
      return { ...state, jobsLoadingMore: true, jobsLoadMoreError: null };
    case 'jobs/pageAppended': {
      const jobs = appendUniqueById(state.jobs, action.jobs);
      return {
        ...state,
        jobs,
        jobsState: state.jobsState === 'ready' || state.jobsState === 'empty' ? successfulCollectionState(jobs) : state.jobsState,
        jobsNextCursor: action.nextCursor,
        jobsLoadingMore: false,
        jobsLoadMoreError: null,
      };
    }
    case 'jobs/pageFailed':
      return { ...state, jobsLoadingMore: false, jobsLoadMoreError: action.message };
    case 'jobs/retrying':
      return { ...state, jobsRetrying: true };
    case 'jobs/loadFailed':
      return {
        ...state,
        jobsState: failedCollectionState(state.jobs.length, action.problem),
        jobsProblem: action.problem,
        jobsRetrying: false,
      };
    case 'jobs/removeCompleted': {
      const completedIds = new Set(state.jobs.filter((job) => job.status === 'completed').map((job) => job.id));
      const jobs = state.jobs.filter((job) => job.status !== 'completed');
      return {
        ...state,
        jobs,
        jobsState: state.jobsState === 'ready' || state.jobsState === 'empty' ? successfulCollectionState(jobs) : state.jobsState,
        selectedJobId: state.selectedJobId && completedIds.has(state.selectedJobId) ? null : state.selectedJobId,
      };
    }
    case 'library/upsert': {
      const reconciled = reconcileLibraryUpsert(state, action.item);
      return action.select ? { ...reconciled, selectedLibraryId: action.item.id } : reconciled;
    }
    case 'library/replace': {
      const visible = action.library.filter((item) => item.status !== 'missing');
      const selectedLibraryId =
        state.selectedLibraryId && visible.some((item) => item.id === state.selectedLibraryId)
          ? state.selectedLibraryId
          : visible[0]?.id || null;
      return {
        ...state,
        library: action.library,
        libraryRecent: action.library,
        libraryGeneration: state.libraryGeneration + 1,
        libraryState: successfulLibraryState(action.library),
        libraryProblem: null,
        libraryRetrying: false,
        libraryNextCursor: action.nextCursor ?? null,
        libraryLoadingMore: false,
        libraryLoadMoreError: null,
        selectedLibraryId,
      };
    }
    case 'library/loadSucceeded':
      return workspaceReducer(state, { type: 'library/replace', library: action.library, nextCursor: action.nextCursor });
    case 'library/pageLoading':
      return { ...state, libraryLoadingMore: true, libraryLoadMoreError: null };
    case 'library/pageAppended': {
      const merged = appendUniqueById(state.library, action.items);
      const library = merged.length > MAX_LIBRARY_WINDOW_ITEMS ? merged.slice(merged.length - MAX_LIBRARY_WINDOW_ITEMS) : merged;
      // A head-drop can trim the selected row out of the mounted window; move
      // the grid highlight to the first available row instead of dangling.
      const selectedLibraryId = state.selectedLibraryId && !library.some((item) => item.id === state.selectedLibraryId)
        ? firstAvailableId(library)
        : state.selectedLibraryId;
      return {
        ...state,
        library,
        selectedLibraryId,
        libraryState: state.libraryState === 'ready' || state.libraryState === 'empty' ? successfulLibraryState(library) : state.libraryState,
        libraryNextCursor: action.nextCursor,
        libraryLoadingMore: false,
        libraryLoadMoreError: null,
      };
    }
    case 'library/pageFailed':
      return { ...state, libraryLoadingMore: false, libraryLoadMoreError: action.message };
    case 'library/retrying':
      return { ...state, libraryRetrying: true };
    case 'library/loadFailed':
      return {
        ...state,
        libraryState: failedCollectionState(availableLibraryCount(state.library), action.problem),
        libraryProblem: action.problem,
        libraryRetrying: false,
      };
    case 'selection/library':
      return { ...state, selectedLibraryId: action.id };
    case 'selection/job':
      return { ...state, selectedJobId: action.id };
    case 'continueWatching/checkpoint': {
      const incoming = action.progress;
      const previous = state.continueWatching.find((entry) => entry.item_id === incoming.item_id);
      // A checkpoint PUT for a title-less entry (e.g. autoplay's next episode) must not blank the hero's title.
      const progress = incoming.title ? incoming : { ...incoming, title: previous?.title ?? incoming.title };
      const without = state.continueWatching.filter((entry) => entry.item_id !== progress.item_id);
      return {
        ...state,
        continueWatching: progress.completed || progress.position_seconds <= 0 ? without : [progress, ...without],
      };
    }
    case 'continueWatching/remove':
      return { ...state, continueWatching: state.continueWatching.filter((entry) => entry.item_id !== action.itemId) };
    case 'settings/replace':
      return { ...state, userSettings: action.settings, settingsHydrated: action.settings !== null };
    case 'settings/remotePlaybackCache':
      return state.userSettings ? {
        ...state,
        userSettings: { ...state.userSettings, remote_playback_cache: action.value },
      } : state;
    case 'automations/replace':
      return { ...state, sourceAutomations: action.automations };
    case 'automations/upsert':
      return { ...state, sourceAutomations: upsert(state.sourceAutomations, action.automation) };
    case 'automations/remove':
      return { ...state, sourceAutomations: state.sourceAutomations.filter((entry) => entry.id !== action.id) };
    case 'preferences/patch':
      return { ...state, preferences: { ...state.preferences, ...action.patch } };
    case 'realtime/disconnected':
      return workspaceReducer(
        workspaceReducer({ ...state, health: 'offline' }, { type: 'jobs/loadFailed', problem: action.problem }),
        { type: 'library/loadFailed', problem: action.problem },
      );
    case 'realtime/event':
      return reconcileWorkspaceEvent(state, action.event);
    case 'realtime/eventsBatch':
      return action.events.reduce(reconcileWorkspaceEvent, state);
    case 'realtime/previewConsumed':
      return { ...state, realtimePreview: null };
    default:
      return state;
  }
}

export function reconcileWorkspaceEvent(state: WorkspaceState, event: AppEvent): WorkspaceState {
  if (!state.currentUser) return state;
  const envelope = event.payload && typeof event.payload === 'object' ? (event.payload as { user_id?: unknown; broadcast?: unknown }) : null;
  const eventUserId = envelope?.user_id;
  const isBroadcast = envelope?.broadcast === true;
  if (eventUserId !== state.currentUser.id && !isBroadcast) {
    return state;
  }
  switch (event.type) {
    case 'preview_ready':
      return { ...state, realtimePreview: event.payload as PreviewResponse };
    case 'job_queued':
    case 'job_started':
    case 'job_progress':
    case 'job_postprocess':
    case 'job_completed':
    case 'job_failed': {
      const job = (event.payload as { job?: DownloadJob }).job;
      return job?.id ? workspaceReducer(state, { type: 'jobs/upsert', job }) : state;
    }
    case 'library_item_upserted':
    case 'library_item_missing': {
      const item = (event.payload as { item?: LibraryItem }).item;
      return item?.id ? workspaceReducer(state, { type: 'library/upsert', item }) : state;
    }
    case 'settings_updated': {
      const settings = (event.payload as { settings?: UserSettings }).settings;
      return settings?.id ? workspaceReducer(state, { type: 'settings/replace', settings }) : state;
    }
    case 'automation_checked':
    case 'automation_failed': {
      const automation = (event.payload as { automation?: SourceAutomation }).automation;
      return automation?.id ? workspaceReducer(state, { type: 'automations/upsert', automation }) : state;
    }
    default:
      return state;
  }
}

/**
 * Realtime progress churn is coalesced into at most one batched dispatch per
 * this interval. Terminal and list-shaped events bypass the buffer and apply
 * immediately, so this only bounds the cost of high-frequency progress redraws.
 */
export const SSE_PROGRESS_COALESCE_INTERVAL_MS = 250;

/** Hints (a library change this member may or may not see) coalesce into one page refetch per this window. */
export const LIBRARY_HINT_REFETCH_MS = 1500;

/** The content-free broadcast a member with library limits receives in place of a shared item's event. */
export const isLibraryHint = (event: AppEvent): boolean => {
  const payload = event.payload && typeof event.payload === 'object' ? (event.payload as { broadcast?: unknown; item?: unknown }) : null;
  return (event.type === 'library_item_upserted' || event.type === 'library_item_missing') && payload?.broadcast === true && payload.item === undefined;
};

const PROGRESS_EVENT_TYPES: ReadonlySet<AppEvent['type']> = new Set(['job_progress', 'job_postprocess']);

function eventJobId(event: AppEvent): string | null {
  const payload = event.payload && typeof event.payload === 'object' ? (event.payload as { job?: { id?: unknown } }) : null;
  const id = payload?.job?.id;
  return typeof id === 'string' && id ? id : null;
}

export type WorkspaceEventCoalescer = {
  /** Buffer a progress event or apply a terminal/list-shaped event immediately. */
  handle: (event: AppEvent) => void;
  /** Apply any buffered progress now and clear the pending timer. */
  flush: () => void;
  /** Drop buffered progress without applying and clear the pending timer. */
  cancel: () => void;
  /** Number of jobs with buffered progress awaiting a flush. */
  pendingCount: () => number;
};

/**
 * Groups realtime events so high-frequency progress redraws collapse into one
 * batched apply per {@link SSE_PROGRESS_COALESCE_INTERVAL_MS}. Terminal and
 * list-shaped events apply immediately; a terminal event for a job first
 * replays that job's buffered progress so a late flush can never overwrite the
 * terminal state. Other jobs' buffered progress is left untouched. The timer is
 * injectable so the coalescing window is deterministic under test.
 */
export function createWorkspaceEventCoalescer(
  apply: (events: AppEvent[]) => void,
  options: { intervalMs?: number; schedule?: (callback: () => void, delayMs: number) => () => void } = {},
): WorkspaceEventCoalescer {
  const intervalMs = options.intervalMs ?? SSE_PROGRESS_COALESCE_INTERVAL_MS;
  const schedule = options.schedule
    ?? ((callback, delayMs) => {
      const handle = setTimeout(callback, delayMs);
      return () => clearTimeout(handle);
    });
  const buffer = new Map<string, AppEvent>();
  let cancelTimer: (() => void) | null = null;

  function flushBuffer() {
    if (buffer.size === 0) return;
    const events = [...buffer.values()];
    buffer.clear();
    apply(events);
  }

  function scheduleFlush() {
    if (cancelTimer) return;
    cancelTimer = schedule(() => {
      cancelTimer = null;
      flushBuffer();
    }, intervalMs);
  }

  function clearTimer() {
    if (cancelTimer) cancelTimer();
    cancelTimer = null;
  }

  return {
    handle(event) {
      const jobId = eventJobId(event);
      if (PROGRESS_EVENT_TYPES.has(event.type)) {
        if (!jobId) {
          apply([event]);
          return;
        }
        buffer.set(jobId, event);
        scheduleFlush();
        return;
      }
      if (jobId && buffer.has(jobId)) {
        const pending = buffer.get(jobId) as AppEvent;
        buffer.delete(jobId);
        apply([pending, event]);
        return;
      }
      apply([event]);
    },
    flush() {
      clearTimer();
      flushBuffer();
    },
    cancel() {
      clearTimer();
      buffer.clear();
    },
    pendingCount() {
      return buffer.size;
    },
  };
}

type WorkspaceHookOptions = {
  onError: (message: string) => void;
};

export type AuthenticatedWorkspace = {
  state: WorkspaceState;
  dispatch: Dispatch<WorkspaceAction>;
  loadAuthenticated: () => Promise<UserProfile>;
  loadPublic: () => Promise<void>;
  retryCollection: (resource: CollectionResource) => Promise<boolean>;
  refreshLibraryCollection: () => Promise<boolean>;
  loadMoreLibrary: () => Promise<boolean>;
  loadMoreJobs: () => Promise<boolean>;
  captureSessionToken: () => number;
  isSessionTokenCurrent: (generation: number) => boolean;
  reset: () => void;
  /** Closes the /api/events stream while held: a member change closes it before the cookie changes. */
  holdEvents: (held: boolean) => void;
};

export function canAutosaveWorkspacePreferences(state: WorkspaceState): boolean {
  return state.authStage === 'ready' && state.currentUser !== null && state.settingsHydrated;
}

function isAuthError(error: unknown): error is ApiRequestError {
  return error instanceof ApiRequestError && error.status === 401;
}

export function useAuthenticatedWorkspace({ onError }: WorkspaceHookOptions): AuthenticatedWorkspace {
  const [state, dispatchBase] = useReducer(workspaceReducer, undefined, createInitialWorkspaceState);
  const settingsSaveTimerRef = useRef<number | null>(null);
  const requestGateRef = useRef(new CollectionRequestGate());
  const errorReporterRef = useRef(onError);
  errorReporterRef.current = onError;
  const stateRef = useRef(state);
  stateRef.current = state;
  const loadMoreGuardRef = useRef<{ jobs: boolean; library: boolean }>({ jobs: false, library: false });
  const [eventsHeld, holdEvents] = useState(false);

  const dispatch = useCallback<Dispatch<WorkspaceAction>>((action) => dispatchBase(action), []);

  const loadPublic = useCallback(async () => {
    const generation = requestGateRef.current.beginSession();
    const [healthResult, bootstrapResult] = await Promise.allSettled([
      getHealth({ timeoutMs: BOOTSTRAP_REQUEST_TIMEOUT_MS }),
      getBootstrapStatus({ timeoutMs: BOOTSTRAP_REQUEST_TIMEOUT_MS }),
    ]);
    if (!requestGateRef.current.isSessionCurrent(generation)) return;
    dispatchBase(healthResult.status === 'fulfilled' ? { type: 'health/resolved', health: healthResult.value } : { type: 'health/offline' });
    dispatchBase({
      type: 'public/resolved',
      bootstrapStatus: bootstrapResult.status === 'fulfilled' ? bootstrapResult.value : { needs_setup: false },
    });
  }, []);

  const loadAuthenticated = useCallback(async (): Promise<UserProfile> => {
    const generation = requestGateRef.current.beginSession();
    const healthPromise = getRuntimeHealth({ timeoutMs: BOOTSTRAP_REQUEST_TIMEOUT_MS });
    const sessionPromise = getSession({ timeoutMs: BOOTSTRAP_REQUEST_TIMEOUT_MS });
    const [healthResult, sessionResult] = await Promise.all([
      healthPromise.catch(() => null),
      sessionPromise,
    ]);
    if (!requestGateRef.current.isSessionCurrent(generation)) return sessionResult.user;
    dispatchBase(healthResult ? { type: 'health/resolved', health: healthResult } : { type: 'health/offline' });
    dispatchBase({
      type: 'public/resolved',
      bootstrapStatus: { needs_setup: false },
      preserveAuthStage: true,
    });

    const hydrationIssues: string[] = [];
    const jobsToken = requestGateRef.current.begin('jobs');
    const libraryToken = requestGateRef.current.begin('library');
    const [jobsResult, libraryResult, settingsResult, automationResult, continueWatchingResult] = await Promise.allSettled([
      listJobs({ limit: JOBS_PAGE_SIZE }),
      listLibrary({ limit: LIBRARY_PAGE_SIZE }),
      getMySettings(),
      listAutomations(),
      listContinueWatching(),
    ]);
    if (
      !requestGateRef.current.isSessionCurrent(generation)
      || !requestGateRef.current.isCurrent(jobsToken)
      || !requestGateRef.current.isCurrent(libraryToken)
    ) return sessionResult.user;
    const jobs = jobsResult.status === 'fulfilled' ? jobsResult.value.items : null;
    const jobsNextCursor = jobsResult.status === 'fulfilled' ? jobsResult.value.next_cursor : null;
    const jobsProblem = jobsResult.status === 'rejected' ? classifyCollectionLoadProblem(jobsResult.reason) : null;
    const library = libraryResult.status === 'fulfilled' ? libraryResult.value.items : null;
    const libraryNextCursor = libraryResult.status === 'fulfilled' ? libraryResult.value.next_cursor : null;
    const libraryProblem = libraryResult.status === 'rejected' ? classifyCollectionLoadProblem(libraryResult.reason) : null;
    const settings = settingsResult.status === 'fulfilled' ? settingsResult.value : null;
    const automations = automationResult.status === 'fulfilled' ? automationResult.value : [];
    // Continue watching is preloaded so the Home shelf paints in its final
    // position without a later insert. A failure is non-critical: fall back to
    // the last-known window for the same member, or an empty shelf otherwise.
    const continueWatching = continueWatchingResult.status === 'fulfilled' ? continueWatchingResult.value : null;
    if (settingsResult.status === 'rejected') hydrationIssues.push('personal settings');
    if (automationResult.status === 'rejected') hydrationIssues.push('automation');
    const preferenceIssues: string[] = [];
    const savedOutputFolder = settings?.resolved_download_defaults.output_profile.subdir;
    if (typeof savedOutputFolder === 'string' && outputFolderProblem(savedOutputFolder)) {
      preferenceIssues.push('an unsafe output folder was reset to the Library root');
    }
    dispatchBase({
      type: 'authenticated/resolved',
      currentUser: sessionResult.user,
      jobs,
      jobsProblem,
      jobsNextCursor,
      library,
      libraryProblem,
      libraryNextCursor,
      userSettings: settings,
      sourceAutomations: automations,
      continueWatching,
    });
    if (hydrationIssues.length > 0) {
      errorReporterRef.current(`Signed in, but some data could not load: ${hydrationIssues.join(', ')}.`);
    }
    if (preferenceIssues.length > 0) {
      errorReporterRef.current(`Some saved acquisition choices need attention: ${preferenceIssues.join('; ')}.`);
    }

    return sessionResult.user;
  }, []);

  const retryCollection = useCallback(async (resource: CollectionResource): Promise<boolean> => {
    const token = requestGateRef.current.begin(resource);
    if (resource === 'jobs') {
      dispatchBase({ type: 'jobs/retrying' });
      try {
        const page = await listJobs({ limit: JOBS_PAGE_SIZE });
        if (!requestGateRef.current.isCurrent(token)) return false;
        dispatchBase({ type: 'jobs/loadSucceeded', jobs: page.items, nextCursor: page.next_cursor });
        return true;
      } catch (error) {
        if (!requestGateRef.current.isCurrent(token)) return false;
        dispatchBase({ type: 'jobs/loadFailed', problem: classifyCollectionLoadProblem(error) });
        return false;
      }
    }
    dispatchBase({ type: 'library/retrying' });
    try {
      const page = await listLibrary({ limit: LIBRARY_PAGE_SIZE });
      if (!requestGateRef.current.isCurrent(token)) return false;
      dispatchBase({ type: 'library/loadSucceeded', library: page.items, nextCursor: page.next_cursor });
      return true;
    } catch (error) {
      if (!requestGateRef.current.isCurrent(token)) return false;
      dispatchBase({ type: 'library/loadFailed', problem: classifyCollectionLoadProblem(error) });
      return false;
    }
  }, []);

  const refreshLibraryCollection = useCallback(async (): Promise<boolean> => {
    const token = requestGateRef.current.begin('library');
    dispatchBase({ type: 'library/retrying' });
    try {
      const page = await refreshLibrary();
      if (!requestGateRef.current.isCurrent(token)) return false;
      dispatchBase({ type: 'library/loadSucceeded', library: page.items, nextCursor: page.next_cursor });
      return true;
    } catch (error) {
      if (!requestGateRef.current.isCurrent(token)) return false;
      dispatchBase({ type: 'library/loadFailed', problem: classifyCollectionLoadProblem(error, 'refresh') });
      return false;
    }
  }, []);

  const loadMoreLibrary = useCallback(async (): Promise<boolean> => {
    const current = stateRef.current;
    const startCursor = current.libraryNextCursor;
    if (!startCursor || current.libraryLoadingMore || loadMoreGuardRef.current.library) return false;
    loadMoreGuardRef.current.library = true;
    const generation = requestGateRef.current.currentSession();
    dispatchBase({ type: 'library/pageLoading' });
    try {
      const page = await listLibrary({ cursor: startCursor, limit: LIBRARY_PAGE_SIZE });
      if (!requestGateRef.current.isSessionCurrent(generation) || stateRef.current.libraryNextCursor !== startCursor) return false;
      dispatchBase({ type: 'library/pageAppended', items: page.items, nextCursor: page.next_cursor });
      return true;
    } catch (error) {
      if (!requestGateRef.current.isSessionCurrent(generation) || stateRef.current.libraryNextCursor !== startCursor) return false;
      dispatchBase({ type: 'library/pageFailed', message: classifyCollectionLoadProblem(error).message });
      return false;
    } finally {
      loadMoreGuardRef.current.library = false;
    }
  }, []);

  const loadMoreJobs = useCallback(async (): Promise<boolean> => {
    const current = stateRef.current;
    const startCursor = current.jobsNextCursor;
    if (!startCursor || current.jobsLoadingMore || loadMoreGuardRef.current.jobs) return false;
    loadMoreGuardRef.current.jobs = true;
    const generation = requestGateRef.current.currentSession();
    dispatchBase({ type: 'jobs/pageLoading' });
    try {
      const page = await listJobs({ cursor: startCursor, limit: JOBS_PAGE_SIZE });
      if (!requestGateRef.current.isSessionCurrent(generation) || stateRef.current.jobsNextCursor !== startCursor) return false;
      dispatchBase({ type: 'jobs/pageAppended', jobs: page.items, nextCursor: page.next_cursor });
      return true;
    } catch (error) {
      if (!requestGateRef.current.isSessionCurrent(generation) || stateRef.current.jobsNextCursor !== startCursor) return false;
      dispatchBase({ type: 'jobs/pageFailed', message: classifyCollectionLoadProblem(error).message });
      return false;
    } finally {
      loadMoreGuardRef.current.jobs = false;
    }
  }, []);

  const reset = useCallback(() => {
    requestGateRef.current.invalidateSession();
    dispatchBase({ type: 'authenticated/reset' });
  }, []);

  const captureSessionToken = useCallback(() => requestGateRef.current.currentSession(), []);
  const isSessionTokenCurrent = useCallback((generation: number) => requestGateRef.current.isSessionCurrent(generation), []);

  useEffect(() => {
    let active = true;
    void loadAuthenticated().catch(async (bootstrapError) => {
      if (!active) return;
      reset();
      try {
        await loadPublic();
      } catch {
        dispatchBase({ type: 'health/offline' });
        dispatchBase({ type: 'public/resolved', bootstrapStatus: { needs_setup: false } });
      }
      if (!isAuthError(bootstrapError)) {
        dispatchBase({ type: 'session/loadFailed', problem: classifySessionLoadProblem(bootstrapError) });
      }
    });
    return () => {
      active = false;
    };
  }, [loadAuthenticated, loadPublic, reset]);

  useEffect(() => {
    if (state.authStage !== 'ready' || eventsHeld) return undefined;
    let active = true;
    let disconnected = false;
    const generation = requestGateRef.current.currentSession();
    const coalescer = createWorkspaceEventCoalescer((events) => {
      if (!active || !requestGateRef.current.isSessionCurrent(generation)) return;
      startTransition(() => dispatchBase({ type: 'realtime/eventsBatch', events }));
    });
    // A member with library limits gets a content-free {broadcast: true} hint instead of a shared item (ADR 0019):
    // refetch their own first page, at most once per window, and upsert it the way an item event would (#167).
    let hintTimer: ReturnType<typeof setTimeout> | undefined;
    const refetchOnHint = () => {
      hintTimer = undefined;
      listLibrary({ limit: LIBRARY_PAGE_SIZE }, { background: true }).then((page) => {
        if (!active || !requestGateRef.current.isSessionCurrent(generation)) return;
        // Oldest first: each row then sorts at or before the head and is kept.
        startTransition(() => { for (const item of [...page.items].reverse()) dispatchBase({ type: 'library/upsert', item }); });
      }, () => undefined); // a failed background refetch waits for the next hint or reload
    };
    const source = connectEvents((event) => {
      if (!active || !requestGateRef.current.isSessionCurrent(generation)) return;
      if (isLibraryHint(event)) {
        hintTimer ??= setTimeout(refetchOnHint, LIBRARY_HINT_REFETCH_MS);
        return;
      }
      coalescer.handle(event);
    });
    source.onopen = () => {
      if (!active || !requestGateRef.current.isSessionCurrent(generation)) return;
      dispatchBase({ type: 'health/online' });
      if (!disconnected) return;
      disconnected = false;
      void Promise.all([retryCollection('jobs'), retryCollection('library')]);
    };
    source.onerror = () => {
      if (!active || !requestGateRef.current.isSessionCurrent(generation)) return;
      disconnected = true;
      // Drop buffered progress: a flush after the disconnect downgrade would
      // briefly flip jobsState offline -> stale. The reconnect snapshot
      // refetch carries anything the cancel discards.
      coalescer.cancel();
      requestGateRef.current.invalidate('jobs');
      requestGateRef.current.invalidate('library');
      const problem = classifyCollectionLoadProblem(new TypeError('Realtime connection lost'));
      dispatchBase({ type: 'realtime/disconnected', problem });
    };
    return () => {
      active = false;
      clearTimeout(hintTimer);
      coalescer.cancel();
      source.close();
    };
  }, [eventsHeld, retryCollection, state.authStage, state.currentUser?.id]);

  useEffect(() => {
    try {
      const storage = typeof window === 'undefined' ? undefined : window.localStorage;
      if (typeof storage?.setItem === 'function') {
        storage.setItem(
          SIDEBAR_COLLAPSED_STORAGE_KEY,
          sidebarCollapsedStorageValue(state.preferences.sidebarCollapsed),
        );
      }
    } catch {
      // Storage denied (e.g. private browsing/quota): the server-side ui_prefs value is still the source of truth.
    }
  }, [state.preferences.sidebarCollapsed]);

  useEffect(() => {
    if (!canAutosaveWorkspacePreferences(state)) return undefined;
    if (settingsSaveTimerRef.current) window.clearTimeout(settingsSaveTimerRef.current);
    const generation = requestGateRef.current.currentSession();
    settingsSaveTimerRef.current = window.setTimeout(() => {
      if (!requestGateRef.current.isSessionCurrent(generation)) return;
      updateMySettings(workspaceSettingsChanges(state))
        .then((settings) => {
          if (requestGateRef.current.isSessionCurrent(generation)) dispatchBase({ type: 'settings/replace', settings });
        })
        .catch((error) => {
          if (requestGateRef.current.isSessionCurrent(generation)) errorReporterRef.current(error instanceof Error ? error.message : 'Unable to save personal settings');
        });
    }, 650);
    return () => {
      if (settingsSaveTimerRef.current) window.clearTimeout(settingsSaveTimerRef.current);
    };
  }, [state.authStage, state.currentUser, state.preferences]);

  return { state, dispatch, loadAuthenticated, loadPublic, retryCollection, refreshLibraryCollection, loadMoreLibrary, loadMoreJobs, captureSessionToken, isSessionTokenCurrent, reset, holdEvents };
}
