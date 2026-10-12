import type { EditConflict, EditEntry, FieldChange, FieldState, KeptValue, TitleMetadataDoc, TitleType, UserProfile } from '../../../types';

export type TabId = 'details' | 'people' | 'artwork' | 'ids' | 'episodes' | 'history';
export interface TitleDraft { changes: Record<string, FieldChange>; pin: string[]; locked?: boolean }
export type Draft = Record<string, TitleDraft>; // by title id

export interface TabProps {
  doc: TitleMetadataDoc;
  draft: Draft;
  user: UserProfile;
  setField: (titleId: string, key: string, value: unknown, base: unknown) => void;
  togglePin: (titleId: string, key: string) => void;
  setItemLock: (titleId: string, next: boolean, base: boolean) => void;
  discardField: (titleId: string, key: string) => void;
  reload: () => void; // refetch the doc, keep the draft
  reloadAfterUndo?: () => void; // also drops the title cache and refreshes the episode table and History
  episodesRevision: number;
}

/** Lists compare in order, on purpose. */
export const sameValue = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** Drops empty title entries so an untouched draft is `{}`. */
function prune(draft: Draft): Draft {
  const next: Draft = {};
  for (const [id, entry] of Object.entries(draft)) {
    if (Object.keys(entry.changes).length || entry.pin.length || entry.locked !== undefined) next[id] = entry;
  }
  return next;
}
const entryOf = (draft: Draft, id: string): TitleDraft => draft[id] ?? { changes: {}, pin: [] };

/** The first base of a field wins; a value that returns to its base is no change at all. */
export function setChange(draft: Draft, titleId: string, key: string, value: unknown, base: unknown): Draft {
  const entry = entryOf(draft, titleId);
  const effectiveBase = key in entry.changes ? entry.changes[key].base : base;
  const changes = { ...entry.changes };
  if (sameValue(value, effectiveBase)) delete changes[key];
  else changes[key] = { value, base: effectiveBase };
  return prune({ ...draft, [titleId]: { ...entry, changes } });
}

export function togglePinned(draft: Draft, titleId: string, key: string): Draft {
  const entry = entryOf(draft, titleId);
  const pin = entry.pin.includes(key) ? entry.pin.filter((name) => name !== key) : [...entry.pin, key];
  return prune({ ...draft, [titleId]: { ...entry, pin } });
}

/** The item lock rides in the draft only while it differs from the loaded flag. */
export function setLock(draft: Draft, titleId: string, next: boolean, base: boolean): Draft {
  const { locked: _dropped, ...rest } = entryOf(draft, titleId);
  return prune({ ...draft, [titleId]: next === base ? rest : { ...rest, locked: next } });
}

export function discardKey(draft: Draft, titleId: string, key: string): Draft {
  const entry = entryOf(draft, titleId);
  const changes = { ...entry.changes };
  delete changes[key];
  return prune({ ...draft, [titleId]: { ...entry, changes, pin: entry.pin.filter((name) => name !== key) } });
}

export const dirtyCount = (draft: Draft): number =>
  Object.values(draft).reduce((sum, entry) => sum + Object.keys(entry.changes).length + entry.pin.length + (entry.locked === undefined ? 0 : 1), 0);

export function toRequests(draft: Draft): EditEntry[] {
  return Object.entries(draft).map(([title_id, entry]) => ({
    title_id,
    changes: entry.changes,
    ...(entry.pin.length ? { pin: entry.pin } : {}),
    ...(entry.locked === undefined ? {} : { locked: entry.locked }),
  }));
}

/** "Keep mine" re-bases the conflicting fields on their current value; "load theirs" drops them from the draft. */
export function applyConflicts(draft: Draft, conflicts: EditConflict[], mode: 'mine' | 'theirs'): Draft {
  let next = draft;
  for (const conflict of conflicts) {
    for (const field of conflict.fields) {
      const entry = entryOf(next, conflict.title_id);
      if (mode === 'theirs') next = discardKey(next, conflict.title_id, field);
      else if (field in entry.changes) next = { ...next, [conflict.title_id]: { ...entry, changes: { ...entry.changes, [field]: { value: entry.changes[field].value, base: conflict.current[field]?.value ?? null } } } };
    }
  }
  return prune(next);
}

export type FieldKind = 'text' | 'long' | 'int' | 'decimal' | 'date' | 'time' | 'datetime' | 'list' | 'days' | 'status' | 'season' | 'order' | 'group';
export interface FieldMeta { label: string; kind: FieldKind; max?: number; vocabulary?: 'genres' | 'tags' | 'studios' | 'official_rating' }

export const FIELD_META: Record<string, FieldMeta> = {
  name: { label: 'Name', kind: 'text', max: 300 },
  original_title: { label: 'Original title', kind: 'text', max: 300 },
  sort_name: { label: 'Sort title', kind: 'text', max: 300 },
  year: { label: 'Year', kind: 'int' },
  premiered: { label: 'Release date', kind: 'date' },
  end_date: { label: 'End date', kind: 'date' },
  status: { label: 'Status', kind: 'status' },
  air_days: { label: 'Air days', kind: 'days' },
  air_time: { label: 'Air time', kind: 'time' },
  added_at: { label: 'Date added', kind: 'datetime' },
  runtime_minutes: { label: 'Runtime (minutes)', kind: 'int' },
  tagline: { label: 'Tagline', kind: 'text', max: 500 },
  overview: { label: 'Overview', kind: 'long', max: 8000 },
  official_rating: { label: 'Parental rating', kind: 'text', max: 20, vocabulary: 'official_rating' },
  custom_rating: { label: 'Custom rating', kind: 'text', max: 20 },
  community_rating: { label: 'Community rating', kind: 'decimal' },
  critic_rating: { label: 'Critic rating', kind: 'decimal' },
  genres: { label: 'Genres', kind: 'list', vocabulary: 'genres' },
  tags: { label: 'Tags', kind: 'list', vocabulary: 'tags' },
  studios: { label: 'Studios', kind: 'list', vocabulary: 'studios' },
  parent_id: { label: 'Season', kind: 'season' },
  index_number: { label: 'Number', kind: 'int' },
  index_number_end: { label: 'Ends at', kind: 'int' },
  airsbefore_season: { label: 'Airs before season', kind: 'int' },
  airsbefore_episode: { label: 'Airs before episode', kind: 'int' },
  airsafter_season: { label: 'Airs after season', kind: 'int' },
  display_order: { label: 'Episode order', kind: 'order' },
  episode_group: { label: 'TMDB episode group', kind: 'group', max: 24 },
};
/** Field order, minus the fields that have their own tabs (people, provider ids, images). */
export const DETAIL_ORDER = Object.keys(FIELD_META);

export function parseInput(kind: FieldKind, raw: string): unknown {
  switch (kind) {
    case 'int': return raw.trim() === '' ? null : Math.trunc(Number(raw));
    case 'decimal': return raw.trim() === '' ? null : Number(raw);
    case 'datetime': return raw ? new Date(raw).toISOString() : null;
    default: return raw.trim() === '' ? null : raw;
  }
}

const SOURCE_NAME = { tmdb: 'TMDB', nfo: 'NFO file', path: 'folder name' } as const;

export function sourceChip(state: FieldState): string | null {
  if (state.source === 'user') return sameValue(state.value, state.kept?.value ?? null) ? 'Pinned' : 'Your edit';
  if (state.source === null) return null;
  return `From ${SOURCE_NAME[state.source]}`;
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '(empty)';
  if (Array.isArray(value)) return value.length ? value.join(', ') : '(empty)';
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return text.length > 200 ? `${text.slice(0, 200)}…` : text;
}

export function revertCopy(kept: KeptValue | null): { question: string; quote: string | null; canRevert: true } {
  if (kept && kept.source && kept.source !== 'user') return { question: `Go back to the ${SOURCE_NAME[kept.source]} value?`, quote: formatValue(kept.value), canRevert: true };
  return { question: 'Nothing to go back to. The field will be empty until the next scan or refresh.', quote: null, canRevert: true };
}

export const TYPE_LABEL: Partial<Record<TitleType, string>> = { movie: 'Movie', series: 'Series', season: 'Season', episode: 'Episode' };

const TAB_LABEL: Record<TabId, string> = { details: 'Details', people: 'People', artwork: 'Artwork', ids: 'IDs', episodes: 'Episodes', history: 'History' };
export function tabsFor(type: TitleType): { value: TabId; label: string }[] {
  const tabs: TabId[] = ['details'];
  if (type !== 'season') tabs.push('people');
  tabs.push('artwork', 'ids');
  if (type === 'series' || type === 'season') tabs.push('episodes');
  tabs.push('history');
  return tabs.map((value) => ({ value, label: TAB_LABEL[value] }));
}
