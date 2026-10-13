import { Search, Upload } from 'lucide-react';
import { type ReactElement, useEffect, useMemo, useRef, useState } from 'react';

import * as api from '../../api';
import { Artwork } from '../../Artwork';
import type { BulkOp, TitleMetadataDoc, TitleSummary } from '../../types';
import { Button, EmptyState, ErrorState, Field, fieldProps, Input, Masthead, Select, SegmentedControl, Skeleton, TextButton } from '../../ui';
import { ChipInput } from '../titles/editor/ChipInput';
import { FIELD_META, MUSIC_RELEASE_TYPES } from '../titles/editor/editorModel';
import { useVocabulary } from '../titles/editor/useVocabulary';
import '../titles/editor/fields.css';
import './musicCuration.css';

export interface MusicCurationProps {
  user: { id: string; role: string; can_edit_details?: boolean };
  onOpenTitle: (title: TitleSummary) => void;
  onChanged?: () => void;
}

type MusicKind = 'album' | 'artist';
type ListField = 'genres' | 'tags' | 'styles' | 'mood';
type CurationField = ListField | 'release_type' | 'year' | 'label' | 'edition';
type CurationOperation = 'add' | 'remove' | 'set';

interface CurationDraft {
  field: CurationField;
  operation: CurationOperation;
  values: string[];
  scalar: string;
}

interface ReadDoc {
  id: string;
  doc: TitleMetadataDoc | null;
  reason?: string;
}

interface MetadataPreview {
  kind: 'metadata';
  ids: string[];
  draft: CurationDraft;
  docs: TitleMetadataDoc[];
  skipped: CurationSkip[];
  summary: string;
}

interface ArtworkContext {
  id: string;
  name: string;
  url: string | null;
  tag: string | null;
}

interface ArtworkPreview {
  kind: 'artwork';
  ids: string[];
  file: File;
  base: Record<string, string | null>;
  before: ArtworkContext[];
  skipped: CurationSkip[];
  summary: string;
}

type CurationPreview = MetadataPreview | ArtworkPreview;

interface CurationSkip {
  title_id: string;
  reason: string;
}

interface CurationResult {
  applied: number;
  skipped: CurationSkip[];
  batchId: string | null;
  undone?: { restored: number; skipped: CurationSkip[] };
}

type UploadMusicArtwork = (
  titleIds: string[],
  image: Blob,
  base: Record<string, string | null>,
  imageType?: 'Primary' | 'Backdrop',
) => Promise<{ batch_id: string | null; applied: number; skipped: CurationSkip[] }>;

// The artwork export is being added alongside this surface. Keeping the optional shape here lets this branch remain
// type-safe while the parent wires the concrete API export; production never falls back to a fake upload.
const apiWithArtwork = api as typeof api & { uploadMusicArtwork?: UploadMusicArtwork };

const FIELD_OPTIONS: Array<{ field: CurationField; label: string; albumOnly?: boolean }> = [
  { field: 'genres', label: 'Genres' },
  { field: 'tags', label: 'Tags' },
  { field: 'styles', label: 'Styles' },
  { field: 'mood', label: 'Mood' },
  { field: 'release_type', label: 'Release type', albumOnly: true },
  { field: 'year', label: 'Year', albumOnly: true },
  { field: 'label', label: 'Label', albumOnly: true },
  { field: 'edition', label: 'Edition', albumOnly: true },
];
const LIST_FIELDS = new Set<ListField>(['genres', 'tags', 'styles', 'mood']);
const REASON_LABELS: Record<string, string> = {
  not_found: 'Title is no longer available.',
  locked_item: 'Item is locked.',
  wrong_type: 'Field does not apply to this title.',
  changed_since: 'Changed after the preview.',
  metadata_unavailable: 'Current metadata could not be read.',
  owner_only: 'Only a vault owner can undo this change.',
  not_visible: 'Title is no longer visible.',
};

const plural = (count: number, singular: string, multiple = `${singular}s`) => `${count} ${count === 1 ? singular : multiple}`;
const fieldLabel = (field: CurationField) => FIELD_META[field]?.label ?? FIELD_OPTIONS.find((option) => option.field === field)?.label ?? field;
const selectedIdsOf = (selected: ReadonlySet<string>) => [...selected];
const listField = (field: CurationField): field is ListField => LIST_FIELDS.has(field as ListField);
const endpointOperation = (draft: CurationDraft) => draft.field === 'release_type' || (listField(draft.field) && draft.operation !== 'set');

function initialDraft(kind: MusicKind): CurationDraft {
  return { field: kind === 'album' ? 'genres' : 'genres', operation: 'add', values: [], scalar: '' };
}

function valueLabel(draft: CurationDraft): string {
  if (listField(draft.field)) return draft.values.length ? draft.values.join(', ') : '(no values)';
  if (draft.field === 'release_type') return MUSIC_RELEASE_TYPES.find((option) => option.value === draft.scalar)?.label ?? '(not set)';
  return draft.scalar.trim() || '(empty)';
}

function previewSummary(draft: CurationDraft): string {
  const verb = draft.operation === 'add' ? 'Add' : draft.operation === 'remove' ? 'Remove' : 'Set';
  return `${verb} ${fieldLabel(draft.field)}: ${valueLabel(draft)}`;
}

function readyForPreview(draft: CurationDraft): boolean {
  if (listField(draft.field)) return draft.values.length > 0;
  if (draft.field === 'year') {
    const year = Number(draft.scalar);
    return Number.isInteger(year) && year >= 1870 && year <= 2100;
  }
  return draft.scalar.trim().length > 0;
}

function bulkOp(draft: CurationDraft): BulkOp {
  if (draft.field === 'release_type') return { op: 'set', field: draft.field, value: draft.scalar };
  return { op: draft.operation, field: draft.field, values: draft.values };
}

function desiredValue(draft: CurationDraft): unknown {
  if (listField(draft.field)) return draft.values;
  if (draft.field === 'year') return Number(draft.scalar);
  return draft.scalar.trim() || null;
}

function skipText(reason: string): string {
  return REASON_LABELS[reason] ?? reason.replace(/_/g, ' ');
}

function loadErrorText(error: unknown): string {
  if (error instanceof api.ApiRequestError && error.status === 403) return 'Ask a household member with detail editing permission to curate music.';
  return 'Lumina could not load the music library.';
}

function actionErrorText(error: unknown): string {
  if (error instanceof api.ApiRequestError && error.status === 403) return 'You do not have permission to change music metadata.';
  return 'Lumina could not save these music changes. Try again.';
}

function rowMeta(title: TitleSummary): string {
  const artist = title.type === 'album' ? title.artist_name || 'Artist not set' : 'Artist profile';
  const year = title.year ? String(title.year) : 'Year not set';
  const count = title.type === 'artist' && title.child_count != null ? plural(title.child_count, 'album') : null;
  return [artist, year, count].filter(Boolean).join(' · ');
}

async function readDocs(ids: string[]): Promise<ReadDoc[]> {
  const out: ReadDoc[] = [];
  // These reads are deliberately sequential. A selection can span many pages, and artwork bases must be current per title.
  for (const id of ids) {
    try {
      out.push({ id, doc: await api.getTitleMetadata(id) });
    } catch {
      out.push({ id, doc: null, reason: 'metadata_unavailable' });
    }
  }
  return out;
}

export function MusicCuration({ user, onOpenTitle, onChanged }: MusicCurationProps): ReactElement {
  const canEdit = user.can_edit_details ?? user.role === 'admin';
  const [kind, setKind] = useState<MusicKind>('album');
  const [rows, setRows] = useState<TitleSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set());
  const [draft, setDraft] = useState<CurationDraft>(() => initialDraft('album'));
  const [preview, setPreview] = useState<CurationPreview | null>(null);
  const [result, setResult] = useState<CurationResult | null>(null);
  const [busy, setBusy] = useState<'load' | 'preview' | 'apply' | 'undo' | 'artwork' | null>(null);
  const [artworkFile, setArtworkFile] = useState<File | null>(null);
  const [artworkUrl, setArtworkUrl] = useState<string | null>(null);
  const artworkInput = useRef<HTMLInputElement>(null);
  const request = useRef(0);
  const [reloadKey, setReloadKey] = useState(0);

  const suggestions = useVocabulary(draft.field === 'genres' || draft.field === 'tags' ? draft.field : undefined);
  const filteredRows = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return rows;
    return rows.filter((title) => [title.name, title.artist_name, rowMeta(title)].filter(Boolean).some((value) => String(value).toLocaleLowerCase().includes(needle)));
  }, [rows, search]);
  const allLoadedSelected = rows.length > 0 && rows.every((title) => selectedIds.has(title.id));
  const availableFields = FIELD_OPTIONS.filter((option) => !option.albumOnly || kind === 'album');

  useEffect(() => {
    if (!artworkFile) {
      setArtworkUrl(null);
      return undefined;
    }
    const url = URL.createObjectURL(artworkFile);
    setArtworkUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [artworkFile]);

  useEffect(() => {
    const token = ++request.current;
    setRows([]);
    setNextCursor(null);
    setTotal(null);
    setSearch('');
    setError(null);
    setLoading(true);
    void api.listTitles({ type: kind, sort: 'name', limit: 60 }).then((page) => {
      if (token !== request.current) return;
      setRows(page.items);
      setNextCursor(page.next_cursor ?? null);
      setTotal(page.total ?? null);
    }, (loadError) => {
      if (token === request.current) setError(loadErrorText(loadError));
    }).finally(() => {
      if (token === request.current) setLoading(false);
    });
  }, [kind, reloadKey]);

  const changeKind = (next: MusicKind) => {
    if (next === kind) return;
    setKind(next);
    setSelectedIds(new Set());
    setDraft(initialDraft(next));
    setPreview(null);
    setResult(null);
    setArtworkFile(null);
  };

  const toggleSelected = (id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setPreview(null);
    setResult(null);
  };

  const selectAllLoaded = () => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (allLoadedSelected) rows.forEach((title) => next.delete(title.id));
      else rows.forEach((title) => next.add(title.id));
      return next;
    });
    setPreview(null);
    setResult(null);
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setPreview(null);
    setResult(null);
  };

  const updateDraft = (patch: Partial<CurationDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setPreview(null);
    setResult(null);
  };

  async function loadMore() {
    if (!nextCursor || loading) return;
    const token = ++request.current;
    setLoading(true);
    try {
      const page = await api.listTitles({ type: kind, sort: 'name', cursor: nextCursor, limit: 60 });
      if (token !== request.current) return;
      setRows((current) => {
        const seen = new Set(current.map((title) => title.id));
        return [...current, ...page.items.filter((title) => !seen.has(title.id))];
      });
      setNextCursor(page.next_cursor ?? null);
      if (page.total != null) setTotal(page.total);
      setError(null);
    } catch (loadError) {
      if (token === request.current) setError(loadErrorText(loadError));
    } finally {
      if (token === request.current) setLoading(false);
    }
  }

  async function previewMetadata() {
    if (!canEdit || !selectedIds.size || !readyForPreview(draft)) return;
    setBusy('preview');
    setError(null);
    const ids = selectedIdsOf(selectedIds);
    let docs: TitleMetadataDoc[] = [];
    let skipped: CurationSkip[] = [];
    if (!endpointOperation(draft)) {
      const read = await readDocs(ids);
      docs = read.flatMap((entry) => entry.doc ? [entry.doc] : []);
      skipped = read.filter((entry) => !entry.doc).map((entry) => ({ title_id: entry.id, reason: entry.reason ?? 'metadata_unavailable' }));
      skipped.push(...docs.filter((doc) => doc.locked).map((doc) => ({ title_id: doc.title_id, reason: 'locked_item' })));
    }
    setPreview({ kind: 'metadata', ids, draft: { ...draft, values: [...draft.values] }, docs, skipped, summary: previewSummary(draft) });
    setBusy(null);
  }

  async function previewArtwork() {
    if (!canEdit || !selectedIds.size || !artworkFile) return;
    setBusy('artwork');
    setError(null);
    const ids = selectedIdsOf(selectedIds);
    const read = await readDocs(ids);
    const base: Record<string, string | null> = {};
    const before: ArtworkContext[] = [];
    const skipped: CurationSkip[] = [];
    for (const entry of read) {
      if (!entry.doc) {
        skipped.push({ title_id: entry.id, reason: entry.reason ?? 'metadata_unavailable' });
        continue;
      }
      const image = entry.doc.images.find((candidate) => candidate.type === 'Primary' && candidate.index === 0);
      base[entry.id] = image?.tag ?? null;
      before.push({ id: entry.id, name: entry.doc.name, url: image?.url ?? null, tag: image?.tag ?? null });
      if (entry.doc.locked) skipped.push({ title_id: entry.id, reason: 'locked_item' });
    }
    setPreview({ kind: 'artwork', ids: Object.keys(base).filter((id) => !skipped.some((entry) => entry.title_id === id && entry.reason === 'locked_item')), file: artworkFile, base, before, skipped, summary: `Replace ${kind === 'album' ? 'album covers' : 'artist portraits'} with the selected image.` });
    setBusy(null);
  }

  async function applyMetadata(current: MetadataPreview): Promise<CurationResult> {
    if (endpointOperation(current.draft)) {
      const response = await api.bulkEditMetadata({ title_ids: current.ids, ops: [bulkOp(current.draft)] });
      return { applied: response.applied, skipped: [...current.skipped, ...response.skipped], batchId: response.batch_id };
    }
    const changes = current.docs.filter((doc) => !doc.locked).map((doc) => ({
      title_id: doc.title_id,
      changes: { [current.draft.field]: { value: desiredValue(current.draft), base: doc.fields[current.draft.field]?.value ?? null } },
    }));
    if (!changes.length) return { applied: 0, skipped: current.skipped, batchId: null };
    const response = await api.saveMetadataEdits(changes);
    const conflicts = response.conflicts.map((conflict) => ({ title_id: conflict.title_id, reason: 'changed_since' }));
    return { applied: response.titles.length, skipped: [...current.skipped, ...conflicts], batchId: response.batch_id };
  }

  async function applyArtwork(current: ArtworkPreview): Promise<CurationResult> {
    if (!apiWithArtwork.uploadMusicArtwork) throw new Error('Artwork upload is not available yet.');
    const response = await apiWithArtwork.uploadMusicArtwork(current.ids, current.file, current.base, 'Primary');
    return { applied: response.applied, skipped: [...current.skipped, ...response.skipped], batchId: response.batch_id };
  }

  async function applyPreview() {
    if (!preview) return;
    setBusy('apply');
    setError(null);
    try {
      const next = preview.kind === 'metadata' ? await applyMetadata(preview) : await applyArtwork(preview);
      setResult(next);
      setPreview(null);
      if (next.applied) onChanged?.();
    } catch (applyError) {
      setError(applyError instanceof Error && applyError.message === 'Artwork upload is not available yet.' ? applyError.message : actionErrorText(applyError));
    } finally {
      setBusy(null);
    }
  }

  async function undo() {
    if (!result?.batchId) return;
    setBusy('undo');
    setError(null);
    try {
      const response = await api.undoMetadataBatch(result.batchId);
      const undone = { restored: response.restored, skipped: response.skipped.map((entry) => ({ title_id: entry.title_id, reason: entry.reason })) };
      setResult((current) => current ? { ...current, undone } : current);
      if (response.restored) onChanged?.();
    } catch (undoError) {
      setError(actionErrorText(undoError));
    } finally {
      setBusy(null);
    }
  }

  const renderRow = (title: TitleSummary) => {
    const checked = selectedIds.has(title.id);
    return (
      <li className={`music-curation-row${checked ? ' is-selected' : ''}`} key={title.id}>
        {canEdit ? <label className="music-curation-check"><input aria-label={`Select ${title.name}`} checked={checked} onChange={() => toggleSelected(title.id)} type="checkbox" /><span aria-hidden="true" /></label> : <span aria-hidden="true" />}
        <Artwork alt="" className="music-curation-art" src={title.poster?.url ?? title.poster_url} />
        <div className="music-curation-copy">
          <strong>{title.name}</strong>
          <span>{rowMeta(title)}</span>
        </div>
        <Button data-focus-item onClick={() => onOpenTitle(title)} variant="quiet">{canEdit ? 'Edit' : 'Open'}</Button>
      </li>
    );
  };

  const selectedLabel = plural(selectedIds.size, 'title');
  const showCount = total == null ? `${rows.length} loaded` : `${filteredRows.length} shown · ${rows.length} loaded · ${total} total`;
  const resultBody = result ? (
    <section aria-labelledby="music-curation-result" className="music-curation-result" role="status">
      <div>
        <h2 id="music-curation-result">{result.undone ? `Undone ${result.undone.restored} ${result.undone.restored === 1 ? 'title' : 'titles'}.` : `Updated ${result.applied} ${result.applied === 1 ? 'title' : 'titles'}.`}</h2>
        {result.batchId && !result.undone ? <Button busy={busy === 'undo'} disabled={busy !== null} onClick={() => void undo()} variant="quiet">Undo</Button> : null}
      </div>
      {result.skipped.length ? <div className="music-curation-skips"><strong>{result.skipped.length} skipped</strong><ul>{result.skipped.map((entry) => <li key={`${entry.title_id}-${entry.reason}`}>{entry.title_id}: {skipText(entry.reason)}</li>)}</ul></div> : null}
      {result.undone?.skipped.length ? <div className="music-curation-skips"><strong>{result.undone.skipped.length} could not be undone</strong><ul>{result.undone.skipped.map((entry) => <li key={`${entry.title_id}-${entry.reason}`}>{entry.title_id}: {skipText(entry.reason)}</li>)}</ul></div> : null}
    </section>
  ) : null;

  return (
    <main className="music-curation surface" aria-busy={loading}>
      <Masthead lede="Clean up albums and artists in batches. Changes stay manual and can be undone." title="Manage music" />
      <SegmentedControl legend="Music type" name="music-curation-type" onChange={changeKind} options={[{ value: 'album', label: 'Albums' }, { value: 'artist', label: 'Artists' }]} value={kind} />
      {!canEdit ? <p className="g-notice music-curation-permission" role="status">You can browse music metadata, but detail editing is not enabled for this account.</p> : null}
      <div className="music-curation-toolbar">
        <Field hint="Searches titles loaded so far; it does not search the full library." label="Search loaded titles">
          {(ids) => <span className="music-curation-search"><Search aria-hidden="true" /><Input {...fieldProps(ids)} onChange={(event) => setSearch(event.target.value)} placeholder="Search loaded titles" type="search" value={search} /></span>}
        </Field>
        <div className="music-curation-toolbar-actions">
          <span aria-live="polite" className="g-setting-note">{showCount}</span>
          {canEdit ? <><Button disabled={!rows.length} onClick={selectAllLoaded} variant="quiet">{allLoadedSelected ? 'Clear loaded' : 'Select all loaded'}</Button><Button disabled={!selectedIds.size} onClick={clearSelection} variant="quiet">Clear selection</Button></> : null}
        </div>
      </div>
      {error && !rows.length ? <ErrorState onRetry={() => setReloadKey((current) => current + 1)} title={error} /> : null}
      {error && rows.length ? <p className="music-curation-error" role="alert">{error}</p> : null}
      {!error && loading && !rows.length ? <Skeleton count={6} label="Loading music" shape="row" /> : null}
      {!loading && !error && !filteredRows.length ? <EmptyState body={search ? 'Search only covers titles loaded so far. Load more, or clear the search.' : `No ${kind}s are visible in your library.`} title={search ? 'Nothing matches this loaded page.' : `No ${kind}s yet.`} /> : null}
      {filteredRows.length ? <ul aria-label={`${kind === 'album' ? 'Albums' : 'Artists'} to curate`} className="music-curation-list">{filteredRows.map(renderRow)}</ul> : null}
      {nextCursor ? <Button busy={loading} disabled={loading} onClick={() => void loadMore()} variant="quiet">Load more</Button> : null}

      {canEdit && selectedIds.size ? (
        <section aria-labelledby="music-curation-bulk" className="music-curation-bulk">
          <header><div><h2 id="music-curation-bulk">Bulk edit</h2><p className="g-setting-note">{selectedLabel} selected across loaded pages. Selecting a field never saves.</p></div><TextButton onClick={clearSelection}>Clear selection</TextButton></header>
          <div className="music-curation-form">
            <Field label="Field">
              {(ids) => <Select {...fieldProps(ids)} onChange={(event) => { const field = event.target.value as CurationField; updateDraft({ field, operation: field === 'release_type' || !listField(field) ? 'set' : 'add', values: [], scalar: '' }); }} value={draft.field}>
                {availableFields.map((option) => <option key={option.field} value={option.field}>{option.label}</option>)}
              </Select>}
            </Field>
            {listField(draft.field) ? (
              <Field label="Operation">
                {(ids) => <Select {...fieldProps(ids)} onChange={(event) => updateDraft({ operation: event.target.value as CurationOperation })} value={draft.operation}><option value="add">Add</option><option value="remove">Remove</option><option value="set">Set</option></Select>}
              </Field>
            ) : null}
            {listField(draft.field) ? (
              <Field hint={draft.operation === 'set' ? 'Replace the entire list with these values.' : undefined} label={`${draft.operation === 'remove' ? 'Values to remove' : draft.operation === 'set' ? 'New values' : 'Values to add'} for ${fieldLabel(draft.field)}`}>
                {(ids) => <ChipInput id={ids.inputId} label={`${fieldLabel(draft.field)} values`} maxItems={30} onChange={(values) => updateDraft({ values })} suggestions={suggestions} values={draft.values} />}
              </Field>
            ) : draft.field === 'release_type' ? (
              <Field hint="Use the same guided values as the title editor." label="Release type">
                {(ids) => <Select {...fieldProps(ids)} onChange={(event) => updateDraft({ scalar: event.target.value })} value={draft.scalar}><option value="">Choose a type</option>{MUSIC_RELEASE_TYPES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</Select>}
              </Field>
            ) : (
              <Field hint={draft.field === 'year' ? 'Albums only; use a four-digit year.' : undefined} label={`New ${fieldLabel(draft.field).toLowerCase()}`}>
                {(ids) => <Input {...fieldProps(ids)} max={draft.field === 'year' ? 2100 : undefined} min={draft.field === 'year' ? 1870 : undefined} onChange={(event) => updateDraft({ scalar: event.target.value })} type={draft.field === 'year' ? 'number' : 'text'} value={draft.scalar} />}
              </Field>
            )}
          </div>
          <p className="music-curation-provenance">These are manual edits. Locked items are skipped and the result will name every skip.</p>
          <div className="music-curation-actions"><Button busy={busy === 'preview'} disabled={busy !== null || !readyForPreview(draft)} onClick={() => void previewMetadata()} variant="primary">Preview changes</Button></div>
          {preview?.kind === 'metadata' ? <PreviewPanel busy={busy === 'apply'} onApply={() => void applyPreview()} onCancel={() => setPreview(null)} preview={preview} /> : null}
        </section>
      ) : null}

      {canEdit && selectedIds.size ? (
        <section aria-labelledby="music-curation-artwork" className="music-curation-bulk music-curation-artwork-section">
          <header><div><h2 id="music-curation-artwork">Artwork</h2><p className="g-setting-note">Replace the {kind === 'album' ? 'album cover' : 'artist portrait'} for the selected {kind === 'album' ? 'albums' : 'artists'}.</p></div></header>
          <Field hint="PNG, JPEG, WebP, or another image your server accepts." label={kind === 'album' ? 'Album cover image' : 'Artist portrait image'}>
            {(ids) => <Input {...fieldProps(ids)} accept="image/*" onChange={(event) => { setArtworkFile(event.target.files?.[0] ?? null); setPreview(null); setResult(null); }} ref={artworkInput} type="file" />}
          </Field>
          {artworkFile ? <div className="music-curation-artwork-file"><Upload aria-hidden="true" /><span>{artworkFile.name}</span><TextButton onClick={() => { setArtworkFile(null); if (artworkInput.current) artworkInput.current.value = ''; }}>Remove</TextButton></div> : null}
          <p className="music-curation-provenance">Lumina reads each current Primary image tag immediately before the upload so concurrent image edits are protected.</p>
          <div className="music-curation-actions"><Button busy={busy === 'artwork'} disabled={busy !== null || !artworkFile} onClick={() => void previewArtwork()} variant="primary">Preview artwork upload</Button></div>
          {preview?.kind === 'artwork' ? <ArtworkPreviewPanel artworkUrl={artworkUrl} busy={busy === 'apply'} onApply={() => void applyPreview()} onCancel={() => setPreview(null)} preview={preview} /> : null}
        </section>
      ) : null}
      {resultBody}
    </main>
  );
}

function PreviewPanel({ preview, busy, onApply, onCancel }: { preview: MetadataPreview; busy: boolean; onApply: () => void; onCancel: () => void }) {
  return (
    <aside aria-label="Preview changes" className="music-curation-preview">
      <div><strong>{plural(preview.ids.length, 'title')} selected</strong><span>{preview.summary}</span></div>
      {preview.skipped.length ? <p className="music-curation-preview-note">{preview.skipped.length} will be skipped: {preview.skipped.map((entry) => skipText(entry.reason)).join(' ')}</p> : null}
      <div className="music-curation-actions"><Button busy={busy} disabled={busy} onClick={onApply} variant="primary">Apply changes</Button><Button disabled={busy} onClick={onCancel} variant="quiet">Back</Button></div>
    </aside>
  );
}

function ArtworkPreviewPanel({ preview, artworkUrl, busy, onApply, onCancel }: { preview: ArtworkPreview; artworkUrl: string | null; busy: boolean; onApply: () => void; onCancel: () => void }) {
  const shown = preview.before.slice(0, 6);
  return (
    <aside aria-label="Preview artwork upload" className="music-curation-preview music-curation-artwork-preview">
      <div><strong>{plural(preview.ids.length, 'title')} ready for artwork</strong><span>{preview.summary}</span></div>
      <div className="music-curation-before-after">
        <div><span className="g-label">New</span>{artworkUrl ? <img alt="New artwork preview" src={artworkUrl} /> : null}</div>
        <div className="music-curation-current"><span className="g-label">Current</span><ul>{shown.map((entry) => <li key={entry.id}><Artwork alt={`Current artwork for ${entry.name}`} src={entry.url} /><span>{entry.name}</span></li>)}</ul>{preview.before.length > shown.length ? <small>{`+${preview.before.length - shown.length} more current images`}</small> : null}</div>
      </div>
      {preview.skipped.length ? <p className="music-curation-preview-note">{preview.skipped.length} will be skipped: {preview.skipped.map((entry) => skipText(entry.reason)).join(' ')}</p> : null}
      <div className="music-curation-actions"><Button busy={busy} disabled={busy} onClick={onApply} variant="primary">Apply artwork</Button><Button disabled={busy} onClick={onCancel} variant="quiet">Back</Button></div>
    </aside>
  );
}
