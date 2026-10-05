import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { AdminTasks } from './features/admin/AdminTasks';
import { useAdminResource } from './features/admin/useAdminResource';
import { SummaryPanel } from './features/watch/SummaryPanel';
import { TranscriptPanel } from './features/watch/TranscriptPanel';
import { pollDelay } from './features/watch/usePollJob';
import {
  type AdminTaskPage, ApiRequestError, cancelAdminTask, getLatestAsrJob, getLatestSummary, listAdminTasks, requestAsrTranscript, retryAdminTask, type Transcript,
} from './api';

vi.mock('./api', async (importOriginal) => ({
  ApiRequestError: (await importOriginal<typeof import('./api')>()).ApiRequestError,
  getAiConfig: vi.fn(), updateAiConfig: vi.fn(), testAiConnection: vi.fn(), listAdminTasks: vi.fn(), cancelAdminTask: vi.fn(), retryAdminTask: vi.fn(),
  getLatestAsrJob: vi.fn(), requestAsrTranscript: vi.fn(), getLatestSummary: vi.fn(), getSummary: vi.fn(), requestSummary: vi.fn(),
  listTranscriptCues: vi.fn(() => new Promise(() => undefined)), searchTranscript: vi.fn(),
}));

const counts = { download: { failed: 1 }, asr: { active: 1 }, summary: { succeeded: 4, failed: 1 }, import: {} };
const page = (items: AdminTaskPage['items'] = []): AdminTaskPage => ({ items, next_cursor: null, counts });
const job = (state: string, error: string | null = null) => ({ id: 'asr-1', library_item_id: 'item', model_id: 'whisper', state, transcript_id: null, error, created_at: '2026-01-01T00:00:00Z', completed_at: null }) as never;
const caption: Transcript = { id: 'tr-1', library_item_id: 'item', language: 'en', source_kind: 'source_caption', revision: 1, cue_count: 3, model_label: null, created_at: '2026-01-01T00:00:00Z' };

afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

it('test_admin_task_cancel_owner_semantics', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page([
    { kind: 'download', id: 'd1', status: 'failed', title: null, detail: 'youtube', owner: 'Member', error: 'HTTP 403', attempts: 1, library_item_id: null, created_at: '2026-01-01T00:00:00Z', finished_at: null, can_cancel: false, can_retry: true },
  ]));
  render(<AdminTasks />);
  expect(await screen.findByText('Private download')).toBeTruthy();
  expect(screen.getByText('HTTP 403')).toBeTruthy();
  expect(screen.getByRole('tab', { name: /Downloads/ }).getAttribute('aria-selected')).toBe('true');

  vi.mocked(listAdminTasks).mockResolvedValue(page([
    { kind: 'asr', id: 'a1', status: 'running', title: 'Long talk', detail: 'whisper', owner: 'Member', error: null, attempts: 0, library_item_id: 'item', created_at: '2026-01-01T00:00:00Z', finished_at: null, can_cancel: true, can_retry: false },
  ]));
  fireEvent.click(screen.getByRole('tab', { name: /Transcriptions/ }));
  expect(await screen.findByText('Long talk')).toBeTruthy();
  expect(listAdminTasks).toHaveBeenLastCalledWith('asr', 'all', undefined);
  vi.mocked(cancelAdminTask).mockResolvedValue({ status: 'canceling' });
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(cancelAdminTask).toHaveBeenCalledWith('asr', 'a1'));
  fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'failed' } });
  await waitFor(() => expect(listAdminTasks).toHaveBeenLastCalledWith('asr', 'failed', undefined));
});

it('Tasks uses real tabs for Downloads, Transcriptions, Summaries and Imports', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page());
  render(<AdminTasks />);
  const tabs = await screen.findByRole('tablist', { name: 'Task kinds' });
  await screen.findByText(/in progress/);
  expect(within(tabs).getAllByRole('tab').map((tab) => tab.textContent?.replace(/\d+$/, ''))).toEqual(['Downloads', 'Transcriptions', 'Summaries', 'Imports']);
  expect(within(tabs).getByRole('tab', { name: /^Downloads/ }).getAttribute('aria-selected')).toBe('true');
  const summaries = within(tabs).getByRole('tab', { name: /^Summaries/ });
  fireEvent.click(summaries);
  expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(summaries.id);
  expect(summaries.getAttribute('aria-selected')).toBe('true');
  fireEvent.keyDown(summaries, { key: 'ArrowLeft' });
  const transcriptions = within(tabs).getByRole('tab', { name: /^Transcriptions/ });
  expect(transcriptions.getAttribute('aria-selected')).toBe('true');
  expect(document.activeElement).toBe(transcriptions);
  // Home and End jump to the ends, and the arrows wrap around them.
  const tab = (name: RegExp) => within(tabs).getByRole('tab', { name });
  fireEvent.keyDown(transcriptions, { key: 'End' });
  expect(document.activeElement).toBe(tab(/^Imports/));
  fireEvent.keyDown(tab(/^Imports/), { key: 'ArrowRight' });
  expect(document.activeElement).toBe(tab(/^Downloads/));
  fireEvent.keyDown(tab(/^Downloads/), { key: 'ArrowLeft' });
  expect(document.activeElement).toBe(tab(/^Imports/));
  fireEvent.keyDown(tab(/^Imports/), { key: 'Home' });
  expect(document.activeElement).toBe(tab(/^Downloads/));
});

it('test_generate_transcript_states', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(getLatestAsrJob).mockRejectedValueOnce(new ApiRequestError('No transcription job yet', 404))
    .mockResolvedValueOnce(job('running')).mockResolvedValueOnce(job('succeeded'));
  vi.mocked(requestAsrTranscript).mockResolvedValue(job('queued'));
  const onGenerated = vi.fn();
  render(<TranscriptPanel asr currentTime={0} itemId="item" onGenerated={onGenerated} onSeek={vi.fn()} transcripts={[]} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Generate transcript' }));
  expect(await screen.findByText(/Transcribing the audio/)).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(pollDelay(0) + pollDelay(1)));
  expect(onGenerated).toHaveBeenCalledTimes(1);
  cleanup();

  vi.mocked(getLatestAsrJob).mockResolvedValue(job('failed', 'ASR endpoint is unreachable'));
  render(<TranscriptPanel asr currentTime={0} itemId="item" onSeek={vi.fn()} transcripts={[caption]} />);
  expect(await screen.findByText('Transcription failed: ASR endpoint is unreachable')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Retry transcript' })).toBeTruthy();
  cleanup();

  render(<TranscriptPanel currentTime={0} itemId="item" onSeek={vi.fn()} transcripts={[caption]} />);
  expect(screen.queryByRole('button', { name: /transcript/i })).toBeNull(); // ASR off: no offer
});

it('test_ai_off_hides_generate_summary', async () => {
  vi.mocked(getLatestSummary).mockRejectedValue(new ApiRequestError('No summary yet', 404));
  render(<SummaryPanel aiEnabled={false} itemId="item" onSeek={vi.fn()} transcripts={[caption]} />);
  expect(await screen.findByText(/Local AI isn’t set up/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /summary/i })).toBeNull();
});


it('links to Library & storage through the app router', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page());
  const onPopState = vi.fn();
  window.addEventListener('popstate', onPopState);
  render(<AdminTasks />);
  fireEvent.click(await screen.findByRole('link', { name: 'Library & storage' }));
  window.removeEventListener('popstate', onPopState);
  expect(window.location.pathname).toBe('/settings/library');
  expect(onPopState).toHaveBeenCalledTimes(1);
});

it('a reload still in flight never overwrites data set after it started', async () => {
  let finish!: (value: string) => void;
  const fetcher = vi.fn().mockResolvedValueOnce('first').mockReturnValueOnce(new Promise<string>((done) => { finish = done; }));
  const { result } = renderHook(() => useAdminResource(fetcher as () => Promise<string>, 'failed'));
  await waitFor(() => expect(result.current.data).toBe('first'));
  act(() => { result.current.reload(); });
  act(() => { result.current.setData('saved'); });  // e.g. a Save response lands first
  await act(async () => { finish('stale'); });
  expect(result.current.data).toBe('saved');
  expect(result.current.loading).toBe(false);
});

const imp = (over: Partial<AdminTaskPage['items'][number]> = {}): AdminTaskPage['items'][number] => ({
  kind: 'import', id: 'i1', status: 'running', title: 'TV · 2 folders', detail: 'Watched folder', owner: 'Admin', error: null, attempts: 0,
  library_item_id: null, created_at: '2026-01-01T00:00:00Z', finished_at: null, can_cancel: false, can_retry: false, ...over,
});
const openImports = async () => { render(<AdminTasks />); fireEvent.click(await screen.findByRole('tab', { name: /^Imports/ })); };

it('lists import runs under an Imports tab with title, trigger and status', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page([imp({ status: 'needs_confirmation' })]));
  await openImports();
  expect(await screen.findByText('TV · 2 folders')).toBeTruthy();
  expect(screen.getByText('Needs confirmation')).toBeTruthy();
  expect(screen.getByText(/Watched folder/)).toBeTruthy();
  expect(listAdminTasks).toHaveBeenLastCalledWith('import', 'all', undefined);
});

it('cancels a running import with kind import', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page([imp({ can_cancel: true })]));
  vi.mocked(cancelAdminTask).mockResolvedValue({ status: 'canceling' });
  await openImports();
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(cancelAdminTask).toHaveBeenCalledWith('import', 'i1'));
});

it('retries an interrupted import through the new route', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page([imp({ status: 'interrupted', can_retry: true })]));
  vi.mocked(retryAdminTask).mockResolvedValue({ status: 'queued' });
  await openImports();
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(retryAdminTask).toHaveBeenCalledWith('import', 'i1'));
});

it('retries a download through retryAdminTask', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page([{ ...imp({ can_retry: true, status: 'failed' }), kind: 'download', id: 'd1' }]));
  vi.mocked(retryAdminTask).mockResolvedValue({ status: 'queued' });
  render(<AdminTasks />);
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(retryAdminTask).toHaveBeenCalledWith('download', 'd1'));
});

it('maps an import error code to words', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page([imp({ status: 'failed', error: 'offline' })]));
  await openImports();
  expect(await screen.findByText('The folder was unavailable.')).toBeTruthy();
  expect(screen.queryByText('offline')).toBeNull();
});

it('says "No library scans yet." for an empty Imports tab', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue(page());
  await openImports();
  expect(await screen.findByText('No library scans yet.')).toBeTruthy();
});

it('counts import runs by the spec groups', async () => {
  vi.mocked(listAdminTasks).mockResolvedValue({ items: [], next_cursor: null, counts: { ...counts, import: { running: 1, needs_confirmation: 1, failed: 1, interrupted: 1, cancelled: 1, succeeded: 4 } } });
  await openImports();
  expect(await screen.findByText('2 in progress · 3 failed or interrupted · 9 total')).toBeTruthy();
});
