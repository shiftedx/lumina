import { useEffect } from 'react';
import { RefreshCw } from 'lucide-react';

import { getAdminOverview } from '../../api';
import { formatBytes, formatDateTime as time, parseServerTime } from '../../utils';
import { Button, StatusText, type StatusTone } from '../../ui';
import { STATES } from './AdminStorage';
import { useAdminResource } from './useAdminResource';

const ACTIVE = ['queued', 'running', 'postprocessing'];
const rootTone = (state: string | null): StatusTone => (state === 'available' ? 'ok' : state === 'low_space' ? 'attention' : state ? 'danger' : 'muted');
const sum = (values: Record<string, number>, keys?: string[]) => Object.entries(values).reduce((total, [key, count]) => total + (!keys || keys.includes(key) ? count : 0), 0);

export function AdminOverview({ refreshKey = 0 }: { refreshKey?: number }) {
  const { data: overview, error, loading, reload: load } = useAdminResource(getAdminOverview, 'Unable to load the overview.');
  // A saved download-limit change (the Overview Save form) refreshes these numbers.
  useEffect(() => { if (refreshKey) load(); }, [refreshKey, load]);

  const managedBytes = overview?.roots.filter((root) => root.mode === 'managed').reduce((total, root) => total + root.artifact_bytes, 0) ?? 0;
  const db = overview ? Object.values(overview.persistence) : [];

  return (
    <div aria-busy={loading} className="admin-overview">
      <div className="admin-toolbar">
        <p role="status">{overview ? `Updated ${parseServerTime(overview.generated_at).toLocaleTimeString()} · all members, sampled on request` : loading ? 'Loading overview…' : 'Overview unavailable'}</p>
        <Button disabled={loading} icon={<RefreshCw />} onClick={load}>Refresh</Button>
      </div>
      {error ? <StatusText tone="danger"><span role="alert">{error}{overview ? ' Showing the previous sample.' : ''}</span></StatusText> : null}

      {overview ? (
        <>
          <dl className="g-stat-strip">
            <div className="g-stat"><dt className="g-label">Library items</dt><dd className="g-stat-figure">{sum(overview.library_items_by_status)}</dd><dd className="g-stat-note">{overview.library_items_by_status.missing ? `${overview.library_items_by_status.missing} missing on disk` : 'All present'}</dd></div>
            <div className="g-stat"><dt className="g-label">Managed media</dt><dd className="g-stat-figure">{formatBytes(managedBytes)}</dd><dd className="g-stat-note">Unique files in managed roots</dd></div>
            <div className="g-stat"><dt className="g-label">Active downloads</dt><dd className="g-stat-figure">{sum(overview.jobs_by_status, ACTIVE)}</dd><dd className="g-stat-note">{overview.concurrency} at a time · {overview.max_active_jobs_per_user} per member</dd></div>
            <div className="g-stat"><dt className="g-label">Live event streams</dt><dd className="g-stat-figure">{overview.event_streams}</dd><dd className="g-stat-note">Open browser connections</dd></div>
          </dl>

          <section aria-labelledby="admin-roots-title" className="g-panel">
            <h3 className="g-panel-title" id="admin-roots-title">Storage roots</h3>
            <p className="g-setting-note">Library volume free space: <strong>{overview.library_free_bytes === null ? 'unavailable' : formatBytes(overview.library_free_bytes)}</strong> · downloads pause below {overview.min_free_disk_mb} MB.</p>
            {overview.roots.length ? (
              <div className="g-table-scroll" role="region" aria-label="Storage roots table" tabIndex={0}>
                <table className="g-table">
                  <thead><tr><th scope="col">Root</th><th scope="col">State</th><th scope="col">Free</th><th scope="col">Stored</th><th scope="col">Last checked</th></tr></thead>
                  <tbody>{overview.roots.map((root) => (
                    <tr key={root.id}>
                      <th scope="row">{root.label}<small>{root.mode === 'managed' ? 'Managed' : 'External, read-only'}{root.enabled ? '' : ' · disabled'}</small></th>
                      <td><StatusText tone={rootTone(root.state)}>{root.state ? STATES[root.state]?.label ?? root.state : 'Not probed'}</StatusText></td>
                      <td>{root.free_bytes === null ? 'Unavailable' : `${formatBytes(root.free_bytes)} of ${formatBytes(root.total_bytes)}`}</td>
                      <td>{formatBytes(root.artifact_bytes)}<small>{root.artifact_count} files</small></td>
                      <td>{time(root.checked_at)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : <p className="g-setting-note">No storage roots are registered yet.</p>}
          </section>

          <div className="admin-columns">
            <section aria-labelledby="admin-jobs-title" className="g-panel">
              <h3 className="g-panel-title" id="admin-jobs-title">Downloads</h3>
              {Object.keys(overview.jobs_by_status).length ? (
                <table className="g-table"><tbody>{Object.entries(overview.jobs_by_status).map(([status, count]) => <tr key={status}><th scope="row">{status[0].toUpperCase() + status.slice(1)}</th><td className="g-tabular">{count}</td></tr>)}</tbody></table>
              ) : <p className="g-setting-note">No downloads yet.</p>}
              <h4 className="g-label">Recent failures</h4>
              {overview.recent_failures.length ? (
                <ul className="admin-failures">{overview.recent_failures.map((failure) => <li key={failure.reason}><p>{failure.reason}</p><small>{failure.count}× · last {time(failure.last_at)}</small></li>)}</ul>
              ) : <p className="g-setting-note">No failed downloads in the last {overview.failure_window_days} days.</p>}
            </section>
            <section aria-labelledby="admin-db-title" className="g-panel">
              <h3 className="g-panel-title" id="admin-db-title">Database writes</h3>
              <p className="g-setting-note">Since the server started.</p>
              <table className="g-table"><tbody>
                <tr><th scope="row">Transactions</th><td className="g-tabular">{db.reduce((total, metric) => total + metric.count, 0)}</td></tr>
                <tr><th scope="row">Errors</th><td className="g-tabular">{db.reduce((total, metric) => total + metric.errors, 0)}</td></tr>
                <tr><th scope="row">Busy timeouts</th><td className="g-tabular">{db.reduce((total, metric) => total + metric.busy_timeouts, 0)}</td></tr>
                <tr><th scope="row">Longest lock wait</th><td className="g-tabular">{db.length ? `${Math.round(Math.max(...db.map((metric) => metric.max_wait_ms)))} ms` : '—'}</td></tr>
              </tbody></table>
            </section>
          </div>
        </>
      ) : null}
    </div>
  );
}
