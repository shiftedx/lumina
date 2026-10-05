import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { albumDetail, albumSummary, albumTrack, stillItem } from '../../test/galleryFixtures';
import { resetImageLoader } from '../gallery/imageLoader';
import { AudioStage } from './AudioStage';

const session = { metadata: null as unknown, setActionHandler: vi.fn() };
class FakeMetadata { constructor(init: object) { Object.assign(this, init); } }

const album = albumDetail(albumSummary(), [albumTrack(1), albumTrack(2, { artist: 'Guest' }), albumTrack(3)]);
const track = stillItem('track-2', { kind: 'track', title: '02 Song 2.flac', title_id: 'album-1' });
const handler = (action: string) => session.setActionHandler.mock.calls.filter(([name]) => name === action).at(-1)?.[1] as ((details?: object) => void) | null;

beforeEach(() => {
  resetImageLoader();
  session.metadata = null;
  session.setActionHandler.mockReset();
  Object.defineProperty(navigator, 'mediaSession', { configurable: true, value: session });
  vi.stubGlobal('MediaMetadata', FakeMetadata);
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete (navigator as { mediaSession?: unknown }).mediaSession;
});

describe('AudioStage', () => {
  it('shows the album cover, the track and "Artist · Album" for a track, on the cover’s colour', () => {
    const { container } = render(<AudioStage album={album} item={track} next={null} onPlayItem={vi.fn()} onSeek={vi.fn()} previous={null} />);
    expect(screen.getByText('Song 2')).toBeTruthy();
    expect(screen.getByText('Guest · Album One')).toBeTruthy();
    const stage = container.querySelector<HTMLElement>('.gallery.g-audio-stage');
    expect(stage?.querySelector('.g-art-square')).not.toBeNull();
    expect(stage?.style.backgroundColor).toBe('rgb(42, 59, 76)'); // the cover's dominant #2a3b4c, under the shade
    expect(stage?.querySelector('img:not([alt=""])')).toBeNull();
  });

  it('names saved audio by its uploader, on the item’s own thumbnail', () => {
    render(<AudioStage album={null} item={stillItem('audio-1', { kind: 'audio', title: 'Harbor walk', uploader: 'Chan' })} next={null} onPlayItem={vi.fn()} onSeek={vi.fn()} previous={null} />);
    expect(screen.getByText('Harbor walk')).toBeTruthy();
    expect(screen.getByText('Chan')).toBeTruthy();
    expect(session.metadata).toMatchObject({ title: 'Harbor walk', artist: 'Chan', album: '', artwork: [{ src: expect.stringContaining('/api/library/audio-1/artwork'), sizes: '480x480' }] });
  });

  it('tells the operating system what plays, steps the album queue from its keys, and clears it all on unmount', () => {
    const onPlayItem = vi.fn();
    const onSeek = vi.fn();
    const view = render(<AudioStage album={album} item={track} next={{ itemId: 'track-3', label: '3. Song 3' }} onPlayItem={onPlayItem} onSeek={onSeek} previous={{ itemId: 'track-1', label: '1. Song 1' }} />);
    expect(session.metadata).toMatchObject({ title: 'Song 2', artist: 'Guest', album: 'Album One', artwork: [{ src: expect.stringContaining('-480.webp'), sizes: '480x480' }] });
    handler('nexttrack')?.();
    expect(onPlayItem).toHaveBeenLastCalledWith('track-3');
    handler('previoustrack')?.();
    expect(onPlayItem).toHaveBeenLastCalledWith('track-1');
    handler('seekto')?.({ action: 'seekto', seekTime: 42 });
    expect(onSeek).toHaveBeenCalledWith(42);
    expect(screen.getByText('Next · 3. Song 3')).toBeTruthy();
    view.unmount();
    expect(session.metadata).toBeNull();
    expect(session.setActionHandler.mock.calls.slice(-3)).toEqual([['previoustrack', null], ['nexttrack', null], ['seekto', null]]);
  });

  it('offers no next-track key after the last track, and survives a browser that refuses an action', () => {
    session.setActionHandler.mockImplementation((action: string) => { if (action === 'seekto') throw new TypeError('unsupported'); });
    render(<AudioStage album={album} item={track} next={null} onPlayItem={vi.fn()} onSeek={vi.fn()} previous={null} />);
    expect(handler('nexttrack')).toBeNull();
    expect(screen.getByText('Song 2')).toBeTruthy();
    expect(screen.queryByText(/^Next ·/)).toBeNull();
  });

  it('leaves Media Session alone where the browser has none', () => {
    delete (navigator as { mediaSession?: unknown }).mediaSession;
    render(<AudioStage album={album} item={track} next={null} onPlayItem={vi.fn()} onSeek={vi.fn()} previous={null} />);
    expect(screen.getByText('Song 2')).toBeTruthy();
    expect(session.setActionHandler).not.toHaveBeenCalled();
  });
});
