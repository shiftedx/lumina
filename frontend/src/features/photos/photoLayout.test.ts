import { describe, expect, it } from 'vitest';

import type { Photo } from './photoApi';
import { packPhotoRows, photoDay } from './photoLayout';

function photo(index: number, width = 1600, height = 1000): Photo {
  return {
    id: `photo-${index}`, title: null, filename: `photo-${index}.jpg`, content_type: 'image/jpeg', width, height,
    captured_at: '2026-10-24T12:00:00Z', added_at: '2026-10-24T13:00:00Z', camera: null, bytes: 10,
    favorite: false, visibility: 'private', thumbnail_url: `/api/photos/photo-${index}/image?width=400`,
    preview_url: `/api/photos/photo-${index}/image?width=1200`, original_url: null, deleted_at: null,
    album_ids: [], person_ids: [], can_edit: true,
  };
}

describe('photo timeline layout', () => {
  it('keeps source order and justifies complete rows without stretching the last row', () => {
    const input = [photo(0, 1600, 900), photo(1, 900, 1600), photo(2, 1000, 1000), photo(3, 2400, 800)];
    const rows = packPhotoRows(input, 1000, 240, 8);
    expect(rows.flatMap((row) => row.photos.map((item) => item.id))).toEqual(input.map((item) => item.id));
    for (const row of rows.slice(0, -1)) expect(Math.round(row.widths.reduce((sum, width) => sum + width, 0) + (row.widths.length - 1) * 8)).toBe(1000);
    expect(rows.at(-1)?.height).toBeLessThanOrEqual(240);
  });

  it('bounds corrupt dimensions and packs 100,000 records in a single linear pass', () => {
    const input = Array.from({ length: 100_000 }, (_, index) => photo(index, index % 17 === 0 ? 0 : 800 + index % 1200, index % 23 === 0 ? 0 : 600 + index % 900));
    const started = performance.now();
    const rows = packPhotoRows(input, 1440, 260, 8);
    expect(performance.now() - started).toBeLessThan(2_000);
    expect(rows.flatMap((row) => row.photos)).toHaveLength(100_000);
    expect(rows.every((row) => Number.isFinite(row.height) && row.widths.every(Number.isFinite))).toBe(true);
  });

  it('uses capture date, then added date, with an honest unknown fallback', () => {
    expect(photoDay(photo(1))).toMatch(/October 24, 2026/);
    expect(photoDay({ captured_at: null, added_at: '2026-10-23T12:00:00Z' })).toMatch(/October 23, 2026/);
    const localDayForUtcUpload = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'long', day: 'numeric' }).format(new Date('2026-10-24T01:00:00Z'));
    expect(photoDay({ captured_at: null, added_at: '2026-10-24T01:00:00' })).toBe(localDayForUtcUpload);
    expect(photoDay({ captured_at: null, added_at: 'bad' })).toBe('Unknown date');
  });
});
