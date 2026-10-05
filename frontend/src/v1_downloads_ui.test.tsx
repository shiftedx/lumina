import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from './api';
import { onCommand, PALETTE_EVENT } from './app/commands';
import { DownloadsSurface, LiveRecordingActivity } from './features/downloads/DownloadsSurface';
import type { DownloadJob, LiveRecording } from './types';
import type { CollectionLoadState } from './workspace';

afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

const job = (id: string, status: DownloadJob['status'], extra: Partial<DownloadJob> = {}): DownloadJob => ({
  id, source_url: `https://example.test/${id}`, status, title: `${id} title`, created_at: '2026-07-20T10:00:00Z', ...extra,
});

const handlers = { onCancel: vi.fn<(job: DownloadJob) => void>(), onRetry: vi.fn<(job: DownloadJob) => Promise<void>>().mockResolvedValue(undefined), onClear: vi.fn<() => void>(), onOpenItem: vi.fn<(id: string) => void>() };
const downloadsElement = (jobs: DownloadJob[], loadState: CollectionLoadState = 'ready') => <DownloadsSurface jobs={jobs} loadState={loadState} onRetryLoad={vi.fn()} onSignIn={vi.fn()} problem={null} retryingLoad={false} {...handlers} />;
function renderDownloads(jobs: DownloadJob[]) {
  render(downloadsElement(jobs));
  return handlers;
}
const row = (title: string) => screen.getByText(title).closest('li') as HTMLElement;

describe('v1 Downloads UI', () => {
  it('test_download_retry_rules_ui_api', async () => {
    const failed = job('failed', 'failed', { error: 'The source is unavailable right now.', attempts: [{ status: 'interrupted', error: 'Lumina restarted.', finished_at: '2026-07-20T09:00:00Z' }] });
    const props = renderDownloads([job('running', 'running', { progress: 40 }), failed]);

    // A running download can be cancelled, never retried.
    expect(within(row('running title')).queryByRole('button', { name: /Retry/ })).toBeNull();
    await userEvent.click(within(row('running title')).getByRole('button', { name: 'Cancel running title' }));
    expect(props.onCancel).toHaveBeenCalledTimes(1);

    // A failed one keeps its past outcome visible and retries as a new attempt.
    const failedRow = row('failed title');
    expect(screen.getByRole('heading', { name: /^Needs attention/ })).toBeTruthy();
    expect(within(failedRow).getByText('The source is unavailable right now.')).toBeTruthy();
    expect(within(failedRow).getByText('1 earlier attempt')).toBeTruthy();
    expect(within(failedRow).getByText(/Lumina restarted\./)).toBeTruthy();
    await userEvent.click(within(failedRow).getByRole('button', { name: 'Retry failed title' }));
    expect(props.onRetry).toHaveBeenCalledWith(failed);
  });

  it('shows a refused retry on the job row', async () => {
    const props = renderDownloads([job('full', 'failed')]);
    props.onRetry.mockRejectedValueOnce(new ApiRequestError('You already have 3 downloads queued or running; wait for some to finish.', 429));
    await userEvent.click(within(row('full title')).getByRole('button', { name: 'Retry full title' }));
    expect((await within(row('full title')).findByRole('alert')).textContent).toMatch(/already have 3 downloads/);
    await userEvent.click(within(row('full title')).getByRole('button', { name: 'Retry full title' }));
    expect(within(row('full title')).queryByRole('alert')).toBeNull();
  });

  it('shows phase text, not a fake percentage, when progress is unknown', () => {
    renderDownloads([job('unknown', 'running'), job('known', 'running', { downloaded_bytes: 50, total_bytes: 200 })]);
    expect(within(row('unknown title')).queryByRole('progressbar')).toBeNull();
    expect(within(row('unknown title')).getByText('Downloading')).toBeTruthy();
    expect(within(row('known title')).getByRole('progressbar').getAttribute('aria-valuenow')).toBe('25');
  });

  it('test_download_partial_results', async () => {
    const props = renderDownloads([
      job('saved', 'completed', { outputs: [{ library_item_id: 'item-1', root_label: 'Media', folder: '' }] }),
      job('broken', 'failed', { error: 'Private video.' }),
    ]);
    await userEvent.click(within(row('saved title')).getByRole('button', { name: 'Open' }));
    expect(props.onOpenItem).toHaveBeenCalledWith('item-1');
    expect(within(row('saved title')).queryByRole('button', { name: /Retry/ })).toBeNull();
    await userEvent.click(within(row('broken title')).getByRole('button', { name: 'Retry broken title' }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
    expect(within(row('broken title')).queryByRole('button', { name: 'Open' })).toBeNull();
  });

  it('test_download_destination_truth', () => {
    renderDownloads([job('uhd', 'completed', { outputs: [{ library_item_id: 'item-2', root_label: 'Archive disk', folder: 'Video UHD' }] })]);
    expect(within(row('uhd title')).getByText('Saved to Archive disk › Video UHD')).toBeTruthy();
  });

  it('test_download_clear_history_not_media', async () => {
    const props = renderDownloads([job('done', 'completed', { outputs: [{ library_item_id: 'item-3', root_label: 'Media', folder: '' }] })]);
    const clear = screen.getByRole('button', { name: 'Clear finished' });
    expect(clear.getAttribute('title')).toMatch(/files stay in your Library/);
    await userEvent.click(clear);
    expect(props.onClear).toHaveBeenCalledTimes(1);
    expect(props.onCancel).not.toHaveBeenCalled();
    expect(props.onRetry).not.toHaveBeenCalled();
    expect(screen.queryByText(/download queue|the queue/i)).toBeNull();
  });

  it('notes an Editable download whose source had no H.264', () => {
    const fallback = { requested_selector: 'x', fallback_reason: 'Not editable: the source has no H.264 + AAC version.' };
    renderDownloads([
      job('vp9', 'completed', { format_selection: { preset: 'best_editable' }, format_resolution: fallback }),
      job('h264', 'completed', { format_selection: { preset: 'best_editable' }, format_resolution: { fallback_reason: null } }),
    ]);
    expect(within(row('vp9 title')).getByText('Not editable: source has no H.264')).toBeTruthy();
    expect(within(row('h264 title')).queryByText(/Not editable/)).toBeNull();
  });

  it('follows live recordings only while one is still pending', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const recording = (status: LiveRecording['status'], extra: Partial<LiveRecording> = {}): LiveRecording => ({
      id: 'rec-1', source_url: 'https://example.test/live', title: 'Night stream', status, stop_requested: false, cancel_requested: false,
      media: { status: status === 'live' ? 'recording' : 'partial', library_item_id: status === 'live' ? null : 'item-rec', end_reason: status === 'live' ? null : 'disk_low' },
      chat: { status: 'completed' }, kept: status !== 'live', created_at: '2026-07-20T10:00:00Z', ...extra,
    });
    const listLiveRecordings = vi.fn()
      .mockResolvedValueOnce({ items: [recording('live')], next_cursor: null })
      .mockResolvedValue({ items: [recording('partial')], next_cursor: null });
    const onOpenItem = vi.fn();
    render(<LiveRecordingActivity api={{ listLiveRecordings }} onOpenItem={onOpenItem} session={{ captureSessionToken: () => 1, isSessionTokenCurrent: () => true }} />);
    expect(await screen.findByText('Recording')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(await screen.findByText('Recorded with a partial outcome')).toBeTruthy();
    expect(screen.getByText(/Stopped because the server was running out of disk space\..*Kept/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(listLiveRecordings).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));
    expect(onOpenItem).toHaveBeenCalledWith('item-rec');
  });
});

describe('editorial Downloads', () => {
  it('heads the page with the vault activity meta and a Clear finished action', () => {
    renderDownloads([job('a', 'running', { progress: 45 }), job('b', 'failed'), job('c', 'completed'), job('d', 'completed')]);
    expect(screen.getByRole('heading', { level: 1, name: 'Downloads' })).toBeTruthy();
    expect(document.querySelector('.g-kicker')).toBeNull();
    expect(screen.getByText('1 downloading · 1 needs attention · 2 finished')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Clear finished' })).toBeTruthy();
  });

  it('groups rows under section heads and shows progress and status words, never pills', () => {
    renderDownloads([job('a', 'running', { progress: 45 }), job('b', 'failed')]);
    expect(screen.getByRole('heading', { level: 2, name: /^Downloading/ })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: /^Needs attention/ })).toBeTruthy();
    expect(screen.getByRole('progressbar', { name: '45 percent downloaded' }).getAttribute('aria-valuenow')).toBe('45');
    expect(screen.getByText('Failed').closest('.g-status')?.classList.contains('is-danger')).toBe(true);
    expect(document.querySelector('.batch-state, .lifecycle-badge')).toBeNull();
  });

  it('loading, empty, error and stale states', async () => {
    const { rerender } = render(downloadsElement([], 'loading'));
    expect(screen.getByRole('status').textContent).toBe('Loading downloads…');
    rerender(downloadsElement([], 'empty'));
    expect(screen.getByText('Nothing is downloading.')).toBeTruthy();
    const listener = vi.fn();
    const stop = onCommand(PALETTE_EVENT, listener);
    await userEvent.click(screen.getByRole('button', { name: 'Add a link' }));
    expect(listener).toHaveBeenCalledWith({ mode: 'link' });
    stop();
    rerender(downloadsElement([], 'failed'));
    expect(screen.getByText('Downloads could not load')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    rerender(downloadsElement([job('c', 'completed')], 'stale'));
    expect(screen.getByText('Activity may be out of date').closest('.g-status')?.classList.contains('is-attention')).toBe(true);
  });
});
