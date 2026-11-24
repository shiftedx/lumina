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
import { outputFolderProblem } from './sourcePreferences';
import {
  SIDEBAR_COLLAPSED_STORAGE_KEY,
  sidebarCollapsedStorageValue,
} from './sidebarModel';
import { THEATER_MODE_UI_PREF_KEY } from './theaterMode';
import { STREAMING_PROVIDERS_UI_PREF_KEY } from './features/streaming/providers';
import { AUTOPLAY_UP_NEXT_UI_PREF_KEY } from './autoplayUpNext';
import { THEME_UI_PREF_KEY } from './theme';
import {
  CollectionRequestGate,
  createWorkspaceEventCoalescer,
  isLibraryHint,
  LIBRARY_HINT_REFETCH_MS,
  type CollectionResource,
} from './workspaceEvents';
import {
  classifyCollectionLoadProblem,
  classifySessionLoadProblem,
  createInitialWorkspaceState,
  JOBS_PAGE_SIZE,
  LIBRARY_PAGE_SIZE,
  type WorkspaceAction,
  type WorkspacePreferences,
  type WorkspaceState,
  workspaceReducer,
  workspaceSettingsChanges,
} from './workspaceState';
import type { UserProfile, UserSettings } from './types';

const BOOTSTRAP_REQUEST_TIMEOUT_MS = 3500;

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
  refreshContinueWatching: () => Promise<boolean>;
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

type WorkspaceSettingsDirty = {
  downloadDefaults: number | null;
  uiPrefs: Map<string, number>;
};

type WorkspaceSettingsKeys = { downloadDefaults: boolean; uiPrefs: readonly string[] };

function hasWorkspaceSettingsChanges(state: Pick<WorkspaceState, 'preferences' | 'userSettings'>, keys: WorkspaceSettingsKeys): boolean {
  const changes = workspaceSettingsChanges(state, keys);
  return changes.download_defaults !== undefined || Object.keys(changes.ui_prefs).length > 0;
}

type WorkspaceSettingsPending = { revision: number; deadline: number };
type WorkspaceSettingsAutosavePhase =
  | { kind: 'idle'; revision: number }
  | { kind: 'debouncing'; revision: number; timer: number }
  | { kind: 'writing'; revision: number; generation: number; settings: UserSettings; sent: WorkspaceSettingsKeys; pending: WorkspaceSettingsPending | null }
  | { kind: 'awaitingBaseline'; revision: number; pending: WorkspaceSettingsPending };

type WorkspaceSettingsAutosave = {
  ownerId: string | null;
  dirty: WorkspaceSettingsDirty;
  phase: WorkspaceSettingsAutosavePhase;
};

function emptyWorkspaceSettingsDirty(): WorkspaceSettingsDirty {
  return { downloadDefaults: null, uiPrefs: new Map() };
}

function workspaceSettingsRevision(phase: WorkspaceSettingsAutosavePhase): number {
  return phase.kind === 'writing' && phase.pending ? phase.pending.revision
    : phase.kind === 'awaitingBaseline' ? phase.pending.revision
      : phase.revision;
}

function workspaceSettingsKeysAtRevision(dirty: WorkspaceSettingsDirty, revision: number): WorkspaceSettingsKeys {
  return {
    downloadDefaults: dirty.downloadDefaults !== null && dirty.downloadDefaults <= revision,
    uiPrefs: [...dirty.uiPrefs].flatMap(([key, changedRevision]) => changedRevision <= revision ? [key] : []),
  };
}

function clearWorkspaceSettingsDirty(dirty: WorkspaceSettingsDirty, sent: WorkspaceSettingsKeys, revision: number): void {
  if (sent.downloadDefaults && dirty.downloadDefaults !== null && dirty.downloadDefaults <= revision) dirty.downloadDefaults = null;
  for (const key of sent.uiPrefs) {
    if ((dirty.uiPrefs.get(key) ?? Infinity) <= revision) dirty.uiPrefs.delete(key);
  }
}

function clearWorkspaceSettingsAutosave(autosave: WorkspaceSettingsAutosave): void {
  if (autosave.phase.kind === 'debouncing') window.clearTimeout(autosave.phase.timer);
  autosave.ownerId = null;
  autosave.dirty = emptyWorkspaceSettingsDirty();
  autosave.phase = { kind: 'idle', revision: 0 };
}

function workspaceSettingsKeysForPatch(patch: Partial<WorkspacePreferences>): WorkspaceSettingsKeys {
  const uiPrefs: string[] = [];
  let downloadDefaults = false;
  for (const key of Object.keys(patch) as Array<keyof WorkspacePreferences>) {
    switch (key) {
      case 'outputFolder': case 'outputContainer': case 'formatPreset': case 'downloadSubtitles': downloadDefaults = true; break;
      case 'sidebarCollapsed': uiPrefs.push('sidebar_collapsed'); break;
      case 'theaterMode': uiPrefs.push(THEATER_MODE_UI_PREF_KEY); break;
      case 'streamingProviders': uiPrefs.push(STREAMING_PROVIDERS_UI_PREF_KEY); break;
      case 'autoplayUpNext': uiPrefs.push(AUTOPLAY_UP_NEXT_UI_PREF_KEY); break;
      case 'theme': uiPrefs.push(THEME_UI_PREF_KEY); break;
      case 'playerVolume': uiPrefs.push('player_volume'); break;
      case 'playbackMaxHeight': uiPrefs.push('playback_max_height'); break;
      case 'normalizeLoudness': uiPrefs.push('normalize_loudness'); break;
      case 'autoSkip': uiPrefs.push('auto_skip'); break;
      case 'captions': uiPrefs.push('captions'); break;
      case 'profanity': uiPrefs.push('profanity'); break;
      case 'homeShelves': uiPrefs.push('home_shelves'); break;
      case 'settingsAdvanced': uiPrefs.push('settings_advanced'); break;
      case 'musicBrowseView': uiPrefs.push('music_browse'); break;
    }
  }
  return { downloadDefaults, uiPrefs };
}

export function useAuthenticatedWorkspace({ onError }: WorkspaceHookOptions): AuthenticatedWorkspace {
  const [state, dispatchBase] = useReducer(workspaceReducer, undefined, createInitialWorkspaceState);
  const settingsAutosaveRef = useRef<WorkspaceSettingsAutosave>({ ownerId: null, dirty: emptyWorkspaceSettingsDirty(), phase: { kind: 'idle', revision: 0 } });
  const requestGateRef = useRef(new CollectionRequestGate());
  const errorReporterRef = useRef(onError);
  errorReporterRef.current = onError;
  const stateRef = useRef(state);
  stateRef.current = state;
  const loadMoreJobsGuardRef = useRef(false);
  const [eventsHeld, holdEvents] = useState(false);
  const scheduleSettingsAutosaveRef = useRef<(ownerId: string, revision: number, delay: number) => void>(() => undefined);

  const scheduleSettingsAutosave = useCallback((ownerId: string, revision: number, delay: number) => {
    const autosave = settingsAutosaveRef.current;
    if (autosave.ownerId !== ownerId) return;
    if (autosave.phase.kind === 'debouncing') window.clearTimeout(autosave.phase.timer);
    if (autosave.phase.kind !== 'idle' && autosave.phase.kind !== 'debouncing') return;
    const timer = window.setTimeout(() => {
      const phase = autosave.phase;
      if (phase.kind !== 'debouncing' || phase.revision !== revision) return;
      const current = stateRef.current;
      if (!canAutosaveWorkspacePreferences(current) || current.currentUser?.id !== ownerId) {
        clearWorkspaceSettingsAutosave(autosave);
        return;
      }
      const sent = workspaceSettingsKeysAtRevision(autosave.dirty, revision);
      if (!hasWorkspaceSettingsChanges(current, sent)) {
        clearWorkspaceSettingsDirty(autosave.dirty, sent, revision);
        autosave.phase = { kind: 'idle', revision };
        return;
      }
      const writing: WorkspaceSettingsAutosavePhase = {
        kind: 'writing', revision, generation: requestGateRef.current.currentSession(), settings: current.userSettings!, sent, pending: null,
      };
      autosave.phase = writing;
      void updateMySettings(workspaceSettingsChanges(current, sent)).then((saved) => {
        const currentState = stateRef.current;
        if (autosave.phase !== writing || !requestGateRef.current.isSessionCurrent(writing.generation) || currentState.currentUser?.id !== ownerId) return;
        clearWorkspaceSettingsDirty(autosave.dirty, writing.sent, writing.revision);
        const pending = writing.pending;
        const acknowledged = currentState.userSettings === writing.settings;
        if (acknowledged) {
          autosave.phase = pending ? { kind: 'awaitingBaseline', revision: writing.revision, pending } : { kind: 'idle', revision: writing.revision };
          dispatchBase({ type: 'settings/replace', settings: saved });
        } else {
          autosave.phase = { kind: 'idle', revision: writing.revision };
          if (pending) scheduleSettingsAutosaveRef.current(ownerId, pending.revision, Math.max(0, pending.deadline - Date.now()));
        }
      }, (error) => {
        if (autosave.phase !== writing || !requestGateRef.current.isSessionCurrent(writing.generation) || stateRef.current.currentUser?.id !== ownerId) return;
        const pending = writing.pending;
        autosave.phase = { kind: 'idle', revision: writing.revision };
        errorReporterRef.current(error instanceof Error ? error.message : 'Unable to save personal settings');
        if (pending) scheduleSettingsAutosaveRef.current(ownerId, pending.revision, Math.max(0, pending.deadline - Date.now()));
      });
    }, delay);
    autosave.phase = { kind: 'debouncing', revision, timer };
  }, []);
  scheduleSettingsAutosaveRef.current = scheduleSettingsAutosave;

  const requestWorkspaceSettingsAutosave = useCallback((patch: Partial<WorkspacePreferences>) => {
    const current = stateRef.current;
    if (!canAutosaveWorkspacePreferences(current)) return;
    const autosave = settingsAutosaveRef.current;
    const ownerId = current.currentUser!.id;
    if (autosave.ownerId !== ownerId) return;
    const revision = workspaceSettingsRevision(autosave.phase) + 1;
    const keys = workspaceSettingsKeysForPatch(patch);
    if (keys.downloadDefaults) autosave.dirty.downloadDefaults = revision;
    for (const key of keys.uiPrefs) autosave.dirty.uiPrefs.set(key, revision);
    const deadline = Date.now() + 650;
    switch (autosave.phase.kind) {
      case 'idle':
      case 'debouncing':
        scheduleSettingsAutosave(ownerId, revision, 650);
        break;
      case 'writing':
        autosave.phase.pending = { revision, deadline };
        break;
      case 'awaitingBaseline':
        autosave.phase.pending = { revision, deadline };
        break;
    }
  }, [scheduleSettingsAutosave]);

  const dispatch = useCallback<Dispatch<WorkspaceAction>>((action) => {
    if (action.type === 'preferences/patch') requestWorkspaceSettingsAutosave(action.patch);
    dispatchBase(action);
  }, [requestWorkspaceSettingsAutosave]);

  const loadPublic = useCallback(async () => {
    const generation = requestGateRef.current.beginSession();
    clearWorkspaceSettingsAutosave(settingsAutosaveRef.current);
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
    clearWorkspaceSettingsAutosave(settingsAutosaveRef.current);
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
    const libraryProblem = libraryResult.status === 'rejected' ? classifyCollectionLoadProblem(libraryResult.reason) : null;
    const settings = settingsResult.status === 'fulfilled' ? settingsResult.value : null;
    const automations = automationResult.status === 'fulfilled' ? automationResult.value : [];
    const continueWatching = continueWatchingResult.status === 'fulfilled' ? continueWatchingResult.value : null;
    if (settingsResult.status === 'rejected') hydrationIssues.push('personal settings');
    if (automationResult.status === 'rejected') hydrationIssues.push('automation');
    const preferenceIssues: string[] = [];
    const savedOutputFolder = settings?.resolved_download_defaults.output_profile.subdir;
    if (typeof savedOutputFolder === 'string' && outputFolderProblem(savedOutputFolder)) {
      preferenceIssues.push('an unsafe output folder was reset to the Library root');
    }
    settingsAutosaveRef.current.ownerId = sessionResult.user.id;
    dispatchBase({
      type: 'authenticated/resolved',
      currentUser: sessionResult.user,
      jobs,
      jobsProblem,
      jobsNextCursor,
      library,
      libraryProblem,
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
      dispatchBase({ type: 'library/loadSucceeded', library: page.items });
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
      dispatchBase({ type: 'library/loadSucceeded', library: page.items });
      return true;
    } catch (error) {
      if (!requestGateRef.current.isCurrent(token)) return false;
      dispatchBase({ type: 'library/loadFailed', problem: classifyCollectionLoadProblem(error, 'refresh') });
      return false;
    }
  }, []);

  const refreshContinueWatching = useCallback(async (): Promise<boolean> => {
    const generation = requestGateRef.current.currentSession();
    try {
      const entries = await listContinueWatching();
      if (!requestGateRef.current.isSessionCurrent(generation)) return false;
      dispatchBase({ type: 'continueWatching/replace', entries });
      return true;
    } catch {
      return false;
    }
  }, []);

  const loadMoreJobs = useCallback(async (): Promise<boolean> => {
    const current = stateRef.current;
    const startCursor = current.jobsNextCursor;
    if (!startCursor || current.jobsLoadingMore || loadMoreJobsGuardRef.current) return false;
    loadMoreJobsGuardRef.current = true;
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
      loadMoreJobsGuardRef.current = false;
    }
  }, []);

  const reset = useCallback(() => {
    requestGateRef.current.invalidateSession();
    clearWorkspaceSettingsAutosave(settingsAutosaveRef.current);
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
        startTransition(() => { for (const item of [...page.items].reverse()) dispatchBase({ type: 'library/upsert', item }); });
      }, () => undefined);
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
    const autosave = settingsAutosaveRef.current;
    if (!canAutosaveWorkspacePreferences(state)) {
      clearWorkspaceSettingsAutosave(autosave);
      return undefined;
    }
    const ownerId = state.currentUser!.id;
    if (autosave.ownerId !== ownerId) {
      clearWorkspaceSettingsAutosave(autosave);
      autosave.ownerId = ownerId;
      return undefined;
    }
    if (autosave.phase.kind === 'awaitingBaseline') {
      const { pending, revision } = autosave.phase;
      autosave.phase = { kind: 'idle', revision };
      scheduleSettingsAutosave(ownerId, pending.revision, Math.max(0, pending.deadline - Date.now()));
    }
    return undefined;
  }, [scheduleSettingsAutosave, state.authStage, state.currentUser?.id, state.settingsHydrated, state.userSettings]);

  useEffect(() => () => clearWorkspaceSettingsAutosave(settingsAutosaveRef.current), []);

  return { state, dispatch, loadAuthenticated, loadPublic, retryCollection, refreshLibraryCollection, refreshContinueWatching, loadMoreJobs, captureSessionToken, isSessionTokenCurrent, reset, holdEvents };
}
