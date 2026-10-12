import { describe, expect, it } from 'vitest';
import type { LocalSearchResponse, SearchHistoryEntry, SourceAutomation, UserProfile, YouTubeSearchResult } from '../../types';
import { ACTION_DESTINATIONS, buildPalette, cleanQuery, flatOptions, keepActive, matchesQuery, moveActive, type PaletteInput } from './paletteModel';

const admin = { id: 'u1', username: 'alexandria', display_name: 'Alexandria', role: 'admin', is_active: true } as UserProfile;
const member = { ...admin, id: 'u2', role: 'viewer' } as UserProfile;
const history = (n: number): SearchHistoryEntry[] => Array.from({ length: n }, (_, i) => ({ id: `h${i}`, query: `query ${i}`, searched_at: '2026-09-01T00:00:00Z' }) as SearchHistoryEntry);
const title = (id: string, name: string, category: string | null, type = 'movie') => ({ kind: 'title', id, title: name, subtitle: '2024', media_title: { id, name, type, category, year: 2024 } });
const local = (matches: unknown[]): LocalSearchResponse => ({ query: 'al', mode: 'hybrid', matches, items: [], index_generation: 1 }) as unknown as LocalSearchResponse;
const channels = [{ id: 'c1', label: 'Lime Alps', source_url: 'https://youtube.com/@alpine' }, { id: 'c2', label: 'Baking', source_url: 'https://youtube.com/@bake' }] as SourceAutomation[];
const remote = [{ id: 'y1', title: 'Alpine drive', webpage_url: 'https://youtube.com/watch?v=y1', uploader: 'Road' }] as YouTubeSearchResult[];
const base: PaletteInput = { query: '', mode: 'search', user: admin, history: history(8), local: null, remote: [], channels, actions: [] };

describe('buildPalette', () => {
  it('shows up to 6 recent searches and the pinned actions for an empty query', () => {
    const groups = buildPalette({ ...base, actions: [{ id: 'add-link', label: 'Add a link…' }] as never });
    expect(groups.map((group) => group.id)).toEqual(['recent', 'actions']);
    expect(groups[0].options).toHaveLength(6);
    expect(flatOptions(groups).at(-1)?.kind).not.toBe('everything');
  });

  it('orders groups as the spec does and caps each', () => {
    const matches = [
      ...Array.from({ length: 6 }, (_, i) => title(`m${i}`, `Alpha ${i}`, 'movies')),
      title('s1', 'Alpine Show', 'shows', 'series'), title('a1', 'Alpine Anime', 'anime', 'series'), title('al1', 'Alpine Album', null, 'album'),
      { kind: 'moment', id: 'mo1', title: 'Alps at dawn', subtitle: 'Alpha 0 · 12:04', item: { id: 'lib1' }, start_ms: 724000 },
      { kind: 'library', id: 'v1', title: 'Alpine vlog', subtitle: 'YouTube', item: { id: 'lib2', title: 'Alpine vlog' } },
    ];
    const groups = buildPalette({ ...base, query: 'li', local: local(matches), remote });
    expect(groups.map((group) => group.id).filter((id) => id !== 'everything')).toEqual(['movies', 'shows', 'anime', 'music', 'moments', 'videos', 'channels', 'youtube', 'goto']);
    expect(groups.find((group) => group.id === 'movies')?.options).toHaveLength(4);
    expect(flatOptions(groups).at(-1)).toMatchObject({ kind: 'everything', label: 'Search everything for “li”' });
  });

  it('files albums and artists under Music and every other title by its category', () => {
    const groups = buildPalette({ ...base, query: 'al', local: local([title('ar1', 'Alpha Artist', null, 'artist'), title('e1', 'Alpha Episode', 'anime', 'episode')]) });
    expect(groups.find((group) => group.id === 'music')?.options[0].label).toBe('Alpha Artist');
    expect(groups.find((group) => group.id === 'anime')?.options[0].label).toBe('Alpha Episode');
  });

  it('a URL offers only Open link', () => {
    const groups = buildPalette({ ...base, query: 'https://youtu.be/abc', local: local([title('m1', 'Alpha', 'movies')]), remote });
    expect(flatOptions(groups).map((option) => option.kind)).toEqual(['link', 'everything']);
  });

  it('channels and Go to need one character; titles and YouTube need two', () => {
    const one = buildPalette({ ...base, query: 'a', local: local([title('m1', 'Alpha', 'movies')]), remote });
    expect(one.map((group) => group.id).filter((id) => id !== 'everything')).toEqual(['channels', 'goto']);
  });

  it('Go to lists only the sections the member can open', () => {
    const asMember = flatOptions(buildPalette({ ...base, user: member, query: 'members' }));
    const asAdmin = flatOptions(buildPalette({ ...base, query: 'members' }));
    expect(asMember.some((option) => option.kind === 'goto' && option.path === '/settings/members')).toBe(false);
    expect(asAdmin.some((option) => option.kind === 'goto' && option.path === '/settings/members')).toBe(true);
  });

  it('Go to finds settings by their old and Jellyfin names (aliases)', () => {
    const paths = (query: string) => flatOptions(buildPalette({ ...base, query })).flatMap((option) => (option.kind === 'goto' ? [option.path] : []));
    expect(paths('users')).toContain('/settings/members');
    expect(paths('account')).toContain('/settings/account');
    expect(paths('logs')).toContain('/settings/diagnostics');
  });

  it('Go to never lists the pages the Actions group owns (one row per destination)', () => {
    for (const query of ['live', 'explore', 'subscriptions', 'channels', 'discovery', 'downloads', 'playback', 'library']) {
      const goto = flatOptions(buildPalette({ ...base, query })).flatMap((option) => (option.kind === 'goto' ? [option.path] : []));
      expect(goto.filter((path) => ACTION_DESTINATIONS.has(path)), query).toEqual([]);
    }
  });

  it('link mode shows only Open link, and only for a URL', () => {
    expect(flatOptions(buildPalette({ ...base, mode: 'link', query: 'hello' }))).toEqual([]);
    expect(flatOptions(buildPalette({ ...base, mode: 'link', query: 'https://twitch.tv/x' })).map((option) => option.kind)).toEqual(['link']);
  });
});

describe('query helpers', () => {
  it('caps and trims the query', () => {
    expect(cleanQuery(`  ${'x'.repeat(5000)}  `)).toHaveLength(200);
    expect(cleanQuery('   ')).toBe('');
  });
  it('matches every query word as a word prefix, case-insensitively', () => {
    expect(matchesQuery('Use light theme', 'LI th')).toBe(true);
    expect(matchesQuery('Use light theme', 'ght')).toBe(false);
    expect(matchesQuery('Open downloads', '', ['queue'])).toBe(true);
    expect(matchesQuery('Open downloads', 'que', ['queue'])).toBe(true);
  });
});

describe('active row', () => {
  const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id) => ({ id }));
  it('moves, wraps and clamps', () => {
    expect(moveActive(ids, 'g', 'next')).toBe('a');
    expect(moveActive(ids, 'a', 'prev')).toBe('g');
    expect(moveActive(ids, 'f', 'pageDown')).toBe('g');
    expect(moveActive(ids, 'b', 'pageUp')).toBe('a');
    expect(moveActive(ids, 'c', 'first')).toBe('a');
    expect(moveActive(ids, 'c', 'last')).toBe('g');
    expect(moveActive([], null, 'next')).toBeNull();
  });
  it('keeps the active id across re-renders, else falls back to the first row', () => {
    expect(keepActive(ids, 'd')).toBe('d');
    expect(keepActive(ids, 'zz')).toBe('a');
    expect(keepActive([], 'd')).toBeNull();
  });
});
