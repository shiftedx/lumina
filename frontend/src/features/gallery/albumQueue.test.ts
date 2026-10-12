import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { albumTrack } from '../../test/galleryFixtures';
import { albumQueue, clearAlbumQueue, setAlbumQueue, shuffled, stepFrom, trackLabel } from './albumQueue';

const tracks = [1, 2, 3].map((number) => albumTrack(number));

beforeEach(() => clearAlbumQueue());
afterEach(() => vi.restoreAllMocks());

describe('albumQueue', () => {
  it('steps through the album in order and ends after the last track', () => {
    expect(stepFrom('track-1', tracks, 1)).toEqual({ itemId: 'track-2', label: '2. Song 2' });
    expect(stepFrom('track-2', tracks, -1)).toEqual({ itemId: 'track-1', label: '1. Song 1' });
    expect(stepFrom('track-3', tracks, 1)).toBeNull();
    expect(stepFrom('track-1', tracks, -1)).toBeNull();
    expect(stepFrom('elsewhere', tracks, 1)).toBeNull();
  });

  it('follows a stored order while it holds the playing track, across albums', () => {
    const other = albumTrack(1, { item_id: 'other-1', name: 'Elsewhere' });
    setAlbumQueue('artist-1', [tracks[2], other, tracks[0]]);
    expect(albumQueue()).toEqual({ albumId: 'artist-1', order: ['track-3', 'other-1', 'track-1'], labels: { 'track-3': '3. Song 3', 'other-1': '1. Elsewhere', 'track-1': '1. Song 1' } });
    expect(stepFrom('track-3', tracks, 1)).toEqual({ itemId: 'other-1', label: '1. Elsewhere' });
    expect(stepFrom('other-1', [], 1)).toEqual({ itemId: 'track-1', label: '1. Song 1' });
    expect(stepFrom('track-1', tracks, 1)).toBeNull();
    // Not in the stored order: the album's own order applies.
    expect(stepFrom('track-2', tracks, 1)).toEqual({ itemId: 'track-3', label: '3. Song 3' });
    clearAlbumQueue();
    expect(stepFrom('track-3', tracks, 1)).toBeNull();
  });

  it('shuffles with Fisher–Yates over crypto.getRandomValues, keeping every item once', () => {
    const random = vi.spyOn(crypto, 'getRandomValues').mockImplementation((array) => { (array as Uint32Array)[0] = 0; return array; });
    // i = 2 swaps with 0 → c b a; i = 1 swaps with 0 → b c a.
    expect(shuffled(['a', 'b', 'c'])).toEqual(['b', 'c', 'a']);
    expect(random).toHaveBeenCalledTimes(2);
    random.mockRestore();
    const many = Array.from({ length: 50 }, (_, index) => index);
    expect([...shuffled(many)].sort((a, b) => a - b)).toEqual(many);
  });

  it('labels an unnumbered track by its name alone', () => {
    expect(trackLabel({ number: null, name: 'Intro' })).toBe('Intro');
    expect(trackLabel({ number: 4, name: 'The Channel' })).toBe('4. The Channel');
  });
});
