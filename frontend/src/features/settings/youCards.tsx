import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Download, RotateCcw } from 'lucide-react';
import { type AutoSkipPref, type MemberInterests, type ProfanityPref, type RemotePlaybackCachePreferences, type SearchHistoryEntry, type Suppression, type SuppressionList, type UserProfile } from '../../types';
import { type AcquisitionFormat } from '../../mediaAcquisition';
import { useStreamingProviders } from '../streaming/providers';
import { PROFANITY_MAX_WORD_CHARS, PROFANITY_MAX_WORDS } from '../../workspace';
import { clearRecoHistory, exportMyData } from '../../api';
import { resetRecoEvents } from '../reco/recoEvents';
import { Button, Checkbox, ConfirmDialog, Field, fieldProps, Input, SegmentedControl, Select, StatusText, Textarea } from '../../ui';
import { SettingSwitch } from './SettingRow';

export const DEFAULT_REMOTE_PLAYBACK_CACHE: RemotePlaybackCachePreferences = {
  enabled: true,
  recent_video_limit: 5,
  storage_limit_mb: 2048,
};

export const remoteVideoCachePresets = [
  { value: 0, label: 'Off' },
  { value: 3, label: '3' },
  { value: 5, label: '5' },
  { value: 10, label: '10' },
  { value: 20, label: '20' },
] as const;
export const remoteStoragePresets = [
  { value: 512, label: '512 MB' },
  { value: 2048, label: '2 GB' },
  { value: 5120, label: '5 GB' },
  { value: 10240, label: '10 GB' },
  { value: 25600, label: '25 GB' },
] as const;

export function RemotePlaybackCacheSettings({ onChange, value }: { value: RemotePlaybackCachePreferences; onChange: (value: RemotePlaybackCachePreferences) => Promise<void> | void }) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'saved' | 'error' | null>(null);
  const saveGenerationRef = useRef(0);
  const saveMutationRef = useRef<Promise<void>>(Promise.resolve());
  const canonicalValueRef = useRef(value);
  const pendingSaveCountRef = useRef(0);

  useEffect(() => {
    canonicalValueRef.current = value;
    if (pendingSaveCountRef.current === 0) setDraft(value);
  }, [value.enabled, value.recent_video_limit, value.storage_limit_mb]);

  async function save(next: RemotePlaybackCachePreferences) {
    const generation = ++saveGenerationRef.current;
    pendingSaveCountRef.current += 1;
    setDraft(next);
    setSaving(true);
    setSaveStatus(null);
    const mutation = saveMutationRef.current.catch(() => undefined).then(() => onChange(next));
    saveMutationRef.current = mutation.then(() => undefined, () => undefined);
    try {
      await mutation;
      canonicalValueRef.current = next;
      if (generation === saveGenerationRef.current) setSaveStatus('saved');
    } catch {
      if (generation === saveGenerationRef.current) {
        setDraft(canonicalValueRef.current);
        setSaveStatus('error');
      }
    } finally {
      pendingSaveCountRef.current = Math.max(0, pendingSaveCountRef.current - 1);
      if (generation === saveGenerationRef.current) setSaving(false);
    }
  }

  function chooseRecentVideoCount(count: 0 | 3 | 5 | 10 | 20) {
    void save(count === 0 ? { ...draft, enabled: false } : { ...draft, enabled: true, recent_video_limit: count });
  }

  const selectedCount = draft.enabled ? draft.recent_video_limit : 0;
  return (
    <div className="g-stream-cache">
      <div className="g-subrows">
        <SegmentedControl legend="Recent videos kept ready" onChange={(value) => chooseRecentVideoCount(Number(value) as 0 | 3 | 5 | 10 | 20)} options={remoteVideoCachePresets.map((preset) => ({ value: String(preset.value), label: preset.label }))} value={String(selectedCount)} />
        <Field label="Maximum storage">{(ids) => <Select {...fieldProps(ids)} disabled={!draft.enabled} onChange={(event) => void save({ ...draft, storage_limit_mb: Number(event.currentTarget.value) as RemotePlaybackCachePreferences['storage_limit_mb'] })} value={draft.storage_limit_mb}>{remoteStoragePresets.map((preset) => <option key={preset.value} value={preset.value}>{preset.label}</option>)}</Select>}</Field>
      </div>
      <p className="g-setting-note"><strong>Your watch progress stays saved when this cache is off.</strong> Lumina will simply fetch the stream again when you return. The storage limit is a ceiling; the oldest cached stream leaves first, and cached stream data expires after 7 days.</p>
      <p aria-live="polite" className="g-save-status">{saving ? 'Saving…' : saveStatus ? <StatusText tone={saveStatus === 'error' ? 'danger' : 'ok'}>{saveStatus === 'saved' ? 'Cache preference saved.' : 'Could not save this preference. Try again.'}</StatusText> : ''}</p>
    </div>
  );
}

export function InterestSettingsCard({ interests, onSave }: { interests: MemberInterests; onSave: (keys: string[]) => Promise<void> }) {
  const [selected, setSelected] = useState<string[]>(interests.selected_keys);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(false);
  useEffect(() => { if (!saving) setSelected(interests.selected_keys); }, [interests.selected_keys, saving]);
  const toggle = (key: string) => setSelected((current) => current.includes(key) ? current.filter((value) => value !== key) : [...current, key]);
  const save = async () => {
    setSaving(true);
    setSaveError(false);
    try { await onSave(selected); } catch { setSaveError(true); } finally { setSaving(false); }
  };
  return (
    <div className="interest-settings-card">
      <fieldset className="g-fieldset"><legend>Choose interests</legend>{interests.categories.map((category) => <Checkbox checked={selected.includes(category.key)} key={category.key} label={category.label} onChange={() => toggle(category.key)} />)}</fieldset>
      <div className="g-actions">
        <Button busy={saving} onClick={() => void save()} variant="secondary">{saving ? 'Saving interests…' : 'Save interests'}</Button>
        <span aria-live="polite">{saveError ? <StatusText tone="danger">Could not save interests. Try again.</StatusText> : ''}</span>
      </div>
    </div>
  );
}

/** "Back to normal on 7 February 2027" for a shown-fewer channel; nothing when the server sent no date. */
function backToNormal(iso: string | null | undefined): string | null {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return null;
  return `Back to normal on ${date.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })}`;
}

/**
 * The member's recommendation feedback: four lists, each row restorable. An older server sends no `fewer`
 * or `titles`, so those read as empty.
 */
export function SuppressionSettingsCard({ suppressions, onRestore }: { suppressions: SuppressionList; onRestore: (id: string) => void }) {
  const groups: Array<{ heading: string; entries: Suppression[]; name: (entry: Suppression) => string }> = [
    { heading: 'Not interested: videos', entries: suppressions.items, name: (entry) => entry.title || 'Hidden video' },
    { heading: 'Not interested: titles', entries: suppressions.titles ?? [], name: (entry) => entry.title || 'Hidden title' },
    { heading: 'Showing fewer: channel', entries: suppressions.fewer ?? [], name: (entry) => entry.channel_name || 'Channel' },
    { heading: 'Hidden channels', entries: suppressions.channels, name: (entry) => entry.channel_name || 'Hidden channel' },
  ];
  const shown = groups.filter((group) => group.entries.length);
  return (
    <div className="g-suppressions">
      {shown.length ? shown.map((group) => (
        <div key={group.heading}>
          <h3 className="g-setting-group-head">{group.heading}</h3>
          <ul className="g-list">
            {group.entries.map((entry) => {
              const primary = group.name(entry);
              const note = entry.scope === 'fewer' ? backToNormal(entry.recovers_at) : entry.scope === 'item' ? entry.channel_name : null;
              return (
                <li className="g-list-row" key={entry.id}>
                  <div className="g-list-row-copy">
                    <span className="g-list-row-title">{primary}</span>
                    {note ? <span className="g-list-row-meta">{note}</span> : null}
                  </div>
                  <Button aria-label={`Restore recommendations of ${primary}`} icon={<RotateCcw />} onClick={() => onRestore(entry.id)} variant="quiet">Restore</Button>
                </li>
              );
            })}
          </ul>
        </div>
      )) : (
        <p className="g-setting-note">Nothing is suppressed. Use “Not interested”, “Show fewer” or “Don't recommend” on a recommendation to see it here.</p>
      )}
    </div>
  );
}

/** Clear recommendation history: the question first, in the spec's words; watch history and hidden lists stay. */
export function RecoHistoryCard({ onMessage }: { onMessage?: (message: string) => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    setConfirming(false);
    setError(null);
  }

  async function clear() {
    setBusy(true);
    setError(null);
    try {
      // Events buffered before the clear must not be posted after it (they would be stored dated before it).
      resetRecoEvents();
      await clearRecoHistory();
      setConfirming(false);
      onMessage?.('Recommendation history cleared.');
    } catch {
      setError('Could not clear recommendation history. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="reco-history-card">
      <Button aria-haspopup="dialog" onClick={() => setConfirming(true)} variant="secondary">Clear recommendation history</Button>
      <ConfirmDialog
        body={<>Lumina forgets what it showed you and what you opened. Your watch history and hidden lists stay.{error ? <StatusText tone="danger"><span role="alert">{error}</span></StatusText> : null}</>}
        busy={busy}
        confirmLabel="Clear history"
        danger
        onCancel={close}
        onConfirm={() => { void clear(); }}
        open={confirming}
        title="Clear recommendation history?"
      />
    </div>
  );
}

export function SearchHistorySettingsCard({ history, onClear }: { history: SearchHistoryEntry[]; onClear: () => void }) {
  return history.length
    ? <Button onClick={onClear} variant="secondary">Clear search history</Button>
    : <p className="g-setting-note">No search history yet.</p>;
}

export function ProfileNameSettings({ user, onSave }: { user: UserProfile; onSave: (displayName: string) => Promise<void> }) {
  const [draft, setDraft] = useState(user.display_name || user.username);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  useEffect(() => { if (!saving) setDraft(user.display_name || user.username); }, [user.display_name, user.username, saving]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = draft.trim();
    if (!trimmed) return;
    setSaving(true);
    setResult(null);
    try {
      await onSave(trimmed);
      setResult({ ok: true, message: 'Display name updated.' });
    } catch (updateError) {
      setResult({ ok: false, message: updateError instanceof Error ? updateError.message : 'Unable to update your display name.' });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="g-subrows" onSubmit={(event) => { void submit(event); }}>
      <Field label="Display name">{(ids) => <Input {...fieldProps(ids)} maxLength={120} onChange={(event) => setDraft(event.target.value)} required value={draft} />}</Field>
      <div className="g-actions">
        {result ? <p className={result.ok ? 'g-setting-note' : 'auth-error'} role={result.ok ? 'status' : 'alert'}>{result.message}</p> : null}
        <Button disabled={saving || draft.trim() === (user.display_name || user.username)} type="submit" variant="secondary">{saving ? 'Saving…' : 'Save name'}</Button>
      </div>
    </form>
  );
}

export function DataExportCard() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const data = await exportMyData();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'lumina-export.json';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : 'Unable to export your data.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="data-export">
      <Button busy={busy} icon={<Download />} onClick={() => void download()} variant="secondary">{busy ? 'Preparing export…' : 'Export my data'}</Button>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
    </div>
  );
}

/** The exact wording the page promises. */
export const MUTE_COPY = "Applies in Lumina's web player only. Infuse and other apps play unfiltered. Accuracy depends on the transcript: generated transcripts can miss or mishear words.";
export const SKIP_LABELS: Record<keyof AutoSkipPref, string> = { intro: 'Skip intros', recap: 'Skip recaps', credits: 'Skip credits' };

/** One word per line or comma; blanks and case-insensitive repeats dropped; over-limit input is explained, never sent. */
export function cleanWordList(text: string): { words: string[]; problem: string | null } {
  const seen = new Set<string>();
  const words: string[] = [];
  let tooLong = 0;
  for (const raw of text.split(/[\n,]/)) {
    const word = raw.trim();
    if (!word) continue;
    if (word.length > PROFANITY_MAX_WORD_CHARS) { tooLong += 1; continue; }
    const key = word.toLocaleLowerCase();
    if (!seen.has(key)) { seen.add(key); words.push(word); }
  }
  if (tooLong) return { words, problem: `${tooLong} ${tooLong === 1 ? 'entry is' : 'entries are'} longer than ${PROFANITY_MAX_WORD_CHARS} characters. Shorten ${tooLong === 1 ? 'it' : 'them'} to save.` };
  if (words.length > PROFANITY_MAX_WORDS) return { words, problem: `Keep the list to ${PROFANITY_MAX_WORDS} words or fewer (${words.length} now).` };
  return { words, problem: null };
}

/** The extra words row; the on/off is its own row (playback.mute), so the switch never repeats the row label. */
export function MuteWords({ value, onChange }: { value: ProfanityPref; onChange: (value: ProfanityPref) => void }) {
  const [draft, setDraft] = useState(value.words.join('\n'));
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => setDraft(value.words.join('\n')), [value.words]);
  function save() {
    const cleaned = cleanWordList(draft);
    setProblem(cleaned.problem);
    if (!cleaned.problem) onChange({ ...value, words: cleaned.words });
  }
  return (
    <div className="g-subrows">
      <Field label="Your extra words">{(ids) => <Textarea {...fieldProps(ids)} aria-describedby="mute-words-hint mute-words-problem" disabled={!value.enabled} onChange={(event) => setDraft(event.currentTarget.value)} rows={5} spellCheck={false} value={draft} />}</Field>
      <small className="g-setting-note is-control" id="mute-words-hint">Lumina already mutes common strong language. Add one word or name per line; end a word with * to match its endings.</small>
      <p aria-live="polite" className="g-setting-note is-control" id="mute-words-problem">{problem ?? ''}</p>
      <div className="g-actions"><Button disabled={!value.enabled} onClick={save} variant="secondary">Save words</Button></div>
    </div>
  );
}

const PRESETS = [['best', 'Best'], ['best_1080p', '1080p'], ['best_editable', 'Editable'], ['audio_only', 'Audio']] as const;

/** The download-quality choice when the app shell passes no full download form (tests, first paint). */
export function PresetFallback({ formatPreset, onFormatChange }: { formatPreset: string; onFormatChange: (value: AcquisitionFormat) => void }) {
  return (
    <>
      <SegmentedControl hideLegend legend="Default acquisition preset" onChange={(value) => onFormatChange(value)} options={PRESETS.map(([value, label]) => ({ value, label }))} value={formatPreset as AcquisitionFormat} />
      <p className="g-setting-note">Files are saved to <strong>your private folder inside the server Library</strong>. The vault owner controls the Library root.</p>
    </>
  );
}

/** Settings → Streaming: YouTube is always on; Twitch and Kick are the member's to switch. */
export function StreamingProvidersCard() {
  const { providers, setEnabled } = useStreamingProviders();
  return (
    <fieldset className="g-fieldset"><legend className="sr-only">Streaming providers</legend>
      <p className="g-setting-note">YouTube — Always on</p>
      <SettingSwitch checked={providers.includes('twitch')} label="Twitch" onChange={(on) => void setEnabled('twitch', on)} />
      <SettingSwitch checked={providers.includes('kick')} label="Kick" onChange={(on) => void setEnabled('kick', on)} />
      <p className="g-setting-note">Kick chat needs an account; playback works.</p>
    </fieldset>
  );
}
