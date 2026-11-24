import { use, useReducer, useState } from 'react';
import { act, fireEvent, render as baseRender, screen, waitFor, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider, useToast } from './ui';
import type { LibraryItem, MemberInterests, SourceAutomation, UserProfile, UserTag, YouTubeSearchResult } from './types';
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
import { artistSummary, movieSummary, titleDetail, titlePage } from './test/galleryFixtures';
import { docFixture } from './features/titles/editor/editorFixtures';
import { forgetTitles } from './features/gallery/titleCache';
import { forgetWallStores } from './features/gallery/wallPages';
import { capabilities, CHANNEL_ID, channelPage, remoteEntry } from './test/remoteFixtures';
import { recoEntry } from './test/recoFixtures';
import { openAppPath } from './features/channels/channelMention';
import { openMemberPicker } from './app/commands';
import { resetDeviceRing } from './features/auth/deviceRing';
import { recordRecoOpen } from './features/reco/recoEvents';
import { clearAlbumQueue } from './features/gallery/albumQueue';
import { chooseSelect } from './test/selectChoice';

// The app raises its shell messages as toasts, which need the provider main.tsx supplies.
const render = (ui: ReactElement, options: Parameters<typeof baseRender>[1] = {}) => baseRender(ui, { wrapper: ToastProvider, ...options });

const mocks = vi.hoisted(() => ({
  useAuthenticatedWorkspace: vi.fn(),
  listContinueWatching: vi.fn(),
  getMemberInterests: vi.fn(),
  skipOnboarding: vi.fn(),
  searchLibrary: vi.fn(),
  sourceSearch: vi.fn(),
  recordSearchHistory: vi.fn(),
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
  getServerNavigation: vi.fn(),
  getHomeArrivals: vi.fn(),
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
  playMusic: vi.fn(),
  getMusicSharedSession: vi.fn(),
  putMusicSharedSession: vi.fn(),
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

vi.mock('./features/music/musicSession', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./features/music/musicSession')>()),
  playMusic: mocks.playMusic,
}));

vi.mock('./features/music/musicSharedSession', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./features/music/musicSharedSession')>()),
  getMusicSharedSession: mocks.getMusicSharedSession,
  putMusicSharedSession: mocks.putMusicSharedSession,
}));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  listContinueWatching: mocks.listContinueWatching,
  getMemberInterests: mocks.getMemberInterests,
  skipOnboarding: mocks.skipOnboarding,
  searchLibrary: mocks.searchLibrary,
  sourceSearch: mocks.sourceSearch,
  recordSearchHistory: mocks.recordSearchHistory,
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
  getServerNavigation: mocks.getServerNavigation,
  getHomeArrivals: mocks.getHomeArrivals,
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
  kind: 'video',
  title: 'Film in the vault',
  uploader: 'Archive Channel',
  webpage_url: 'https://example.test/watch/film',
  status: 'available',
  metadata_json: {},
};
const musicItem: LibraryItem = {
  id: 'track-1', title: 'Song in the vault', kind: 'track', status: 'available', title_id: null, metadata_json: {},
};

function upNextSnapshot(items: YouTubeSearchResult[]) {
  return { items, categories: [], state: 'ready' as const, refreshing: false, stale: false, error: null };
}

let mobile = false;
async function openNavigationMenu(browser = userEvent.setup()) {
  if (!screen.queryByRole('dialog', { name: 'Navigation' })) await browser.click(screen.getByRole('button', { name: 'Open navigation' }));
}
async function navigateWithMenu(browser: ReturnType<typeof userEvent.setup>, name: string) {
  await openNavigationMenu(browser);
  await browser.click(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: name === 'Settings' ? 'My Settings' : name }));
}

let drawerOnly = false;
const workspaceState = createInitialWorkspaceState();

beforeEach(() => {
  window.history.replaceState(null, "", '/');
  mobile = false;
  drawerOnly = false;
  clearAlbumQueue();
  forgetWallStores();
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
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true,
    value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(performance.now()), 0),
  });

  Object.assign(workspaceState, createInitialWorkspaceState());
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
  mocks.getMemberInterests.mockRejectedValue(new TypeError('offline'));
  mocks.skipOnboarding.mockResolvedValue({ status: 'skipped', selected_keys: [], followed: [] });
  mocks.searchLibrary.mockResolvedValue({ items: [libraryItem] });
  mocks.sourceSearch.mockResolvedValue({ items: [], errors: [] });
  mocks.recordSearchHistory.mockResolvedValue({ id: 'history-1', query: 'film noir', created_at: '2026-10-22T12:00:00Z' });
  mocks.youtubeSearch.mockResolvedValue({ items: [] });
  mocks.getUpNext.mockResolvedValue(upNextSnapshot([]));
  mocks.getHomeRecommendations.mockResolvedValue(upNextSnapshot([]));
  mocks.getPopularDiscovery.mockRejectedValue(new Error('Popular is unavailable.'));
  mocks.previewUrl.mockResolvedValue({ kind: 'playlist', title: 'Channel', entries: [], raw: {} });
  mocks.createJob.mockResolvedValue({ id: 'job-acquisition', source_url: 'https://example.test/video-acquisition', status: 'queued' });
  mocks.playMusic.mockResolvedValue(undefined);
  // Video handoff checks the authenticated shared output even after a cold reload.
  const musicSnapshot = { revision: 0, queue: [], current_index: null, position_seconds: 0,
    repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 };
  mocks.getMusicSharedSession.mockResolvedValue(musicSnapshot);
  mocks.putMusicSharedSession.mockImplementation(async (payload: { base_revision: number }) => ({ ...musicSnapshot, revision: payload.base_revision + 1 }));
  mocks.getLibraryItem.mockReset().mockImplementation(async (id: string) => ({ ...libraryItem, id }));
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
  mocks.getServerNavigation.mockResolvedValue({ categories: ['movies', 'tv', 'anime', 'music', 'books', 'youtube', 'recordings', 'photos'], features: { streaming: true, requests: true } });
  mocks.getHomeArrivals.mockResolvedValue([]);
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

async function openPalette(browser: ReturnType<typeof userEvent.setup>) {
  await browser.click(screen.getByRole('button', { name: /^Search Lumina, / }));
  return screen.findByRole('combobox', { name: 'Search Lumina' });
}

async function openLinkPalette(browser: ReturnType<typeof userEvent.setup>) {
  await browser.click(await screen.findByRole('button', { name: 'Add a link' }));
  return screen.findByRole('combobox', { name: 'Add a link' });
}

function setMobile(value: boolean) {
  mobile = value;
}

function setDrawerOnly(value: boolean) {
  drawerOnly = value;
}

const fixture = {
  act, fireEvent, screen, waitFor, within, userEvent, useReducer, useState, useToast, render, mocks, user, libraryItem, musicItem, upNextSnapshot,
  openNavigationMenu, navigateWithMenu, workspaceState, cancelDialog, openPalette, openLinkPalette,
  setMobile, setDrawerOnly, paletteChunk,
  ApiRequestError, keepaliveWritesSettled, updateRemotePlaybackProgress, createInitialWorkspaceState, workspaceReducer,
  LuminaApp, ChannelsSurface, followOutcome, useUnsavedChanges, watchSurface, cancelSpeculativeStart,
  claimSpeculativeStart, forgetPlaybackWarmup, takePlayIntent, artistSummary, movieSummary, titleDetail, titlePage,
  docFixture, forgetTitles, forgetWallStores, capabilities, CHANNEL_ID, channelPage, remoteEntry, recoEntry,
  openAppPath, openMemberPicker, resetDeviceRing, recordRecoOpen, clearAlbumQueue, chooseSelect,
};

export { fixture };
export type { LibraryItem, MemberInterests, SourceAutomation, UserProfile, UserTag, YouTubeSearchResult, AcquisitionFormat };
