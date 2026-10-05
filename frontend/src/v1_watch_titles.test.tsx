import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LibraryItem, LibraryUpNext, SubtitleTrack, TitleDetail, TitleSummary, YouTubeSearchResult } from './types';
import { AccessProvider } from './features/access/access';
import { albumDetail, albumSummary, albumTrack } from './test/galleryFixtures';

vi.mock('hls.js', () => import('./test/fakeHls'));
const api = vi.hoisted(() => ({
  getTitle: vi.fn(), getLibraryUpNext: vi.fn(), getWatchQueue: vi.fn(), listSubtitleTracks: vi.fn(), getMediaSegments: vi.fn(), getRecap: vi.fn(), getMuteRanges: vi.fn(),
  getLocalPlaybackOptions: vi.fn(), startLocalPlaybackSession: vi.fn(), stopLocalPlaybackSession: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const audioGraph = vi.hoisted(() => {
  const graph = { mode: 'webaudio' as const, setLoudnessGain: vi.fn(), setMuteRanges: vi.fn(), setVolume: vi.fn(), resume: vi.fn(async () => undefined), schedule: vi.fn() };
  return { graph, audioGraphFor: vi.fn(() => graph) };
});
vi.mock('./features/watch/audioGraph', () => ({ audioGraphFor: audioGraph.audioGraphFor }));
const { watchSurface } = await import('./test/watchSurface');
const { clearAlbumQueue, setAlbumQueue } = await import('./features/gallery/albumQueue');

afterEach(() => vi.resetAllMocks());

const userData = { played: false, is_favorite: false, position_seconds: 0 };
const episode = (id: string, index: number): TitleSummary => ({ id, type: 'episode', name: `Episode ${index}`, series_id: 's1', series_name: 'Harbor Lights', season_number: 1, index_number: index, play_item_id: `i-${id}`, genres: [], added_at: '2026-09-01T00:00:00Z', user_data: userData });
const detail: TitleDetail = { ...episode('e1', 1), studios: [], provider_ids: {}, people: [], versions: [], extras: [], children: [], has_recap: false };
const followingEpisodes: LibraryUpNext = { kind: 'episodes', title: 'Harbor Lights', title_id: 's1', current_id: 'e1', items: [episode('e2', 2), { ...episode('e3', 3), user_data: { ...userData, played: true } }] };
const item = { id: 'i-e1', title: 'Harbor Lights S01E01', status: 'available', kind: 'episode', title_id: 'e1', metadata_json: {} } as LibraryItem;
const direct = { mode: 'direct', reason: null, facts: { container: 'webm', video_codec: 'vp9', audio_codec: 'opus', width: 1920, height: 1080, duration: 1320 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null };
const textTrack: SubtitleTrack = { id: 's:0', label: 'English', origin: 'sidecar', format: 'text', forced: false, default: false, hearing_impaired: false, url: '/api/library/i-e1/subtitle-tracks/s:0.vtt' };

function available(overrides: Partial<typeof api> = {}) {
  api.getTitle.mockResolvedValue(detail);
  api.getLibraryUpNext.mockResolvedValue(followingEpisodes);
  api.getWatchQueue.mockResolvedValue({ entries: [], revision: 0 });
  api.listSubtitleTracks.mockResolvedValue([textTrack]);
  api.getMediaSegments.mockResolvedValue({ item_id: 'i-e1', segments: [] });
  api.getRecap.mockRejectedValue(new Error('404'));
  api.getMuteRanges.mockResolvedValue([]);
  api.getLocalPlaybackOptions.mockResolvedValue(direct);
  Object.assign(api, overrides);
}

describe('Watch for a title', () => {
  it('credits the show, adds subtitle tracks and settings, and plays the next episode when this one ends', async () => {
    available();
    const onOpenTitle = vi.fn();
    const onOpenLibraryItem = vi.fn();
    render(watchSurface({ selection: { kind: 'library', item }, onOpenLibraryItem, onOpenTitle }));
    await userEvent.click(await screen.findByRole('button', { name: 'Harbor Lights' }));
    expect(onOpenTitle).toHaveBeenCalledWith(detail);
    expect(screen.getByText(/S1 · E1 · Episode 1/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Playback settings' })).toBeTruthy();
    await waitFor(() => expect(document.querySelector('video track')?.id).toBe('s:0'));
    await waitFor(() => expect(api.getLibraryUpNext).toHaveBeenCalledWith('i-e1'));
    await screen.findByRole('heading', { name: 'Up next' });
    fireEvent.ended(document.querySelector('video') as HTMLVideoElement);
    await waitFor(() => expect(onOpenLibraryItem).toHaveBeenCalledWith('i-e2'));
  });

  it('marks the chosen text subtitle as the default track so a restart keeps it', async () => {
    available();
    render(watchSurface({ selection: { kind: 'library', item } }));
    await waitFor(() => expect(document.querySelector('video track')?.id).toBe('s:0'));
    expect(document.querySelector('video track')?.hasAttribute('default')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Playback settings' }));
    await userEvent.click(await screen.findByRole('radio', { name: /English/ }));
    await waitFor(() => expect(document.querySelector('video track')?.hasAttribute('default')).toBe(true));
  });

  it('opens the series from the episode byline with arrow keys, never Tab', async () => {
    available();
    const onOpenTitle = vi.fn();
    // jsdom has no layout: the byline sits above the actions row.
    const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const top = this.closest('.watch-episode-byline') ? 0 : this.closest('.watch-actions') ? 100 : 50;
      return { left: 0, top, width: 100, height: 40, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
    render(watchSurface({ selection: { kind: 'library', item }, onOpenTitle }));
    await screen.findByRole('button', { name: 'Harbor Lights' });
    const share = screen.getByRole('button', { name: /Share/ });
    share.focus();
    // Nothing below the actions: the key keeps its native page scroll instead of jumping to the byline.
    expect(fireEvent.keyDown(share, { key: 'ArrowDown' })).toBe(true);
    expect(document.activeElement).toBe(share);
    const follow = screen.getByRole('button', { name: /Follow channel/ });
    await userEvent.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(follow);
    await userEvent.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Harbor Lights' }));
    await userEvent.keyboard('{ArrowDown}{ArrowDown}');
    expect(document.activeElement).toBe(share);
    await userEvent.keyboard('{ArrowUp}{ArrowUp}{Enter}');
    expect(onOpenTitle).toHaveBeenCalledWith(detail);
    rect.mockRestore();
  });

  it('degrades to plain playback when the title, subtitle, segment and recap endpoints are missing', async () => {
    available();
    for (const mock of [api.getTitle, api.getLibraryUpNext, api.listSubtitleTracks, api.getMediaSegments]) mock.mockRejectedValue(new Error('404'));
    const onOpenLibraryItem = vi.fn();
    render(watchSurface({ selection: { kind: 'library', item }, onOpenLibraryItem }));
    await waitFor(() => expect(document.querySelector('video')?.getAttribute('src')).toBe('/api/library/i-e1/media'));
    await userEvent.click(screen.getByRole('button', { name: 'Playback settings' }));
    expect(screen.getByRole('radio', { name: 'Off' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Harbor Lights' })).toBeNull();
    fireEvent.ended(document.querySelector('video') as HTMLVideoElement);
    expect(onOpenLibraryItem).not.toHaveBeenCalled();
  });

  it('burns in an image subtitle through the session, and switches version where the member is', async () => {
    available();
    api.getTitle.mockResolvedValue({ ...detail, versions: [{ item_id: 'i-e1', height: 2160, hdr: true }, { item_id: 'i-e1-sd', height: 480, hdr: false }] });
    api.listSubtitleTracks.mockResolvedValue([{ ...textTrack, id: 'i:3', format: 'image', origin: 'embedded', url: null }]);
    // The SD copy needs conversion, so the version switch itself restarts a session.
    api.getLocalPlaybackOptions.mockImplementation(async (id: string) => (id === 'i-e1-sd' ? { ...direct, mode: 'transcode' } : direct));
    api.startLocalPlaybackSession.mockResolvedValue({ session_id: 'x', mode: 'transcode', playback_url: '/api/playback-sessions/x/index.m3u8', start: 0 });
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    render(watchSurface({ selection: { kind: 'library', item } }));
    await userEvent.click(await screen.findByRole('button', { name: 'Playback settings' }));
    await userEvent.click(await screen.findByRole('radio', { name: /burned in/ }));
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledWith('i-e1', 0, { subtitle: 'i:3' }));
    Object.defineProperty(document.querySelector('video') as HTMLVideoElement, 'currentTime', { configurable: true, value: 312.4, writable: true });
    await userEvent.click(await screen.findByRole('radio', { name: '480p' }));
    // The player probes the chosen version and restarts its session where the member was.
    await waitFor(() => expect(api.getLocalPlaybackOptions).toHaveBeenLastCalledWith('i-e1-sd'));
    // The old file's burned-in stream does not carry over; the new version's own tracks load.
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenLastCalledWith('i-e1', 312, { version_id: 'i-e1-sd' }));
    await waitFor(() => expect(api.listSubtitleTracks).toHaveBeenLastCalledWith('i-e1-sd'));
    expect((screen.getByRole('radio', { name: 'Off' }) as HTMLInputElement).checked).toBe(true);
  });
});

describe('Up next for library media', () => {
  const movie = (id: string, name: string, year: number): TitleSummary => ({ id, type: 'movie', name, year, play_item_id: `i-${id}`, genres: [], added_at: '2026-09-01T00:00:00Z', user_data: userData });
  const film = { id: 'i-m1', title: 'Movie', status: 'available', kind: 'movie', title_id: 'm1', metadata_json: {} } as LibraryItem;
  const collection: LibraryUpNext = { kind: 'collection', title: 'Saga', title_id: 'b1', current_id: 'm1', items: [movie('m0', 'Prequel', 2018), movie('m1', 'Movie', 2020), movie('m2', 'Sequel', 2022)] };
  const filmDetail: TitleDetail = { ...movie('m1', 'Movie', 2020), studios: [], provider_ids: {}, people: [], versions: [], extras: [], children: [], has_recap: false };

  it('lists the following episodes with their state, opens one, links the series, and hides the empty queue', async () => {
    available();
    const onOpenLibraryItem = vi.fn();
    const onOpenTitle = vi.fn();
    render(watchSurface({ selection: { kind: 'library', item }, onOpenLibraryItem, onOpenTitle }));
    const rail = await screen.findByRole('region', { name: 'Up next' });
    expect(rail.textContent).toContain('S1 · E2 · Episode 2');
    expect(rail.textContent).toContain('Watched');
    expect(screen.queryByText(/Related videos appear here/)).toBeNull();
    await waitFor(() => expect(api.getWatchQueue).toHaveBeenCalled());
    expect(screen.queryByRole('heading', { name: /Your queue/ })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /S1 · E2 · Episode 2/ }));
    expect(onOpenLibraryItem).toHaveBeenCalledWith('i-e2');
    await userEvent.click(screen.getByRole('button', { name: 'See all episodes' }));
    expect(onOpenTitle).toHaveBeenCalledWith(detail);
  });

  it('does not play the next episode when the member turned autoplay off', async () => {
    available();
    const onOpenLibraryItem = vi.fn();
    render(watchSurface({ selection: { kind: 'library', item }, onOpenLibraryItem, autoplayUpNext: false }));
    expect((await screen.findByRole('switch', { name: 'Autoplay' }) as HTMLInputElement).checked).toBe(false);
    fireEvent.ended(document.querySelector('video') as HTMLVideoElement);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(onOpenLibraryItem).not.toHaveBeenCalled();
  });

  it('stops instead of advancing once watching time is up', async () => {
    available();
    const onOpenLibraryItem = vi.fn();
    const access = { access: { sections: null, blocked_streaming: [], followed_only: false, allowed_now: true, until: null, remaining_minutes: 0 }, stop: null, refresh: () => undefined };
    render(<AccessProvider value={access}>{watchSurface({ selection: { kind: 'library', item }, onOpenLibraryItem })}</AccessProvider>);
    await screen.findByRole('region', { name: 'Up next' });
    fireEvent.ended(document.querySelector('video') as HTMLVideoElement);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(onOpenLibraryItem).not.toHaveBeenCalled();
  });

  it('shows a movie’s collection in order with this film marked, and never starts the next film by itself', async () => {
    available();
    api.getTitle.mockResolvedValue(filmDetail);
    api.getLibraryUpNext.mockResolvedValue(collection);
    const onOpenLibraryItem = vi.fn();
    render(watchSurface({ selection: { kind: 'library', item: film }, onOpenLibraryItem }));
    const rail = await screen.findByRole('region', { name: 'Saga' });
    expect([...rail.querySelectorAll('strong')].map((node) => node.textContent)).toEqual(['Prequel', 'Movie', 'Sequel']);
    const current = rail.querySelector('[aria-current="true"]') as HTMLButtonElement;
    expect(current.textContent).toContain('Now playing');
    expect(current.disabled).toBe(true);
    expect(screen.queryByRole('switch', { name: 'Autoplay' })).toBeNull();
    fireEvent.ended(document.querySelector('video') as HTMLVideoElement);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(onOpenLibraryItem).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: /Sequel/ }));
    expect(onOpenLibraryItem).toHaveBeenCalledWith('i-m2');
  });

  it('shows no Up next at all for a movie outside a collection', async () => {
    available();
    api.getTitle.mockResolvedValue(filmDetail);
    api.getLibraryUpNext.mockResolvedValue({ kind: 'none', items: [] });
    render(watchSurface({ selection: { kind: 'library', item: film } }));
    await waitFor(() => expect(api.getLibraryUpNext).toHaveBeenCalledWith('i-m1'));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.queryByRole('heading', { name: 'Up next' })).toBeNull();
    expect(screen.queryByText(/Related videos/)).toBeNull();
  });
});

describe('Media Session (picture-in-picture window and system media controls)', () => {
  function fakeMediaSession() {
    const handlers = new Map<string, (() => void) | null>();
    const session = { metadata: null as unknown, setActionHandler: (action: string, handler: (() => void) | null) => handlers.set(action, handler) };
    Object.defineProperty(navigator, 'mediaSession', { configurable: true, value: session });
    vi.stubGlobal('MediaMetadata', class { constructor(init: object) { Object.assign(this, init); } });
    return { session, handlers };
  }
  afterEach(() => { delete (navigator as { mediaSession?: unknown }).mediaSession; vi.unstubAllGlobals(); });

  it('names the episode and its show, skips to the next episode, and clears when Watch closes', async () => {
    available();
    const { session, handlers } = fakeMediaSession();
    const onOpenLibraryItem = vi.fn();
    const { unmount } = render(watchSurface({ selection: { kind: 'library', item }, onOpenLibraryItem }));
    await waitFor(() => expect(handlers.get('nexttrack')).toBeTypeOf('function'));
    expect(session.metadata).toMatchObject({ title: 'Harbor Lights S01E01', artist: 'Harbor Lights' });
    expect(handlers.get('previoustrack')).toBeNull(); // an episode list holds only the following episodes
    handlers.get('nexttrack')?.();
    expect(onOpenLibraryItem).toHaveBeenCalledWith('i-e2');
    unmount();
    expect(handlers.get('nexttrack')).toBeNull();
    expect(session.metadata).toBeNull();
  });

  it('goes back to the previous film in a collection', async () => {
    available();
    api.getLibraryUpNext.mockResolvedValue({ kind: 'collection', title: 'Saga', title_id: 'b1', current_id: 'e1', items: [{ ...episode('m0', 0), type: 'movie' }, { ...episode('e1', 1), type: 'movie' }] });
    const { handlers } = fakeMediaSession();
    const onOpenLibraryItem = vi.fn();
    render(watchSurface({ selection: { kind: 'library', item }, onOpenLibraryItem }));
    await waitFor(() => expect(handlers.get('previoustrack')).toBeTypeOf('function'));
    handlers.get('previoustrack')?.();
    expect(onOpenLibraryItem).toHaveBeenCalledWith('i-m0');
  });
});

describe('Watch audio', () => {
  it('sends normalization gain and mute ranges to the shared audio graph, and follows the member’s choices', async () => {
    available();
    api.getLocalPlaybackOptions.mockResolvedValue({ ...direct, loudness_gain_db: -6 });
    api.getMuteRanges.mockResolvedValue([{ start_seconds: 12, end_seconds: 12.6 }]);
    const on = { normalizeLoudness: true, autoSkip: { intro: false, credits: false, recap: false }, profanity: { enabled: true, words: ['heck'] } };
    const { rerender } = render(watchSurface({ selection: { kind: 'library', item }, playbackPrefs: on }));
    await waitFor(() => expect(api.getMuteRanges).toHaveBeenCalledWith('i-e1'));
    // The element is still preparing (no src) right after mount; D remounts to the direct
    // source once probed, which would drop a graph started too early.
    await waitFor(() => expect(document.querySelector('video')?.getAttribute('src')).toBe('/api/library/i-e1/media'));
    fireEvent.play(document.querySelector('video') as HTMLVideoElement);
    await waitFor(() => expect(audioGraph.graph.setLoudnessGain).toHaveBeenLastCalledWith(expect.closeTo(0.501, 3)));
    await waitFor(() => expect(audioGraph.graph.setMuteRanges).toHaveBeenLastCalledWith([{ start_seconds: 12, end_seconds: 12.6 }]));

    rerender(watchSurface({ selection: { kind: 'library', item }, playbackPrefs: { ...on, normalizeLoudness: false, profanity: { enabled: false, words: ['heck'] } } }));
    await waitFor(() => expect(audioGraph.graph.setLoudnessGain).toHaveBeenLastCalledWith(1));
    expect(audioGraph.graph.setMuteRanges).toHaveBeenLastCalledWith([]);
    expect(api.getMuteRanges).toHaveBeenCalledTimes(1);
  });

  it('re-keys mute ranges on the version actually playing after a version switch', async () => {
    available();
    api.getTitle.mockResolvedValue({ ...detail, versions: [{ item_id: 'i-e1', height: 2160, hdr: true }, { item_id: 'i-e1-sd', height: 480, hdr: false }] });
    api.getMuteRanges.mockImplementation(async (id: string) => (id === 'i-e1-sd' ? [{ start_seconds: 5, end_seconds: 5.4 }] : [{ start_seconds: 12, end_seconds: 12.6 }]));
    const on = { normalizeLoudness: true, autoSkip: { intro: false, credits: false, recap: false }, profanity: { enabled: true, words: ['heck'] } };
    render(watchSurface({ selection: { kind: 'library', item }, playbackPrefs: on }));
    await waitFor(() => expect(api.getMuteRanges).toHaveBeenCalledWith('i-e1'));
    await userEvent.click(await screen.findByRole('button', { name: 'Playback settings' }));
    await userEvent.click(await screen.findByRole('radio', { name: '480p' }));
    // Mute ranges belong to the file actually playing, not the item that was opened.
    await waitFor(() => expect(api.getMuteRanges).toHaveBeenLastCalledWith('i-e1-sd'));
    await waitFor(() => expect(document.querySelector('video')?.getAttribute('src')).toBe('/api/library/i-e1-sd/media'));
    fireEvent.play(document.querySelector('video') as HTMLVideoElement);
    await waitFor(() => expect(audioGraph.graph.setMuteRanges).toHaveBeenLastCalledWith([{ start_seconds: 5, end_seconds: 5.4 }]));
  });
});

describe('Watch music', () => {
  const tracks = [1, 2, 3].map((number) => albumTrack(number, { item_id: `t${number}` }));
  const song = (number: number) => ({ id: `t${number}`, title: `0${number} Song ${number}.flac`, status: 'available', kind: 'track', title_id: 'album-1', metadata_json: {} }) as LibraryItem;

  function playing(number: number, overrides: Omit<Parameters<typeof watchSurface>[0], 'selection'> = {}) {
    available();
    api.getTitle.mockResolvedValue(albumDetail(albumSummary(), tracks));
    const onOpenLibraryItem = vi.fn();
    render(watchSurface({ selection: { kind: 'library', item: song(number) }, onOpenLibraryItem, ...overrides }));
    return onOpenLibraryItem;
  }
  const media = async (number: number) => {
    await waitFor(() => expect(document.querySelector('audio')?.getAttribute('src')).toBe(`/api/library/t${number}/media`));
    return document.querySelector('audio') as HTMLAudioElement;
  };

  it('shows the audio stage over the playing <audio> and continues with the next track in album order', async () => {
    clearAlbumQueue();
    const onOpenLibraryItem = playing(1);
    expect(await screen.findByText('Song 1')).toBeTruthy();
    expect(screen.getByText('Artist A · Album One')).toBeTruthy();
    expect(screen.getByText('Next · 2. Song 2')).toBeTruthy();
    expect(document.querySelector('.player-frame.has-audio-stage .g-audio-stage')).not.toBeNull();
    const audio = await media(1);
    expect(document.querySelector('.player-up-next')).toBeNull();
    fireEvent.ended(audio);
    await waitFor(() => expect(onOpenLibraryItem).toHaveBeenCalledWith('t2'));
  });

  it('follows a stored shuffle order', async () => {
    setAlbumQueue('album-1', [tracks[2], tracks[0], tracks[1]]);
    const onOpenLibraryItem = playing(3);
    expect(await screen.findByText('Next · 1. Song 1')).toBeTruthy();
    fireEvent.ended(await media(3));
    await waitFor(() => expect(onOpenLibraryItem).toHaveBeenCalledWith('t1'));
    clearAlbumQueue();
  });

  it('ends the queue after the album’s last track', async () => {
    clearAlbumQueue();
    // A playable recommendation that ordinary autoplay would open: the album's end must not reach it.
    const onOpenRelated = vi.fn();
    const recommendation = { id: 'rec1', source: 'youtube', title: 'Recommended', webpage_url: 'https://www.youtube.com/watch?v=rec1', capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } } } as YouTubeSearchResult;
    const onOpenLibraryItem = playing(3, { related: [recommendation], onOpenRelated });
    expect(await screen.findByText('Song 3')).toBeTruthy();
    expect(screen.queryByText(/^Next ·/)).toBeNull();
    fireEvent.ended(await media(3));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(onOpenLibraryItem).not.toHaveBeenCalled();
    expect(onOpenRelated).not.toHaveBeenCalled();
  });
});
