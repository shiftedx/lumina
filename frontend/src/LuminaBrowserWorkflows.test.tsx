import { describe, expect, it, vi } from 'vitest';
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

describe('household and Library curation integration', () => {
  const openNotes = async (browser: ReturnType<typeof userEvent.setup>) => browser.click(await screen.findByRole('tab', { name: 'Sharing & tags' }));
  // Library items open from their own tab now that All is the chaptered landing.
  const openYouTube = async (browser: ReturnType<typeof userEvent.setup>) => {
    act(() => { openAppPath('/video/youtube'); });
    await screen.findByRole('heading', { level: 1, name: 'YouTube' });
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
    await openYouTube(browser);
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault, / }));
    await openNotes(browser);

    const curation = await screen.findByRole('region', { name: 'About this Library item' });
    expect(within(curation).getByText('family')).not.toBeNull();
    chooseSelect(within(curation).getByRole('combobox', { name: /Who can see Film in the vault/ }), 'Shared with household');
    await waitFor(() => expect(mocks.updateLibraryItemVisibility).toHaveBeenCalledWith(ownedItem.id, 'shared'));
    await waitFor(() => expect(within(curation).getByRole('textbox', { name: 'Add a private tag' }).hasAttribute('disabled')).toBe(false));
    await browser.type(within(curation).getByRole('textbox', { name: 'Add a private tag' }), 'favorite');
    await browser.click(within(curation).getByRole('button', { name: 'Add tag' }));

    await waitFor(() => expect(mocks.createLibraryTag).toHaveBeenCalledWith(ownedItem.id, 'favorite'));
  });

  it('ignores a stale item mutation without overwriting or unlocking the newly selected item', async () => {
    const browser = userEvent.setup();
    const itemA = { ...libraryItem, id: 'item-a', title: 'Item A', user_id: user.id, visibility: 'private' as const };
    const itemB = { ...libraryItem, id: 'item-b', title: 'Item B', user_id: user.id, visibility: 'private' as const };
    workspaceState.library = [itemA, itemB];
    mocks.getLibraryItem.mockReset().mockImplementation(async (id: string) => id === itemA.id ? itemA : itemB);
    mocks.listLibraryTags.mockImplementation(async (id: string) => [{ id: `tag-${id}`, item_id: id, user_id: user.id, tag: id === itemA.id ? 'alpha' : 'bravo' }]);
    let finishA: ((item: LibraryItem) => void) | undefined;
    let finishB: ((tag: UserTag) => void) | undefined;
    mocks.updateLibraryItemVisibility.mockImplementation(() => new Promise((resolve) => { finishA = resolve; }));
    mocks.createLibraryTag.mockImplementation(() => new Promise((resolve) => { finishB = resolve; }));

    render(<LuminaApp />);
    await openYouTube(browser);
    await browser.click(await screen.findByRole('button', { name: /^Item A, / }));
    await openNotes(browser);
    const itemAVisibility = await screen.findByRole('combobox', { name: 'Who can see Item A' });
    chooseSelect(itemAVisibility, 'Shared with household');
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
    expect(screen.getByRole('combobox', { name: 'Who can see Item B' }).textContent).toBe('Private');
    expect(screen.getByRole('button', { name: 'Add tag' }).hasAttribute('disabled')).toBe(true);

    finishB?.({ id: 'tag-busy-b', item_id: itemB.id, user_id: user.id, tag: 'busy-b' });
    expect(await screen.findByText('busy-b')).not.toBeNull();
  });

  it('hides Item A private curation synchronously while Item B curation is deferred', async () => {
    const browser = userEvent.setup();
    const itemA = { ...libraryItem, id: 'deferred-a', title: 'Deferred A', user_id: user.id, visibility: 'private' as const };
    const itemB = { ...libraryItem, id: 'deferred-b', title: 'Deferred B', user_id: user.id, visibility: 'private' as const };
    workspaceState.library = [itemA, itemB];
    mocks.getLibraryItem.mockReset().mockImplementation(async (id: string) => id === itemA.id ? itemA : itemB);
    let finishBTags: ((tags: UserTag[]) => void) | undefined;
    mocks.listLibraryTags.mockImplementation((id: string) => id === itemA.id
      ? Promise.resolve([{ id: 'private-a-tag', item_id: itemA.id, user_id: user.id, tag: 'private-alpha' }])
      : new Promise((resolve) => { finishBTags = resolve; }));

    render(<LuminaApp />);
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
    mocks.getLibraryItem.mockReset().mockImplementation(async (id: string) => id === itemA.id ? itemA : itemB);
    mocks.listLibraryTags.mockImplementation((id: string) => id === itemA.id
      ? Promise.resolve([{ id: 'failed-private-tag', item_id: itemA.id, user_id: user.id, tag: 'secret-alpha' }])
      : Promise.reject(new Error('Item B notes are unavailable.')));

    render(<LuminaApp />);
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
  it.each([['Resume', 30], ['Start Over', 0]] as const)('offers %s for a movie using fresh progress', async (choice, expected) => {
    await forgetPlaybackWarmup();
    const browser = userEvent.setup();
    const movie = { ...libraryItem, kind: 'movie' as const, duration: 120 };
    workspaceState.library = [movie]; workspaceState.libraryRecent = [movie];
    mocks.getLibraryItem.mockResolvedValue(movie);
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: movie.id, position_seconds: 30, duration_seconds: 120, completed: false });
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault,/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Resume playback?' });
    expect(within(dialog).getByRole('heading', { name: movie.title })).toBeTruthy();
    expect(within(dialog).getByText('Resume playback from 0:30?')).toBeTruthy();
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Resume' }));
    expect(document.querySelector('video')).toBeNull();
    await browser.click(within(dialog).getByRole('button', { name: choice }));
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    const player = document.querySelector('video') as HTMLVideoElement;
    Object.defineProperty(player, 'duration', { configurable: true, value: 120 });
    fireEvent.loadedMetadata(player);
    expect(player.currentTime).toBe(expected);
    expect(mocks.getPlaybackProgress).toHaveBeenCalledWith(movie.id);
  });

  it('starts a new converted movie session at zero after Start Over', async () => {
    const browser = userEvent.setup();
    const movie = { ...libraryItem, kind: 'movie' as const, duration: 120 };
    workspaceState.library = [movie]; workspaceState.libraryRecent = [movie];
    mocks.getLibraryItem.mockResolvedValue(movie);
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: movie.id, position_seconds: 30, duration_seconds: 120, completed: false });
    mocks.getLocalPlaybackOptions.mockResolvedValue({ mode: 'remux', reason: null, facts: { duration: 120 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 1 });
    mocks.startLocalPlaybackSession.mockResolvedValue({ session_id: 'fresh', mode: 'remux', playback_url: '/api/playback-sessions/fresh/index.m3u8', start: 0, kind: 'remux' });
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault,/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Resume playback?' });
    await browser.click(within(dialog).getByRole('button', { name: 'Start Over' }));
    await waitFor(() => expect(mocks.startLocalPlaybackSession).toHaveBeenCalledWith(movie.id, 0, undefined));
  });

  it('does not open a chosen movie after the member session changes', async () => {
    const browser = userEvent.setup();
    const movie = { ...libraryItem, kind: 'movie' as const };
    workspaceState.library = [movie]; workspaceState.libraryRecent = [movie];
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: movie.id, position_seconds: 30, duration_seconds: 120, completed: false });
    let finishStop!: () => void;
    vi.mocked(cancelSpeculativeStart).mockReturnValueOnce(new Promise<void>((resolve) => { finishStop = resolve; }));
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault,/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Resume playback?' });
    await browser.click(within(dialog).getByRole('button', { name: 'Resume' }));
    mocks.useAuthenticatedWorkspace.mock.results[0].value.isSessionTokenCurrent.mockReturnValue(false);
    await act(async () => { finishStop(); });
    expect(document.querySelector('video')).toBeNull();
    expect(mocks.startLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('keeps browsing and lets the member retry when movie history is unavailable', async () => {
    const browser = userEvent.setup();
    const movie = { ...libraryItem, kind: 'movie' as const };
    workspaceState.library = [movie]; workspaceState.libraryRecent = [movie];
    mocks.getPlaybackProgress.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(null);
    render(<LuminaApp />);
    const play = await screen.findByRole('button', { name: /^Film in the vault,/ });
    await browser.click(play);
    expect(await screen.findByText('Playback history could not be loaded. Try playing again.')).toBeTruthy();
    expect(document.querySelector('video')).toBeNull();
    expect(mocks.startLocalPlaybackSession).not.toHaveBeenCalled();
    await browser.click(play);
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(screen.queryByRole('dialog', { name: 'Resume playback?' })).toBeNull();
  });

  it('retains a warmed session when an unplayed movie starts', async () => {
    const browser = userEvent.setup();
    const movie = { ...libraryItem, kind: 'movie' as const };
    workspaceState.library = [movie]; workspaceState.libraryRecent = [movie];
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault,/ }));
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    expect(vi.mocked(claimSpeculativeStart)).toHaveBeenCalledWith(movie.id);
    expect(vi.mocked(cancelSpeculativeStart)).not.toHaveBeenCalled();
  });

  it('requests item and history together for an explicit item ID', async () => {
    let finishItem!: (item: LibraryItem) => void;
    mocks.getLibraryItem.mockReturnValueOnce(new Promise((resolve) => { finishItem = resolve; }));
    window.history.replaceState(null, '', '/watch/library/library-9');
    render(<LuminaApp />);
    await waitFor(() => expect(mocks.getPlaybackProgress).toHaveBeenCalledWith('library-9'));
    expect(document.querySelector('video')).toBeNull();
    await act(async () => { finishItem({ ...libraryItem, id: 'library-9', kind: 'movie' }); });
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
  });

  it('cancels an episode choice without starting playback or changing progress', async () => {
    const browser = userEvent.setup();
    const episode = { ...libraryItem, kind: 'episode' as const };
    workspaceState.library = [episode]; workspaceState.libraryRecent = [episode];
    mocks.getLibraryItem.mockResolvedValue(episode);
    mocks.getPlaybackProgress.mockResolvedValue({ item_id: episode.id, position_seconds: 30, duration_seconds: 120, completed: false });
    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault,/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Resume playback?' });
    await browser.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Resume playback?' })).toBeNull();
    expect(document.querySelector('video')).toBeNull();
    expect(mocks.startLocalPlaybackSession).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /^Film in the vault,/ })).toBeTruthy();
  });

  it('starts local playback at the resume point once history arrives, without waiting for the item refresh', async () => {
    const browser = userEvent.setup();
    let resolveItem: ((item: LibraryItem) => void) | undefined;
    let resolveProgress: ((progress: { item_id: string; position_seconds: number; duration_seconds: number; completed: boolean }) => void) | undefined;
    mocks.getLibraryItem.mockImplementationOnce(() => new Promise((resolve) => { resolveItem = resolve; }));
    mocks.getPlaybackProgress.mockImplementationOnce(() => new Promise((resolve) => { resolveProgress = resolve; }));

    render(<LuminaApp />);
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault/ }));
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
    await browser.click(await screen.findByRole('button', { name: /^Film in the vault/ }));

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

  it('keeps a playback chain across remote player remounts and skips a stale earlier source', async () => {
    const browser = userEvent.setup();
    const source = (id: string) => `https://www.youtube.com/watch?v=${id}`;
    const a = remoteEntry('chain-a', { title: 'Watch A', webpage_url: source('chain-a') });
    const b = remoteEntry('chain-b', { title: 'Watch B', webpage_url: source('chain-b') });
    const c = remoteEntry('chain-c', { title: 'Watch C', webpage_url: source('chain-c') });
    mocks.previewUrl.mockImplementation(async (payload: { source_url: string }) => {
      const id = new URL(payload.source_url).searchParams.get('v') || '';
      return { kind: 'video', title: `Watch ${id.at(-1)?.toUpperCase()}`, webpage_url: payload.source_url,
        entries: [], raw: { id, extractor: 'youtube', duration: 120 },
        playback: { status: 'ready', stream_id: id, transport: 'progressive', media_kind: 'video',
          playback_url: `/api/remote-streams/${id}/content`, content_type: 'video/mp4', has_video: true,
          has_audio: true, seekable: true } };
    });
    mocks.getUpNext.mockImplementation(async (payload: { source_url: string }) => upNextSnapshot(
      payload.source_url === a.webpage_url ? [b] : payload.source_url === b.webpage_url ? [a, c] : [],
    ));

    render(<LuminaApp />);
    await browser.type(await openPalette(browser), `${a.webpage_url}{Enter}`);
    await screen.findByRole('heading', { level: 1, name: 'Watch A' });
    await screen.findByRole('button', { name: /^Watch B/ });
    fireEvent.ended(document.querySelector('video')!);
    await screen.findByRole('heading', { level: 1, name: 'Watch B' });
    await waitFor(() => expect(mocks.getUpNext).toHaveBeenCalledWith(expect.objectContaining({
      source_url: b.webpage_url, excluded_sources: ['youtube:chain-a', 'youtube:chain-b'],
    }), { signal: expect.any(AbortSignal) }));
    await screen.findByRole('button', { name: /^Watch C/ });
    fireEvent.ended(document.querySelector('video')!);
    await screen.findByRole('heading', { level: 1, name: 'Watch C' });
    expect(mocks.getUpNext).toHaveBeenCalledWith(expect.objectContaining({
      source_url: c.webpage_url, excluded_sources: ['youtube:chain-a', 'youtube:chain-b', 'youtube:chain-c'],
    }), { signal: expect.any(AbortSignal) });
    await browser.type(await openPalette(browser), `${a.webpage_url}{Enter}`);
    await screen.findByRole('heading', { level: 1, name: 'Watch A' });
    await waitFor(() => expect(mocks.getUpNext).toHaveBeenLastCalledWith(expect.objectContaining({
      source_url: a.webpage_url, excluded_sources: [],
    }), { signal: expect.any(AbortSignal) }));
  });

  it('preserves the recent chain when autoplay opens a saved YouTube copy', async () => {
    const browser = userEvent.setup();
    const sourceA = 'https://www.youtube.com/watch?v=chain-a';
    const saved = { ...libraryItem, id: 'saved-chain-b', title_id: null, remote_id: 'chain-b',
      title: 'Watch B', extractor: 'youtube', webpage_url: 'https://www.youtube.com/watch?v=chain-b',
      metadata_json: { id: 'chain-b', extractor: 'youtube' } };
    const next = remoteEntry('chain-b', { title: 'Watch B', webpage_url: saved.webpage_url, saved_item_id: saved.id });
    mocks.previewUrl.mockResolvedValue({ kind: 'video', title: 'Watch A', webpage_url: sourceA, entries: [],
      raw: { id: 'chain-a', extractor: 'youtube', duration: 120 },
      playback: { status: 'ready', stream_id: 'chain-a', transport: 'progressive', media_kind: 'video',
        playback_url: '/api/remote-streams/chain-a/content', content_type: 'video/mp4', has_video: true,
        has_audio: true, seekable: true } });
    mocks.getUpNext.mockImplementation(async (payload: { source_url: string }) => upNextSnapshot(payload.source_url === sourceA ? [next] : []));
    mocks.getLibraryItem.mockResolvedValue(saved);

    render(<LuminaApp />);
    await browser.type(await openPalette(browser), `${sourceA}{Enter}`);
    await screen.findByRole('button', { name: /^Watch B/ });
    fireEvent.ended(document.querySelector('video')!);
    await waitFor(() => expect(mocks.getUpNext).toHaveBeenCalledWith(expect.objectContaining({
      source_url: saved.webpage_url, excluded_sources: ['youtube:chain-a', 'youtube:chain-b'],
    }), { signal: expect.any(AbortSignal) }));
  });

  it.each([
    { annotated: true, recording: false },
    { annotated: false, recording: false },
    { annotated: true, recording: true },
  ])('opens an Up next recommendation from its saved copy ($annotated, recording: $recording)', async ({ annotated, recording }) => {
    const browser = userEvent.setup();
    const source = 'https://www.youtube.com/watch?v=streamed';
    const saved = { ...libraryItem, id: 'saved-follow-up', remote_id: 'saved', title: 'Saved follow-up', extractor: 'youtube', webpage_url: 'https://youtu.be/saved' };
    // An annotated copy can be outside the loaded library page; older responses match the canonical source.
    if (!annotated) workspaceState.library = [saved];
    mocks.getUpNext.mockResolvedValue(upNextSnapshot([{ id: 'saved', title: 'Saved follow-up', source: 'youtube', webpage_url: 'https://www.youtube.com/watch?v=saved', saved_item_id: annotated ? saved.id : null, kind: recording ? 'live' : 'video', capabilities: capabilities('youtube', recording ? 'completed_live' : 'vod') }]));
    mocks.previewUrl.mockResolvedValue({ kind: 'video', title: 'Streamed video', webpage_url: source, entries: [], raw: { id: 'streamed', extractor: 'youtube' } });
    mocks.getLibraryItem.mockResolvedValue(saved);

    render(<LuminaApp />);
    await browser.type(await openPalette(browser), `${source}{Enter}`);
    await browser.click(await screen.findByRole('button', { name: /^Saved follow-up/ }));

    await waitFor(() => expect(document.querySelector('video')).not.toBeNull());
    if (annotated) expect(mocks.getLibraryItem).toHaveBeenCalledWith('saved-follow-up');
    else expect(mocks.getLibraryItem).not.toHaveBeenCalled();
    await waitFor(() => expect(window.location.pathname).toBe('/watch/library/saved-follow-up'));
    expect(mocks.previewUrl).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(document.querySelector('.g-watch-kicker')?.textContent).toBe('YouTube · In your library'));
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
    await navigateWithMenu(browser, 'Settings');
    await browser.click(within(await screen.findByRole('navigation', { name: 'Settings sections' })).getByRole('link', { name: 'Downloads' }));
    chooseSelect(await screen.findByRole('combobox', { name: 'Default quality' }), 'Video · 1080p');
    // Nothing hides behind a disclosure: Container is one row away.
    chooseSelect(screen.getByRole('combobox', { name: 'Container' }), 'MKV');
    const subtitles = screen.getByRole('checkbox', { name: 'Download subtitles when available' });
    if (!(subtitles as HTMLInputElement).checked) await browser.click(subtitles);
    await browser.type(screen.getByRole('textbox', { name: 'Folder inside the Library' }), 'family/protected');

    await browser.type(await openLinkPalette(browser), 'https://example.test/video-acquisition{Enter}');
    expect(await screen.findByRole('heading', { level: 1, name: 'Inspected acquisition' })).not.toBeNull();

    await browser.click(screen.getByRole('button', { name: 'Save to library' }));
    await vi.waitFor(() => expect(mocks.createJob).toHaveBeenCalledTimes(1));

    expect(mocks.previewUrl).toHaveBeenNthCalledWith(1, expect.objectContaining({
      format_selection: expect.objectContaining({ preset: 'best_1080p', output_container: 'mkv', subtitles: true }),
    }), { signal: expect.any(AbortSignal) });
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
    await browser.type(await openLinkPalette(browser), `${sourceUrl}{Enter}`);
    await screen.findByRole('heading', { level: 1, name: 'Navigation film' });
    await browser.click(screen.getByRole('button', { name: 'Save to library' }));
    await vi.waitFor(() => expect(finishQueue).toBeTypeOf('function'));

    await navigateWithMenu(browser, 'Streaming');
    await browser.type(await openLinkPalette(browser), `${sourceUrl}{Enter}`);
    await screen.findByRole('heading', { level: 1, name: 'Navigation film' });

    const duplicate = screen.getByRole('button', { name: 'Save to library' });
    expect(duplicate.hasAttribute('disabled')).toBe(true);
    await browser.click(duplicate);
    expect(mocks.createJob).toHaveBeenCalledTimes(1);

    finishQueue?.({ id: 'job-navigation', source_url: sourceUrl, status: 'queued' });
    await waitFor(() => expect(duplicate.hasAttribute('disabled')).toBe(false));
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
    await screen.findByRole('heading', { level: 1, name: 'My settings' });
    await waitFor(() => expect(mocks.getDeviceMembers).toHaveBeenCalled());
    const workspace = mocks.useAuthenticatedWorkspace.mock.results[0].value;
    act(() => openMemberPicker());
    await userEvent.click(await screen.findByRole('button', { name: 'Continue as Two Person' }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(mocks.switchMember).not.toHaveBeenCalled();
    expect(workspace.holdEvents).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { level: 1, name: 'My settings' })).toBeTruthy();
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
    vi.stubGlobal('fetch', vi.fn((url, init) => {
      if (url !== '/api/playback/remote/remote-1' || init?.method !== 'PUT') return new Promise<Response>(() => undefined);
      return new Promise<Response>((resolve) => { answerPut = () => { order.push('put answered'); resolve(new Response('{}', { status: 200 })); }; });
    }));
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
