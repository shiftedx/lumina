import { SOURCE_LABELS } from './luminaModel';
import type { LiveSnapshot, YouTubeSearchResult } from './types';

export interface LiveRail {
  key: string;
  label: string;
  state: 'pending' | 'ready' | 'stale' | 'empty' | 'failed';
  liveCount: number | null;
  entries: Array<YouTubeSearchResult & { category_keys?: string[] }>;
}

/** One rail per snapshot category, in snapshot order; empty rails are dropped.
 * Entry order inside a rail follows the snapshot (the backend viewer-ranks it). */
export function buildLiveRails(snapshot: LiveSnapshot | null): LiveRail[] {
  if (!snapshot) return [];
  return snapshot.categories
    .map((category) => ({
      key: category.key,
      label: category.label,
      state: category.state,
      liveCount: category.live_count ?? null,
      entries: snapshot.items.filter((item) => item.category_keys?.includes(category.key)),
    }))
    .filter((rail) => rail.entries.length > 0);
}

/** Honest partial-availability notes: what could not be checked, and when it was last tried. */
export function liveNotices(snapshot: LiveSnapshot | null): string[] {
  if (!snapshot) return [];
  const notices = snapshot.twitch_available ? [] : ['Twitch streams are temporarily unavailable.'];
  for (const { source, checked_at: checkedAt } of snapshot.followed_unavailable || []) {
    const time = new Date(checkedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    notices.push(`Live status for followed ${SOURCE_LABELS[source]} channels is unavailable (last tried ${time}).`);
  }
  return notices;
}

/** Home's Live shelf holds at most this many streams. */
export const LIVE_SHELF_MAX = 20;
export type LiveShelfEntry = { entry: YouTubeSearchResult; followed: boolean };

/**
 * Home's Live shelf: the member's followed channels live now (`hero`), then popular live (`items`, viewer-
 * ranked by the snapshot), each stream once by webpage_url (else id; first wins), capped at LIVE_SHELF_MAX.
 */
export function liveShelfEntries(snapshot: LiveSnapshot | null): LiveShelfEntry[] {
  if (!snapshot) return [];
  const seen = new Set<string>();
  const entries: LiveShelfEntry[] = [];
  for (const [list, followed] of [[snapshot.hero ?? [], true], [snapshot.items, false]] as const) {
    for (const entry of list) {
      const key = entry.webpage_url || entry.id;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      entries.push({ entry, followed });
      if (entries.length === LIVE_SHELF_MAX) return entries;
    }
  }
  return entries;
}
