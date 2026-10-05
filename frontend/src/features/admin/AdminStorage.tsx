import { createContext, type FormEvent, useContext, useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, MoreHorizontal, Plus, Trash2 } from 'lucide-react';

import {
  ApiRequestError, createStorageRoot, deleteStorageRoot, getStorageRules, listStorageRoots, probeStorageRoot, resolveStorage, saveStorageRules, startImport, updateStorageRoot,
  type ImportVisibility, type StorageDecision, type StorageMediaKind, type StorageRootRecord, type StorageRule, type StorageRuleSet, type StorageSource,
} from '../../api';
import { Button, Checkbox, ConfirmDialog, Field, fieldProps, IconButton, Input, Menu, ProgressBar, Radio, Select, StatusText, type StatusTone } from '../../ui';
import { errorMessage as errorText, formatBytes, formatDateTime } from '../../utils';
import { useUnsavedChanges } from '../settings/unsavedChanges';
import { useAdminResource } from './useAdminResource';

const SOURCES: Record<StorageSource, string> = { youtube: 'YouTube', twitch: 'Twitch', kick: 'Kick', soundcloud: 'SoundCloud', generic: 'Other sites' };
const KINDS: Record<StorageMediaKind, string> = { video: 'Video', audio: 'Audio only', recording: 'Live recording' };
export const STATES: Record<string, { label: string; reason: string }> = {
  available: { label: 'Available', reason: 'Reachable and verified.' },
  low_space: { label: 'Low space', reason: 'Free space is below this root’s reserve, so new downloads to it are held.' },
  offline: { label: 'Offline', reason: 'The folder is missing. Check that the drive is mounted into the container at this path.' },
  permission_denied: { label: 'No permission', reason: 'Lumina cannot read or write here. Check the folder’s owner and the mount’s read/write option.' },
  identity_mismatch: { label: 'Different disk mounted', reason: 'Something other than the registered drive is mounted at this path. Remount the original drive, then check again.' },
};
/** Who can see imported media: the household by default, or only you. Key order is display order. */
export const IMPORT_VISIBILITY: Record<ImportVisibility, { label: string; detail: string }> = {
  shared: { label: 'Household', detail: 'Everyone with a Lumina account in this household sees them. Never public.' },
  private: { label: 'Private', detail: 'Only you see the imported items. You can share single items later.' },
};
const rootTone = (state: string | null | undefined): StatusTone => (state === 'available' ? 'ok' : state === 'low_space' ? 'attention' : state ? 'danger' : 'muted');
/** Lets a started import refresh the Imports row in the same section. Without a provider it is a no-op. */
export const LibraryRefreshContext = createContext<{ version: number; bump: () => void }>({ version: 0, bump: () => undefined });

/** The "Import now" step for an external root: shared with the household by default, Private available. */
function ImportNow({ root, onClose }: { root: StorageRootRecord; onClose: (started: boolean) => void }) {
  const { bump } = useContext(LibraryRefreshContext);
  const [visibility, setVisibility] = useState<ImportVisibility>('shared');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = `root-${root.id}-import`;
  async function start() {
    setBusy(true);
    setError(null);
    try {
      await startImport({ root_id: root.id, visibility });
      bump();
      onClose(true);
    } catch (failure) {
      setError(errorText(failure, 'Unable to start the import.'));
      setBusy(false);
    }
  }
  return (
    <div aria-labelledby={id} className="admin-confirm" role="group">
      <p id={id}><strong>Import {root.label} now?</strong> Lumina reads the folder and adds its media to the Library. Nothing in the folder changes.</p>
      <fieldset className="admin-import-step">
        <legend>Who can see the imported media</legend>
        {(Object.keys(IMPORT_VISIBILITY) as ImportVisibility[]).map((value) => (
          <Radio checked={visibility === value} key={value} label={<><strong>{IMPORT_VISIBILITY[value].label}</strong> — {IMPORT_VISIBILITY[value].detail}</>} name={`${id}-visibility`} onChange={() => setVisibility(value)} value={value} />
        ))}
      </fieldset>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <div className="admin-member-actions">
        <Button autoFocus busy={busy} disabled={busy} onClick={() => { void start(); }} variant="primary">{busy ? 'Starting…' : 'Import now'}</Button>
        <Button disabled={busy} onClick={() => onClose(false)}>Not now</Button>
      </div>
    </div>
  );
}

const toggle = <T,>(list: T[], value: T) => (list.includes(value) ? list.filter((item) => item !== value) : [...list, value]);
const height = (value: string) => (value.trim() ? Math.max(1, Math.round(Number(value))) || null : null);
const byPriority = (rules: StorageRule[]) => [...rules].sort((a, b) => a.priority - b.priority || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

/** Mirrors the server's template rules so mistakes show next to the field before saving; the server re-validates. */
export function ruleProblem(rule: StorageRule, managed: StorageRootRecord[]): string | null {
  if (!managed.some((root) => root.id === rule.target_root_id)) return 'Choose a managed root for this rule.';
  if (rule.min_height && rule.max_height && rule.min_height > rule.max_height) return 'Minimum height must not be above maximum height.';
  const unknown = [...rule.relative_template.matchAll(/\{([^}]*)\}/g)].find((match) => !['source', 'kind', 'height'].includes(match[1]));
  if (unknown) return `Folder can only use {source}, {kind} and {height}, not {${unknown[1]}}.`;
  const parts = rule.relative_template.trim().split('/');
  if (rule.relative_template.trim().startsWith('/') || parts.some((part) => part === '..' || part.startsWith('.') || /[\\:]/.test(part))) {
    return 'Folder must be a plain relative folder (no leading /, .., hidden names, \\ or :).';
  }
  return null;
}

function RootRow({ root, busy, offerImport = false, onProbe, onEnabled, onRemove, error }: { root: StorageRootRecord; busy: boolean; offerImport?: boolean; onProbe: () => void; onEnabled: () => void; onRemove: () => void; error?: string }) {
  const [confirming, setConfirming] = useState(false);
  const [importing, setImporting] = useState(offerImport && root.mode === 'external');
  const [started, setStarted] = useState(false);
  const state = root.observation.state;
  const known = state ? STATES[state] : undefined;
  const reason = !state ? 'Not checked yet. Use “Check now”.' : state === 'available' && root.mode === 'external' ? 'Reachable. Lumina only reads from external roots.' : known?.reason ?? state;
  const { free_bytes: free, total_bytes: total } = root.observation;
  const hasSpace = free != null && total != null && total > 0;
  const refocusActions = () => window.setTimeout(() => document.querySelector<HTMLElement>(`[data-root-actions="${root.id}"]`)?.focus(), 0);
  return (
    <>
      <tr aria-busy={busy}>
        <th scope="row">
          {root.label}
          <small>{root.mode === 'managed' ? 'Managed' : 'External · read-only'}</small>
          {root.enabled ? null : <small><StatusText tone="muted">Disabled</StatusText></small>}
        </th>
        <td><span className="g-mono">{root.path}</span></td>
        <td>
          <StatusText tone={rootTone(state)}>{known?.label ?? 'Not checked'}</StatusText>
          <small>{reason}</small>
          <small>Checked {formatDateTime(root.observation.checked_at, 'Never')}</small>
        </td>
        <td>
          {hasSpace ? <ProgressBar label={`${root.label} space used`} value={((total - free) / total) * 100} /> : null}
          <span className="g-tabular">{free == null ? 'Unavailable' : `${formatBytes(free)} of ${formatBytes(total)}`}</span>
          {root.mode === 'managed' ? <small>Keeps {root.minimum_free_bytes ? formatBytes(root.minimum_free_bytes) : 'none'} free</small> : null}
        </td>
        <td className="g-tabular">{root.artifact_count}</td>
        <td>
          <Menu
            align="end"
            items={[
              ...(root.mode === 'external' ? [{ kind: 'item' as const, label: 'Import', disabled: busy || importing || !root.enabled, onSelect: () => { setStarted(false); setImporting(true); } }] : []),
              { kind: 'item', label: 'Check now', disabled: busy, onSelect: onProbe },
              { kind: 'item', label: root.enabled ? 'Disable' : 'Enable', disabled: busy, onSelect: onEnabled },
              { kind: 'item', label: 'Remove', danger: true, disabled: busy, onSelect: () => setConfirming(true) },
            ]}
            trigger={(props) => <IconButton {...props} data-root-actions={root.id} icon={<MoreHorizontal />} label={`Actions for ${root.label}`} />}
          />
          <ConfirmDialog
            body="Lumina forgets this location; no files are deleted. A root that still holds registered media or is used by a rule cannot be removed."
            confirmLabel="Remove"
            danger
            onCancel={() => setConfirming(false)}
            onConfirm={() => { setConfirming(false); onRemove(); }}
            open={confirming}
            title={`Remove ${root.label}?`}
          />
        </td>
      </tr>
      {importing || started || error ? (
        <tr className="g-root-detail">
          <td colSpan={6}>
            {importing ? <ImportNow onClose={(done) => { setImporting(false); setStarted(done); if (!done) refocusActions(); }} root={root} /> : null}
            {started ? <p className="admin-note" role="status">Import started. Its progress shows under Imports.</p> : null}
            {error ? <p className="auth-error" role="alert"><StatusText tone="danger">{error}</StatusText></p> : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

function AddRootForm({ onAdded }: { onAdded: (root: StorageRootRecord) => void }) {
  const [path, setPath] = useState('');
  const [label, setLabel] = useState('');
  const [mode, setMode] = useState<'managed' | 'external'>('managed');
  const [reserveGb, setReserveGb] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const root = await createStorageRoot({ container_path: path.trim(), label: label.trim(), mode, minimum_free_bytes: mode === 'managed' ? Math.round((Number(reserveGb) || 0) * 1024 ** 3) : 0 });
      setPath(''); setLabel(''); setReserveGb('');
      onAdded(root);
    } catch (addError) {
      setError(errorText(addError, 'Unable to add this storage root.'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form aria-labelledby="admin-add-root-title" className="admin-add-root" onSubmit={(event) => { void submit(event); }}>
      <h3 id="admin-add-root-title">Add a root</h3>
      <p className="admin-note">Mount the host folder into the container first — for example <code>/srv/media:/media</code> in the compose file — then enter the path <strong>as the container sees it</strong>. It must sit inside a mount parent listed in <code>LUMINA_STORAGE_MOUNT_PARENTS</code>.</p>
      <div className="g-subrows">
        <Field error={error} label="Container path">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" onChange={(event) => setPath(event.target.value)} placeholder="/media/movies" required spellCheck={false} value={path} />}</Field>
        <Field label="Label">{(ids) => <Input {...fieldProps(ids)} maxLength={120} onChange={(event) => setLabel(event.target.value)} placeholder="Movies drive" required value={label} />}</Field>
        <Field label="Mode">
          {(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => setMode(event.target.value as 'managed' | 'external')} value={mode}>
              <option value="managed">Managed — Lumina saves downloads here</option>
              <option value="external">External — read-only, for imports</option>
            </Select>
          )}
        </Field>
        {mode === 'managed' ? <Field label="Keep free (GB)">{(ids) => <Input {...fieldProps(ids)} inputMode="decimal" min="0" onChange={(event) => setReserveGb(event.target.value)} placeholder="0" step="any" type="number" value={reserveGb} />}</Field> : null}
      </div>
      <div className="g-subrows"><div className="g-actions"><Button busy={busy} type="submit" variant="primary">{busy ? 'Checking path…' : 'Add root'}</Button></div></div>
    </form>
  );
}

function RuleEditor({ rule, index, count, managed, problem, onChange, onMove, onDelete }: {
  rule: StorageRule; index: number; count: number; managed: StorageRootRecord[]; problem: string | null;
  onChange: (rule: StorageRule) => void; onMove: (delta: -1 | 1) => void; onDelete: () => void;
}) {
  const id = `rule-${rule.id}`;
  const set = (patch: Partial<StorageRule>) => onChange({ ...rule, ...patch });
  return (
    <li className="admin-rule">
      <fieldset aria-describedby={problem ? `${id}-problem` : undefined}>
        <legend>Rule {index + 1}</legend>
        <div className="admin-rule-head">
          <Checkbox checked={rule.enabled} label="Enabled" onChange={(event) => set({ enabled: event.target.checked })} />
          <div className="admin-member-actions">
            <IconButton disabled={index === 0} icon={<ArrowUp />} label={`Move rule ${index + 1} up`} onClick={() => onMove(-1)} />
            <IconButton disabled={index === count - 1} icon={<ArrowDown />} label={`Move rule ${index + 1} down`} onClick={() => onMove(1)} />
            <IconButton icon={<Trash2 />} label={`Delete rule ${index + 1}`} onClick={onDelete} />
          </div>
        </div>
        <fieldset className="admin-chips"><legend>Sources <small>(none ticked = any)</small></legend>
          {(Object.keys(SOURCES) as StorageSource[]).map((source) => <Checkbox checked={rule.sources.includes(source)} key={source} label={SOURCES[source]} onChange={() => set({ sources: toggle(rule.sources, source) })} />)}
        </fieldset>
        <fieldset className="admin-chips"><legend>Kinds <small>(none ticked = any)</small></legend>
          {(Object.keys(KINDS) as StorageMediaKind[]).map((kind) => <Checkbox checked={rule.media_kinds.includes(kind)} key={kind} label={KINDS[kind]} onChange={() => set({ media_kinds: toggle(rule.media_kinds, kind) })} />)}
        </fieldset>
        <div className="admin-rule-fields">
          <Field label="Min height (px)">{(ids) => <Input {...fieldProps(ids)} min="1" onChange={(event) => set({ min_height: height(event.target.value) })} placeholder="Any" type="number" value={rule.min_height ?? ''} />}</Field>
          <Field label="Max height (px)">{(ids) => <Input {...fieldProps(ids)} min="1" onChange={(event) => set({ max_height: height(event.target.value) })} placeholder="Any" type="number" value={rule.max_height ?? ''} />}</Field>
          <Field label="Save to">
            {(ids) => (
              <Select {...fieldProps(ids)} onChange={(event) => set({ target_root_id: event.target.value })} value={rule.target_root_id}>
                {managed.some((root) => root.id === rule.target_root_id) ? null : <option value={rule.target_root_id}>Choose a root…</option>}
                {managed.map((root) => <option key={root.id} value={root.id}>{root.label}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Folder inside root">{(ids) => <Input {...fieldProps(ids)} maxLength={240} onChange={(event) => set({ relative_template: event.target.value })} placeholder="{source}/{height}" spellCheck={false} value={rule.relative_template} />}</Field>
        </div>
        {rule.min_height || rule.max_height ? <p className="admin-note">Height rules match the actual downloaded height; audio and not-yet-known heights skip this rule.</p> : null}
        {problem ? <p className="auth-error" id={`${id}-problem`} role="alert">{problem}</p> : null}
      </fieldset>
    </li>
  );
}

type Draft = { default_root_id: string | null; rules: StorageRule[] };
const normalize = (set: StorageRuleSet): Draft => ({ default_root_id: set.default_root_id, rules: byPriority(set.rules) });

export function AdminStorage() {
  const { data: roots, setData: setRoots, error: loadError } = useAdminResource(listStorageRoots, 'Unable to load storage roots.');
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [rootErrors, setRootErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<StorageRuleSet | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saveState, setSaveState] = useState<{ busy?: boolean; error?: string; conflict?: boolean; done?: boolean }>({});
  const [probe, setProbe] = useState<{ source: StorageSource; media_kind: StorageMediaKind; height: string }>({ source: 'youtube', media_kind: 'video', height: '1080' });
  const [decision, setDecision] = useState<{ result?: StorageDecision; error?: string; busy?: boolean }>({});
  const mounted = useRef(true);
  const [offered, setOffered] = useState<string | null>(null);
  const { bump } = useContext(LibraryRefreshContext);

  function loadRules() {
    void getStorageRules().then((set) => { if (mounted.current) { setSaved(set); setDraft(normalize(set)); setSaveState({}); } })
      .catch((error) => { if (mounted.current) setSaveState({ error: errorText(error, 'Unable to load storage rules.') }); });
  }
  useEffect(() => {
    mounted.current = true;
    loadRules();
    return () => { mounted.current = false; };
  }, []);

  async function rootAction(root: StorageRootRecord, action: () => Promise<StorageRootRecord | void>) {
    setBusy((ids) => new Set(ids).add(root.id));
    setRootErrors((errors) => { const next = { ...errors }; delete next[root.id]; return next; });
    try {
      const updated = await action();
      if (!mounted.current) return;
      setRoots((list) => (updated ? list?.map((entry) => (entry.id === root.id ? updated : entry)) : list?.filter((entry) => entry.id !== root.id)) ?? null);
    } catch (error) {
      const text = errorText(error, 'Unable to update this root.');
      const hint = error instanceof ApiRequestError && error.status === 409 ? ' Disable it instead to stop new downloads while keeping its files.' : '';
      if (mounted.current) setRootErrors((errors) => ({ ...errors, [root.id]: text + hint }));
    } finally {
      if (mounted.current) setBusy((ids) => { const next = new Set(ids); next.delete(root.id); return next; });
    }
  }

  const managed = roots?.filter((root) => root.mode === 'managed') ?? [];
  const problems = draft?.rules.map((rule) => ruleProblem(rule, managed)) ?? [];
  const dirty = !!draft && !!saved && JSON.stringify(draft) !== JSON.stringify(normalize(saved));
  useUnsavedChanges(dirty, 'Library & storage');
  const editRules = (rules: StorageRule[]) => { setDraft((current) => (current ? { ...current, rules } : current)); setSaveState((state) => (state.conflict ? state : {})); };

  function move(index: number, delta: -1 | 1) {
    if (!draft) return;
    const rules = [...draft.rules];
    [rules[index], rules[index + delta]] = [rules[index + delta], rules[index]];
    editRules(rules);
    // Keep keyboard focus on the same control of the moved rule.
    const at = index + delta + 1;
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`[aria-label="Move rule ${at} ${delta < 0 ? 'up' : 'down'}"]:not(:disabled), [aria-label="Move rule ${at} ${delta < 0 ? 'down' : 'up'}"]`)?.focus());
  }

  async function save() {
    if (!draft || !saved) return;
    setSaveState({ busy: true });
    try {
      // List order is the evaluation order; priorities are derived from it.
      const next = await saveStorageRules({ expected_revision: saved.revision, default_root_id: draft.default_root_id, rules: draft.rules.map((rule, index) => ({ ...rule, priority: (index + 1) * 10 })) });
      if (!mounted.current) return;
      setSaved(next); setDraft(normalize(next)); setSaveState({ done: true });
    } catch (error) {
      if (!mounted.current) return;
      const conflict = error instanceof ApiRequestError && error.status === 409;
      setSaveState({ conflict, error: conflict ? 'These rules were changed elsewhere since you opened them. Reload to see the latest version; your unsaved edits here will be replaced.' : errorText(error, 'Unable to save storage rules.') });
    }
  }

  async function test(event: FormEvent) {
    event.preventDefault();
    setDecision({ busy: true });
    try {
      const result = await resolveStorage({ source: probe.source, media_kind: probe.media_kind, height: probe.media_kind === 'audio' ? null : height(probe.height) });
      if (mounted.current) setDecision({ result });
    } catch (error) {
      if (mounted.current) setDecision({ error: errorText(error, 'Unable to test this download.') });
    }
  }

  const rootLabel = (id: string) => roots?.find((root) => root.id === id)?.label ?? 'Unknown root';

  return (
    <div aria-busy={!roots && !loadError} className="admin-storage">
      <section aria-labelledby="admin-storage-roots-title" className="admin-panel">
        <h2 id="admin-storage-roots-title">Storage roots</h2>
        <p className="admin-note">Folders Lumina can use. <strong>Managed</strong> roots receive downloads; <strong>external</strong> roots are read-only libraries you import from. Changes apply to new downloads only — existing files are never moved.</p>
        {loadError ? <p className="auth-error" role="alert">{loadError}</p> : null}
        {!roots && !loadError ? <p className="admin-note" role="status">Loading storage roots…</p> : null}
        {roots && !roots.length ? <p className="admin-note">No extra roots yet. Downloads go to the built-in library folder.</p> : null}
        {roots?.length ? (
          <div aria-label="Storage roots" className="admin-table-scroll" role="region" tabIndex={0}>
            <table className="admin-table g-table g-roots">
              <thead><tr><th scope="col">Root</th><th scope="col">Path</th><th scope="col">State</th><th scope="col">Free space</th><th scope="col">Files</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {roots.map((root) => (
                  <RootRow
                    busy={busy.has(root.id)}
                    error={rootErrors[root.id]}
                    key={root.id}
                    offerImport={root.id === offered}
                    onEnabled={() => { void rootAction(root, () => updateStorageRoot(root.id, { enabled: !root.enabled })); }}
                    onProbe={() => { void rootAction(root, () => probeStorageRoot(root.id)); }}
                    onRemove={() => { void rootAction(root, () => deleteStorageRoot(root.id)); }}
                    root={root}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <AddRootForm onAdded={(root) => { setRoots((list) => [...(list ?? []), root]); setOffered(root.id); bump(); }} />
      </section>

      <section aria-labelledby="admin-storage-rules-title" className="admin-panel">
        <h2 id="admin-storage-rules-title">Where downloads go</h2>
        <p className="admin-note">Rules are checked top to bottom; the first enabled rule whose conditions all match wins. Within a condition, any ticked value matches. Anything no rule matches goes to the default destination.</p>
        {draft ? (
          <>
            {draft.rules.length ? (
              <ol aria-label="Routing rules" className="admin-rule-list">
                {draft.rules.map((rule, index) => (
                  <RuleEditor
                    count={draft.rules.length}
                    index={index}
                    key={rule.id}
                    managed={managed}
                    onChange={(next) => editRules(draft.rules.map((entry) => (entry.id === rule.id ? next : entry)))}
                    onDelete={() => editRules(draft.rules.filter((entry) => entry.id !== rule.id))}
                    onMove={(delta) => move(index, delta)}
                    problem={problems[index]}
                    rule={rule}
                  />
                ))}
              </ol>
            ) : <p className="admin-note">No rules yet — everything goes to the default destination.</p>}
            <div className="admin-rule-footer">
              <Button disabled={!managed.length} icon={<Plus />} onClick={() => editRules([...draft.rules, { id: `rule-${Date.now().toString(36)}`, enabled: true, priority: 0, sources: [], media_kinds: [], min_height: null, max_height: null, target_root_id: draft.default_root_id ?? managed[0]?.id ?? '', relative_template: '{source}' }])}>Add rule</Button>
              <Field label="Default destination">
                {(ids) => (
                  <Select {...fieldProps(ids)} onChange={(event) => { const value = event.target.value || null; setDraft((current) => (current ? { ...current, default_root_id: value } : current)); }} value={draft.default_root_id ?? ''}>
                    <option value="">Built-in library folder</option>
                    {managed.map((root) => <option key={root.id} value={root.id}>{root.label}</option>)}
                  </Select>
                )}
              </Field>
            </div>
            <div className="g-save-bar">
              <p className="g-save-status" role="status">{saveState.busy ? 'Saving…' : saveState.done ? 'Saved. New downloads use these rules; existing files stay where they are.' : dirty ? <span className="g-label">Unsaved changes</span> : `Revision ${saved?.revision ?? 0}.`}</p>
              <div className="g-save-actions">
                {dirty ? <Button onClick={() => saved && (setDraft(normalize(saved)), setSaveState({}))} variant="quiet">Discard</Button> : null}
                <Button busy={saveState.busy} disabled={!dirty || saveState.conflict || problems.some(Boolean)} onClick={() => { void save(); }} variant="primary">Save rules</Button>
              </div>
            </div>
          </>
        ) : saveState.error ? null : <p className="admin-note" role="status">Loading rules…</p>}
        {saveState.error ? (
          <div className="admin-confirm" role="alert">
            <p>{saveState.error}</p>
            {saveState.conflict || !draft ? <div><Button onClick={loadRules}>Reload rules</Button></div> : null}
          </div>
        ) : null}
      </section>

      <section aria-labelledby="admin-storage-test-title" className="admin-panel">
        <h2 id="admin-storage-test-title">Test a download</h2>
        <p className="admin-note">Shows where a finished file with these facts would be saved under the <strong>saved</strong> rules. Use the actual downloaded height — a “best quality” request that resolved to 1080p routes as 1080p.{dirty ? ' Save your edits first to test them.' : ''}</p>
        <form className="admin-test-form" onSubmit={(event) => { void test(event); }}>
          <Field label="Source">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => setProbe({ ...probe, source: event.target.value as StorageSource })} value={probe.source}>{Object.entries(SOURCES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select>}</Field>
          <Field label="Kind">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => setProbe({ ...probe, media_kind: event.target.value as StorageMediaKind })} value={probe.media_kind}>{Object.entries(KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select>}</Field>
          <Field label="Actual height (px)">{(ids) => <Input {...fieldProps(ids)} disabled={probe.media_kind === 'audio'} min="1" onChange={(event) => setProbe({ ...probe, height: event.target.value })} placeholder="Unknown" type="number" value={probe.media_kind === 'audio' ? '' : probe.height} />}</Field>
          <Button busy={decision.busy} type="submit" variant="primary">Test</Button>
        </form>
        <div aria-live="polite">
          {decision.result ? (
            <div className="admin-decision">
              <p><strong>{decision.result.root_label || rootLabel(decision.result.root_id)}</strong> <span aria-hidden="true">›</span> <code className="g-mono">{decision.result.relative_path || '(top level)'}</code></p>
              <p className="admin-note">{decision.result.rule_id ? `Rule ${Math.max(1, byPriority(saved?.rules ?? []).findIndex((rule) => rule.id === decision.result?.rule_id) + 1)} matched.` : decision.result.reason} Rules revision {decision.result.rule_revision}.</p>
              {decision.result.can_admit ? null : <p className="auth-error">This root cannot receive files right now, so such a download would wait rather than be saved elsewhere.</p>}
            </div>
          ) : null}
          {decision.error ? <p className="auth-error" role="alert">{decision.error}</p> : null}
        </div>
      </section>
    </div>
  );
}
