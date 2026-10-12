import { describe, expect, it } from 'vitest';

import { stillItem } from '../../test/galleryFixtures';
import { CHANNEL_ID, remoteEntry } from '../../test/remoteFixtures';
import { bylineChannelId, isWebVideo, spanLabel, watchBadge, watchMeta, watchProvider } from './watchInfoModel';

const now = new Date(2026, 8, 29, 20, 42);

describe('the web-video watch column model', () => {
  it('treats remote sources and saved items without a Media title as web video', () => {
    expect(isWebVideo({ kind: 'remote', item: remoteEntry(), preview: null })).toBe(true);
    expect(isWebVideo({ kind: 'library', item: stillItem('v', { title_id: null }) })).toBe(true);
    expect(isWebVideo({ kind: 'library', item: stillItem('m', { title_id: 'movie-1' }) })).toBe(false);
  });

  it('names the provider from the capabilities, the extractor or the source', () => {
    expect(watchProvider({}, remoteEntry('a', { source: 'twitch' }), null)).toBe('Twitch');
    expect(watchProvider({ extractor_key: 'Youtube' }, null, stillItem())).toBe('YouTube');
    expect(watchProvider({}, null, stillItem('k', { extractor: 'kick' }))).toBe('Kick');
    expect(watchProvider({}, null, stillItem('x', { extractor: 'generic' }))).toBe('Web');
  });

  it('maps the lifecycle to the badge, ENDED winning once the relay said so', () => {
    expect(watchBadge('live', false)).toBe('live');
    expect(watchBadge('live', true)).toBe('ended');
    expect(watchBadge('upcoming', false)).toBe('upcoming');
    expect(watchBadge('completed_live', false)).toBe('ended');
    expect(watchBadge('post_live', false)).toBe('ended');
    expect(watchBadge('vod', true)).toBeNull();
    expect(watchBadge('vod', false)).toBeNull();
  });

  it('writes the meta line for each state', () => {
    expect(watchMeta({ state: null, published: '20251003', views: 1_200_000, duration: 1452 }, now)).toBe(`${new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(2025, 9, 3))} · 1.2M views · 24:12`);
    const started = new Date(now.getTime() - (2 * 60 + 10) * 60_000).getTime() / 1000;
    expect(watchMeta({ state: 'live', viewers: 12_412, startedAt: started }, now)).toBe(`${(12_412).toLocaleString()} watching · Started 2h 10m ago`);
    expect(watchMeta({ state: 'live', viewers: 12_412, viewersAsOf: now.getTime() }, now)).toBe(`${(12_412).toLocaleString()} watching (as of ${now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })})`);
    const tuesday = new Date(2026, 8, 29, 20, 30);
    expect(watchMeta({ state: 'upcoming', startsAt: tuesday.toISOString() }, now)).toBe(`Starts ${tuesday.toLocaleDateString(undefined, { weekday: 'short' })} ${tuesday.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`);
    expect(watchMeta({ state: 'ended', duration: 7800 }, now)).toBe('Ended · streamed 2h 10m');
    expect(watchMeta({ state: 'live' }, now)).toBe('');
    expect(watchMeta({ state: 'live', viewers: 0 }, now)).toBe('0 watching');
  });

  it('spans hours and minutes compactly', () => {
    expect([spanLabel(7800), spanLabel(540), spanLabel(59)]).toEqual(['2h 10m', '9m', '0m']);
  });

  it('links a YouTube byline only when a channel id is known', () => {
    expect(bylineChannelId({ channel_id: CHANNEL_ID }, null, 'YouTube')).toBe(CHANNEL_ID);
    expect(bylineChannelId({ uploader_url: `https://www.youtube.com/channel/${CHANNEL_ID}` }, null, 'YouTube')).toBe(CHANNEL_ID);
    expect(bylineChannelId({}, remoteEntry(), 'YouTube')).toBe(CHANNEL_ID);
    expect(bylineChannelId({ channel_id: CHANNEL_ID }, null, 'Twitch')).toBeNull();
    expect(bylineChannelId({ uploader_id: '@harbor' }, null, 'YouTube')).toBeNull();
  });
});
