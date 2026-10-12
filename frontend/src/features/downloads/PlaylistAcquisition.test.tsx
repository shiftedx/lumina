import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AcquisitionBatchActivity, PlaylistAcquisitionPanel, acquisitionBatchNeedsPolling, playlistBatchPayload } from './PlaylistAcquisition';
import { formatBytes } from '../../utils';
import type { AcquisitionBatch, AcquisitionBatchCreateRequest, PreviewResponse } from '../../types';

const preview: PreviewResponse = {
  kind: 'playlist',
  title: 'Evening picks',
  extractor: 'youtube:tab',
  extractor_key: 'YoutubeTab',
  webpage_url: 'https://www.youtube.com/playlist?list=one',
  entries: [
    { id: 'one', title: 'First film', uploader: 'Archive', duration: 120 },
    { id: 'two', title: 'Second film', uploader: 'Archive', duration: 180 },
  ],
  raw: { id: 'one', uploader: 'Archive', provider_raw: { must_not_persist: true } },
};

function batch(): AcquisitionBatch {
  return {
    id: 'batch-1', source_url: preview.webpage_url!, source_title: preview.title, source_provenance: {}, status: 'completed',
    selected_count: 1, queued_count: 0, duplicate_count: 0, completed_count: 1, failed_count: 0, progress: 100,
    format_selection: {}, output_profile: {}, entries: [], created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z',
  };
}

describe('playlist acquisition', () => {
  it('labels and gates an upcoming entry with the backend reason copy', () => {
    const upcoming = {
      ...preview,
      entries: [{
        id: 'soon', title: 'Soon live', webpage_url: 'https://www.youtube.com/watch?v=soon',
        capabilities: { provider: 'youtube' as const, lifecycle: 'upcoming' as const, can_play: false, play_reason: 'upcoming_not_started' as const, can_acquire: false, acquire_reason: 'upcoming_not_started' as const, chat: { live: 'unavailable' as const, replay: 'unavailable' as const } },
      }],
    };
    render(<PlaylistAcquisitionPanel api={{ createBatch: vi.fn(), listBatches: vi.fn(), retryEntry: vi.fn() }} formatSelection={{ preset: 'best', output_container: 'mp4' }} onOpenEntry={vi.fn()} outputProfile={{}} preview={upcoming} />);

    expect(screen.getByText('Upcoming')).toBeTruthy();
    expect(screen.getByText('This source has not started yet.')).toBeTruthy();
    expect((screen.getByRole('checkbox', { name: /soon live/i }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Play Soon live' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('queues only the checked playlist entries and never forwards raw provider metadata', async () => {
    const createBatch = vi.fn(async (_payload: AcquisitionBatchCreateRequest) => batch());
    const onOpenEntry = vi.fn();
    const user = userEvent.setup();
    render(<PlaylistAcquisitionPanel
      api={{ createBatch, listBatches: vi.fn(), retryEntry: vi.fn() }}
      formatSelection={{ preset: 'best_1080p', output_container: 'mp4' }}
      onOpenEntry={onOpenEntry}
      outputProfile={{ organize_by: 'playlist' }}
      preview={preview}
    />);

    await user.click(screen.getByRole('button', { name: 'Play First film' }));
    expect(onOpenEntry).toHaveBeenCalledWith(expect.objectContaining({ id: 'one', webpage_url: 'https://www.youtube.com/watch?v=one' }));
    await user.click(screen.getByRole('checkbox', { name: /second film/i }));
    await user.click(screen.getByRole('button', { name: /keep 1 videos/i }));

    await waitFor(() => expect(createBatch).toHaveBeenCalledTimes(1));
    const payload = createBatch.mock.calls[0][0];
    expect(payload.entries.map((entry) => entry.remote_id)).toEqual(['one']);
    expect(JSON.stringify(payload)).not.toContain('provider_raw');
  });

  it('submits every available entry when all remain selected', async () => {
    const createBatch = vi.fn(async (_payload: AcquisitionBatchCreateRequest) => batch());
    const user = userEvent.setup();
    render(<PlaylistAcquisitionPanel api={{ createBatch, listBatches: vi.fn(), retryEntry: vi.fn() }} formatSelection={{ preset: 'best', output_container: 'mp4' }} outputProfile={{ organize_by: 'playlist' }} preview={preview} />);

    await user.click(screen.getByRole('button', { name: /keep 2 videos/i }));
    await waitFor(() => expect(createBatch).toHaveBeenCalledTimes(1));
    expect(createBatch.mock.calls[0][0].entries.map((entry) => entry.remote_id)).toEqual(['one', 'two']);
  });
});

describe('playlist source identity', () => {
  it('does not invent a YouTube address for another provider entry id', () => {
    const soundcloud = { ...preview, extractor: 'soundcloud:set', extractor_key: 'SoundcloudSet', entries: [{ id: 'track-one', title: 'Track', webpage_url: '/relative/track' }] };
    expect(playlistBatchPayload(soundcloud, [0], { preset: 'best', output_container: 'mp4' }, {})).toBeNull();
  });

  it('rejects relative entry URLs but safely falls back for a YouTube id', () => {
    const youtube = { ...preview, entries: [{ id: 'safe-id', title: 'Video', webpage_url: '../relative' }] };
    const payload = playlistBatchPayload(youtube, [0], { preset: 'best', output_container: 'mp4' }, {});
    expect(payload?.entries[0].source_url).toBe('https://www.youtube.com/watch?v=safe-id');
  });
});

describe('batch retry concurrency', () => {
  it('renders partial and duplicate entry outcomes with retry only for failures', async () => {
    const partial: AcquisitionBatch = {
      ...batch(), status: 'partial', selected_count: 2, duplicate_count: 1, failed_count: 1, completed_count: 0, finished_at: '2026-07-16T00:01:00Z',
      entries: [
        { id: 'duplicate', batch_id: 'batch-1', selection_index: 0, source_url: 'https://example.com/one', title: 'Already there', status: 'duplicate', progress: 100, dispatch_attempts: 1, details: {}, created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z' },
        { id: 'failed', batch_id: 'batch-1', selection_index: 1, source_url: 'https://example.com/two', title: 'Needs retry', status: 'failed', progress: 100, dispatch_attempts: 1, details: {}, error: 'The source is unavailable.', created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z' },
      ],
    };
    render(<AcquisitionBatchActivity api={{ createBatch: vi.fn(), listBatches: vi.fn(async () => [partial]), retryEntry: vi.fn() }} />);

    expect(await screen.findByText('Already downloading or saved')).toBeTruthy();
    expect(screen.getByText('The source is unavailable.')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /^Retry / })).toHaveLength(1);
  });

  it('ignores a superseded refresh response and stops polling a finished partial batch', async () => {
    let releaseFirst: ((value: AcquisitionBatch[]) => void) | undefined;
    const first = new Promise<AcquisitionBatch[]>((resolve) => { releaseFirst = resolve; });
    const oldBatch = { ...batch(), id: 'old-batch', source_title: 'Member A activity' };
    const currentBatch = { ...batch(), id: 'current-batch', source_title: 'Current activity' };
    const api = {
      createBatch: vi.fn(),
      listBatches: vi.fn().mockReturnValueOnce(first).mockResolvedValueOnce([currentBatch]),
      retryEntry: vi.fn(),
    };
    const view = render(<AcquisitionBatchActivity api={api} refreshKey={0} />);
    view.rerender(<AcquisitionBatchActivity api={api} refreshKey={1} />);
    expect(await screen.findByText('Current activity')).toBeTruthy();
    releaseFirst?.([oldBatch]);
    await Promise.resolve();
    expect(screen.queryByText('Member A activity')).toBeNull();
    expect(acquisitionBatchNeedsPolling({ ...batch(), status: 'partial', finished_at: '2026-07-16T00:00:00Z' })).toBe(false);
    expect(acquisitionBatchNeedsPolling({ ...batch(), status: 'partial', finished_at: null })).toBe(true);
  });

  it('polls an active batch with one request per tick and skips ticks while the tab is hidden', async () => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    try {
      const listBatches = vi.fn(async () => [{ ...batch(), status: 'queued' as const, finished_at: null }]);
      render(<AcquisitionBatchActivity api={{ createBatch: vi.fn(), listBatches, retryEntry: vi.fn() }} />);
      await act(() => vi.advanceTimersByTimeAsync(0));
      await act(() => vi.advanceTimersByTimeAsync(3000));
      expect(listBatches).toHaveBeenCalledTimes(2);
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      await act(() => vi.advanceTimersByTimeAsync(9000));
      expect(listBatches).toHaveBeenCalledTimes(2);
    } finally {
      Reflect.deleteProperty(document, 'hidden');
      vi.useRealTimers();
    }
  });

  it('allows distinct failed entries to retry at the same time', async () => {
    const failed: AcquisitionBatch = {
      ...batch(), status: 'failed', completed_count: 0, failed_count: 2, selected_count: 2,
      entries: ['entry-one', 'entry-two'].map((id, index) => ({
        id, batch_id: 'batch-1', selection_index: index, source_url: `https://example.com/${index}`, title: `Failed ${index + 1}`,
        status: 'failed', progress: 100, dispatch_attempts: 1, details: {}, created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z',
      })),
    };
    const retryEntry = vi.fn((_batchId: string, _entryId: string) => new Promise<AcquisitionBatch>(() => undefined));
    const user = userEvent.setup();
    render(<AcquisitionBatchActivity api={{ createBatch: vi.fn(), listBatches: vi.fn(async () => [failed]), retryEntry }} />);

    const retries = await screen.findAllByRole('button', { name: /^Retry / });
    await user.click(retries[0]);
    await user.click(retries[1]);
    expect(retryEntry.mock.calls.map((call) => call[1])).toEqual(['entry-one', 'entry-two']);
    expect(screen.getAllByRole('button', { name: /^Retry / }).filter((button) => button.getAttribute('aria-busy') === 'true')).toHaveLength(2);
  });
});


function entry(over: Record<string, unknown> = {}) {
  return { id: `v${Math.random().toString(36).slice(2, 8)}`, title: 'A video', webpage_url: `https://www.youtube.com/watch?v=${Math.random().toString(36).slice(2, 8)}`, ...over };
}
function entries(count: number) { return Array.from({ length: count }, (_, index) => entry({ title: `Entry ${index + 1}` })); }
function renderPanel(over: { playlistTitle?: string; entries: ReturnType<typeof entry>[] }) {
  const value = { ...preview, title: over.playlistTitle ?? preview.title, entries: over.entries } as PreviewResponse;
  return render(<PlaylistAcquisitionPanel api={{ createBatch: vi.fn(), listBatches: vi.fn(), retryEntry: vi.fn() }} formatSelection={{ preset: 'best', output_container: 'mp4' }} outputProfile={{}} preview={value} />);
}
const gone = () => entry({ title: 'Gone', capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: false, can_acquire: false, acquire_reason: null, chat: { live: 'unavailable', replay: 'unavailable' } } });

describe('playlist acquisition', () => {
  it('heads the panel editorially', () => {
    renderPanel({ playlistTitle: 'Synthetic mixtape', entries: entries(3) });
    expect(screen.getByRole('heading', { level: 2, name: 'Keep videos from this playlist' })).toBeTruthy();
    expect(screen.getByText('Synthetic mixtape').classList.contains('g-kicker')).toBe(true);
  });

  it('select all is tri-state and skips unavailable entries', async () => {
    renderPanel({ entries: [...entries(3), gone()] });
    const all = screen.getByRole('checkbox', { name: 'Select all 3' }) as HTMLInputElement;
    const boxes = () => screen.getAllByRole('checkbox', { name: /^Video / }) as HTMLInputElement[];
    await userEvent.click(all); // starts all selected; clear it
    await userEvent.click(boxes()[0]);
    expect(all.indeterminate).toBe(true);
    await userEvent.click(all);
    expect(all.indeterminate).toBe(false);
    expect(boxes().filter((box) => !box.disabled).every((box) => box.checked)).toBe(true);
    const row = screen.getByText('Gone', { selector: '.g-list-row-title' }).closest('.g-playlist-row') as HTMLElement;
    expect(row.getAttribute('aria-disabled')).toBe('true');
    expect(within(row).getByText('Unavailable').closest('.g-status')?.classList.contains('is-muted')).toBe(true);
  });

  it('the footer counts only what is selected', async () => {
    renderPanel({ entries: [entry({ size_bytes: 1_000_000_000 }), entry({ size_bytes: null })] });
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all 2' })); // clear
    const keep = screen.getByRole('button', { name: 'Keep 0 videos' });
    expect(keep.getAttribute('aria-disabled')).toBe('true');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all 2' }));
    expect(screen.getByText(`2 of 2 selected · about ${formatBytes(1_000_000_000)}`)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Keep 2 videos' })).toBeTruthy();
  });

  it('batch activity uses status words and retry buttons', async () => {
    const list = ['partial', 'failed', 'completed'].map((status, index) => ({ ...batch(), id: `b${index}`, status, entries: status === 'failed' ? [{ id: 'e1', title: 'Broken', status: 'failed', progress: 0 }] : [] })) as unknown as AcquisitionBatch[];
    render(<AcquisitionBatchActivity api={{ createBatch: vi.fn(), listBatches: vi.fn(async () => list), retryEntry: vi.fn() }} />);
    await screen.findByText('partial');
    expect(document.querySelectorAll('.g-status.is-attention, .g-status.is-danger, .g-status.is-ok')).toHaveLength(3);
    expect(document.querySelector('.batch-state, .lifecycle-badge')).toBeNull();
    expect(screen.getByRole('button', { name: 'Retry Broken' })).toBeTruthy();
  });
});
