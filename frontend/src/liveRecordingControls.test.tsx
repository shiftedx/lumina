import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveRecordingControls } from './liveRecordingControls';
import {
  cancelLiveRecording,
  createLiveRecording,
  getLiveRecording,
  listLiveRecordings,
  stopLiveRecording,
} from './api';
import type { LiveRecording, LiveRecordingChatStatus, LiveRecordingMediaStatus, MediaSourceCapabilities } from './types';

vi.mock('./api', async (importActual) => ({
  ...(await importActual<typeof import('./api')>()),
  listLiveRecordings: vi.fn(),
  createLiveRecording: vi.fn(),
  getLiveRecording: vi.fn(),
  stopLiveRecording: vi.fn(),
  cancelLiveRecording: vi.fn(),
}));

const SOURCE = 'https://www.youtube.com/watch?v=LIVE1';

// The live-recordings list is paginated (issue #111); mocks resolve one page.
function page(items: LiveRecording[], next_cursor: string | null = null) {
  return { items, next_cursor };
}

function caps(canRecord = true): MediaSourceCapabilities {
  return {
    provider: 'youtube',
    lifecycle: 'live',
    can_play: true,
    can_acquire: false,
    can_record: canRecord,
    record_reason: canRecord ? null : 'live_record_not_supported',
    chat: { live: 'available', replay: 'unavailable' },
  };
}

function recording(
  status: LiveRecording['status'],
  media: LiveRecordingMediaStatus,
  chat: LiveRecordingChatStatus,
): LiveRecording {
  return {
    id: 'rec-1',
    source_url: SOURCE,
    title: 'Live stream',
    status,
    stop_requested: false,
    cancel_requested: false,
    media: { status: media, library_item_id: media === 'completed' || media === 'partial' ? 'item-1' : null },
    chat: { status: chat, chat_asset_id: chat === 'completed' ? 'chat-1' : null },
    created_at: '2026-07-19T00:00:00',
  };
}

beforeEach(() => {
  vi.mocked(listLiveRecordings).mockReset().mockResolvedValue(page([]));
  vi.mocked(createLiveRecording).mockReset();
  vi.mocked(getLiveRecording).mockReset();
  vi.mocked(stopLiveRecording).mockReset();
  vi.mocked(cancelLiveRecording).mockReset();
});

describe('LiveRecordingControls', () => {
  it('shows a disabled Record affordance with its reason for a live-but-not-recordable source', async () => {
    render(<LiveRecordingControls capabilities={caps(false)} sourceUrl={SOURCE} />);
    // The displayed-reason product contract: never hide the control, present the
    // stable reason like the play/acquire disabled affordances do.
    const button = await screen.findByRole('button', { name: /record from now/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Recording is not available for this source yet\./i)).toBeTruthy();
  });

  it('renders nothing for a non-live source that cannot be recorded', async () => {
    const vod: MediaSourceCapabilities = { ...caps(false), lifecycle: 'vod' };
    const { container } = render(<LiveRecordingControls capabilities={vod} sourceUrl={SOURCE} />);
    await waitFor(() => expect(listLiveRecordings).toHaveBeenCalled());
    expect(container.querySelector('.live-recording-controls')).toBeNull();
  });

  it('keeps the boundary media claim honest for a live from-start source while chat stays forward-only', async () => {
    // The recording surface: a currently-LIVE source that also advertises the best-effort
    // from-start intent (from_start_available is computed for lifecycle=='live'
    // INDEPENDENT of scheduling), so the intent chooser renders on a live source.
    const fromStart: MediaSourceCapabilities = { ...caps(), from_start_available: true };
    render(<LiveRecordingControls capabilities={fromStart} sourceUrl={SOURCE} />);
    await screen.findByRole('button', { name: /record from now/i });

    // Default live-edge intent keeps the strict forward-only boundary.
    expect(screen.getByRole('note').textContent).toMatch(/Nothing from earlier is imported/i);

    // Selecting from-start makes the MEDIA claim honest: from-start media is
    // best-effort from the broadcast beginning, so the note must NOT keep the
    // blanket "nothing from earlier is imported" media claim...
    fireEvent.click(screen.getByRole('radio', { name: /from the beginning if available/i }));
    const note = screen.getByRole('note');
    expect(note.textContent).not.toMatch(/Nothing from earlier is imported/i);
    expect(note.textContent).toMatch(/best-effort|experimental/i);
    expect(note.textContent).toMatch(/beginning|from its start/i);
    // ...while STILL disclosing chat is captured only from connect (never imported).
    expect(note.textContent).toMatch(/chat/i);
    expect(note.textContent).toMatch(/from the moment Lumina connects|earlier chat is never imported/i);
  });

  it('starts a recording and shows the durable multi-output panel', async () => {
    vi.mocked(createLiveRecording).mockResolvedValue(recording('live', 'recording', 'capturing'));
    render(<LiveRecordingControls capabilities={caps()} sourceUrl={SOURCE} />);

    const start = await screen.findByRole('button', { name: /record from now/i });
    fireEvent.click(start);
    await waitFor(() =>
      // The from-start intent rides along; a live source defaults to the live-edge
      // behavior (issue #98) unless the member chooses otherwise.
      expect(createLiveRecording).toHaveBeenCalledWith({
        source_url: SOURCE,
        start_intent: 'live_edge',
        fallback_policy: 'allow_live_edge',
      }),
    );
    const panel = await screen.findByRole('region', { name: 'Live recording' });
    // Both sibling outputs are shown while live.
    expect(within(panel).getByText(/Recording the video/i)).toBeTruthy();
    expect(within(panel).getByText(/Capturing chat/i)).toBeTruthy();
  });

  it('walks the paginated list to adopt a recording beyond the first page (issue #111)', async () => {
    const other: LiveRecording = { ...recording('completed', 'completed', 'completed'), id: 'rec-other', source_url: 'https://www.youtube.com/watch?v=OTHER' };
    vi.mocked(listLiveRecordings)
      .mockResolvedValueOnce(page([other], 'cursor-1'))
      .mockResolvedValueOnce(page([recording('partial', 'completed', 'failed')]));
    render(<LiveRecordingControls capabilities={caps()} sourceUrl={SOURCE} />);
    const panel = await screen.findByRole('region', { name: 'Live recording' });
    // A media-ok + chat-fail outcome is an explicit partial, never binary.
    expect(within(panel).getByText(/Recorded with a partial outcome/i)).toBeTruthy();
    expect(within(panel).getByText(/saved to your library/i)).toBeTruthy();
    expect(within(panel).getByText(/Chat could not be captured/i)).toBeTruthy();
    // A terminal recording exposes no stop/cancel controls.
    expect(within(panel).queryByRole('button', { name: /stop/i })).toBeNull();
    expect(listLiveRecordings).toHaveBeenCalledTimes(2);
    expect(vi.mocked(listLiveRecordings).mock.calls[1][0]).toEqual({ cursor: 'cursor-1' });
  });

  it('reports a Twitch recording as a media-only completion without any captured chat', async () => {
    const twitchRecording: LiveRecording = { ...recording('completed', 'completed', 'unavailable'), source_url: 'https://www.twitch.tv/coolstreamer' };
    vi.mocked(listLiveRecordings).mockResolvedValue(page([twitchRecording]));
    const twitchCaps: MediaSourceCapabilities = { ...caps(), provider: 'twitch', chat: { live: 'unavailable', replay: 'unavailable' } };
    render(<LiveRecordingControls capabilities={twitchCaps} sourceUrl="https://www.twitch.tv/coolstreamer" />);
    const panel = await screen.findByRole('region', { name: 'Live recording' });
    // The media sibling is published and the chat sibling carries the honest
    // "no chat to capture" state — a media-only outcome, never a partial.
    expect(within(panel).getByText(/saved to your library/i)).toBeTruthy();
    expect(within(panel).getByText(/no chat to capture/i)).toBeTruthy();
    expect(within(panel).queryByText(/partial/i)).toBeNull();
  });

  it('stops a live recording deliberately and shows the resulting partial', async () => {
    vi.mocked(listLiveRecordings).mockResolvedValue(page([recording('live', 'recording', 'capturing')]));
    vi.mocked(stopLiveRecording).mockResolvedValue(recording('partial', 'partial', 'completed'));
    render(<LiveRecordingControls capabilities={caps()} sourceUrl={SOURCE} />);
    const stop = await screen.findByRole('button', { name: /stop & save/i });
    fireEvent.click(stop);
    await waitFor(() => expect(stopLiveRecording).toHaveBeenCalledWith('rec-1'));
    const panel = screen.getByRole('region', { name: 'Live recording' });
    expect(within(panel).getByText(/partial recording was saved/i)).toBeTruthy();
  });

  it('cancels a live recording distinctly from stopping', async () => {
    vi.mocked(listLiveRecordings).mockResolvedValue(page([recording('live', 'recording', 'capturing')]));
    vi.mocked(cancelLiveRecording).mockResolvedValue(recording('cancelled', 'failed', 'failed'));
    render(<LiveRecordingControls capabilities={caps()} sourceUrl={SOURCE} />);
    const cancel = await screen.findByRole('button', { name: /^cancel$/i });
    fireEvent.click(cancel);
    await waitFor(() => expect(cancelLiveRecording).toHaveBeenCalledWith('rec-1'));
    expect(screen.getByText(/Recording cancelled/i)).toBeTruthy();
  });

  // -- issue #98: scheduling + from-start ------------------------------------

  function upcomingCaps(overrides: Partial<MediaSourceCapabilities> = {}): MediaSourceCapabilities {
    return {
      provider: 'youtube',
      lifecycle: 'upcoming',
      can_play: false,
      can_acquire: false,
      can_record: false,
      can_schedule: true,
      from_start_available: true,
      scheduled_start: '2026-07-20T18:00:00',
      chat: { live: 'available', replay: 'unavailable' },
      ...overrides,
    };
  }

  it('offers a schedule action for an upcoming broadcast with a from-start intent choice', async () => {
    render(<LiveRecordingControls capabilities={upcomingCaps()} sourceUrl={SOURCE} />);
    // A schedule action, not "record from now".
    expect(await screen.findByRole('button', { name: /schedule recording/i })).toBeTruthy();
    // The from-start intent is expressed as a choice, never a raw flag.
    expect(screen.getByRole('group', { name: /where should recording start/i })).toBeTruthy();
    expect(screen.getByRole('radio', { name: /from the beginning if available/i })).toBeTruthy();
    expect(screen.getByRole('radio', { name: /from the live edge/i })).toBeTruthy();
  });

  it('schedules with from_start intent and require-choice fallback when selected', async () => {
    vi.mocked(createLiveRecording).mockResolvedValue({
      ...recording('waiting', 'pending', 'pending'),
      start_intent: 'from_start',
      scheduled_start_at: '2026-07-20T18:00:00',
    });
    render(<LiveRecordingControls capabilities={upcomingCaps()} sourceUrl={SOURCE} />);
    fireEvent.click(await screen.findByRole('radio', { name: /from the beginning if available/i }));
    fireEvent.click(screen.getByRole('checkbox', { name: /don.t record from the edge/i }));
    fireEvent.click(screen.getByRole('button', { name: /schedule recording/i }));
    await waitFor(() =>
      expect(createLiveRecording).toHaveBeenCalledWith({
        source_url: SOURCE,
        start_intent: 'from_start',
        fallback_policy: 'require_choice',
      }),
    );
  });

  it('shows the waiting state with the scheduled time for a scheduled recording', async () => {
    vi.mocked(listLiveRecordings).mockResolvedValue(page([
      { ...recording('waiting', 'pending', 'pending'), scheduled_start_at: '2026-07-20T18:00:00' },
    ]));
    render(<LiveRecordingControls capabilities={upcomingCaps()} sourceUrl={SOURCE} />);
    const panel = await screen.findByRole('region', { name: 'Live recording' });
    expect(within(panel).getAllByText(/Waiting for the broadcast/i).length).toBeGreaterThan(0);
    expect(within(panel).getByText(/Waiting for this broadcast to begin/i)).toBeTruthy();
    // A waiting recording can be cancelled (its schedule), never a false "recording".
    expect(within(panel).getByRole('button', { name: /cancel schedule/i })).toBeTruthy();
  });

  it('discloses partial-history explicitly for an incomplete from-start recording', async () => {
    vi.mocked(listLiveRecordings).mockResolvedValue(page([
      {
        ...recording('partial', 'completed', 'completed'),
        capture_origin: 'source_beginning',
        history: 'partial',
      },
    ]));
    render(<LiveRecordingControls capabilities={upcomingCaps()} sourceUrl={SOURCE} />);
    const panel = await screen.findByRole('region', { name: 'Live recording' });
    expect(within(panel).getByText(/began at the beginning of the broadcast/i)).toBeTruthy();
    // The explicit partial-history disclosure — never a silent false-complete —
    // is announced as a live region (role="status"), never a silent role="note".
    const historyNote = within(panel).getByText(/Earlier history is incomplete/i);
    expect(historyNote.closest('[role="status"]')).not.toBeNull();
  });

  it('offers to record from the edge when from-start was unavailable and choice was required', async () => {
    vi.mocked(listLiveRecordings).mockResolvedValue(page([
      { ...recording('failed', 'failed', 'unavailable'), awaiting_fallback_choice: true, waiting_reason: 'from_start_unavailable' },
    ]));
    vi.mocked(createLiveRecording).mockResolvedValue(recording('live', 'recording', 'capturing'));
    render(<LiveRecordingControls capabilities={upcomingCaps()} sourceUrl={SOURCE} />);
    const edge = await screen.findByRole('button', { name: /record from the live edge/i });
    fireEvent.click(edge);
    await waitFor(() =>
      expect(createLiveRecording).toHaveBeenCalledWith({
        source_url: SOURCE,
        start_intent: 'live_edge',
        fallback_policy: 'allow_live_edge',
      }),
    );
  });

  it('shows a disabled schedule affordance with its reason for a non-schedulable upcoming source', async () => {
    render(
      <LiveRecordingControls
        capabilities={upcomingCaps({ can_schedule: false, from_start_available: false, schedule_reason: 'upcoming_schedule_not_supported' })}
        sourceUrl={SOURCE}
      />,
    );
    const button = await screen.findByRole('button', { name: /schedule recording|record from now/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Scheduling is not available/i)).toBeTruthy();
  });
});
