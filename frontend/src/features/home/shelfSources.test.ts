import { beforeEach, describe, expect, it, vi } from 'vitest';

import { episodeSummary, movieSummary, stillItem } from '../../test/galleryFixtures';
import type { HouseholdCollection } from '../../types';
import { createVisit, loadShelf } from './shelfSources';

const api = vi.hoisted(() => ({ listNextUp: vi.fn(), listTitles: vi.fn(), getHomeTitleRows: vi.fn(), listHouseholdCollections: vi.fn(), getHouseholdCollection: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const collection = (id: string, patch: Partial<HouseholdCollection> = {}): HouseholdCollection => ({
  id, owner_user_id: 'member-1', name: `Collection ${id}`, visibility: 'private', revision: 0, item_count: 0, items: [], entries: [],
  rules: { type: 'movie', match: 'all', conditions: [], limit: 100 } as HouseholdCollection['rules'], titles: [movieSummary(`${id}-m`)], created_at: '', updated_at: '', ...patch,
});

beforeEach(() => Object.values(api).forEach((mock) => mock.mockReset()));

describe('shelf loaders', () => {
  it('loads Next up and the three title walls at Home\'s page size, with their See all routes', async () => {
    api.listNextUp.mockResolvedValue([episodeSummary(2, 5)]);
    api.listTitles.mockImplementation(async () => ({ items: [movieSummary('m')] }));
    const visit = createVisit();
    expect(await loadShelf('next_up', visit, 'member-1')).toEqual([{ key: 'next_up', heading: 'Next up', shape: 'still', titles: [episodeSummary(2, 5)], seeAll: null }]);
    expect(api.listNextUp).toHaveBeenCalledWith(20);
    expect((await loadShelf('new_anime', visit, 'member-1'))[0]).toMatchObject({ heading: 'New in Anime', shape: 'poster', seeAll: { surface: 'library', view: 'anime' } });
    expect(api.listTitles).toHaveBeenLastCalledWith({ category: 'anime', sort: 'created', limit: 20 });
    expect((await loadShelf('recent_music', visit, 'member-1'))[0]).toMatchObject({ heading: 'Recently added music', shape: 'square', seeAll: { surface: 'library', view: 'music' } });
    expect(api.listTitles).toHaveBeenLastCalledWith({ type: 'album', sort: 'created', limit: 20 });
    expect((await loadShelf('new_in_library', visit, 'member-1'))[0]).toMatchObject({ heading: 'New in your library', seeAll: { surface: 'library' } });
    expect(api.listTitles).toHaveBeenLastCalledWith({ sort: 'created', limit: 20 });
  });

  it('returns no rows for an empty source, and throws when its request fails', async () => {
    api.listNextUp.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error('503'));
    const visit = createVisit();
    expect(await loadShelf('next_up', visit, 'member-1')).toEqual([]);
    await expect(loadShelf('next_up', visit, 'member-1')).rejects.toThrow('503');
  });

  it('shares one title-rows request between Because you watched and Recommended per visit', async () => {
    api.getHomeTitleRows.mockResolvedValue({ rows: [
      { id: 'b1', kind: 'because_you_watched', title: 'Because you watched Arrival', items: [movieSummary('c')] },
      { id: 'b2', kind: 'because_you_watched', title: 'Because you watched Contact', items: [] },
      { id: 'b3', kind: 'because_you_watched', title: 'Because you watched Dune', items: [movieSummary('d')] },
      { id: 'r', kind: 'recommended', title: 'Recommended', items: [movieSummary('r')] },
    ] });
    const visit = createVisit();
    const [because, recommended] = await Promise.all([loadShelf('because_you_watched', visit, 'member-1'), loadShelf('recommended', visit, 'member-1')]);
    expect(because.map((row) => [row.key, row.heading])).toEqual([['because_you_watched:b1', 'Because you watched Arrival'], ['because_you_watched:b3', 'Because you watched Dune']]);
    expect(recommended).toMatchObject([{ key: 'recommended', heading: 'Recommended for you', shape: 'poster' }]);
    expect(api.getHomeTitleRows).toHaveBeenCalledTimes(1);
    await loadShelf('recommended', createVisit(), 'member-1');
    expect(api.getHomeTitleRows).toHaveBeenCalledTimes(2);
  });

  it('shares the newest titles between the hero and New in your library, and retries after a failure', async () => {
    api.listTitles.mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ items: [movieSummary('n')] });
    const visit = createVisit();
    await expect(visit.newest()).rejects.toThrow('offline');
    expect(await visit.newest()).toEqual([movieSummary('n')]);
    await loadShelf('new_in_library', visit, 'member-1');
    expect(api.listTitles).toHaveBeenCalledTimes(2);
  });

  it('lists only the member\'s own smart collections, the first four, one row each; channel videos are stills', async () => {
    api.listHouseholdCollections.mockResolvedValue([
      collection('c1'), collection('manual', { rules: null }), collection('theirs', { owner_user_id: 'member-2' }),
      collection('c2', { rules: { type: 'channel_video', match: 'all', conditions: [], limit: 100 } as HouseholdCollection['rules'] }),
      collection('c3'), collection('c4'), collection('c5'),
    ]);
    api.getHouseholdCollection.mockImplementation(async (id: string) => {
      if (id === 'c3') throw new Error('gone');
      if (id === 'c2') return collection('c2', { rules: { type: 'channel_video' } as HouseholdCollection['rules'], items: [stillItem('v1')], titles: [] });
      if (id === 'c4') return collection('c4', { titles: [] });
      return collection(id);
    });
    const rows = await loadShelf('collections', createVisit(), 'member-1');
    expect(api.getHouseholdCollection.mock.calls.map(([id]) => id)).toEqual(['c1', 'c2', 'c3', 'c4']);
    expect(rows.map((row) => [row.key, row.shape, (row.titles ?? row.items ?? []).length])).toEqual([['collections:c1', 'poster', 1], ['collections:c2', 'still', 1]]);
    expect(rows[1].items?.[0].id).toBe('v1');
  });
});
