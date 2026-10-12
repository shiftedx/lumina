import { describe, expect, it } from 'vitest';

import {
  cancelText, DEFAULT_HOME_ORDER, dropText, enterText, HOME_CATALOGUE, HOME_SHELF_IDS, moveShelf, normalizeHomeShelves,
  pickUpText, positionText, setShelfVisible, STORED_SHELVES_CAP, visibilityText,
} from './homeShelves';

const defaults = () => DEFAULT_HOME_ORDER.map((id) => ({ id, visible: true }));

describe('normalizeHomeShelves', () => {
  it('gives the default order, all visible, for no preference or a value that is not a list', () => {
    for (const stored of [undefined, null, 'continue', 7, { id: 'live', visible: false }]) expect(normalizeHomeShelves(stored)).toEqual(defaults());
  });

  it('keeps the stored order and visibility, then appends missing shelves visible in default order', () => {
    const layout = normalizeHomeShelves([{ id: 'recent_music', visible: false }, { id: 'continue', visible: true }]);
    expect(layout.slice(0, 2)).toEqual([{ id: 'recent_music', visible: false }, { id: 'continue', visible: true }]);
    expect(layout.slice(2)).toEqual(DEFAULT_HOME_ORDER.filter((id) => id !== 'recent_music' && id !== 'continue').map((id) => ({ id, visible: true })));
  });

  it('drops unknown ids, repeats and malformed entries, and reads a non-boolean visible as shown', () => {
    const layout = normalizeHomeShelves([
      { id: 'retired_shelf', visible: false }, { id: 'live', visible: 'no' }, { id: 'live', visible: false }, null, 'next_up',
      { id: 'constructor', visible: false }, { id: 'next_up', visible: false },
    ]);
    expect(layout.slice(0, 2)).toEqual([{ id: 'live', visible: true }, { id: 'next_up', visible: false }]);
    expect(layout.map((entry) => entry.id).sort()).toEqual([...HOME_SHELF_IDS].sort());
  });

  it('reads at most 64 stored entries', () => {
    const stored = [...Array.from({ length: STORED_SHELVES_CAP }, () => ({ id: 'bogus', visible: true })), { id: 'recent_music', visible: false }];
    expect(normalizeHomeShelves(stored).find((entry) => entry.id === 'recent_music')).toEqual({ id: 'recent_music', visible: true });
  });

  it('names and shapes every catalogue shelf, in the default order', () => {
    expect(DEFAULT_HOME_ORDER).toEqual([
      'continue', 'live', 'next_up', 'watchlist', 'new_in_library', 'because_you_watched', 'recommended',
      'picked_for_you', 'collections', 'from_follows', 'recently_saved', 'new_anime', 'recent_music',
    ]);
    expect(Object.keys(HOME_CATALOGUE).sort()).toEqual([...HOME_SHELF_IDS].sort());
    expect(HOME_CATALOGUE.because_you_watched).toEqual({ name: 'Because you watched…', shape: 'poster' });
    expect(HOME_CATALOGUE.recent_music).toEqual({ name: 'Recently added music', shape: 'square' });
    expect(HOME_CATALOGUE.live).toEqual({ name: 'Live now', shape: 'still' });
  });
});

describe('editing helpers', () => {
  const layout = normalizeHomeShelves(null);
  const ids = (list: { id: string }[]) => list.map((entry) => entry.id);

  it('moves a shelf to a clamped index and returns the same list when it does not move', () => {
    expect(ids(moveShelf(layout, 'next_up', 4)).slice(0, 5)).toEqual(['continue', 'live', 'watchlist', 'new_in_library', 'next_up']);
    expect(ids(moveShelf(layout, 'next_up', -5))[0]).toBe('next_up');
    expect(ids(moveShelf(layout, 'next_up', 99)).at(-1)).toBe('next_up');
    expect(moveShelf(layout, 'next_up', 2)).toBe(layout);
    expect(layout[2].id).toBe('next_up'); // the input is never mutated
  });

  it('sets one shelf visible or hidden and leaves the rest', () => {
    const hidden = setShelfVisible(layout, 'live', false);
    expect(hidden.find((entry) => entry.id === 'live')).toEqual({ id: 'live', visible: false });
    expect(hidden.filter((entry) => !entry.visible)).toHaveLength(1);
    expect(setShelfVisible(hidden, 'live', true).every((entry) => entry.visible)).toBe(true);
  });

  it('builds every edit-mode announcement verbatim', () => {
    expect(enterText(13)).toBe('Editing Home. 13 shelves.');
    expect(pickUpText('Next up', 3, 13)).toBe('Next up picked up, position 3 of 13. Use Up and Down to move, Space to drop, Escape to cancel.');
    expect(positionText('Next up', 4, 13)).toBe('Next up, position 4 of 13.');
    expect(dropText('Next up', 4, 13)).toBe('Next up dropped at position 4 of 13.');
    expect(cancelText('Next up', 3)).toBe('Reorder cancelled. Next up is back at position 3.');
    expect(visibilityText('Next up', false)).toBe('Next up hidden.');
    expect(visibilityText('Next up', true)).toBe('Next up shown.');
  });
});
