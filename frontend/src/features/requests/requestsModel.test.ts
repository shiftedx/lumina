import { afterEach, describe, expect, it, vi } from 'vitest';

import { anime, animeFilm, movie, quotas, show, showDetail } from './requestsFixtures';
import {
  buildRequest, canAskFor, catalogId, countdown, needsLanguage, optimisticState, quotaLine, rememberedLanguage, rememberLanguage, routingLine,
  seasonLabel, seasonOf, seasonsPayload, shiftSeason, statusBadge, timeline, unrequestableCopy,
} from './requestsModel';

describe('status ribbons', () => {
  it('maps every state to its copy, with the download percent', () => {
    expect(statusBadge({ state: 'none' })).toBeNull();
    expect(statusBadge({ state: 'pending' })).toEqual({ label: 'Requested', state: 'pending', percent: null });
    expect(statusBadge({ state: 'processing', progress: 0.424 })).toEqual({ label: 'Downloading 42%', state: 'processing', percent: 42 });
    expect(statusBadge({ state: 'processing', progress: 7 })?.percent).toBe(100);
    expect(statusBadge({ state: 'processing', progress: 1 })?.label).toBe('Downloaded — adding to your vault');
    expect(statusBadge({ state: 'available' })?.label).toBe('In your vault');
    expect(statusBadge({ state: 'failed' })?.label).toBe('Needs attention');
  });

  it('allows a request from nothing, after a decline, or for more seasons', () => {
    expect(canAskFor(movie)).toBe(true);
    expect(canAskFor(show)).toBe(false);
    expect(canAskFor({ status: { state: 'declined' } })).toBe(true);
    expect(canAskFor({ status: { state: 'partially_available' } })).toBe(true);
    expect(canAskFor({ status: { state: 'none' }, requestable: false })).toBe(false);
  });

  it('gives known unrequestable reasons their own line and anything new a calm one', () => {
    expect(unrequestableCopy('unmapped')).toMatch(/match this anime/);
    expect(unrequestableCopy('something_new')).toBe("Can't be requested yet.");
    expect(unrequestableCopy(undefined)).toBe("Can't be requested yet.");
  });
});

describe('the request sheet', () => {
  it('turns the season choice into the payload', () => {
    expect(seasonsPayload('all', [], showDetail.seasons)).toBe('all');
    expect(seasonsPayload('latest', [], showDetail.seasons)).toEqual([2]);
    expect(seasonsPayload('pick', [2, 1, 2], showDetail.seasons)).toEqual([1, 2]);
    expect(seasonsPayload('pick', [], showDetail.seasons)).toBeNull();
    expect(seasonsPayload('latest', [], [])).toBe('all');
  });

  it('asks the language only for anime series, and sends only what the kind takes', () => {
    expect(needsLanguage(anime)).toBe(true);
    expect(needsLanguage(animeFilm)).toBe(false);
    expect(needsLanguage(show)).toBe(false);
    expect(buildRequest(movie, { seasons: 'all', language: 'dub' })).toEqual({ kind: 'movie', tmdb_id: 603 });
    expect(buildRequest(show, { seasons: [1], language: 'dub' })).toEqual({ kind: 'show', tmdb_id: 1399, tvdb_id: 121361, seasons: [1] });
    expect(buildRequest(anime, { seasons: 'all', language: 'sub' })).toEqual({ kind: 'anime', anilist_id: 16498, tmdb_id: 1429, tvdb_id: 267440, seasons: 'all', language: 'sub' });
    expect(buildRequest(animeFilm, { seasons: 'all', language: 'sub' })).toEqual({ kind: 'anime', anilist_id: 199, tmdb_id: 129, media_type: 'movie' });
    expect(catalogId(anime)).toBe(16498);
    expect(catalogId(movie)).toBe(603);
  });

  it('words the quota and where the request goes', () => {
    const [films, series] = quotas;
    expect(quotaLine(series)).toBe('3 of 10 left this week');
    expect(quotaLine(films)).toBe('No request limit');
    expect(quotaLine({ ...series, days: 30 })).toBe('3 of 10 left this month');
    expect(quotaLine({ ...series, can_request: false })).toMatch(/can't request series/);
    expect(routingLine(films, 'movie')).toBe('Will be sent straight to Radarr');
    expect(routingLine(series, 'show')).toBe("Needs an admin's approval");
    expect(optimisticState(films)).toBe('approved');
    expect(optimisticState(series)).toBe('pending');
  });

  describe('remembered language', () => {
    afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });
    it('is kept per member', () => {
      expect(rememberedLanguage('a')).toBeNull();
      rememberLanguage('a', 'dub');
      expect(rememberedLanguage('a')).toBe('dub');
      expect(rememberedLanguage('b')).toBeNull();
    });
    it('never throws when storage is blocked', () => {
      vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
      expect(() => rememberLanguage('a', 'sub')).not.toThrow();
      expect(rememberedLanguage('a')).toBeNull();
    });
  });
});

describe('My requests timeline', () => {
  const states = (status: Parameters<typeof timeline>[0]['status'], progress?: number) => timeline({ status, progress }).map((step) => `${step.label}:${step.state}`);
  it('walks Requested → Approved → Downloading → In your vault', () => {
    expect(states('pending')).toEqual(['Requested:done', 'Approved:current', 'Downloading:upcoming', 'In your vault:upcoming']);
    expect(states('processing', 0.5)).toEqual(['Requested:done', 'Approved:done', 'Downloading 50%:current', 'In your vault:upcoming']);
    expect(states('available')).toEqual(['Requested:done', 'Approved:done', 'Downloading:done', 'In your vault:done']);
    expect(states('partially_available')[2]).toBe('Partly in your vault:current');
    expect(states('declined')).toEqual(['Requested:done', 'Declined:stopped']);
    expect(states('failed').at(-1)).toBe("Couldn't send:stopped");
  });
});

describe('anime seasons and countdowns', () => {
  it('knows the season and steps across years', () => {
    expect(seasonOf(new Date(2026, 9, 4))).toEqual({ season: 'FALL', year: 2026 });
    expect(seasonOf(new Date(2026, 0, 1))).toEqual({ season: 'WINTER', year: 2026 });
    expect(shiftSeason({ season: 'FALL', year: 2026 }, 1)).toEqual({ season: 'WINTER', year: 2027 });
    expect(shiftSeason({ season: 'WINTER', year: 2026 }, -1)).toEqual({ season: 'FALL', year: 2025 });
    expect(seasonLabel({ season: 'SUMMER', year: 2026 })).toBe('Summer 2026');
  });
  it('counts down to the next episode', () => {
    const now = Date.parse('2026-10-04T12:00:00Z');
    expect(countdown('2026-10-06T16:30:00Z', now)).toBe('in 2d 4h');
    expect(countdown('2026-10-04T15:12:00Z', now)).toBe('in 3h 12m');
    expect(countdown('2026-10-04T12:12:00Z', now)).toBe('in 12m');
    expect(countdown('2026-10-04T12:00:30Z', now)).toBe('airing now');
    expect(countdown('2026-10-04T10:00:00Z', now)).toBe('aired');
    expect(countdown('nope', now)).toBe('');
  });
});

describe('review fixes', () => {
  it('words a seasons follow-up', async () => {
    const { seasonsWords } = await import('./requestsModel');
    expect(seasonsWords([4, 2, 3])).toBe('seasons 2–4');
    expect(seasonsWords([2, 5])).toBe('seasons 2, 5');
    expect(seasonsWords([3])).toBe('season 3');
  });
  it('caps a schedule day to the six most popular', async () => {
    const { scheduleEntries } = await import('./AnimePage');
    const entries = Array.from({ length: 9 }, (_, index) => ({ item: { ...anime, key: `anime:${index}`, anime: { ...anime.anime!, popularity: index } } }));
    expect(scheduleEntries(entries, false).map((entry) => entry.item.key)).toEqual(['anime:8', 'anime:7', 'anime:6', 'anime:5', 'anime:4', 'anime:3']);
    expect(scheduleEntries(entries, true)).toHaveLength(9);
  });
});
