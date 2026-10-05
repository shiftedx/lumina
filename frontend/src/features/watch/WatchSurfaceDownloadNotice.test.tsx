import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { capabilities, liveEntry } from '../../test/remoteFixtures';
import { watchSurface } from '../../test/watchSurface';
import type { DownloadJob, PreviewResponse } from '../../types';

vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), getRemotePlaybackProgress: vi.fn().mockResolvedValue(null), getLiveDiscovery: vi.fn().mockResolvedValue({ items: [], hero: [] }) }));

const URL = 'https://www.youtube.com/watch?v=l1';
const preview = { kind: 'video', title: 'Night', webpage_url: URL, entries: [], playback: null, capabilities: capabilities('youtube', 'vod', {}), chapters: [], description_timestamps: [], raw: {} } as unknown as PreviewResponse;
const job = (progress: number) => ({ id: 'j1', source_url: URL, status: 'running', progress }) as unknown as DownloadJob;

describe('the download notice on the watch page', () => {
  it('announces the state phrase politely but never the changing percent', () => {
    const { container } = render(watchSurface({ jobs: [job(37)], selection: { kind: 'remote', item: liveEntry('l1'), preview } }));
    const notice = container.querySelector('.queue-notice') as HTMLElement;
    expect(notice.textContent).toContain('37%');
    const live = [...notice.querySelectorAll('[role="status"]'), ...(notice.getAttribute('role') === 'status' ? [notice] : [])];
    expect(live.map((el) => el.textContent)).toEqual(['Downloading to your vault']);
  });
});
