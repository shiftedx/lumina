import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { type AdminTask, type AdminTaskPage, type TaskFilter, type TaskKind, cancelAdminTask, listAdminTasks, retryAdminTask } from '../../api';
import { errorMessage as errorText, formatDateTime as when } from '../../utils';
import { Button, Field, fieldProps, Select, StatusText, type StatusTone } from '../../ui';
import { appLink } from './AdminImports';
import '../settings/settings.css';

const KINDS: Array<{ id: TaskKind; label: string }> = [
  { id: 'download', label: 'Downloads' },
  { id: 'asr', label: 'Transcriptions' },
  { id: 'summary', label: 'Summaries' },
  { id: 'import', label: 'Imports' },
];
const FILTERS: Array<{ id: TaskFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'active', label: 'In progress' },
  { id: 'failed', label: 'Failed or stopped' },
  { id: 'finished', label: 'Finished' },
];
const STATUS: Record<string, string> = {
  queued: 'Queued', running: 'Running', postprocessing: 'Processing', completed: 'Completed', succeeded: 'Done',
  failed: 'Failed', cancelled: 'Cancelled', canceled: 'Cancelled', interrupted: 'Interrupted by restart',
  needs_confirmation: 'Needs confirmation', partial: 'Finished with skipped files', cancel_requested: 'Cancelling',
};
const TONE: Record<string, StatusTone> = { completed: 'ok', succeeded: 'ok', failed: 'danger', interrupted: 'danger', cancelled: 'muted', canceled: 'muted', needs_confirmation: 'attention', partial: 'attention', cancel_requested: 'muted' };
const IMPORT_ERRORS: Record<string, string> = {
  offline: 'The folder was unavailable.', root_empty: 'The folder looked empty, so nothing was changed.', root_missing: 'The folder is gone.', interrupted: 'Stopped by a server restart.',
  permission_denied: 'Lumina was not allowed to read the folder.', identity_mismatch: 'The folder is not the one that was imported.', root_removed: 'The folder was removed from Lumina.',
};
const EMPTY: Partial<Record<TaskKind, string>> = { import: 'No library scans yet.' };
const PRIVATE: Record<TaskKind, string> = { download: 'Private download', asr: 'Private item', summary: 'Private item', import: 'Library scan' };

const total = (counts: Record<string, number> | undefined) => Object.values(counts ?? {}).reduce((sum, count) => sum + count, 0);

/** Every kind of background work in one place; commands go to the same managers members use. */
export function AdminTasks() {
  const [kind, setKind] = useState<TaskKind>('download');
  const [filter, setFilter] = useState<TaskFilter>('all');
  const [page, setPage] = useState<AdminTaskPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const request = useRef(0);

  const load = useCallback((cursor?: string | null) => {
    const token = ++request.current;
    setLoading(true);
    void listAdminTasks(kind, filter, cursor)
      .then((next) => {
        if (token !== request.current) return;
        setPage((current) => (cursor && current ? { ...next, items: [...current.items, ...next.items] } : next));
        setError(null);
      })
      .catch((loadError) => { if (token === request.current) setError(errorText(loadError, 'Unable to load tasks.')); })
      .finally(() => { if (token === request.current) setLoading(false); });
  }, [kind, filter]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => () => { request.current += 1; }, []);

  async function act(task: AdminTask, action: 'cancel' | 'retry') {
    setBusy(task.id);
    setError(null);
    try {
      await (action === 'cancel' ? cancelAdminTask(task.kind, task.id) : retryAdminTask(task.kind as 'download' | 'import', task.id));
      load();
    } catch (actionError) {
      setError(errorText(actionError, `Unable to ${action} this task.`));
    } finally {
      setBusy(null);
    }
  }

  function onTabsKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const at = KINDS.findIndex((entry) => entry.id === kind);
    const to = event.key === 'ArrowRight' ? (at + 1) % KINDS.length : event.key === 'ArrowLeft' ? (at + KINDS.length - 1) % KINDS.length : event.key === 'Home' ? 0 : event.key === 'End' ? KINDS.length - 1 : -1;
    if (to < 0) return;
    event.preventDefault();
    setKind(KINDS[to].id);
    setPage(null);
    document.getElementById(`tasks-tab-${KINDS[to].id}`)?.focus();
  }

  const counts = page?.counts;
  return (
    <div aria-busy={loading} className="admin-overview admin-tasks">
      <div aria-label="Task kinds" className="g-tabs" onKeyDown={onTabsKeyDown} role="tablist">
        {KINDS.map((entry) => (
          <button aria-controls={`tasks-panel-${entry.id}`} aria-selected={entry.id === kind} className="g-tab" id={`tasks-tab-${entry.id}`} key={entry.id} onClick={() => { setKind(entry.id); setPage(null); }} role="tab" tabIndex={entry.id === kind ? 0 : -1} type="button">
            {entry.label}{counts ? <span className="g-tabular">{total(counts[entry.id])}</span> : null}
          </button>
        ))}
      </div>
      <div aria-labelledby={`tasks-tab-${kind}`} className="admin-overview" id={`tasks-panel-${kind}`} role="tabpanel">
      <div className="admin-toolbar">
        <Field label="Show">
          {(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => { setFilter(event.target.value as TaskFilter); setPage(null); }} value={filter}>
              {FILTERS.map((entry) => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
            </Select>
          )}
        </Field>
        <p role="status">{counts ? summaryLine(kind, counts[kind]) : loading ? 'Loading tasks…' : ''}</p>
        <Button disabled={loading} icon={<RefreshCw />} onClick={() => load()}>Refresh</Button>
      </div>
      {error ? <StatusText tone="danger"><span role="alert">{error}</span></StatusText> : null}

      {page ? (
        page.items.length ? (
          <ul aria-label={`${KINDS.find((entry) => entry.id === kind)?.label} tasks`} className="g-task-list">
            {page.items.map((task) => (
              <li className="g-list-row g-task" key={task.id}>
                <div className="g-task-head">
                  <strong className={task.title ? undefined : 'g-task-private'}>{task.title ?? PRIVATE[task.kind]}</strong>
                  <StatusText tone={TONE[task.status] ?? 'attention'}>{STATUS[task.status] ?? task.status}</StatusText>
                </div>
                <p className="g-setting-note">
                  {[task.owner ? `Requested by ${task.owner}` : 'Requester removed', task.detail, when(task.created_at), task.attempts ? `${task.attempts} earlier attempt${task.attempts === 1 ? '' : 's'}` : null].filter(Boolean).join(' · ')}
                </p>
                {task.error ? <p className="g-task-error">{task.kind === 'import' ? IMPORT_ERRORS[task.error] ?? task.error : task.error}</p> : null}
                {task.can_cancel || task.can_retry ? (
                  <div className="admin-member-actions">
                    {task.can_cancel ? <Button disabled={busy === task.id} onClick={() => void act(task, 'cancel')}>Cancel</Button> : null}
                    {task.can_retry ? <Button disabled={busy === task.id} onClick={() => void act(task, 'retry')}>Retry</Button> : null}
                  </div>
                ) : task.kind !== 'download' && task.kind !== 'import' && task.status === 'interrupted' ? <p className="g-setting-note">Stopped by a server restart. The member can start it again from the watch page.</p> : null}
              </li>
            ))}
          </ul>
        ) : <p className="g-setting-note">{filter === 'all' ? EMPTY[kind] ?? `No ${KINDS.find((entry) => entry.id === kind)?.label.toLowerCase()} yet.` : 'Nothing matches this filter.'}</p>
      ) : null}
      {page?.next_cursor ? <Button className="g-task-more" disabled={loading} onClick={() => load(page.next_cursor)}>{loading ? 'Loading…' : 'Load more'}</Button> : null}
      </div>
      <p className="g-setting-note">Library imports run per storage root; see <a {...appLink('/settings/library')}>Library &amp; storage</a> for scan progress and skipped files.</p>
    </div>
  );
}

function summaryLine(kind: TaskKind, counts: Record<string, number> = {}) {
  const active = kind === 'download' ? (counts.queued ?? 0) + (counts.running ?? 0) + (counts.postprocessing ?? 0)
    : kind === 'import' ? (counts.running ?? 0) + (counts.cancel_requested ?? 0) + (counts.needs_confirmation ?? 0) : counts.active ?? 0;
  const failed = (counts.failed ?? 0) + (counts.interrupted ?? 0) + (kind === 'import' ? counts.cancelled ?? 0 : 0);
  return `${active} in progress · ${failed} failed or interrupted · ${total(counts)} total`;
}
