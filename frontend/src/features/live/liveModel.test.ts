import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { endedEntry, FIXED_NOW, liveEntry, liveSnapshot, upcomingEntry } from '../../test/remoteFixtures';
import { clockTime, liveKicker, liveNoticeLines, liveView, mergeRail, startedAgo, upcomingGroups } from './liveModel';

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(FIXED_NOW); });
afterEach(() => vi.useRealTimers());

const ids = (entries: Array<{ id?: string | null }>) => entries.map((entry) => entry.id);

describe('the Live page model', () => {
  it('leads with the most-watched followed stream and rails the other follows', () => {
    const snapshot = liveSnapshot({ hero: [liveEntry('f1', { view_count: 10 }), liveEntry('f2', { view_count: 99 }), upcomingEntry('f3', null)] });
    const view = liveView(snapshot, 'all');
    expect(view.hero?.id).toBe('f2');
    expect(view.heroFromFollows).toBe(true);
    expect(ids(view.alsoFollowed)).toEqual(['f1']);
    expect(view.followedCount).toBe(2);
    expect(view.liveCount).toBe(7);
  });

  it('falls back to the most-watched live stream overall, and leaves it out of its rail', () => {
    const view = liveView(liveSnapshot(), 'all');
    expect(view.hero?.id).toBe('g1');
    expect(view.heroFromFollows).toBe(false);
    expect(view.rails.map((rail) => [rail.key, ids(rail.entries)])).toEqual([['gaming', ['g2', 'g3']], ['music', ['m1', 'm2']]]);
  });

  it('omits the hero when nothing is live, and filters by provider', () => {
    expect(liveView(liveSnapshot({ items: [endedEntry('e1')] }), 'all').hero).toBeNull();
    const twitch = liveView(liveSnapshot(), 'twitch');
    expect(twitch.hero?.id).toBe('g3');
    expect(twitch.rails).toEqual([]);
    expect(twitch.providers).toEqual(['youtube', 'twitch']);
    expect(liveView(liveSnapshot({ twitch_available: false }), 'all').providers).toEqual(['youtube', 'twitch']);
  });

  it('collects upcoming and at most 12 recently ended', () => {
    const items = [...Array.from({ length: 14 }, (_, index) => endedEntry(`e${index}`)), upcomingEntry('u1', null)];
    const view = liveView(liveSnapshot({ items }), 'all');
    expect(view.ended).toHaveLength(12);
    expect(ids(view.upcoming)).toEqual(['u1']);
  });

  it('groups the schedule by day, with unknown times last', () => {
    const at = (day: number, hour: number) => new Date(2026, 8, day, hour, 30).toISOString();
    const groups = upcomingGroups([upcomingEntry('x', null), upcomingEntry('c', at(32, 9)), upcomingEntry('b', at(30, 8)), upcomingEntry('a', at(29, 21)), upcomingEntry('a2', at(29, 22))]);
    expect(groups.map((group) => [group.label, ids(group.entries)])).toEqual([
      ['Later today', ['a', 'a2']],
      ['Tomorrow', ['b']],
      [new Date(2026, 9, 2).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }), ['c']],
      ['Time not announced', ['x']],
    ]);
  });

  it('writes the kicker from the counts and the check time', () => {
    const snapshot = liveSnapshot({ hero: [liveEntry('f1')] });
    const view = liveView(snapshot, 'all');
    expect(liveKicker(snapshot, view)).toBe(`6 live now · 1 from your follows · Checked ${clockTime(snapshot.refreshed_at)}`);
    expect(liveKicker({ ...snapshot, refreshing: true }, view)).toBe('6 live now · 1 from your follows · Refreshing…');
    expect(liveKicker({ ...snapshot, stale: true }, view)).toBe(`6 live now · 1 from your follows · Last known at ${clockTime(snapshot.last_success_at)}`);
    expect(liveKicker(null, liveView(null, 'all'))).toBe('');
  });

  it('says what could not be checked, and when, never that nobody is live', () => {
    const snapshot = liveSnapshot({ twitch_available: false, state: 'partial', followed_unavailable: [{ source: 'kick', checked_at: '2026-09-29T20:39:00' }] });
    expect(liveNoticeLines(snapshot, true)).toEqual([
      `Twitch could not be checked · last tried ${clockTime(snapshot.refreshed_at)}`,
      `Live status for followed Kick channels is unavailable · last tried ${clockTime('2026-09-29T20:39:00')}`,
      'Showing a partial feed while more categories warm up.',
    ]);
    expect(liveNoticeLines(liveSnapshot({ stale: true }), true)).toEqual(['Showing the last-known feed while discovery refreshes.']);
    expect(liveNoticeLines(liveSnapshot({ stale: true }), false)).toEqual([]);
  });

  it('says how long ago a stream started, compactly', () => {
    expect(startedAgo(new Date(FIXED_NOW.getTime() - 130 * 60_000).toISOString())).toBe('Started 2h ago');
    expect(startedAgo(new Date(FIXED_NOW.getTime() - 9 * 60_000).toISOString())).toBe('Started 9m ago');
    expect(startedAgo(null)).toBeNull();
  });
});

describe('the refresh merge', () => {
  const [a, b, c, d] = ['a', 'b', 'c', 'd'].map((id) => liveEntry(id, { webpage_url: `https://www.youtube.com/watch?v=${id}` }));
  const key = (id: string) => `https://www.youtube.com/watch?v=${id}`;

  it('keeps the visit\'s order, updates entries in place and appends newcomers', () => {
    const first = mergeRail(undefined, [a, b, c], null);
    const next = mergeRail(first, [c, { ...b, view_count: 1 }, d], null);
    expect(ids(next.entries)).toEqual(['b', 'c', 'd']);
    expect(next.entries[0].view_count).toBe(1);
    expect(next.ended.size).toBe(0);
  });

  it('keeps a vanished focused card as ENDED until focus leaves its rail', () => {
    const first = mergeRail(undefined, [a, b, c], null);
    const kept = mergeRail(first, [a, c], key('b'));
    expect(ids(kept.entries)).toEqual(['a', 'b', 'c']);
    expect([...kept.ended]).toEqual([key('b')]);
    const stillInRail = mergeRail(kept, [a, c], key('c'));
    expect(ids(stillInRail.entries)).toEqual(['a', 'b', 'c']);
    const left = mergeRail(stillInRail, [a, c], null);
    expect(ids(left.entries)).toEqual(['a', 'c']);
    expect(left.ended.size).toBe(0);
  });
});
