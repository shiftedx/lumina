import { describe, expect, it } from 'vitest';

import { ApiRequestError } from '../../api';
import { CHANNEL_ID, channelPage, FIXED_NOW, libraryChannel, remoteEntry } from '../../test/remoteFixtures';
import { channelFailure, channelKicker, libraryChannelFor, pageLimitNote, pageTabs, sortEntries } from './channelPageModel';

describe('channel page model', () => {
  it('lists the tabs the response names, in order, plus In your library with its count', () => {
    expect(pageTabs(channelPage(), 12)).toEqual([['videos', 'Videos'], ['live', 'Live'], ['shorts', 'Shorts'], ['playlists', 'Playlists'], ['library', 'In your library (12)']]);
    expect(pageTabs(channelPage({ channel: { ...channelPage().channel, tabs: ['videos', 'shorts'] } }), 0)).toEqual([['videos', 'Videos'], ['shorts', 'Shorts']]);
    expect(pageTabs(null, 3)).toEqual([['videos', 'Videos'], ['library', 'In your library (3)']]);
  });

  it('writes the kicker, with Updated when served stale', () => {
    expect(channelKicker(channelPage(), FIXED_NOW)).toBe('YOUTUBE · @harborfilms · 1.2M subscribers · 845 videos');
    const stale = channelPage({ stale: true, fetched_at: new Date(FIXED_NOW.getTime() - 42 * 60_000).toISOString(), channel: { ...channelPage().channel, handle: null, video_count: null } });
    expect(channelKicker(stale, FIXED_NOW)).toBe('YOUTUBE · 1.2M subscribers · Updated 42 min ago');
  });

  it('sorts Latest as served and Popular by views, without touching the input', () => {
    const entries = [remoteEntry('a', { view_count: 5 }), remoteEntry('b', { view_count: null }), remoteEntry('c', { view_count: 50 })];
    expect(sortEntries(entries, 'latest').map((entry) => entry.id)).toEqual(['a', 'b', 'c']);
    expect(sortEntries(entries, 'popular').map((entry) => entry.id)).toEqual(['c', 'a', 'b']);
    expect(entries.map((entry) => entry.id)).toEqual(['a', 'b', 'c']);
  });

  it('tells an unavailable channel from an unreachable YouTube', () => {
    expect(channelFailure(new ApiRequestError('channel_unavailable', 404))).toBe('unavailable');
    expect(channelFailure(new ApiRequestError('YouTube did not respond in time.', 504))).toBe('unreachable');
    expect(channelFailure(new Error('network'))).toBe('unreachable');
  });

  it('finds the Library channel by id first, then by the uploader name', () => {
    const byId = libraryChannel('a', { channel_id: CHANNEL_ID, uploader: 'Old Name' });
    const byName = libraryChannel('b', { channel_id: null, uploader: 'Harbor Films' });
    expect(libraryChannelFor([byName, byId], CHANNEL_ID, 'Harbor Films')?.key).toBe('a');
    expect(libraryChannelFor([byName], CHANNEL_ID, 'Harbor Films')?.key).toBe('b');
    expect(libraryChannelFor([byName], CHANNEL_ID, 'Other')).toBeNull();
  });

  it('notes the 120 cap only once the second page still has more', () => {
    expect(pageLimitNote(60, true)).toBeNull();
    expect(pageLimitNote(120, true)).toBe('Showing the latest 120 · Open on YouTube for more');
    expect(pageLimitNote(120, false)).toBeNull();
  });
});
