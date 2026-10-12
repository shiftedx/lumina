import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Plus, Sparkles, X } from 'lucide-react';

import { ApiRequestError, buildSmartCollectionRules, createHouseholdCollection, previewSmartCollectionRules, setSmartCollectionRules } from '../../api';
import { Button, Dialog, Field, fieldProps, IconButton, Input, SegmentedControl, Select, StatusText, TextButton, Textarea } from '../../ui';
import type { CollectionVisibility, HouseholdCollection, SmartCollectionRule, SmartRuleCondition, SmartRuleField, SmartRuleOp, SmartRulePreview, SmartRuleType } from '../../types';
import { errorMessage } from '../../utils';
import './collections.css';

/** UI hints only: the server's allowlisted validator is the authority. */
export const RULE_FIELDS: Record<SmartRuleField, { label: string; ops: SmartRuleOp[]; numeric?: boolean; values?: Array<[string, string]> }> = {
  genre: { label: 'Genre', ops: ['is', 'is_not', 'in', 'not_in'] },
  year: { label: 'Year', ops: ['is', 'gte', 'lte'], numeric: true },
  rating: { label: 'Rating', ops: ['gte', 'lte'], numeric: true },
  official_rating: { label: 'Age rating', ops: ['is', 'is_not', 'in', 'not_in'] },
  people: { label: 'Cast and crew', ops: ['is', 'in'] },
  provider: { label: 'Provider', ops: ['is', 'is_not'] },
  watched: { label: 'Watched', ops: ['is', 'is_not'], values: [['unwatched', 'Unwatched'], ['in_progress', 'In progress'], ['watched', 'Watched']] },
  added: { label: 'Added', ops: ['within_days'], numeric: true },
  runtime: { label: 'Runtime (minutes)', ops: ['gte', 'lte'], numeric: true },
  channel: { label: 'Channel', ops: ['is', 'is_not'] },
  tags: { label: 'Tag', ops: ['is', 'is_not', 'in', 'not_in'] },
};
const OP_LABELS: Record<SmartRuleOp, string> = { is: 'is', is_not: 'is not', in: 'is any of', not_in: 'is none of', gte: 'at least', lte: 'at most', within_days: 'within the last' };
const TYPES: Array<[SmartRuleType, string]> = [['movie', 'Movies'], ['series', 'Shows'], ['episode', 'Episodes'], ['channel_video', 'Channel videos']];
const EMPTY_RULE: SmartCollectionRule = { type: 'movie', match: 'all', conditions: [], limit: 100 };

export function ruleChipLabel(condition: SmartRuleCondition): string {
  const field = RULE_FIELDS[condition.field];
  const raw = Array.isArray(condition.value) ? condition.value.join(', ') : String(condition.value);
  const value = field.values?.find(([key]) => key === raw)?.[1] ?? raw;
  return condition.op === 'within_days' ? `${field.label} within the last ${value} days` : `${field.label} ${OP_LABELS[condition.op]} ${value}`;
}

export function parseConditionValue(field: SmartRuleField, op: SmartRuleOp, raw: string): SmartRuleCondition['value'] | null {
  const text = raw.trim();
  if (!text) return null;
  if (op === 'in' || op === 'not_in') {
    const list = text.split(',').map((entry) => entry.trim()).filter(Boolean);
    return list.length ? list : null;
  }
  if (RULE_FIELDS[field].numeric) {
    const number = Number(text);
    return Number.isFinite(number) ? number : null;
  }
  return text;
}

interface Row { key: number; field: SmartRuleField; op: SmartRuleOp; raw: string }
let rowKey = 0;
const blankRow = (): Row => ({ key: ++rowKey, field: 'genre', op: 'is', raw: '' });
const rowsOf = (rule: SmartCollectionRule): Row[] => (rule.conditions.length
  ? rule.conditions.map((condition) => ({ key: ++rowKey, field: condition.field, op: condition.op, raw: Array.isArray(condition.value) ? condition.value.join(', ') : String(condition.value) }))
  : [blankRow()]);

export function SmartCollectionBuilder({
  aiAvailable,
  mode,
  open,
  collection,
  onCreated,
  onCancel,
  create = createHouseholdCollection,
}: {
  aiAvailable: boolean;
  mode: 'new' | 'edit';
  open: boolean;
  /** Required for `edit`: its rules seed the rows and save through `setSmartCollectionRules`. */
  collection?: HouseholdCollection;
  /** Called with the saved collection, for both a new one and an edit. */
  onCreated: (collection: HouseholdCollection) => void;
  onCancel: () => void;
  /** The host panel's injectable create call (falls back to the real endpoint). */
  create?: typeof createHouseholdCollection;
}) {
  const seed = collection?.rules ?? EMPTY_RULE;
  const [prompt, setPrompt] = useState('');
  const [type, setType] = useState<SmartRuleType>(seed.type);
  const [match, setMatch] = useState<SmartCollectionRule['match']>(seed.match);
  const [sort, setSort] = useState(seed.sort);
  const [limit, setLimit] = useState(seed.limit);
  const [rows, setRows] = useState<Row[]>(() => rowsOf(seed));
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [count, setCount] = useState<number | null>(null);
  const [description, setDescription] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<CollectionVisibility>('private');
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<'draft' | 'save' | null>(null);
  const [draftHidden, setDraftHidden] = useState(false);
  const previewRequest = useRef(0);
  const skipPreviewRef = useRef(false);

  // Only rows that already parse count towards the preview and the saved rule.
  const parsed: SmartRuleCondition[] = [];
  for (const row of rows) {
    const value = parseConditionValue(row.field, row.op, row.raw);
    if (value !== null) parsed.push({ field: row.field, op: row.op, value });
  }
  const rule: SmartCollectionRule = { type, match, conditions: parsed, ...(sort ? { sort } : {}), limit };
  const ruleKey = JSON.stringify(rule);

  // Live count, debounced 300 ms; `live` dies with the dialog or the next edit, so a late answer never lands.
  useEffect(() => {
    if (!open) return undefined;
    if (skipPreviewRef.current) { skipPreviewRef.current = false; return undefined; }
    if (!rule.conditions.length) { setCount(null); return undefined; }
    let live = true;
    const timer = window.setTimeout(() => {
      previewSmartCollectionRules(rule).then((next) => { if (live) setCount(next.count); }, () => { if (live) setCount(null); });
    }, 300);
    return () => { live = false; window.clearTimeout(timer); };
  }, [ruleKey, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const patchRow = (key: number, patch: Partial<Row>) => {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
    setErrors(({ [key]: _gone, ...rest }) => rest);
    setDescription(null);
  };

  async function draft(event: FormEvent) {
    event.preventDefault();
    setBusy('draft');
    setStatus(null);
    try {
      const drafted = await buildSmartCollectionRules(prompt.trim());
      skipPreviewRef.current = true; // the draft carries its own preview
      setType(drafted.rule.type);
      setMatch(drafted.rule.match);
      setSort(drafted.rule.sort);
      setLimit(drafted.rule.limit);
      setRows(rowsOf(drafted.rule));
      setErrors({});
      setCount(drafted.preview.count);
      setDescription(drafted.description);
      if (!name.trim()) setName(prompt.trim().slice(0, 80));
    } catch (failure) {
      if (failure instanceof ApiRequestError && failure.status === 404) { setDraftHidden(true); setStatus('Describing a collection needs an AI endpoint. Add rules below instead.'); }
      else setStatus(failure instanceof ApiRequestError && failure.status === 422 ? 'Lumina could not turn that into rules. Try other words, or add rules below.' : errorMessage(failure, 'Drafting did not work. Add rules below instead.'));
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    const next: Record<number, string> = {};
    for (const row of rows) {
      if (parseConditionValue(row.field, row.op, row.raw) === null) next[row.key] = `Enter a ${RULE_FIELDS[row.field].numeric ? 'number' : 'value'} for ${RULE_FIELDS[row.field].label.toLowerCase()}.`;
    }
    const nameMissing = mode === 'new' && !name.trim();
    setErrors(next);
    setNameError(nameMissing ? 'Name this collection.' : null);
    if (nameMissing || Object.keys(next).length) return;
    setBusy('save');
    setStatus(null);
    try {
      onCreated(mode === 'edit' && collection ? await setSmartCollectionRules(collection.id, rule) : await create({ name: name.trim(), visibility, rules: rule }));
    } catch (failure) {
      setStatus(errorMessage(failure, 'Lumina could not save this collection.'));
      setBusy(null);
    }
  }

  return (
    <Dialog
      busy={busy === 'save'}
      footer={<><Button onClick={onCancel} variant="quiet">Cancel</Button><Button busy={busy === 'save'} onClick={() => void save()} variant="primary">Save</Button></>}
      onClose={onCancel}
      open={open}
      size="lg"
      title={mode === 'new' ? 'New smart collection' : 'Edit rules'}
    >
      <div className="g-builder">
        {mode === 'new' ? <Field error={nameError} label="Name">{(ids) => <Input {...fieldProps(ids)} maxLength={255} onChange={(event) => { setName(event.target.value); setNameError(null); }} value={name} />}</Field> : null}
        {aiAvailable && !draftHidden ? (
          <form className="g-builder-suggestion" onSubmit={(event) => void draft(event)}>
            <p className="g-label">Suggested by your assistant</p>
            <Field label="Describe it">{(ids) => <Textarea {...fieldProps(ids)} maxLength={500} onChange={(event) => setPrompt(event.target.value)} placeholder="Unwatched comedies from the 90s under two hours" rows={2} value={prompt} />}</Field>
            <Button busy={busy === 'draft'} disabled={!prompt.trim()} icon={<Sparkles />} type="submit">Draft rules</Button>
          </form>
        ) : null}
        <Field label="Show">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => setType(event.target.value as SmartRuleType)} value={type}>{TYPES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select>}</Field>
        <SegmentedControl legend="Match" onChange={setMatch} options={[{ value: 'all', label: 'All rules' }, { value: 'any', label: 'Any rule' }]} value={match} />
        <ol className="g-builder-rules">
          {rows.map((row, index) => {
            const choices = RULE_FIELDS[row.field].values;
            return (
              <li className="g-builder-rule" key={row.key}>
                <Field hideLabel label={`Field ${index + 1}`}>{(ids) => <Select {...fieldProps(ids)} onChange={(event) => { const field = event.target.value as SmartRuleField; patchRow(row.key, { field, op: RULE_FIELDS[field].ops[0], raw: '' }); }} value={row.field}>{(Object.keys(RULE_FIELDS) as SmartRuleField[]).map((key) => <option key={key} value={key}>{RULE_FIELDS[key].label}</option>)}</Select>}</Field>
                <Field hideLabel label={`Condition ${index + 1}`}>{(ids) => <Select {...fieldProps(ids)} onChange={(event) => patchRow(row.key, { op: event.target.value as SmartRuleOp })} value={row.op}>{RULE_FIELDS[row.field].ops.map((key) => <option key={key} value={key}>{OP_LABELS[key]}</option>)}</Select>}</Field>
                <Field error={errors[row.key] ?? null} hideLabel label={`Value ${index + 1}`}>{(ids) => (choices
                  ? <Select {...fieldProps(ids)} onChange={(event) => patchRow(row.key, { raw: event.target.value })} value={row.raw}><option value="">Choose…</option>{choices.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select>
                  : <Input {...fieldProps(ids)} inputMode={RULE_FIELDS[row.field].numeric ? 'numeric' : undefined} maxLength={200} onChange={(event) => patchRow(row.key, { raw: event.target.value })} placeholder={row.op === 'in' || row.op === 'not_in' ? 'Comma, separated' : undefined} value={row.raw} />)}</Field>
                <IconButton icon={<X />} label={`Remove rule ${index + 1}`} onClick={() => setRows((current) => current.filter((entry) => entry.key !== row.key))} />
              </li>
            );
          })}
        </ol>
        <TextButton icon={<Plus />} onClick={() => setRows((current) => [...current, blankRow()])}>Add rule</TextButton>
        {description ? <p className="g-builder-note">{description}</p> : null}
        {count === null ? null : count === 0 ? <StatusText tone="muted">Nothing matches these rules.</StatusText> : <p aria-live="polite" className="g-builder-count">Matches {count} {count === 1 ? 'title' : 'titles'}</p>}
        {mode === 'new' ? <Field label="Who can see it">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => setVisibility(event.target.value as CollectionVisibility)} value={visibility}><option value="private">Only me</option><option value="shared">Household</option></Select>}</Field> : null}
        {status ? <p className="g-builder-note" role="status">{status}</p> : null}
      </div>
    </Dialog>
  );
}
