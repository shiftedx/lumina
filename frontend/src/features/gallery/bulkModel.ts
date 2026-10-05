import type { BulkOp, TitleSummary } from '../../types';

export const BULK_MAX = 500;
export type LockChoice = 'leave' | 'lock' | 'unlock';
export type BulkDraft = {
  addGenres: string; removeGenres: string; addTags: string; removeTags: string;
  rating: string; lockGenres: LockChoice; lockTags: LockChoice; lockRating: LockChoice; lockItems: LockChoice;
};
export const EMPTY_BULK: BulkDraft = { addGenres: '', removeGenres: '', addTags: '', removeTags: '', rating: '', lockGenres: 'leave', lockTags: 'leave', lockRating: 'leave', lockItems: 'leave' };

/** "Noir, Heist" -> ['Noir','Heist']: trimmed, 1-60 chars, case-insensitively unique. */
export function parseList(text: string): string[] {
  const seen = new Set<string>();
  return text.split(',').map((part) => part.trim().slice(0, 60)).filter((part) => part && !seen.has(part.toLowerCase()) && seen.add(part.toLowerCase()));
}

/** Toggle one id; at the cap a new id is refused (returns an equal set). */
export function toggleId(selected: ReadonlySet<string>, id: string): ReadonlySet<string> {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id); else if (next.size < BULK_MAX) next.add(id);
  return next;
}

/** Shift-click: add every loaded title between two indices (inclusive), stopping at the cap. */
export function rangeIds(selected: ReadonlySet<string>, from: number, to: number, at: (index: number) => TitleSummary | undefined): ReadonlySet<string> {
  const next = new Set(selected);
  for (let index = Math.min(from, to); index <= Math.max(from, to) && next.size < BULK_MAX; index += 1) { const title = at(index); if (title) next.add(title.id); }
  return next;
}

export const selectionNote = (size: number): string | null => (size >= BULK_MAX ? `You can edit up to ${BULK_MAX} titles at once.` : null);

/** The section 4.5 ops for a draft; empty when nothing was asked for. */
export function bulkOps(draft: BulkDraft): BulkOp[] {
  const ops: BulkOp[] = [];
  for (const [field, add, remove] of [['genres', draft.addGenres, draft.removeGenres], ['tags', draft.addTags, draft.removeTags]] as const) {
    if (parseList(add).length) ops.push({ op: 'add', field, values: parseList(add) });
    if (parseList(remove).length) ops.push({ op: 'remove', field, values: parseList(remove) });
  }
  if (draft.rating.trim()) ops.push({ op: 'set', field: 'official_rating', value: draft.rating.trim().slice(0, 20) });
  const fields = (choice: LockChoice) => [['genres', draft.lockGenres], ['tags', draft.lockTags], ['official_rating', draft.lockRating]].filter(([, value]) => value === choice).map(([field]) => field);
  if (fields('lock').length) ops.push({ op: 'lock', fields: fields('lock') });
  if (fields('unlock').length) ops.push({ op: 'unlock', fields: fields('unlock') });
  if (draft.lockItems === 'lock') ops.push({ op: 'lock_item' });
  if (draft.lockItems === 'unlock') ops.push({ op: 'unlock_item' });
  return ops;
}

/** "Updated 48 titles. 2 were skipped because they're locked." */
export function resultMessage(applied: number, skipped: ReadonlyArray<{ reason: string }>): string {
  const locked = skipped.filter((entry) => entry.reason === 'locked_item').length;
  const other = skipped.length - locked;
  const noun = (n: number) => `${n} title${n === 1 ? '' : 's'}`;
  return [`Updated ${noun(applied)}.`, locked ? `${locked} ${locked === 1 ? 'was' : 'were'} skipped because ${locked === 1 ? "it's" : "they're"} locked.` : '', other ? `${other} could not be changed.` : ''].filter(Boolean).join(' ');
}
