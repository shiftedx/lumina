import { describe, expect, it, vi } from 'vitest';

import type { AppEvent, DownloadJob, LibraryItem, PlaybackProgress, UserProfile, UserSettings } from './types';
import { ApiRequestError } from './api';
import {
  canAutosaveWorkspacePreferences,
  classifyCollectionLoadProblem,
  CollectionRequestGate,
  createInitialWorkspaceState,
  createWorkspaceEventCoalescer,
  reconcileWorkspaceEvent,
  workspaceDownloadDefaults,
  workspaceReducer,
  workspaceSettingsChanges,
  workspaceSettingsUpdate,
  type WorkspaceAction,
} from './workspace';

const user = { id: 'user-1', username: 'one', display_name: 'One', role: 'viewer' } as UserProfile;
const otherUser = { id: 'user-2', username: 'two', display_name: 'Two', role: 'viewer' } as UserProfile;

const settings = {
  id: 'settings-1',
  resolved_download_defaults: {
    format_selection: { preset: 'audio_only', output_container: 'mkv', subtitles: false },
    output_profile: { base_path: '/server-controlled-library', subdir: 'family/videos', template: '', organize_by: 'downloads' },
  },
  resolved_automation_defaults: {},
  ui_prefs: { sidebar_collapsed: true },
} as unknown as UserSettings;

const job = { id: 'job-1', source_url: 'https://example.test/1', status: 'queued', progress: 0 } as DownloadJob;

type ResolvedAction = Extract<WorkspaceAction, { type: 'authenticated/resolved' }>;

function resolve(overrides: Partial<ResolvedAction> = {}, state = createInitialWorkspaceState()) {
  return workspaceReducer(state, {
    type: 'authenticated/resolved', currentUser: user, jobs: [], jobsProblem: null, library: [], libraryProblem: null,
    userSettings: settings, sourceAutomations: [], ...overrides,
  });
}

describe('workspaceReducer', () => {
  it('persists the editable output folder as a Library subdirectory', () => {
    const preferences = {
      ...createInitialWorkspaceState().preferences,
      formatPreset: 'audio_only' as const,
      outputContainer: 'mkv' as const,
      downloadSubtitles: false,
      outputFolder: 'family/audio',
    };

    expect(workspaceDownloadDefaults(preferences)).toEqual({
      format_selection: expect.objectContaining({ preset: 'audio_only', output_container: 'mkv', subtitles: false }),
      output_profile: expect.objectContaining({ base_path: null, subdir: 'family/audio' }),
    });
  });

  it('starts both collections in an explicit loading state', () => {
    const initial = createInitialWorkspaceState();
    expect(initial.jobsState).toBe('loading');
    expect(initial.libraryState).toBe('loading');
  });

  it('keeps returning users on the loading screen while authenticated metadata resolves', () => {
    const state = workspaceReducer(createInitialWorkspaceState(), {
      type: 'public/resolved',
      bootstrapStatus: { needs_setup: false },
      preserveAuthStage: true,
    });
    expect(state.authStage).toBe('loading');
  });

  it('keeps a partially hydrated session usable while exposing the failed library transition', () => {
    const state = resolve({ jobs: [job], library: null, libraryProblem: classifyCollectionLoadProblem(new ApiRequestError('Timed out', 0)) });

    expect(state.authStage).toBe('ready');
    expect(state.libraryState).toBe('offline');
    expect(state.library).toEqual([]);
    expect(state.libraryProblem?.kind).toBe('timeout');
    expect(state.jobsState).toBe('ready');
    expect(state.settingsHydrated).toBe(true);
    expect(canAutosaveWorkspacePreferences(state)).toBe(true);
    expect(state.jobs).toEqual([job]);
    expect(state.preferences).toMatchObject({
      outputFolder: 'family/videos',
      outputContainer: 'mkv',
      formatPreset: 'audio_only',
      downloadSubtitles: false,
      sidebarCollapsed: true,
    });
  });

  it('preserves an empty saved subdirectory as the Library root', () => {
    const withHealth = workspaceReducer(createInitialWorkspaceState(), {
      type: 'health/resolved',
      health: { status: 'ok' },
    });
    const rootSettings = {
      ...settings,
      resolved_download_defaults: {
        ...settings.resolved_download_defaults,
        output_profile: { ...settings.resolved_download_defaults.output_profile, subdir: '' },
      },
    } as UserSettings;
    const hydrated = resolve({ userSettings: rootSettings }, withHealth);

    expect(hydrated.preferences.outputFolder).toBe('');
  });

  it('does not enable preference autosave when settings hydration fails', () => {
    const state = resolve({ userSettings: null });

    expect(state.authStage).toBe('ready');
    expect(state.settingsHydrated).toBe(false);
    expect(canAutosaveWorkspacePreferences(state)).toBe(false);
    expect(state.jobsState).toBe('empty');
    expect(state.libraryState).toBe('empty');
  });

  it('clears authenticated data on reset while retaining public runtime knowledge', () => {
    const hydrated = resolve({ jobs: [job], userSettings: null }, workspaceReducer(createInitialWorkspaceState(), {
      type: 'health/resolved',
      health: { status: 'degraded', version: '1.0.0' },
    }));

    const reset = workspaceReducer(hydrated, { type: 'authenticated/reset' });
    expect(reset.currentUser).toBeNull();
    expect(reset.jobs).toEqual([]);
    expect(reset.authStage).toBe('loading');
    expect(reset.health).toBe('ok');
    expect(reset.preferences.outputFolder).toBe('');
  });

  it('clears unsafe hydrated and programmatic preferences at the settings update boundary', () => {
    const invalidFolders = ['C:relative', 'family/%name', 'family/videos\nprivate', 'family/videos\n', 'x'.repeat(241)];
    for (const outputFolder of invalidFolders) {
      const unsafeSettings = {
        ...settings,
        resolved_download_defaults: {
          ...settings.resolved_download_defaults,
          output_profile: { ...settings.resolved_download_defaults.output_profile, subdir: outputFolder },
        },
      } as UserSettings;
      const hydrated = resolve({ userSettings: unsafeSettings });

      expect(hydrated.preferences.outputFolder).toBe('');

      const payload = workspaceSettingsUpdate({
        userSettings: unsafeSettings,
        preferences: {
          ...hydrated.preferences,
          outputFolder,
        },
      });
      expect(payload.download_defaults.output_profile.subdir).toBe('');
    }
  });

  it('reads and writes ui_prefs.captions without touching other prefs', () => {
    const state = resolve({ userSettings: { ...settings, ui_prefs: { captions: { size: 'small', background: 'none' }, theme: 'dark', auto_skip: { intro: true, credits: false, recap: false } } } });
    expect(state.preferences.captions).toEqual({ size: 'small', background: 'none' });
    const update = workspaceSettingsUpdate({ userSettings: state.userSettings, preferences: { ...state.preferences, captions: { size: 'large', background: 'box' } } });
    expect(update.ui_prefs.captions).toEqual({ size: 'large', background: 'box' });
    expect(update.ui_prefs.theme).toBe('dark');
    expect(update.ui_prefs.auto_skip).toEqual({ intro: true, credits: false, recap: false });
  });

  it('keeps unknown UI preferences while saving the sidebar choice', () => {
    const payload = workspaceSettingsUpdate({
      userSettings: { ...settings, ui_prefs: { density: 'compact', sidebar_collapsed: false } },
      preferences: { ...createInitialWorkspaceState().preferences, sidebarCollapsed: true },
    });

    expect(payload.ui_prefs).toEqual({
      density: 'compact', sidebar_collapsed: true, streaming_providers: ['twitch'], theater_mode: false, autoplay_up_next: true, theme: 'system', player_volume: 1, playback_max_height: null,
      normalize_loudness: true, auto_skip: { intro: false, credits: false, recap: false }, captions: { size: 'medium', background: 'shadow' }, profanity: { enabled: false, words: [] }, home_shelves: null, settings_advanced: false,
    });
  });

  it('hydrates and persists the member streaming providers, defaulting to Twitch', () => {
    expect(resolve({ userSettings: { ...settings, ui_prefs: {} } }).preferences.streamingProviders).toEqual(['twitch']);
    const hydrated = resolve({ userSettings: { ...settings, ui_prefs: { streaming_providers: ['kick'], theater_mode: true } } });
    expect(hydrated.preferences.streamingProviders).toEqual(['kick']);
    expect(workspaceSettingsUpdate(hydrated).ui_prefs).toMatchObject({ streaming_providers: ['kick'], theater_mode: true });
  });

  it('hydrates and persists the member autoplay preference', () => {
    const hydrated = resolve({ userSettings: { ...settings, ui_prefs: { autoplay_up_next: false } } });

    expect(hydrated.preferences.autoplayUpNext).toBe(false);
    expect(workspaceSettingsUpdate(hydrated).ui_prefs).toMatchObject({ autoplay_up_next: false });
  });

  it('restores the member playback ceiling, defaulting to Best available for missing or unknown values', () => {
    const hydrate = (ui_prefs: Record<string, unknown>) => resolve({ userSettings: { ...settings, ui_prefs } });
    const capped = hydrate({ playback_max_height: 720 });
    expect(capped.preferences.playbackMaxHeight).toBe(720);
    expect(workspaceSettingsUpdate(capped).ui_prefs).toMatchObject({ playback_max_height: 720 });
    expect(workspaceSettingsUpdate(capped).download_defaults).toEqual(workspaceSettingsUpdate(hydrate({})).download_defaults);
    for (const ui_prefs of [{}, { playback_max_height: 999 }, { playback_max_height: '720' }]) {
      expect(hydrate(ui_prefs).preferences.playbackMaxHeight).toBeNull();
    }
  });

  it('hydrates loudness, auto-skip and mute preferences, ignoring malformed values', () => {
    const hydrate = (ui_prefs: Record<string, unknown>) => resolve({ userSettings: { ...settings, ui_prefs } }).preferences;
    const saved = hydrate({ normalize_loudness: false, auto_skip: { intro: true, credits: 'yes', extra: true }, profanity: { enabled: true, words: ['heck', 7, 'darn'] } });
    expect(saved.normalizeLoudness).toBe(false);
    expect(saved.autoSkip).toEqual({ intro: true, credits: false, recap: false });
    expect(saved.profanity).toEqual({ enabled: true, words: ['heck', 'darn'] });
    for (const bad of [{ normalize_loudness: 'no', auto_skip: [], profanity: 'on' }, {}]) {
      const fallback = hydrate(bad);
      expect([fallback.normalizeLoudness, fallback.autoSkip, fallback.profanity]).toEqual([true, { intro: false, credits: false, recap: false }, { enabled: false, words: [] }]);
    }
    expect(workspaceSettingsUpdate(resolve({ userSettings: { ...settings, ui_prefs: { profanity: { enabled: true, words: ['heck'] } } } })).ui_prefs).toMatchObject({ profanity: { enabled: true, words: ['heck'] } });
  });

  it('hydrates the member Home layout, saves it, resets it, and never carries it to another member', () => {
    const layout = [{ id: 'next_up', visible: false }, { id: 'continue', visible: true }] as const;
    const hydrated = resolve({ userSettings: { ...settings, ui_prefs: { home_shelves: layout } } });
    expect(hydrated.preferences.homeShelves).toEqual(layout);
    expect(workspaceSettingsUpdate(hydrated).ui_prefs).toMatchObject({ home_shelves: layout });
    // Anything but a list is no preference (normalizeHomeShelves then gives the default).
    for (const home_shelves of ['next_up', { id: 'live' }, null]) {
      expect(resolve({ userSettings: { ...settings, ui_prefs: { home_shelves } } }).preferences.homeShelves).toBeNull();
    }
    expect(resolve({ userSettings: { ...settings, ui_prefs: {} } }).preferences.homeShelves).toBeNull();
    // Same member, settings unavailable: keep what this browser had; another member: never the previous member's.
    expect(resolve({ userSettings: null }, hydrated).preferences.homeShelves).toEqual(layout);
    expect(resolve({ currentUser: otherUser, userSettings: null }, hydrated).preferences.homeShelves).toBeNull();
    // Reset to default writes null; sign-out forgets it.
    const reset = workspaceReducer(hydrated, { type: 'preferences/patch', patch: { homeShelves: null } });
    expect(workspaceSettingsUpdate(reset).ui_prefs.home_shelves).toBeNull();
    expect(workspaceReducer(hydrated, { type: 'authenticated/reset' }).preferences.homeShelves).toBeNull();
  });

  it('autosaves only the ui_prefs keys this browser changed, so a layout saved elsewhere is not overwritten', () => {
    const layout = [{ id: 'next_up', visible: false }] as const;
    const hydrated = resolve({ userSettings: { ...settings, ui_prefs: { home_shelves: layout } } });
    expect(workspaceSettingsChanges(hydrated).ui_prefs).not.toHaveProperty('home_shelves');
    const changed = workspaceReducer(hydrated, { type: 'preferences/patch', patch: { playerVolume: 0.25 } });
    expect(workspaceSettingsChanges(changed).ui_prefs).toMatchObject({ player_volume: 0.25 });
    expect(workspaceSettingsChanges(changed).ui_prefs).not.toHaveProperty('home_shelves');
    expect(workspaceSettingsChanges(workspaceReducer(hydrated, { type: 'preferences/patch', patch: { homeShelves: null } })).ui_prefs).toMatchObject({ home_shelves: null });
  });

  it('defaults autoplay on when member settings omit or malform the preference', () => {
    for (const ui_prefs of [{}, { autoplay_up_next: 'false' }]) {
      const hydrated = resolve({ userSettings: { ...settings, ui_prefs } });
      expect(hydrated.preferences.autoplayUpNext).toBe(true);
    }
  });

  it('merges incremental job updates without losing hydrated fields', () => {
    const withJob = workspaceReducer(createInitialWorkspaceState(), { type: 'jobs/upsert', job });
    const progressed = workspaceReducer(withJob, { type: 'jobs/upsert', job: { id: job.id, progress: 42 } });
    expect(progressed.jobs[0]).toMatchObject({ id: 'job-1', source_url: job.source_url, status: 'queued', progress: 42 });
  });

  it('does not present failed initial jobs or library hydration as empty collections', () => {
    const unauthorized = classifyCollectionLoadProblem(new ApiRequestError('Unauthorized', 401));
    const server = classifyCollectionLoadProblem(new ApiRequestError('Internal server error', 500));
    const failed = resolve({ jobs: null, jobsProblem: unauthorized, library: null, libraryProblem: server });

    expect(failed.jobsState).toBe('failed');
    expect(failed.jobsProblem).toMatchObject({ kind: 'unauthorized', requiresSignIn: true });
    expect(failed.libraryState).toBe('failed');
    expect(failed.libraryProblem?.kind).toBe('server');
  });

  it('preserves and labels last-known data when a refresh fails, then clears the warning after recovery', () => {
    const hydrated = resolve({ jobs: [job], library: [{ id: 'library-1', title: 'Saved film', status: 'available' } as LibraryItem] });
    const stale = workspaceReducer(hydrated, {
      type: 'jobs/loadFailed',
      problem: classifyCollectionLoadProblem(new TypeError('fetch failed')),
    });

    expect(stale.jobs).toEqual([job]);
    expect(stale.jobsState).toBe('stale');
    expect(stale.jobsProblem?.kind).toBe('offline');

    const recovered = workspaceReducer(stale, { type: 'jobs/loadSucceeded', jobs: [] });
    expect(recovered.jobs).toEqual([]);
    expect(recovered.jobsState).toBe('empty');
    expect(recovered.jobsProblem).toBeNull();

    const libraryRecovered = workspaceReducer(stale, {
      type: 'library/loadSucceeded',
      library: [{ id: 'library-2', title: 'Recovered film', status: 'available' } as LibraryItem],
    });
    expect(libraryRecovered.libraryState).toBe('ready');
    expect(libraryRecovered.libraryProblem).toBeNull();
  });

  it('keeps the failed recovery panel state mounted while retrying', () => {
    const problem = classifyCollectionLoadProblem(new TypeError('fetch failed'));
    const failed = workspaceReducer(createInitialWorkspaceState(), { type: 'jobs/loadFailed', problem });
    const retrying = workspaceReducer(failed, { type: 'jobs/retrying' });

    expect(retrying.jobsState).toBe('offline');
    expect(retrying.jobsProblem).toBe(problem);
    expect(retrying.jobsRetrying).toBe(true);
  });

  it('never preserves one user’s last-known collections for another user', () => {
    const userA = resolve({ jobs: [job], library: [{ id: 'private-a', title: 'A private item', status: 'available' } as LibraryItem] });
    const userB = resolve({ currentUser: otherUser, jobs: null, jobsProblem: classifyCollectionLoadProblem(new ApiRequestError('server', 503)), library: null, libraryProblem: classifyCollectionLoadProblem(new ApiRequestError('server', 503)) }, userA);

    expect(userB.jobs).toEqual([]);
    expect(userB.library).toEqual([]);
    expect(userB.jobsState).toBe('failed');
    expect(userB.libraryState).toBe('failed');
  });

  it('marks successful empty snapshots unavailable on realtime loss and recovers from a fresh snapshot', () => {
    const empty = resolve();
    const disconnected = workspaceReducer(empty, {
      type: 'realtime/disconnected',
      problem: classifyCollectionLoadProblem(new TypeError('connection lost')),
    });
    expect(disconnected.jobsState).toBe('offline');
    expect(disconnected.libraryState).toBe('offline');
    expect(disconnected.health).toBe('offline');

    const jobsRecovered = workspaceReducer(disconnected, { type: 'jobs/loadSucceeded', jobs: [] });
    const recovered = workspaceReducer(jobsRecovered, { type: 'library/loadSucceeded', library: [] });
    expect(recovered.jobsState).toBe('empty');
    expect(recovered.libraryState).toBe('empty');
  });

  it('treats a library containing only missing rows as visibly empty', () => {
    const available = workspaceReducer(createInitialWorkspaceState(), {
      type: 'library/loadSucceeded',
      library: [{ id: 'library-1', title: 'Saved film', status: 'available' } as LibraryItem],
    });
    const missing = workspaceReducer(available, {
      type: 'library/upsert',
      item: { id: 'library-1', status: 'missing' },
    });
    expect(missing.libraryState).toBe('empty');
    expect(missing.selectedLibraryId).toBeNull();
  });

  it('classifies timeout, offline, unauthorized, and server failures for durable recovery guidance', () => {
    expect(classifyCollectionLoadProblem(new ApiRequestError('timeout', 0)).kind).toBe('timeout');
    expect(classifyCollectionLoadProblem(new TypeError('network down')).kind).toBe('offline');
    expect(classifyCollectionLoadProblem(new ApiRequestError('unauthorized', 401))).toMatchObject({
      kind: 'unauthorized',
      requiresSignIn: true,
    });
    expect(classifyCollectionLoadProblem(new ApiRequestError('server', 503)).kind).toBe('server');
    expect(classifyCollectionLoadProblem(new ApiRequestError('server', 503), 'refresh').retryOperation).toBe('refresh');
  });
});

function libraryItem(id: string, downloadedAt: string | null, extra: Partial<LibraryItem> = {}): LibraryItem {
  return { id, title: `Item ${id}`, status: 'available', downloaded_at: downloadedAt, ...extra } as LibraryItem;
}

describe('library paging window', () => {
  const hydrated = resolve({ jobsNextCursor: 'jobs-cursor-2', library: [libraryItem('lib-b', '2026-06-10T00:00:00Z'), libraryItem('lib-a', '2026-06-01T00:00:00Z')], libraryNextCursor: 'cursor-2' });

  it('records the first-page cursors from the authenticated hydration', () => {
    expect(hydrated.libraryNextCursor).toBe('cursor-2');
    expect(hydrated.jobsNextCursor).toBe('jobs-cursor-2');
    expect(hydrated.libraryLoadingMore).toBe(false);
  });

  it('appends and de-duplicates the next page onto the ordered window', () => {
    const loading = workspaceReducer(hydrated, { type: 'library/pageLoading' });
    expect(loading.libraryLoadingMore).toBe(true);

    const appended = workspaceReducer(loading, {
      type: 'library/pageAppended',
      items: [libraryItem('lib-a', '2026-06-01T00:00:00Z'), libraryItem('lib-c', '2026-05-20T00:00:00Z')],
      nextCursor: 'cursor-3',
    });

    expect(appended.library.map((item) => item.id)).toEqual(['lib-b', 'lib-a', 'lib-c']);
    expect(appended.libraryNextCursor).toBe('cursor-3');
    expect(appended.libraryLoadingMore).toBe(false);
  });

  it('bounds the mounted window to five pages by dropping from the head', () => {
    const firstPage = Array.from({ length: 300 }, (_, index) => libraryItem(`seed-${String(index).padStart(3, '0')}`, `2026-06-01T00:00:${String(index % 60).padStart(2, '0')}Z`));
    const seeded = resolve({ library: firstPage, libraryNextCursor: 'cursor-2' });
    const nextPage = Array.from({ length: 60 }, (_, index) => libraryItem(`page2-${index}`, `2026-05-01T00:00:${String(index % 60).padStart(2, '0')}Z`));

    const appended = workspaceReducer(seeded, { type: 'library/pageAppended', items: nextPage, nextCursor: 'cursor-3' });

    expect(appended.library).toHaveLength(300);
    expect(appended.library[0].id).toBe('seed-060');
    expect(appended.library[appended.library.length - 1].id).toBe('page2-59');
  });

  it('clears the loading flag and records a retry affordance when a page fetch fails', () => {
    const loading = workspaceReducer(hydrated, { type: 'library/pageLoading' });
    const failed = workspaceReducer(loading, { type: 'library/pageFailed', message: 'Could not load more of your library.' });
    expect(failed.libraryLoadingMore).toBe(false);
    expect(failed.libraryLoadMoreError).toBe('Could not load more of your library.');
  });

  it('updates a windowed item in place when a realtime upsert matches it', () => {
    const event = { type: 'library_item_upserted', payload: { user_id: user.id, item: { id: 'lib-b', title: 'Renamed in place' } } } as AppEvent;
    const next = reconcileWorkspaceEvent(hydrated, event);
    expect(next.library.map((item) => item.id)).toEqual(['lib-b', 'lib-a']);
    expect(next.library[0].title).toBe('Renamed in place');
  });

  it('prepends a brand-new download that sorts before the window head', () => {
    const item = libraryItem('lib-new', '2026-06-20T00:00:00Z', { title: 'Just downloaded' });
    const event = { type: 'library_item_upserted', payload: { user_id: user.id, item } } as AppEvent;
    const next = reconcileWorkspaceEvent(hydrated, event);
    expect(next.library.map((entry) => entry.id)).toEqual(['lib-new', 'lib-b', 'lib-a']);
  });

  it('ignores a realtime upsert for an item that lives beyond the loaded window', () => {
    const item = libraryItem('lib-older', '2026-05-01T00:00:00Z', { title: 'Older, unloaded' });
    const event = { type: 'library_item_upserted', payload: { user_id: user.id, item } } as AppEvent;
    const next = reconcileWorkspaceEvent(hydrated, event);
    expect(next.library.map((entry) => entry.id)).toEqual(['lib-b', 'lib-a']);
  });

  it('keeps the Home recent snapshot anchored to page one after the window head-drops', () => {
    const firstPage = Array.from({ length: 300 }, (_, index) => libraryItem(`seed-${String(index).padStart(3, '0')}`, `2026-06-01T00:00:${String(index % 60).padStart(2, '0')}Z`));
    const seeded = resolve({ library: firstPage, libraryNextCursor: 'cursor-2' });
    const nextPage = Array.from({ length: 60 }, (_, index) => libraryItem(`page2-${index}`, `2026-05-01T00:00:${String(index % 60).padStart(2, '0')}Z`));

    const appended = workspaceReducer(seeded, { type: 'library/pageAppended', items: nextPage, nextCursor: 'cursor-3' });

    expect(appended.library[0].id).toBe('seed-060');
    expect(appended.libraryRecent[0].id).toBe('seed-000');
    expect(appended.libraryRecent.map((item) => item.id)).toEqual(firstPage.map((item) => item.id));
  });

  it('reflects a fresh download in the Home recent snapshot', () => {
    const item = libraryItem('lib-new', '2026-06-20T00:00:00Z', { title: 'Just downloaded' });
    const next = reconcileWorkspaceEvent(hydrated, { type: 'library_item_upserted', payload: { user_id: user.id, item } } as AppEvent);
    expect(next.libraryRecent[0].id).toBe('lib-new');
  });

  it('rebuilds the Home recent snapshot from page one on refresh', () => {
    const appended = workspaceReducer(hydrated, {
      type: 'library/pageAppended', items: [libraryItem('lib-c', '2026-05-20T00:00:00Z')], nextCursor: 'cursor-3',
    });
    const refreshed = workspaceReducer(appended, {
      type: 'library/loadSucceeded', library: [libraryItem('lib-fresh', '2026-07-01T00:00:00Z')], nextCursor: 'fresh-cursor',
    });
    expect(refreshed.libraryRecent.map((item) => item.id)).toEqual(['lib-fresh']);
  });

  it('resets the window and cursor when a refresh replaces the library', () => {
    const appended = workspaceReducer(hydrated, {
      type: 'library/pageAppended', items: [libraryItem('lib-c', '2026-05-20T00:00:00Z')], nextCursor: 'cursor-3',
    });
    const refreshed = workspaceReducer(appended, {
      type: 'library/loadSucceeded', library: [libraryItem('lib-fresh', '2026-07-01T00:00:00Z')], nextCursor: 'fresh-cursor',
    });
    expect(refreshed.library.map((item) => item.id)).toEqual(['lib-fresh']);
    expect(refreshed.libraryNextCursor).toBe('fresh-cursor');
    expect(refreshed.libraryLoadingMore).toBe(false);
    expect(refreshed.libraryLoadMoreError).toBeNull();
  });

  it('bumps the window generation on replace but not on append', () => {
    const appended = workspaceReducer(hydrated, {
      type: 'library/pageAppended', items: [libraryItem('lib-c', '2026-05-20T00:00:00Z')], nextCursor: 'cursor-3',
    });
    expect(appended.libraryGeneration).toBe(hydrated.libraryGeneration);

    const refreshed = workspaceReducer(appended, {
      type: 'library/loadSucceeded', library: [libraryItem('lib-fresh', '2026-07-01T00:00:00Z')], nextCursor: 'fresh',
    });
    expect(refreshed.libraryGeneration).toBe(hydrated.libraryGeneration + 1);
  });

  it('re-sorts a re-downloaded mid-window item to the window head on a fresh downloaded_at', () => {
    const event = {
      type: 'library_item_upserted',
      payload: { user_id: user.id, item: { id: 'lib-a', downloaded_at: '2026-06-15T00:00:00Z', title: 'Downloaded again' } },
    } as AppEvent;
    const next = reconcileWorkspaceEvent(hydrated, event);
    expect(next.library.map((item) => item.id)).toEqual(['lib-a', 'lib-b']);
    expect(next.library[0].title).toBe('Downloaded again');
    expect(next.libraryRecent.map((item) => item.id)).toEqual(['lib-a', 'lib-b']);
  });

  it('keeps a merged item in place when its ordering fields are unchanged', () => {
    const event = { type: 'library_item_upserted', payload: { user_id: user.id, item: { id: 'lib-a', title: 'Renamed only' } } } as AppEvent;
    const next = reconcileWorkspaceEvent(hydrated, event);
    expect(next.library.map((item) => item.id)).toEqual(['lib-b', 'lib-a']);
  });

  it('moves the grid highlight to the first available row when a head-drop trims the selected item', () => {
    const firstPage = Array.from({ length: 300 }, (_, index) => libraryItem(`seed-${String(index).padStart(3, '0')}`, `2026-06-01T00:00:${String(index % 60).padStart(2, '0')}Z`));
    const seeded = resolve({ library: firstPage, libraryNextCursor: 'cursor-2' });
    expect(seeded.selectedLibraryId).toBe('seed-000');
    const nextPage = Array.from({ length: 60 }, (_, index) => libraryItem(`page2-${index}`, `2026-05-01T00:00:${String(index % 60).padStart(2, '0')}Z`));

    const appended = workspaceReducer(seeded, { type: 'library/pageAppended', items: nextPage, nextCursor: 'cursor-3' });

    expect(appended.library.some((item) => item.id === 'seed-000')).toBe(false);
    expect(appended.selectedLibraryId).toBe('seed-060');
  });

  it('keeps the selected item highlighted when a page append does not trim it', () => {
    const appended = workspaceReducer(hydrated, {
      type: 'library/pageAppended', items: [libraryItem('lib-c', '2026-05-20T00:00:00Z')], nextCursor: 'cursor-3',
    });
    expect(appended.selectedLibraryId).toBe(hydrated.selectedLibraryId);
  });

  it('appends and de-duplicates the next jobs page for the downloads feed', () => {
    const withJobs = resolve({ jobs: [job], jobsNextCursor: 'jobs-cursor-2' });
    const appended = workspaceReducer(withJobs, {
      type: 'jobs/pageAppended',
      jobs: [job, { id: 'job-2', source_url: 'https://example.test/2', status: 'completed' } as DownloadJob],
      nextCursor: null,
    });
    expect(appended.jobs.map((entry) => entry.id)).toEqual(['job-1', 'job-2']);
    expect(appended.jobsNextCursor).toBeNull();
  });
});

describe('downloads feed paging window', () => {
  function jobRow(id: string, createdAt: string | null, extra: Partial<DownloadJob> = {}): DownloadJob {
    return { id, source_url: `https://example.test/${id}`, status: 'queued', created_at: createdAt, ...extra } as DownloadJob;
  }

  const hydrated = resolve({ jobs: [jobRow('job-new', '2026-06-10T00:00:00Z'), jobRow('job-mid', '2026-06-05T00:00:00Z')], jobsNextCursor: 'jobs-cursor-2' });

  it('ignores a realtime update for a job beyond the loaded window instead of prepending it', () => {
    const event = {
      type: 'job_started',
      payload: { user_id: user.id, job: jobRow('job-old', '2026-05-01T00:00:00Z', { status: 'running' }) },
    } as AppEvent;
    const next = reconcileWorkspaceEvent(hydrated, event);
    expect(next.jobs.map((entry) => entry.id)).toEqual(['job-new', 'job-mid']);
    expect(next).toBe(hydrated);
  });

  it('prepends a freshly queued job that sorts before the feed head', () => {
    const event = {
      type: 'job_queued',
      payload: { user_id: user.id, job: jobRow('job-fresh', '2026-06-20T00:00:00Z') },
    } as AppEvent;
    const next = reconcileWorkspaceEvent(hydrated, event);
    expect(next.jobs.map((entry) => entry.id)).toEqual(['job-fresh', 'job-new', 'job-mid']);
  });

  it('updates a windowed job in place without reordering the feed', () => {
    const event = {
      type: 'job_progress',
      payload: { user_id: user.id, job: { id: 'job-mid', status: 'running', progress: 40 } },
    } as AppEvent;
    const next = reconcileWorkspaceEvent(hydrated, event);
    expect(next.jobs.map((entry) => entry.id)).toEqual(['job-new', 'job-mid']);
    expect(next.jobs[1]).toMatchObject({ status: 'running', progress: 40, created_at: '2026-06-05T00:00:00Z' });
  });

  it('accepts any job into an empty feed', () => {
    const empty = { ...hydrated, jobs: [] };
    const next = workspaceReducer(empty, { type: 'jobs/upsert', job: jobRow('job-old', '2026-05-01T00:00:00Z') });
    expect(next.jobs.map((entry) => entry.id)).toEqual(['job-old']);
  });

  it('still honours a deliberate selection when the job lives beyond the window', () => {
    const next = workspaceReducer(hydrated, {
      type: 'jobs/upsert',
      job: jobRow('job-old', '2026-05-01T00:00:00Z'),
      select: true,
    });
    expect(next.jobs.map((entry) => entry.id)).toEqual(['job-new', 'job-mid']);
    expect(next.selectedJobId).toBe('job-old');
  });
});

function playback(itemId: string, overrides: Partial<PlaybackProgress> = {}): PlaybackProgress {
  return {
    id: `pb-${itemId}`,
    item_id: itemId,
    item: { id: itemId, title: `Item ${itemId}`, status: 'available' } as LibraryItem,
    position_seconds: 100,
    duration_seconds: 600,
    completed: false,
    ...overrides,
  } as PlaybackProgress;
}

describe('workspaceReducer continue watching', () => {
  it('hydrates continue watching from the authenticated bundle', () => {
    const entries = [playback('lib-1'), playback('lib-2')];
    const state = resolve({ continueWatching: entries });
    expect(state.continueWatching).toEqual(entries);
  });

  it('starts continue watching empty and resets it per user and on reset', () => {
    expect(createInitialWorkspaceState().continueWatching).toEqual([]);
    const userA = resolve({ continueWatching: [playback('lib-1')] });
    const userB = resolve({ currentUser: otherUser, continueWatching: null }, userA);
    expect(userB.continueWatching).toEqual([]);
    expect(workspaceReducer(userA, { type: 'authenticated/reset' }).continueWatching).toEqual([]);
  });

  it('preserves last-known continue watching for the same user when the bundle omits it', () => {
    const userA = resolve({ continueWatching: [playback('lib-1')] });
    const reauth = resolve({ continueWatching: null }, userA);
    expect(reauth.continueWatching).toEqual([playback('lib-1')]);
  });

  it('prepends and de-duplicates a continue-watching checkpoint', () => {
    const seeded = { ...createInitialWorkspaceState(), continueWatching: [playback('lib-1'), playback('lib-2')] };
    const advanced = workspaceReducer(seeded, { type: 'continueWatching/checkpoint', progress: playback('lib-2', { position_seconds: 250 }) });
    expect(advanced.continueWatching.map((entry) => entry.item_id)).toEqual(['lib-2', 'lib-1']);
    expect(advanced.continueWatching[0].position_seconds).toBe(250);
  });

  it('drops a completed or reset checkpoint from continue watching', () => {
    const seeded = { ...createInitialWorkspaceState(), continueWatching: [playback('lib-1'), playback('lib-2')] };
    const completed = workspaceReducer(seeded, { type: 'continueWatching/checkpoint', progress: playback('lib-1', { completed: true }) });
    expect(completed.continueWatching.map((entry) => entry.item_id)).toEqual(['lib-2']);
    const rewound = workspaceReducer(seeded, { type: 'continueWatching/checkpoint', progress: playback('lib-2', { position_seconds: 0 }) });
    expect(rewound.continueWatching.map((entry) => entry.item_id)).toEqual(['lib-1']);
  });

  it('keeps the previous title when a checkpoint for the same item arrives without one (P-I1)', () => {
    const titled = playback('lib-1', { title: { id: 'title-1', type: 'episode', name: 'Show S2E4' } as PlaybackProgress['title'] });
    const seeded = { ...createInitialWorkspaceState(), continueWatching: [titled] };
    const titleless = workspaceReducer(seeded, {
      type: 'continueWatching/checkpoint',
      progress: playback('lib-1', { position_seconds: 250, title: null }),
    });
    expect(titleless.continueWatching[0].title).toEqual(titled.title);
  });

  it('removes a cleared item from continue watching', () => {
    const seeded = { ...createInitialWorkspaceState(), continueWatching: [playback('lib-1'), playback('lib-2')] };
    const cleared = workspaceReducer(seeded, { type: 'continueWatching/remove', itemId: 'lib-1' });
    expect(cleared.continueWatching.map((entry) => entry.item_id)).toEqual(['lib-2']);
  });
});

describe('CollectionRequestGate', () => {
  it('rejects a response after logout or account replacement', () => {
    const gate = new CollectionRequestGate();
    gate.beginSession();
    const userARequest = gate.begin('library');
    gate.invalidateSession();
    gate.beginSession();
    expect(gate.isCurrent(userARequest)).toBe(false);
  });

  it('makes collection requests latest-wins within one session', () => {
    const gate = new CollectionRequestGate();
    gate.beginSession();
    const first = gate.begin('jobs');
    const second = gate.begin('jobs');
    expect(gate.isCurrent(first)).toBe(false);
    expect(gate.isCurrent(second)).toBe(true);
  });

  it('invalidates reconnect recovery when realtime drops again', () => {
    const gate = new CollectionRequestGate();
    gate.beginSession();
    gate.invalidate('jobs');
    const reconnectRecovery = gate.begin('jobs');
    gate.invalidate('jobs');
    expect(gate.isCurrent(reconnectRecovery)).toBe(false);
  });
});

describe('reconcileWorkspaceEvent', () => {
  const signedIn = { ...createInitialWorkspaceState(), currentUser: user };

  it.each([
    ['foreign', { user_id: 'user-2', job }],
    ['missing audience', { job }],
  ])('ignores %s realtime updates', (_label, payload) => {
    const event = { type: 'job_progress', payload } as AppEvent;
    expect(reconcileWorkspaceEvent(signedIn, event)).toBe(signedIn);
  });

  it('applies an update addressed to the current user', () => {
    const event = { type: 'job_progress', payload: { user_id: user.id, job } } as AppEvent;
    expect(reconcileWorkspaceEvent(signedIn, event).jobs).toEqual([job]);
  });

  it('preserves route-minted job artwork when progress events omit it', () => {
    const current = {
      ...signedIn,
      jobs: [{ ...job, artwork_url: '/api/artwork/remote/opaque' }],
    };
    const event = { type: 'job_progress', payload: { user_id: user.id, job: { id: job.id, progress: 45 } } } as AppEvent;
    expect(reconcileWorkspaceEvent(current, event).jobs[0]).toMatchObject({
      artwork_url: '/api/artwork/remote/opaque',
      progress: 45,
    });
  });

  it('applies a broadcast shared-library update without a user audience', () => {
    const item = { id: 'library-1', title: 'Shared item', status: 'available' } as LibraryItem;
    const event = { type: 'library_item_upserted', payload: { broadcast: true, item } } as AppEvent;
    expect(reconcileWorkspaceEvent(signedIn, event).library).toEqual([item]);
  });

  it('ignores queued late events after logout, including broadcasts', () => {
    const event = { type: 'library_item_upserted', payload: { broadcast: true, item: { id: 'late' } } } as AppEvent;
    expect(reconcileWorkspaceEvent(createInitialWorkspaceState(), event).library).toEqual([]);
  });
});

function manualScheduler() {
  let pending: (() => void) | null = null;
  return {
    schedule(callback: () => void) {
      pending = callback;
      return () => { pending = null; };
    },
    run() {
      const callback = pending;
      pending = null;
      callback?.();
    },
    get scheduled() {
      return pending !== null;
    },
  };
}

function progress(id: string, value: number): AppEvent {
  return { type: 'job_progress', payload: { user_id: user.id, job: { id, progress: value } } } as AppEvent;
}

describe('createWorkspaceEventCoalescer', () => {
  it('buffers high-frequency progress into a single batched apply after the interval', () => {
    const timer = manualScheduler();
    const apply = vi.fn();
    const coalescer = createWorkspaceEventCoalescer(apply, { schedule: timer.schedule });

    coalescer.handle(progress('job-1', 10));
    coalescer.handle(progress('job-2', 20));
    expect(apply).not.toHaveBeenCalled();
    expect(timer.scheduled).toBe(true);

    timer.run();
    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply.mock.calls[0][0]).toEqual([progress('job-1', 10), progress('job-2', 20)]);
  });

  it('keeps only the latest buffered progress per job', () => {
    const timer = manualScheduler();
    const apply = vi.fn();
    const coalescer = createWorkspaceEventCoalescer(apply, { schedule: timer.schedule });

    coalescer.handle(progress('job-1', 10));
    coalescer.handle(progress('job-1', 55));
    coalescer.handle(progress('job-1', 80));
    timer.run();

    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply.mock.calls[0][0]).toEqual([progress('job-1', 80)]);
  });

  it('applies a terminal event immediately, flushing that job’s buffered progress ahead of it', () => {
    const timer = manualScheduler();
    const apply = vi.fn();
    const coalescer = createWorkspaceEventCoalescer(apply, { schedule: timer.schedule });

    coalescer.handle(progress('job-1', 90));
    const completed = { type: 'job_completed', payload: { user_id: user.id, job: { id: 'job-1', status: 'completed' } } } as AppEvent;
    coalescer.handle(completed);

    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply.mock.calls[0][0]).toEqual([progress('job-1', 90), completed]);
    expect(coalescer.pendingCount()).toBe(0);
  });

  it('does not flush an unrelated job’s buffered progress when a terminal event arrives', () => {
    const timer = manualScheduler();
    const apply = vi.fn();
    const coalescer = createWorkspaceEventCoalescer(apply, { schedule: timer.schedule });

    coalescer.handle(progress('job-1', 30));
    coalescer.handle(progress('job-2', 40));
    const failed = { type: 'job_failed', payload: { user_id: user.id, job: { id: 'job-1', status: 'failed' } } } as AppEvent;
    coalescer.handle(failed);

    expect(apply.mock.calls[0][0]).toEqual([progress('job-1', 30), failed]);
    expect(coalescer.pendingCount()).toBe(1);
    timer.run();
    expect(apply.mock.calls[1][0]).toEqual([progress('job-2', 40)]);
  });

  it('applies list-shaped events immediately without buffering', () => {
    const timer = manualScheduler();
    const apply = vi.fn();
    const coalescer = createWorkspaceEventCoalescer(apply, { schedule: timer.schedule });

    const upserted = { type: 'library_item_upserted', payload: { user_id: user.id, item: { id: 'library-1' } } } as AppEvent;
    coalescer.handle(upserted);

    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply.mock.calls[0][0]).toEqual([upserted]);
    expect(timer.scheduled).toBe(false);
  });

  it('flushes at most once per interval for sustained progress', () => {
    const timer = manualScheduler();
    const apply = vi.fn();
    const coalescer = createWorkspaceEventCoalescer(apply, { schedule: timer.schedule });

    coalescer.handle(progress('job-1', 10));
    coalescer.handle(progress('job-1', 20));
    expect(timer.scheduled).toBe(true);
    timer.run();
    expect(apply).toHaveBeenCalledTimes(1);

    coalescer.handle(progress('job-1', 30));
    expect(timer.scheduled).toBe(true);
    timer.run();
    expect(apply).toHaveBeenCalledTimes(2);
    expect(apply.mock.calls[1][0]).toEqual([progress('job-1', 30)]);
  });

  it('drops buffered progress on cancel but replays it on flush', () => {
    const timer = manualScheduler();
    const applyForCancel = vi.fn();
    const cancelled = createWorkspaceEventCoalescer(applyForCancel, { schedule: timer.schedule });
    cancelled.handle(progress('job-1', 10));
    cancelled.cancel();
    timer.run();
    expect(applyForCancel).not.toHaveBeenCalled();
    expect(cancelled.pendingCount()).toBe(0);

    const applyForFlush = vi.fn();
    const flushed = createWorkspaceEventCoalescer(applyForFlush, { schedule: timer.schedule });
    flushed.handle(progress('job-2', 15));
    flushed.flush();
    expect(applyForFlush).toHaveBeenCalledTimes(1);
    expect(applyForFlush.mock.calls[0][0]).toEqual([progress('job-2', 15)]);
  });
});

describe('workspaceReducer realtime batch', () => {
  const signedIn = { ...createInitialWorkspaceState(), currentUser: user };

  it('reconciles a batch of events in order within one dispatch', () => {
    const events = [
      { type: 'job_progress', payload: { user_id: user.id, job: { id: 'job-1', progress: 30 } } },
      { type: 'job_completed', payload: { user_id: user.id, job: { id: 'job-1', status: 'completed', progress: 100 } } },
    ] as AppEvent[];

    const next = workspaceReducer(signedIn, { type: 'realtime/eventsBatch', events });
    expect(next.jobs).toHaveLength(1);
    expect(next.jobs[0]).toMatchObject({ id: 'job-1', status: 'completed', progress: 100 });
  });

  it('returns the same state for an empty batch', () => {
    const next = workspaceReducer(signedIn, { type: 'realtime/eventsBatch', events: [] });
    expect(next).toBe(signedIn);
  });
});
