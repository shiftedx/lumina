import { describe, expect, it } from 'vitest';

import {
  canRecordFromNow,
  forwardOnlyRecordingBoundary,
  canScheduleUpcoming,
  isFromStartAvailable,
  isLiveRecordingTerminal,
  liveRecordingStatusLabel,
  summarizeLiveRecording,
} from './liveRecording';
import type { LiveRecording, LiveRecordingChatStatus, LiveRecordingMediaStatus, MediaSourceCapabilities } from './types';

function caps(overrides: Partial<MediaSourceCapabilities> = {}): MediaSourceCapabilities {
  return {
    provider: 'youtube',
    lifecycle: 'live',
    can_play: true,
    can_acquire: false,
    can_record: true,
    chat: { live: 'available', replay: 'unavailable' },
    ...overrides,
  };
}

function recording(
  status: LiveRecording['status'],
  media: LiveRecordingMediaStatus,
  chat: LiveRecordingChatStatus,
  overrides: Partial<LiveRecording> = {},
): LiveRecording {
  return {
    id: 'rec-1',
    source_url: 'https://youtube.com/watch?v=LIVE1',
    status,
    stop_requested: false,
    cancel_requested: false,
    media: { status: media, library_item_id: media === 'completed' ? 'item-1' : null },
    chat: { status: chat, chat_asset_id: chat === 'completed' ? 'chat-1' : null },
    created_at: '2026-07-19T00:00:00',
    ...overrides,
  };
}

describe('canRecordFromNow', () => {
  it('offers recording only when the capability allows it', () => {
    expect(canRecordFromNow(caps())).toBe(true);
    expect(canRecordFromNow(caps({ can_record: false }))).toBe(false);
    expect(canRecordFromNow(null)).toBe(false);
  });
});

describe('isLiveRecordingTerminal', () => {
  it('treats only the four documented terminal states as done', () => {
    for (const status of ['completed', 'partial', 'failed', 'cancelled'] as const) {
      expect(isLiveRecordingTerminal(status)).toBe(true);
    }
    for (const status of ['queued', 'live', 'stopping', 'finalizing'] as const) {
      expect(isLiveRecordingTerminal(status)).toBe(false);
    }
  });
});

describe('summarizeLiveRecording', () => {
  it('reports both outputs succeeding as a success', () => {
    const summary = summarizeLiveRecording(recording('completed', 'completed', 'completed'));
    expect(summary.tone).toBe('success');
    expect(summary.libraryItemId).toBe('item-1');
    expect(summary.chatAssetId).toBe('chat-1');
  });

  it('reports media-ok + chat-fail as an explicit partial, never a failure', () => {
    const summary = summarizeLiveRecording(recording('partial', 'completed', 'failed'));
    expect(summary.tone).toBe('partial');
    expect(summary.libraryItemId).toBe('item-1');
    expect(summary.chatSummary).toMatch(/could not be captured/i);
  });

  it('reports chat-ok + media-fail as an explicit partial, never a total failure', () => {
    const summary = summarizeLiveRecording(recording('partial', 'failed', 'completed'));
    expect(summary.tone).toBe('partial');
    expect(summary.chatAssetId).toBe('chat-1');
    expect(summary.mediaSummary).toMatch(/could not be recorded/i);
  });

  it('completes when the broadcast simply had no chat', () => {
    const summary = summarizeLiveRecording(recording('completed', 'completed', 'unavailable'));
    expect(summary.tone).toBe('success');
    expect(summary.chatSummary).toMatch(/no chat/i);
  });

  it('describes a usable-but-partial media capture as not the whole broadcast', () => {
    const summary = summarizeLiveRecording(recording('partial', 'partial', 'completed'));
    expect(summary.tone).toBe('partial');
    // A media 'partial' still published a Library item, but honestly signals it
    // is not a complete capture.
    expect(summary.mediaSummary).toMatch(/whole broadcast|partial/i);
  });

  it('keeps cancellation distinct from failure', () => {
    expect(summarizeLiveRecording(recording('cancelled', 'failed', 'failed')).tone).toBe('cancelled');
    expect(summarizeLiveRecording(recording('failed', 'failed', 'unavailable')).tone).toBe('failure');
  });

  it('labels non-terminal states as in-progress', () => {
    expect(summarizeLiveRecording(recording('live', 'recording', 'capturing')).tone).toBe('progress');
    expect(liveRecordingStatusLabel('stopping')).toBe('Stopping');
  });

  it('degrades instead of throwing on a malformed recording missing a sibling', () => {
    // A contract-violating payload must never white-screen the Watch surface.
    const malformed = {
      id: 'x', source_url: 's', status: 'partial', stop_requested: false, cancel_requested: false, created_at: '',
    } as unknown as Parameters<typeof summarizeLiveRecording>[0];
    expect(() => summarizeLiveRecording(malformed)).not.toThrow();
    expect(summarizeLiveRecording(malformed).tone).toBe('partial');
  });

  // -- issue #98: scheduling + from-start honesty --------------------------

  it('labels and tones the waiting/scheduled phase distinctly', () => {
    const summary = summarizeLiveRecording(
      recording('waiting', 'pending', 'pending', { scheduled_start_at: '2026-07-20T18:00:00' }),
    );
    expect(summary.tone).toBe('waiting');
    expect(liveRecordingStatusLabel('waiting')).toBe('Waiting for the broadcast');
    expect(summary.waitingNote).toMatch(/Waiting for this broadcast to begin/i);
  });

  it('records where capture began for the final result', () => {
    const fromStart = summarizeLiveRecording(
      recording('completed', 'completed', 'completed', { capture_origin: 'source_beginning', history: 'complete' }),
    );
    expect(fromStart.originSummary).toMatch(/beginning of the broadcast/i);
    const edge = summarizeLiveRecording(
      recording('completed', 'completed', 'completed', { capture_origin: 'live_edge', history: 'from_edge' }),
    );
    expect(edge.originSummary).toMatch(/live edge/i);
  });

  it('discloses partial-history explicitly and never a false complete', () => {
    const summary = summarizeLiveRecording(
      recording('partial', 'completed', 'completed', { capture_origin: 'source_beginning', history: 'partial' }),
    );
    expect(summary.tone).toBe('partial');
    expect(summary.historyNote).toMatch(/Earlier history is incomplete/i);
    // A complete history carries no partial-history note.
    const complete = summarizeLiveRecording(
      recording('completed', 'completed', 'completed', { capture_origin: 'source_beginning', history: 'complete' }),
    );
    expect(complete.historyNote).toBeNull();
  });

  it('explains each bounded scheduled-acquisition outcome', () => {
    const reasons: Record<string, RegExp> = {
      source_cancelled: /cancelled before it started/i,
      excessive_delay: /delayed too long/i,
      auth_expired: /requires a sign-in/i,
      from_start_unavailable: /from the beginning was not available/i,
    };
    for (const [reason, pattern] of Object.entries(reasons)) {
      const summary = summarizeLiveRecording(recording('failed', 'failed', 'unavailable', { waiting_reason: reason }));
      expect(summary.waitingNote).toMatch(pattern);
    }
  });

  it('flags an awaiting-fallback-choice result', () => {
    const summary = summarizeLiveRecording(
      recording('failed', 'failed', 'unavailable', { awaiting_fallback_choice: true }),
    );
    expect(summary.awaitingFallbackChoice).toBe(true);
  });
});

describe('canScheduleUpcoming / isFromStartAvailable', () => {
  it('reflects the capability seam', () => {
    expect(canScheduleUpcoming(caps({ lifecycle: 'upcoming', can_schedule: true }))).toBe(true);
    expect(canScheduleUpcoming(caps({ can_schedule: false }))).toBe(false);
    expect(canScheduleUpcoming(null)).toBe(false);
    expect(isFromStartAvailable(caps({ from_start_available: true }))).toBe(true);
    expect(isFromStartAvailable(caps({ from_start_available: false }))).toBe(false);
  });
});

describe('forwardOnlyRecordingBoundary', () => {
  it('states the media-only boundary for Twitch and never promises captured chat', () => {
    const note = forwardOnlyRecordingBoundary(caps({ provider: 'twitch' }));
    expect(note).toMatch(/from now/i);
    expect(note).toMatch(/Live chat is not captured/i);
    expect(note).toMatch(/nothing from earlier is imported/i);
  });

  it('states a generic forward-only boundary for other providers', () => {
    const note = forwardOnlyRecordingBoundary(caps({ provider: 'youtube' }));
    expect(note).toMatch(/from the moment Lumina connects/i);
    expect(note).toMatch(/Nothing from earlier/i);
  });

  it('makes the media claim honest when from-start is the effective intent, keeping chat forward-only', () => {
    const note = forwardOnlyRecordingBoundary(caps({ provider: 'youtube' }), { fromStartSelected: true });
    // MEDIA is best-effort from the broadcast beginning — never the blanket
    // "nothing from earlier is imported" media claim.
    expect(note).not.toMatch(/Nothing from earlier is imported/i);
    expect(note).toMatch(/best-effort/i);
    expect(note).toMatch(/beginning|from its start/i);
    // CHAT is still only captured from connect, and earlier chat is never imported.
    expect(note).toMatch(/chat/i);
    expect(note).toMatch(/from the moment Lumina connects/i);
    expect(note).toMatch(/earlier chat is never imported/i);
  });

  it('never applies from-start wording to Twitch, whose media-only boundary is unchanged', () => {
    // Twitch never advertises from-start; even if asked, its media-only
    // boundary must not gain a from-start media promise.
    const note = forwardOnlyRecordingBoundary(caps({ provider: 'twitch' }), { fromStartSelected: true });
    expect(note).toMatch(/Live chat is not captured/i);
    expect(note).toMatch(/nothing from earlier is imported/i);
    expect(note).not.toMatch(/best-effort/i);
  });
});
