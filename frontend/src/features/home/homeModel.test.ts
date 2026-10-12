import { describe, expect, it } from 'vitest';

import { episodeSummary, movieSummary, seriesSummary, stillItem, titleArt, titleDetail } from '../../test/galleryFixtures';
import type { PlaybackProgress, TitleSummary } from '../../types';
import { captionLabel, entryPercent, greeting, HERO_CONTINUE, HERO_SLIDES, heroAnchor, heroArt, heroMeta, heroSlides, pickHero, timeLeft, titleCaption } from './homeModel';

const AT = '2026-09-28T20:00:00Z';
const entry = (title: TitleSummary | null, patch: Partial<PlaybackProgress> = {}): PlaybackProgress => ({
  id: `pb-${title?.id ?? 'video'}`, user_id: 'member-1', item_id: `item-${title?.id ?? 'video'}`, position_seconds: 1200, duration_seconds: 2640, completed: false,
  last_watched_at: AT, created_at: AT, updated_at: AT, item: stillItem(`item-${title?.id ?? 'video'}`), title, ...patch,
});

describe('pickHero', () => {
  it('anchors on the first Continue entry with a title, skipping remote and YouTube entries', () => {
    const episode = episodeSummary(2, 4);
    const pick = pickHero([entry(null), entry(episode)], null);
    expect(pick).toEqual({ kind: 'continue', entry: expect.objectContaining({ id: 'pb-ep-2-4' }), title: episode });
    expect(heroAnchor(episode)).toBe('series-1');
    expect(heroAnchor(movieSummary('m'))).toBe('m');
  });

  it('else takes the newest with a backdrop among the first 10, else the newest; else nothing', () => {
    const plain = (id: string) => movieSummary(id, { backdrop: null });
    const newest = [plain('n0'), plain('n1'), movieSummary('n2'), plain('n3')];
    expect(pickHero([entry(null)], newest)).toEqual({ kind: 'newest', title: newest[2] });
    const late = [...Array.from({ length: 10 }, (_, index) => plain(`p${index}`)), movieSummary('p10')];
    expect(pickHero([], late)).toEqual({ kind: 'newest', title: late[0] });
    expect(pickHero([], [])).toBeNull();
    expect(pickHero([], null)).toBeNull();
  });
});

describe('heroSlides', () => {
  it('leads with the Continue titles (capped), then recommendations with Because-you-watched reasons, one per anchor', () => {
    const resume = Array.from({ length: HERO_CONTINUE + 2 }, (_, index) => entry(movieSummary(`c${index}`)));
    const recs = [movieSummary('c0'), movieSummary('r1'), movieSummary('r2')];
    const because = [movieSummary('r1'), movieSummary('b1'), movieSummary('b2')];
    const rows = [
      { id: 'b', kind: 'because_you_watched' as const, title: 'Because you watched Harbor Lights', items: because },
      { id: 'r', kind: 'recommended' as const, title: 'Recommended', items: recs },
    ];
    const slides = heroSlides([entry(null), ...resume], rows, null);
    expect(slides).toHaveLength(HERO_SLIDES);
    expect(slides.slice(0, HERO_CONTINUE).every((pick) => pick.kind === 'continue')).toBe(true);
    expect(slides.slice(HERO_CONTINUE)).toEqual([
      { kind: 'recommended', title: recs[1], reason: null },
      { kind: 'recommended', title: recs[2], reason: null },
      { kind: 'recommended', title: because[1], reason: 'Because you watched Harbor Lights' },
    ]);
  });

  it('without Continue titles, starts from the newest pick', () => {
    const newest = [movieSummary('n0')];
    expect(heroSlides([], null, newest)).toEqual([{ kind: 'newest', title: newest[0] }]);
    expect(heroSlides([], [{ id: 'r', kind: 'recommended', title: 'R', items: [movieSummary('n0'), movieSummary('r1')] }], newest).map((pick) => pick.title.id)).toEqual(['n0', 'r1']);
    expect(heroSlides([], null, null)).toEqual([]);
  });
});

describe('hero art and meta', () => {
  it('falls back from the anchor backdrop to the episode still, the poster, then the colour field', () => {
    const episode = episodeSummary(2, 4);
    const series = seriesSummary('series-1');
    const detail = titleDetail(series);
    expect(heroArt(episode, detail)).toEqual({ kind: 'backdrop', art: detail.backdrop });
    expect(heroArt(episode, titleDetail(series, { backdrop: null }))).toEqual({ kind: 'backdrop', art: episode.poster });
    const movie = movieSummary('m', { backdrop: null });
    expect(heroArt(movie, titleDetail(movie))).toEqual({ kind: 'poster', art: movie.poster });
    expect(heroArt(movie, titleDetail(movie, { poster: null }))).toEqual({ kind: 'field' });
  });

  it('composes portrait art as a poster: a tall backdrop, or a show poster standing in for an episode still', () => {
    const tall = titleArt('series-1', 'Backdrop', { width: 1000, height: 1500 });
    const series = seriesSummary('series-1');
    const episode = episodeSummary(2, 4, { poster: titleArt('series-1', 'Primary', { width: 680, height: 1000 }) });
    expect(heroArt(episode, titleDetail(series, { backdrop: tall }))).toEqual({ kind: 'poster', art: episode.poster });
    const movie = movieSummary('m', { backdrop: tall });
    expect(heroArt(movie, null)).toEqual({ kind: 'field' });
    expect(heroArt(movie, titleDetail(movie, { poster: null }))).toEqual({ kind: 'poster', art: tall });
  });

  it('paints from the summary before the detail: its own backdrop, never an episode still', () => {
    const movie = movieSummary('m');
    expect(heroArt(movie, null)).toEqual({ kind: 'backdrop', art: movie.backdrop });
    expect(heroArt(episodeSummary(2, 4), null)).toEqual({ kind: 'field' });
    expect(heroArt(movieSummary('m', { backdrop: null, poster: titleArt('m', 'Primary') }), null)).toEqual({ kind: 'field' });
  });

  it('writes the meta line for Continue and for the newest', () => {
    expect(heroMeta({ kind: 'continue', entry: entry(episodeSummary(2, 4)), title: episodeSummary(2, 4) })).toBe('S2 · E4 · Episode 4 · 24 min left');
    const movie = movieSummary('m', { runtime_seconds: 6720 });
    expect(heroMeta({ kind: 'continue', entry: entry(movie, { position_seconds: 1200, duration_seconds: 6720 }), title: movie })).toBe('1h 32m left');
    expect(heroMeta({ kind: 'newest', title: movieSummary('m', { year: 2019, runtime_seconds: 6720, genres: ['Adventure', 'Family', 'Drama', 'War'] }) })).toBe('2019 · 1h 52m · Adventure · Family · Drama');
  });
});

describe('Home card text', () => {
  it('counts time left in minutes, then hours and minutes', () => {
    expect(timeLeft(1200, 2640)).toBe('24 min left');
    expect(timeLeft(0, 2640)).toBeNull();
    expect(timeLeft(2640, 2640)).toBeNull();
    expect(timeLeft(10, null)).toBeNull();
    expect(timeLeft(600, 7200)).toBe('1h 50m left');
    expect(timeLeft(0.5, 3600.5)).toBe('1h left');
  });

  it('captions a 16:9 title card with the series, then the code and time left', () => {
    const episode = episodeSummary(2, 4);
    expect(titleCaption(episode, '24 min left')).toEqual({ name: 'Harbor Lights', line: 'S2 · E4 · 24 min left' });
    expect(titleCaption(episodeSummary(2, 5))).toEqual({ name: 'Harbor Lights', line: 'S2 · E5' });
    expect(titleCaption(movieSummary('m'), '1h 12m left')).toEqual({ name: 'Northern Lantern', line: '1h 12m left' });
    expect(captionLabel({ name: 'Harbor Lights', line: 'S2 · E5' })).toBe('Harbor Lights, S2 · E5');
    expect(captionLabel({ name: 'Northern Lantern', line: '' })).toBe('Northern Lantern');
  });

  it('draws progress from the entry, never for a finished or unstarted one', () => {
    expect(entryPercent(entry(movieSummary('m'), { position_seconds: 1320, duration_seconds: 2640 }))).toBe(50);
    expect(entryPercent(entry(movieSummary('m'), { completed: true }))).toBeNull();
    expect(entryPercent(entry(movieSummary('m'), { position_seconds: 0 }))).toBeNull();
  });

  it('greets by the hour', () => {
    expect(greeting(new Date(2026, 8, 29, 9))).toBe('Good morning');
    expect(greeting(new Date(2026, 8, 29, 13))).toBe('Good afternoon');
    expect(greeting(new Date(2026, 8, 29, 20))).toBe('Good evening');
  });
});
