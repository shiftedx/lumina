import { describe, expect, it } from 'vitest';

import { CHANNEL_ID, endedEntry, liveEntry, liveSnapshot, remoteEntry } from '../../test/remoteFixtures';
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

  it('keeps followed live streams first, dedupes by first occurrence, and omits non-live entries', () => {
    const followed = liveEntry('followed', { title: 'Followed first', view_count: 1 });
    const followedSecond = liveEntry('followed-second', { view_count: 99 });
    const backendCopy = liveEntry('followed', { title: 'Backend copy', view_count: 100_000 });
    const backend = liveEntry('backend', { view_count: 90_000 });
    const teaser = liveTeaser(liveSnapshot({ hero: [followed, followedSecond, endedEntry('ended')], items: [backendCopy, backend] }));
    expect(teaser.map((entry) => entry.id)).toEqual(['followed', 'followed-second', 'backend']);
    expect(teaser[0]?.title).toBe('Followed first');
    expect(liveTeaser(null)).toEqual([]);
  });

  it('filters the selected provider before applying the 16-entry limit', () => {
    const followed = liveEntry('followed-youtube', { title: 'Followed YouTube', source: 'youtube', view_count: 1 });
    const twitch = Array.from({ length: 18 }, (_, index) => liveEntry(`twitch-${index}`, { source: 'twitch', view_count: 100_000 - index }));
    const youtube = Array.from({ length: 15 }, (_, index) => liveEntry(`youtube-${index}`, { source: 'youtube', view_count: 10_000 - index }));
    const teaser = liveTeaser(liveSnapshot({ hero: [followed], items: [...twitch, ...youtube] }), (item) => item.source === 'youtube');
    expect(teaser).toHaveLength(16);
    expect(teaser.map((entry) => entry.id)).toEqual(['followed-youtube', ...youtube.map((entry) => entry.id)]);
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
