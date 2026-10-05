/** Diagnostics "Media loading": client percentiles against budgets, image failure rate, artwork preparation. */
import type { ArtworkProgress, MediaLoading, MetricSummary } from '../../types';
import { StatusText, type StatusTone } from '../../ui';
import { formatBytes } from '../../utils';

const METRICS: Record<MetricSummary['metric'], string> = {
  wall_first_screen_ms: 'Wall first screen', wall_sharp_ms: 'Wall posters sharp', detail_hero_ms: 'Title page hero', home_first_screen_ms: 'Home first screen', home_hero_ms: 'Home hero',
  image_load_ms: 'Image load', image_failed: 'Image failures', ttff_ms: 'Time to first frame', long_tasks: 'Long tasks while scrolling',
};
const LABELS: Record<string, string> = {
  movies: 'Movies', shows: 'Shows', home: 'Home', click: 'from the wall', deep_link: 'opened by link', direct: 'direct play', direct_resume: 'direct resume',
  transcode_hw: 'hardware conversion', transcode_sw: 'software conversion', remux: 'remux or audio conversion', wall_scroll: 'wall scroll',
  poster: 'poster', backdrop: 'backdrop', still: 'episode still', logo: 'logo', hit: 'cached', net: 'network', '404': 'missing', transient: 'retried', exhausted: 'gave up',
};
const REASONS: Record<string, string> = {
  timeout: 'Timed out', ffmpeg_failed: 'Could not be converted', oversize_output: 'Result too large', unreadable: 'Unreadable file', unsupported_format: 'Unsupported format',
};

const labelText = (label: string) => label.split(':').map((part) => LABELS[part] ?? part).join(' · ');
const amount = (metric: MetricSummary['metric'], value: number | null | undefined) => {
  if (value == null) return '—';
  if (!metric.endsWith('_ms')) return String(value);
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
};
const pair = (metric: MetricSummary['metric'], p50: number | null | undefined, p95: number | null | undefined) => `${amount(metric, p50)} / ${amount(metric, p95)}`;
const rate = (value: number | null | undefined) => (value == null ? 'No images yet' : `${(value * 100).toFixed(value > 0 && value < 0.01 ? 2 : 1)} %`);

function verdict(summary: MetricSummary): { text: string; tone: StatusTone } {
  if (summary.within_budget === true) return { text: 'Within budget', tone: 'ok' };
  if (summary.within_budget === false) return { text: 'Over budget', tone: 'danger' };
  return { text: summary.budget_p50 == null ? 'No budget' : 'No samples this week', tone: 'muted' };
}

export function MediaLoadingPanel({ loading, artwork }: { loading: MediaLoading | null | undefined; artwork: ArtworkProgress | null | undefined }) {
  const serving = artwork?.serving;
  return (
    <section aria-labelledby="admin-diag-media-title" className="g-panel">
      <h3 className="g-panel-title" id="admin-diag-media-title">Media loading</h3>
      <p className="g-setting-note">How fast pictures and videos load in this household's browsers, today and over the last 7 days. Nothing here names a person or a title.</p>
      {loading?.metrics.length ? (
        <div aria-label="Media loading table" className="g-table-scroll" role="region" tabIndex={0}>
          <table className="g-table">
            <thead><tr><th scope="col">Measure</th><th scope="col">Today p50 / p95</th><th scope="col">7 days p50 / p95</th><th scope="col">Budget p50 / p95</th><th scope="col">Status</th></tr></thead>
            <tbody>{loading.metrics.map((summary) => {
              const status = verdict(summary);
              return (
                <tr key={`${summary.metric}:${summary.label}`}>
                  <th scope="row">{METRICS[summary.metric]}<small>{labelText(summary.label)}</small></th>
                  <td>{pair(summary.metric, summary.today_p50, summary.today_p95)}<small>{summary.today_count} samples</small></td>
                  <td>{pair(summary.metric, summary.week_p50, summary.week_p95)}<small>{summary.week_count} samples</small></td>
                  <td>{summary.budget_p50 == null ? '—' : pair(summary.metric, summary.budget_p50, summary.budget_p95)}</td>
                  <td><StatusText tone={status.tone}>{status.text}</StatusText></td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      ) : <p className="g-setting-note">No loading measurements yet. They appear after someone browses the library or plays something.</p>}
      <table className="g-table"><tbody><tr><th scope="row">Image failure rate today</th><td className="g-tabular">{rate(loading?.image_failure_rate_today)}</td></tr>
        <tr><th scope="row">Image failure rate, 7 days</th><td className="g-tabular">{rate(loading?.image_failure_rate_week)}</td></tr></tbody></table>
      <h4 className="g-label">Artwork preparation</h4>
      {artwork && serving ? (
        <>
          <table className="g-table"><tbody><tr><th scope="row">Artwork prepared</th><td className="g-tabular">{artwork.prepared.toLocaleString('en-US')} / {artwork.total.toLocaleString('en-US')}</td></tr>
            <tr><th scope="row">Failed · unsupported</th><td className="g-tabular">{artwork.failed.toLocaleString('en-US')} · {artwork.unsupported.toLocaleString('en-US')}</td></tr>
            <tr><th scope="row">Cache on disk</th><td className="g-tabular">{formatBytes(artwork.cache_bytes)}</td></tr>
            <tr><th scope="row">Background pass</th><td className="g-tabular">{artwork.paused_for_playback ? 'Paused while a video is being converted' : artwork.running ? 'Running' : 'Stopped'}</td></tr>
            <tr><th scope="row">Served from memory · disk</th><td className="g-tabular">{serving.hits_memory} · {serving.hits_disk}</td></tr>
            <tr><th scope="row">Misses · prepared on request</th><td className="g-tabular">{serving.misses} · {serving.generated}</td></tr>
            <tr><th scope="row">Sent the original · asked to retry</th><td className="g-tabular">{serving.fallback_original} · {serving.fallback_unavailable}</td></tr>
            <tr><th scope="row">Preparation failures</th><td className="g-tabular">{serving.failures}</td></tr></tbody></table>
          {Object.keys(artwork.failure_reasons).length ? (
            <table className="g-table"><tbody>{Object.entries(artwork.failure_reasons).sort(([, a], [, b]) => b - a).map(([reason, count]) => <tr key={reason}><th scope="row">{REASONS[reason] ?? reason}</th><td className="g-tabular">{count}</td></tr>)}</tbody></table>
          ) : null}
        </>
      ) : <p className="g-setting-note">Artwork preparation is not running.</p>}
    </section>
  );
}
