import { describe, expect, it } from 'vitest';
import { imageAt, moveOrder, originLabel, slotKey, slotsFor } from './artworkModel';
import { image } from './artworkFixtures';

describe('slots', () => {
  it('names a slot key the way the history and pin API do', () => {
    expect(slotKey('Primary', 0)).toBe('images.Primary');
    expect(slotKey('Backdrop', 0)).toBe('images.Backdrop');
    expect(slotKey('Backdrop', 3)).toBe('images.Backdrop.3');
    expect(slotKey('Logo', 0)).toBe('images.Logo');
  });
  it('gives a movie a Poster, its occupied backdrops plus one empty slot, and a Logo', () => {
    const slots = slotsFor('movie', [image('Primary', 0), image('Backdrop', 0), image('Backdrop', 1)]);
    expect(slots.map((slot) => slot.label)).toEqual(['Poster', 'Backdrop 1', 'Backdrop 2', 'Backdrop 3', 'Logo']);
  });
  it('offers no empty backdrop slot once five are in use', () => {
    const five = [0, 1, 2, 3, 4].map((index) => image('Backdrop', index));
    expect(slotsFor('series', five).filter((slot) => slot.type === 'Backdrop')).toHaveLength(5);
  });
  it('shows the first empty backdrop slot when there are none', () => {
    expect(slotsFor('series', []).filter((slot) => slot.type === 'Backdrop').map((slot) => slot.label)).toEqual(['Backdrop 1']);
  });
  it('a season has Poster and Backdrops only; an episode has a Still only', () => {
    expect(slotsFor('season', []).map((slot) => slot.label)).toEqual(['Poster', 'Backdrop 1']);
    expect(slotsFor('episode', []).map((slot) => slot.label)).toEqual(['Still']);
  });
  it('finds an image by type and index; an empty-slot entry is not an image', () => {
    const images = [image('Backdrop', 1), image('Logo', 0, { url: null, tag: null })];
    expect(imageAt(images, 'Backdrop', 1)?.index).toBe(1);
    expect(imageAt(images, 'Backdrop', 0)).toBeNull();
    expect(imageAt(images, 'Logo', 0)).toBeNull();
  });
});

describe('reordering backdrops', () => {
  it('moves an image one place left or right inside the occupied set', () => {
    expect(moveOrder([0, 1, 2], 2, -1)).toEqual([0, 2, 1]);
    expect(moveOrder([0, 1, 2], 0, 1)).toEqual([1, 0, 2]);
  });
  it('refuses to move past either end', () => {
    expect(moveOrder([0, 1, 2], 0, -1)).toBeNull();
    expect(moveOrder([0, 1, 2], 2, 1)).toBeNull();
  });
});

describe('origin chips', () => {
  it('says where an image came from', () => {
    expect(originLabel(image('Primary', 0, { origin: 'tmdb' }))).toBe('From TMDB');
    expect(originLabel(image('Primary', 0, { origin: 'upload' }))).toBe('Uploaded');
    expect(originLabel(image('Primary', 0, { origin: 'local' }))).toBe('From folder');
    expect(originLabel(image('Primary', 0, { origin: 'embedded' }))).toBe('From video file');
  });
});
