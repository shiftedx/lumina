import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Clock } from 'lucide-react';
import { createLibraryNote, deleteLibraryNote, listLibraryNotes, updateLibraryNote } from '../../api';
import { type LibraryNote, type NoteVisibility } from '../../types';
import { formatDuration } from '../../utils';

const NOTE_MAX_CHARS = 4000; // Mirrors backend NOTE_BODY_MAX_CHARS.

/** Untimed notes first, then playback order — the same order the API returns. */
function byTimestamp(a: LibraryNote, b: LibraryNote) {
  return (a.timestamp_ms ?? -1) - (b.timestamp_ms ?? -1) || String(a.created_at).localeCompare(String(b.created_at));
}

const errorText = (error: unknown) => error instanceof Error ? error.message : 'Unable to save this note.';

/** Private and household notes for one Library item; timestamps capture and seek the player. */
export function HouseholdNotesPanel({ itemId, getTime, onSeek, refreshKey = 0, onNotes }: {
  itemId: string;
  getTime: () => number;
  onSeek: (seconds: number) => void;
  /** Bumped when another tool (Moments) changed this item's notes. */
  refreshKey?: number;
  onNotes?: (notes: LibraryNote[]) => void;
}) {
  const [notes, setNotes] = useState<LibraryNote[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [body, setBody] = useState('');
  const [household, setHousehold] = useState(false);
  const [timestampMs, setTimestampMs] = useState<number | null>(null);
  const [editing, setEditing] = useState<{ id: string; body: string; household: boolean } | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let active = true;
    listLibraryNotes(itemId).then((loaded) => { if (active) setNotes(loaded); }, (loadError) => { if (active) { setNotes([]); setError(errorText(loadError)); } });
    return () => { active = false; };
  }, [itemId, refreshKey]);
  useEffect(() => { if (notes) onNotes?.(notes); }, [notes]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try { await action(); } catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }

  function captureTime() {
    setTimestampMs(Math.max(0, Math.round(getTime() * 1000)));
    composer.current?.focus();
  }

  const visibility = (shared: boolean): NoteVisibility => shared ? 'household' : 'private';

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!body.trim()) return;
    void run(async () => {
      const created = await createLibraryNote(itemId, { body: body.trim(), visibility: visibility(household), timestamp_ms: timestampMs });
      setNotes((current) => [...(current || []), created].sort(byTimestamp));
      setBody('');
      setTimestampMs(null);
    });
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (!editing || !editing.body.trim()) return;
    void run(async () => {
      const updated = await updateLibraryNote(editing.id, { body: editing.body.trim(), visibility: visibility(editing.household) });
      setNotes((current) => (current || []).map((note) => note.id === updated.id ? updated : note));
      setEditing(null);
    });
  }

  function remove(note: LibraryNote) {
    void run(async () => {
      await deleteLibraryNote(note.id);
      setNotes((current) => (current || []).filter((entry) => entry.id !== note.id));
    });
  }

  return (
    <section aria-labelledby="notes-title" className="notes-panel">
      <header><h2 id="notes-title">Notes</h2><p>Private notes are just for you. Household notes are visible to everyone who can watch this item. Notes stay in Lumina and are never posted to YouTube, Twitch or any other source.</p></header>
      <form className="notes-composer" onSubmit={submit}>
        <label className="g-field settings-select"><span className="g-field-label">New note</span><textarea className="g-input g-textarea" disabled={busy} maxLength={NOTE_MAX_CHARS} onChange={(event) => setBody(event.target.value)} ref={composer} value={body} /></label>
        <div className="notes-composer__actions">
          {timestampMs === null
            ? <button className="g-button" disabled={busy} onClick={captureTime} type="button"><Clock /> Add current time</button>
            : <span className="notes-stamp">At {formatDuration(timestampMs / 1000)} <button aria-label="Remove timestamp" className="g-text-button" onClick={() => setTimestampMs(null)} type="button">Remove</button></span>}
          <label className="g-check"><input checked={household} disabled={busy} onChange={(event) => setHousehold(event.target.checked)} role="switch" type="checkbox" /> Share with household</label>
          <button className="g-button is-primary" disabled={busy || !body.trim()} type="submit">Save note</button>
        </div>
      </form>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      {notes === null ? <p aria-live="polite" role="status">Loading notes…</p> : notes.length ? (
        <ol aria-label="Notes on this item" className="notes-list">
          {notes.map((note) => {
            const author = note.is_owner ? 'You' : note.author_display_name || note.author_username || 'Household member';
            const label = note.visibility === 'household' ? 'Household' : 'Private';
            const isEditing = editing?.id === note.id;
            return (
              <li key={note.id}>
                <article aria-label={`${label} note from ${author}`}>
                  <header>
                    {note.timestamp_ms != null ? <button aria-label={`Seek to ${formatDuration(note.timestamp_ms / 1000)}`} className="g-chip" onClick={() => onSeek(note.timestamp_ms! / 1000)} type="button">{formatDuration(note.timestamp_ms / 1000)}</button> : null}
                    <strong>{author}</strong><span>{label}</span>
                  </header>
                  {isEditing ? (
                    <form onSubmit={save}>
                      <label className="g-field settings-select"><span className="g-field-label">Note text</span><textarea className="g-input g-textarea" autoFocus disabled={busy} maxLength={NOTE_MAX_CHARS} onChange={(event) => setEditing({ ...editing, body: event.target.value })} value={editing.body} /></label>
                      <div><label className="g-check"><input checked={editing.household} disabled={busy} onChange={(event) => setEditing({ ...editing, household: event.target.checked })} role="switch" type="checkbox" /> Share with household</label><button className="g-button is-primary" disabled={busy || !editing.body.trim()} type="submit">Save</button><button className="g-button is-quiet" disabled={busy} onClick={() => setEditing(null)} type="button">Cancel</button></div>
                    </form>
                  ) : <p>{note.body}</p>}
                  {!isEditing && (note.is_owner || note.can_delete) ? (
                    <div>
                      {note.is_owner ? <button className="g-text-button" disabled={busy} onClick={() => setEditing({ id: note.id, body: note.body, household: note.visibility === 'household' })} type="button">Edit</button> : null}
                      {note.can_delete ? <button className="g-text-button" disabled={busy} onClick={() => remove(note)} type="button">Delete</button> : null}
                    </div>
                  ) : null}
                </article>
              </li>
            );
          })}
        </ol>
      ) : <p className="notes-empty">No notes yet. Add one at the current moment to find it again later.</p>}
    </section>
  );
}
