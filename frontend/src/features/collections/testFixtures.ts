import { vi } from 'vitest';

import { movieSummary, stillItem } from '../../test/galleryFixtures';
import type { CollectionEntry, HouseholdCollection, LibraryItem, TitleSummary } from '../../types';

export const poster = (id: string): TitleSummary => movieSummary(id, { name: `Title ${id}` });

export function collection(patch: Partial<HouseholdCollection> = {}): HouseholdCollection {
  const titles = patch.titles ?? [];
  return { id: 'c1', owner_user_id: 'me', name: 'Rainy Sundays', visibility: 'private', revision: 1, item_count: titles.length, items: [], entries: [], created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z', ...patch };
}

/** An in-memory stand-in for the page's `api` prop (the same shape as HouseholdCollectionsPanel's). */
export function fakeCollectionApi(list: HouseholdCollection[], options: { failFirstList?: boolean } = {}) {
  let rows = [...list];
  let failures = options.failFirstList ? 1 : 0;
  return {
    list: vi.fn(async () => { if (failures) { failures -= 1; throw new Error('offline'); } return rows; }),
    get: vi.fn(async (id: string) => { const found = rows.find((row) => row.id === id); if (!found) throw new Error('missing'); return found; }),
    create: vi.fn(async (body: { name: string; visibility: HouseholdCollection['visibility'] }) => { const made = collection({ id: `new-${rows.length}`, name: body.name, visibility: body.visibility }); rows = [...rows, made]; return made; }),
    rename: vi.fn(), visibility: vi.fn(), remove: vi.fn(), addItem: vi.fn(),
  } as never;
}

export function libraryItem(id: string): LibraryItem {
  return stillItem(id);
}

export function entry(id: string, title: string): CollectionEntry {
  return { id, position: 0, availability: 'available', ref: { kind: 'library', library_item_id: id, provider: null, remote_id: null, url: null }, title, uploader: null, artwork_url: null, duration: 60 };
}

export function fakeDetailApi() {
  return { addRemote: vi.fn(), removeEntry: vi.fn(), moveEntry: vi.fn(), createBatch: vi.fn() } as never;
}
