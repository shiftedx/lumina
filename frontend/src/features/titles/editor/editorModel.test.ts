import { describe, expect, it } from 'vitest';
import {
  applyConflicts, dirtyCount, FIELD_META, parseInput, revertCopy, setChange, setLock, sourceChip, tabsFor, togglePinned, toRequests,
  type Draft,
} from './editorModel';

const state = (value: unknown, source: 'user' | 'nfo' | 'tmdb' | 'path' | null = 'tmdb', patch = {}) => ({ value, source, locked: source === 'user', kept: null, ...patch });

describe('draft', () => {
  it('records a change with the loaded value as its base and drops it when the value returns to the base', () => {
    let draft: Draft = setChange({}, 't1', 'overview', 'New', 'Old');
    expect(draft).toEqual({ t1: { changes: { overview: { value: 'New', base: 'Old' } }, pin: [] } });
    draft = setChange(draft, 't1', 'overview', 'Old', 'Old');
    expect(draft).toEqual({});
  });
  it('keeps the first base when a field is edited twice', () => {
    let draft = setChange({}, 't1', 'genres', ['Drama'], ['Drama', 'Sci-Fi']);
    draft = setChange(draft, 't1', 'genres', [], ['Drama']);   // a later base argument must not win
    expect(draft.t1.changes.genres).toEqual({ value: [], base: ['Drama', 'Sci-Fi'] });
  });
  it('treats null as a clear and compares lists in order', () => {
    expect(setChange({}, 't1', 'tagline', null, 'Old').t1.changes.tagline).toEqual({ value: null, base: 'Old' });
    expect(setChange({}, 't1', 'genres', ['B', 'A'], ['A', 'B']).t1.changes.genres.value).toEqual(['B', 'A']);
  });
  it('pins once, unpins on the second toggle, and an empty draft disappears', () => {
    let draft = togglePinned({}, 't1', 'official_rating');
    expect(draft.t1.pin).toEqual(['official_rating']);
    draft = togglePinned(draft, 't1', 'official_rating');
    expect(draft).toEqual({});
  });
  it('carries the item lock only while it differs from the loaded flag', () => {
    expect(setLock({}, 't1', true, false)).toEqual({ t1: { changes: {}, pin: [], locked: true } });
    expect(setLock(setLock({}, 't1', true, false), 't1', false, false)).toEqual({});
  });
  it('counts every change, pin and lock', () => {
    const draft = setLock(togglePinned(setChange(setChange({}, 't1', 'name', 'A', 'B'), 't2', 'overview', 'x', 'y'), 't1', 'genres'), 't2', true, false);
    expect(dirtyCount(draft)).toBe(4);
  });
  it('builds one request per touched title, in the spec 4.2 shape', () => {
    const draft = setLock(togglePinned(setChange({}, 't1', 'overview', 'New', 'Old'), 't1', 'official_rating'), 't1', true, false);
    expect(toRequests(draft)).toEqual([{ title_id: 't1', changes: { overview: { value: 'New', base: 'Old' } }, pin: ['official_rating'], locked: true }]);
    expect(toRequests({})).toEqual([]);
  });
});

describe('conflicts', () => {
  const draft = setChange(setChange({}, 't1', 'overview', 'Mine', 'Old'), 't1', 'name', 'Dune', 'Dune (2021)');
  const conflicts = [{ title_id: 't1', fields: ['overview'], current: { overview: state('Theirs', 'user') } }];
  it('"keep mine" re-bases only the conflicting field on their current value', () => {
    const next = applyConflicts(draft, conflicts, 'mine');
    expect(next.t1.changes.overview).toEqual({ value: 'Mine', base: 'Theirs' });
    expect(next.t1.changes.name).toEqual({ value: 'Dune', base: 'Dune (2021)' });
  });
  it('"load theirs" drops the conflicting field from the draft and keeps the rest', () => {
    const next = applyConflicts(draft, conflicts, 'theirs');
    expect(Object.keys(next.t1.changes)).toEqual(['name']);
  });
});

describe('field presentation', () => {
  it('names the source of a field', () => {
    expect(sourceChip(state('x', 'tmdb'))).toBe('From TMDB');
    expect(sourceChip(state('x', 'nfo'))).toBe('From NFO file');
    expect(sourceChip(state('x', 'path'))).toBe('From folder name');
    expect(sourceChip(state(null, null))).toBeNull();
  });
  it('tells an edit from a pin by whether the value left the kept one', () => {
    expect(sourceChip(state('New', 'user', { kept: { source: 'tmdb', value: 'Old' } }))).toBe('Your edit');
    expect(sourceChip(state('Same', 'user', { kept: { source: 'tmdb', value: 'Same' } }))).toBe('Pinned');
    expect(sourceChip(state(null, 'user', { kept: null }))).toBe('Pinned');
  });
  it('words the revert question exactly as the spec does', () => {
    expect(revertCopy({ source: 'nfo', value: 'Dune (2021)' })).toEqual({ question: 'Go back to the NFO file value?', quote: 'Dune (2021)', canRevert: true });
    expect(revertCopy(null)).toEqual({ question: 'Nothing to go back to. The field will be empty until the next scan or refresh.', quote: null, canRevert: true });
  });
  it('parses inputs: blank is a clear, numbers are numbers, dates are ISO', () => {
    expect(parseInput('text', '  ')).toBeNull();
    expect(parseInput('int', '12')).toBe(12);
    expect(parseInput('int', '')).toBeNull();
    expect(parseInput('decimal', '7.5')).toBe(7.5);
    expect(parseInput('date', '2021-10-22')).toBe('2021-10-22');
  });
  it('lists the tabs each type has (People is not on a season; Episodes only on series and seasons)', () => {
    expect(tabsFor('movie').map((tab) => tab.value)).toEqual(['details', 'people', 'artwork', 'ids', 'history']);
    expect(tabsFor('series').map((tab) => tab.value)).toEqual(['details', 'people', 'artwork', 'ids', 'episodes', 'history']);
    expect(tabsFor('season').map((tab) => tab.value)).toEqual(['details', 'artwork', 'ids', 'episodes', 'history']);
    expect(tabsFor('episode').map((tab) => tab.value)).toEqual(['details', 'people', 'artwork', 'ids', 'history']);
  });
  it('labels every catalogue key the Details tab can show', () => {
    expect(FIELD_META.official_rating.label).toBe('Parental rating');
    expect(FIELD_META.index_number.label).toBe('Number');
  });
});
