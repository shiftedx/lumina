import { use, useReducer, useState } from 'react';
import { act, fireEvent, render as baseRender, screen, waitFor, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider, useToast } from './ui';
import type { LibraryItem, SourceAutomation, UserProfile, UserTag, YouTubeSearchResult } from './types';
import { ApiRequestError, keepaliveWritesSettled, updateRemotePlaybackProgress } from './api';
import { createInitialWorkspaceState, workspaceReducer } from './workspace';
import LuminaApp from './LuminaApp';
import { ChannelsSurface } from './features/channels/ChannelsSurface';
import { followOutcome } from './subscriptionFeed';
import { useUnsavedChanges } from './features/settings/unsavedChanges';
import { watchSurface } from './test/watchSurface';
import { cancelSpeculativeStart, claimSpeculativeStart, forgetPlaybackWarmup } from './playbackPrefetch';
import type { AcquisitionFormat } from './mediaAcquisition';
import { takePlayIntent } from './perfMetrics';
import { movieSummary, titleDetail, titlePage } from './test/galleryFixtures';
import { docFixture } from './features/titles/editor/editorFixtures';
import { forgetTitles } from './features/gallery/titleCache';
import { CHANNEL_ID, channelPage } from './test/remoteFixtures';
import { recoEntry } from './test/recoFixtures';
import { openAppPath } from './features/channels/channelMention';
import { openMemberPicker } from './app/commands';
import { resetDeviceRing } from './features/auth/deviceRing';
import { recordRecoOpen } from './features/reco/recoEvents';

// The app raises its shell messages as toasts, which need the provider main.tsx supplies.
const render = (ui: ReactElement, options: Parameters<typeof baseRender>[1] = {}) => baseRender(ui, { wrapper: ToastProvider, ...options });

const mocks = vi.hoisted(() => ({
  useAuthenticatedWorkspace: vi.fn(),
  listContinueWatching: vi.fn(),
  searchLibrary: vi.fn(),
  youtubeSearch: vi.fn(),
  getUpNext: vi.fn(),
  previewUrl: vi.fn(),
  createJob: vi.fn(),
  getLibraryItem: vi.fn(),
  getPlaybackProgress: vi.fn(),
  listUsers: vi.fn(),
  updateUser: vi.fn(),
  listLibraryTags: vi.fn(),
  createLibraryTag: vi.fn(),
  deleteLibraryTag: vi.fn(),
  listLibraryNotes: vi.fn(),
  updateLibraryItemVisibility: vi.fn(),
  runAutomation: vi.fn(),
  listAutomations: vi.fn(),
  refreshFollows: vi.fn(),
  retryJob: vi.fn(),
  retryAcquisitionEntry: vi.fn(),
  getTitle: vi.fn(),
  listSeasonEpisodes: vi.fn(),
  listSimilarTitles: vi.fn(),
  getLocalPlaybackOptions: vi.fn(),
  startLocalPlaybackSession: vi.fn(),
  stopLocalPlaybackSession: vi.fn(),
  listTitles: vi.fn(),
  listLibrary: vi.fn(),
  getLibrarySections: vi.fn(),
  getHomeRecommendations: vi.fn(),
  getPopularDiscovery: vi.fn(),
  markNavigation: vi.fn(),
  logoutSession: vi.fn(),
  getChannelPage: vi.fn(),
  resolveChannel: vi.fn(),
  listLibraryChannels: vi.fn(),
  sendRecoEvents: vi.fn(),
  switchMember: vi.fn(),
  getDeviceMembers: vi.fn(),
  createAutomation: vi.fn(),
  getTitleMetadata: vi.fn(),
}));

const paletteChunk = vi.hoisted(() => ({ gate: null as Promise<void> | null }));
vi.mock('./features/palette/CommandPalette', async (importOriginal) => {
  const original = await importOriginal<typeof import('./features/palette/CommandPalette')>();
  const Original = original.default;
  // Stands in for the lazy chunk still downloading: the real palette mounts only once the gate is released.
  return { ...original, default: (props: Parameters<typeof Original>[0]) => { if (paletteChunk.gate) use(paletteChunk.gate); return <Original {...props} />; } };
});
vi.mock('./perfMetrics', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./perfMetrics')>();
  mocks.markNavigation.mockImplementation(actual.markNavigation);
  return { ...actual, markNavigation: mocks.markNavigation };
});

vi.mock('./playbackPrefetch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./playbackPrefetch')>();
  return { ...actual, claimSpeculativeStart: vi.fn(actual.claimSpeculativeStart), cancelSpeculativeStart: vi.fn(actual.cancelSpeculativeStart), forgetPlaybackWarmup: vi.fn(actual.forgetPlaybackWarmup) };
});

vi.mock('./workspace', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./workspace')>()),
  useAuthenticatedWorkspace: mocks.useAuthenticatedWorkspace,
}));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  listContinueWatching: mocks.listContinueWatching,
  searchLibrary: mocks.searchLibrary,
  youtubeSearch: mocks.youtubeSearch,
  getUpNext: mocks.getUpNext,
  previewUrl: mocks.previewUrl,
  createJob: mocks.createJob,
  getLibraryItem: mocks.getLibraryItem,
  getPlaybackProgress: mocks.getPlaybackProgress,
  listUsers: mocks.listUsers,
  updateUser: mocks.updateUser,
  listLibraryTags: mocks.listLibraryTags,
  createLibraryTag: mocks.createLibraryTag,
  deleteLibraryTag: mocks.deleteLibraryTag,
  listLibraryNotes: mocks.listLibraryNotes,
  updateLibraryItemVisibility: mocks.updateLibraryItemVisibility,
  runAutomation: mocks.runAutomation,
  listAutomations: mocks.listAutomations,
  refreshFollows: mocks.refreshFollows,
  retryJob: mocks.retryJob,
  retryAcquisitionEntry: mocks.retryAcquisitionEntry,
  getTitle: mocks.getTitle,
  getTitleMetadata: mocks.getTitleMetadata,
  listSeasonEpisodes: mocks.listSeasonEpisodes,
  listSimilarTitles: mocks.listSimilarTitles,
  getLocalPlaybackOptions: mocks.getLocalPlaybackOptions,
  startLocalPlaybackSession: mocks.startLocalPlaybackSession,
  stopLocalPlaybackSession: mocks.stopLocalPlaybackSession,
  listTitles: mocks.listTitles,
  listLibrary: mocks.listLibrary,
  getLibrarySections: mocks.getLibrarySections,
  getHomeRecommendations: mocks.getHomeRecommendations,
  getPopularDiscovery: mocks.getPopularDiscovery,
  logoutSession: mocks.logoutSession,
  getChannelPage: mocks.getChannelPage,
  resolveChannel: mocks.resolveChannel,
  listLibraryChannels: mocks.listLibraryChannels,
  sendRecoEvents: mocks.sendRecoEvents,
  switchMember: mocks.switchMember,
  getDeviceMembers: mocks.getDeviceMembers,
  createAutomation: mocks.createAutomation,
}));

const user: UserProfile = {
  id: 'user-1',
  username: 'one',
  display_name: 'One Person',
  role: 'viewer',
  is_active: true,
};

const libraryItem: LibraryItem = {
  id: 'library-1',
  remote_id: 'remote-1',
  title: 'Film in the vault',
  uploader: 'Archive Channel',
  webpage_url: 'https://example.test/watch/film',
  status: 'available',
  metadata_json: {},
};

function upNextSnapshot(items: YouTubeSearchResult[]) {
  return { items, categories: [], state: 'ready' as const, refreshing: false, stale: false, error: null };
}

let mobile = false;
let drawerOnly = false;
let workspaceState: ReturnType<typeof createInitialWorkspaceState>;

beforeEach(() => {
  window.history.replaceState(null, "", '/');
  mobile = false;
  drawerOnly = false;
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(max-width: 680px)' ? mobile : query === '(max-width: 960px)' ? mobile || drawerOnly : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  Object.defineProperty(window, 'scrollTo', { configurable: true, value: vi.fn() });
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true,
    value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(performance.now()), 0),
  });

  workspaceState = createInitialWorkspaceState();
  workspaceState.preferences.sidebarCollapsed = false;
  Object.assign(workspaceState, {
    authStage: 'ready',
    currentUser: user,
    jobs: [],
    jobsState: 'empty',
    library: [libraryItem],
    libraryRecent: [libraryItem],
    libraryState: 'ready',
    settingsHydrated: false,
  });
  mocks.useAuthenticatedWorkspace.mockReturnValue({
    state: workspaceState,
    dispatch: vi.fn(),
    loadAuthenticated: vi.fn(),
    loadPublic: vi.fn(),
    retryCollection: vi.fn(),
    refreshLibraryCollection: vi.fn(),
    loadMoreLibrary: vi.fn(),
    loadMoreJobs: vi.fn(),
    captureSessionToken: vi.fn(() => 1),
    isSessionTokenCurrent: vi.fn(() => true),
    reset: vi.fn(),
    holdEvents: vi.fn(),
  });
  resetDeviceRing();
  mocks.getDeviceMembers.mockResolvedValue([]);
  mocks.sendRecoEvents.mockResolvedValue(undefined);
  mocks.listContinueWatching.mockResolvedValue([]);
  mocks.searchLibrary.mockResolvedValue({ items: [libraryItem] });
  mocks.youtubeSearch.mockResolvedValue({ items: [] });
  mocks.getUpNext.mockResolvedValue(upNextSnapshot([]));
  mocks.getHomeRecommendations.mockResolvedValue(upNextSnapshot([]));
  mocks.getPopularDiscovery.mockRejectedValue(new Error('Popular is unavailable.'));
  mocks.previewUrl.mockResolvedValue({ kind: 'playlist', title: 'Channel', entries: [], raw: {} });
  mocks.createJob.mockResolvedValue({ id: 'job-acquisition', source_url: 'https://example.test/video-acquisition', status: 'queued' });
  mocks.getLibraryItem.mockImplementation(async (id: string) => ({ ...libraryItem, id }));
  mocks.getPlaybackProgress.mockResolvedValue(null);
  mocks.listUsers.mockResolvedValue([]);
  mocks.listLibraryTags.mockResolvedValue([]);
  mocks.listLibraryNotes.mockResolvedValue([]);
  mocks.runAutomation.mockResolvedValue({ id: 'run-1', automation_id: 'channel-1', status: 'partial', queued_items: 1, failed_items: 1 });
  mocks.listAutomations.mockResolvedValue([]);
  // As the real requests do in jsdom: no server.
  mocks.getLocalPlaybackOptions.mockRejectedValue(new TypeError('offline'));
  mocks.startLocalPlaybackSession.mockRejectedValue(new TypeError('offline'));
  mocks.stopLocalPlaybackSession.mockResolvedValue(undefined);
  // The Library's own requests (library gallery): an empty landing, the YouTube tab listing the workspace's items.
  window.localStorage.clear();
  mocks.listTitles.mockResolvedValue(titlePage([]));
  mocks.listLibrary.mockImplementation(async () => ({ items: workspaceState.library, next_cursor: null }));
  mocks.getChannelPage.mockResolvedValue(channelPage());
  mocks.resolveChannel.mockResolvedValue({ provider: 'youtube', channel_id: CHANNEL_ID });
  mocks.listLibraryChannels.mockResolvedValue([]);
  mocks.getLibrarySections.mockResolvedValue({ movies: 0, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 1, recordings: 0, deleted: 0 });
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals(); // even when an assertion fails first, no stubbed fetch leaks into later tests
});

/** The shell's one search: the top bar trigger opens the palette, whose combobox takes the typing. */
/** jsdom has no Escape-to-cancel for a native dialog: the cancel event is what the browser raises. */
function cancelDialog() {
  act(() => { document.querySelector('dialog[open]')!.dispatchEvent(new Event('cancel', { cancelable: true })); });
}

/** The profile menu: open it from the account trigger and pick an entry. */
async function chooseFromAccountMenu(browser: ReturnType<typeof userEvent.setup>, label: string) {
  await browser.click(screen.getByRole('button', { name: 'One Person, account menu' }));
  await browser.click(await screen.findByRole('menuitem', { name: label, hidden: true }));
}

async function openPalette(browser: ReturnType<typeof userEvent.setup>) {
  await browser.click(screen.getByRole('button', { name: /^Search Lumina, / }));
  return screen.findByRole('combobox', { name: 'Search Lumina' });
}

describe('browser routes', () => {
  it('reopens a Library watch deep link, and back/forward walk the route stack', async () => {
    const browser = userEvent.setup();
    window.history.replaceState(null, '', '/watch/library/library-1');
    render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(mocks.getLibraryItem).toHaveBeenCalledWith('library-1');
    expect(window.location.pathname).toBe('/watch/library/library-1');

    await browser.click(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Library' }));
    await screen.findByRole('heading', { level: 1, name: 'Library' });
    expect(window.location.pathname).toBe('/library');

    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe('/watch/library/library-1'));
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());

    window.history.forward();
    await screen.findByRole('heading', { level: 1, name: 'Library' });
    expect(window.location.pathname).toBe('/library');
  });

  it('asks for the resume point beside the item, not after it, and loads the item once', async () => {
    let resolveItem: ((item: LibraryItem) => void) | undefined;
    mocks.getLibraryItem.mockImplementationOnce(() => new Promise((resolve) => { resolveItem = resolve; }));
    window.history.replaceState(null, '', '/watch/library/library-1');
    render(<LuminaApp />);
    await waitFor(() => expect(mocks.getLibraryItem).toHaveBeenCalledWith('library-1'));
    expect(mocks.getPlaybackProgress).toHaveBeenCalledWith('library-1'); // the item has not answered yet
    resolveItem?.(libraryItem);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(mocks.getLibraryItem).toHaveBeenCalledTimes(1);
    expect(mocks.getPlaybackProgress).toHaveBeenCalledTimes(1);
  });

  it('keeps the position when the open item is reopened without a time, and seeks when its link time is asked for again', async () => {
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: 'library-1', position_seconds: 30, duration_seconds: 120, completed: false });
    window.history.replaceState(null, '', '/watch/library/library-1?t=42');
    render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    const player = document.querySelector('video') as HTMLVideoElement;
    let position = 50;
    Object.defineProperty(player, 'currentTime', { configurable: true, get: () => position, set: (value: number) => { position = value; } });

    window.history.pushState(null, '', '/watch/library/library-1');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await waitFor(() => expect(window.location.search).toBe(''));
    await new Promise((resolve) => { setTimeout(resolve, 20); });
    expect(position).toBe(50);

    window.history.pushState(null, '', '/watch/library/library-1?t=42');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await waitFor(() => expect(position).toBe(42));
    expect(document.querySelector('video')).toBe(player);
  });

  it('applies a link time once: the address drops it, and Back to the watch page does not seek there again', async () => {
    const browser = userEvent.setup();
    window.history.replaceState(null, '', '/watch/library/library-1?t=42');
    render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    await waitFor(() => expect(window.location.search).toBe(''));
    const player = document.querySelector('video') as HTMLVideoElement;
    let position = 50;
    Object.defineProperty(player, 'currentTime', { configurable: true, get: () => position, set: (value: number) => { position = value; } });

    await browser.click(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Library' }));
    await screen.findByRole('heading', { level: 1, name: 'Library' });
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe('/watch/library/library-1'));
    await new Promise((resolve) => { setTimeout(resolve, 20); });
    expect(position).toBe(50);
    expect(window.location.search).toBe('');

    // A new link time for the open item still seeks, and leaves the address clean.
    window.history.pushState(null, '', '/watch/library/library-1?t=42');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await waitFor(() => expect(position).toBe(42));
    await waitFor(() => expect(window.location.search).toBe(''));
  });

  it('a later Play of the same item after the player closed ignores the old link time', async () => {
    const browser = userEvent.setup();
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: 'library-1', position_seconds: 30, duration_seconds: 120, completed: false });
    window.history.replaceState(null, '', '/watch/library/library-1?t=42');
    render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    await browser.click(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Home' }));
    await browser.click(await screen.findByRole('button', { name: 'Close player' }));
    await waitFor(() => expect(document.querySelector('video')).toBeNull());

    await browser.click(screen.getByRole('button', { name: 'Film in the vault, Archive Channel' }));
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    const player = document.querySelector('video') as HTMLVideoElement;
    Object.defineProperty(player, 'duration', { configurable: true, value: 120 });
    fireEvent.loadedMetadata(player);
    expect(player.currentTime).toBe(30);
  });

  it('a Play whose item fails to load leaves no pending Play for the next first frame', async () => {
    takePlayIntent();
    mocks.getLibraryItem.mockRejectedValueOnce(new TypeError('offline'));
    window.history.replaceState(null, '', '/watch/library/library-9');
    render(<LuminaApp />);
    await waitFor(() => expect(window.location.pathname).toBe('/'));
    expect(takePlayIntent()).toBeNull();
  });

  it('a Play with a link time calls off the early start, and a plain Play keeps it for the player', async () => {
    window.history.replaceState(null, '', '/watch/library/library-1?t=42');
    const view = render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(vi.mocked(cancelSpeculativeStart)).toHaveBeenCalledWith(); // any unclaimed early session, whichever item it is for
    expect(vi.mocked(claimSpeculativeStart)).not.toHaveBeenCalled();
    view.unmount();

    window.history.replaceState(null, '', '/watch/library/library-2');
    render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(vi.mocked(claimSpeculativeStart)).toHaveBeenCalledWith('library-2');
  });

  it('Play on the title page keeps the early session for the player: closing the page stops nothing', async () => {
    const early = { session_id: 'early', mode: 'remux', playback_url: '/api/playback-sessions/early/index.m3u8', start: 754, kind: 'remux' };
    mocks.getLocalPlaybackOptions.mockResolvedValue({ mode: 'remux', reason: null, facts: { duration: 6720 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 1 });
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: 'library-1', position_seconds: 754, duration_seconds: 6720, completed: false });
    mocks.startLocalPlaybackSession.mockResolvedValue(early); // the server hands the player's start the same session
    mocks.getTitle.mockResolvedValue({
      id: 'movie-1', type: 'movie', name: 'Northern Lantern', genres: [], added_at: '2026-09-01T00:00:00Z', play_item_id: 'library-1',
      user_data: { played: false, is_favorite: false, position_seconds: 754, resume_item_id: 'library-1' },
      studios: [], provider_ids: {}, people: [], versions: [], extras: [], children: [], has_recap: false, play_next: null,
    });
    mocks.listSimilarTitles.mockResolvedValue([]);
    window.history.replaceState(null, '', '/title/movie-1');
    render(<LuminaApp />);
    const play = await screen.findByRole('button', { name: 'Resume at 12:34' });
    await waitFor(() => expect(mocks.getLocalPlaybackOptions).toHaveBeenCalledWith('library-1'));
    fireEvent.pointerEnter(play);
    await waitFor(() => expect(mocks.startLocalPlaybackSession).toHaveBeenCalledWith('library-1', 754), { timeout: 2000 });
    fireEvent.click(play);
    await waitFor(() => expect(window.location.pathname).toBe('/watch/library/library-1'));
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(vi.mocked(claimSpeculativeStart)).toHaveBeenCalledWith('library-1');
    expect(vi.mocked(cancelSpeculativeStart)).toHaveBeenCalledWith('library-1'); // the title page's unmount
    expect(vi.mocked(claimSpeculativeStart).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(cancelSpeculativeStart).mock.invocationCallOrder.at(-1) ?? 0);
    await new Promise((resolve) => setTimeout(resolve, 1000));
    expect(mocks.stopLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('times the title page hero from the poster click, before the page renders', async () => {
    const browser = userEvent.setup();
    mocks.listTitles.mockResolvedValue(titlePage([movieSummary()]));
    mocks.getTitle.mockResolvedValue(titleDetail(movieSummary()));
    mocks.listSimilarTitles.mockResolvedValue([]);
    window.history.replaceState(null, '', '/library/movies');
    render(<LuminaApp />);
    const poster = await screen.findByRole('button', { name: /^Northern Lantern, 2019/ });
    const beforePage: boolean[] = [];
    mocks.markNavigation.mockClear();
    mocks.markNavigation.mockImplementation(() => { beforePage.push(document.querySelector('.g-route-loading, .t-page') === null); });
    await browser.click(poster);
    await waitFor(() => expect(window.location.pathname).toBe('/title/movie-1'));
    await screen.findByRole('heading', { level: 1, name: 'Northern Lantern' });
    expect(beforePage).toEqual([true]); // once, at the click: the page's own commit does not restart the clock
  });

  it('a member switch never shows the next member the last member\'s cached title marks', async () => {
    forgetTitles(); // an earlier test's open of movie-1 is still fresh
    const other: UserProfile = { ...user, id: 'user-2', username: 'two', display_name: 'Two Person' };
    mocks.listTitles.mockResolvedValue(titlePage([movieSummary()]));
    mocks.listSimilarTitles.mockResolvedValue([]);
    mocks.getTitle
      .mockResolvedValueOnce(titleDetail(movieSummary(), { user_data: { played: false, is_favorite: true, position_seconds: 754, resume_item_id: 'item-movie-1' } }))
      .mockResolvedValue(titleDetail(movieSummary(), { user_data: { played: false, is_favorite: false, position_seconds: 0 } }));
    const go = (path: string) => { window.history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')); };
    window.history.replaceState(null, '', '/title/movie-1');
    const view = render(<LuminaApp />);
    await screen.findByRole('button', { name: /^Resume at 12:34/ });
    go('/library/movies');
    await screen.findByRole('heading', { level: 1, name: 'Movies' });
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...workspace, state: { ...workspaceState, currentUser: other } });
    view.rerender(<LuminaApp />);
    go('/title/movie-1');
    await screen.findByRole('heading', { level: 1, name: 'Northern Lantern' });
    await waitFor(() => expect(mocks.getTitle).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('button', { name: /^Resume at/ })).toBeNull();
  });

  it('sign-out stops the early playback session before the session itself ends', async () => {
    const browser = userEvent.setup();
    let stopped = (): void => undefined;
    vi.mocked(forgetPlaybackWarmup).mockReturnValueOnce(new Promise<void>((resolve) => { stopped = resolve; }));
    mocks.logoutSession.mockResolvedValue(undefined);
    window.history.replaceState(null, '', '/settings/account');
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: 'Sign out' }));
    expect(forgetPlaybackWarmup).toHaveBeenCalled();
    await new Promise((resolve) => { setTimeout(resolve, 20); });
    expect(mocks.logoutSession).not.toHaveBeenCalled();
    stopped();
    await waitFor(() => expect(mocks.logoutSession).toHaveBeenCalledTimes(1));
  });

  it('opens a title deep link, keeps the season in the address, and returns to its lens', async () => {
    const browser = userEvent.setup();
    const season = (n: number) => ({ id: `season-${n}`, type: 'season', name: `Season ${n}`, index_number: n, genres: [], added_at: '2026-09-01T00:00:00Z', user_data: { played: false, is_favorite: false, position_seconds: 0 } });
    mocks.getTitle.mockResolvedValue({
      id: 'series-1', type: 'series', name: 'Harbor Lights', genres: [], added_at: '2026-09-01T00:00:00Z', user_data: { played: false, is_favorite: false, position_seconds: 0 },
      studios: [], provider_ids: {}, people: [], versions: [], extras: [], children: [season(1), season(2)], has_recap: false, play_next: null,
    });
    mocks.listSeasonEpisodes.mockResolvedValue([]);
    mocks.listSimilarTitles.mockResolvedValue([]);
    window.history.replaceState(null, '', '/title/series-1?season=2');
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1, name: 'Harbor Lights' });
    expect(mocks.getTitle).toHaveBeenCalledWith('series-1');
    expect(screen.getByRole('tab', { name: 'Season 2' }).getAttribute('aria-selected')).toBe('true');
    await browser.click(screen.getByRole('tab', { name: 'Season 1' }));
    await waitFor(() => expect(`${window.location.pathname}${window.location.search}`).toBe('/title/series-1?season=1'));
    await browser.click(screen.getByRole('button', { name: 'Back to Shows' }));
    await waitFor(() => expect(window.location.pathname).toBe('/library/shows'));
    window.history.back();
    await waitFor(() => expect(window.location.search).toBe('?season=1'));
    await screen.findByRole('heading', { level: 1, name: 'Harbor Lights' });
  });

  it('an editor tab change replaces the history entry, and Back between same-title editor entries never asks to leave', async () => {
    const browser = userEvent.setup();
    function DirtyDraft() { useUnsavedChanges(true, 'this title'); return null; }
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    mocks.getTitleMetadata.mockResolvedValue(docFixture('movie'));
    window.history.replaceState(null, '', '/title/t1/edit');
    render(<><LuminaApp /><DirtyDraft /></>);
    await screen.findByRole('heading', { name: 'Severance' });
    const length = window.history.length;
    await browser.click(screen.getByRole('radio', { name: 'People' }));
    await waitFor(() => expect(`${window.location.pathname}${window.location.search}`).toBe('/title/t1/edit?tab=people'));
    expect(window.history.length).toBe(length);
    window.history.pushState(null, '', '/title/t1/edit');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await waitFor(() => expect((screen.getByRole('radio', { name: 'Details' }) as HTMLInputElement).checked).toBe(true));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('asks once before a header search or a Library open leaves Settings with unsaved edits', async () => {
    const browser = userEvent.setup();
    function DirtyAiForm() { useUnsavedChanges(true, 'AI & models'); return null; }
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    window.history.replaceState(null, '', '/settings/account');
    render(<><LuminaApp /><DirtyAiForm /></>);
    await screen.findByRole('heading', { level: 1, name: 'Settings' });

    const search = await openPalette(browser);
    await browser.type(search, 'mars');
    await browser.keyboard('{Meta>}{Enter}{/Meta}'); // search everything in Explore
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(mocks.youtubeSearch).not.toHaveBeenCalledWith(expect.objectContaining({ query: 'mars', limit: 30 }));
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeTruthy();

    mocks.searchLibrary.mockResolvedValue({ items: [libraryItem], matches: [{ kind: 'library', id: 'library-1', title: 'Film in the vault', subtitle: 'Archive Channel', score: 1, lexical_score: 1, semantic_score: 0, match_mode: 'lexical', item: libraryItem }] });
    await browser.type(await openPalette(browser), 'vault');
    await browser.click(await screen.findByRole('option', { name: /Film in the vault/ }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(mocks.getLibraryItem).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe('/settings/account');
    confirm.mockRestore();
  });

  it('canonicalizes an unknown path to Home without adding a history entry', async () => {
    window.history.replaceState(null, '', '/not-a-route');
    const before = window.history.length;
    render(<LuminaApp />);
    await waitFor(() => expect(window.location.pathname).toBe('/'));
    expect(window.history.length).toBe(before);
  });

  it('/music opens the Music lens and replaces the address', async () => {
    window.history.replaceState(null, '', '/music');
    const length = window.history.length;
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1, name: 'Music' });
    expect(window.location.pathname).toBe('/library/music');
    expect(window.history.length).toBe(length);
    // Music is not a sidebar destination any more: Library is the current page.
    expect(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Library' }).getAttribute('aria-current')).toBe('page');
  });

  it('Back from a title opened on the All landing returns to All', async () => {
    const browser = userEvent.setup();
    mocks.getLibrarySections.mockResolvedValue({ movies: 1, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 0, recordings: 0, deleted: 0 });
    mocks.listTitles.mockResolvedValue(titlePage([movieSummary()]));
    mocks.getTitle.mockResolvedValue(titleDetail(movieSummary()));
    mocks.listSimilarTitles.mockResolvedValue([]);
    window.history.replaceState(null, '', '/library');
    render(<LuminaApp />);
    const chapter = await waitFor(() => {
      const found = document.getElementById('g-chapter-movies');
      if (!found) throw new Error('the Movies chapter is not rendered yet');
      return found;
    });
    await browser.click(await within(chapter).findByRole('button', { name: /^Northern Lantern, 2019/ }));
    await screen.findByRole('heading', { level: 1, name: 'Northern Lantern' });
    await browser.click(document.querySelector('.t-back') as HTMLElement);
    await waitFor(() => expect(window.location.pathname).toBe('/library'));
    await screen.findByRole('heading', { level: 1, name: 'Library' });
  });

  it('sign-out forgets the member\'s stored Library tabs', async () => {
    const browser = userEvent.setup();
    mocks.logoutSession.mockResolvedValue(undefined);
    window.localStorage.setItem('lumina.sections.user-1', JSON.stringify({ movies: 1, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 0, recordings: 0, deleted: 0 }));
    window.history.replaceState(null, '', '/settings/account');
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(window.localStorage.getItem('lumina.sections.user-1')).toBeNull());
  });
});

describe('keyboard navigation in the authenticated shell', () => {
  it('supports skip navigation, traps the mobile drawer, restores focus, and focuses its destination', async () => {
    mobile = true;
    const keyboard = userEvent.setup();
    const { container } = render(<LuminaApp />);

    document.body.focus();
    await keyboard.tab();
    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Skip to main content' }));
    await keyboard.keyboard('{Enter}');
    expect(document.activeElement).toBe(screen.getByRole('main'));

    const menuTrigger = screen.getByRole('button', { name: 'Open navigation' });
    menuTrigger.focus();
    await keyboard.keyboard('{Enter}');
    const drawer = await screen.findByRole('dialog', { name: 'Mobile navigation' });
    const home = within(drawer).getByRole('button', { name: 'Home' });
    await waitFor(() => expect(document.activeElement).toBe(home));
    expect(container.querySelector('.app-background')?.hasAttribute('inert')).toBe(true);

    await keyboard.tab({ shift: true });
    expect(document.activeElement).toBe(within(drawer).getByRole('button', { name: 'Sign out' }));
    await keyboard.tab();
    expect(document.activeElement).toBe(home);
    await keyboard.keyboard('{Escape}');
    await waitFor(() => expect(document.activeElement).toBe(menuTrigger));
    expect(screen.queryByRole('dialog', { name: 'Mobile navigation' })).toBeNull();
    expect(container.querySelector('.app-background')?.hasAttribute('inert')).toBe(false);

    await keyboard.keyboard('{Enter}');
    const reopenedDrawer = await screen.findByRole('dialog', { name: 'Mobile navigation' });
    const library = within(reopenedDrawer).getByRole('button', { name: 'Library' });
    library.focus();
    await keyboard.keyboard('{Enter}');
    const heading = await screen.findByRole('heading', { level: 1, name: 'Library' });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.queryByRole('dialog', { name: 'Mobile navigation' })).toBeNull();
    expect(container.querySelector('.g-mobile-tabs button:nth-child(2)')?.getAttribute('aria-current')).toBe('page');
  });

  it('marks desktop navigation current and moves focus to the changed surface', async () => {
    const keyboard = userEvent.setup();
    render(<LuminaApp />);

    const primary = screen.getByRole('navigation', { name: 'Primary' });
    const explore = within(primary).getByRole('button', { name: 'Streaming' });
    explore.focus();
    await keyboard.keyboard('{Enter}');

    const heading = await screen.findByRole('heading', { level: 1, name: 'Streaming' });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(explore.getAttribute('aria-current')).toBe('page');
    expect(document.querySelectorAll('main')).toHaveLength(1);
  });

  it('moves forward from the expanded desktop menu trigger into primary navigation', async () => {
    const keyboard = userEvent.setup();
    render(<LuminaApp />);

    const trigger = document.querySelector('.g-topbar-start button') as HTMLButtonElement;
    trigger.focus();
    await keyboard.tab();
    expect(document.activeElement).toBe(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Home' }));
  });
});

describe('household and Library curation integration', () => {
  const openNotes = async (browser: ReturnType<typeof userEvent.setup>) => browser.click(await screen.findByRole('tab', { name: 'Sharing & tags' }));
  // Library items open from their own tab now that All is the chaptered landing.
  const openYouTube = async (browser: ReturnType<typeof userEvent.setup>) => {
    await browser.click(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Library' }));
    await browser.click(await within(screen.getByRole('navigation', { name: 'Library' })).findByRole('link', { name: 'YouTube' }));
  };

  it('loads and mutates owner-only item policy and private tags', async () => {
    const browser = userEvent.setup();
    const ownedItem = { ...libraryItem, user_id: user.id, visibility: 'private' as const, owner_display_name: user.display_name };
    workspaceState.library = [ownedItem];
    mocks.getLibraryItem.mockResolvedValue(ownedItem);
    mocks.listLibraryTags.mockResolvedValue([{ id: 'tag-1', item_id: ownedItem.id, user_id: user.id, tag: 'family' }]);
    mocks.updateLibraryItemVisibility.mockResolvedValue({ ...ownedItem, visibility: 'shared' });
    mocks.createLibraryTag.mockResolvedValue({ id: 'tag-2', item_id: ownedItem.id, user_id: user.id, tag: 'favorite' });

    render(<LuminaApp />);
    const primary = screen.getByRole('navigation', { name: 'Primary' });
    await openYouTube(browser);
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault, / }));
    await openNotes(browser);

    const curation = await screen.findByRole('region', { name: 'About this Library item' });
    expect(within(curation).getByText('family')).not.toBeNull();
    await browser.selectOptions(within(curation).getByRole('combobox', { name: /Who can see Film in the vault/ }), 'shared');
    await browser.type(within(curation).getByRole('textbox', { name: 'Add a private tag' }), 'favorite');
    await browser.click(within(curation).getByRole('button', { name: 'Add tag' }));

    await waitFor(() => expect(mocks.updateLibraryItemVisibility).toHaveBeenCalledWith(ownedItem.id, 'shared'));
    await waitFor(() => expect(mocks.createLibraryTag).toHaveBeenCalledWith(ownedItem.id, 'favorite'));
  });

  it('ignores a stale item mutation without overwriting or unlocking the newly selected item', async () => {
    const browser = userEvent.setup();
    const itemA = { ...libraryItem, id: 'item-a', title: 'Item A', user_id: user.id, visibility: 'private' as const };
    const itemB = { ...libraryItem, id: 'item-b', title: 'Item B', user_id: user.id, visibility: 'private' as const };
    workspaceState.library = [itemA, itemB];
    mocks.getLibraryItem.mockImplementation(async (id: string) => id === itemA.id ? itemA : itemB);
    mocks.listLibraryTags.mockImplementation(async (id: string) => [{ id: `tag-${id}`, item_id: id, user_id: user.id, tag: id === itemA.id ? 'alpha' : 'bravo' }]);
    let finishA: ((item: LibraryItem) => void) | undefined;
    let finishB: ((tag: UserTag) => void) | undefined;
    mocks.updateLibraryItemVisibility.mockImplementation(() => new Promise((resolve) => { finishA = resolve; }));
    mocks.createLibraryTag.mockImplementation(() => new Promise((resolve) => { finishB = resolve; }));

    render(<LuminaApp />);
    const primary = screen.getByRole('navigation', { name: 'Primary' });
    await openYouTube(browser);
    await browser.click(await screen.findByRole('button', { name: /^Item A, / }));
    await openNotes(browser);
    const itemAVisibility = await screen.findByRole('combobox', { name: 'Who can see Item A' });
    await browser.selectOptions(itemAVisibility, 'shared');
    await vi.waitFor(() => expect(finishA).toBeTypeOf('function'));

    await browser.click(screen.getByRole('button', { name: 'Back' }));
    await browser.click(await screen.findByRole('button', { name: /^Item B, / }));
    await openNotes(browser);
    expect(await screen.findByText('bravo')).not.toBeNull();
    await browser.type(screen.getByRole('textbox', { name: 'Add a private tag' }), 'busy-b');
    await browser.click(screen.getByRole('button', { name: 'Add tag' }));
    await vi.waitFor(() => expect(finishB).toBeTypeOf('function'));

    finishA?.({ ...itemA, visibility: 'shared' });
    await Promise.resolve();
    expect(screen.getByRole('heading', { level: 1, name: 'Item B' })).not.toBeNull();
    expect((screen.getByRole('combobox', { name: 'Who can see Item B' }) as HTMLSelectElement).value).toBe('private');
    expect(screen.getByRole('button', { name: 'Add tag' }).hasAttribute('disabled')).toBe(true);

    finishB?.({ id: 'tag-busy-b', item_id: itemB.id, user_id: user.id, tag: 'busy-b' });
    expect(await screen.findByText('busy-b')).not.toBeNull();
  });

  it('hides Item A private curation synchronously while Item B curation is deferred', async () => {
    const browser = userEvent.setup();
    const itemA = { ...libraryItem, id: 'deferred-a', title: 'Deferred A', user_id: user.id, visibility: 'private' as const };
    const itemB = { ...libraryItem, id: 'deferred-b', title: 'Deferred B', user_id: user.id, visibility: 'private' as const };
    workspaceState.library = [itemA, itemB];
    mocks.getLibraryItem.mockImplementation(async (id: string) => id === itemA.id ? itemA : itemB);
    let finishBTags: ((tags: UserTag[]) => void) | undefined;
    mocks.listLibraryTags.mockImplementation((id: string) => id === itemA.id
      ? Promise.resolve([{ id: 'private-a-tag', item_id: itemA.id, user_id: user.id, tag: 'private-alpha' }])
      : new Promise((resolve) => { finishBTags = resolve; }));

    render(<LuminaApp />);
    const primary = screen.getByRole('navigation', { name: 'Primary' });
    await openYouTube(browser);
    await browser.click(await screen.findByRole('button', { name: /^Deferred A, / }));
    await openNotes(browser);
    expect(await screen.findByText('private-alpha')).not.toBeNull();
    await browser.type(screen.getByRole('textbox', { name: 'Add a private tag' }), 'A secret draft');
    await browser.click(screen.getByRole('button', { name: 'Back' }));
    await browser.click(await screen.findByRole('button', { name: /^Deferred B, / }));
    await openNotes(browser);

    expect(screen.queryByText('private-alpha')).toBeNull();
    expect((screen.getByRole('textbox', { name: 'Add a private tag' }) as HTMLInputElement).value).toBe('');
    expect(screen.getByText('Loading Library details…')).not.toBeNull();
    finishBTags?.([{ id: 'tag-b', item_id: itemB.id, user_id: user.id, tag: 'bravo' }]);
    expect(await screen.findByText('bravo')).not.toBeNull();
  });

  it('leaves Item B empty when its curation load fails instead of exposing Item A private data', async () => {
    const browser = userEvent.setup();
    const itemA = { ...libraryItem, id: 'failed-a', title: 'Failed A', user_id: user.id, visibility: 'private' as const };
    const itemB = { ...libraryItem, id: 'failed-b', title: 'Failed B', user_id: user.id, visibility: 'private' as const };
    workspaceState.library = [itemA, itemB];
    mocks.getLibraryItem.mockImplementation(async (id: string) => id === itemA.id ? itemA : itemB);
    mocks.listLibraryTags.mockImplementation((id: string) => id === itemA.id
      ? Promise.resolve([{ id: 'failed-private-tag', item_id: itemA.id, user_id: user.id, tag: 'secret-alpha' }])
      : Promise.reject(new Error('Item B notes are unavailable.')));

    render(<LuminaApp />);
    const primary = screen.getByRole('navigation', { name: 'Primary' });
    await openYouTube(browser);
    await browser.click(await screen.findByRole('button', { name: /^Failed A, / }));
    await openNotes(browser);
    expect(await screen.findByText('secret-alpha')).not.toBeNull();
    await browser.click(screen.getByRole('button', { name: 'Back' }));
    await browser.click(await screen.findByRole('button', { name: /^Failed B, / }));
    await openNotes(browser);

    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Item B notes are unavailable.');
    expect(screen.queryByText('secret-alpha')).toBeNull();
    expect(screen.getByText('No private tags yet.')).not.toBeNull();
  });
});

describe('Media acquisition shell integration', () => {
  it('starts local playback at the resume point once history arrives, without waiting for the item refresh', async () => {
    const browser = userEvent.setup();
    let resolveItem: ((item: LibraryItem) => void) | undefined;
    let resolveProgress: ((progress: { item_id: string; position_seconds: number; duration_seconds: number; completed: boolean }) => void) | undefined;
    mocks.getLibraryItem.mockImplementationOnce(() => new Promise((resolve) => { resolveItem = resolve; }));
    mocks.getPlaybackProgress.mockImplementationOnce(() => new Promise((resolve) => { resolveProgress = resolve; }));

    render(<LuminaApp />);
    await browser.click(screen.getByRole('button', { name: 'Film in the vault, Archive Channel' }));
    await vi.waitFor(() => expect(resolveItem).toBeTypeOf('function'));
    expect(document.querySelector('video')).toBeNull();

    // The player mounts once its start point is known; the item refresh no longer gates it.
    resolveProgress?.({ item_id: libraryItem.id, position_seconds: 30, duration_seconds: 120, completed: false });
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(await screen.findByRole('button', { name: 'Start over' })).not.toBeNull();
    resolveItem?.({ ...libraryItem, duration: 120 });
    const player = document.querySelector('video') as HTMLVideoElement;
    Object.defineProperty(player, 'duration', { configurable: true, value: 120 });
    fireEvent.loadedMetadata(player);
    expect(player.currentTime).toBe(30);
  });

  it('keeps local playback available when optional playback history fails', async () => {
    const browser = userEvent.setup();
    mocks.getLibraryItem.mockResolvedValueOnce({ ...libraryItem, duration: 120 });
    mocks.getPlaybackProgress.mockRejectedValueOnce(new TypeError('history offline'));

    render(<LuminaApp />);
    await browser.click(screen.getByRole('button', { name: 'Film in the vault, Archive Channel' }));

    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(await screen.findByText('Playback history is unavailable. Starting from the beginning.')).not.toBeNull();
  });

  it('offers no per-row download in a web video\'s Up next, so Save queues only the watched source (C4 decision D38)', async () => {
    const browser = userEvent.setup();
    const sourceA = 'https://example.test/watch-a';
    const sourceB = 'https://example.test/queue-b';
    mocks.getUpNext.mockResolvedValue(upNextSnapshot([{ id: 'b', title: 'Queue B', webpage_url: sourceB }]));
    mocks.previewUrl.mockImplementation(async (payload: { source_url: string }) => (
      { kind: 'video', title: payload.source_url === sourceA ? 'Watch A' : 'Queue B', webpage_url: payload.source_url, entries: [], raw: { uploader: 'Maker' } }
    ));

    render(<LuminaApp />);
    await browser.type(await openPalette(browser), `${sourceA}{Enter}`);
    await screen.findByRole('heading', { level: 1, name: 'Watch A' });
    expect(await screen.findByRole('button', { name: /^Queue B/ })).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Add Queue B to download queue' })).toBeNull();
    await browser.click(screen.getByRole('button', { name: 'Save to library' }));

    await waitFor(() => expect(mocks.createJob).toHaveBeenCalledTimes(1));
    expect(mocks.createJob).toHaveBeenCalledWith(expect.objectContaining({ source_url: sourceA }));
    expect(mocks.createJob).not.toHaveBeenCalledWith(expect.objectContaining({ source_url: sourceB }));
  });

  it('drives format choices from inspection through queue submission', async () => {
    const browser = userEvent.setup();
    const workspace = mocks.useAuthenticatedWorkspace();
    const dispatch = vi.fn((action: { type: string; patch?: Record<string, unknown> }) => {
      if (action.type === 'preferences/patch' && action.patch) Object.assign(workspaceState.preferences, action.patch);
    });
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...workspace, dispatch });
    mocks.previewUrl.mockImplementation(async (payload: { source_url: string }) => ({
      kind: 'video',
      title: 'Inspected acquisition',
      webpage_url: payload.source_url,
      entries: [],
      raw: { id: 'video-acquisition', uploader: 'Acquisition Maker' },
    }));

    render(<LuminaApp />);
    await chooseFromAccountMenu(browser, 'Settings');
    await browser.click(within(await screen.findByRole('navigation', { name: 'Settings sections' })).getByRole('link', { name: 'Downloads' }));
    await browser.selectOptions(await screen.findByRole('combobox', { name: 'Default quality' }), 'best_1080p');
    // Nothing hides behind a disclosure: Container is one row away.
    await browser.selectOptions(screen.getByRole('combobox', { name: 'Container' }), 'mkv');
    const subtitles = screen.getByRole('checkbox', { name: 'Download subtitles when available' });
    if (!(subtitles as HTMLInputElement).checked) await browser.click(subtitles);
    await browser.type(screen.getByRole('textbox', { name: 'Folder inside the Library' }), 'family/protected');

    await browser.type(await openPalette(browser), 'https://example.test/video-acquisition{Enter}');
    expect(await screen.findByRole('heading', { level: 1, name: 'Inspected acquisition' })).not.toBeNull();

    await browser.click(screen.getByRole('button', { name: 'Save to library' }));
    await vi.waitFor(() => expect(mocks.createJob).toHaveBeenCalledTimes(1));

    expect(mocks.previewUrl).toHaveBeenNthCalledWith(1, expect.objectContaining({
      format_selection: expect.objectContaining({ preset: 'best_1080p', output_container: 'mkv', subtitles: true }),
    }));
    expect(mocks.createJob).toHaveBeenCalledWith(expect.objectContaining({
      format_selection: expect.objectContaining({ preset: 'best_1080p', output_container: 'mkv', subtitles: true }),
      output_profile: expect.objectContaining({ subdir: 'family/protected' }),
    }));
  });


  it('keeps an in-flight source busy after navigation resets the watch presentation', async () => {
    const browser = userEvent.setup();
    const sourceUrl = 'https://example.test/video-navigation';
    let finishQueue: ((value: { id: string; source_url: string; status: 'queued' }) => void) | undefined;
    mocks.previewUrl.mockImplementation(async (payload: { source_url: string }) => ({
      kind: 'video',
      title: 'Navigation film',
      webpage_url: payload.source_url,
      entries: [],
      raw: { id: 'video-navigation' },
    }));
    mocks.createJob.mockImplementationOnce(() => new Promise((resolve) => { finishQueue = resolve; }));

    render(<LuminaApp />);
    await browser.type(await openPalette(browser), `${sourceUrl}{Enter}`);
    await screen.findByRole('heading', { level: 1, name: 'Navigation film' });
    await browser.click(screen.getByRole('button', { name: 'Save to library' }));
    await vi.waitFor(() => expect(finishQueue).toBeTypeOf('function'));

    const primary = screen.getByRole('navigation', { name: 'Primary' });
    await browser.click(within(primary).getByRole('button', { name: 'Streaming' }));
    await browser.type(await openPalette(browser), `${sourceUrl}{Enter}`);
    await screen.findByRole('heading', { level: 1, name: 'Navigation film' });

    const duplicate = screen.getByRole('button', { name: 'Save to library' });
    expect(duplicate.hasAttribute('disabled')).toBe(true);
    await browser.click(duplicate);
    expect(mocks.createJob).toHaveBeenCalledTimes(1);

    finishQueue?.({ id: 'job-navigation', source_url: sourceUrl, status: 'queued' });
    await waitFor(() => expect(duplicate.hasAttribute('disabled')).toBe(false));
  });
});

describe('keyboard-safe interactive overlays', () => {
  it('inspects an owned channel match by its saved source instead of searching the live catalog by label', async () => {
    const browser = userEvent.setup();
    const sourceUrl = 'https://www.youtube.com/@mars-astronomy';
    workspaceState.sourceAutomations = [{
      id: 'channel-1', user_id: user.id, label: 'Mars Astronomy', source_url: sourceUrl, source_type: 'channel', cron_expression: '0 */6 * * *', active: true, auto_download: false,
      format_selection: {}, output_profile: {}, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
      duplicate_policy: 'skip_same_source', last_run_summary: {}, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    } satisfies SourceAutomation];
    render(<LuminaApp />);

    await browser.type(await openPalette(browser), 'mars');
    const channel = await screen.findByRole('option', { name: /Mars Astronomy/ });
    await browser.click(channel);

    await waitFor(() => expect(mocks.previewUrl).toHaveBeenCalledWith(expect.objectContaining({ source_url: sourceUrl })));
    expect(mocks.youtubeSearch).not.toHaveBeenCalledWith(expect.objectContaining({ query: 'Mars Astronomy' }));
  });

  it('uses a roving radio group in the watch download menu and restores its trigger on Escape', async () => {
    const keyboard = userEvent.setup();
    const remote: YouTubeSearchResult = { id: 'video-1', title: 'Remote film', uploader: 'Film Maker', webpage_url: 'https://example.test/video-1' };

    function WatchHarness() {
      const [quality, setQuality] = useState<AcquisitionFormat>('best');
      const [open, setOpen] = useState(false);
      return watchSurface({
        downloadMenuOpen: open,
        downloadQuality: quality,
        onCloseDownloadMenu: () => setOpen(false),
        onDownloadQuality: setQuality,
        onToggleDownloadMenu: () => setOpen((value) => !value),
        selection: { kind: 'remote', item: remote, preview: null },
      });
    }

    render(<WatchHarness />);
    const trigger = screen.getByRole('button', { name: 'Download options' });
    trigger.focus();
    await keyboard.keyboard('{Enter}');
    const firstPreset = await screen.findByRole('radio', { name: /Best available/ });
    await waitFor(() => expect(document.activeElement).toBe(firstPreset));

    await keyboard.keyboard('{ArrowRight}');
    const nextPreset = screen.getByRole('radio', { name: /1080p/ });
    await waitFor(() => expect(document.activeElement).toBe(nextPreset));
    expect(nextPreset.getAttribute('aria-checked')).toBe('true');
    expect(firstPreset.tabIndex).toBe(-1);

    await keyboard.keyboard('{Escape}');
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(screen.queryByRole('radiogroup', { name: 'Download quality' })).toBeNull();
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('Watch sharing', () => {
  const remote: YouTubeSearchResult = {
    id: 'video-share',
    title: 'Shareable film',
    uploader: 'Film Maker',
    webpage_url: 'https://example.test/watch/shareable',
  };

  function renderWatch(item: YouTubeSearchResult = remote) {
    return render(watchSurface({ selection: { kind: 'remote', item, preview: null } }));
  }

  it('confirms a successful source-address copy', async () => {
    const browser = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    renderWatch();

    await browser.click(screen.getByRole('button', { name: 'Share' }));

    expect(writeText).toHaveBeenCalledWith(remote.webpage_url);
    expect(await screen.findByText('Source address copied.')).not.toBeNull();
  });

  it('exposes a selectable source-address fallback when clipboard access is rejected', async () => {
    const browser = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new DOMException('blocked', 'NotAllowedError')) } });
    renderWatch();

    await browser.click(screen.getByRole('button', { name: 'Share' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Lumina could not copy the source address.');
    expect((screen.getByRole('textbox', { name: 'Share address' }) as HTMLInputElement).value).toBe(remote.webpage_url);
  });

  it('exposes the fallback when the Clipboard API is unavailable', async () => {
    const browser = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    renderWatch();

    await browser.click(screen.getByRole('button', { name: 'Share' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Copy is unavailable in this browser.');
    expect((screen.getByRole('textbox', { name: 'Share address' }) as HTMLInputElement).value).toBe(remote.webpage_url);
  });

  it('falls back to the current app address when the item has no source URL', async () => {
    const browser = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    renderWatch({ ...remote, webpage_url: null });

    await browser.click(screen.getByRole('button', { name: 'Share' }));

    expect((screen.getByRole('textbox', { name: 'Share address' }) as HTMLInputElement).value).toBe(window.location.href);
  });
});

describe('followed-channel recovery', () => {
  it('shows the refreshed feed once a manual check upserts the follow, without client provider work', async () => {
    const browser = userEvent.setup();
    const channel = {
      id: 'channel-1',
      user_id: user.id,
      label: 'Channel One',
      source_url: 'https://example.test/channel-one',
      source_type: 'channel',
      cron_expression: '0 */6 * * *',
      active: true,
      auto_download: false,
      format_selection: {},
      output_profile: {},
      rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
      duplicate_policy: 'skip_same_source',
      last_run_summary: {},
      last_checked_at: '2026-01-01T00:00:00',
      feed_entries: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    } satisfies SourceAutomation;
    const refreshed = {
      ...channel,
      last_checked_at: '2026-01-01T01:00:00',
      feed_entries: [{ id: 'fresh', title: 'Fresh film', webpage_url: 'https://example.test/watch/fresh' }],
    };
    workspaceState.sourceAutomations = [channel];
    let workspaceDispatch: (action: Parameters<typeof workspaceReducer>[1]) => void = () => undefined;
    const stableWorkspace = { loadAuthenticated: vi.fn(), loadPublic: vi.fn(), retryCollection: vi.fn(), refreshLibraryCollection: vi.fn(), captureSessionToken: vi.fn(() => 1), isSessionTokenCurrent: vi.fn(() => true), reset: vi.fn() };
    mocks.useAuthenticatedWorkspace.mockImplementation(() => {
      const [state, dispatch] = useReducer(workspaceReducer, workspaceState);
      workspaceDispatch = dispatch;
      return { state, dispatch, ...stableWorkspace };
    });
    mocks.runAutomation.mockImplementation(async () => {
      workspaceDispatch({ type: 'automations/upsert', automation: refreshed });
      return { id: 'run-1', automation_id: channel.id, status: 'completed' };
    });
    mocks.listAutomations.mockResolvedValue([refreshed]);

    render(<LuminaApp />);
    act(() => { openAppPath('/streaming/channels'); });
    HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
    await browser.click(await screen.findByRole('link', { name: /^Channel One(,|$)/ }));
    await browser.click(await screen.findByRole('button', { name: 'Follow settings' }));
    await browser.click(await screen.findByRole('button', { name: 'Check Channel One now' }));

    expect(await screen.findByRole('button', { name: 'Fresh film' })).not.toBeNull();
    expect(mocks.runAutomation).toHaveBeenCalledTimes(1);
    expect(mocks.previewUrl).not.toHaveBeenCalled();
  });

  it('keeps successful media visible, degrades failed artwork, and keeps a members-only channel flagged', async () => {
    const browser = userEvent.setup();
    const channels = ['open', 'protected'].map((id) => ({
      id,
      user_id: user.id,
      label: id === 'open' ? 'Open Channel' : 'Protected Channel',
      source_url: `https://example.test/${id}`,
      source_type: 'channel',
      artwork_url: '/api/artwork/remote/channel-avatar',
      cron_expression: '0 */6 * * *',
      active: true,
      auto_download: false,
      format_selection: {},
      output_profile: {},
      rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
      duplicate_policy: 'skip_same_source',
      last_run_summary: {},
      last_checked_at: '2026-01-01T00:00:00',
      last_error: id === 'open' ? null : 'This channel publishes members-only content.',
      feed_entries: id === 'open' ? [{
        id: 'Open film',
        title: 'Open film',
        uploader: 'Maker',
        thumbnail: 'https://images.example.test/broken.jpg',
        artwork_url: '/api/artwork/remote/video-thumbnail',
        webpage_url: 'https://example.test/watch/Open film',
      }] : [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    } satisfies SourceAutomation));
    workspaceState.sourceAutomations = channels;
    let workspaceDispatch: (action: Parameters<typeof workspaceReducer>[1]) => void = () => undefined;
    const stableWorkspace = { loadAuthenticated: vi.fn(), loadPublic: vi.fn(), retryCollection: vi.fn(), refreshLibraryCollection: vi.fn(), captureSessionToken: vi.fn(() => 1), isSessionTokenCurrent: vi.fn(() => true), reset: vi.fn() };
    mocks.useAuthenticatedWorkspace.mockImplementation(() => {
      const [state, dispatch] = useReducer(workspaceReducer, workspaceState);
      workspaceDispatch = dispatch;
      return { state, dispatch, ...stableWorkspace };
    });
    mocks.refreshFollows.mockResolvedValue(channels);

    const { container } = render(<LuminaApp />);
    await screen.findByRole('button', { name: 'Open film, Maker' });

    const primary = screen.getByRole('navigation', { name: 'Primary' });
    act(() => { openAppPath('/streaming/channels'); });
    expect(await screen.findByRole('button', { name: 'Open film, Maker' })).not.toBeNull();
    expect(screen.getByRole('heading', { name: 'Channels needing attention' })).not.toBeNull();
    expect(screen.getByRole('link', { name: 'Protected Channel, Last check failed' })).not.toBeNull();
    expect(screen.getByText('This channel publishes members-only content, which Lumina cannot inspect without an account.')).not.toBeNull();
    expect(screen.queryByRole('button', { name: /Choose a Saved sign-in/ })).toBeNull();
    expect(screen.getByText(/Subscription update complete\. 1 need attention/)).not.toBeNull();
    // Avatar tiles draw the proxied channel art; a provider's raw thumbnail URL is never used.
    expect(container.querySelectorAll('img[src$="/api/artwork/remote/channel-avatar"]')).toHaveLength(2);
    expect(container.querySelector<HTMLImageElement>('img[src="https://images.example.test/broken.jpg"]')).toBeNull();

    HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
    await browser.click(screen.getByRole('link', { name: /^Open Channel(,|$)/ }));
    await browser.click(await screen.findByRole('button', { name: 'Follow settings' }));
    expect(screen.getByRole('checkbox', { name: 'Download new videos automatically' })).not.toBeNull();
    const integratedUnfollow = screen.getByRole('button', { name: 'Unfollow Open Channel' });
    await browser.click(integratedUnfollow);
    expect(screen.getByRole('alertdialog')).not.toBeNull();
    await browser.keyboard('{Escape}');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(document.activeElement).toBe(integratedUnfollow);
    await browser.click(screen.getByRole('button', { name: 'Done' }));
    act(() => { openAppPath('/streaming/channels'); });

    await browser.click(screen.getByRole('button', { name: /Retry channels/ }));
    expect(mocks.refreshFollows).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('button', { name: /Restart retry/ })).not.toBeNull();
    expect(screen.getByText(/Checking 2 followed channels/)).not.toBeNull();
    // Members-only content is a content classification, not a transient error:
    // the server's next check keeps the channel flagged with the honest explanation.
    channels.forEach((channel) => workspaceDispatch({ type: 'automations/upsert', automation: { ...channel, last_checked_at: '2026-01-01T01:00:00' } }));
    await vi.waitFor(() => expect(screen.queryByRole('button', { name: /Restart retry/ })).toBeNull());
    expect(screen.getByRole('heading', { name: 'Channels needing attention' })).not.toBeNull();
    expect(screen.getByText('This channel publishes members-only content, which Lumina cannot inspect without an account.')).not.toBeNull();
    expect(mocks.previewUrl).not.toHaveBeenCalled();
  });

  it('keeps focus on the channel list across detail and unfollow', async () => {
    const browser = userEvent.setup();
    const makeChannel = (id: string, label: string): SourceAutomation => ({
      id,
      user_id: user.id,
      label,
      source_url: `https://example.test/${id}`,
      source_type: 'channel',
      cron_expression: '0 */6 * * *',
      active: true,
      auto_download: false,
      format_selection: {},
      output_profile: {},
      rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
      duplicate_policy: 'skip_same_source',
      last_run_summary: {},
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    const first = makeChannel('channel-one', 'Channel One');
    const second = makeChannel('channel-two', 'Channel Two');
    const third = makeChannel('channel-three', 'Channel Three');
    const commands = {
      pause: vi.fn().mockResolvedValue(first),
      resume: vi.fn().mockResolvedValue(first),
      setAutomaticAcquisition: vi.fn().mockResolvedValue(first),
      prepareUnfollow: vi.fn((automation: SourceAutomation) => ({ automationId: automation.id, sourceIdentity: automation.source_url })),
      confirmUnfollow: vi.fn().mockResolvedValue(undefined),
    };

    function StatefulSubscriptions() {
      const [channels, setChannels] = useState([first, second, third]);
      const [channelId, setChannelId] = useState<string | null>(null);
      return <><button onClick={() => setChannelId('channel-two')} type="button">Open Channel Two</button><ChannelsSurface
        channelId={channelId}
        channels={channels}
        commands={commands}
        isQueueing={() => false}
        library={[]}
        onAutomationChange={vi.fn()}
        onAutomationRemoved={(automationId) => setChannels((current) => current.filter((channel) => channel.id !== automationId))}
        onExplore={vi.fn()}
        onOpen={vi.fn()}
        onOpenChannel={setChannelId}
        onQueue={vi.fn()}
        onRecover={vi.fn().mockResolvedValue(undefined)}
        onRetry={vi.fn()}
        outcomes={channels.map(followOutcome)}
        refreshing={false}
      /></>;
    }

    HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
    render(<StatefulSubscriptions />);
    // A tile is a link to its follow detail; LuminaApp opens the route, here the harness's own Open does.
    expect(screen.getByRole('link', { name: /^Channel Two(,|$)/ }).getAttribute('href')).toBe('/subscriptions/channel-two');
    await browser.click(screen.getByRole('button', { name: 'Open Channel Two' }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1, name: 'Channel Two' })));

    await browser.click(screen.getByRole('button', { name: 'Follow settings' }));
    await browser.click(screen.getByRole('button', { name: 'Unfollow Channel Two' }));
    await browser.click(screen.getByRole('button', { name: 'Confirm unfollow Channel Two' }));

    await waitFor(() => expect(screen.queryByRole('link', { name: /^Channel Two(,|$)/ })).toBeNull());
    expect(screen.getByRole('link', { name: /^Channel Three(,|$)/ })).not.toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1, name: 'Subscriptions' })));
  });

});

describe('Downloads retry', () => {
  it('retries a batch save job through its batch entry, and a plain job directly', async () => {
    const browser = userEvent.setup();
    const failed = { status: 'failed' as const, created_at: '2026-07-20T10:00:00Z' };
    Object.assign(workspaceState, { jobsState: 'ready', jobs: [
      { ...failed, id: 'job-batch', source_url: 'https://example.test/a', title: 'Batch video', acquisition_batch_id: 'batch-1', acquisition_entry_id: 'entry-1' },
      { ...failed, id: 'job-plain', source_url: 'https://example.test/b', title: 'Plain video' },
    ] });
    mocks.retryAcquisitionEntry.mockResolvedValue({ id: 'batch-2', entries: [] });
    mocks.retryJob.mockResolvedValue({ ...failed, id: 'job-plain', source_url: 'https://example.test/b', status: 'queued' });
    window.history.replaceState(null, '', '/downloads');
    render(<LuminaApp />);

    await browser.click(await screen.findByRole('button', { name: 'Retry Batch video' }));
    await waitFor(() => expect(mocks.retryAcquisitionEntry).toHaveBeenCalledWith('batch-1', 'entry-1'));
    expect(mocks.retryJob).not.toHaveBeenCalled();

    await browser.click(screen.getByRole('button', { name: 'Retry Plain video' }));
    await waitFor(() => expect(mocks.retryJob).toHaveBeenCalledWith('job-plain'));
    expect(mocks.retryAcquisitionEntry).toHaveBeenCalledTimes(1);
  });
});

describe('live and YouTube routes', () => {
  it('opens a channel page from its address and keeps the tab in it', async () => {
    window.history.replaceState(null, '', `/channel/youtube/${CHANNEL_ID}?tab=live`);
    render(<LuminaApp />);
    expect(await screen.findByRole('tablist', { name: 'Channel' })).toBeTruthy();
    expect(mocks.getChannelPage).toHaveBeenCalledWith(CHANNEL_ID, { tab: 'streams', limit: 60 }, expect.anything());
    await userEvent.click(screen.getByRole('tab', { name: 'Videos' }));
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(`/channel/youtube/${CHANNEL_ID}`));
  });

  it('resolves /channel?url= and replaces it', async () => {
    window.history.replaceState(null, '', `/channel?url=${encodeURIComponent('https://www.youtube.com/@harborfilms')}`);
    const length = window.history.length;
    render(<LuminaApp />);
    await waitFor(() => expect(window.location.pathname).toBe(`/channel/youtube/${CHANNEL_ID}`));
    expect(mocks.resolveChannel).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('tablist', { name: 'Channel' })).toBeTruthy();
    expect(window.history.length).toBe(length);
  });

  it('leaves the channel page with Escape, as the browser Back does', async () => {
    window.history.replaceState(null, '', '/streaming');
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    act(() => { openAppPath(`/channel/youtube/${CHANNEL_ID}`); });
    const tabs = await screen.findByRole('tablist', { name: 'Channel' });
    fireEvent.keyDown(within(tabs).getAllByRole('tab')[0], { key: 'Escape' });
    await waitFor(() => expect(window.location.pathname).toBe('/streaming'));
  });

  it('focuses the channel page heading after a Subscriptions tile opens it (never <body>)', async () => {
    window.history.replaceState(null, '', '/streaming/channels');
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1, name: 'Streaming' });
    // A tile's own navigation: the surface stays 'subscriptions', only the page changes.
    act(() => { openAppPath(`/channel/youtube/${CHANNEL_ID}`); });
    await screen.findByRole('tablist', { name: 'Channel' });
    const heading = within(document.querySelector('.g-channel-page') as HTMLElement).getByRole('heading', { level: 1 });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(document.activeElement).not.toBe(document.body);
  });

  it('keeps focus on a channel tab the member just chose (no over-focusing)', async () => {
    window.history.replaceState(null, '', `/channel/youtube/${CHANNEL_ID}?tab=live`);
    render(<LuminaApp />);
    await screen.findByRole('tablist', { name: 'Channel' });
    const videos = screen.getByRole('tab', { name: 'Videos' });
    await userEvent.click(videos);
    await waitFor(() => expect(window.location.search).toBe(''));
    expect(document.activeElement).toBe(videos);
  });

  it('Back from a video opened on a channel page returns to the channel page, not Subscriptions', async () => {
    window.history.replaceState(null, '', '/streaming/channels');
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1, name: 'Streaming' });
    act(() => { openAppPath(`/channel/youtube/${CHANNEL_ID}`); });
    await screen.findByRole('tablist', { name: 'Channel' });
    await userEvent.click(await screen.findByRole('button', { name: /^Harbor film 1\b/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('tablist', { name: 'Channel' })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1, name: 'Streaming' })).toBeNull();
    expect(window.location.pathname).toBe(`/channel/youtube/${CHANNEL_ID}`);
  });

  it('clicking Videos on the default channel page does not make the next navigation replace the channel entry', async () => {
    window.history.replaceState(null, '', '/streaming/channels');
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1, name: 'Streaming' });
    act(() => { openAppPath(`/channel/youtube/${CHANNEL_ID}`); });
    await screen.findByRole('tablist', { name: 'Channel' });
    await userEvent.click(screen.getByRole('tab', { name: 'Videos' }));
    const pushes = vi.spyOn(window.history, 'pushState');
    await userEvent.click(await screen.findByRole('button', { name: /^Harbor film 1\b/ }));
    await waitFor(() => expect(pushes).toHaveBeenCalled());
    pushes.mockRestore();
    await userEvent.click(await screen.findByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('tablist', { name: 'Channel' })).toBeTruthy();
    expect(window.location.pathname).toBe(`/channel/youtube/${CHANNEL_ID}`);
  });

  it('switching channel tabs replaces history instead of pushing one entry per tab', async () => {
    window.history.replaceState(null, '', `/channel/youtube/${CHANNEL_ID}`);
    render(<LuminaApp />);
    await screen.findByRole('tablist', { name: 'Channel' });
    const pushes = vi.spyOn(window.history, 'pushState');
    await userEvent.click(screen.getByRole('tab', { name: 'Live' }));
    await waitFor(() => expect(window.location.search).toBe('?tab=live'));
    await userEvent.click(screen.getByRole('tab', { name: 'Shorts' }));
    await waitFor(() => expect(window.location.search).toBe('?tab=shorts'));
    expect(pushes).not.toHaveBeenCalled();
    pushes.mockRestore();
  });

  it('Follow on a channel page creates the channel automation and shows Following', async () => {
    mocks.createAutomation.mockImplementation(async (payload: Record<string, unknown>) => ({ ...payload, id: 'f-new', user_id: 'user-1' }));
    mocks.refreshFollows.mockResolvedValue([]);
    window.history.replaceState(null, '', `/channel/youtube/${CHANNEL_ID}`);
    render(<LuminaApp />);
    // The mocked workspace has no reducer: an upsert lands in the state the next render reads.
    mocks.useAuthenticatedWorkspace.mock.results[0].value.dispatch.mockImplementation((action: { type: string; automation?: SourceAutomation }) => {
      if (action.type === 'automations/upsert') workspaceState.sourceAutomations = [action.automation!];
    });
    await userEvent.click(await screen.findByRole('button', { name: /^Follow$/ }));
    await waitFor(() => expect(mocks.createAutomation).toHaveBeenCalledTimes(1));
    expect(mocks.createAutomation).toHaveBeenCalledWith(expect.objectContaining({ source_type: 'channel', source_url: `https://www.youtube.com/channel/${CHANNEL_ID}`, label: 'Harbor Films' }));
    expect(await screen.findByText('Following Harbor Films.')).toBeTruthy();
    expect((await screen.findByRole('button', { name: 'Following' })).getAttribute('aria-pressed')).toBe('true');
  });

  it('a follow that lands after a member switch reports nothing and upserts nothing', async () => {
    let finish: (value: unknown) => void = () => undefined;
    mocks.refreshFollows.mockResolvedValue([]);
    mocks.createAutomation.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    window.history.replaceState(null, '', `/channel/youtube/${CHANNEL_ID}`);
    render(<LuminaApp />);
    await userEvent.click(await screen.findByRole('button', { name: /^Follow$/ }));
    await waitFor(() => expect(mocks.createAutomation).toHaveBeenCalledTimes(1));
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    workspace.isSessionTokenCurrent.mockReturnValue(false);
    await act(async () => { finish({ id: 'f-late', user_id: 'user-1', source_url: `https://www.youtube.com/channel/${CHANNEL_ID}`, source_type: 'channel' }); });
    expect(workspace.dispatch).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'automations/upsert' }));
    expect(screen.queryByText('Following Harbor Films.')).toBeNull();
    expect(screen.queryByRole('button', { name: /Following/ })).toBeNull();
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Lumina could not follow this channel. Try again.');
  });

  it.each([
    ['/live?rail=gaming', '/streaming/live?rail=gaming'],
    ['/explore?rail=popular-gaming', '/streaming?rail=popular-gaming'],
    ['/live', '/streaming/live'],
    ['/subscriptions', '/streaming/channels'],
    ['/explore', '/streaming'],
  ])('redirects the old address %s to %s by replacing the history entry', async (old, canonical) => {
    window.history.replaceState(null, '', old);
    const length = window.history.length;
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(canonical));
    expect(window.history.length).toBe(length);
  });

  it('keeps ?rail= for a popular rail on Explore', async () => {
    window.history.replaceState(null, '', '/streaming?rail=popular-gaming');
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    expect(window.location.search).toBe('?rail=popular-gaming');
  });
});

describe('Back after a surface redirects during mount', () => {
  it('pushes one entry for /subscriptions/{id} of a YouTube follow, and Back returns to /streaming/live', async () => {
    const follow = {
      id: 'f1', user_id: 'user-1', label: 'Harbor', source_url: `https://www.youtube.com/channel/${CHANNEL_ID}`, source_type: 'channel', cron_expression: '*/30 * * * *', active: true,
      auto_download: false, format_selection: { preset: 'best' }, output_profile: {}, rules: {}, duplicate_policy: 'skip_same_source', max_items_per_run: 5, max_items_per_day: 20,
      last_checked_at: '2026-01-01T12:00:00', last_error: null, last_run_summary: {}, feed_entries: [], created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    } as unknown as SourceAutomation;
    Object.assign(workspaceState, { sourceAutomations: [follow] });
    window.history.replaceState(null, '', '/streaming/live');
    const pushes: string[] = [];
    const push = window.history.pushState.bind(window.history);
    const spy = vi.spyOn(window.history, 'pushState').mockImplementation((data, unused, url) => { pushes.push(String(url)); push(data, unused, url); });
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1, name: 'Streaming' });
    act(() => { openAppPath('/subscriptions/f1'); });
    await waitFor(() => expect(window.location.pathname).toBe(`/channel/youtube/${CHANNEL_ID}`));
    await new Promise((resolve) => { setTimeout(resolve, 1000); });
    expect(pushes).toEqual(['/subscriptions/f1']);
    act(() => { window.history.back(); });
    await waitFor(() => expect(window.location.pathname).toBe('/streaming/live'));
    await screen.findByRole('heading', { level: 1, name: 'Streaming' });
    await new Promise((resolve) => { setTimeout(resolve, 1000); });
    expect(window.location.pathname).toBe('/streaming/live');
    spy.mockRestore();
  });
});

describe('recommendation wiring', () => {
  // The follow copy is covered by `followHadSuppression`'s unit tests; this only checks the app still follows.
  it('fetches Home picks even when the member chose no interests (the server decides what counts as a signal)', async () => {
    render(<LuminaApp />);
    await waitFor(() => expect(mocks.getHomeRecommendations).toHaveBeenCalled());
  });

  it('the next member\'s Explore never shows the previous member\'s For you, even when their fetch fails', async () => {
    const other: UserProfile = { ...user, id: 'user-2', username: 'two', display_name: 'Two Person' };
    const pick = recoEntry('f1', 0, { title: 'A private pick' }, { reason: 'Because you finished Secret Show' });
    mocks.getPopularDiscovery.mockResolvedValueOnce({ items: [], categories: [], state: 'ready', refreshing: false, stale: false, for_you: [pick], category_order: [] });
    window.history.replaceState(null, '', '/streaming');
    const view = render(<LuminaApp />);
    await screen.findByRole('heading', { name: 'Recommended for you' });
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    mocks.getPopularDiscovery.mockRejectedValue(new Error('Popular is unavailable.'));
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...workspace, state: { ...workspaceState, currentUser: other } });
    view.rerender(<LuminaApp />);
    await screen.findByText('Popular is unavailable.');
    expect(screen.queryByRole('heading', { name: 'Recommended for you' })).toBeNull();
    expect(screen.queryByText(/Secret Show|A private pick/)).toBeNull();
  });

  it('a failed prepare hands the /api/events stream back and sends no switch (security review M2)', async () => {
    mocks.getDeviceMembers.mockResolvedValue([
      { user_id: 'user-1', display_name: 'One Person', username: 'one', role: 'viewer', switch: 'instant', active: true },
      { user_id: 'user-2', display_name: 'Two Person', username: 'two', role: 'viewer', switch: 'instant', active: false },
    ]);
    render(<LuminaApp />);
    await waitFor(() => expect(mocks.getDeviceMembers).toHaveBeenCalled());
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    vi.mocked(forgetPlaybackWarmup).mockRejectedValueOnce(new Error('warm-up stop failed'));
    act(() => openMemberPicker());
    await userEvent.click(await screen.findByRole('button', { name: 'Continue as Two Person' }));
    await waitFor(() => expect(workspace.holdEvents).toHaveBeenLastCalledWith(false));
    expect(workspace.holdEvents).toHaveBeenCalledWith(true);
    expect(mocks.switchMember).not.toHaveBeenCalled();
  });

  it('declining the unsaved-Settings prompt cancels a member switch before anything is torn down', async () => {
    mocks.getDeviceMembers.mockResolvedValue([
      { user_id: 'user-1', display_name: 'One Person', username: 'one', role: 'viewer', switch: 'instant', active: true },
      { user_id: 'user-2', display_name: 'Two Person', username: 'two', role: 'viewer', switch: 'instant', active: false },
    ]);
    function DirtyForm() { useUnsavedChanges(true, 'Account'); return null; }
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    window.history.replaceState(null, '', '/settings/account');
    render(<><LuminaApp /><DirtyForm /></>);
    await screen.findByRole('heading', { level: 1, name: 'Settings' });
    await waitFor(() => expect(mocks.getDeviceMembers).toHaveBeenCalled());
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    act(() => openMemberPicker());
    await userEvent.click(await screen.findByRole('button', { name: 'Continue as Two Person' }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(mocks.switchMember).not.toHaveBeenCalled();
    expect(workspace.holdEvents).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeTruthy();
    confirm.mockRestore();
  });

  it('a member switch lets the leaving member finish first: reco events, keepalive playback writes, the events stream', async () => {
    const order: string[] = [];
    const two: UserProfile = { ...user, id: 'user-2', username: 'two', display_name: 'Two Person' };
    mocks.getDeviceMembers.mockResolvedValue([
      { user_id: 'user-1', display_name: 'One Person', username: 'one', role: 'viewer', switch: 'instant', active: true },
      { user_id: 'user-2', display_name: 'Two Person', username: 'two', role: 'viewer', switch: 'instant', active: false },
    ]);
    mocks.sendRecoEvents.mockImplementation(async () => { order.push('reco'); });
    mocks.switchMember.mockImplementation(async () => { order.push('switch'); return { user: two }; });
    let answerPut = (): void => undefined;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { answerPut = () => { order.push('put answered'); resolve(new Response('{}', { status: 200 })); }; })));
    render(<LuminaApp />);
    await waitFor(() => expect(mocks.getDeviceMembers).toHaveBeenCalled());
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    workspace.holdEvents.mockImplementation((held: boolean) => { order.push(held ? 'events closed' : 'events open'); });
    recordRecoOpen({ list_id: 'list-1', key: 'key-1' } as never); // buffered under member one
    // The leaving member's player already sent its last checkpoint (keepalive), still unanswered.
    void updateRemotePlaybackProgress('remote-1', { source_identity: 'remote-1', source_url: 'https://example.test/v', position_seconds: 30, checkpoint_client_id: 'c', checkpoint_sequence: 1, expected_revision: 0 }, { keepalive: true }).catch(() => undefined);
    act(() => openMemberPicker());
    await userEvent.click(await screen.findByRole('button', { name: 'Continue as Two Person' }));
    await new Promise((resolve) => { setTimeout(resolve, 20); });
    expect(mocks.switchMember).not.toHaveBeenCalled(); // waiting for the checkpoint
    answerPut();
    await waitFor(() => expect(mocks.switchMember).toHaveBeenCalledWith('user-2'));
    await keepaliveWritesSettled();
    await waitFor(() => expect(order).toContain('events open'));
    expect(order.indexOf('reco')).toBeLessThan(order.indexOf('switch'));
    expect(order.indexOf('events closed')).toBeLessThan(order.indexOf('switch'));
    expect(order.indexOf('put answered')).toBeLessThan(order.indexOf('switch'));
    expect(order.filter((step) => step === 'reco')).toHaveLength(1); // nothing posted after the switch
    expect(workspace.loadAuthenticated).toHaveBeenCalled();
  });
});

describe('palette openers', () => {
  it('⌘K and Ctrl K open the palette with the input focused', async () => {
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    await userEvent.keyboard('{Meta>}k{/Meta}');
    expect(document.activeElement).toBe(await screen.findByRole('combobox'));
    cancelDialog();
    await waitFor(() => expect(screen.queryByRole('combobox')).toBeNull());
    await userEvent.keyboard('{Control>}k{/Control}');
    expect(document.activeElement).toBe(await screen.findByRole('combobox'));
  });

  it('Escape while the palette chunk is still loading cancels the open', async () => {
    await import('./features/palette/CommandPalette'); // warm, so the release mounts the palette at once
    let release = () => undefined as void;
    paletteChunk.gate = new Promise<void>((resolve) => { release = resolve; });
    try {
      render(<LuminaApp />);
      await screen.findByRole('heading', { level: 1 });
      await userEvent.click(screen.getByRole('button', { name: /^Search Lumina, / }));
      await userEvent.keyboard('{Escape}');
      paletteChunk.gate = null;
      release();
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1000)); });
      expect(screen.queryByRole('combobox')).toBeNull();
    } finally { paletteChunk.gate = null; release(); }
  });

  it('slash opens the palette only outside text fields', async () => {
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    await userEvent.keyboard('/');
    expect(document.activeElement).toBe(await screen.findByRole('combobox'));
    cancelDialog();
    await waitFor(() => expect(screen.queryByRole('combobox')).toBeNull());
    const field = document.createElement('input');
    document.body.append(field);
    field.focus();
    await userEvent.keyboard('/');
    expect(field.value).toBe('/');
    expect(screen.queryByRole('combobox')).toBeNull();
    field.remove();
  });

  it('the Add link button opens link mode and the Search trigger opens search', async () => {
    const browser = userEvent.setup();
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: 'Add a link' }));
    expect(await screen.findByRole('dialog', { name: 'Add a link' })).toBeTruthy();
    cancelDialog();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect((await openPalette(browser)).getAttribute('placeholder')).toBe('Search titles, channels, videos, settings…');
  });

  it('closing the palette forgets its query, so the next opening starts clean', async () => {
    const browser = userEvent.setup();
    render(<LuminaApp />);
    await browser.type(await openPalette(browser), 'alp');
    cancelDialog();
    await waitFor(() => expect(screen.queryByRole('combobox')).toBeNull());
    expect((await openPalette(browser) as HTMLInputElement).value).toBe('');
  });

  it('has no fixed banner and no combobox outside the palette', async () => {
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    expect(document.querySelector('.g-toast')).toBeNull(); // the live toast row; the retired .app-toast could never match
    expect(screen.queryByRole('combobox')).toBeNull();
  });
});

describe('drawer breakpoint', () => {
  it('between 681px and 960px the menu opens the drawer', async () => {
    drawerOnly = true;
    render(<LuminaApp />);
    await userEvent.click(await screen.findByRole('button', { name: 'Open navigation' }));
    expect(await screen.findByRole('dialog', { name: 'Mobile navigation' })).toBeTruthy();
  });
});

describe('shell toasts and openers', () => {
  let toaster: ReturnType<typeof useToast>;
  const Grab = () => { toaster = useToast(); return null; };

  it('sign-out clears the leaving member\'s toasts and their Undo', async () => {
    const browser = userEvent.setup();
    const undo = vi.fn();
    mocks.logoutSession.mockResolvedValue(undefined);
    window.history.replaceState(null, '', '/settings/account');
    render(<><Grab /><LuminaApp /></>);
    act(() => { toaster({ tone: 'info', message: 'Hidden for A.', action: { label: 'Undo', onAction: undo } }); });
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
    await browser.click(await screen.findByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(screen.queryByText('Hidden for A.')).toBeNull());
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(undo).not.toHaveBeenCalled();
  });

  it('the offline toast goes away when the connection returns', async () => {
    const view = render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...workspace, state: { ...workspaceState, jobsState: 'offline' } });
    view.rerender(<LuminaApp />);
    await screen.findByText("Lumina can't reach the server. Trying again…");
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...workspace, state: { ...workspaceState, jobsState: 'ready' } });
    view.rerender(<LuminaApp />);
    await waitFor(() => expect(screen.queryByText("Lumina can't reach the server. Trying again…")).toBeNull());
  });

  it('slash and Ctrl K do nothing while signed out or while a dialog is open', async () => {
    const base = mocks.useAuthenticatedWorkspace();
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...base, state: { ...workspaceState, authStage: 'login', currentUser: null } });
    const out = render(<LuminaApp />);
    const down = (init: KeyboardEventInit) => { const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }); document.body.dispatchEvent(event); return event; };
    expect(down({ key: '/' }).defaultPrevented).toBe(false);
    expect(down({ key: 'k', ctrlKey: true }).defaultPrevented).toBe(false);
    out.unmount();
    mocks.useAuthenticatedWorkspace.mockReturnValue({ ...base, state: workspaceState });
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1 });
    const dialog = document.createElement('dialog');
    dialog.setAttribute('open', '');
    document.body.append(dialog);
    expect(down({ key: '/' }).defaultPrevented).toBe(false);
    expect(down({ key: 'k', metaKey: true }).defaultPrevented).toBe(false);
    dialog.remove();
    expect(down({ key: '/' }).defaultPrevented).toBe(true);
  });
});

describe('account links (hooks stay unconditional)', () => {
  it.each(['#invite=abc123', '#reset=abc123'])('cancelling a %s link returns to the app instead of crashing', async (hash) => {
    window.history.replaceState(null, '', `/${hash}`);
    render(<LuminaApp />);
    await userEvent.click(await screen.findByRole('button', { name: 'Back to sign in' }));
    expect(screen.queryByRole('button', { name: 'Back to sign in' })).toBeNull();
    expect(document.body.textContent).not.toBe('');
    expect(screen.getByRole('navigation', { name: 'Primary' })).not.toBeNull();
  });
});
