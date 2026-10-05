import { describe, expect, it } from 'vitest';

import { episodeSummary, movieSummary, seriesSummary, titleArt, userData } from '../../test/galleryFixtures';
import {
  artSrcSet, backdropWidthFor, CARD_TEXT_DARK, CARD_TEXT_LIGHT, cardColour, cardTextColour, contrastRatio, FALLBACK_PALETTE, fallbackColour,
  HEX_COLOUR, posterLabel, posterMarker, readableAccent, renditionUrl, safeColour, safePreview,
  activeFacetCount, countNoun, DEFAULT_WALL, parseWallQuery, rowCells, rowCount, rowOf, rowStart, serializeWallQuery, verticalNeighbour, visibleRows,
  wallColumns, wallFeatured, wallFiltered, wallKey, wallListQuery, type WallLayout, WALLS, wallQueryFor,
} from './galleryModel';

describe('gallery helpers', () => {
  it('substitutes only an issued width', () => {
    const art = titleArt('movie-1', 'Primary');
    expect(renditionUrl(art, 240)).toBe(art.rendition?.replace('{w}', '240'));
    expect(renditionUrl(art, 300)).toBeNull();
    expect(renditionUrl({ ...art, rendition: null }, 240)).toBeNull();
  });

  it('builds srcset from widths, adding the original only for wide stills', () => {
    const still = titleArt('ep-1-1', 'Primary', { width: 1280 }, true);
    expect(artSrcSet(still, 'still')).toBe(`${renditionUrl(still, 400)} 400w, ${still.url} 1280w`);
    expect(artSrcSet({ ...still, width: 320 }, 'still')).toBe(`${renditionUrl(still, 400)} 400w`);
    expect(artSrcSet({ ...still, rendition: null, widths: [] }, 'still')).toBeNull();
  });

  it('prefetches the 1920 backdrop only on wide viewports', () => {
    expect([backdropWidthFor(1199), backdropWidthFor(1200)]).toEqual([960, 1920]);
  });

  it('accepts only exact colours and webp/jpeg previews', () => {
    expect(safeColour('#a1b2c3')).toBe('#a1b2c3');
    for (const bad of ['#A1B2C3', 'red', '#abc', 'url(x)', null]) expect(safeColour(bad)).toBeNull();
    expect(safePreview('data:image/jpeg;base64,/9j/')).toBe('data:image/jpeg;base64,/9j/');
    for (const bad of ['data:image/svg+xml;base64,PHN2Zz4=', 'https://x/y.webp', 'data:image/webp;base64,a"b']) expect(safePreview(bad)).toBeNull();
  });

  it('picks the palette by FNV-1a of the id', () => {
    expect(fallbackColour('')).toBe(FALLBACK_PALETTE[0x811c9dc5 % 8]);
    expect(fallbackColour('a')).toBe(FALLBACK_PALETTE[0xe40c292c % 8]);
  });

  it('falls back from own colour to the other art to the palette', () => {
    const title = movieSummary();
    expect(cardColour(title, 'poster')).toEqual({ colour: '#2a3b4c', fromPalette: false });
    expect(cardColour({ ...title, poster: null }, 'poster')).toEqual({ colour: '#2a3b4c', fromPalette: false });
    expect(cardColour({ ...title, poster: null, backdrop: null }, 'poster')).toEqual({ colour: fallbackColour(title.id), fromPalette: true });
  });
});

describe('poster states, card text and accent', () => {
  it('marks an unwatched movie, a started one with a 4 % floor, and nothing once watched', () => {
    expect(posterMarker(movieSummary())).toEqual({ kind: 'unwatched' });
    expect(posterMarker(movieSummary('m', { user_data: userData({ position_seconds: 60, duration_seconds: 6000 }) }))).toEqual({ kind: 'progress', percent: 4 });
    expect(posterMarker(movieSummary('m', { user_data: userData({ position_seconds: 3000, duration_seconds: 6000 }) }))).toEqual({ kind: 'progress', percent: 50 });
    expect(posterMarker(movieSummary('m', { user_data: userData({ played: true, position_seconds: 3000 }) }))).toEqual({ kind: 'none' });
    expect(posterMarker(episodeSummary(1, 2))).toEqual({ kind: 'unwatched' });
  });

  it('counts unwatched episodes on a series and leaves a finished or unknown one clean', () => {
    expect(posterMarker(seriesSummary())).toEqual({ kind: 'count', count: 4 });
    expect(posterMarker(seriesSummary('s', { user_data: userData({ unplayed_count: 0 }) }))).toEqual({ kind: 'none' });
    expect(posterMarker(seriesSummary('s', { user_data: userData({ unplayed_count: null }) }))).toEqual({ kind: 'none' });
  });

  it('names the state in words', () => {
    const dune = { name: 'Dune: Part Two', year: 2024, runtime_seconds: 9960 };
    expect(posterLabel(movieSummary('m', dune))).toBe('Dune: Part Two, 2024, unwatched');
    expect(posterLabel(movieSummary('m', { ...dune, user_data: userData({ position_seconds: 7680 }) }))).toBe('Dune: Part Two, 2024, in progress, 38 minutes left');
    expect(posterLabel(movieSummary('m', { ...dune, runtime_seconds: null, user_data: userData({ position_seconds: 7680 }) }))).toBe('Dune: Part Two, 2024, in progress');
    expect(posterLabel(movieSummary('m', { ...dune, year: null, user_data: userData({ played: true }) }))).toBe('Dune: Part Two, watched');
    expect(posterLabel(seriesSummary('s', { name: 'Night Ferry' }))).toBe('Night Ferry, 4 unwatched episodes');
    expect(posterLabel(seriesSummary('s', { name: 'Night Ferry', user_data: userData({ unplayed_count: 1 }) }))).toBe('Night Ferry, 1 unwatched episode');
    expect(posterLabel(seriesSummary('s', { name: 'Night Ferry', user_data: userData({ unplayed_count: 0 }) }))).toBe('Night Ferry, 2021, watched');
  });

  it('measures WCAG contrast and picks the card text with the higher contrast, light on the palette', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 5);
    expect(cardTextColour('#f0e0c0', true)).toBe(CARD_TEXT_LIGHT);
    expect(cardTextColour('#f0e0c0', false)).toBe(CARD_TEXT_DARK);
    expect(cardTextColour('#202020', false)).toBe(CARD_TEXT_LIGHT);
    expect(cardTextColour('url(x)', false)).toBe(CARD_TEXT_LIGHT);
    for (const colour of FALLBACK_PALETTE) expect(contrastRatio(colour, CARD_TEXT_LIGHT)).toBeGreaterThanOrEqual(7);
  });

  it('shifts an accent until it reads at 3:1 on the paper, and refuses anything but #rrggbb', () => {
    const onLight = readableAccent('#c08a4b', 'light');
    expect(onLight).toMatch(HEX_COLOUR);
    expect(onLight).not.toBe('#c08a4b');
    expect(contrastRatio(onLight, '#f4f1ea')).toBeGreaterThanOrEqual(3);
    expect(readableAccent('#c08a4b', 'dark')).toBe('#c08a4b');
    const onDark = readableAccent('#2a3b4c', 'dark');
    expect(onDark).toMatch(HEX_COLOUR);
    expect(contrastRatio(onDark, '#0f0e0c')).toBeGreaterThanOrEqual(3);
    for (const bad of [null, undefined, 'red', '#ABCDEF', 'url(x)']) expect(readableAccent(bad, 'light')).toBe('var(--g-ink)');
  });
});

describe('wall query and layout', () => {
  it('parses the address into a wall query and writes it back canonically', () => {
    expect(parseWallQuery(undefined)).toEqual(DEFAULT_WALL);
    expect(serializeWallQuery(DEFAULT_WALL)).toBe('');
    const query = parseWallQuery('q=lighthouse+keeper&res=sd&res=4k&genre=Sci-Fi&genre=Drama&to=1999&from=1990&fav=1&progress=1&unwatched=1&sort=name');
    expect(query).toEqual({ sort: 'name', unwatched: true, progress: true, fav: true, genre: ['Sci-Fi', 'Drama'], from: 1990, to: 1999, res: ['4k', 'sd'], q: 'lighthouse keeper' });
    expect(serializeWallQuery(query)).toBe('sort=name&unwatched=1&progress=1&fav=1&genre=Drama&genre=Sci-Fi&from=1990&to=1999&res=4k&res=sd&q=lighthouse+keeper');
    expect(parseWallQuery(serializeWallQuery(query))).toEqual({ ...query, genre: ['Drama', 'Sci-Fi'] });
  });

  it('drops every invalid value silently', () => {
    const eleven = Array.from({ length: 11 }, (_, index) => `genre=G${index}`).join('&');
    const query = parseWallQuery(`sort=bogus&unwatched=yes&genre=${'x'.repeat(65)}&genre=&from=1869&to=2101&res=8k&q=${'a'.repeat(201)}`);
    expect(query).toEqual(DEFAULT_WALL);
    expect(parseWallQuery(eleven).genre).toHaveLength(10);
    expect(parseWallQuery('genre=Drama&genre=Drama').genre).toEqual(['Drama']);
    expect(parseWallQuery('from=19x0&to=1990.5').from).toBeNull();
  });

  it('counts facets, knows when the wall is filtered, and maps to the listing request', () => {
    const query = parseWallQuery('fav=1&genre=Drama&genre=Sci-Fi&from=1990&res=4k');
    expect(activeFacetCount(query)).toBe(3);
    expect(activeFacetCount(parseWallQuery('to=1990'))).toBe(1);
    expect(wallFiltered(parseWallQuery('progress=1'))).toBe(true);
    expect(wallFiltered(parseWallQuery('sort=name&q=boat'))).toBe(false);
    expect(wallListQuery('shows', query)).toEqual({
      category: 'shows', sort: 'created', unwatched: false, in_progress: false, favorites: true, genre: ['Drama', 'Sci-Fi'], year_from: 1990, year_to: null, resolution: ['4k'],
    });
    expect(wallListQuery('movies', DEFAULT_WALL)).not.toHaveProperty('type');
    expect(WALLS.movies.facets).toEqual({ category: 'movies' });
    expect(WALLS.shows.facets).toEqual({ category: 'shows' });
    expect(wallKey('movies', parseWallQuery('sort=name&q=boat'))).toBe(wallKey('movies', parseWallQuery('sort=name')));
    expect(wallKey('movies', DEFAULT_WALL)).not.toBe(wallKey('shows', DEFAULT_WALL));
  });

  it('fits 168 px posters with a minimum of three columns, fixed three on phone', () => {
    expect(wallColumns(1200, 24, false)).toBe(6);
    expect(wallColumns(1400, 24, false)).toBe(7);
    expect(wallColumns(500, 16, false)).toBe(3);
    expect(wallColumns(0, 24, false)).toBe(3);
    expect(wallColumns(2000, 8, true)).toBe(3);
  });

  it('features only the default recently-added wall at six or more columns, never on phone', () => {
    expect(wallFeatured(DEFAULT_WALL, 6, false)).toBe(true);
    expect(wallFeatured(DEFAULT_WALL, 5, false)).toBe(false);
    expect(wallFeatured(DEFAULT_WALL, 7, true)).toBe(false);
    for (const wall of ['sort=name', 'unwatched=1', 'genre=Drama', 'q=boat']) expect(wallFeatured(parseWallQuery(wall), 7, false)).toBe(false);
  });

  it('maps indices to rows in closed form, with columns − 2 titles on every sixth row', () => {
    const plain: WallLayout = { columns: 6, featured: false };
    expect([rowStart(0, plain), rowStart(3, plain), rowOf(17, plain), rowOf(18, plain), rowCount(1735, plain)]).toEqual([0, 18, 2, 3, 290]);
    const featured: WallLayout = { columns: 7, featured: true };
    expect([0, 1, 2, 6, 7, 12].map((row) => rowStart(row, featured))).toEqual([0, 5, 12, 40, 45, 80]);
    for (let index = 0; index < 400; index += 1) {
      const row = rowOf(index, featured);
      expect(rowStart(row, featured)).toBeLessThanOrEqual(index);
      expect(rowStart(row + 1, featured)).toBeGreaterThan(index);
    }
    expect(rowCount(0, featured)).toBe(0);
    expect(rowCount(5, featured)).toBe(1);
    expect(rowCount(6, featured)).toBe(2);
  });

  it('places the tile first on even feature rows and last on odd ones, three columns wide', () => {
    const featured: WallLayout = { columns: 7, featured: true };
    expect(rowCells(0, featured, 1000)).toEqual([
      { index: 0, column: 0, span: 3 }, { index: 1, column: 3, span: 1 }, { index: 2, column: 4, span: 1 }, { index: 3, column: 5, span: 1 }, { index: 4, column: 6, span: 1 },
    ]);
    expect(rowCells(6, featured, 1000)).toEqual([
      { index: 40, column: 0, span: 1 }, { index: 41, column: 1, span: 1 }, { index: 42, column: 2, span: 1 }, { index: 43, column: 3, span: 1 }, { index: 44, column: 4, span: 3 },
    ]);
    expect(rowCells(1, featured, 8)).toEqual([{ index: 5, column: 0, span: 1 }, { index: 6, column: 1, span: 1 }, { index: 7, column: 2, span: 1 }]);
  });

  it('moves up and down to the cell under the same horizontal centre', () => {
    const featured: WallLayout = { columns: 7, featured: true };
    expect(verticalNeighbour(1, 1, featured, 1000)).toBe(8);
    expect(verticalNeighbour(0, 1, featured, 1000)).toBe(6);
    expect(verticalNeighbour(8, -1, featured, 1000)).toBe(1);
    expect(verticalNeighbour(5, -1, featured, 1000)).toBe(0);
    expect(verticalNeighbour(2, -1, featured, 1000)).toBeNull();
    expect(verticalNeighbour(11, 1, featured, 12)).toBeNull();
  });

  it('finds the rows in view plus three either side', () => {
    expect(visibleRows(-100, 800, 338, 290)).toEqual({ first: 0, last: 2, from: 0, to: 5 });
    expect(visibleRows(6660, 800, 338, 290)).toEqual({ first: 19, last: 22, from: 16, to: 25 });
    expect(visibleRows(0, 800, 338, 2)).toEqual({ first: 0, last: 1, from: 0, to: 1 });
    expect(visibleRows(0, 800, 338, 0)).toEqual({ first: 0, last: -1, from: 0, to: -1 });
  });
});

describe('library walls', () => {
  it('lists each wall by its own category or type, with that wall’s sorts', () => {
    expect(wallListQuery('anime', DEFAULT_WALL)).toMatchObject({ category: 'anime', sort: 'created' });
    expect(wallListQuery('anime', DEFAULT_WALL)).not.toHaveProperty('type');
    expect(wallListQuery('albums', DEFAULT_WALL)).toMatchObject({ type: 'album', sort: 'created' });
    expect(wallListQuery('artists', wallQueryFor('artists', ''))).toMatchObject({ type: 'artist', sort: 'name' });
    expect(Object.fromEntries(Object.entries(WALLS).map(([wall, def]) => [wall, def.sorts]))).toEqual({
      movies: ['created', 'name', 'year', 'rating'], shows: ['created', 'name', 'year', 'rating'], anime: ['created', 'name', 'year', 'rating'],
      albums: ['created', 'name', 'year'], artists: ['name', 'created'],
    });
    expect(Object.values(WALLS).map((def) => [def.shape, def.chips, def.search, def.featured])).toEqual([
      ['poster', true, 'movies', true], ['poster', true, 'shows', true], ['poster', true, 'anime', true], ['square', false, null, false], ['square', false, null, false],
    ]);
    expect(WALLS.anime.facets).toEqual({ category: 'anime' });
    expect(WALLS.albums.facets).toEqual({ type: 'album' });
    expect(WALLS.artists.facets).toBeNull();
  });

  it('drops what a music wall does not offer from a hand-edited address', () => {
    expect(wallQueryFor('albums', 'sort=rating&unwatched=1&progress=1&fav=1&res=4k&q=boat&genre=Folk&from=1990')).toEqual({ ...DEFAULT_WALL, genre: ['Folk'], from: 1990 });
    expect(wallQueryFor('artists', 'genre=Folk&from=1990&sort=year')).toEqual({ ...DEFAULT_WALL, sort: 'name' });
    expect(wallQueryFor('anime', 'unwatched=1&res=4k&q=boat')).toEqual({ ...DEFAULT_WALL, unwatched: true, res: ['4k'], q: 'boat' });
    expect(wallQueryFor('movies', 'sort=rating')).toEqual({ ...DEFAULT_WALL, sort: 'rating' });
  });

  it('round-trips a wall whose default sort is not recently added', () => {
    const created = { ...wallQueryFor('artists', ''), sort: 'created' as const };
    expect(serializeWallQuery(created, 'name')).toBe('sort=created');
    expect(wallQueryFor('artists', serializeWallQuery(created, 'name')).sort).toBe('created');
    expect(serializeWallQuery(wallQueryFor('artists', ''), 'name')).toBe('');
    expect(wallKey('artists', wallQueryFor('artists', ''))).toBe('artists?');
    expect(wallKey('albums', DEFAULT_WALL)).not.toBe(wallKey('artists', { ...DEFAULT_WALL, sort: 'name' }));
  });

  it('counts with the wall’s noun', () => {
    expect(countNoun(1735, WALLS.movies.noun)).toBe('1,735 titles');
    expect(countNoun(1, WALLS.albums.noun)).toBe('1 album');
    expect(countNoun(0, WALLS.artists.noun)).toBe('0 artists');
  });
});
