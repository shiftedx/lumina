import { render } from '@testing-library/react';
import { useEffect } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { capabilities, liveEntry } from '../../test/remoteFixtures';
import { watchSurface } from '../../test/watchSurface';
import type { PreviewResponse } from '../../types';

// The relay reports the end through RemotePlayer's onLiveEnded; this player reports it as soon as it mounts.
vi.mock('../../remotePlayer', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../remotePlayer')>()),
  RemotePlayer: ({ onLiveEnded }: { onLiveEnded?: () => void }) => {
    useEffect(() => { onLiveEnded?.(); }, [onLiveEnded]);
    return <div data-testid="player" />;
  },
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), getRemotePlaybackProgress: vi.fn().mockResolvedValue(null), getLiveDiscovery: vi.fn().mockResolvedValue({ items: [], hero: [] }) }));

describe('the end of a live stream on the watch page', () => {
  it('turns LIVE into ENDED and says how long it streamed', () => {
    const preview = { kind: 'video', title: 'Night', webpage_url: 'https://www.youtube.com/watch?v=l1', entries: [], playback: null, capabilities: capabilities('youtube', 'live', { can_record: true }), chapters: [], description_timestamps: [], raw: { duration: 7800 } } as unknown as PreviewResponse;
    const { container } = render(watchSurface({ selection: { kind: 'remote', item: liveEntry('l1'), preview } }));
    expect(container.querySelector('.g-watch-kicker .g-live')?.textContent).toBe('ENDED');
    expect(container.querySelector('.g-watch-meta')?.textContent).toBe('Ended · streamed 2h 10m');
    expect(container.textContent).not.toContain('Live chat isn\'t shown in Lumina');
  });
});
