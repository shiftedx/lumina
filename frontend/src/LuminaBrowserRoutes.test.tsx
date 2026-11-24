import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const {
  act, fireEvent, screen, waitFor, within, userEvent, useReducer, useState, useToast, render, mocks, user, libraryItem, musicItem, upNextSnapshot,
  openNavigationMenu, navigateWithMenu, workspaceState, cancelDialog, openPalette, openLinkPalette,
  ApiRequestError, keepaliveWritesSettled, updateRemotePlaybackProgress, createInitialWorkspaceState, workspaceReducer,
  LuminaApp, ChannelsSurface, followOutcome, useUnsavedChanges, watchSurface, cancelSpeculativeStart,
  claimSpeculativeStart, forgetPlaybackWarmup, takePlayIntent, artistSummary, movieSummary, titleDetail, titlePage,
  docFixture, forgetTitles, forgetWallStores, capabilities, CHANNEL_ID, channelPage, remoteEntry, recoEntry,
  openAppPath, openMemberPicker, resetDeviceRing, recordRecoOpen, clearAlbumQueue, chooseSelect,
} = (await import('./LuminaKeyboard.fixture')).fixture;
import type { LibraryItem, MemberInterests, SourceAutomation, UserProfile, UserTag, YouTubeSearchResult, AcquisitionFormat } from './LuminaKeyboard.fixture';

describe('pending-member interest loading', () => {
  beforeEach(async () => {
    const api = await import('./api');
    vi.spyOn(api, 'getServerNavigation').mockResolvedValue({ categories: ['youtube'], features: { streaming: true, requests: false } });
  });
  afterEach(async () => {
    const api = await import('./api');
    vi.mocked(api.getServerNavigation).mockRestore();
  });
  const interests: MemberInterests = {
    categories: [{ key: 'music', label: 'Music' }, { key: 'film', label: 'Film' }],
    selected_keys: ['music'],
  };

  it('guards Continue until the selected interests finish loading while Skip stays available', async () => {
    const browser = userEvent.setup();
    let finish!: (value: MemberInterests) => void;
    workspaceState.currentUser = { ...user, onboarding_status: 'pending' };
    mocks.getMemberInterests.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    window.history.replaceState(null, '', '/streaming');
    render(<LuminaApp />);

    await browser.click(await screen.findByRole('button', { name: 'Set up my Streaming' }));
    expect(await screen.findByText('Loading interests…')).toBeTruthy();
    expect((screen.getByRole('button', { name: /^Continue$/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Skip for now' }) as HTMLButtonElement).disabled).toBe(false);

    await act(async () => { finish(interests); });
    expect((await screen.findByRole('checkbox', { name: /^Music$/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('button', { name: /^Continue$/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('retries a failed interest load without treating it as an empty choice', async () => {
    const browser = userEvent.setup();
    workspaceState.currentUser = { ...user, onboarding_status: 'pending' };
    mocks.getMemberInterests
      .mockRejectedValueOnce(new Error('temporarily unavailable'))
      .mockResolvedValueOnce({ ...interests, selected_keys: ['film'] });
    window.history.replaceState(null, '', '/streaming');
    render(<LuminaApp />);

    await browser.click(await screen.findByRole('button', { name: 'Set up my Streaming' }));
    expect((await screen.findByRole('alert')).textContent).toContain('We couldn’t load your interests.');
    expect((screen.getByRole('button', { name: /^Continue$/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Skip for now' }) as HTMLButtonElement).disabled).toBe(false);
    await browser.click(screen.getByRole('button', { name: 'Try again' }));

    expect((await screen.findByRole('checkbox', { name: /^Film$/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('button', { name: /^Continue$/ }) as HTMLButtonElement).disabled).toBe(false);
    expect(mocks.getMemberInterests).toHaveBeenCalledTimes(2);
  });

  it('ignores a late initial interest response after canonical Skip completes', async () => {
    const browser = userEvent.setup();
    let finish!: (value: MemberInterests) => void;
    workspaceState.currentUser = { ...user, onboarding_status: 'pending' };
    mocks.getMemberInterests.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    window.history.replaceState(null, '', '/streaming');
    render(<LuminaApp />);
    await waitFor(() => expect(mocks.getMemberInterests).toHaveBeenCalledTimes(1));

    await browser.click(await screen.findByRole('button', { name: 'Back to Home' }));
    expect(await screen.findByRole('button', { name: 'Choose interests' })).toBeTruthy();
    await act(async () => { finish(interests); });
    expect(screen.getByRole('button', { name: 'Choose interests' })).toBeTruthy();
  });
});

describe('browser routes', () => {
  it('does not re-read member navigation for ordinary route changes', async () => {
    const api = await import('./api');
    const navigation = vi.spyOn(api, 'getServerNavigation').mockResolvedValue({ categories: ['movies', 'tv', 'music', 'youtube'], features: { streaming: true, requests: false } });
    try {
      render(<LuminaApp />);
      await waitFor(() => expect(navigation).toHaveBeenCalledTimes(1));
      const go = (path: string) => act(() => { window.history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')); });
      go('/library/movies');
      await screen.findByRole('heading', { level: 1, name: 'Movies' });
      go('/streaming');
      await screen.findByRole('heading', { level: 1, name: 'Streaming' });
      go('/');
      await screen.findByRole('heading', { level: 1, name: 'Home' });
      expect(navigation).toHaveBeenCalledTimes(1);
    } finally { navigation.mockRestore(); }
  });

  it('keeps a Library watch route closed when its authenticated speaker output cannot be checked', async () => {
    mocks.getMusicSharedSession.mockRejectedValue(new Error('Speaker output unavailable'));
    window.history.replaceState(null, '', '/watch/library/library-1');
    render(<LuminaApp />);

    expect(await screen.findByText('Speaker output unavailable')).toBeTruthy();
    expect(mocks.getMusicSharedSession).toHaveBeenCalled();
    expect(document.querySelector('video')).toBeNull();
  });

  it('reopens a Library watch deep link, and back/forward walk the route stack', async () => {
    const browser = userEvent.setup();
    workspaceState.library = [];
    window.history.replaceState(null, '', '/watch/library/library-1');
    render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(mocks.getLibraryItem).toHaveBeenCalledWith('library-1');
    expect(window.location.pathname).toBe('/watch/library/library-1');

    await navigateWithMenu(browser, 'Movies');
    await screen.findByRole('heading', { level: 1, name: 'Movies' });
    expect(window.location.pathname).toBe('/video/movies');

    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe('/watch/library/library-1'));
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());

    window.history.forward();
    await screen.findByRole('heading', { level: 1, name: 'Movies' });
    expect(window.location.pathname).toBe('/video/movies');
  });

  it('asks for the resume point beside the item, not after it, and loads the item once', async () => {
    let resolveItem: ((item: LibraryItem) => void) | undefined;
    mocks.getLibraryItem.mockImplementationOnce(() => new Promise((resolve) => { resolveItem = resolve; }));
    workspaceState.library = [];
    window.history.replaceState(null, '', '/watch/library/library-1');
    render(<LuminaApp />);
    await waitFor(() => expect(mocks.getLibraryItem).toHaveBeenCalledWith('library-1'));
    expect(mocks.getPlaybackProgress).toHaveBeenCalledWith('library-1'); // the item has not answered yet
    resolveItem?.(libraryItem);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(mocks.getLibraryItem).toHaveBeenCalledTimes(1);
    expect(mocks.getPlaybackProgress).toHaveBeenCalledTimes(1);
  });

  it('does not start a timed watch link after navigation cancels its pending item lookup', async () => {
    const browser = userEvent.setup();
    let finishItem!: (item: LibraryItem) => void;
    workspaceState.library = [];
    mocks.getLibraryItem.mockImplementationOnce(() => new Promise((resolve) => { finishItem = resolve; }));
    window.history.replaceState(null, '', '/watch/library/library-1?t=42');
    render(<LuminaApp />);

    await waitFor(() => expect(mocks.getLibraryItem).toHaveBeenCalledWith('library-1'));
    await navigateWithMenu(browser, 'Home');
    await screen.findByRole('heading', { level: 1, name: 'Home' });
    await act(async () => { finishItem(libraryItem); });

    expect(window.location.pathname).toBe('/');
    expect(document.querySelector('video')).toBeNull();
  });

  it('clears an unstarted watch selection when navigation wins its pending history lookup', async () => {
    const browser = userEvent.setup();
    let finishProgress!: (progress: null) => void;
    mocks.getPlaybackProgress.mockImplementationOnce(() => new Promise((resolve) => { finishProgress = resolve; }));
    render(<LuminaApp />);

    await browser.click(await screen.findByRole('button', { name: /^Film in the vault,/ }));
    await waitFor(() => expect(mocks.getPlaybackProgress).toHaveBeenCalledWith(libraryItem.id));
    await navigateWithMenu(browser, 'Home');
    await screen.findByRole('heading', { level: 1, name: 'Home' });
    await act(async () => { finishProgress(null); });

    expect(window.location.pathname).toBe('/');
    expect(document.querySelector('video')).toBeNull();
  });

  it('starts only the newer watch route when its item lookup completes before the older one', async () => {
    let finishA!: (item: LibraryItem) => void;
    let finishB!: (item: LibraryItem) => void;
    const itemA = { ...libraryItem, id: 'library-a', title: 'Older route' };
    const itemB = { ...libraryItem, id: 'library-b', title: 'Newer route' };
    workspaceState.library = [];
    mocks.getLibraryItem.mockImplementation((id: string) => new Promise((resolve) => {
      if (id === itemA.id) finishA = resolve;
      else if (id === itemB.id) finishB = resolve;
    }));
    window.history.replaceState(null, '', `/watch/library/${itemA.id}?t=1`);
    render(<LuminaApp />);

    await waitFor(() => expect(mocks.getLibraryItem).toHaveBeenCalledWith(itemA.id));
    act(() => { openAppPath(`/watch/library/${itemB.id}?t=2`); });
    await waitFor(() => expect(mocks.getLibraryItem).toHaveBeenCalledWith(itemB.id));
    await act(async () => { finishB(itemB); });
    expect(await screen.findByText('Newer route')).toBeTruthy();
    await act(async () => { finishA(itemA); });

    expect(screen.getByText('Newer route')).toBeTruthy();
    expect(screen.queryByText('Older route')).toBeNull();
  });

  it('keeps the active watch item when its reselect cancels a pending successor', async () => {
    let finishB!: (item: LibraryItem) => void;
    const itemA = { ...libraryItem, id: 'library-a', title: 'Active route' };
    const itemB = { ...libraryItem, id: 'library-b', title: 'Pending route' };
    workspaceState.library = [];
    mocks.getLibraryItem.mockImplementation((id: string) => id === itemA.id
      ? Promise.resolve(itemA)
      : new Promise((resolve) => { if (id === itemB.id) finishB = resolve; }));
    window.history.replaceState(null, '', `/watch/library/${itemA.id}?t=1`);
    render(<LuminaApp />);

    expect(await screen.findByText('Active route')).toBeTruthy();
    act(() => { openAppPath(`/watch/library/${itemB.id}?t=2`); });
    await waitFor(() => expect(mocks.getLibraryItem).toHaveBeenCalledWith(itemB.id));
    act(() => { openAppPath(`/watch/library/${itemA.id}`); });
    await act(async () => { finishB(itemB); });

    expect(screen.getByText('Active route')).toBeTruthy();
    expect(screen.queryByText('Pending route')).toBeNull();
  });

  it('keeps the position when the open item is reopened without a time, and seeks when its link time is asked for again', async () => {
    const movie = { ...libraryItem, kind: 'movie' as const };
    workspaceState.library = [movie]; workspaceState.libraryRecent = [movie];
    mocks.getLibraryItem.mockResolvedValue(movie);
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
    expect(screen.queryByRole('dialog', { name: 'Resume playback?' })).toBeNull();

    window.history.pushState(null, '', '/watch/library/library-1?t=42');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await waitFor(() => expect(position).toBe(42));
    expect(document.querySelector('video')).toBe(player);
  });

  it('applies a link time once: the address drops it, and Back to the watch page does not seek there again', async () => {
    const browser = userEvent.setup();
    const movie = { ...libraryItem, kind: 'movie' as const };
    workspaceState.library = [movie]; workspaceState.libraryRecent = [movie];
    mocks.getLibraryItem.mockResolvedValue(movie);
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: movie.id, position_seconds: 30, duration_seconds: 120, completed: false });
    window.history.replaceState(null, '', '/watch/library/library-1?t=42');
    render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    await waitFor(() => expect(window.location.search).toBe(''));
    const player = document.querySelector('video') as HTMLVideoElement;
    let position = 50;
    Object.defineProperty(player, 'currentTime', { configurable: true, get: () => position, set: (value: number) => { position = value; } });

    await navigateWithMenu(browser, 'Movies');
    await screen.findByRole('heading', { level: 1, name: 'Movies' });
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe('/watch/library/library-1'));
    await new Promise((resolve) => { setTimeout(resolve, 20); });
    expect(position).toBe(50);
    expect(screen.queryByRole('dialog', { name: 'Resume playback?' })).toBeNull();
    expect(window.location.search).toBe('');

    // A new link time for the open item still seeks, and leaves the address clean.
    window.history.pushState(null, '', '/watch/library/library-1?t=42');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await waitFor(() => expect(position).toBe(42));
    await waitFor(() => expect(window.location.search).toBe(''));
  });

  it('a later Play of the same item after the player closed ignores the old link time', async () => {
    const browser = userEvent.setup();
    const movie = { ...libraryItem, kind: 'movie' as const };
    workspaceState.library = [movie]; workspaceState.libraryRecent = [movie];
    mocks.getLibraryItem.mockResolvedValue(movie);
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: 'library-1', position_seconds: 30, duration_seconds: 120, completed: false });
    window.history.replaceState(null, '', '/watch/library/library-1?t=42');
    render(<LuminaApp />);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    await navigateWithMenu(browser, 'Home');
    await browser.click(await screen.findByRole('button', { name: 'Close player' }));
    await waitFor(() => expect(document.querySelector('video')).toBeNull());

    await browser.click(screen.getByRole('button', { name: /^Film in the vault,/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Resume playback?' });
    await browser.click(within(dialog).getByRole('button', { name: 'Resume' }));
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

  it('adopts the warmed conversion for an unplayed movie', async () => {
    forgetTitles();
    await forgetPlaybackWarmup();
    const early = { session_id: 'unplayed-early', mode: 'remux', playback_url: '/api/playback-sessions/unplayed-early/index.m3u8', start: 0, kind: 'remux' };
    mocks.getLibraryItem.mockResolvedValue({ ...libraryItem, kind: 'movie' });
    mocks.getLocalPlaybackOptions.mockResolvedValue({ mode: 'remux', reason: null, facts: { duration: 120 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 1 });
    mocks.startLocalPlaybackSession.mockResolvedValue(early);
    mocks.getTitle.mockResolvedValue({
      id: 'movie-1', type: 'movie', name: 'Northern Lantern', genres: [], added_at: '2026-09-01T00:00:00Z', play_item_id: 'library-1',
      user_data: { played: false, is_favorite: false, position_seconds: 0, resume_item_id: null },
      studios: [], provider_ids: {}, people: [], versions: [], extras: [], children: [], has_recap: false, play_next: null,
    });
    mocks.listSimilarTitles.mockResolvedValue([]);
    window.history.replaceState(null, '', '/title/movie-1');
    render(<LuminaApp />);
    const play = await screen.findByRole('button', { name: 'Play' });
    await waitFor(() => expect(mocks.getLocalPlaybackOptions).toHaveBeenCalledWith('library-1'));
    fireEvent.pointerEnter(play);
    await waitFor(() => expect(mocks.startLocalPlaybackSession).toHaveBeenCalledWith('library-1', 0), { timeout: 2000 });
    fireEvent.click(play);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(vi.mocked(claimSpeculativeStart)).toHaveBeenCalledWith('library-1');
    expect(mocks.stopLocalPlaybackSession).not.toHaveBeenCalledWith('unplayed-early');
  });

  it('times the title page hero from the poster click, before the page renders', async () => {
    const browser = userEvent.setup();
    mocks.listTitles.mockResolvedValue(titlePage([movieSummary()]));
    mocks.getTitle.mockResolvedValue(titleDetail(movieSummary()));
    mocks.listSimilarTitles.mockResolvedValue([]);
    window.history.replaceState(null, '', '/library/movies');
    render(<LuminaApp />);
    const spotlight = await screen.findByRole('region', { name: 'Northern Lantern' });
    const poster = within(spotlight).getByRole('button', { name: 'Details' });
    const beforePage: boolean[] = [];
    mocks.markNavigation.mockClear();
    mocks.markNavigation.mockImplementation(() => { beforePage.push(document.querySelector('.g-route-loading, .t-page') === null); });
    await browser.click(poster);
    await waitFor(() => expect(window.location.pathname).toBe('/video/title/movie-1'));
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
    await waitFor(() => expect(`${window.location.pathname}${window.location.search}`).toBe('/video/title/series-1?season=1'));
    await browser.click(screen.getByRole('button', { name: 'Back to Shows' }));
    await waitFor(() => expect(window.location.pathname).toBe('/video/shows'));
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
    await waitFor(() => expect(`${window.location.pathname}${window.location.search}`).toBe('/video/title/t1/edit?tab=people'));
    expect(window.history.length).toBe(length);
    window.history.pushState(null, '', '/title/t1/edit');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await waitFor(() => expect((screen.getByRole('radio', { name: 'Details' }) as HTMLInputElement).checked).toBe(true));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('keeps contextual Settings search open and asks before leaving unsaved Settings', async () => {
    const browser = userEvent.setup();
    function DirtyAiForm() { useUnsavedChanges(true, 'AI & models'); return null; }
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    try {
      window.history.replaceState(null, '', '/settings/account');
      render(<><LuminaApp /><DirtyAiForm /></>);
      await screen.findByRole('heading', { level: 1, name: 'My settings' });

      await browser.click(screen.getByRole('button', { name: /^Search Lumina, / }));
      expect(await screen.findByRole('searchbox', { name: 'Search settings' })).toBe(document.activeElement);
      expect(confirm).not.toHaveBeenCalled();

      await navigateWithMenu(browser, 'Movies');
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(window.location.pathname).toBe('/settings/account');
    } finally { confirm.mockRestore(); }
  });

  it('canonicalizes an unknown path to Home without adding a history entry', async () => {
    window.history.replaceState(null, '', '/not-a-route');
    const before = window.history.length;
    render(<LuminaApp />);
    await waitFor(() => expect(window.location.pathname).toBe('/'));
    expect(window.history.length).toBe(before);
  });

  it('/music opens Audio and replaces the address', async () => {
    window.history.replaceState(null, '', '/music');
    const length = window.history.length;
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1, name: 'Music' });
    expect(window.location.pathname).toBe('/audio');
    expect(window.history.length).toBe(length);
    await openNavigationMenu();
    expect(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Music' }).getAttribute('aria-current')).toBe('page');
  });

  it('resolves a timed music Library route back to Audio and starts at the explicit link time', async () => {
    mocks.getLibraryItem.mockResolvedValueOnce(musicItem);
    window.history.replaceState(null, '', '/watch/library/track-1?t=37');

    render(<LuminaApp />);

    await waitFor(() => expect(mocks.playMusic).toHaveBeenCalledWith('user-1', { item_ids: ['track-1'] }, 'track-1', 37));
    await waitFor(() => expect(window.location.pathname).toBe('/audio'));
    expect(mocks.getPlaybackProgress).toHaveBeenCalledWith('track-1');
  });

  it('keeps the open Audio artist location when a direct music ID resolves asynchronously', async () => {
    mocks.getTitle.mockResolvedValue(titleDetail(artistSummary()));
    mocks.listSimilarTitles.mockResolvedValue([]);
    mocks.getLibraryItem.mockResolvedValueOnce({ ...musicItem, title_id: 'album-1' });
    window.history.replaceState(null, '', '/audio/title/artist-1');
    render(<LuminaApp />);
    await screen.findByRole('heading', { level: 1, name: 'Artist A' });

    act(() => { openAppPath('/watch/library/track-1?t=19'); });

    await waitFor(() => expect(mocks.playMusic).toHaveBeenCalledWith('user-1', { item_ids: ['track-1'] }, 'track-1', 19));
    await waitFor(() => expect(window.location.pathname).toBe('/audio/title/artist-1'));
  });

  it('reports a rejected foreground music start through the shell error UI', async () => {
    mocks.getLibraryItem.mockResolvedValueOnce(musicItem);
    mocks.playMusic.mockRejectedValueOnce(new Error('Audio device failed'));
    window.history.replaceState(null, '', '/watch/library/track-1');

    render(<LuminaApp />);

    expect(await screen.findByText('Audio device failed')).toBeTruthy();
  });

  it('replaces a Video artist alias with Audio so Back skips the redirect entry', async () => {
    mocks.getTitle.mockResolvedValue(titleDetail(artistSummary()));
    mocks.listSimilarTitles.mockResolvedValue([]);
    window.history.replaceState(null, '', '/video');
    const pushes: string[] = [];
    const push = window.history.pushState.bind(window.history);
    const spy = vi.spyOn(window.history, 'pushState').mockImplementation((data, unused, url) => { pushes.push(String(url)); push(data, unused, url); });
    render(<LuminaApp />);

    act(() => { openAppPath('/video/title/artist-1'); });
    await waitFor(() => expect(window.location.pathname).toBe('/audio/title/artist-1'));
    expect(pushes).toEqual(['/video/title/artist-1']);
    act(() => { window.history.back(); });
    await waitFor(() => expect(window.location.pathname).toBe('/video/movies'));
    spy.mockRestore();
  });

  it('Back from a title opened through the legacy Library URL returns to Movies', async () => {
    const browser = userEvent.setup();
    mocks.getLibrarySections.mockResolvedValue({ movies: 1, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 0, recordings: 0, deleted: 0 });
    mocks.listTitles.mockResolvedValue(titlePage([movieSummary()]));
    mocks.getTitle.mockResolvedValue(titleDetail(movieSummary()));
    mocks.listSimilarTitles.mockResolvedValue([]);
    window.history.replaceState(null, '', '/library');
    render(<LuminaApp />);
    const spotlight = await screen.findByRole('region', { name: 'Northern Lantern' });
    await browser.click(within(spotlight).getByRole('button', { name: 'Details' }));
    await screen.findByRole('heading', { level: 1, name: 'Northern Lantern' });
    await browser.click(document.querySelector('.t-back') as HTMLElement);
    await waitFor(() => expect(window.location.pathname).toBe('/video/movies'));
    await screen.findByRole('heading', { level: 1, name: 'Movies' });
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

describe('account links (hooks stay unconditional)', () => {
  it.each(['/#invite=abc123', '/invite#invite=abc123', '/#reset=abc123'])('cancelling a %s link returns to the app instead of crashing', async (path) => {
    window.history.replaceState(null, '', path);
    render(<LuminaApp />);
    expect(window.location.hash).toBe('');
    if (path.includes('invite')) expect(screen.getByRole('button', { name: 'Create my account' })).toBeTruthy();
    await userEvent.click(await screen.findByRole('button', { name: 'Back to sign in' }));
    expect(screen.queryByRole('button', { name: 'Back to sign in' })).toBeNull();
    expect(document.body.textContent).not.toBe('');
    expect(screen.getByRole('button', { name: 'Open navigation' })).not.toBeNull();
  });
});
