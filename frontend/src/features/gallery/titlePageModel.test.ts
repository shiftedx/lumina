import { describe, expect, it } from 'vitest';

import { episodeSummary, movieSummary, seriesSummary, titleDetail, userData } from '../../test/galleryFixtures';
import type { TitleSummary } from '../../types';
import { versionQuality } from '../titles/titleModel';
import { readableAccent } from './galleryModel';
import {
  castOf, episodeBlurb, episodeHeading, episodeLabel, episodeMeta, hasDropCap, metaLine, minutesLeft, pageAccent, playNextScrollLeft, sideBlocks,
  stillPriority, storyVisible, usesLogo,
} from './titlePageModel';

const season = (n: number): TitleSummary => movieSummary(`season-${n}`, { type: 'season', name: n ? `Season ${n}` : 'Specials', index_number: n, parent_id: 'series-1' });
const inProgress = userData({ position_seconds: 960, duration_seconds: 2640 });
const cast = ['Mara Leigh', 'Tom Oduya', 'Kit Halloran', 'Ada Rook'].map((name) => ({ name, type: 'Actor' as const }));
const show = (next: TitleSummary | null) => titleDetail(seriesSummary('series-1', { user_data: userData({ last_watched_at: '2026-09-01T12:00:00', unplayed_count: 4 }) }), {
  children: [season(0), season(1), season(2), season(3)], play_next: next, people: [{ name: 'Ines Varga', type: 'Creator' }, ...cast],
});

describe('versionQuality', () => {
  it('names resolution and HDR without the size', () => {
    expect(versionQuality({ height: 2160, hdr: true })).toBe('4K HDR');
    expect(versionQuality({ height: 1080, hdr: false })).toBe('1080p');
    expect(versionQuality({ label: '4K Dolby Vision HDR', height: 2160, hdr: true })).toBe('4K Dolby Vision HDR');
  });
});

describe('metaLine', () => {
  it('reads year, rating, runtime, up to three genres and the best version for a movie', () => {
    const movie = titleDetail(movieSummary('movie-1', { official_rating: 'PG-13', genres: ['Adventure', 'Family', 'Drama', 'War'] }), {
      versions: [{ item_id: 'a', height: 1080, hdr: false }, { item_id: 'b', height: 2160, hdr: true }],
    });
    expect(metaLine(movie)).toEqual(['2019', 'PG-13', '1h 52m', 'Adventure', 'Family', 'Drama', '4K HDR']);
  });

  it('works on a clicked summary before the detail arrives', () => {
    expect(metaLine(movieSummary())).toEqual(['2019', '1h 52m', 'Adventure']);
  });

  it('counts regular seasons and says where the member is in a show', () => {
    expect(metaLine(show(episodeSummary(2, 4, { user_data: inProgress })))).toEqual(['2021', '3 seasons', 'Drama', 'Continue S2 · E4, 28 min left']);
    expect(metaLine(show(episodeSummary(2, 5)))).toEqual(['2021', '3 seasons', 'Drama', 'Up next S2 · E5']);
    expect(metaLine(show(null))).toEqual(['2021', '3 seasons', 'Drama']);
  });
});

describe('sideBlocks', () => {
  it('gives a movie its director, first three actors and what is in the library', () => {
    const movie = titleDetail(movieSummary(), {
      people: [{ name: 'Ines Varga', type: 'Director' }, ...cast],
      versions: [{ item_id: 'b', height: 1080, hdr: false, file_size: 8 * 1024 ** 3 }, { item_id: 'a', height: 2160, hdr: true, file_size: 58 * 1024 ** 3 }],
    });
    expect(sideBlocks(movie)).toEqual([
      { label: 'Directed by', text: 'Ines Varga' },
      { label: 'Starring', text: 'Mara Leigh · Tom Oduya · Kit Halloran' },
      { label: 'In your library', text: '4K HDR · 1080p · 66 GB' },
    ]);
  });

  it('gives a show its creators, seasons, episodes and best quality', () => {
    expect(sideBlocks(show(null))).toEqual([
      { label: 'Created by', text: 'Ines Varga' },
      { label: 'Starring', text: 'Mara Leigh · Tom Oduya · Kit Halloran' },
      { label: 'In your library', text: '3 seasons · 24 episodes · 4K' },
    ]);
  });

  it('leaves out blocks with nothing to say', () => {
    expect(sideBlocks(titleDetail(movieSummary()))).toEqual([]);
  });
});

describe('headline, lede and accent', () => {
  it('uses the logo only for names longer than 30 characters', () => {
    const logo = titleDetail(movieSummary()).logo;
    expect(usesLogo({ name: 'x'.repeat(31), logo })).toBe(true);
    expect(usesLogo({ name: 'x'.repeat(30), logo })).toBe(false);
    expect(usesLogo({ name: 'x'.repeat(31), logo: null })).toBe(false);
  });

  it('drops a capital only when the overview starts with a letter and runs long enough to wrap past it', () => {
    const long = (start: string) => `${start} ${'x'.repeat(160)}`;
    expect([long('A girl…'), long('Émile returns.'), long('“Carry it,” she said.'), long('1984 again.'), '', null].map((text) => hasDropCap(text))).toEqual([true, true, false, false, false, false]);
    expect(hasDropCap('A'.repeat(159))).toBe(false);
    expect(hasDropCap('A'.repeat(160))).toBe(true);
  });

  it('tints with the backdrop accent, then the poster accent, then ink', () => {
    const movie = movieSummary();
    expect(pageAccent(movie, 'light')).toBe(readableAccent('#c08a4b', 'light'));
    const poster = movie.poster ?? null;
    const backdrop = movie.backdrop ?? null;
    expect(pageAccent({ backdrop: backdrop && { ...backdrop, accent: 'red' }, poster: poster && { ...poster, accent: '#112233' } }, 'dark')).toBe(readableAccent('#112233', 'dark'));
    expect(pageAccent({ poster: null, backdrop: null }, 'dark')).toBe(readableAccent(null, 'dark'));
  });
});

describe('episode cards', () => {
  it('numbers titles like a contents page', () => {
    expect(episodeHeading(episodeSummary(2, 4))).toBe('4. Episode 4');
    expect(episodeHeading(episodeSummary(2, 4, { index_number_end: 5 }))).toBe('4–5. Episode 4');
    expect(episodeHeading(episodeSummary(0, 2))).toBe('Special 2. Episode 2');
    expect(episodeHeading(episodeSummary(2, 4, { index_number: null }))).toBe('Episode 4');
  });

  it('says the running time, or the time left while in progress', () => {
    expect(episodeMeta(episodeSummary(2, 4))).toBe('44 min');
    expect(episodeMeta(episodeSummary(2, 4, { user_data: inProgress }))).toBe('28 min left');
    expect(episodeMeta(episodeSummary(2, 4, { user_data: userData({ played: true, position_seconds: 960 }) }))).toBe('44 min');
    expect(episodeMeta(episodeSummary(2, 4, { runtime_seconds: null }))).toBeNull();
    expect(minutesLeft(episodeSummary(2, 4, { runtime_seconds: null, user_data: userData({ position_seconds: 60 }) }))).toBeNull();
  });

  it('shows an AI summary only on a watched episode (success criterion 9)', () => {
    const teaser = 'The keepers argue about the lamp.';
    expect(episodeBlurb(episodeSummary(2, 4), 'AI summary')).toBe(teaser);
    expect(episodeBlurb(episodeSummary(2, 4, { user_data: inProgress }), 'AI summary')).toBe(teaser);
    expect(episodeBlurb(episodeSummary(2, 4, { user_data: userData({ played: true }) }), 'AI summary')).toBe('AI summary');
    expect(episodeBlurb(episodeSummary(2, 4, { user_data: userData({ played: true }) }), undefined)).toBe(teaser);
    expect(episodeBlurb(episodeSummary(2, 4, { overview: null }), undefined)).toBeNull();
  });

  it('names the card by its visible heading, its code and its state (WCAG 2.5.3)', () => {
    expect(episodeLabel(episodeSummary(2, 4, { user_data: userData({ played: true }) }))).toBe('Play 4. Episode 4, S2 · E4, watched');
    expect(episodeLabel(episodeSummary(2, 4))).toBe('Play 4. Episode 4, S2 · E4, unwatched');
    expect(episodeLabel(episodeSummary(2, 4, { user_data: inProgress }))).toBe('Play 4. Episode 4, S2 · E4, in progress, 28 minutes left');
    expect(episodeLabel(episodeSummary(2, 4, { runtime_seconds: null, user_data: userData({ position_seconds: 2580, duration_seconds: 2640 }) }))).toBe('Play 4. Episode 4, S2 · E4, in progress, 1 minute left');
    expect(episodeLabel(episodeSummary(2, 4, { index_number: null, season_number: null }))).toBe('Play Episode 4, unwatched');
  });

  it('scrolls the next episode to the first full slot and loads the four in view first', () => {
    expect(playNextScrollLeft(3 * 344, 24)).toBe(968);
    expect(playNextScrollLeft(30, 24)).toBe(0);
    expect([2, 3, 6, 7].map((index) => stillPriority(index, 3))).toEqual([3, 2, 2, 3]);
  });

  it('shows the story so far only for a show in progress', () => {
    const next = episodeSummary(2, 4);
    expect(storyVisible(show(next))).toBe(true);
    expect(storyVisible(show(null))).toBe(false);
    expect(storyVisible({ ...show(next), user_data: userData({ last_watched_at: '2026-09-01T12:00:00', unplayed_count: 0 }) })).toBe(false);
    expect(storyVisible({ ...show(next), user_data: userData({ unplayed_count: 4 }) })).toBe(false);
    expect(storyVisible(titleDetail(movieSummary(), { play_next: next }))).toBe(false);
  });
});

describe('credits', () => {
  const credits = [
    { name: 'Ines Varga', type: 'Director' as const },
    { name: 'Wren Hale', type: 'Writer' as const },
    { name: 'Omar Pike', type: 'Writer' as const },
    { name: 'Andre Coutu', role: 'Post Producer', type: 'Producer' as const },
    { name: 'Lio Brand', type: 'Composer' as const },
    { name: 'Mara Leigh', role: 'Keeper', type: 'Actor' as const },
    { name: 'Kit Halloran', role: 'Guest', type: 'GuestStar' as const },
  ];

  it('credits writers, producers and composers in the side column', () => {
    expect(sideBlocks(titleDetail(movieSummary(), { people: credits })).map((block) => [block.label, block.text])).toEqual([
      ['Directed by', 'Ines Varga'],
      ['Written by', 'Wren Hale · Omar Pike'],
      ['Produced by', 'Andre Coutu'],
      ['Music by', 'Lio Brand'],
      ['Starring', 'Mara Leigh'],
    ]);
  });

  it('keeps only performers in the Cast row', () => {
    expect(castOf(credits).map((person) => person.name)).toEqual(['Mara Leigh', 'Kit Halloran']);
  });
});
