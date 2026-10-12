// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { keepLiveRecording, listLiveRecordings } from './api';
import { summarizeLiveRecording } from './liveRecording';
import { LiveRecordingControls } from './liveRecordingControls';
import type { LiveRecording } from './types';

vi.mock('./api', async (importActual) => ({
  ...(await importActual<typeof import('./api')>()),
  listLiveRecordings: vi.fn(),
  getLiveRecording: vi.fn(),
  keepLiveRecording: vi.fn(),
}));

const SOURCE = 'https://www.youtube.com/watch?v=LIVE1';

function rec(overrides: Partial<LiveRecording> = {}): LiveRecording {
  return {
    id: 'rec-1', source_url: SOURCE, status: 'partial', stop_requested: false, cancel_requested: false,
    media: { status: 'partial', library_item_id: 'item-1', end_reason: 'size_limit' },
    chat: { status: 'unavailable' }, created_at: '2026-07-19T00:00:00',
    max_runtime_seconds: 21600, max_bytes: 8 * 1024 ** 3,
    ...overrides,
  };
}

describe('recording lifecycle', () => {
  it('names why capture ended and discloses a restart gap', () => {
    const summary = summarizeLiveRecording(rec({ resumed_after_restart: true }));
    expect(summary.endNote).toBe('Stopped at the recording size limit.');
    expect(summary.restartNote).toMatch(/restarted during this recording/);
    expect(summary.limitsNote).toBeNull();
  });

  it('shows the limits and destination while recording', () => {
    const summary = summarizeLiveRecording(rec({ status: 'live', media: { status: 'recording' } }));
    expect(summary.limitsNote).toBe('Stops automatically after 6 h or 8 GB. Saved privately to your Library as a recording.');
    expect(summary.endNote).toBeNull();
  });

  it('lets the member keep a finished recording out of automatic cleanup', async () => {
    vi.mocked(listLiveRecordings).mockResolvedValue({ items: [rec()], next_cursor: null });
    vi.mocked(keepLiveRecording).mockResolvedValue(rec({ kept: true }));
    render(<LiveRecordingControls sourceUrl={SOURCE} />);
    const keep = await screen.findByRole('checkbox', { name: /keep this recording/i });
    expect((keep as HTMLInputElement).checked).toBe(false);
    fireEvent.click(keep);
    await waitFor(() => expect((keep as HTMLInputElement).checked).toBe(true));
    expect(keepLiveRecording).toHaveBeenCalledWith('rec-1', true);
    expect(screen.getByText('Stopped at the recording size limit.')).toBeTruthy();
  });
});
