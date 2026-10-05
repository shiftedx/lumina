import { describe, expect, it } from 'vitest';
import { BULK_MAX, bulkOps, EMPTY_BULK, parseList, rangeIds, resultMessage, selectionNote, toggleId } from './bulkModel';

describe('bulkModel', () => {
  it('parses lists trimmed and case-insensitively unique', () => expect(parseList(' Noir, noir ,Heist,, ')).toEqual(['Noir', 'Heist']));
  it('builds ops in spec order and nothing for an untouched draft', () => {
    expect(bulkOps(EMPTY_BULK)).toEqual([]);
    expect(bulkOps({ ...EMPTY_BULK, addGenres: 'Noir', removeTags: 'kids', rating: 'TV-14', lockGenres: 'lock', lockRating: 'lock', lockTags: 'unlock', lockItems: 'lock' })).toEqual([
      { op: 'add', field: 'genres', values: ['Noir'] }, { op: 'remove', field: 'tags', values: ['kids'] },
      { op: 'set', field: 'official_rating', value: 'TV-14' },
      { op: 'lock', fields: ['genres', 'official_rating'] }, { op: 'unlock', fields: ['tags'] }, { op: 'lock_item' },
    ]);
  });
  it('caps the selection at 500', () => {
    let selected: ReadonlySet<string> = new Set();
    for (let i = 0; i < BULK_MAX + 5; i += 1) selected = toggleId(selected, `t${i}`);
    expect(selected.size).toBe(BULK_MAX);
    expect(toggleId(selected, 't0').size).toBe(BULK_MAX - 1); // deselect still works at the cap
    expect(selectionNote(BULK_MAX)).toBe('You can edit up to 500 titles at once.');
    expect(selectionNote(3)).toBeNull();
  });
  it('selects a loaded range either direction, skips unloaded rows and stops at the cap', () => {
    const at = (i: number) => (i === 3 ? undefined : ({ id: `t${i}` } as never));
    expect([...rangeIds(new Set(), 5, 1, at)].sort()).toEqual(['t1', 't2', 't4', 't5']);
    expect(rangeIds(new Set(), 0, 900, (i) => ({ id: `t${i}` }) as never).size).toBe(BULK_MAX);
  });
  it('words the result like the spec', () => {
    expect(resultMessage(48, [{ reason: 'locked_item' }, { reason: 'locked_item' }])).toBe("Updated 48 titles. 2 were skipped because they're locked.");
    expect(resultMessage(1, [])).toBe('Updated 1 title.');
  });
});
