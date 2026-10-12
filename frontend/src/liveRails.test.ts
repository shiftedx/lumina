import { describe, expect, it } from 'vitest';
import { buildLiveRails, LIVE_SHELF_MAX, liveNotices, liveShelfEntries } from './liveRails';
import type { LiveSnapshot } from './types';

function snapshot(overrides: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    items: [
      { id: 'g1', title: 'big stream', category_keys: ['gaming'] },
      { id: 'm1', title: 'concert', category_keys: ['music'] },
      { id: 'g2', title: 'small stream', category_keys: ['gaming'] },
    ],
    categories: [
      { key: 'gaming', label: 'Gaming', state: 'ready' },
      { key: 'music', label: 'Music', state: 'ready' },
      { key: 'news', label: 'News', state: 'empty' },
    ],
    state: 'ready',
    refreshing: false,
    stale: false,
    twitch_available: true,
    hero: [],
    ...overrides,
  };
}

describe('buildLiveRails', () => {
  it('groups items into category rails preserving snapshot order', () => {
    const rails = buildLiveRails(snapshot());
    expect(rails.map((rail) => rail.key)).toEqual(['gaming', 'music']);
    expect(rails[0].entries.map((entry) => entry.id)).toEqual(['g1', 'g2']);
    expect(rails[1].label).toBe('Music');
  });

  it('drops empty rails and handles null snapshot', () => {
    expect(buildLiveRails(null)).toEqual([]);
    const rails = buildLiveRails(snapshot({ items: [] }));
    expect(rails).toEqual([]);
  });

  it('keeps rail state so the surface can message stale categories', () => {
    const rails = buildLiveRails(
      snapshot({ categories: [{ key: 'gaming', label: 'Gaming', state: 'stale' }] }),
    );
    expect(rails[0].state).toBe('stale');
  });
});

describe('liveShelfEntries', () => {
  const stream = (id: string, url: string | null = `https://www.twitch.tv/${id}`) => ({ id, title: id, webpage_url: url });

  it('lists followed live first, then popular, each stream once by URL (first wins)', () => {
    const entries = liveShelfEntries(snapshot({ hero: [stream('mine')], items: [stream('big'), stream('mine-again', 'https://www.twitch.tv/mine'), stream('small')] }));
    expect(entries.map(({ entry, followed }) => [entry.id, followed])).toEqual([['mine', true], ['big', false], ['small', false]]);
  });

  it('falls back to the id for an entry without a URL, skips one with neither, and caps at 20', () => {
    const items = [stream('no-url', null), { title: 'nothing to key on' }, ...Array.from({ length: 30 }, (_, index) => stream(`s${index}`))];
    const entries = liveShelfEntries(snapshot({ hero: [], items }));
    expect(entries).toHaveLength(LIVE_SHELF_MAX);
    expect(entries[0].entry.id).toBe('no-url');
    expect(entries.some(({ entry }) => entry.title === 'nothing to key on')).toBe(false);
    expect(liveShelfEntries(null)).toEqual([]);
  });

  it('keeps liveNotices unchanged', () => {
    expect(liveNotices(snapshot({ twitch_available: false }))).toEqual(['Twitch streams are temporarily unavailable.']);
    expect(liveNotices(snapshot())).toEqual([]);
  });
});
