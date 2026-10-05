import { type FormEvent, type MouseEvent, useContext, useEffect, useRef, useState } from 'react';

import {
  cancelImport, confirmImport, getImport, listImportEntries, listImports, listStorageRoots, probeStorageRoot, resumeImport, startImport,
  type ImportEntry, type ImportOutcome, type ImportRun, type ImportVisibility, type StorageRootRecord,
} from '../../api';
import { Button, ButtonLink, ConfirmDialog, Radio, Select, StatusText, type StatusTone } from '../../ui';
import { errorMessage as errorText, formatDateTime as when } from '../../utils';
import { IMPORT_VISIBILITY as VISIBILITY, LibraryRefreshContext, STATES as ROOT_STATES } from './AdminStorage';

const ACTIVE = new Set(['running', 'cancel_requested']);
const RESUMABLE = new Set(['cancelled', 'failed', 'interrupted']);
const ONLINE = new Set(['available', 'low_space']);
const RUN_STATES: Record<string, string> = {
  running: 'Scanning', cancel_requested: 'Stopping', cancelled: 'Cancelled', failed: 'Failed', interrupted: 'Interrupted', partial: 'Partial', succeeded: 'Complete',
  needs_confirmation: 'Needs your confirmation',
};
const REASONS: Record<string, string> = {
  offline: 'The folder is not mounted. Nothing was marked missing. Remount the drive, check it under Storage roots, then resume.',
  permission_denied: 'Lumina cannot read this folder. Fix its permissions, then resume.',
  identity_mismatch: 'A different disk is mounted at this path, so Lumina stopped instead of treating it as the same library. Remount the original drive, then resume.',
  root_removed: 'This folder was removed from Storage roots.',
  root_empty: 'The folder looked empty although it held imported files before, so nothing was marked missing. If the drive is unmounted, remount it and rescan.',
  unreadable: 'Could not be read — check permissions, or the file may be damaged.',
  symlink: 'Symbolic link — never followed, for safety.',
  too_deep: 'Nested too deeply; this folder was not scanned.',
  possible_move: 'Looks like a moved copy of an existing item. Added as a new item; check it and remove the duplicate if needed.',
};
const COUNTERS: [string, string][] = [
  ['inspected', 'Files checked'], ['indexed', 'New'], ['updated', 'Updated'], ['relinked', 'Moved'], ['unchanged', 'Unchanged'],
  ['skipped', 'Skipped'], ['failed', 'Failed'], ['missing', 'No longer found'],
];
const OUTCOMES: Record<ImportOutcome, string> = { failed: 'Failed', skipped: 'Skipped', review: 'Needs review' };
const ENTRY_LIMIT = 100;
const RUN_TONES: Record<string, StatusTone> = { succeeded: 'ok', running: 'attention', cancel_requested: 'attention', needs_confirmation: 'attention', partial: 'attention', failed: 'danger', interrupted: 'danger', cancelled: 'muted' };
const OUTCOME_TONES: Record<ImportOutcome, StatusTone> = { failed: 'danger', skipped: 'muted', review: 'attention' };
const rootTone = (state: string | null | undefined): StatusTone => (state === 'available' ? 'ok' : state === 'low_space' ? 'attention' : state ? 'danger' : 'muted');

/** In-app link: plain clicks go through the app router (it listens to popstate); modified clicks open natively. */
export function appLink(path: string) {
  return {
    href: path,
    onClick: (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      window.history.pushState(null, '', path);
      window.dispatchEvent(new PopStateEvent('popstate'));
    },
  };
}

function statusText(run: ImportRun): string {
  const reason = run.error ? REASONS[run.error] ?? run.error : '';
  if (run.state === 'needs_confirmation') return `${run.counters.missing_candidates ?? 0} of ${run.counters.available ?? 0} files would be marked missing. If a drive is unmounted, remount it. Otherwise confirm.`;
  if (run.state === 'running') return 'Scanning. Counts grow as each batch finishes; the total is not known until the scan ends.';
  if (run.state === 'cancel_requested') return 'Stopping after the current batch.';
  if (run.state === 'succeeded') return 'Complete. Every file in the folder was checked.';
  if (run.state === 'partial') return `Partial. Some files could not be checked; everything else is in the Library. ${reason}`.trim();
  if (run.state === 'cancelled') return 'Cancelled. Everything indexed so far stays in the Library; resume continues where it stopped.';
  if (run.state === 'interrupted') return 'Lumina restarted during the scan. Resume continues where it stopped.';
  return reason || 'The import stopped.';
}

export function AdminImports() {
  const [roots, setRoots] = useState<StorageRootRecord[] | null>(null);
  const [runs, setRuns] = useState<ImportRun[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rootId, setRootId] = useState('');
  const [visibility, setVisibility] = useState<ImportVisibility>('shared');
  const [run, setRun] = useState<ImportRun | null>(null);
  const [entries, setEntries] = useState<ImportEntry[] | null>(null);
  const [filter, setFilter] = useState<ImportOutcome | ''>('');
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmMissing, setConfirmMissing] = useState<ImportRun | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const delay = useRef(1000);
  const mounted = useRef(true);
  const { version } = useContext(LibraryRefreshContext);

  const external = roots?.filter((root) => root.mode === 'external') ?? [];
  const ready = (root: StorageRootRecord) => root.enabled && ONLINE.has(root.observation.state ?? '');
  const label = (id: string) => roots?.find((root) => root.id === id)?.label ?? 'Removed folder';
  const selected = external.find((root) => root.id === rootId);

  useEffect(() => {
    mounted.current = true;
    delay.current = 1000;
    void Promise.all([listStorageRoots(), listImports()]).then(([rootList, runList]) => {
      if (!mounted.current) return;
      setRoots(rootList);
      setRuns(runList);
      setRun(runList[0] ?? null);
      setRootId(rootList.find((root) => root.mode === 'external' && root.enabled && ONLINE.has(root.observation.state ?? ''))?.id ?? '');
    }).catch((error) => { if (mounted.current) setLoadError(errorText(error, 'Unable to load imports.')); });
    return () => { mounted.current = false; };
  }, [version]);

  function show(next: ImportRun) {
    setRun(next);
    setRuns((list) => (list.some((entry) => entry.id === next.id) ? list.map((entry) => (entry.id === next.id ? next : entry)) : [next, ...list]));
  }

  // Poll the shown run while it is active, backing off to 8 s between polls.
  useEffect(() => {
    if (!run || !ACTIVE.has(run.state)) return;
    const timer = window.setTimeout(() => {
      delay.current = Math.min(delay.current * 1.5, 8000);
      void getImport(run.id).then((next) => { if (mounted.current) show(next); })
        .catch(() => { if (mounted.current) setRun((current) => (current ? { ...current } : current)); });
    }, delay.current);
    return () => window.clearTimeout(timer);
  }, [run]);

  const done = run ? !ACTIVE.has(run.state) : false;
  useEffect(() => {
    if (!run || !done) return;
    let current = true;
    setEntries(null);
    void listImportEntries(run.id, filter || undefined).then((list) => { if (current) setEntries(list); })
      .catch(() => { if (current) setEntries([]); });
    return () => { current = false; };
  }, [run?.id, done, filter]);

  async function act(name: string, action: () => Promise<ImportRun>) {
    setBusy(name);
    setActionError(null);
    try {
      const next = await action();
      if (!mounted.current) return;
      delay.current = 1000;
      show(next);
    } catch (error) {
      if (mounted.current) setActionError(errorText(error, 'That did not work. Try again.'));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  async function probe(root: StorageRootRecord) {
    setBusy(`probe-${root.id}`);
    try {
      const updated = await probeStorageRoot(root.id);
      if (!mounted.current) return;
      setRoots((list) => list?.map((entry) => (entry.id === root.id ? updated : entry)) ?? null);
      if (!rootId && ready(updated)) setRootId(updated.id);
    } catch (error) {
      if (mounted.current) setActionError(errorText(error, 'Unable to check this folder.'));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (selected && ready(selected)) void act('start', () => startImport({ root_id: selected.id, visibility }));
  }

  if (loadError) return <p className="auth-error" role="alert">{loadError}</p>;
  if (!roots) return <p aria-busy="true" className="admin-note">Loading imports…</p>;

  const imported = run ? ['indexed', 'updated', 'relinked', 'unchanged'].reduce((sum, key) => sum + (run.counters[key] ?? 0), 0) : 0;
  return (
    <div className="admin-storage">
      <section aria-labelledby="admin-import-title" className="admin-panel">
        <h2 id="admin-import-title">Import a folder</h2>
        <p className="admin-note"><strong>Lumina only reads the folder.</strong> Originals are never moved, renamed, changed or deleted; the Library just points at them. This is not a download.</p>
        <form className="admin-import-form" onSubmit={submit}>
          <fieldset className="admin-import-step">
            <legend>1. Choose a folder</legend>
            {external.length ? (
              <ul className="admin-import-roots">
                {external.map((root) => {
                  const state = root.observation.state;
                  const usable = ready(root);
                  const reason = !root.enabled ? 'Disabled under Storage roots.' : !state ? 'Not checked yet. Use “Check now”.' : usable ? 'Reachable. Lumina only reads it.' : ROOT_STATES[state]?.reason ?? state;
                  return (
                    <li key={root.id}>
                      <Radio aria-describedby={`import-root-${root.id}`} checked={rootId === root.id} disabled={!usable} label={<><strong>{root.label}</strong> <code className="g-mono">{root.path}</code></>} name="import-root" onChange={() => setRootId(root.id)} value={root.id} />
                      <p className="admin-root-state" id={`import-root-${root.id}`}><StatusText tone={rootTone(state)}>{state ? ROOT_STATES[state]?.label ?? state : 'Not checked'}</StatusText> {reason}</p>
                      <Button aria-label={`Check ${root.label} now`} disabled={busy !== null} onClick={() => { void probe(root); }}>Check now</Button>
                    </li>
                  );
                })}
              </ul>
            ) : <p className="admin-note">No external folders yet. Mount the folder into the container, then add it under Storage roots as <strong>External — read-only</strong>.</p>}
          </fieldset>
          <fieldset className="admin-import-step">
            <legend>2. Who can see the imported media</legend>
            {(Object.keys(VISIBILITY) as ImportVisibility[]).map((value) => (
              <Radio checked={visibility === value} key={value} label={<><strong>{VISIBILITY[value].label}</strong> — {VISIBILITY[value].detail}</>} name="import-visibility" onChange={() => setVisibility(value)} value={value} />
            ))}
          </fieldset>
          <fieldset className="admin-import-step">
            <legend>3. Review and start</legend>
            <p className="admin-root-state" id="import-summary">{selected && ready(selected)
              ? <>Lumina will read <strong>{selected.label}</strong> (read-only) and add its media as <strong>{VISIBILITY[visibility].label.toLowerCase()}</strong> items. Nothing in the folder changes.</>
              : 'Choose a reachable external folder first.'}</p>
            <Button aria-describedby="import-summary" busy={busy === 'start'} disabled={!selected || !ready(selected) || busy !== null} type="submit" variant="primary">{busy === 'start' ? 'Starting…' : 'Start import'}</Button>
          </fieldset>
        </form>
        {actionError ? <p className="auth-error" role="alert">{actionError}</p> : null}
      </section>

      {run ? (
        <section aria-busy={!done} aria-labelledby="admin-import-run-title" className="admin-panel admin-import-run">
          <h2 id="admin-import-run-title">{label(run.root_id)} <StatusText tone={RUN_TONES[run.state] ?? 'muted'}>{RUN_STATES[run.state] ?? run.state}</StatusText></h2>
          <p aria-live="polite" className="admin-root-state">{statusText(run)}</p>
          <dl className="admin-root-facts g-import-counters">
            {COUNTERS.map(([key, name]) => <div key={key}><dt>{name}</dt><dd className="g-tabular">{run.counters[key] ?? 0}</dd></div>)}
            <div><dt>Visible to</dt><dd>{VISIBILITY[run.visibility]?.label ?? run.visibility}</dd></div>
            <div><dt>Started</dt><dd>{when(run.created_at)}</dd></div>
          </dl>
          <div className="admin-member-actions">
            {run.state === 'running' ? <Button disabled={busy !== null} onClick={() => { void act('cancel', () => cancelImport(run.id)); }}>Cancel import</Button> : null}
            {RESUMABLE.has(run.state) ? <Button disabled={busy !== null} onClick={() => { void act('resume', () => resumeImport(run.id)); }} variant="primary">Resume</Button> : null}
            {run.state === 'needs_confirmation' ? (
              <>
                <Button busy={busy === 'confirm'} disabled={busy !== null} onClick={() => setConfirmMissing(run)} variant="danger">{busy === 'confirm' ? 'Confirming…' : 'Confirm missing files'}</Button>
                <Button disabled={busy !== null} onClick={() => { void act('rescan', () => startImport({ root_id: run.root_id, visibility: run.visibility })); }}>Rescan</Button>
              </>
            ) : null}
            {run.state === 'succeeded' || run.state === 'partial' ? <Button disabled={busy !== null} onClick={() => { void act('rescan', () => startImport({ root_id: run.root_id, visibility: run.visibility })); }}>Rescan</Button> : null}
            {done && imported ? <ButtonLink variant="quiet" {...appLink('/library')}>View in Library</ButtonLink> : null}
          </div>
          {run.state === 'succeeded' || run.state === 'partial' ? <p className="admin-note">Rescan checks the folder again for new, changed, moved and missing files. Unchanged files are not re-read.</p> : null}
          {done ? (
            <>
              <div className="admin-toolbar admin-import-entries-head">
                <h3>Files that need attention</h3>
                <label className="g-field settings-select"><span className="g-field-label">Show</span>
                  <Select onChange={(event) => setFilter(event.target.value as ImportOutcome | '')} value={filter}>
                    <option value="">All</option>
                    {(Object.keys(OUTCOMES) as ImportOutcome[]).map((value) => <option key={value} value={value}>{OUTCOMES[value]}</option>)}
                  </Select>
                </label>
              </div>
              {entries === null ? <p aria-busy="true" className="admin-note">Loading files…</p> : entries.length ? (
                <div aria-label="Files that need attention" className="admin-table-scroll" role="region" tabIndex={0}>
                  <table className="admin-table g-table">
                    <thead><tr><th scope="col">File</th><th scope="col">Result</th><th scope="col">Why</th></tr></thead>
                    <tbody>
                      {entries.map((entry, index) => (
                        <tr key={`${entry.relative_path}-${index}`}>
                          <th scope="row">{entry.library_item_id ? <a {...appLink(`/watch/library/${encodeURIComponent(entry.library_item_id)}`)}>{entry.relative_path}</a> : entry.relative_path || '(whole folder)'}</th>
                          <td><StatusText tone={OUTCOME_TONES[entry.outcome] ?? 'muted'}>{OUTCOMES[entry.outcome] ?? entry.outcome}</StatusText></td>
                          <td>{entry.error ? REASONS[entry.error] ?? entry.error : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {entries.length >= ENTRY_LIMIT ? <p className="admin-note">Showing the first {ENTRY_LIMIT}. Counts above are exact.</p> : null}
                </div>
              ) : <p className="admin-note">{filter ? `No ${OUTCOMES[filter].toLowerCase()} files.` : 'Nothing needs attention.'}</p>}
            </>
          ) : null}
        </section>
      ) : null}

      {runs.length > 1 ? (
        <section aria-labelledby="admin-import-history-title" className="admin-panel">
          <h2 id="admin-import-history-title">Recent imports</h2>
          <ul className="admin-counts">
            {runs.map((entry) => (
              <li key={entry.id}>
                <Button aria-current={entry.id === run?.id ? 'true' : undefined} onClick={() => { delay.current = 1000; setFilter(''); setRun(entry); }} variant="quiet">{label(entry.root_id)} · {when(entry.created_at)}</Button>
                <strong>{RUN_STATES[entry.state] ?? entry.state}</strong>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <ConfirmDialog
        body="They come back if a later scan finds them again."
        busy={busy === 'confirm'}
        confirmLabel="Mark as missing"
        danger
        onCancel={() => setConfirmMissing(null)}
        onConfirm={() => { const asked = confirmMissing; setConfirmMissing(null); if (asked) void act('confirm', () => confirmImport(asked.id)); }}
        open={confirmMissing !== null}
        title={`Mark ${confirmMissing?.counters.missing_candidates ?? 0} files as missing?`}
      />
    </div>
  );
}
