import { useEffect, useRef, useState } from 'react';

import type { EpisodeTableRow } from '../../../types';
import { Dialog, Field, fieldProps, Input, Textarea } from '../../../ui';
import { parseInput, sameValue, type TabProps } from './editorModel';

type CellKey = 'index_number' | 'index_number_end' | 'name' | 'premiered' | 'runtime_minutes' | 'community_rating' | 'overview';
export interface Column { key: CellKey; label: string; head: string; kind: 'int' | 'decimal' | 'date' | 'text' | 'long' }

/** Column order. */
export const COLUMNS: Column[] = [
  { key: 'index_number', label: 'Number', head: '№', kind: 'int' },
  { key: 'index_number_end', label: 'Ends at', head: 'Ends at', kind: 'int' },
  { key: 'name', label: 'Name', head: 'Name', kind: 'text' },
  { key: 'premiered', label: 'Air date', head: 'Air date', kind: 'date' },
  { key: 'runtime_minutes', label: 'Runtime', head: 'Runtime', kind: 'int' },
  { key: 'community_rating', label: 'Rating', head: 'Rating', kind: 'decimal' },
  { key: 'overview', label: 'Overview', head: 'Overview', kind: 'long' },
];

export const cellValue = (row: EpisodeTableRow, draft: TabProps['draft'], key: CellKey): unknown => draft[row.title_id]?.changes[key]?.value ?? (key in (draft[row.title_id]?.changes ?? {}) ? null : row[key]);

type Parsed = { value: unknown } | { error: string };
function validate(column: Column, raw: string, number: number | null): Parsed {
  const value = parseInput(column.kind, raw);
  const whole = (min: number, max: number, message: string): Parsed => (typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max ? { value } : { error: message });
  switch (column.key) {
    case 'name': return value === null ? { error: "A name can't be empty." } : { value };
    case 'index_number': return whole(0, 9999, 'Use a whole number from 0 to 9999.');
    case 'index_number_end': return value === null ? { value } : whole(number ?? 0, 9999, 'Use a whole number that is not below the number.');
    case 'runtime_minutes': return value === null ? { value } : whole(1, 2000, 'Use whole minutes from 1 to 2000.');
    case 'community_rating': return value === null || (typeof value === 'number' && value >= 0 && value <= 10) ? { value } : { error: 'Use a rating from 0 to 10.' };
    case 'premiered': return value === null || /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? { value } : { error: 'Use a full date.' };
    default: return { value };
  }
}

/** Text state for one cell: valid input goes to the draft, invalid input stays local with a message. */
export function useCell(row: EpisodeTableRow, column: Column, draft: TabProps['draft'], setField: TabProps['setField']) {
  const current = cellValue(row, draft, column.key);
  const number = (cellValue(row, draft, 'index_number') as number | null) ?? null;
  const show = (value: unknown) => (value === null || value === undefined ? '' : String(value));
  const [text, setText] = useState(show(current));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const parsed = validate(column, text, number);
    if ('value' in parsed && !sameValue(parsed.value, current)) { setText(show(current)); setError(null); }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only an outside change of the value resets the text
  }, [current]);
  const onChange = (raw: string) => {
    setText(raw);
    const parsed = validate(column, raw, number);
    // Invalid input must not leave an earlier valid prefix in the draft: put the cell back to its loaded value.
    if ('error' in parsed) { setError(parsed.error); setField(row.title_id, column.key, row[column.key], row[column.key]); return; }
    setError(null);
    setField(row.title_id, column.key, parsed.value, row[column.key]);
  };
  return { text, error, onChange, edited: column.key in (draft[row.title_id]?.changes ?? {}) };
}

function SheetField({ row, column, draft, setField, inputRef }: { row: EpisodeTableRow; column: Column; draft: TabProps['draft']; setField: TabProps['setField']; inputRef?: React.RefObject<HTMLInputElement | null> }) {
  const cell = useCell(row, column, draft, setField);
  return (
    <Field error={cell.error} label={column.label}>
      {(ids) => column.kind === 'long'
        ? <Textarea {...fieldProps(ids)} onChange={(event) => cell.onChange(event.target.value)} rows={4} value={cell.text} />
        : <Input {...fieldProps(ids)} inputMode={column.kind === 'int' || column.kind === 'decimal' ? 'decimal' : undefined} onChange={(event) => cell.onChange(event.target.value)} ref={inputRef} type={column.kind === 'date' ? 'date' : 'text'} value={cell.text} />}
    </Field>
  );
}

/** The phone editor for one episode: the same draft, one field per line. */
export default function EpisodeSheet({ row, draft, setField, onClose }: { row: EpisodeTableRow; draft: TabProps['draft']; setField: TabProps['setField']; onClose: () => void }) {
  const first = useRef<HTMLInputElement>(null);
  const name = String(cellValue(row, draft, 'name') ?? row.name);
  return (
    <Dialog initialFocus={first} onClose={onClose} open size="sheet" title={name}>
      {COLUMNS.slice().sort((a, b) => (a.key === 'name' ? -1 : b.key === 'name' ? 1 : 0)).map((column) => (
        <SheetField column={column} draft={draft} inputRef={column.key === 'name' ? first : undefined} key={column.key} row={row} setField={setField} />
      ))}
    </Dialog>
  );
}
