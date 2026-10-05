import { X } from 'lucide-react';
import { type KeyboardEvent, useId, useState } from 'react';

import { IconButton, Input } from '../../../ui';

export interface ChipInputProps {
  id: string;
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  suggestions?: readonly string[];
  maxItems?: number;
  maxLength?: number;
  disabled?: boolean;
}

/** The one chip input for genres, tags and studios (editor Details tab and the bulk dialog). Keyboard only works. */
export function ChipInput({ id, label, values, onChange, suggestions = [], maxItems = 30, maxLength = 60, disabled = false }: ChipInputProps) {
  const [text, setText] = useState('');
  const listId = useId();

  function commit(raw: string) {
    const next = raw.trim().slice(0, maxLength);
    setText('');
    if (!next || values.length >= maxItems || values.some((value) => value.toLowerCase() === next.toLowerCase())) return;
    onChange([...values, next]);
  }
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); commit(text); }
    else if (event.key === 'Backspace' && !text && values.length) onChange(values.slice(0, -1));
  }
  return (
    <div aria-label={label} className="ed-chipbox" role="group">
      <ul className="ed-chips">
        {values.map((value) => (
          <li className="ed-chip" key={value}>
            <span>{value}</span>
            {disabled ? null : <IconButton icon={<X />} label={`Remove ${value}`} onClick={() => onChange(values.filter((item) => item !== value))} />}
          </li>
        ))}
      </ul>
      <Input
        disabled={disabled}
        enterKeyHint="done"
        id={id}
        list={listId}
        onBlur={() => commit(text)}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        value={text}
      />
      <datalist id={listId}>{suggestions.map((value) => <option key={value} value={value} />)}</datalist>
    </div>
  );
}
