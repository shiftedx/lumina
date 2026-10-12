import { useState } from 'react';
import { ClipboardCopy, RefreshCw } from 'lucide-react';

import { getAdminDiagnostics } from '../../api';
import type { RecoDiagnostics, RecoSurface } from '../../types';
import { formatDateTime as time, parseServerTime } from '../../utils';
import { Button, StatusText } from '../../ui';
import { LibraryAutomationPanel } from './LibraryAutomationPanel';
import { MediaLoadingPanel } from './MediaLoadingPanel';
import { useAdminResource } from './useAdminResource';

const VERSION_LABELS: Record<string, string> = { lumina: 'Lumina', python: 'Python', yt_dlp: 'yt-dlp', ffmpeg: 'FFmpeg', node: 'Node.js' };
const RUNTIME_LABELS: Record<string, string> = { ffmpeg_available: 'FFmpeg', js_runtime_available: 'JavaScript runtime', yt_dlp_ejs_available: 'yt-dlp EJS' };
const SOURCES: Record<string, string> = { download: 'Download', import: 'Import', summary: 'Summary', playback: 'Playback' };

const SURFACE_LABELS: Record<RecoSurface, string> = {
  home_picked: 'Home · Picked for you', home_recommended: 'Home · Recommended titles', home_because: 'Home · Because you watched',
  explore_for_you: 'Explore · For you', explore_popular: 'Explore · Popular', up_next: 'Up next', title_similar: 'More like this',
};
export const PROVIDER_CALL_BUDGET = 300; // mirrors the server's daily provider-call ceiling; the payload carries no budget field yet
const percent = (value: number | null | undefined, digits = 1) => (value == null ? '—' : `${(value * 100).toFixed(digits)} %`);
const whole = (value: number) => value.toLocaleString('en-US');
function refreshAge(minutes: number | null | undefined) {
  if (minutes == null) return 'No refresh yet';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.floor(hours / 24)} days ago`;
}

/** Diagnostics "Recommendations": household totals over the window; a dash is a rate with fewer than 50 behind it. */
export function RecommendationsPanel({ reco }: { reco: RecoDiagnostics | null | undefined }) {
  return (
    <section aria-labelledby="admin-diag-reco-title" className="g-panel">
      <h3 className="g-panel-title" id="admin-diag-reco-title">Recommendations</h3>
      <p className="g-setting-note">
        Household totals for the last {reco?.window_days ?? 28} days. Nothing here names a person or a title, and a dash means fewer than 50 are behind that figure yet.
      </p>
      {reco && !reco.enabled ? <p className="g-setting-note" role="status">Personalised recommendations are switched off. Lists use the earlier ranking.</p> : null}
      {reco ? (
        <>
          <dl className="g-stat-strip"><div className="g-stat"><dt className="g-label">Plays that started from a recommendation</dt><dd className="g-stat-figure">{percent(reco.reco_share_of_remote_plays)}</dd></div></dl>
          <p className="g-setting-note">Target 40 % by day 42. Other online targets, reviewed on day 14 and day 42: Picked for you click rate 5 % or more, median watched 50 % or more, finished 35 % or more, negative feedback 3 % or less.</p>
          <div aria-label="Recommendations by surface" className="g-table-scroll" role="region" tabIndex={0}>
            <table className="g-table"><caption className="sr-only">Recommendations by surface</caption>
              <thead><tr>
                <th scope="col">Surface</th><th scope="col">Impressions</th><th scope="col">Opens</th><th scope="col">Plays from a list</th>
                <th scope="col">Median watched</th><th scope="col">Finished</th><th scope="col">Negative feedback</th><th scope="col">Explore · exploit play rate</th>
              </tr></thead>
              <tbody>{reco.surfaces.filter((row) => row.surface !== 'explore_popular' || row.impressions || row.opens || row.plays).map((row) => (
                <tr key={row.surface}>
                  <th scope="row">{SURFACE_LABELS[row.surface]}</th>
                  <td>{whole(row.impressions)}</td>
                  <td>{whole(row.opens)}{row.ctr == null ? null : <small>{percent(row.ctr)} click rate</small>}</td>
                  <td>{whole(row.plays)}</td>
                  <td>{percent(row.play_through_median, 0)}</td>
                  <td>{percent(row.completion_rate)}</td>
                  <td>{percent(row.negative_rate)}</td>
                  <td>{percent(row.explore_play_rate)} · {percent(row.exploit_play_rate)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <h4 className="g-label">Pool health</h4>
          <table className="g-table"><tbody><tr><th scope="row">Members with a pool</th><td className="g-tabular">{reco.pool.members_with_pool}</td></tr>
            <tr><th scope="row">Median pool size</th><td className="g-tabular">{whole(reco.pool.median_pool_size)}</td></tr>
            <tr><th scope="row">Oldest refresh</th><td className="g-tabular">{refreshAge(reco.pool.oldest_refresh_age_minutes)}</td></tr>
            <tr><th scope="row">Provider calls, 24 h</th><td className="g-tabular">{reco.pool.provider_calls_24h} of {PROVIDER_CALL_BUDGET}</td></tr>
            <tr><th scope="row">Budget hits, 24 h</th><td className="g-tabular">{reco.pool.budget_hits_24h}</td></tr>
            <tr><th scope="row">Remote vectors ready</th><td className="g-tabular">{percent(reco.pool.remote_vector_coverage)}</td></tr>
            <tr><th scope="row">Events dropped, 24 h</th><td className="g-tabular">{reco.pool.dropped_events_24h}</td></tr></tbody></table>
        </>
      ) : <p className="g-setting-note">No recommendation figures yet. They appear once the server reports them.</p>}
    </section>
  );
}

export function AdminDiagnostics() {
  const { data: report, error, loading, reload: load } = useAdminResource(getAdminDiagnostics, 'Unable to load diagnostics.');
  const [copied, setCopied] = useState<string | null>(null);

  async function copy() {
    if (!report) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(report, null, 2));
      setCopied('Report copied. It contains no paths, tokens or keys.');
    } catch {
      setCopied('Copying is blocked in this browser. Select the report below instead.');
    }
  }

  const jobs = report?.queue.jobs_by_status ?? {};
  const sweeps = report?.maintenance_sweeps;
  const automation = report?.library_automation;
  return (
    <div aria-busy={loading} className="admin-overview">
      <div className="admin-toolbar">
        <p role="status">{report ? `Sampled ${parseServerTime(report.generated_at).toLocaleTimeString()} · redacted for sharing` : loading ? 'Loading diagnostics…' : 'Diagnostics unavailable'}</p>
        <div className="admin-member-actions">
          <Button disabled={loading} icon={<RefreshCw />} onClick={load}>Refresh</Button>
          <Button disabled={!report} icon={<ClipboardCopy />} onClick={() => { void copy(); }} variant="primary">Copy report</Button>
        </div>
      </div>
      {copied ? <p aria-live="polite" className="g-setting-note">{copied}</p> : null}
      {error ? <StatusText tone="danger"><span role="alert">{error}{report ? ' Showing the previous sample.' : ''}</span></StatusText> : null}

      {report && sweeps ? (
        <>
          <dl className="g-stat-strip">
            <div className="g-stat"><dt className="g-label">Server</dt><dd className="g-stat-figure"><StatusText tone={report.status === 'ok' ? 'ok' : 'danger'}>{report.status === 'ok' ? 'Healthy' : 'Degraded'}</StatusText></dd><dd className="g-stat-note">{report.status === 'ok' ? 'Maintenance and tools are working' : 'See maintenance and runtime below'}</dd></div>
            <div className="g-stat"><dt className="g-label">Active downloads</dt><dd className="g-stat-figure">{(jobs.running ?? 0) + (jobs.postprocessing ?? 0)}</dd><dd className="g-stat-note">{jobs.queued ?? 0} queued · {report.queue.concurrency} at a time</dd></div>
            <div className="g-stat"><dt className="g-label">Maintenance failures</dt><dd className="g-stat-figure">{sweeps.consecutive_failures}</dd><dd className="g-stat-note">Last success {time(sweeps.last_success_at)}</dd></div>
            <div className="g-stat"><dt className="g-label">Live event streams</dt><dd className="g-stat-figure">{report.queue.event_streams}</dd><dd className="g-stat-note">Open browser connections</dd></div>
            <div className="g-stat"><dt className="g-label">Library automation</dt><dd className="g-stat-figure">{automation ? <StatusText tone={automation.poller.stalled ? 'danger' : 'ok'}>{automation.poller.stalled ? 'Stalled' : 'Healthy'}</StatusText> : <StatusText tone="muted">Not reported</StatusText>}</dd>{automation ? <dd className="g-stat-note">{automation.runs_24h.manual + automation.runs_24h.scheduled + automation.runs_24h.watch} scans in 24 h</dd> : null}</div>
          </dl>
          {sweeps.last_error ? <StatusText tone="danger"><span role="alert">Maintenance is failing: {sweeps.last_error}</span></StatusText> : null}

          <div className="admin-columns admin-columns-top">
            <section aria-labelledby="admin-diag-errors-title" className="g-panel">
              <h3 className="g-panel-title" id="admin-diag-errors-title">Recent errors</h3>
              <p className="g-setting-note">Newest failures from downloads, imports, summaries and playback, with paths and secrets removed.</p>
              {report.recent_errors.length ? (
                <ul className="admin-failures">{report.recent_errors.map((entry, index) => <li key={`${entry.source}-${index}`}><p>{entry.message}</p><small>{SOURCES[entry.source] ?? entry.source} · {time(entry.at)}</small></li>)}</ul>
              ) : <p className="g-setting-note">No recent errors.</p>}
            </section>
            <section aria-labelledby="admin-diag-runtime-title" className="g-panel">
              <h3 className="g-panel-title" id="admin-diag-runtime-title">Versions</h3>
              <table className="g-table"><tbody>{Object.entries(report.versions).map(([key, value]) => <tr key={key}><th scope="row">{VERSION_LABELS[key] ?? key}</th><td className="g-tabular">{value ?? 'Not found'}</td></tr>)}</tbody></table>
              <h4 className="g-label">Runtime</h4>
              <table className="g-table"><tbody>{Object.entries(report.runtime).map(([key, value]) => <tr key={key}><th scope="row">{RUNTIME_LABELS[key] ?? key}</th><td><StatusText tone={value ? 'ok' : 'danger'}>{value ? 'Available' : 'Missing'}</StatusText></td></tr>)}</tbody></table>
              <h4 className="g-label">Storage roots</h4>
              {report.storage_roots.length ? (
                <table className="g-table"><tbody>{report.storage_roots.map((root) => <tr key={root.label}><th scope="row">{root.label}{root.enabled ? '' : ' (disabled)'}</th><td>{root.state ?? 'Not probed'}</td></tr>)}</tbody></table>
              ) : <p className="g-setting-note">No storage roots registered.</p>}
              <h4 className="g-label">Local AI</h4>
              <table className="g-table"><tbody><tr><th scope="row">Summaries endpoint</th><td className="g-tabular">{!report.ai.enabled ? 'Off' : report.ai.ok ? 'Reachable' : report.ai.error ?? 'Unavailable'}</td></tr>
                <tr><th scope="row">Speech-to-text</th><td className="g-tabular">{report.ai.asr_configured ? 'Configured' : 'Off'}</td></tr></tbody></table>
            </section>
          </div>

          <MediaLoadingPanel artwork={report.artwork} loading={report.media_loading} />
          <RecommendationsPanel reco={report.recommendations} />
          <LibraryAutomationPanel data={automation} />

          <details className="g-panel admin-panel">
            <summary>Report preview</summary>
            <pre className="g-log">{JSON.stringify(report, null, 2)}</pre>
          </details>
        </>
      ) : null}
    </div>
  );
}
