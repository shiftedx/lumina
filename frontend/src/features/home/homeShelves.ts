/**
 * Home's shelf catalogue and the member's layout. The catalogue, the default order,
 * SHELF_SIZES and normalizeHomeShelves (the one reader of ui_prefs.home_shelves) are frozen; the
 * editing helpers and announcement text below the marker.
 */

/** Stable shelf ids in the default order. */
export const HOME_SHELF_IDS = [
  'continue', 'live', 'next_up', 'watchlist', 'new_in_library', 'because_you_watched', 'recommended',
  'picked_for_you', 'collections', 'from_follows', 'recently_saved', 'new_anime', 'recent_music',
] as const;
export type HomeShelfId = (typeof HOME_SHELF_IDS)[number];
/** One entry of ui_prefs.home_shelves, in display order. */
export type HomeShelfPref = { id: HomeShelfId; visible: boolean };
/** Card shape: 2:3 PosterCard, 16:9 StillFrame, 1:1 AlbumCard. */
export type ShelfShape = 'poster' | 'still' | 'square';
/** What a shelf's last load found. 'empty' hides the shelf; edit mode then says "Nothing to show right now". */
export type ShelfStatus = 'loading' | 'items' | 'empty' | 'failed';

/** `name`: the heading when it has no variable part, and always the edit-mode row name. */
export const HOME_CATALOGUE: Readonly<Record<HomeShelfId, { name: string; shape: ShelfShape }>> = {
  continue: { name: 'Continue watching', shape: 'still' },
  live: { name: 'Live now', shape: 'still' },
  next_up: { name: 'Next up', shape: 'still' },
  watchlist: { name: 'Your watchlist', shape: 'still' },
  new_in_library: { name: 'New in your library', shape: 'poster' },
  because_you_watched: { name: 'Because you watched…', shape: 'poster' },
  recommended: { name: 'Recommended for you', shape: 'poster' },
  picked_for_you: { name: 'Picked for you', shape: 'still' },
  collections: { name: 'Your smart collections', shape: 'poster' },
  from_follows: { name: 'New from your follows', shape: 'still' },
  recently_saved: { name: 'Recently saved', shape: 'still' },
  new_anime: { name: 'New in Anime', shape: 'poster' },
  recent_music: { name: 'Recently added music', shape: 'square' },
};

export const DEFAULT_HOME_ORDER: readonly HomeShelfId[] = HOME_SHELF_IDS;

/** `sizes` per card shape, from the layout table: phone, TV, tablet, then desktop. */
export const SHELF_SIZES: Readonly<Record<ShelfShape, string>> = {
  poster: '(max-width: 599px) 30vw, (min-width: 1600px) 220px, (max-width: 1023px) 150px, 168px',
  still: '(max-width: 599px) min(72vw, 300px), (min-width: 1600px) 400px, (max-width: 1023px) 280px, 320px',
  square: '(max-width: 599px) 40vw, (min-width: 1600px) 260px, (max-width: 1023px) 180px, 200px',
};

/** A hand-edited or corrupted value never costs more than this many entries. */
export const STORED_SHELVES_CAP = 64;

const isShelfId = (value: unknown): value is HomeShelfId => typeof value === 'string' && (HOME_SHELF_IDS as readonly string[]).includes(value);

/**
 * The member's layout from the raw ui_prefs.home_shelves value: not a list means the default; unknown ids
 * and repeats are dropped; a non-boolean `visible` is true; every catalogue shelf not listed is appended visible, in
 * default order, so new shelves reach members who customised (B3).
 */
export function normalizeHomeShelves(stored: unknown): HomeShelfPref[] {
  const layout: HomeShelfPref[] = [];
  const seen = new Set<HomeShelfId>();
  for (const entry of Array.isArray(stored) ? stored.slice(0, STORED_SHELVES_CAP) : []) {
    const { id, visible } = (typeof entry === 'object' && entry !== null ? entry : {}) as { id?: unknown; visible?: unknown };
    if (!isShelfId(id) || seen.has(id)) continue;
    seen.add(id);
    layout.push({ id, visible: typeof visible === 'boolean' ? visible : true });
  }
  for (const id of DEFAULT_HOME_ORDER) if (!seen.has(id)) layout.push({ id, visible: true });
  return layout;
}

// ---- editing helpers and announcement text (append below) ----

/** `list` with `id` moved to `toIndex` (clamped); the same array when nothing moves. */
export function moveShelf(list: readonly HomeShelfPref[], id: HomeShelfId, toIndex: number): HomeShelfPref[] {
  const from = list.findIndex((shelf) => shelf.id === id);
  const to = Math.min(list.length - 1, Math.max(0, toIndex));
  if (from < 0 || from === to) return list as HomeShelfPref[];
  const next = list.filter((shelf) => shelf.id !== id);
  next.splice(to, 0, list[from]);
  return next;
}

export function setShelfVisible(list: readonly HomeShelfPref[], id: HomeShelfId, visible: boolean): HomeShelfPref[] {
  return list.map((shelf) => (shelf.id === id ? { ...shelf, visible } : shelf));
}

// Edit mode's live-region text. Positions are 1-based.
export const enterText = (count: number): string => `Editing Home. ${count} shelves.`;
export const pickUpText = (name: string, position: number, count: number): string =>
  `${name} picked up, position ${position} of ${count}. Use Up and Down to move, Space to drop, Escape to cancel.`;
export const positionText = (name: string, position: number, count: number): string => `${name}, position ${position} of ${count}.`;
export const dropText = (name: string, position: number, count: number): string => `${name} dropped at position ${position} of ${count}.`;
export const cancelText = (name: string, position: number): string => `Reorder cancelled. ${name} is back at position ${position}.`;
export const visibilityText = (name: string, visible: boolean): string => `${name} ${visible ? 'shown' : 'hidden'}.`;
