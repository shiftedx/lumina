import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { capabilities, remoteEntry } from '../../test/remoteFixtures';
import { watchSurface } from '../../test/watchSurface';
import type { PreviewResponse } from '../../types';

// Stands in for the player: like LuminaPlayer, its own video click toggles play.
vi.mock('../../remotePlayer', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../remotePlayer')>()),
  RemotePlayer: () => (
    <div className="lumina-player">
      <video data-testid="video" onClick={(event) => { const v = event.currentTarget; if (v.paused) void v.play(); else v.pause(); }} />
    </div>
  ),
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), getRemotePlaybackProgress: vi.fn().mockResolvedValue(null), getLiveDiscovery: vi.fn().mockResolvedValue({ items: [], hero: [] }) }));

const preview = { kind: 'video', title: 'Walk', webpage_url: 'https://www.youtube.com/watch?v=v1', entries: [], playback: { src: 'x' }, capabilities: capabilities('youtube', 'vod'), chapters: [], description_timestamps: [], raw: {} } as unknown as PreviewResponse;

afterEach(() => vi.restoreAllMocks());

describe('the docked mini player surface', () => {
  const setup = () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) { Object.defineProperty(this, 'paused', { configurable: true, value: false }); return Promise.resolve(); });
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) { Object.defineProperty(this, 'paused', { configurable: true, value: true }); });
    const onExpand = vi.fn();
    const view = render(watchSurface({ mini: true, onExpand, selection: { kind: 'remote', item: remoteEntry('v1'), preview } }));
    return { play, pause, onExpand, ...view };
  };

  it('flips play state exactly once for one click on the video', () => {
    const { play, pause, getByTestId } = setup();
    fireEvent.click(getByTestId('video'));
    expect(play).toHaveBeenCalledTimes(1);
    expect(pause).not.toHaveBeenCalled();
  });

  it('toggles once for a click on the surface outside the player', () => {
    const { play, pause, container } = setup();
    fireEvent.click(container.querySelector('.watch-back')!.parentElement!);
    expect(play.mock.calls.length + pause.mock.calls.length).toBe(1);
  });

  it('expands on a double-click', () => {
    const { onExpand, getByTestId } = setup();
    fireEvent.doubleClick(getByTestId('video'));
    expect(onExpand).toHaveBeenCalledTimes(1);
  });
});
