import { describe, expect, it } from 'vitest';

import { CHANNEL_ID, liveEntry, liveSnapshot, remoteEntry } from '../../test/remoteFixtures';
import type { PopularSnapshot } from '../../types';
import { channelTarget, exploreGroups, liveTeaser, popularNotice, popularRails } from './exploreModel';

const kinds = (id: string, kind: 'video' | 'short' | 'live' | 'channel' | 'playlist' | null) => remoteEntry(id, { kind });

describe('Explore model', () => {
  it('groups results by kind in the fixed order, live by lifecycle too', () => {
    const groups = exploreGroups([kinds('v', 'video'), kinds('n', null), kinds('s', 'short'), kinds('c', 'channel'), kinds('p', 'playlist'), kinds('l', 'live'), liveEntry('l2', { kind: 'video' })]);
    expect(Object.fromEntries(Object.entries(groups).map(([key, entries]) => [key, entries.map((entry) => entry.id)]))).toEqual({
      channels: ['c'], live: ['l', 'l2'], videos: ['v', 'n'], shorts: ['s'], playlists: ['p'],
    });
  });

  it('teases the eight most-watched live streams from the snapshot, follows included, once each', () => {
    const snapshot = liveSnapshot({ hero: [liveEntry('g1', { view_count: 30_000 }), liveEntry('f', { view_count: 99_000 })] });
    expect(liveTeaser(snapshot).map((entry) => entry.id)).toEqual(['f', 'g1', 'g3', 'm1', 'g2', 'm2']);
    expect(liveTeaser(liveSnapshot({ items: Array.from({ length: 12 }, (_, index) => liveEntry(`x${index}`)) }))).toHaveLength(8);
    expect(liveTeaser(null)).toEqual([]);
  });

  it('makes one rail per ready or stale popular category, in snapshot order', () => {
    const popular = {
      items: [remoteEntry('a', { category_keys: ['music'] }), remoteEntry('b', { category_keys: ['gaming', 'music'] })],
      categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }, { key: 'news', label: 'News', state: 'pending' }, { key: 'music', label: 'Music', state: 'stale' }],
      state: 'ready', refreshing: false, stale: false,
    } as PopularSnapshot;
    expect(popularRails(popular).map((rail) => [rail.key, rail.label, rail.entries.map((entry) => entry.id)])).toEqual([
      ['popular-gaming', 'Gaming', ['b']], ['popular-music', 'Music', ['a', 'b']],
    ]);
  });

  it('keeps the popular notices and states in their new words', () => {
    const base = { items: [remoteEntry('a')], categories: [], refreshing: false } as unknown as PopularSnapshot;
    expect(popularNotice({ ...base, state: 'partial', stale: false })).toBe('Showing a partial feed while more categories warm up.');
    expect(popularNotice({ ...base, state: 'ready', stale: true })).toBe('Showing the last-known feed while discovery refreshes.');
    expect(popularNotice({ ...base, state: 'ready', stale: false })).toBeNull();
  });

  it('sends a YouTube channel to its page, a handle through the resolver, and anything else to the watch flow', () => {
    expect(channelTarget(remoteEntry('c', { kind: 'channel', id: CHANNEL_ID, webpage_url: `https://www.youtube.com/channel/${CHANNEL_ID}` }))).toEqual({ href: `/channel/youtube/${CHANNEL_ID}`, id: CHANNEL_ID });
    expect(channelTarget(remoteEntry('c', { kind: 'channel', id: 'harbor', uploader_id: '@harbor', uploader_url: null, webpage_url: 'https://www.youtube.com/@harbor' }))).toEqual({ href: `/channel?url=${encodeURIComponent('https://www.youtube.com/@harbor')}`, id: null });
    expect(channelTarget(remoteEntry('c', { kind: 'channel', source: 'soundcloud', uploader_id: null, uploader_url: null, webpage_url: 'https://soundcloud.com/artist' }))).toBeNull();
  });
});
