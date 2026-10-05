/** Bulk edit for the selected titles: add or remove genres and tags, set the parental rating, lock fields. */
import { useState } from 'react';

import { bulkEditMetadata } from '../../api';
import { Button, Dialog, Field, fieldProps, Input, Radio } from '../../ui';
import { ChipInput } from '../titles/editor/ChipInput';
import { useVocabulary } from '../titles/editor/useVocabulary';
import '../titles/editor/fields.css';
import { BULK_MAX, type BulkDraft, bulkOps, EMPTY_BULK, type LockChoice, parseList, resultMessage } from './bulkModel';

export type BulkEditDialogProps = { titleIds: string[]; onClose: () => void; onDone: (message: string, batchId: string) => void };

const LOCKS: ReadonlyArray<[keyof BulkDraft, string]> = [['lockGenres', 'genres'], ['lockTags', 'tags'], ['lockRating', 'parental rating'], ['lockItems', 'items']];
const CHOICES: ReadonlyArray<[LockChoice, string]> = [['leave', 'Leave'], ['lock', 'Lock'], ['unlock', 'Unlock']];

export function BulkEditDialog({ titleIds, onClose, onDone }: BulkEditDialogProps) {
  const [draft, setDraft] = useState<BulkDraft>(EMPTY_BULK);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const genres = useVocabulary('genres');
  const tags = useVocabulary('tags');
  const ratings = useVocabulary('official_rating');
  const n = Math.min(titleIds.length, BULK_MAX);
  const ops = bulkOps(draft);
  const set = (patch: Partial<BulkDraft>) => setDraft((current) => ({ ...current, ...patch }));
  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await bulkEditMetadata({ title_ids: titleIds, ops });
      onDone(resultMessage(result.applied, result.skipped), result.batch_id ?? '');
    } catch {
      setError('Lumina could not save these changes. Try again.');
      setBusy(false);
    }
  };
  const listField = (label: string, key: 'addGenres' | 'removeGenres' | 'addTags' | 'removeTags', suggestions: readonly string[]) => (
    <Field label={label}>
      {(ids) => <ChipInput id={ids.inputId} label={label} onChange={(values) => set({ [key]: values.join(', ') })} suggestions={suggestions} values={parseList(draft[key])} />}
    </Field>
  );
  return (
    <Dialog
      busy={busy}
      description={`${n} title${n === 1 ? '' : 's'} selected`}
      footer={<><Button onClick={onClose} variant="secondary">Cancel</Button><Button disabled={!ops.length || busy} onClick={apply} variant="primary">{`Apply to ${n} title${n === 1 ? '' : 's'}`}</Button></>}
      onClose={onClose}
      open
      title="Edit details"
    >
      {listField('Add genres', 'addGenres', genres)}
      {listField('Remove genres', 'removeGenres', genres)}
      {listField('Add tags', 'addTags', tags)}
      {listField('Remove tags', 'removeTags', tags)}
      <Field label="Parental rating">
        {(ids) => <Input {...fieldProps(ids)} list="bulk-rating-list" maxLength={20} onChange={(event) => set({ rating: event.target.value })} placeholder="Leave as is" value={draft.rating} />}
      </Field>
      <datalist id="bulk-rating-list">{ratings.map((value) => <option key={value} value={value} />)}</datalist>
      {LOCKS.map(([key, name]) => (
        <fieldset aria-label={`Lock ${name}`} key={key} role="group">
          <legend className="g-label">{`Lock ${name}`}</legend>
          {CHOICES.map(([value, label]) => <Radio checked={draft[key] === value} key={value} label={label} name={`bulk-${key}`} onChange={() => set({ [key]: value })} />)}
        </fieldset>
      ))}
      {error ? <p role="alert">{error}</p> : null}
    </Dialog>
  );
}
