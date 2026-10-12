import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { SummaryPanel } from './features/watch/SummaryPanel';
import { pollDelay } from './features/watch/usePollJob';
import { ApiRequestError, getLatestSummary, getSummary, requestSummary, type Summary, type Transcript } from './api';

vi.mock('./api', async (importOriginal) => ({
  ApiRequestError: (await importOriginal<typeof import('./api')>()).ApiRequestError,
  getLatestSummary: vi.fn(), getSummary: vi.fn(), requestSummary: vi.fn(),
}));

const transcript: Transcript = { id: 'tr-1', library_item_id: 'item', language: 'en', source_kind: 'source_caption', revision: 1, cue_count: 40, model_label: null, created_at: '2026-01-01T00:00:00Z' };
const summary = (overrides: Partial<Summary> = {}): Summary => ({
  id: 'sum-1', library_item_id: 'item', transcript_id: 'tr-1', transcript_revision: 1, model_id: 'local-model', state: 'succeeded',
  overview: 'A valley rebuilds its bridge every summer.', key_points: [{ text: 'The bridge floods every spring.', cue_ordinals: [7, 8], start_ms: 42_000 }],
  chapters: [{ title: 'High pastures', cue_ordinal: 20, start_ms: 90_500 }], dropped_points: 2, error: null, created_at: '2026-01-01T00:00:00Z', completed_at: '2026-01-01T00:01:00Z',
  ...overrides,
});
const notFound = () => Promise.reject(new ApiRequestError('No summary yet', 404));

beforeEach(() => { vi.mocked(getLatestSummary).mockImplementation(notFound); });
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

it('test_summary_evidence_opens_cue', async () => {
  vi.mocked(getLatestSummary).mockResolvedValue(summary());
  const onSeek = vi.fn();
  render(<SummaryPanel itemId="item" onSeek={onSeek} transcripts={[transcript]} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Play evidence at 0:42' }));
  expect(onSeek).toHaveBeenLastCalledWith(42);
  fireEvent.click(screen.getByRole('button', { name: /High pastures/ }));
  expect(onSeek).toHaveBeenLastCalledWith(90.5);
  expect(screen.getByText('2 points omitted: no supporting transcript evidence.')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /summar/i })).toBeNull(); // current result: nothing to regenerate
});

it('test_summary_states_real', async () => {
  const { unmount } = render(<SummaryPanel itemId="item" onSeek={vi.fn()} transcripts={[]} />);
  expect(await screen.findByText('No transcript for this video.')).toBeTruthy();
  expect(screen.queryByRole('button')).toBeNull();
  unmount();

  vi.mocked(requestSummary).mockRejectedValueOnce(new ApiRequestError('Local AI is not configured', 409));
  render(<SummaryPanel itemId="item" onSeek={vi.fn()} transcripts={[transcript]} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Generate summary' }));
  expect(await screen.findByText(/Local AI isn’t set up/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Generate summary' })).toBeNull();
  cleanup();

  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(requestSummary).mockResolvedValueOnce(summary({ state: 'queued', overview: null }));
  vi.mocked(getSummary).mockResolvedValueOnce(summary({ state: 'running', overview: null })).mockResolvedValueOnce(summary({ state: 'failed', error: 'model returned no grounded points' }));
  render(<SummaryPanel itemId="item" onSeek={vi.fn()} transcripts={[transcript]} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Generate summary' }));
  expect(await screen.findByText(/Generating summary/)).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(pollDelay(0) + pollDelay(1)));
  expect(screen.getByText('The summary failed: model returned no grounded points')).toBeTruthy();
  expect(screen.queryByText(/Generating summary/)).toBeNull();
  expect(getSummary).toHaveBeenCalledTimes(2); // polling stops once the job is done
  expect(screen.getByRole('button', { name: 'Retry summary' })).toBeTruthy();

  vi.mocked(requestSummary).mockResolvedValueOnce(summary({ id: 'sum-2', state: 'queued' }));
  vi.mocked(getSummary).mockResolvedValueOnce(summary({ id: 'sum-2' }));
  fireEvent.click(screen.getByRole('button', { name: 'Retry summary' }));
  await screen.findByText(/Generating summary/);
  await act(() => vi.advanceTimersByTimeAsync(pollDelay(0)));
  expect(screen.getByText('A valley rebuilds its bridge every summer.')).toBeTruthy();
});

it('test_summary_stale_offers_regeneration', async () => {
  vi.mocked(getLatestSummary).mockResolvedValue(summary());
  render(<SummaryPanel itemId="item" onSeek={vi.fn()} transcripts={[{ ...transcript, id: 'tr-2', revision: 2 }, transcript]} />);
  expect(await screen.findByText(/From an older transcript/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Summarize the latest transcript' })).toBeTruthy();
});

it('test_summary_no_autogenerate_loop', async () => {
  for (let open = 0; open < 2; open += 1) {
    render(<SummaryPanel itemId="item" onSeek={vi.fn()} transcripts={[transcript]} />);
    await screen.findByRole('button', { name: 'Generate summary' });
    cleanup();
  }
  expect(requestSummary).not.toHaveBeenCalled();
  expect(pollDelay(0)).toBe(1000);
  expect(pollDelay(20)).toBe(10_000);
});
