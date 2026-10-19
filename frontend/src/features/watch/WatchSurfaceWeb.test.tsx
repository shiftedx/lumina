import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { stillItem } from '../../test/galleryFixtures';
import { capabilities, CHANNEL_ID, liveEntry, remoteEntry } from '../../test/remoteFixtures';
import { watchSurface } from '../../test/watchSurface';
import type { MemberRecommendationSnapshot, PreviewResponse } from '../../types';

const api = vi.hoisted(() => ({ getLiveDiscovery: vi.fn(), getRemotePlaybackProgress: vi.fn(), getTitle: vi.fn(), getUpNext: vi.fn(), listLiveRecordings: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const preview = (patch: Partial<PreviewResponse> = {}) => ({
  kind: 'video', title: 'Harbor walk at dawn', webpage_url: 'https://www.youtube.com/watch?v=v1', entries: [], playback: null,
  capabilities: capabilities('youtube', 'vod'), chapters: [{ start_time: 0, title: 'Intro' }, { start_time: 60, title: 'Harbor' }], description_timestamps: [],
  raw: { extractor_key: 'Youtube', uploader: 'Chan', channel_id: CHANNEL_ID, channel_follower_count: 2_100_000, view_count: 1_200_000, upload_date: '20251003', duration: 1452, description: 'A walk. https://example.test/x' },
  ...patch,
}) as unknown as PreviewResponse;

const upNext = (items: MemberRecommendationSnapshot['items']): MemberRecommendationSnapshot => ({ items, categories: [], state: 'ready', refreshing: false, stale: false, error: null });

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => {
  vi.clearAllMocks();
  api.getRemotePlaybackProgress.mockResolvedValue(null);
  api.getUpNext.mockResolvedValue(upNext([]));
  api.getLiveDiscovery.mockResolvedValue({ items: [], hero: [], categories: [], state: 'ready', refreshing: false, stale: false, twitch_available: true });
  api.listLiveRecordings.mockResolvedValue({ items: [], next_cursor: null });
});

describe('the web-video watch page', () => {
  it('lays out the editorial column and side column under the player', () => {
    const { container } = render(watchSurface({ selection: { kind: 'remote', item: remoteEntry('v1'), preview: preview() } }));
    const column = container.querySelector('.gallery.g-watch-body .g-watch-column') as HTMLElement;
    expect(within(column).getByRole('heading', { level: 1, name: 'Harbor walk at dawn' })).toBeTruthy();
    expect(container.querySelector('.g-watch-kicker')?.textContent).toBe('YouTube');
    expect(within(column).getByRole('link', { name: 'Chan' }).getAttribute('href')).toBe(`/channel/youtube/${CHANNEL_ID}`);
    expect(within(column).getByRole('button', { name: 'Save to library' })).toBeTruthy();
    expect(within(column).getByRole('heading', { level: 2, name: 'Chapters' })).toBeTruthy();
    expect(screen.queryByRole('tab', { name: 'Overview' })).toBeNull();
    expect(container.querySelector('aside.watch-rail')).toBeNull();
    expect(container.querySelector('.g-watch-side .g-up-next')).not.toBeNull();
    expect(container.querySelector('.g-watch-side details.g-watch-details summary')?.textContent).toBe('Details & provenance');
  });

  it('shows LIVE, the viewer count and Record for a live source', () => {
    const { container } = render(watchSurface({ selection: { kind: 'remote', item: liveEntry('l1', { webpage_url: 'https://www.youtube.com/watch?v=l1' }), preview: preview({ capabilities: capabilities('youtube', 'live', { can_record: true }), webpage_url: 'https://www.youtube.com/watch?v=l1' }) } }));
    expect(container.querySelector('.g-watch-kicker .g-live.is-live')).not.toBeNull();
    expect(screen.getByText(new RegExp(`^${(12_400).toLocaleString()} watching`))).toBeTruthy();
    expect(screen.getByRole('button', { name: /Record/ })).toBeTruthy();
    expect(screen.getByRole('list', { name: 'Live chat messages' })).toBeTruthy();
  });

  it('gives a saved web video the same column, marked as in the library', () => {
    const item = stillItem('saved-1', { title_id: null, extractor: 'youtube', metadata_json: { uploader: 'Chan', channel_id: CHANNEL_ID, description: 'Saved.' } });
    const { container } = render(watchSurface({ selection: { kind: 'library', item } }));
    expect(container.querySelector('.g-watch-kicker')?.textContent).toBe('YouTube · In your library');
    expect(screen.getByRole('button', { name: 'Saved' })).toBeTruthy();
  });

  it('gets a saved YouTube video’s Up next from the shared feed and opens saved or streamed results alike', async () => {
    const item = stillItem('saved-1', {
      title_id: null, extractor: 'youtube', remote_id: 'saved-video', webpage_url: 'https://www.youtube.com/watch?v=saved-video',
      metadata_json: { id: 'saved-video', uploader: 'Chan', channel_id: CHANNEL_ID, channel_url: `https://www.youtube.com/channel/${CHANNEL_ID}` },
    });
    const savedNext = remoteEntry('saved-next', { title: 'Saved next', saved_item_id: 'saved-next-item' });
    const streamedNext = remoteEntry('streamed-next', { title: 'Streamed next' });
    api.getUpNext.mockResolvedValue(upNext([remoteEntry('saved-video'), savedNext, streamedNext]));
    const onOpenRelated = vi.fn();

    render(watchSurface({ related: [remoteEntry('stale', { title: 'Stale acquisition result' })], onOpenRelated, selection: { kind: 'library', item } }));

    await waitFor(() => expect(api.getUpNext).toHaveBeenCalledWith(expect.objectContaining({
      source_url: item.webpage_url, source_id: 'saved-video', source: 'youtube', channel_id: CHANNEL_ID, channel_url: `https://www.youtube.com/channel/${CHANNEL_ID}`,
    })));
    expect(await screen.findByRole('button', { name: /^Saved next/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Streamed next/ })).toBeTruthy();
    expect(screen.queryByText('Stale acquisition result')).toBeNull();
    expect(screen.getByText('Plays next')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /^Saved next/ }));
    expect(onOpenRelated).toHaveBeenCalledWith(savedNext);
    fireEvent.click(screen.getByRole('button', { name: /^Streamed next/ }));
    expect(onOpenRelated).toHaveBeenCalledWith(streamedNext);

    onOpenRelated.mockClear();
    const media = screen.getByLabelText('Harbor walk at dawn video') as HTMLVideoElement;
    Object.defineProperties(media, {
      currentTime: { configurable: true, value: 761, writable: true },
      duration: { configurable: true, value: 761 },
      ended: { configurable: true, value: true, writable: true },
    });
    fireEvent.ended(media);
    fireEvent.ended(media);
    expect(onOpenRelated).toHaveBeenCalledTimes(1);
    expect(onOpenRelated).toHaveBeenCalledWith(savedNext);
  });

  it('refetches a saved video’s Up next when its library summary hydrates channel context', async () => {
    const item = stillItem('saved-1', {
      title_id: null, extractor: 'youtube', remote_id: 'saved-video', webpage_url: 'https://www.youtube.com/watch?v=saved-video',
      metadata_json: { id: 'saved-video', uploader: 'Chan' },
    });
    let resolveFirst!: (value: MemberRecommendationSnapshot) => void;
    api.getUpNext
      .mockImplementationOnce(() => new Promise<MemberRecommendationSnapshot>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(upNext([remoteEntry('hydrated-next', { title: 'Hydrated next' })]));
    const view = render(watchSurface({ selection: { kind: 'library', item } }));

    await waitFor(() => expect(api.getUpNext).toHaveBeenCalledWith(expect.not.objectContaining({ channel_id: expect.anything() })));
    view.rerender(watchSurface({ selection: { kind: 'library', item: { ...item, metadata_json: { ...item.metadata_json, channel_id: CHANNEL_ID, channel_url: `https://www.youtube.com/channel/${CHANNEL_ID}` } } } }));

    await waitFor(() => expect(api.getUpNext).toHaveBeenLastCalledWith(expect.objectContaining({ channel_id: CHANNEL_ID, channel_url: `https://www.youtube.com/channel/${CHANNEL_ID}` })));
    expect(await screen.findByRole('button', { name: /^Hydrated next/ })).toBeTruthy();
    await act(async () => { resolveFirst(upNext([remoteEntry('old-next', { title: 'Old response' })])); });
    expect(screen.queryByRole('button', { name: /^Old response/ })).toBeNull();
  });

  it('leaves movie and episode playback as it was', () => {
    api.getTitle.mockResolvedValue({ id: 'movie-1', type: 'movie' });
    const { container } = render(watchSurface({ selection: { kind: 'library', item: stillItem('m', { title_id: 'movie-1', kind: 'movie' }) } }));
    expect(container.querySelector('section.watch-info')).not.toBeNull();
    expect(container.querySelector('aside.watch-rail')).not.toBeNull();
    expect(container.querySelector('.g-watch-body')).toBeNull();
  });

  it('keeps a Notes draft and timestamp when title metadata moves tools into the title layout', async () => {
    let resolveTitle: (value: { id: string; name: string; type: 'movie' }) => void = () => undefined;
    api.getTitle.mockReturnValue(new Promise((resolve) => { resolveTitle = resolve; }));
    render(watchSurface({ selection: { kind: 'library', item: stillItem('m', { title: 'Imported film', title_id: 'movie-1', kind: 'movie' }) } }));

    const notes = screen.getByRole('tab', { name: 'Notes' });
    fireEvent.click(notes);
    expect(notes.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('region', { name: 'Notes' })).toBeTruthy();
    const composer = screen.getByLabelText('New note') as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: 'Remember this moment' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add current time' }));
    expect(composer.value).toBe('Remember this moment');
    expect(screen.getByText(/^At 0:00/)).toBeTruthy();

    resolveTitle({ id: 'movie-1', name: 'Metadata title', type: 'movie' });
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: 'Metadata title' })).toBeTruthy());
    expect(screen.getByRole('tab', { name: 'Notes' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('region', { name: 'Notes' })).toBeTruthy();
    expect((screen.getByLabelText('New note') as HTMLTextAreaElement).value).toBe('Remember this moment');
    expect(screen.getByText(/^At 0:00/)).toBeTruthy();
  });

  it('does not query the phone breakpoint on a movie page', () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    vi.stubGlobal('matchMedia', matchMedia);
    api.getTitle.mockResolvedValue({ id: 'movie-1', type: 'movie' });
    render(watchSurface({ selection: { kind: 'library', item: stillItem('m', { title_id: 'movie-1', kind: 'movie' }) } }));
    expect(matchMedia).not.toHaveBeenCalledWith('(max-width: 599px)');
  });
});
