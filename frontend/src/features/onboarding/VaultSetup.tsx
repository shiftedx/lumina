import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, FolderOpen, HardDrive, RefreshCw } from 'lucide-react';

import {
  createStorageRoot,
  getAdminMediaSetup,
  listImports,
  listStorageRoots,
  probeStorageRoot,
  startImport,
  type ImportVisibility,
  type ImportRun,
  type StorageRootRecord,
} from '../../api';
import { BrandMark, Button, ErrorState, Field, fieldProps, Input, Radio, Skeleton, StatusText } from '../../ui';
import { errorMessage } from '../../utils';
import './vault-setup.css';

type ScanState = { kind: 'idle' | 'probing' | 'starting' | 'active' | 'complete' | 'attention' | 'error'; message?: string };
type LoadState = 'loading' | 'ready' | 'error';
const ACTIVE_IMPORT_STATES = new Set(['queued', 'running', 'cancel_requested']);

const availability = (root: StorageRootRecord) => {
  if (!root.enabled) return { label: 'Paused', tone: 'muted' as const, detail: 'Enable this folder in Admin settings before scanning.' };
  const state = root.observation.state;
  if (state === 'available') return { label: 'Available', tone: 'ok' as const, detail: 'Mounted and ready to scan.' };
  if (state === 'offline') return { label: 'Offline', tone: 'danger' as const, detail: 'Mount this folder into the Lumina container, then check again.' };
  if (state === 'permission_denied') return { label: 'No permission', tone: 'danger' as const, detail: 'The container cannot read this folder. Check its mount permissions.' };
  if (state === 'identity_mismatch') return { label: 'Different storage', tone: 'danger' as const, detail: 'The mounted folder does not match the one Lumina registered.' };
  if (state === 'low_space') return { label: 'Low space', tone: 'attention' as const, detail: 'The folder is mounted, but its free space is low.' };
  return { label: 'Not checked', tone: 'muted' as const, detail: 'Check the mount before starting a scan.' };
};

const readyToScan = (root: StorageRootRecord) => root.enabled && (root.observation.state === 'available' || root.observation.state === 'low_space');

function VisibilityChoices({ name, value, disabled, onChange }: {
  name: string;
  value: ImportVisibility;
  disabled: boolean;
  onChange: (value: ImportVisibility) => void;
}) {
  return (
    <fieldset className="g-vault-visibility" disabled={disabled}>
      <legend>Who can see imported media</legend>
      <Radio checked={value === 'shared'} hint="Everyone in this Lumina household. Never public." label="Household" name={name} onChange={() => onChange('shared')} value="shared" />
      <Radio checked={value === 'private'} hint="Only you. Individual items can be shared later." label="Private" name={name} onChange={() => onChange('private')} value="private" />
    </fieldset>
  );
}

function ScanStatus({ state }: { state: ScanState }) {
  if (state.kind === 'probing') return <p className="g-vault-scan-status" role="status"><RefreshCw aria-hidden="true" />Checking folder…</p>;
  if (state.kind === 'starting') return <p className="g-vault-scan-status" role="status"><RefreshCw aria-hidden="true" />Starting scan…</p>;
  if (state.kind === 'active') return <p className="g-vault-scan-status is-success" role="status"><RefreshCw aria-hidden="true" />{state.message ?? 'Scan underway. New media will appear in your Library.'}</p>;
  if (state.kind === 'complete') return <p className="g-vault-scan-status is-success" role="status"><Check aria-hidden="true" />{state.message ?? 'Scan complete. This folder is in your Library.'}</p>;
  if (state.kind === 'attention') return <p className="g-vault-scan-status is-attention" role="status"><CircleAlert aria-hidden="true" />{state.message}</p>;
  if (state.kind === 'error') return <p className="g-vault-scan-status is-error" role="alert"><CircleAlert aria-hidden="true" />{state.message}</p>;
  return <p className="g-vault-scan-status">Ready to scan when you are.</p>;
}

export function VaultSetup({ onContinue, error = null, active = true }: { onContinue: () => Promise<void>; error?: string | null; active?: boolean }) {
  const alive = useRef(true);
  const loadGeneration = useRef(0);
  const latestRunIds = useRef<Record<string, string>>({});
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mountParents, setMountParents] = useState<string[]>([]);
  const [roots, setRoots] = useState<StorageRootRecord[]>([]);
  const [label, setLabel] = useState('');
  const [path, setPath] = useState('');
  const [visibility, setVisibility] = useState<ImportVisibility>('shared');
  const [rootVisibility, setRootVisibility] = useState<Record<string, ImportVisibility>>({});
  const [scanStates, setScanStates] = useState<Record<string, ScanState>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [continueError, setContinueError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  function restoreScans(runs: ImportRun[], replace = false, only?: ReadonlySet<string>) {
    const restored: Record<string, ScanState> = {};
    const visibilities: Record<string, ImportVisibility> = {};
    const runIds: Record<string, string> = {};
    for (const run of runs) {
      if ((only && !only.has(run.root_id)) || restored[run.root_id]) continue;
      runIds[run.root_id] = run.id;
      visibilities[run.root_id] = run.visibility;
      if (ACTIVE_IMPORT_STATES.has(run.state)) restored[run.root_id] = { kind: 'active', message: 'Scan underway. You can continue while Lumina builds your Library.' };
      else if (run.state === 'succeeded') restored[run.root_id] = { kind: 'complete' };
      else if (run.state === 'partial') restored[run.root_id] = { kind: 'attention', message: 'Some files need attention in Imports after you enter your vault.' };
      else if (run.state === 'needs_confirmation') restored[run.root_id] = { kind: 'attention', message: 'This scan needs review in Imports after you enter your vault.' };
      else restored[run.root_id] = { kind: 'error', message: run.error || 'The previous scan stopped. Retry when the folder is ready.' };
    }
    latestRunIds.current = replace ? runIds : { ...latestRunIds.current, ...runIds };
    setScanStates((current) => replace ? restored : { ...current, ...restored });
    setRootVisibility((current) => replace ? visibilities : { ...current, ...visibilities });
  }

  async function load() {
    const generation = ++loadGeneration.current;
    setLoadState('loading');
    setLoadError(null);
    try {
      const [setup, allRoots, runs] = await Promise.all([getAdminMediaSetup(), listStorageRoots(), listImports()]);
      if (!alive.current || generation !== loadGeneration.current) return;
      const externalRoots = allRoots.filter((root) => root.mode === 'external');
      setMountParents(setup.mount_parents);
      setRoots(externalRoots);
      restoreScans(runs, true, new Set(externalRoots.map((root) => root.id)));
      setLoadState('ready');
    } catch (failure) {
      if (!alive.current || generation !== loadGeneration.current) return;
      setLoadError(errorMessage(failure, 'Lumina could not load your media folders.'));
      setLoadState('error');
    }
  }

  useEffect(() => {
    alive.current = true;
    void load();
    return () => { alive.current = false; loadGeneration.current += 1; };
  }, []);

  useEffect(() => { if (active) headingRef.current?.focus({ preventScroll: true }); }, [active]);

  useEffect(() => {
    const activeRootIds = new Set(Object.entries(scanStates).filter(([, state]) => state.kind === 'active').map(([id]) => id));
    if (!active || !activeRootIds.size) return;
    const generation = loadGeneration.current;
    let current = true;
    let timer: number;
    const schedule = () => {
      timer = window.setTimeout(() => {
        void listImports().then((runs) => {
          if (current && alive.current && generation === loadGeneration.current) restoreScans(runs, false, activeRootIds);
        }).catch(() => undefined).finally(() => {
          if (current && alive.current && generation === loadGeneration.current) schedule();
        });
      }, 5000);
    };
    schedule();
    return () => { current = false; window.clearTimeout(timer); };
  }, [active, scanStates]);

  const interactionBusy = loadState === 'loading' || busy !== null;
  const allowedLocationHint = mountParents.length
    ? `Absolute container path. Allowed locations: ${mountParents.slice(0, 2).join(', ')}${mountParents.length > 2 ? `, and ${mountParents.length - 2} more` : ''}.`
    : 'No mount locations are allowed yet. Open “How mounted folders work” for the server setting.';
  const updateRoot = (updated: StorageRootRecord) => setRoots((current) => current.map((root) => root.id === updated.id ? updated : root));
  const setScanState = (id: string, state: ScanState) => setScanStates((current) => ({ ...current, [id]: state }));

  async function reconcileScan(rootId: string, knownRunId: string | undefined) {
    try {
      const runs = await listImports();
      if (!alive.current) return false;
      const latest = runs.find((run) => run.root_id === rootId);
      if (!latest || (latest.id === knownRunId && !ACTIVE_IMPORT_STATES.has(latest.state))) return false;
      restoreScans([latest], false, new Set([rootId]));
      return true;
    } catch {
      return false;
    }
  }

  async function scan(root: StorageRootRecord, chosenVisibility: ImportVisibility) {
    setBusy(`scan-${root.id}`);
    setScanState(root.id, { kind: readyToScan(root) ? 'starting' : 'probing' });
    try {
      let current = root;
      if (!readyToScan(current)) {
        current = await probeStorageRoot(root.id);
        if (!alive.current) return;
        updateRoot(current);
        if (!readyToScan(current)) {
          const state = availability(current);
          setScanState(root.id, { kind: 'error', message: `${state.label}. ${state.detail}` });
          return;
        }
        setScanState(root.id, { kind: 'starting' });
      }
      const knownRunId = latestRunIds.current[root.id];
      try {
        const started = await startImport({ root_id: root.id, visibility: chosenVisibility });
        if (alive.current) {
          if (started.state) restoreScans([started], false, new Set([root.id]));
          else setScanState(root.id, { kind: 'active' });
        }
      } catch (failure) {
        const recovered = await reconcileScan(root.id, knownRunId);
        if (alive.current && !recovered) setScanState(root.id, { kind: 'error', message: errorMessage(failure, 'Lumina could not start this scan.') });
      }
    } catch (failure) {
      if (alive.current) setScanState(root.id, { kind: 'error', message: errorMessage(failure, 'Lumina could not start this scan.') });
    } finally {
      if (alive.current) setBusy(null);
    }
  }

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedLabel = label.trim();
    const trimmedPath = path.trim();
    if (!trimmedPath.startsWith('/')) {
      setFormError('Use an absolute container path that starts with /.');
      return;
    }
    setBusy('add');
    setFormError(null);
    const generation = loadGeneration.current;
    try {
      const root = await createStorageRoot({ label: trimmedLabel, container_path: trimmedPath, mode: 'external', minimum_free_bytes: 0 });
      if (!alive.current) return;
      setRoots((current) => [...current.filter((entry) => entry.id !== root.id), root]);
      setRootVisibility((current) => ({ ...current, [root.id]: visibility }));
      setLabel('');
      setPath('');
      await scan(root, visibility);
    } catch (failure) {
      let recovered: StorageRootRecord | undefined;
      try {
        const currentRoots = await listStorageRoots();
        if (alive.current && generation === loadGeneration.current) recovered = currentRoots.find((root) => root.mode === 'external' && root.path === trimmedPath);
      } catch { /* The original create error remains the useful recovery message. */ }
      if (recovered && alive.current && generation === loadGeneration.current) {
        setRoots((current) => [...current.filter((entry) => entry.id !== recovered.id), recovered]);
        setRootVisibility((current) => ({ ...current, [recovered.id]: visibility }));
        setLabel('');
        setPath('');
        await scan(recovered, visibility);
      } else if (alive.current) {
        setFormError(errorMessage(failure, 'Lumina could not add this folder.'));
        setBusy(null);
      }
    }
  }

  async function finish() {
    setBusy('continue');
    setContinueError(null);
    try {
      await onContinue();
    } catch (failure) {
      if (alive.current) setContinueError(errorMessage(failure, 'Lumina could not save this step. Please try again.'));
    } finally {
      if (alive.current) setBusy(null);
    }
  }

  return (
    <main aria-busy={interactionBusy || undefined} className="g-vault-setup">
      <div aria-hidden="true" className="g-vault-art"><img alt="" src="/onboarding/vault-arrival-v2.jpg" /></div>
      <div className="g-vault-workspace">
        <header className="g-vault-header">
          <BrandMark size="bar" />
          <p className="g-vault-kicker">Media setup</p>
          <h1 ref={headingRef} tabIndex={-1}>Give your vault a home.</h1>
          <p>Bring folders you already own into Lumina. Your files stay where they are; Lumina reads them and builds your Library.</p>
        </header>

        <section aria-labelledby="vault-folders-title" className="g-vault-folders">
          <div className="g-vault-section-heading">
            <h2 id="vault-folders-title">Media folders</h2>
          </div>

          {loadState === 'loading' ? <Skeleton count={2} label="Loading media folders…" shape="row" /> : null}
          {loadState === 'error' ? <ErrorState body={loadError ?? undefined} onRetry={() => { void load(); }} title="Lumina could not load media setup." /> : null}

          {loadState === 'ready' ? (
            <>
              {roots.length ? <ul aria-label="Media folders" className="g-vault-root-list">{roots.map((root) => {
                const currentAvailability = availability(root);
                const scanState = scanStates[root.id] ?? { kind: 'idle' as const };
                const chosenVisibility = rootVisibility[root.id] ?? 'shared';
                const retrying = busy === `scan-${root.id}`;
                const scanFinished = scanState.kind === 'active' || scanState.kind === 'complete' || scanState.kind === 'attention';
                return <li className="g-vault-root" key={root.id}>
                  <div className="g-vault-root-main">
                    <FolderOpen aria-hidden="true" />
                    <div><h3>{root.label}</h3><p className="g-vault-path">{root.path}</p><StatusText tone={currentAvailability.tone}>{currentAvailability.label}</StatusText><small>{currentAvailability.detail}</small></div>
                  </div>
                  <VisibilityChoices disabled={interactionBusy || scanFinished || !root.enabled} name={`vault-visibility-${root.id}`} onChange={(value) => setRootVisibility((current) => ({ ...current, [root.id]: value }))} value={chosenVisibility} />
                  <ScanStatus state={scanState} />
                  <Button busy={retrying} disabled={interactionBusy || scanFinished || !root.enabled} icon={<HardDrive />} onClick={() => { void scan(root, chosenVisibility); }}>
                    {!root.enabled ? 'Scan unavailable' : scanState.kind === 'active' ? 'Scan underway' : scanState.kind === 'complete' ? 'Scan complete' : scanState.kind === 'attention' ? 'Review in Imports' : scanState.kind === 'error' ? 'Retry scan' : readyToScan(root) ? 'Start scan' : 'Check and scan'}
                  </Button>
                </li>;
              })}</ul> : null}

              {!mountParents.length ? <p className="g-vault-prerequisite" role="status"><strong>Server setup needed.</strong> Add an allowed mount parent and a read-only volume mount before connecting a folder.</p> : null}
              <form className="g-vault-form" onSubmit={(event) => { void add(event); }}>
                <div className="g-vault-fields">
                  <Field label="Folder name" required>{(ids) => <Input {...fieldProps(ids)} autoComplete="off" disabled={interactionBusy} maxLength={120} onChange={(event) => setLabel(event.target.value)} placeholder="Family films" required value={label} />}</Field>
                  <Field error={formError} hint={allowedLocationHint} label="Mounted path" required>{(ids) => <Input {...fieldProps(ids)} autoComplete="off" disabled={interactionBusy} maxLength={500} onChange={(event) => { setPath(event.target.value); if (formError) setFormError(null); }} placeholder="/media/films" required spellCheck={false} value={path} />}</Field>
                </div>
                <VisibilityChoices disabled={interactionBusy} name="vault-new-visibility" onChange={setVisibility} value={visibility} />
                <Button busy={busy === 'add'} disabled={interactionBusy || !mountParents.length || !label.trim() || !path.trim()} icon={<FolderOpen />} type="submit" variant="primary">Add and scan</Button>
              </form>

              <details className="g-vault-mount-help">
                <summary>How mounted folders work</summary>
                <div>
                  <p>A server operator must mount each host drive or NAS share into the Lumina container first. Lumina does not ask for share credentials or mount network storage itself.</p>
                  <p><strong>Allowed mount parents</strong></p>
                  {mountParents.length ? <ul>{mountParents.map((parent) => <li className="g-vault-path" key={parent}>{parent}</li>)}</ul> : <p>No allowed mount parents were reported. Configure one on the server before adding a folder.</p>}
                  <p><strong>Docker Compose example</strong></p>
                  <pre aria-label="Docker Compose mount example" role="region" tabIndex={0}><code>{`services:
  lumina:
    environment:
      LUMINA_STORAGE_MOUNT_PARENTS: /media
    volumes:
      - /srv/media:/media:ro`}</code></pre>
                </div>
              </details>
            </>
          ) : null}
        </section>

        {(error || continueError) ? <p className="g-vault-error" role="alert">{error || continueError}</p> : null}
        <footer className="g-vault-actions">
          <Button disabled={interactionBusy} onClick={() => { void finish(); }} variant="quiet">Set up media later</Button>
          <Button busy={busy === 'continue'} disabled={interactionBusy || !roots.length} onClick={() => { void finish(); }} variant="primary">Continue</Button>
        </footer>
      </div>
    </main>
  );
}
