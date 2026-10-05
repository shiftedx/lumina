/**
 * The Library's lenses and the Back rule for a title page.
 * routes.ts and LuminaApp and the title, album and artist pages all use it.
 */
import type { TitleCategory, TitleType } from '../../types';

/** Library lenses with a URL of their own, in tab order; All is `/library` itself. */
export const LIBRARY_LENSES = ['movies', 'shows', 'anime', 'music', 'youtube', 'recordings', 'deleted'] as const;
export type LibraryLens = (typeof LIBRARY_LENSES)[number];

export const LENS_LABELS: Readonly<Record<LibraryLens, string>> = {
  movies: 'Movies', shows: 'Shows', anime: 'Anime', music: 'Music', youtube: 'YouTube', recordings: 'Recordings', deleted: 'Deleted',
};

/**
 * Back's lens for a deep-linked title page; null = All. Seasons and episodes pass their own
 * category, which is always their series'.
 */
export function titleLens(type: TitleType | null, category?: TitleCategory | null): LibraryLens | null {
  if (!type) return null;
  if (type === 'album' || type === 'artist') return 'music';
  if (category === 'anime') return 'anime';
  return type === 'movie' || type === 'boxset' ? 'movies' : 'shows';
}
