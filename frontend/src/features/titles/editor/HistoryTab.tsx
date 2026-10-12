import { useCallback, useEffect, useRef, useState } from 'react';

import { getMetadataHistory } from '../../../api';
import type { EditKind, HistoryBatch } from '../../../types';
import { Button, EmptyState, ErrorState, Skeleton, useToast } from '../../../ui';
import { undoBatch } from './editorActions';
import { FIELD_META, type TabProps } from './editorModel';
import './history.css';

/** "just now", "3 min ago", "2 h ago", "4 days ago", then a locale date. */
export function ago(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, (now - Date.parse(iso)) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h ago`;
  const days = Math.floor(seconds / 86_400);
  return days < 30 ? `${days} ${days === 1 ? 'day' : 'days'} ago` : new Date(iso).toLocaleDateString();
}

const KIND: Partial<Record<EditKind, string>> = { image: 'Artwork', item_lock: 'Lock this item', revert: 'Reverted to source', undo: 'Undo', lock: 'Locked fields', bulk: 'Bulk edit' };
const OWN_TAB: Record<string, string> = { people: 'People', provider_ids: 'Provider IDs', 'images.Primary': 'Poster', 'images.Logo': 'Logo' };
const label = (field: string) => FIELD_META[field]?.label ?? OWN_TAB[field] ?? (field.startsWith('images.Backdrop') ? 'Backdrop' : field);
/** One list entry or object value as text: a person is "Name (Role)", an image is its tag, a map is "Key: value". */
function one(value: unknown): string {
  if (value === null || typeof value !== 'object') return String(value);
  const o = value as Record<string, unknown>;
  if (typeof o.name === 'string') return typeof o.role === 'string' && o.role ? `${o.name} (${o.role})` : o.name;
  if (typeof o.tag === 'string') return o.tag;
  return Object.entries(o).map(([k, v]) => `${k}: ${one(v)}`).join(', ');
}
const show = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '(cleared)';
  const text = Array.isArray(value) ? value.map(one).join(', ') : one(value);
  return text || '(cleared)';
};

function summary(batch: HistoryBatch): string {
  const count = batch.changes.length;
  return `${batch.user?.display_name ?? 'Former member'} · ${ago(batch.created_at)} · ${count} ${count === 1 ? 'change' : 'changes'}`;
}
const what = (batch: HistoryBatch) => KIND[batch.kind] ?? batch.changes.slice(0, 3).map((change) => label(change.field)).join(', ');

export default function HistoryTab({ doc, reload, reloadAfterUndo }: TabProps) {
  const toast = useToast();
  const [batches, setBatches] = useState<HistoryBatch[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const latest = useRef(0);
  const load = useCallback((from: string | null) => {
    setFailed(false);
    const mine = ++latest.current;
    getMetadataHistory(doc.title_id, from).then(
      (page) => { if (mine !== latest.current) return; setBatches((current) => (from && current ? [...current, ...page.batches] : page.batches)); setCursor(page.next_cursor); },
      () => { if (mine === latest.current) setFailed(true); },
    );
  }, [doc.title_id]);
  useEffect(() => load(null), [load]);

  if (failed) return <ErrorState onRetry={() => load(null)} title="Lumina couldn't load the history." />;
  if (!batches) return <Skeleton count={3} label="Loading history" shape="row" />;
  if (!batches.length) return <EmptyState title="No edits yet." />;
  return (
    <div>
      <ul className="ed-history">
        {batches.map((batch) => (
          <li key={batch.batch_id}>
            <details>
              <summary>{summary(batch)} <span>{what(batch)}</span></summary>
              <dl>
                {batch.changes.map((change, i) => (
                  <div key={i}>
                    <dt>{label(change.field)}</dt>
                    <dd className="ed-history-value">{show(change.before)} → {show(change.after)}</dd>
                  </div>
                ))}
              </dl>
            </details>
            {batch.undone ? <span>Undone</span> : <Button variant="secondary" onClick={() => void undoBatch(batch.batch_id, toast, () => { (reloadAfterUndo ?? reload)(); load(null); })}>Undo</Button>}
          </li>
        ))}
      </ul>
      {cursor ? <Button variant="quiet" onClick={() => load(cursor)}>Show older</Button> : null}
    </div>
  );
}
