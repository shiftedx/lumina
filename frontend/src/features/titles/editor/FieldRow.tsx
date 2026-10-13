import { Lock, LockOpen } from 'lucide-react';
import { useEffect, useState } from 'react';

import { listEpisodeGroups, revertMetadata } from '../../../api';
import type { EpisodeGroup, FieldState, SeasonChoice } from '../../../types';
import { Button, Checkbox, Field, fieldProps, IconButton, Input, Select, Textarea, useToast } from '../../../ui';
import { forgetTitle } from '../../gallery/titleCache';
import { undoBatch } from './editorActions';
import { ChipInput } from './ChipInput';
import { FIELD_META, MUSIC_RELEASE_TYPES, parseInput, sourceChip, type TitleDraft } from './editorModel';
import { RevertPopover } from './RevertPopover';
import { useVocabulary } from './useVocabulary';
import './fields.css';

const WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const LIST_CAP: Record<string, number> = { genres: 30, studios: 30, tags: 50, mood: 30, styles: 30 };
const pad = (n: number) => String(n).padStart(2, '0');
const ORDERS = [{ value: 'aired', label: 'Aired' }, { value: 'dvd', label: 'DVD' }, { value: 'absolute', label: 'Absolute' }];
const GROUP_TYPES: Record<number, string> = { 1: 'Original air date', 2: 'Absolute', 3: 'DVD', 4: 'Digital', 5: 'Story arc', 6: 'Production', 7: 'TV' };

function toText(kind: string, value: unknown): string {
  if (value === null || value === undefined) return '';
  if (kind === 'datetime') {
    const at = new Date(String(value));
    return Number.isNaN(at.getTime()) ? '' : `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(at.getHours())}:${pad(at.getMinutes())}`;
  }
  return String(value);
}

export interface FieldRowProps {
  titleId: string;
  fieldKey: string;
  state: FieldState;
  draft: TitleDraft | undefined;
  itemLocked: boolean;
  setField: (titleId: string, key: string, value: unknown, base: unknown) => void;
  togglePin: (titleId: string, key: string) => void;
  discardField: (titleId: string, key: string) => void;
  onReverted: () => void;
  /** An episode's seasons, for the Season field. */
  seasons?: SeasonChoice[];
}

export function FieldRow({ titleId, fieldKey, state, draft, itemLocked, setField, togglePin, discardField, onReverted, seasons = [] }: FieldRowProps) {
  const meta = FIELD_META[fieldKey];
  const toast = useToast();
  const edited = Boolean(draft && fieldKey in draft.changes);
  const pinned = Boolean(draft?.pin.includes(fieldKey));
  const shown = edited ? (draft as TitleDraft).changes[fieldKey].value : state.value;
  const [text, setText] = useState(toText(meta.kind, shown));
  const [error, setError] = useState<string | null>(null);
  const shownKey = JSON.stringify(shown ?? null);
  useEffect(() => { setText(toText(meta.kind, shown)); setError(null); }, [shownKey]); // eslint-disable-line react-hooks/exhaustive-deps -- shownKey is the value's identity
  const vocabulary = useVocabulary(meta.vocabulary);
  const [groups, setGroups] = useState<EpisodeGroup[] | null>(null);
  const loadGroups = () => { if (groups === null) { setGroups([]); listEpisodeGroups(titleId).then(setGroups, () => undefined); } };
  const label = meta.label;

  const commit = (value: unknown) => setField(titleId, fieldKey, value, state.value);
  function type(raw: string) {
    setText(raw);
    const value = parseInput(meta.kind, raw);
    if (fieldKey === 'name' && value === null) { setError("A name can't be empty."); commit(state.value); return; }
    setError(null);
    commit(value);
  }

  async function revert() {
    try {
      const result = await revertMetadata(titleId, [fieldKey]);
      discardField(titleId, fieldKey);
      forgetTitle(titleId);
      const batch = result.batch_id;
      if (batch) toast({ tone: 'success', message: `${label} reverted.`, action: { label: 'Undo', onAction: () => void undoBatch(batch, toast, onReverted) } });
      onReverted();
    } catch {
      toast({ tone: 'error', message: `Lumina couldn't revert ${label}.` });
    }
  }

  const lockProps = { icon: state.locked || pinned ? <Lock /> : <LockOpen />, className: 'ed-lock' };
  let lock;
  if (itemLocked && state.source !== 'user') lock = <IconButton {...lockProps} disabled label="Locked by the item lock" />;
  else if (edited && state.source !== 'user') lock = <IconButton {...lockProps} label={`Unlock ${label}`} onClick={() => discardField(titleId, fieldKey)} />;
  else if (state.source === 'user') lock = <RevertPopover kept={state.kept} label={label} onRevert={() => void revert()} trigger={(triggerProps) => <IconButton {...lockProps} {...triggerProps} label={`Unlock ${label}`} />} />;
  else lock = <IconButton {...lockProps} label={`${pinned ? 'Unlock' : 'Lock'} ${label}`} onClick={() => togglePin(titleId, fieldKey)} />;

  const chip = edited ? 'Your edit' : pinned ? 'Pinned' : sourceChip(state);
  const list = Array.isArray(shown) ? (shown as string[]) : [];
  let control;
  switch (meta.kind) {
    case 'credits': {
      const credits = Array.isArray(shown) ? shown as { name: string; role: string; instrument?: string | null }[] : [];
      control = <fieldset disabled={itemLocked} className="ed-days"><legend className="g-field-label">Credits</legend><p className="g-setting-note">{meta.hint}</p>{credits.map((credit, index) => <div className="music-credit-editor" key={index}>
        {(['name', 'role', 'instrument'] as const).map((key) => <Field label={`${key === 'name' ? 'Name' : key === 'role' ? 'Role' : 'Instrument'} ${index + 1}`} key={key}>{(ids) => <Input {...fieldProps(ids)} maxLength={200} value={credit[key] ?? ''} onChange={(event) => commit(credits.map((c, i) => i === index ? { ...c, [key]: event.target.value } : c))} />}</Field>)}
        <Button variant="quiet" onClick={() => commit(credits.filter((_, i) => i !== index))}>Remove credit {index + 1}</Button>
      </div>)}<Button disabled={credits.length >= 200} variant="quiet" onClick={() => commit([...credits, { name: '', role: 'Performer', instrument: null }])}>Add credit</Button></fieldset>;
      break;
    }
    case 'list':
      control = <Field hint={meta.hint} label={label}>{(ids) => <ChipInput id={ids.inputId} label={label} maxItems={LIST_CAP[fieldKey]} maxLength={fieldKey === 'studios' ? 120 : 60} onChange={commit} suggestions={vocabulary} values={list} />}</Field>;
      break;
    case 'days':
      control = (
        <fieldset className="ed-days">
          <legend className="g-field-label">{label}</legend>
          {WEEK.map((day) => <Checkbox checked={list.includes(day)} key={day} label={day} onChange={(event) => commit(WEEK.filter((name) => (name === day ? event.target.checked : list.includes(name))))} />)}
        </fieldset>
      );
      break;
    case 'long':
      control = <Field error={error} hint={meta.hint} label={label}>{(ids) => <Textarea {...fieldProps(ids)} maxLength={20000} onChange={(event) => type(event.target.value)} rows={6} value={text} />}</Field>;
      break;
    case 'status':
      control = (
        <Field hint={meta.hint} label={label}>
          {(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => type(event.target.value)} value={text}>
              <option value="" />
              {['Continuing', 'Ended', 'Unreleased'].map((option) => <option key={option}>{option}</option>)}
            </Select>
          )}
        </Field>
      );
      break;
    case 'release_type':
      control = (
        <Field hint={meta.hint} label={label}>
          {(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => type(event.target.value)} value={text}>
              <option value="">Not set</option>
              {MUSIC_RELEASE_TYPES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </Select>
          )}
        </Field>
      );
      break;
    case 'music_date':
      control = (
        <Field error={error} hint={meta.hint} label={label}>
          {(ids) => <Input {...fieldProps(ids)} inputMode="numeric" maxLength={meta.max} onChange={(event) => type(event.target.value)} placeholder="YYYY or YYYY-MM-DD" value={text} />}
        </Field>
      );
      break;
    case 'season':
      control = (
        <Field hint={meta.hint} label={label}>
          {(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => commit(event.target.value)} value={text}>
              {seasons.map((season) => <option key={season.id} value={season.id}>{season.name}</option>)}
            </Select>
          )}
        </Field>
      );
      break;
    case 'order':
      control = (
        <Field hint={meta.hint ?? "How the files are numbered. Lumina reads TMDB in that order and never renames files."} label={label}>
          {(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => commit(event.target.value)} value={text || 'aired'}>
              {ORDERS.map((order) => <option key={order.value} value={order.value}>{order.label}</option>)}
            </Select>
          )}
        </Field>
      );
      break;
    case 'group':
      control = (
        <Field error={error} hint={meta.hint ?? "Leave empty to use TMDB's largest group for the episode order."} label={label}>
          {(ids) => <Input {...fieldProps(ids)} list={`ed-list-${fieldKey}`} maxLength={meta.max} onChange={(event) => type(event.target.value)} onFocus={loadGroups} value={text} />}
        </Field>
      );
      break;
    default: {
      const isYear = fieldKey === 'year' || fieldKey === 'original_year';
      const attrs = {
        int: { type: 'number', step: 1, min: isYear ? 1870 : fieldKey === 'runtime_minutes' ? 1 : 0, max: isYear ? 2100 : fieldKey === 'runtime_minutes' ? 2000 : 9999 },
        decimal: { type: 'number', step: 0.1, min: 0, max: fieldKey === 'critic_rating' ? 100 : 10 },
        date: { type: 'date' }, time: { type: 'time' }, datetime: { type: 'datetime-local', max: toText('datetime', new Date().toISOString()) },
        text: { type: 'text', maxLength: meta.max },
      }[meta.kind as 'int' | 'decimal' | 'date' | 'time' | 'datetime' | 'text'];
      control = (
        <Field error={error} hint={meta.hint} label={label}>
          {(ids) => <Input {...fieldProps(ids)} {...attrs} list={fieldKey === 'official_rating' ? `ed-list-${fieldKey}` : undefined} onChange={(event) => type(event.target.value)} value={text} />}
        </Field>
      );
    }
  }
  return (
    <div className={`ed-row${edited ? ' is-edited' : ''}`} data-field={fieldKey}>
      {control}
      {chip ? <span className="ed-src">{chip}</span> : <span />}
      {lock}
      {fieldKey === 'official_rating' ? <datalist id="ed-list-official_rating">{vocabulary.map((value) => <option key={value} value={value} />)}</datalist> : null}
      {meta.kind === 'group' ? (
        <datalist id={`ed-list-${fieldKey}`}>
          {(groups ?? []).map((group) => <option key={group.id} value={group.id}>{`${group.name} · ${GROUP_TYPES[group.type ?? 0] ?? 'Other'} · ${group.episode_count ?? 0} episodes`}</option>)}
        </datalist>
      ) : null}
    </div>
  );
}
