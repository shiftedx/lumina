import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { endedEntry, FIXED_NOW, liveEntry, remoteEntry, upcomingEntry } from '../../test/remoteFixtures';
import { fallbackColour } from './galleryModel';
import { isRemoteEnded, relativeAge, remoteArt, remoteColour, remoteLabel, remoteLine, remoteMarker, remoteProvider, remoteScrim } from './remoteModel';

describe('remoteModel', () => {
  it('accepts only Lumina\'s own artwork URLs', () => {
    expect(remoteArt('/api/artwork/remote/abc')).toEqual({ url: '/api/artwork/remote/abc', widths: [] });
    expect(remoteArt('https://i.ytimg.com/vi/abc/hqdefault.jpg')).toBeNull();
    expect(remoteArt('//i.ytimg.com/x.jpg')).toBeNull();
    expect(remoteArt(null)).toBeNull();
  });

  it('colours a card from its address, else its id', () => {
    expect(remoteColour(remoteEntry('a'))).toEqual({ colour: fallbackColour('https://www.youtube.com/watch?v=a'), fromPalette: true });
    expect(remoteColour({ id: 'z', webpage_url: null })).toEqual({ colour: fallbackColour('z'), fromPalette: true });
  });

  it('names the provider from the source, then the capabilities, then YouTube', () => {
    expect(remoteProvider(remoteEntry('a', { source: 'twitch' }))).toBe('twitch');
    expect(remoteProvider({ id: 'p', capabilities: { provider: 'kick', lifecycle: 'live', can_play: true, can_acquire: false, chat: { live: 'unavailable', replay: 'unavailable' } } })).toBe('kick');
    expect(remoteProvider({ id: 'p', capabilities: { provider: 'generic', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } } })).toBe('youtube');
  });

  it('treats post-live and completed-live as ended', () => {
    expect([isRemoteEnded('post_live'), isRemoteEnded('completed_live'), isRemoteEnded('live'), isRemoteEnded(undefined)]).toEqual([true, true, false, false]);
  });
});

describe('web card markers, captions and names', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(FIXED_NOW); });
  afterEach(() => vi.useRealTimers());
  const started = { position_seconds: 41, duration_seconds: 761, completed: false };
  const done = { position_seconds: 761, duration_seconds: 761, completed: true };

  it('follows the marker table', () => {
    expect(remoteMarker(remoteEntry('a', { saved_item_id: 'i1' }))).toEqual({ kind: 'unwatched' });
    expect(remoteMarker(remoteEntry('a', { saved_item_id: 'i1', progress: started }))).toEqual({ kind: 'progress', percent: (41 / 761) * 100 });
    expect(remoteMarker(remoteEntry('a', { progress: started }))).toEqual({ kind: 'progress', percent: (41 / 761) * 100 });
    expect(remoteMarker(remoteEntry('a', { saved_item_id: 'i1', progress: done }))).toEqual({ kind: 'none' });
    expect(remoteMarker(remoteEntry('a'))).toEqual({ kind: 'none' });
    expect(remoteMarker(liveEntry('l', { saved_item_id: 'i1', progress: started }))).toEqual({ kind: 'none' });
    expect(remoteMarker(upcomingEntry('u', null, { saved_item_id: 'i1' }))).toEqual({ kind: 'none' });
    expect(remoteMarker(remoteEntry('a', { saved_item_id: 'i1' }), true)).toEqual({ kind: 'none' });
    expect(remoteMarker(remoteEntry('a', { progress: { position_seconds: 1, duration_seconds: 1000, completed: false } }))).toEqual({ kind: 'progress', percent: 4 });
  });

  it('labels the scrim with viewers, length or SHORT', () => {
    expect(remoteScrim(liveEntry('l'))).toBe('12.4K watching');
    expect(remoteScrim(liveEntry('l', { view_count: null }))).toBeNull();
    expect(remoteScrim(remoteEntry('a', { duration: 724 }))).toBe('12:04');
    expect(remoteScrim(remoteEntry('s', { kind: 'short', duration: 40 }))).toBe('SHORT');
    expect(remoteScrim(endedEntry('e', { duration: 3600 }))).toBe('1:00:00');
    expect(remoteScrim(liveEntry('l'), true)).toBeNull();
  });

  it('writes the caption line in the spec\'s order, with the watch state last', () => {
    expect(remoteLine(remoteEntry('a'))).toBe(`Chan · ${relativeAge(remoteEntry('a').published_at)} · 1.2M views`);
    expect(relativeAge(remoteEntry('a').published_at)).toBe('3 days ago');
    expect(remoteLine(remoteEntry('a', { saved_item_id: 'i1' }))).toBe('Chan · 3 days ago · 1.2M views · In your library');
    expect(remoteLine(remoteEntry('a', { progress: started }))).toBe('Chan · 12 min left · 1.2M views');
    expect(remoteLine(remoteEntry('a', { progress: done }))).toBe('Chan · 3 days ago · 1.2M views · Watched');
    expect(remoteLine(remoteEntry('a', { uploader: null, published_at: null, view_count: null }))).toBe('');
    expect(remoteLine(liveEntry('l'))).toBe('Chan');
  });

  it('names a card by title, channel and state', () => {
    expect(remoteLabel(liveEntry('l'))).toBe('Night market walk, Chan, live, 12,400 watching');
    expect(remoteLabel(liveEntry('l', { title: 'Night market', view_count: null }))).toBe('Night market, Chan, live');
    const at = new Date(2026, 8, 29, 20, 30).toISOString();
    expect(remoteLabel(upcomingEntry('u', at))).toBe(`Premiere tonight, Chan, upcoming at ${new Date(at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`);
    expect(remoteLabel(upcomingEntry('u', null))).toBe('Premiere tonight, Chan, upcoming');
    expect(remoteLabel(remoteEntry('a', { progress: started }))).toBe('Harbor walk at dawn, Chan, in progress, 12 minutes left');
    expect(remoteLabel(remoteEntry('a', { progress: done }))).toBe('Harbor walk at dawn, Chan, watched');
    expect(remoteLabel(remoteEntry('a', { saved_item_id: 'i1' }))).toBe('Harbor walk at dawn, Chan, in your library, unwatched');
    expect(remoteLabel(remoteEntry('a'))).toBe('Harbor walk at dawn, Chan');
    expect(remoteLabel(liveEntry('l'), true)).toBe('Night market walk, Chan, ended');
    expect(remoteLabel(remoteEntry('a', { title: null, uploader: null }))).toBe('Untitled video');
  });
});
