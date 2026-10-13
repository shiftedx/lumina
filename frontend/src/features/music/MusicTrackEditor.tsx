import { Check, RotateCcw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { ApiRequestError, createMusicRelease, editMusicTracks, getMusicQueue, getMusicTrackMetadata, listTitles, undoMusicEdit } from '../../api';
import type { TitleSummary } from '../../types';
import { Button, Checkbox, Dialog, Field, fieldProps, Input, Select, Textarea } from '../../ui';
import type { MusicTrack } from './musicTypes';
import { MusicWriteback } from './MusicWriteback';
import './trackEditor.css';

type EditMode = 'unchanged' | 'set' | 'revert';
type EditField = 'title' | 'artist' | 'album_artist' | 'album_id' | 'genres' | 'disc_number' | 'track_number' | 'lyrics' | 'gain_db';
type Draft = Record<EditField, { mode: EditMode; value: string }>;

type EditPreview = {
  kind: 'edit';
  ids: string[];
  changes: Record<string, unknown>;
  base: Record<string, Record<string, unknown>>;
  lines: string[];
};
type ReleaseRequest = { item_ids: string[]; name: string; artist_id?: string; artist_name?: string; year?: number | null; edition?: string; base: Record<string, { album_id: string | null }> };
type ReleasePreview = { kind: 'release'; ids: string[]; body: ReleaseRequest; lines: string[] };
type Preview = EditPreview | ReleasePreview;

type ApplyResult = {
  batchId: string | null;
  applied: number;
  changedSince: string[];
  undone?: { applied: number; skipped: string[] };
};
type AlbumOption = { id: string; name: string; artist_name?: string | null };

const FIELDS: Array<{ key: EditField; label: string; hint: string; multi?: boolean }> = [
  { key: 'title', label: 'Title', hint: 'The recording title.', multi: true },
  { key: 'artist', label: 'Track artist', hint: 'The performer credited for the selected track or tracks.', multi: true },
  { key: 'album_artist', label: 'Album artist', hint: 'The artist used to group the release.', multi: true },
  { key: 'album_id', label: 'Album', hint: 'Move the selected tracks into a visible album.', multi: true },
  { key: 'genres', label: 'Genres', hint: 'Comma-separated. Setting an empty value explicitly clears genres.', multi: true },
  { key: 'disc_number', label: 'Disc number', hint: 'A whole number from 1 to 999.', multi: true },
  { key: 'track_number', label: 'Track number', hint: 'A whole number from 1 to 999.', multi: true },
  { key: 'lyrics', label: 'Lyrics', hint: 'Available for one track at a time. An explicitly empty value clears lyrics.' },
  { key: 'gain_db', label: 'ReplayGain', hint: 'Track gain from −30 to +20 dB.', multi: true },
];

const blankDraft = (): Draft => Object.fromEntries(FIELDS.map(({ key }) => [key, { mode: 'unchanged', value: '' }])) as Draft;
const fieldName = (key: EditField) => FIELDS.find((field) => field.key === key)?.label ?? key;

function trackValue(track: MusicTrack, field: EditField): unknown {
  if (field === 'track_number') return track.number;
  if (field === 'disc_number') return track.disc;
  if (field === 'album_id') return track.album_id;
  if (field === 'lyrics') return undefined;
  return track[field];
}

function commonValue(tracks: readonly MusicTrack[], field: EditField, metadata: Record<string, unknown> | null): unknown {
  if (!tracks.length) return undefined;
  if (tracks.length === 1 && metadata && field in metadata) return metadata[field];
  const first = trackValue(tracks[0], field);
  return tracks.every((track) => JSON.stringify(trackValue(track, field)) === JSON.stringify(first)) ? first : undefined;
}

function valueText(value: unknown, albums: readonly AlbumOption[] = []): string {
  if (Array.isArray(value)) return value.join(', ') || 'None';
  if (value === null || value === undefined || value === '') return 'Not set';
  if (typeof value === 'string') return albums.find((album) => album.id === value)?.name ?? value;
  return String(value);
}

function parseChanges(draft: Draft): { changes: Record<string, unknown>; error: string | null } {
  const changes: Record<string, unknown> = {};
  for (const { key, label } of FIELDS) {
    const entry = draft[key];
    if (entry.mode === 'unchanged') continue;
    if (entry.mode === 'revert') { changes[key] = null; continue; }
    const value = entry.value;
    if (key === 'genres') {
      changes[key] = [...new Set(value.split(',').map((part) => part.trim()).filter(Boolean))];
    } else if (key === 'lyrics') {
      changes[key] = value;
    } else if (key === 'disc_number' || key === 'track_number') {
      const number = Number(value);
      if (!Number.isInteger(number) || number < 1 || number > 999) return { changes: {}, error: `${label} must be a whole number from 1 to 999.` };
      changes[key] = number;
    } else if (key === 'gain_db') {
      const number = Number(value);
      if (!Number.isFinite(number) || number < -30 || number > 20) return { changes: {}, error: 'ReplayGain must be from −30 to +20 dB.' };
      changes[key] = number;
    } else if (key === 'album_id') {
      if (!value) return { changes: {}, error: 'Choose an album.' };
      changes[key] = value;
    } else {
      if (!value.trim()) return { changes: {}, error: `${label} cannot be blank. Leave it unchanged or revert the manual value instead.` };
      changes[key] = value.trim();
    }
  }
  return { changes, error: Object.keys(changes).length ? null : 'Choose at least one field to change.' };
}

function conflictsOf(error: unknown): string[] {
  if (!(error instanceof ApiRequestError) || error.status !== 409) return [];
  const detail = (error.body as { detail?: { conflicts?: Array<{ item_id?: string }> } } | null)?.detail;
  return detail?.conflicts?.flatMap((entry) => entry.item_id ? [entry.item_id] : []) ?? [];
}

export interface MusicTrackEditorProps {
  open: boolean;
  albumId: string;
  albumName: string;
  user: { id?: string; role: string; can_edit_details?: boolean };
  onClose: () => void;
  onRefreshed?: () => void;
}

/** Previewed individual and bulk track metadata editor. Source audio files remain untouched by default. */
export function MusicTrackEditor({ open, albumId, albumName, user, onClose, onRefreshed }: MusicTrackEditorProps) {
  const [tracks, setTracks] = useState<MusicTrack[]>([]);
  const [albums, setAlbums] = useState<TitleSummary[]>([]);
  const [albumCursor, setAlbumCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [draft, setDraft] = useState<Draft>(blankDraft);
  const [albumTarget, setAlbumTarget] = useState<'existing' | 'create'>('existing');
  const [releaseDraft, setReleaseDraft] = useState({ name: '', year: '', edition: '', artistName: '' });
  const [metadata, setMetadata] = useState<{ id: string; fields: Record<string, unknown>; overridden: string[] } | null>(null);
  const [metadataRevision, setMetadataRevision] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [busy, setBusy] = useState<'load' | 'albums' | 'metadata' | 'apply' | 'undo' | null>(null);
  const [writebackBusy, setWritebackBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedTracks = useMemo(() => tracks.filter((track) => selected.has(track.id)), [selected, tracks]);
  const canEdit = user.can_edit_details ?? user.role === 'admin';
  const isAdmin = user.role === 'admin';

  useEffect(() => {
    if (!open) return undefined;
    let current = true;
    setBusy('load');
    setError(null);
    setSelected(new Set());
    setDraft(blankDraft());
    setAlbumTarget('existing');
    setReleaseDraft({ name: '', year: '', edition: '', artistName: '' });
    setPreview(null);
    setResult(null);
    setMetadata(null);
    void Promise.all([getMusicQueue({ title_id: albumId }), listTitles({ type: 'album', sort: 'name', limit: 60 })]).then(([loadedTracks, page]) => {
      if (!current) return;
      setTracks(loadedTracks);
      setAlbums(page.items);
      setAlbumCursor(page.next_cursor ?? null);
    }, (loadError) => {
      if (current) setError(loadError instanceof Error ? loadError.message : 'Lumina could not load these tracks.');
    }).finally(() => { if (current) setBusy(null); });
    return () => { current = false; };
  }, [albumId, open]);

  useEffect(() => {
    if (!open || selectedTracks.length !== 1) {
      setMetadata(null);
      setBusy((value) => value === 'metadata' ? null : value);
      return undefined;
    }
    let current = true;
    const id = selectedTracks[0].id;
    setBusy('metadata');
    void getMusicTrackMetadata(id).then((answer) => {
      if (current) setMetadata({ id, ...answer });
    }, (loadError) => {
      if (current) setError(loadError instanceof Error ? loadError.message : 'Lumina could not load the track’s original values.');
    }).finally(() => { if (current) setBusy((value) => value === 'metadata' ? null : value); });
    return () => { current = false; };
  }, [metadataRevision, open, selectedTracks.length, selectedTracks[0]?.id]);

  const resetPending = () => { setDraft(blankDraft()); setAlbumTarget('existing'); setReleaseDraft({ name: '', year: '', edition: '', artistName: '' }); setPreview(null); setResult(null); setError(null); };
  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else if (next.size < 500) next.add(id);
      return next;
    });
    resetPending();
  };
  const selectAll = () => {
    setSelected(selected.size === Math.min(500, tracks.length) ? new Set() : new Set(tracks.slice(0, 500).map((track) => track.id)));
    resetPending();
  };
  const updateDraft = (field: EditField, patch: Partial<Draft[EditField]>) => {
    setDraft((current) => ({ ...current, [field]: { ...current[field], ...patch } }));
    setPreview(null);
    setResult(null);
    setError(null);
  };
  const updateReleaseDraft = (field: keyof typeof releaseDraft, value: string) => {
    setReleaseDraft((current) => ({ ...current, [field]: value }));
    setPreview(null);
    setResult(null);
    setError(null);
  };
  const changeMode = (field: EditField, mode: EditMode) => {
    const current = commonValue(selectedTracks, field, metadata?.id === selectedTracks[0]?.id ? metadata.fields : null);
    const value = mode === 'set' ? (Array.isArray(current) ? current.join(', ') : current === null || current === undefined ? '' : String(current)) : '';
    updateDraft(field, { mode, value });
  };

  const makePreview = () => {
    if (draft.album_id.mode === 'set' && albumTarget === 'create') {
      const otherChanges = Object.entries(draft).some(([field, entry]) => field !== 'album_id' && entry.mode !== 'unchanged');
      if (otherChanges) { setError('Create the release first, then preview other metadata changes separately.'); return; }
      const name = releaseDraft.name.trim();
      if (!name) { setError('Give the new release a name.'); return; }
      const commonArtistId = selectedTracks[0]?.artist_id && selectedTracks.every((track) => track.artist_id === selectedTracks[0].artist_id) ? selectedTracks[0].artist_id : null;
      const artistName = releaseDraft.artistName.trim();
      if (!commonArtistId && !artistName) { setError('Enter an artist name for the new release.'); return; }
      const year = releaseDraft.year.trim() ? Number(releaseDraft.year) : null;
      if (year !== null && (!Number.isInteger(year) || year < 1870 || year > 2100)) { setError('Release year must be from 1870 to 2100.'); return; }
      const body: ReleaseRequest = {
        item_ids: selectedTracks.map((track) => track.id), name,
        base: Object.fromEntries(selectedTracks.map((track) => [track.id, { album_id: track.album_id }])),
        ...(commonArtistId ? { artist_id: commonArtistId } : { artist_name: artistName }),
        ...(year === null ? {} : { year }), ...(releaseDraft.edition.trim() ? { edition: releaseDraft.edition.trim() } : {}),
      };
      setPreview({ kind: 'release', ids: body.item_ids, body, lines: [`Release: ${name}`, `Artist: ${commonArtistId ? selectedTracks[0].album_artist : artistName}`, ...(year === null ? [] : [`Year: ${year}`]), ...(body.edition ? [`Edition: ${body.edition}`] : [])] });
      setError(null);
      return;
    }
    const parsed = parseChanges(draft);
    if (parsed.error) { setError(parsed.error); return; }
    const ids = selectedTracks.map((track) => track.id);
    const base: Record<string, Record<string, unknown>> = {};
    for (const track of selectedTracks) {
      base[track.id] = {};
      for (const key of Object.keys(parsed.changes) as EditField[]) {
        const fromMetadata = selectedTracks.length === 1 && metadata?.id === track.id && key in metadata.fields;
        const value = fromMetadata ? metadata.fields[key] : trackValue(track, key);
        if (value !== undefined) base[track.id][key] = value;
      }
    }
    const lines = Object.entries(parsed.changes).map(([field, value]) => `${fieldName(field as EditField)}: ${value === null ? 'Revert manual value' : valueText(value, albums)}`);
    setPreview({ kind: 'edit', ids, changes: parsed.changes, base, lines });
    setError(null);
  };

  const reloadTracks = async () => {
    const loaded = await getMusicQueue({ title_id: albumId });
    setTracks(loaded);
    return loaded;
  };
  const apply = async () => {
    if (!preview) return;
    setBusy('apply');
    setError(null);
    try {
      const answer = preview.kind === 'release'
        ? await createMusicRelease(preview.body)
        : await editMusicTracks({ item_ids: preview.ids, changes: preview.changes, base: preview.base });
      const changed = new Map(answer.tracks.map((track) => [track.id, track]));
      // Keep moved rows visible until the dialog closes so the batch result and Undo remain reachable.
      setTracks((current) => current.map((track) => changed.get(track.id) ?? track));
      setResult({ batchId: answer.batch_id, applied: answer.applied, changedSince: [] });
      setPreview(null);
      setDraft(blankDraft());
      setAlbumTarget('existing');
      setReleaseDraft({ name: '', year: '', edition: '', artistName: '' });
      setMetadataRevision((value) => value + 1);
      onRefreshed?.();
    } catch (applyError) {
      const conflicts = conflictsOf(applyError);
      if (conflicts.length) {
        setResult({ batchId: null, applied: 0, changedSince: conflicts });
        setPreview(null);
        await reloadTracks().catch(() => undefined);
        setMetadataRevision((value) => value + 1);
      } else setError(applyError instanceof Error ? applyError.message : 'Lumina could not save these track changes.');
    } finally {
      setBusy(null);
    }
  };
  const undo = async () => {
    if (!result?.batchId) return;
    setBusy('undo');
    setError(null);
    try {
      const answer = await undoMusicEdit(result.batchId);
      setResult({ ...result, undone: answer });
      await reloadTracks();
      setMetadataRevision((value) => value + 1);
      onRefreshed?.();
    } catch (undoError) {
      setError(undoError instanceof Error ? undoError.message : 'Lumina could not undo this edit.');
    } finally {
      setBusy(null);
    }
  };
  const loadMoreAlbums = async () => {
    if (!albumCursor) return;
    setBusy('albums');
    try {
      const page = await listTitles({ type: 'album', sort: 'name', cursor: albumCursor, limit: 60 });
      setAlbums((current) => [...current, ...page.items.filter((album) => !current.some((entry) => entry.id === album.id))]);
      setAlbumCursor(page.next_cursor ?? null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Lumina could not load more albums.');
    } finally { setBusy(null); }
  };

  const allSelected = tracks.length > 0 && selected.size === Math.min(500, tracks.length);
  const manualCount = selectedTracks.filter((track) => track.edited).length;
  const currentAlbumOptions: AlbumOption[] = selectedTracks.flatMap((track) => track.album_id && !albums.some((album) => album.id === track.album_id)
    ? [{ id: track.album_id, name: track.album }]
    : []);
  const albumOptions: AlbumOption[] = [...albums, ...currentAlbumOptions.filter((entry, index, all) => all.findIndex((candidate) => candidate.id === entry.id) === index)];
  const locked = busy === 'apply' || busy === 'undo' || writebackBusy;

  return (
    <div className="music-track-dialog-boundary" onKeyDown={(event) => event.stopPropagation()}>
    <Dialog
      busy={locked}
      className="music-track-dialog"
      description={`Choose up to 500 tracks from ${albumName}. Review every change before it is saved.`}
      footer={<Button disabled={locked} onClick={onClose} variant="quiet">Done</Button>}
      onClose={onClose}
      open={open}
      size="sheet"
      title="Manage tracks"
    >
      {!canEdit ? <p className="music-editor-error" role="alert">Your account cannot edit music details.</p> : null}
      {error ? <p className="music-editor-error" role="alert">{error}</p> : null}
      {busy === 'load' ? <p className="music-editor-status" role="status">Loading tracks and albums…</p> : null}
      {busy !== 'load' && tracks.length ? (
        <div className="music-track-editor">
          <section aria-labelledby="music-track-selection">
            <header className="music-editor-section-head"><div><h3 id="music-track-selection">Tracks</h3><p>{selected.size} selected{tracks.length > 500 ? ' · maximum 500' : ''}</p></div><Button disabled={!canEdit || locked} onClick={selectAll} variant="quiet">{allSelected ? 'Clear selection' : tracks.length > 500 ? 'Select first 500' : 'Select all'}</Button></header>
            <ul className="music-editor-track-list">
              {tracks.map((track) => <li className={selected.has(track.id) ? 'is-selected' : ''} key={track.id}><Checkbox checked={selected.has(track.id)} disabled={!canEdit || locked || (!selected.has(track.id) && selected.size >= 500)} label={track.title} hint={[track.artist, `Disc ${track.disc ?? '—'} · Track ${track.number ?? '—'}`, track.edited ? 'Protected manual metadata' : null].filter(Boolean).join(' · ')} onChange={() => toggle(track.id)} /></li>)}
            </ul>
          </section>

          {selected.size ? (
            <section aria-labelledby="music-track-fields" className="music-editor-fields-section">
              <header className="music-editor-section-head"><div><h3 id="music-track-fields">Metadata</h3><p>{selected.size === 1 ? 'Current values are loaded from this track.' : 'A set value applies to every selected track.'}</p></div>{manualCount ? <span className="music-editor-protected"><Check aria-hidden="true" /> {manualCount} with protected manual metadata</span> : null}</header>
              {busy === 'metadata' ? <p className="music-editor-status" role="status">Loading original values…</p> : null}
              <div className="music-editor-fields">
                {FIELDS.map((field) => {
                  if (!field.multi && selected.size !== 1) return null;
                  const entry = draft[field.key];
                  const current = commonValue(selectedTracks, field.key, metadata?.id === selectedTracks[0]?.id ? metadata.fields : null);
                  const currentLabel = selectedTracks.length > 1 && current === undefined ? 'Mixed values' : valueText(current, albumOptions);
                  const overridden = selected.size === 1 ? metadata?.overridden.includes(field.key) : undefined;
                  return (
                    <div className="music-editor-field" key={field.key}>
                      <Field hint={<>{field.hint} Current: <strong>{currentLabel}</strong>{overridden ? ' · Protected manual value' : ''}</>} label={`${field.label} action`}>
                        {(ids) => <Select {...fieldProps(ids)} disabled={locked} onChange={(event) => changeMode(field.key, event.currentTarget.value as EditMode)} value={entry.mode}><option value="unchanged">Leave unchanged</option><option value="set">Set a value</option><option value="revert">Revert manual value</option></Select>}
                      </Field>
                      {entry.mode === 'set' && field.key === 'album_id' ? (
                        <div className="music-editor-release-target">
                          <Field label="Regroup tracks into">{(ids) => <Select {...fieldProps(ids)} disabled={locked} onChange={(event) => { setAlbumTarget(event.currentTarget.value as 'existing' | 'create'); setPreview(null); setResult(null); setError(null); }} value={albumTarget}><option value="existing">Existing album</option><option value="create">Create release / edition</option></Select>}</Field>
                          {albumTarget === 'existing' ? <Field label="Album">{(ids) => <Select {...fieldProps(ids)} disabled={locked} onChange={(event) => updateDraft(field.key, { value: event.currentTarget.value })} value={entry.value}><option value="">Choose an album</option>{albumOptions.map((album) => <option key={album.id} value={album.id}>{album.name}{album.artist_name ? ` — ${album.artist_name}` : ''}</option>)}</Select>}</Field> : (
                            <div className="music-editor-release-fields">
                              <Field label="Release name" required>{(ids) => <Input {...fieldProps(ids)} disabled={locked} maxLength={300} onChange={(event) => updateReleaseDraft('name', event.currentTarget.value)} value={releaseDraft.name} />}</Field>
                              <Field label="Release year" hint="Optional">{(ids) => <Input {...fieldProps(ids)} disabled={locked} inputMode="numeric" max={2100} min={1870} onChange={(event) => updateReleaseDraft('year', event.currentTarget.value)} type="number" value={releaseDraft.year} />}</Field>
                              <Field label="Edition" hint="Optional, for example Deluxe or 20th Anniversary">{(ids) => <Input {...fieldProps(ids)} disabled={locked} maxLength={300} onChange={(event) => updateReleaseDraft('edition', event.currentTarget.value)} value={releaseDraft.edition} />}</Field>
                              {selectedTracks[0]?.artist_id && selectedTracks.every((track) => track.artist_id === selectedTracks[0].artist_id) ? <p className="music-editor-status">Artist: <strong>{selectedTracks[0].album_artist}</strong></p> : <Field label="Artist name" required>{(ids) => <Input {...fieldProps(ids)} disabled={locked} maxLength={300} onChange={(event) => updateReleaseDraft('artistName', event.currentTarget.value)} value={releaseDraft.artistName} />}</Field>}
                            </div>
                          )}
                        </div>
                      ) : null}
                      {entry.mode === 'set' && field.key === 'lyrics' ? <Field label="Lyrics">{(ids) => <Textarea {...fieldProps(ids)} disabled={locked} maxLength={100000} onChange={(event) => updateDraft(field.key, { value: event.currentTarget.value })} rows={7} value={entry.value} />}</Field> : null}
                      {entry.mode === 'set' && !['album_id', 'lyrics'].includes(field.key) ? (
                        <Field label={`New ${field.label.toLocaleLowerCase()}`}>{(ids) => field.key === 'disc_number' || field.key === 'track_number' || field.key === 'gain_db'
                          ? <Input {...fieldProps(ids)} disabled={locked} inputMode="decimal" max={field.key === 'gain_db' ? 20 : 999} min={field.key === 'gain_db' ? -30 : 1} onChange={(event) => updateDraft(field.key, { value: event.currentTarget.value })} step={field.key === 'gain_db' ? '0.1' : '1'} type="number" value={entry.value} />
                          : <Input {...fieldProps(ids)} disabled={locked} maxLength={field.key === 'genres' ? 1949 : 300} onChange={(event) => updateDraft(field.key, { value: event.currentTarget.value })} value={entry.value} />}</Field>
                      ) : null}
                    </div>
                  );
                })}
              </div>
              {albumCursor ? <Button busy={busy === 'albums'} disabled={busy !== null} onClick={() => void loadMoreAlbums()} variant="quiet">Load more albums</Button> : null}
              <p className="music-editor-server-note">These edits are protected in Lumina. Your source audio files stay unchanged unless an owner uses file writeback below.</p>
              <div className="music-editor-actions"><Button disabled={busy !== null} onClick={makePreview} variant="primary">Preview changes</Button><Button disabled={busy !== null} onClick={resetPending} variant="quiet">Reset fields</Button></div>
            </section>
          ) : null}

          {preview ? (
            <section aria-labelledby="music-track-preview" className="music-editor-preview">
              <h3 id="music-track-preview">Review changes</h3>
              <p>{preview.ids.length} track{preview.ids.length === 1 ? '' : 's'} will receive:</p>
              <ul>{preview.lines.map((line) => <li key={line}>{line}</li>)}</ul>
              <div className="music-editor-actions"><Button busy={busy === 'apply'} disabled={busy !== null} onClick={() => void apply()} variant="primary">{preview.kind === 'release' ? 'Create release' : 'Apply changes'}</Button><Button disabled={busy !== null} onClick={() => setPreview(null)} variant="quiet">Keep editing</Button></div>
            </section>
          ) : null}

          {result ? (
            <section aria-live="polite" className="music-editor-result">
              {result.changedSince.length ? <p><strong>No changes applied.</strong> {result.changedSince.length} track{result.changedSince.length === 1 ? '' : 's'} changed after this editor loaded and were skipped. Review the current values and preview again.</p> : <p><strong>Updated {result.applied} track{result.applied === 1 ? '' : 's'}.</strong></p>}
              {result.undone ? <p>Undo restored {result.undone.applied}. {result.undone.skipped.length ? `${result.undone.skipped.length} changed since the edit and were skipped.` : ''}</p> : result.batchId ? <Button busy={busy === 'undo'} disabled={busy !== null} icon={<RotateCcw />} onClick={() => void undo()} variant="quiet">Undo track edit</Button> : null}
            </section>
          ) : null}

          <MusicWriteback isAdmin={isAdmin} onBusyChange={setWritebackBusy} onRefreshed={onRefreshed} tracks={selectedTracks} />
        </div>
      ) : busy !== 'load' ? <p className="music-editor-status">No tracks are available for this album.</p> : null}
    </Dialog>
    </div>
  );
}
