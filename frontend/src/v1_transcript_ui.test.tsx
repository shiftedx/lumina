import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

import { activeCueIndex, TranscriptPanel } from './features/watch/TranscriptPanel';
import { listTranscriptCues, searchTranscript, type Transcript, type TranscriptCue } from './api';

vi.mock('./api', () => ({ listTranscriptCues: vi.fn(), searchTranscript: vi.fn(), listTranscripts: vi.fn() }));

const transcript = (id: string, language: string): Transcript => ({ id, library_item_id: 'item', language, source_kind: 'source_caption', revision: 1, cue_count: 3, model_label: null, created_at: '2026-01-01T00:00:00Z' });
const cue = (ordinal: number, text: string): TranscriptCue => ({ ordinal, start_ms: ordinal * 4000 + 500, end_ms: ordinal * 4000 + 4000, text });
const cues = [cue(0, 'Morning on the ridge'), cue(1, 'The old bridge crosses here'), cue(2, 'Back down to the valley')];
// Browsers fire a scroll event for programmatic scrolls too; the panel must tell them apart.
const scrollTo = vi.fn(function (this: Element) { this.dispatchEvent(new Event('scroll')); });
Object.defineProperty(HTMLElement.prototype, 'offsetTop', { configurable: true, get() { return 200 + Number((this as HTMLElement).dataset.ordinal || 0) * 44; } });

beforeEach(() => {
  scrollTo.mockClear();
  Element.prototype.scrollTo = scrollTo as unknown as Element['scrollTo'];
  vi.mocked(listTranscriptCues).mockImplementation(async (id) => ({ items: id === 'de' ? [cue(0, 'Morgen auf dem Grat')] : cues, next_cursor: null }));
  vi.mocked(searchTranscript).mockResolvedValue([cues[1]]);
});
it('test_active_cue_index', () => {
  expect(activeCueIndex(cues, 0)).toBe(-1);
  expect(activeCueIndex(cues, 500)).toBe(0);
  expect(activeCueIndex(cues, 4600)).toBe(1);
  expect(activeCueIndex(cues, 99_000)).toBe(2);
});

it('test_transcript_search_seek', async () => {
  const onSeek = vi.fn();
  render(<TranscriptPanel currentTime={0} onSeek={onSeek} transcripts={[transcript('en', 'en')]} />);
  await screen.findByText('Morning on the ridge');
  await userEvent.type(screen.getByRole('searchbox', { name: 'Search transcript' }), 'BRIDGE{Enter}');
  expect(searchTranscript).toHaveBeenCalledWith('en', 'BRIDGE');
  expect(await screen.findByText('1 of 1')).toBeTruthy();
  const mark = screen.getByText('bridge', { selector: 'mark' });
  const row = mark.closest('button') as HTMLButtonElement;
  expect(row.className).toBe('current-match');
  await userEvent.click(row);
  expect(onSeek).toHaveBeenCalledWith(4.5);
});

it('test_transcript_manual_scroll_respected', async () => {
  const { rerender } = render(<TranscriptPanel currentTime={1} onSeek={vi.fn()} transcripts={[transcript('en', 'en')]} />);
  await screen.findByText('Morning on the ridge');
  const follow = screen.getByRole('button', { name: /follow/i });
  expect(follow.getAttribute('aria-pressed')).toBe('true');
  fireEvent.scroll(screen.getByRole('list', { name: 'Transcript' }));
  expect(follow.getAttribute('aria-pressed')).toBe('false');
  scrollTo.mockClear();
  rerender(<TranscriptPanel currentTime={9} onSeek={vi.fn()} transcripts={[transcript('en', 'en')]} />);
  expect(scrollTo).not.toHaveBeenCalled();
  await userEvent.click(follow);
  expect(follow.getAttribute('aria-pressed')).toBe('true');
  expect(scrollTo).toHaveBeenCalled();
});

it('test_transcript_keyboard_language', async () => {
  render(<TranscriptPanel currentTime={0} onSeek={vi.fn()} transcripts={[transcript('en', 'en'), transcript('de', 'de')]} />);
  await screen.findByText('Morning on the ridge');
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Transcript language' }), 'de');
  await waitFor(() => expect(screen.getByText('Morgen auf dem Grat')).toBeTruthy());
  expect(screen.queryByText('Morning on the ridge')).toBeNull();
  expect(listTranscriptCues).toHaveBeenLastCalledWith('de', null);
});
