import { describe, expect, it } from 'vitest';

import { LENS_LABELS, LIBRARY_LENSES, titleLens } from './libraryLens';

describe('libraryLens', () => {
  it('derives the Back lens of a deep-linked title page from its type and category', () => {
    expect(titleLens('movie', 'movies')).toBe('movies');
    expect(titleLens('boxset', null)).toBe('movies');
    expect(titleLens('movie', 'anime')).toBe('anime');
    expect(titleLens('series', 'shows')).toBe('shows');
    expect(titleLens('series', 'anime')).toBe('anime');
    expect(titleLens('episode', 'anime')).toBe('anime');
    expect(titleLens('season')).toBe('shows');
    expect(titleLens('album', null)).toBe('music');
    expect(titleLens('artist')).toBe('music');
    expect(titleLens(null)).toBeNull();
  });

  it('labels every lens, in tab order', () => {
    expect(LIBRARY_LENSES.map((lens) => LENS_LABELS[lens])).toEqual(['Movies', 'Shows', 'Anime', 'Music', 'YouTube', 'Recordings', 'Deleted']);
  });
});
