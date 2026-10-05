import { describe, expect, it } from 'vitest';

import {
  activeEventIndex,
  chatReplayStatusView,
  filterChatEvents,
  isSeekableOffset,
  visibleEventText,
} from './chatReplay';
import type { TimedChatEvent } from './types';

function event(partial: Partial<TimedChatEvent> & { id: string }): TimedChatEvent {
  return {
    offset_ms: 0,
    kind: 'message',
    text: '',
    moderation: 'visible',
    author: { name: 'Someone', badges: [] },
    ...partial,
  };
}

const events: TimedChatEvent[] = [
  event({ id: 'a', offset_ms: 1000, text: 'first', author: { name: 'Ada', badges: [] } }),
  event({ id: 'b', offset_ms: 3000, text: 'hello world', author: { name: 'Grace', badges: ['moderator'] } }),
  event({ id: 'c', offset_ms: 3000, text: 'same second', author: { name: 'Linus', badges: [] } }),
  event({ id: 'd', offset_ms: 8000, kind: 'paid_message', amount: '$5.00', text: 'nice', author: { name: 'Guido', badges: [] } }),
  event({ id: 'e', offset_ms: null, text: 'no offset', author: { name: 'Anon', badges: [] } }),
];

describe('chat rail synchronization', () => {
  it('finds the last event at or before the playback position', () => {
    expect(activeEventIndex(events, 0)).toBe(-1);
    expect(activeEventIndex(events, 999)).toBe(-1);
    expect(activeEventIndex(events, 1000)).toBe(0);
    expect(activeEventIndex(events, 2999)).toBe(0);
    // Ties resolve to the last event sharing that offset so the rail advances fully.
    expect(activeEventIndex(events, 3000)).toBe(2);
    expect(activeEventIndex(events, 100000)).toBe(3);
  });

  it('ignores events without a usable offset when synchronizing', () => {
    // The offset-less event at the end never becomes "active" by time.
    expect(activeEventIndex(events, 100000)).not.toBe(4);
  });
});

describe('chat rail seeking', () => {
  it('validates offsets against media duration before seeking', () => {
    expect(isSeekableOffset(3000, 600)).toBe(true); // 3s into a 600s video
    expect(isSeekableOffset(null, 600)).toBe(false);
    expect(isSeekableOffset(-1, 600)).toBe(false);
    expect(isSeekableOffset(700000, 600)).toBe(false); // beyond duration
    expect(isSeekableOffset(3000, null)).toBe(true); // unknown duration stays seekable if non-negative
  });
});

describe('chat rail search and filtering', () => {
  it('matches on text and author name case-insensitively', () => {
    expect(filterChatEvents(events, { query: 'HELLO' }).map((item) => item.id)).toEqual(['b']);
    expect(filterChatEvents(events, { query: 'ada' }).map((item) => item.id)).toEqual(['a']);
  });

  it('filters to paid events only', () => {
    expect(filterChatEvents(events, { kind: 'paid_message' }).map((item) => item.id)).toEqual(['d']);
  });

  it('hides moderated events when requested but keeps them otherwise', () => {
    const withRemoved = [...events, event({ id: 'x', offset_ms: 9000, text: '', moderation: 'deleted' })];
    expect(filterChatEvents(withRemoved, { hideModerated: true }).some((item) => item.id === 'x')).toBe(false);
    expect(filterChatEvents(withRemoved, {}).some((item) => item.id === 'x')).toBe(true);
  });

  it('never searches an unbounded query and returns nothing sensible for blank input', () => {
    expect(filterChatEvents(events, { query: '   ' }).length).toBe(events.length);
    expect(filterChatEvents(events, { query: 'zzz-not-present' }).length).toBe(0);
  });
});

describe('moderated event presentation', () => {
  it('renders a placeholder for deleted and author-removed events without leaking text', () => {
    expect(visibleEventText(event({ id: 'k', text: '', moderation: 'deleted' }))).toBe('Message removed');
    expect(visibleEventText(event({ id: 'k', text: '', moderation: 'author_removed' }))).toBe('Removed by a moderator');
    expect(visibleEventText(event({ id: 'k', text: 'kept', moderation: 'visible' }))).toBe('kept');
  });
});

describe('chat rail degraded-state view model', () => {
  it('describes every asset status without ever implying playback is broken', () => {
    for (const status of ['building', 'ready', 'empty', 'partial', 'oversized', 'unavailable', 'malformed', 'failed'] as const) {
      const view = chatReplayStatusView(status);
      expect(typeof view.label).toBe('string');
      expect(view.label.length).toBeGreaterThan(0);
      // Only "ready"/"partial"/"oversized" actually render the event list.
      expect(view.showsEvents).toBe(['ready', 'partial', 'oversized'].includes(status));
    }
    expect(chatReplayStatusView('partial').note).toMatch(/bounded|limit|part/i);
  });
});
