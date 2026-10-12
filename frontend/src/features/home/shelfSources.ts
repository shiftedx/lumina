/**
 * One loader per fetched Home shelf: each returns the shelf's rows (a group shelf,
 * may return several; an empty source returns none) or throws. A Home visit shares one /api/home/title-rows request
 * between Because you watched and Recommended, and one newest-titles request between the hero and New in your
 * library.
 */
import { getHomeTitleRows, getHouseholdCollection, listHouseholdCollections, listNextUp, listTitles } from '../../api';
import type { AppRoute } from '../../app/routes';
import type { LibraryItem, TitleRowsResponse, TitleSummary } from '../../types';
import type { HomeShelfId, ShelfShape } from './homeShelves';

export const FETCHED_SHELVES = ['next_up', 'new_in_library', 'because_you_watched', 'recommended', 'collections', 'new_anime', 'recent_music'] as const;
export type FetchedShelfId = (typeof FETCHED_SHELVES)[number];
export const isFetchedShelf = (id: HomeShelfId): id is FetchedShelfId => (FETCHED_SHELVES as readonly string[]).includes(id);

/** Because you watched keeps today's three rows and Collections the first four smart collections. */
export const SHELF_LIMIT = 20;
export const BECAUSE_ROWS = 3;
export const SMART_ROWS = 4;

/** One row of a shelf: titles (posters, stills, albums) or Library items (a channel_video smart collection). */
export type ShelfRow = { key: string; heading: string; shape: ShelfShape; titles?: TitleSummary[]; items?: LibraryItem[]; seeAll?: AppRoute | null };

/** The requests one Home visit shares; each Home mount makes a new visit, so a return visit refetches. */
export type HomeVisit = { newest: () => Promise<TitleSummary[]>; titleRows: () => Promise<TitleRowsResponse> };

/** One in-flight or settled request per visit; a failure is forgotten so Try again (or the next caller) retries. */
function shared<T>(load: () => Promise<T>): () => Promise<T> {
  let pending: Promise<T> | null = null;
  return () => {
    if (!pending) {
      const request = load();
      pending = request;
      request.catch(() => { if (pending === request) pending = null; });
    }
    return pending;
  };
}

export function createVisit(): HomeVisit {
  return {
    // Movies and series by Recently added, the wall's default sort.
    newest: shared(() => listTitles({ sort: 'created', limit: SHELF_LIMIT }).then((page) => page.items)),
    titleRows: shared(() => getHomeTitleRows()),
  };
}

const oneRow = (key: string, heading: string, shape: ShelfShape, titles: TitleSummary[], seeAll: AppRoute | null = null): ShelfRow[] =>
  titles.length ? [{ key, heading, shape, titles: titles.slice(0, SHELF_LIMIT), seeAll }] : [];

export async function loadShelf(id: FetchedShelfId, visit: HomeVisit, userId: string): Promise<ShelfRow[]> {
  switch (id) {
    case 'next_up':
      return oneRow(id, 'Next up', 'still', await listNextUp(SHELF_LIMIT));
    case 'new_in_library':
      return oneRow(id, 'New in your library', 'poster', await visit.newest(), { surface: 'library' });
    case 'new_anime':
      return oneRow(id, 'New in Anime', 'poster', (await listTitles({ category: 'anime', sort: 'created', limit: SHELF_LIMIT })).items, { surface: 'library', view: 'anime' });
    case 'recent_music':
      return oneRow(id, 'Recently added music', 'square', (await listTitles({ type: 'album', sort: 'created', limit: SHELF_LIMIT })).items, { surface: 'library', view: 'music' });
    case 'recommended':
      return oneRow(id, 'Recommended for you', 'poster', (await visit.titleRows()).rows.find((row) => row.kind === 'recommended')?.items ?? []);
    case 'because_you_watched':
      return (await visit.titleRows()).rows
        .filter((row) => row.kind === 'because_you_watched' && row.items.length)
        .slice(0, BECAUSE_ROWS)
        .map((row): ShelfRow => ({ key: `${id}:${row.id}`, heading: row.title, shape: 'poster', titles: row.items.slice(0, SHELF_LIMIT) }));
    case 'collections': {
      // Only the member's own smart collections, the first four; one that fails to load is left out.
      const smart = (await listHouseholdCollections()).filter((collection) => collection.rules && collection.owner_user_id === userId).slice(0, SMART_ROWS);
      const loaded = await Promise.allSettled(smart.map((collection) => getHouseholdCollection(collection.id)));
      return loaded.flatMap((result): ShelfRow[] => {
        if (result.status !== 'fulfilled') return [];
        const collection = result.value;
        const key = `${id}:${collection.id}`;
        // A channel_video collection lists Library items (16:9), every other rule lists titles (2:3).
        if (collection.rules?.type === 'channel_video') return collection.items.length ? [{ key, heading: collection.name, shape: 'still', items: collection.items.slice(0, SHELF_LIMIT) }] : [];
        return oneRow(key, collection.name, 'poster', collection.titles ?? []);
      });
    }
  }
}
