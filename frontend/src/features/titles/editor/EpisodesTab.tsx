import { Lock, LockOpen } from 'lucide-react';
import { useState } from 'react';

import { getTitle, listMetadataEpisodes } from '../../../api';
import type { EpisodeTableRow } from '../../../types';
import { Button, EmptyState, ErrorState, IconButton, SegmentedControl, Skeleton, VisuallyHidden } from '../../../ui';
import { orderSeasons, seasonLabel, useFetched } from '../titleModel';
import { COLUMNS, cellValue, type Column, useCell } from './EpisodeSheet';
import EpisodeSheet from './EpisodeSheet';
import type { TabProps } from './editorModel';
import { useNarrow } from './useNarrow';
import './episodes.css';

const CAP = 500;

function Cell({ row, column, index, draft, setField }: { row: EpisodeTableRow; column: Column; index: number; draft: TabProps['draft']; setField: TabProps['setField'] }) {
  const cell = useCell(row, column, draft, setField);
  const [open, setOpen] = useState(false);
  const label = `${column.label} of ${row.name}`; // the loaded name, so a label does not change while typing
  const common = { 'aria-label': label, 'aria-invalid': cell.error ? true : undefined, 'data-row': index, 'data-col': column.key, title: cell.error ?? undefined };
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    (event.currentTarget.closest('table')?.querySelector(`[data-row="${index + 1}"][data-col="${column.key}"]`) as HTMLElement | null)?.focus();
  };
  let control;
  if (column.kind === 'long') {
    control = open || cell.error
      ? <textarea {...common} autoFocus className="g-input g-textarea ed-cell-input" onBlur={() => setOpen(false)} onChange={(event) => cell.onChange(event.target.value)} rows={3} value={cell.text} />
      : <button {...common} className="ed-cell-text" onFocus={() => setOpen(true)} type="button">{cell.text}</button>;
  } else {
    control = <input {...common} className="g-input ed-cell-input" onChange={(event) => cell.onChange(event.target.value)} onKeyDown={onKeyDown} type={column.kind === 'date' ? 'date' : 'text'} value={cell.text} />;
  }
  return (
    <td className={cell.edited ? 'ed-cell is-edited' : 'ed-cell'}>
      {control}
      {cell.edited ? <VisuallyHidden>Edited</VisuallyHidden> : null}
      {row.locked_fields.includes(column.key) ? <><Lock aria-hidden="true" className="ed-cell-lock" size={12} /><VisuallyHidden>Locked</VisuallyHidden></> : null}
    </td>
  );
}

export default function EpisodesTab({ doc, draft, setField, setItemLock, episodesRevision }: TabProps) {
  const narrow = useNarrow();
  const isSeason = doc.type === 'season';
  const seriesId = isSeason ? (doc.parent?.id ?? '') : doc.title_id;
  const [retries, setRetries] = useState(0);
  const children = useFetched(isSeason ? null : `seasons:${seriesId}`, async () => orderSeasons((await getTitle(seriesId)).children.filter((child) => child.type === 'season')), retries);
  const [chosen, setChosen] = useState<string | null>(null);
  const seasonId = isSeason ? doc.title_id : (chosen ?? children.data?.[0]?.id ?? null);
  const table = useFetched(seasonId ? `${seriesId}:${seasonId}` : null, () => listMetadataEpisodes(seriesId, seasonId as string), episodesRevision + retries);
  const [sheetId, setSheetId] = useState<string | null>(null);

  const seasons = table.data?.seasons ?? (children.data ?? []).map((child) => ({ id: child.id, name: child.name, index_number: child.index_number ?? null }));
  const rows = [...(table.data?.episodes ?? [])].sort((a, b) => (a.index_number ?? 0) - (b.index_number ?? 0));
  const sheetRow = rows.find((row) => row.title_id === sheetId) ?? null;
  const locked = (row: EpisodeTableRow) => draft[row.title_id]?.locked ?? row.locked;
  const rowName = (row: EpisodeTableRow) => String(cellValue(row, draft, 'name') ?? row.name);

  let body;
  if (table.error || children.error) body = <ErrorState onRetry={() => setRetries((count) => count + 1)} title="Lumina couldn't load the episodes." />;
  else if (!seasonId && !children.loading) body = <EmptyState title="No episodes in this season yet." />;
  else if (!table.data) body = <Skeleton count={4} label="Loading episodes" shape="row" />;
  else if (!rows.length) body = <EmptyState title="No episodes in this season yet." />;
  else if (narrow) {
    body = (
      <ul className="ed-ep-list">
        {rows.map((row) => (
          <li key={row.title_id}>
            <button className="ed-ep-row" onClick={() => setSheetId(row.title_id)} type="button">
              <span>{String(cellValue(row, draft, 'index_number') ?? '')}</span>
              <span>{rowName(row)}</span>
              {draft[row.title_id] ? <span className="ed-ep-edited">Edited</span> : null}
            </button>
          </li>
        ))}
      </ul>
    );
  } else {
    body = (
      <table className="ed-table">
        <thead><tr>{COLUMNS.map((column) => <th key={column.key} scope="col">{column.head}</th>)}<th scope="col"><VisuallyHidden>Lock</VisuallyHidden></th></tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.title_id}>
              {COLUMNS.map((column) => <Cell column={column} draft={draft} index={index} key={column.key} row={row} setField={setField} />)}
              <td><IconButton icon={locked(row) ? <Lock size={16} /> : <LockOpen size={16} />} label={`${locked(row) ? 'Unlock' : 'Lock'} ${rowName(row)}`} onClick={() => setItemLock(row.title_id, !locked(row), row.locked)} pressed={locked(row)} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return (
    <section className="ed-episodes">
      {!isSeason && seasons.length > 1 ? (
        <SegmentedControl legend="Season" onChange={setChosen} options={seasons.map((season) => ({ value: season.id, label: season.index_number === null ? season.name : seasonLabel(season.index_number) }))} value={seasonId ?? ''} />
      ) : null}
      {body}
      {rows.length >= CAP ? <p className="g-field-hint">Only the first 500 episodes are shown.</p> : null}
      {sheetRow ? <EpisodeSheet draft={draft} onClose={() => setSheetId(null)} row={sheetRow} setField={setField} /> : null}
    </section>
  );
}
