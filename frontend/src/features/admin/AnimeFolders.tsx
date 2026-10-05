/**
 * Settings → Library & storage → Anime folders: the folder names whose titles appear under
 * Anime. Admins add and remove names, then Save; the server repeats every rule, stores the list and re-sorts the
 * library in the background, and this row follows the re-sort.
 */
import { type KeyboardEvent, useEffect, useRef, useState } from 'react';

import { getMediaServerSettings, updateMediaServerSettings } from '../../api';
import { Button, Field, fieldProps, Input } from '../../ui';
import { errorMessage } from '../../utils';
import { forgetAllStore } from '../gallery/allStore';
import { forgetWallStores } from '../gallery/wallPages';
import { type FormStatus, SectionForm } from '../settings/SectionForm';
import { useAdminResource } from './useAdminResource';

export const ANIME_FOLDERS_MAX = 20;
export const ANIME_FOLDER_MAX_CHARS = 64;
export const RESORT_POLL_MS = 2_000;
export const RESORTED_NOTE_MS = 10_000;

/** Why `name` cannot join `folders`, in the row's words, or null. The server repeats these rules. */
export function animeFolderProblem(name: string, folders: readonly string[]): string | null {
  const trimmed = name.trim();
  if (!trimmed) return 'Enter a folder name.';
  if (/[/\\]/.test(trimmed)) return 'Use one folder name, not a path.';
  if (trimmed.length > ANIME_FOLDER_MAX_CHARS) return 'Folder names are at most 64 characters.';
  if (trimmed === '.' || trimmed === '..') return 'Choose a real folder name.';
  if (folders.some((folder) => folder.toLowerCase() === trimmed.toLowerCase())) return 'That folder is already listed.';
  if (folders.length >= ANIME_FOLDERS_MAX) return 'Up to 20 folder names.';
  return null;
}

const sameList = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((value, index) => value === b[index]);

export function AnimeFolders() {
  const resource = useAdminResource(getMediaServerSettings, 'Unable to load media server settings.');
  const { setData } = resource;
  const settings = resource.data;
  const [draft, setDraft] = useState<string[] | null>(null);
  const [name, setName] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const [resorted, setResorted] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  const sorting = useRef(false);
  const recategorising = settings?.recategorising ?? false;

  // While the server re-sorts, ask again every 2 s; when it finishes, say so for 10 s.
  useEffect(() => {
    if (!recategorising) return undefined;
    sorting.current = true;
    const timer = setInterval(() => { getMediaServerSettings().then(setData, () => undefined); }, RESORT_POLL_MS);
    return () => clearInterval(timer);
  }, [recategorising, setData]);
  useEffect(() => {
    if (recategorising || !sorting.current) return undefined;
    sorting.current = false;
    setResorted(true);
    // The walls and the All landing kept this tab's pre-sort titles: the next visit asks again.
    forgetWallStores();
    forgetAllStore();
    const timer = setTimeout(() => setResorted(false), RESORTED_NOTE_MS);
    return () => clearTimeout(timer);
  }, [recategorising]);

  if (resource.error && !settings) return <p className="auth-error" role="alert">{resource.error}</p>;
  if (!settings) return <p className="admin-note" role="status">Loading media server settings…</p>;
  const saved = settings.anime_folders ?? [];
  const folders = draft ?? saved;

  function add() {
    const found = animeFolderProblem(name, folders);
    setProblem(found);
    if (found) return;
    setDraft([...folders, name.trim()]);
    setName('');
    setStatus(null);
  }
  function remove(folder: string) {
    setDraft(folders.filter((entry) => entry !== folder));
    setStatus(null);
    field.current?.focus(); // the Remove button is gone; the field is the next thing to act on
  }
  async function save() {
    setSaving(true);
    setStatus(null);
    try {
      const next = await updateMediaServerSettings({ anime_folders: folders });
      setData(next);
      setDraft(null);
      setStatus({ tone: 'ok', text: 'Saved.' });
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, 'Unable to save media server settings.') });
    } finally {
      setSaving(false);
    }
  }
  function onFieldKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return;
    event.preventDefault(); // Enter adds the name; Save stays the form's explicit button
    add();
  }

  return (
    <SectionForm dirty={!sameList(folders, saved)} label="Library & storage" onDiscard={() => { setDraft(null); setProblem(null); setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save" saving={saving} status={status}>
      <div className="g-subrows">
        {folders.length ? (
          <ul aria-label="Anime folders" className="g-list">
            {folders.map((folder) => (
              <li className="g-list-row" key={folder}><span>{folder}</span><Button aria-label={`Remove ${folder}`} onClick={() => remove(folder)} variant="quiet">Remove</Button></li>
            ))}
          </ul>
        ) : <p className="admin-note">Nothing is sorted into Anime. The Anime tab and Jellyfin's Anime library disappear.</p>}
        <Field error={problem} label="Folder name">
          {(ids) => <Input {...fieldProps(ids)} maxLength={ANIME_FOLDER_MAX_CHARS} onChange={(event) => { setName(event.target.value); setProblem(null); }} onKeyDown={onFieldKeyDown} ref={field} spellCheck={false} value={name} />}
        </Field>
        <div className="g-actions"><Button onClick={add}>Add</Button></div>
        <p aria-live="polite" className="g-setting-note">{recategorising ? 'Re-sorting titles into Anime…' : resorted ? 'Titles re-sorted.' : ''}</p>
      </div>
    </SectionForm>
  );
}
