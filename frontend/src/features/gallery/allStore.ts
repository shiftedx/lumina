/**
 * The All landing's model and session store: which chapters show and what
 * they ask for, their column maths, the spotlight's picks and kickers, the masthead's totals, and the one saved
 * landing that Back restores.
 */
import { type RefObject, useLayoutEffect, useState } from 'react';

import { listLibrary, listTitles } from '../../api';
import type { LibraryItem, LibrarySections, TitleCategory, TitleSummary } from '../../types';

export type ChapterId = 'movies' | 'shows' | 'anime' | 'music' | 'youtube' | 'recordings';
export type ChapterShape = 'poster' | 'square' | 'still';
export type Chapter = { id: ChapterId; heading: string; noun: readonly [string, string]; shape: ChapterShape; count: (sections: LibrarySections) => number };

/** One chapter per category, in tab order; Music counts albums. */
export const CHAPTERS: readonly Chapter[] = [
  { id: 'movies', heading: 'Movies', noun: ['movie', 'movies'], shape: 'poster', count: (sections) => sections.movies },
  { id: 'shows', heading: 'Shows', noun: ['show', 'shows'], shape: 'poster', count: (sections) => sections.shows },
  { id: 'anime', heading: 'Anime', noun: ['anime', 'anime'], shape: 'poster', count: (sections) => sections.anime },
  { id: 'music', heading: 'Music', noun: ['album', 'albums'], shape: 'square', count: (sections) => sections.albums },
  { id: 'youtube', heading: 'YouTube', noun: ['video', 'videos'], shape: 'still', count: (sections) => sections.youtube },
  { id: 'recordings', heading: 'Recordings', noun: ['recording', 'recordings'], shape: 'still', count: (sections) => sections.recordings },
];
/** What a chapter's See all and error copy call its contents. */
export const CHAPTER_WORD: Readonly<Record<ChapterId, string>> = { movies: 'movies', shows: 'shows', anime: 'anime', music: 'music', youtube: 'videos', recordings: 'recordings' };

export const countLabel = (count: number, [one, many]: readonly [string, string]): string => `${count.toLocaleString()} ${count === 1 ? one : many}`;

/** The masthead kicker: the non-zero sections in tab order. */
export const sectionsKicker = (sections: LibrarySections): string =>
  CHAPTERS.filter((chapter) => chapter.count(sections) > 0).map((chapter) => countLabel(chapter.count(sections), chapter.noun)).join(' · ');

export const visibleChapters = (sections: LibrarySections): Chapter[] => CHAPTERS.filter((chapter) => chapter.count(sections) > 0);

export const MAX_SLICE = 40;

/** Posters and covers: the wall's formula (F-7.2), 200px columns at 10-foot; stills 280px (320px). Phone 3 × 2 or 2 × 2. */
export function chapterColumns(shape: ChapterShape, width: number, gap: number, phone: boolean, tenFoot: boolean): number {
  if (shape === 'still') return phone ? 2 : Math.max(2, Math.floor((width + gap) / ((tenFoot ? 320 : 280) + gap)));
  return phone ? 3 : Math.max(3, Math.floor((width + gap) / ((tenFoot ? 200 : 168) + gap)));
}

/** Two rows' worth, clamped to 40. */
export const chapterLimit = (columns: number): number => Math.min(MAX_SLICE, 2 * columns);

/** Grid gaps (F-1.3): 24px at ≥ 1024, 16px at 600–1023, 8px on phone. */
export const gridGap = (viewport: number): number => (viewport >= 1024 ? 24 : viewport >= 600 ? 16 : 8);

/** An element's content width as state, following resizes; the window's width where it has none yet (jsdom). */
export function useElementWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = () => setWidth(element.clientWidth || window.innerWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

export type ChapterSlice =
  | { kind: 'titles'; items: TitleSummary[]; limit: number; fresh: boolean }
  | { kind: 'items'; items: LibraryItem[]; limit: number; fresh: boolean };

/** A chapter's slice, newest first: titles by category or albums, else Library items by kind. */
export async function fetchChapter(id: ChapterId, limit: number, signal?: AbortSignal): Promise<ChapterSlice> {
  if (id === 'youtube' || id === 'recordings') {
    const page = await listLibrary({ kind: id === 'youtube' ? 'video' : 'recording', status: 'available', sort: 'recent', limit }, { signal });
    return { kind: 'items', items: page.items, limit, fresh: true };
  }
  const page = await listTitles(id === 'music' ? { type: 'album', sort: 'created', limit } : { category: id, sort: 'created', limit }, { signal });
  return { kind: 'titles', items: page.items, limit, fresh: true };
}

export const SPOTLIGHT_POOL = 12;
export const SPOTLIGHT_SIZE = 3;

/** The first three with a backdrop, in arrival order, then the next newest without one. */
export function spotlightPicks(items: TitleSummary[]): TitleSummary[] {
  return [...items.filter((title) => title.backdrop).slice(0, SPOTLIGHT_SIZE), ...items.filter((title) => !title.backdrop)].slice(0, SPOTLIGHT_SIZE);
}

/** GET /api/titles?sort=created&limit=12: movies and series across every category, newest arrival first. */
export const fetchSpotlight = (signal?: AbortSignal): Promise<TitleSummary[]> =>
  listTitles({ sort: 'created', limit: SPOTLIGHT_POOL }, { signal }).then((page) => spotlightPicks(page.items));

const CATEGORY_NOUN: Readonly<Record<TitleCategory, string>> = { movies: 'Movie', shows: 'Show', anime: 'Anime' };

/** "Movie · 2024 · Sci-Fi": the only place a category is named on art. */
export function spotlightKicker(title: TitleSummary): string {
  const noun = title.category ? CATEGORY_NOUN[title.category] : title.type === 'movie' ? 'Movie' : 'Show';
  return [noun, title.year ? String(title.year) : null, title.genres[0] ?? null].filter(Boolean).join(' · ');
}

export type AllStore = {
  spotlight: TitleSummary[] | null;
  slices: Partial<Record<ChapterId, ChapterSlice>>;
  scrollY: number;
  /** The tile or See all that opened what the member left for, and its chapter (null: the spotlight). */
  focus: { key: string; chapter: ChapterId | null } | null;
};

// one landing per tab session; a per-member map if members ever switch without a reload.
let saved: AllStore | null = null;

export function saveAllStore(store: AllStore): void {
  saved = store;
}

/** The saved landing, every slice marked stale so each refetches once it is in view. */
export function restoreAllStore(): AllStore | null {
  if (!saved) return null;
  const slices: AllStore['slices'] = {};
  for (const [id, slice] of Object.entries(saved.slices) as Array<[ChapterId, ChapterSlice]>) slices[id] = { ...slice, fresh: false };
  return { ...saved, slices };
}

export function forgetAllStore(): void {
  saved = null;
}
