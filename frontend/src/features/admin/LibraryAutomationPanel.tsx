import type { AutomationState, LibraryAutomationDiagnostics, ScanSchedule } from '../../types';
import { formatDateTime as time } from '../../utils';
import { StatusText } from '../../ui';

const SCHEDULES: Record<ScanSchedule, string> = { off: 'Off', '15m': 'Every 15 minutes', '1h': 'Hourly', '6h': 'Every 6 hours', nightly: 'Nightly' };
const STATES: Record<AutomationState, string> = {
  idle: 'Idle', scanning: 'Scanning', preparing: 'Preparing', waiting_files: 'Waiting for files', waiting_confirmation: 'Needs confirmation',
  offline: 'Offline', unresponsive: 'Not answering', too_large: 'Too large to watch', needs_first_import: 'Not imported yet',
};
const when = (value: string | null) => (value ? time(value) : '—');

/** Diagnostics "Library automation": counts and times only; the server redacts, this renders as given. */
export function LibraryAutomationPanel({ data }: { data: LibraryAutomationDiagnostics | null | undefined }) {
  return (
    <section aria-labelledby="admin-diag-automation-title" className="g-panel">
      <h3 className="g-panel-title" id="admin-diag-automation-title">Library automation</h3>
      <p className="g-setting-note">Scheduled scans and folder watching. Counts are for the last 24 hours and name no files.</p>
      {data ? (
        <>
          {data.poller.last_error ? <StatusText tone="danger"><span role="alert">Folder watching error: {data.poller.last_error}</span></StatusText> : null}
          <p className="g-setting-note">Manual {data.runs_24h.manual} · Scheduled {data.runs_24h.scheduled} · Watched {data.runs_24h.watch} · Needs confirmation {data.runs_24h.needs_confirmation} · Failed {data.runs_24h.failed}</p>
          <p className="g-setting-note">Running {data.driver.active_run_id ? 1 : 0} · queued {data.driver.queued}</p>
          <div aria-label="Library automation by folder" className="g-table-scroll" role="region" tabIndex={0}>
            <table className="g-table"><caption className="sr-only">Library automation by folder</caption>
              <thead><tr>
                <th scope="col">Folder</th><th scope="col">Schedule</th><th scope="col">Watch</th><th scope="col">State</th><th scope="col">Folders watched</th>
                <th scope="col">Waiting files</th><th scope="col">Last pass</th><th scope="col">Last change</th><th scope="col">Next scan</th>
              </tr></thead>
              <tbody>{data.roots.map((root, index) => (
                <tr key={`${root.label}:${index}`}>
                  <th scope="row">{root.label}</th>
                  <td>{SCHEDULES[root.schedule] ?? root.schedule}</td>
                  <td>{root.watch ? 'On' : 'Off'}</td>
                  <td>{STATES[root.state] ?? root.state}{root.last_skip ? <small> · Skipped: {(STATES[root.last_skip.reason] ?? root.last_skip.reason).toLowerCase()}</small> : null}</td>
                  <td className="g-tabular">{root.dirs_watched}</td>
                  <td className="g-tabular">{root.pending_files}</td>
                  <td>{root.last_pass_seconds == null ? '' : `${root.last_pass_seconds.toFixed(1)} s · `}{when(root.last_pass_at)}{root.watch_errors_last_pass > 0 ? <small>{root.watch_errors_last_pass} errors</small> : null}</td>
                  <td>{when(root.last_change_at)}</td>
                  <td>{when(root.next_scan_at)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </>
      ) : <p className="g-setting-note">No library automation figures yet. They appear once the server reports them.</p>}
    </section>
  );
}
