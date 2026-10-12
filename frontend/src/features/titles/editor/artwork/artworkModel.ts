import type { EditableImageType, ImageOrigin, TitleImageEntry, TitleMetadataDoc, TitleType } from '../../../../types';

export type ArtworkTabDoc = Pick<TitleMetadataDoc, 'title_id' | 'type' | 'locked' | 'images' | 'tmdb_configured'>;
export interface Slot { type: EditableImageType; index: number; key: string; label: string }
export const MAX_BACKDROPS = 5;

export const slotKey = (type: EditableImageType, index: number) => (index === 0 ? `images.${type}` : `images.${type}.${index}`);

/** The entry in a slot, or null when the slot is empty (the server may list an empty slot with a null url). */
export function imageAt(images: readonly TitleImageEntry[], type: EditableImageType, index: number): TitleImageEntry | null {
  const found = images.find((entry) => entry.type === type && entry.index === index);
  return found && found.url !== null ? found : null;
}

export function slotsFor(docType: TitleType, images: readonly TitleImageEntry[]): Slot[] {
  const slot = (type: EditableImageType, index: number, label: string): Slot => ({ type, index, key: slotKey(type, index), label });
  const slots = [slot('Primary', 0, docType === 'episode' ? 'Still' : 'Poster')];
  if (docType === 'movie' || docType === 'series' || docType === 'season') {
    const used = images.filter((entry) => entry.type === 'Backdrop' && entry.url !== null).map((entry) => entry.index).sort((a, b) => a - b);
    const free = [0, 1, 2, 3, 4].find((index) => !used.includes(index));
    for (const index of free === undefined ? used : [...used, free].sort((a, b) => a - b)) slots.push(slot('Backdrop', index, `Backdrop ${index + 1}`));
  }
  if (docType === 'movie' || docType === 'series') slots.push(slot('Logo', 0, 'Logo'));
  return slots;
}

/** The occupied indices in their new order after moving `index` one place, or null at either end. */
export function moveOrder(occupied: readonly number[], index: number, direction: -1 | 1): number[] | null {
  const position = occupied.indexOf(index);
  const target = position + direction;
  if (position < 0 || target < 0 || target >= occupied.length) return null;
  const next = [...occupied];
  [next[position], next[target]] = [next[target], next[position]];
  return next;
}

const ORIGINS: Record<ImageOrigin, string> = { tmdb: 'From TMDB', upload: 'Uploaded', local: 'From folder', embedded: 'From video file' };
export const originLabel = (entry: TitleImageEntry): string => (entry.origin ? ORIGINS[entry.origin] : '');
