import { useState } from 'react';

import type { TitleType } from '../../../types';
import { Trash2 } from 'lucide-react';
import { Button, Field, IconButton, Input } from '../../../ui';
import type { TabProps } from './editorModel';
import './people.css';

const PATTERNS: Record<string, { re: RegExp; message: string }> = {
  Tmdb: { re: /^\d{1,10}$/, message: 'Use up to 10 digits.' },
  Tvdb: { re: /^\d{1,10}$/, message: 'Use up to 10 digits.' },
  Imdb: { re: /^tt\d{7,10}$/, message: 'Use tt followed by 7 to 10 digits.' },
};
const KEY_RE = /^[A-Za-z][A-Za-z0-9]{0,31}$/;
const LABELS: Record<string, string> = { Tmdb: 'TMDB', Imdb: 'IMDb', Tvdb: 'TVDB' };

function linkFor(key: string, value: string, type: TitleType): string | null {
  const media = type === 'movie' ? 'movie' : type === 'series' ? 'tv' : null;
  if (key === 'Imdb') return `https://www.imdb.com/title/${value}/`;
  if (!media) return null;
  if (key === 'Tmdb') return `https://www.themoviedb.org/${media}/${value}`;
  if (key === 'Tvdb') return `https://thetvdb.com/dereferrer/${media === 'tv' ? 'series' : 'movie'}/${value}`;
  return null;
}

export default function IdsTab({ doc, draft, user, setField }: TabProps) {
  const base = (doc.fields.provider_ids?.value as Record<string, string> | null | undefined) ?? {};
  const [ids, setIds] = useState<Record<string, string>>(() => (draft[doc.title_id]?.changes.provider_ids?.value as Record<string, string> | undefined) ?? base);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');
  const [addError, setAddError] = useState<string | null>(null);
  const owner = user.role === 'admin';
  const keys = [...['Tmdb', 'Imdb', 'Tvdb'], ...Object.keys(ids).filter((key) => !PATTERNS[key])];

  const commit = (next: Record<string, string>) => {
    setIds(next);
    setField(doc.title_id, 'provider_ids', Object.fromEntries(Object.entries(next).filter(([, value]) => value !== '')), base);
  };
  const edit = (key: string, value: string) => {
    const rule = PATTERNS[key];
    if (rule && value !== '' && !rule.re.test(value)) {
      setIds({ ...ids, [key]: value });
      setErrors({ ...errors, [key]: rule.message });
      return;
    }
    setErrors(({ [key]: _gone, ...rest }) => rest);
    commit({ ...ids, [key]: value });
  };
  const add = () => {
    if (!KEY_RE.test(newKey)) return setAddError('Use letters and digits, starting with a letter.');
    const taken = [...Object.keys(PATTERNS), ...Object.keys(ids)].some((key) => key.toLowerCase() === newKey.toLowerCase());
    if (taken) return setAddError('That ID already has its own field above.');
    if (newValue === '' || newValue.length > 64) return setAddError('Enter a value up to 64 characters.');
    if (Object.keys(ids).filter((key) => ids[key] !== '').length >= 10 && !(newKey in ids)) return setAddError('A title can have up to 10 IDs.');
    setAddError(null);
    commit({ ...ids, [newKey]: newValue });
    setNewKey('');
    setNewValue('');
  };

  return (
    <section className="ed-ids">
      {keys.map((key) => {
        const value = ids[key] ?? '';
        const readOnly = key === 'Tmdb' && !owner;
        const link = value && !errors[key] ? linkFor(key, value, doc.type) : null;
        const changedTmdb = key === 'Tmdb' && owner && value !== (base.Tmdb ?? '');
        const hint = changedTmdb
          ? 'Changing the TMDB ID re-identifies this title: details from the old match are removed and fetched again.'
          : readOnly ? 'Only vault owners can change the TMDB match.' : undefined;
        return (
          <div className="ed-row" key={key}>
            <Field error={errors[key] ?? null} hint={hint} label={LABELS[key] ?? key}>
              {(props) => <Input id={props.inputId} maxLength={64} aria-describedby={props.describedBy} aria-invalid={props.invalid || undefined} onChange={(event) => edit(key, event.target.value.trim())} readOnly={readOnly} value={value} />}
            </Field>
            {link ? <a href={link} rel="noreferrer noopener" target="_blank">Open {LABELS[key] ?? key}</a> : null}
            {!PATTERNS[key] ? <IconButton icon={<Trash2 />} label={`Remove ${key}`} onClick={() => commit(Object.fromEntries(Object.entries(ids).filter(([name]) => name !== key)))} /> : null}
          </div>
        );
      })}
      <div className="ed-row">
        <Field error={addError} label="Other ID name">{(props) => <Input id={props.inputId} aria-describedby={props.describedBy} aria-invalid={props.invalid || undefined} onChange={(event) => setNewKey(event.target.value)} value={newKey} />}</Field>
        <Field label="Other ID value">{(props) => <Input id={props.inputId} onChange={(event) => setNewValue(event.target.value)} value={newValue} />}</Field>
        <Button onClick={add} variant="quiet">Add another ID</Button>
      </div>
    </section>
  );
}
