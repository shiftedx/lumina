/**
 * The member's play order for an album or an artist: Play, Shuffle and Play all store it
 * before the first track opens, and the player follows it while it holds the playing track, else the album's own
 * order. In memory for this tab only: a reload loses it and album order applies.
 */
import type { AlbumTrack } from '../../types';

export type AlbumQueue = { albumId: string; order: string[]; labels: Record<string, string> };

let current: AlbumQueue | null = null;

/** "5. Song Five", or the bare name when the track has no number: the up-next and Resume wording. */
export const trackLabel = (track: Pick<AlbumTrack, 'number' | 'name'>): string => (track.number ? `${track.number}. ${track.name}` : track.name);

/** Stores the order for an album (album id) or an artist's Play all (artist id). */
export function setAlbumQueue(albumId: string, tracks: readonly AlbumTrack[]): AlbumQueue {
  current = { albumId, order: tracks.map((track) => track.item_id), labels: Object.fromEntries(tracks.map((track) => [track.item_id, trackLabel(track)])) };
  return current;
}

export const albumQueue = (): AlbumQueue | null => current;

export function clearAlbumQueue(): void {
  current = null;
}

// modulo bias is below 1e-6 for any album or artist size; rejection sampling if orders ever reach millions.
function randomBelow(bound: number): number {
  const value = new Uint32Array(1);
  crypto.getRandomValues(value);
  return value[0] % bound;
}

/** A Fisher–Yates shuffle over crypto.getRandomValues; the input is left as it was. */
export function shuffled<T>(items: readonly T[]): T[] {
  const order = [...items];
  for (let index = order.length - 1; index > 0; index -= 1) {
    const other = randomBelow(index + 1);
    [order[index], order[other]] = [order[other], order[index]];
  }
  return order;
}

/** The track after (1) or before (-1) `itemId`: the stored order while it holds the item, else the album's; null at an end. */
export function stepFrom(itemId: string, tracks: readonly AlbumTrack[], direction: 1 | -1): { itemId: string; label: string } | null {
  const stored = current?.order.includes(itemId) ? current : null;
  const order = stored ? stored.order : tracks.map((track) => track.item_id);
  const index = order.indexOf(itemId);
  const next = index < 0 ? undefined : order[index + direction];
  if (!next) return null;
  const track = tracks.find((entry) => entry.item_id === next);
  return { itemId: next, label: stored?.labels[next] ?? (track ? trackLabel(track) : next) };
}
