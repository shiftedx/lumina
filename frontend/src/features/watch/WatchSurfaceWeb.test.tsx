import { render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { stillItem } from '../../test/galleryFixtures';
import { capabilities, CHANNEL_ID, liveEntry, remoteEntry } from '../../test/remoteFixtures';
import { watchSurface } from '../../test/watchSurface';
import type { PreviewResponse } from '../../types';

const api = vi.hoisted(() => ({ getLiveDiscovery: vi.fn(), getRemotePlaybackProgress: vi.fn(), getTitle: vi.fn(), listLiveRecordings: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const preview = (patch: Partial<PreviewResponse> = {}) => ({
  kind: 'video', title: 'Harbor walk at dawn', webpage_url: 'https://www.youtube.com/watch?v=v1', entries: [], playback: null,
  capabilities: capabilities('youtube', 'vod'), chapters: [{ start_time: 0, title: 'Intro' }, { start_time: 60, title: 'Harbor' }], description_timestamps: [],
  raw: { extractor_key: 'Youtube', uploader: 'Chan', channel_id: CHANNEL_ID, channel_follower_count: 2_100_000, view_count: 1_200_000, upload_date: '20251003', duration: 1452, description: 'A walk. https://example.test/x' },
  ...patch,
}) as unknown as PreviewResponse;

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => {
  vi.clearAllMocks();
  api.getRemotePlaybackProgress.mockResolvedValue(null);
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

  it('leaves movie and episode playback as it was', () => {
    api.getTitle.mockResolvedValue({ id: 'movie-1', type: 'movie' });
    const { container } = render(watchSurface({ selection: { kind: 'library', item: stillItem('m', { title_id: 'movie-1', kind: 'movie' }) } }));
    expect(container.querySelector('section.watch-info')).not.toBeNull();
    expect(container.querySelector('aside.watch-rail')).not.toBeNull();
    expect(container.querySelector('.g-watch-body')).toBeNull();
  });

  it('does not query the phone breakpoint on a movie page', () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    vi.stubGlobal('matchMedia', matchMedia);
    api.getTitle.mockResolvedValue({ id: 'movie-1', type: 'movie' });
    render(watchSurface({ selection: { kind: 'library', item: stillItem('m', { title_id: 'movie-1', kind: 'movie' }) } }));
    expect(matchMedia).not.toHaveBeenCalledWith('(max-width: 599px)');
  });
});
