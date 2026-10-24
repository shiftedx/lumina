import { parseServerTime } from '../../utils';
import type { Photo } from './photoApi';

export type PhotoRow = { key: string; photos: Photo[]; height: number; widths: number[]; last: boolean };

const ratio = (photo: Pick<Photo, 'width' | 'height'>) => {
  if (!Number.isFinite(photo.width) || !Number.isFinite(photo.height) || photo.width <= 0 || photo.height <= 0) return 4 / 3;
  return Math.min(6, Math.max(0.2, photo.width / photo.height));
};

/** Packs source-ordered photos into justified rows. The last row keeps the target height instead of stretching. */
export function packPhotoRows(photos: readonly Photo[], containerWidth: number, targetHeight: number, gap: number): PhotoRow[] {
  if (!photos.length || containerWidth <= 0) return [];
  const rows: PhotoRow[] = [];
  let pending: Photo[] = [];
  let aspect = 0;
  const push = (last: boolean) => {
    if (!pending.length) return;
    const available = Math.max(1, containerWidth - gap * (pending.length - 1));
    const natural = available / aspect;
    const height = last ? Math.min(targetHeight, natural) : Math.max(targetHeight * 0.68, Math.min(targetHeight * 1.32, natural));
    const widths = pending.map((photo) => ratio(photo) * height);
    if (!last) widths[widths.length - 1] += available - widths.reduce((sum, width) => sum + width, 0);
    rows.push({ key: pending.map((photo) => photo.id).join(':'), photos: pending, height, widths, last });
    pending = [];
    aspect = 0;
  };
  for (const photo of photos) {
    pending.push(photo);
    aspect += ratio(photo);
    const projected = (containerWidth - gap * (pending.length - 1)) / aspect;
    if (projected <= targetHeight) push(false);
  }
  push(true);
  return rows;
}

export function photoDay(photo: Pick<Photo, 'captured_at' | 'added_at'>): string {
  const value = photo.captured_at || photo.added_at;
  const date = photo.captured_at ? new Date(value) : parseServerTime(value);
  return Number.isNaN(date.valueOf()) ? 'Unknown date' : new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'long', day: 'numeric' }).format(date);
}
