import type { ChatReplayAssetStatus, TimedChatEvent, TimedChatEventKind, TimedChatModeration } from './types';

/**
 * Pure model for the synchronized replay chat rail. All synchronization,
 * search, seek validation, and degraded-state presentation lives here so the
 * Watch component stays a thin renderer and every rule is unit-tested. Nothing
 * in this module ever touches the media element; a failure here can only affect
 * the rail, never playback.
 */

/** Media offsets are milliseconds; a completed live longer than this is not synchronized. */
const MAX_SYNC_OFFSET_MS = 48 * 60 * 60 * 1000;

export interface ChatEventFilter {
  query?: string;
  kind?: TimedChatEventKind | 'all';
  hideModerated?: boolean;
}

/**
 * Index of the last event whose offset is at or before `positionMs`, or -1 when
 * none has arrived yet. Events without a usable offset never participate in
 * time synchronization. Ties resolve to the last event at that offset so the
 * rail is fully caught up to the current second.
 */
export function activeEventIndex(events: readonly TimedChatEvent[], positionMs: number): number {
  let result = -1;
  for (let index = 0; index < events.length; index += 1) {
    const offset = events[index].offset_ms;
    if (offset === null || offset === undefined || offset < 0) continue;
    if (offset <= positionMs) result = index;
    else if (offset > positionMs && result !== -1 && offset > events[result].offset_ms!) break;
  }
  return result;
}

/** Whether a chat event offset is a valid seek target for the current media. */
export function isSeekableOffset(offsetMs: number | null | undefined, durationSeconds: number | null): boolean {
  if (offsetMs === null || offsetMs === undefined || offsetMs < 0 || offsetMs > MAX_SYNC_OFFSET_MS) return false;
  if (durationSeconds === null || durationSeconds === undefined || !Number.isFinite(durationSeconds)) return true;
  return offsetMs <= durationSeconds * 1000;
}

/** Bounded search + filtering over the already-bounded event list. */
export function filterChatEvents(events: readonly TimedChatEvent[], filter: ChatEventFilter): TimedChatEvent[] {
  const query = (filter.query || '').trim().toLowerCase();
  const kind = filter.kind && filter.kind !== 'all' ? filter.kind : null;
  return events.filter((event) => {
    if (filter.hideModerated && event.moderation !== 'visible') return false;
    if (kind && event.kind !== kind) return false;
    if (!query) return true;
    const haystack = `${event.text} ${event.author?.name || ''}`.toLowerCase();
    return haystack.includes(query);
  });
}

const MODERATION_PLACEHOLDER: Record<TimedChatModeration, string | null> = {
  visible: null,
  deleted: 'Message removed',
  author_removed: 'Removed by a moderator',
};

/** Display text for one event, substituting a placeholder for moderated events. */
export function visibleEventText(event: TimedChatEvent): string {
  const placeholder = MODERATION_PLACEHOLDER[event.moderation];
  return placeholder ?? event.text;
}

export interface ChatReplayStatusView {
  label: string;
  note: string | null;
  showsEvents: boolean;
  tone: 'ready' | 'info' | 'muted';
}

/**
 * Human-facing description of an asset status. Every branch keeps playback
 * usable: even "failed" only reports that the chat could not load.
 */
export function chatReplayStatusView(status: ChatReplayAssetStatus): ChatReplayStatusView {
  switch (status) {
    case 'building':
      return { label: 'Loading replay chat…', note: null, showsEvents: false, tone: 'info' };
    case 'ready':
      return { label: 'Replay chat', note: null, showsEvents: true, tone: 'ready' };
    case 'partial':
      return {
        label: 'Replay chat',
        note: 'Showing a bounded part of a very long chat.',
        showsEvents: true,
        tone: 'info',
      };
    case 'oversized':
      return {
        label: 'Replay chat',
        note: 'This chat was too large to load in full; showing the earliest part.',
        showsEvents: true,
        tone: 'info',
      };
    case 'empty':
      return { label: 'No replay chat', note: 'This broadcast has no saved chat.', showsEvents: false, tone: 'muted' };
    case 'unavailable':
      return {
        label: 'Replay chat unavailable',
        note: 'The source did not provide a replay chat track.',
        showsEvents: false,
        tone: 'muted',
      };
    case 'malformed':
      return {
        label: 'Replay chat could not be read',
        note: 'The saved chat was not in a readable format.',
        showsEvents: false,
        tone: 'muted',
      };
    case 'failed':
    default:
      return {
        label: 'Replay chat could not load',
        note: 'You can keep watching; try loading chat again later.',
        showsEvents: false,
        tone: 'muted',
      };
  }
}
