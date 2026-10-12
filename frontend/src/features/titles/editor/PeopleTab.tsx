import { ArrowDown, ArrowUp, GripVertical, Lock, LockOpen, Trash2, UserPen } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';

import { searchMetadataPeople } from '../../../api';
import type { PersonRef, PersonSuggestion, PersonType } from '../../../types';
import { Button, IconButton, Input, Select } from '../../../ui';
import { forgetTitle } from '../../gallery/titleCache';
import type { TabProps } from './editorModel';
import { PersonDialog } from './PersonDialog';
import './people.css';

type KeyedPerson = PersonRef & { key: number };
const strip = ({ key: _key, ...person }: KeyedPerson): PersonRef => person; // eslint-disable-line @typescript-eslint/no-unused-vars

const TYPES: { value: PersonType; label: string }[] = [
  { value: 'Actor', label: 'Actor' }, { value: 'GuestStar', label: 'Guest star' }, { value: 'Director', label: 'Director' },
  { value: 'Writer', label: 'Writer' }, { value: 'Creator', label: 'Creator' }, { value: 'Producer', label: 'Producer' }, { value: 'Composer', label: 'Composer' },
];

/** Moves one item; an out-of-range move returns the same array. */
export function moveItem<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return list;
  const next = [...list];
  next.splice(to, 0, next.splice(from, 1)[0]);
  return next;
}

export default function PeopleTab({ doc, draft, setField, togglePin, reload }: TabProps) {
  const base = (doc.fields.people?.value as PersonRef[] | null | undefined) ?? [];
  const entry = draft[doc.title_id];
  // Rows live here so a blank new row stays on screen; only named rows enter the draft.
  const nextKey = useRef(0);
  const keyed = (list: PersonRef[]) => list.map((person) => ({ ...person, key: nextKey.current++ }));
  const [people, setPeople] = useState<KeyedPerson[]>(() => keyed((entry?.changes.people?.value as PersonRef[] | undefined) ?? base));
  const locked = entry?.pin.includes('people') ? !doc.fields.people?.locked : Boolean(doc.fields.people?.locked);
  const listId = useId();
  const [found, setFound] = useState<PersonSuggestion[]>([]);
  const [status, setStatus] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const focusLast = useRef(false);
  const list = useRef<HTMLUListElement>(null);
  const drag = useRef(-1);
  const focusMoved = useRef<number | null>(null);
  const searchTimer = useRef(0);
  const searchSeq = useRef(0);
  useEffect(() => () => window.clearTimeout(searchTimer.current), []);

  useEffect(() => {
    if (focusLast.current) {
      focusLast.current = false;
      list.current?.querySelector<HTMLInputElement>('li:last-child input')?.focus();
    }
    if (focusMoved.current !== null) {
      // The moved row keeps focus on its own move button; at an edge that button is disabled, so use the other one.
      const row = list.current?.querySelector<HTMLElement>(`li[data-key="${focusMoved.current}"]`);
      focusMoved.current = null;
      const buttons = [...(row?.querySelectorAll<HTMLButtonElement>('button[data-move]') ?? [])];
      (buttons.find((button) => button.dataset.move === moved.current && !button.disabled) ?? buttons.find((button) => !button.disabled))?.focus();
    }
  });
  const moved = useRef<'up' | 'down'>('up');

  // The title's own credits first (they keep their person_id), then known people from the server.
  const known = new Map<string, string>();
  for (const person of base) if (person.person_id) known.set(person.name, person.person_id);
  for (const person of found) if (!known.has(person.name)) known.set(person.name, person.person_id);

  const write = (next: KeyedPerson[]) => {
    setPeople(next);
    setField(doc.title_id, 'people', next.filter((person) => person.name.trim() !== '').map(strip), base);
  };
  const update = (index: number, patch: Partial<PersonRef>) => write(people.map((person, i) => (i === index ? { ...person, ...patch } : person)));
  const move = (from: number, to: number, via: 'up' | 'down' = to < from ? 'up' : 'down') => {
    focusMoved.current = people[from].key;
    moved.current = via;
    write(moveItem(people, from, to));
    setStatus(`${people[from].name} moved to position ${to + 1} of ${people.length}.`);
  };
  // Debounced, and an older response never overwrites a newer one.
  const search = (text: string) => {
    window.clearTimeout(searchTimer.current);
    if (text.trim().length < 2) return;
    searchTimer.current = window.setTimeout(() => {
      const seq = ++searchSeq.current;
      searchMetadataPeople(text.trim(), 8).then((result) => { if (seq === searchSeq.current) setFound(result); }, () => undefined);
    }, 250);
  };

  return (
    <section className="ed-people">
      <div className="ed-people-head">
        <h2>Cast and crew</h2>
        <IconButton
          icon={locked ? <Lock /> : <LockOpen />}
          label={locked ? 'Unlock Cast' : 'Lock Cast'}
          onClick={() => togglePin(doc.title_id, 'people')}
          pressed={locked}
        />
      </div>
      <datalist id={listId}>{[...known.keys()].map((name) => <option key={name} value={name} />)}</datalist>
      <ul ref={list}>
        {people.map((person, index) => (
          <li
            data-key={person.key}
            key={person.key}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => { if (drag.current >= 0) move(drag.current, index); drag.current = -1; }}
          >
            <button aria-hidden="true" aria-label={`Drag ${person.name || 'person'}`} className="ed-people-grip" draggable onDragStart={() => { drag.current = index; }} tabIndex={-1} type="button"><GripVertical aria-hidden="true" /></button>
            <Input
              aria-label={`Name ${index + 1}`}
              list={listId}
              onChange={(event) => {
                const name = event.target.value;
                search(name);
                update(index, { name, person_id: known.get(name) ?? null });
              }}
              value={person.name}
            />
            <Input aria-label={`Role ${index + 1}`} onChange={(event) => update(index, { role: event.target.value })} value={person.role} />
            <Select aria-label={`Type ${index + 1}`} onChange={(event) => update(index, { type: event.target.value as PersonType })} value={person.type}>
              {TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
            </Select>
            <IconButton data-move="up" disabled={index === 0} icon={<ArrowUp />} label={`Move up ${person.name}`} onClick={() => move(index, index - 1)} />
            <IconButton data-move="down" disabled={index === people.length - 1} icon={<ArrowDown />} label={`Move down ${person.name}`} onClick={() => move(index, index + 1)} />
            <IconButton disabled={!person.person_id} icon={<UserPen />} label={`Edit ${person.name || 'person'} everywhere`} onClick={() => setEditing(person.person_id)} />
            <IconButton icon={<Trash2 />} label={`Remove ${person.name}`} onClick={() => write(people.filter((_, i) => i !== index))} />
          </li>
        ))}
      </ul>
      <Button onClick={() => { focusLast.current = true; write([...people, ...keyed([{ person_id: null, name: '', role: '', type: 'Actor' }])]); }} variant="quiet">Add person</Button>
      <p aria-live="polite" className="sr-only" role="status">{status}</p>
      <PersonDialog onChanged={() => { forgetTitle(doc.title_id); reload(); }} onClose={() => setEditing(null)} personId={editing} />
    </section>
  );
}
