import { memo, type ReactNode, useEffect, useRef, useState } from 'react';
import { Download, Library, Radio, RotateCcw, X } from 'lucide-react';
import { listLiveRecordings } from '../../api';
import { openPalette } from '../../app/commands';
import { usePolledSnapshot } from '../../app/usePolledSnapshot';
import { Artwork } from '../../Artwork';
import { isLiveRecordingTerminal, summarizeLiveRecording } from '../../liveRecording';
import { jobProgress, readString } from '../../luminaModel';
import type { DownloadJob, JobStatus, LiveRecording } from '../../types';
import { formatBytes, formatEta, formatSpeed, formatTime } from '../../utils';
import { type CollectionLoadProblem, type CollectionLoadState } from '../../workspace';
import { CollectionRecovery, StaleCollectionNotice } from '../media/MediaCards';
import { Button, EmptyState, ErrorState, IconButton, Masthead, ProgressBar, Skeleton, StatusText, type StatusTone } from '../../ui';
import './downloads.css';

const ACTIVE: JobStatus[] = ['queued', 'running', 'postprocessing'];
const ATTENTION: JobStatus[] = ['failed', 'interrupted'];
const RETRYABLE: JobStatus[] = ['failed', 'cancelled', 'interrupted'];
const STATUS_LABEL: Partial<Record<JobStatus, string>> = {
  queued: 'Queued',
  running: 'Downloading',
  postprocessing: 'Finishing up',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
};
const TONE: Partial<Record<JobStatus, StatusTone>> = { queued: 'muted', running: 'attention', postprocessing: 'attention', failed: 'danger', interrupted: 'danger', completed: 'ok', cancelled: 'muted' };

function destination(job: DownloadJob): string | null {
  const output = job.outputs?.[0];
  if (!output) return null;
  const place = [output.root_label, output.folder].filter(Boolean).join(' › ');
  return `Saved to ${place || 'your Library'}${job.outputs && job.outputs.length > 1 ? ` (+${job.outputs.length - 1} more)` : ''}`;
}

function detail(job: DownloadJob): string {
  if (ACTIVE.includes(job.status)) {
    return [job.downloaded_bytes ? formatBytes(job.downloaded_bytes) : null, job.speed ? formatSpeed(job.speed) : null, job.eta ? formatEta(job.eta) : null].filter(Boolean).join(' · ') || `Added ${formatTime(job.created_at)}`;
  }
  if (job.status === 'completed') return destination(job) || `Finished ${formatTime(job.finished_at || job.created_at)}`;
  if (job.status === 'interrupted') return job.error || 'Lumina stopped before this finished. Retry to pick it up again.';
  if (job.status === 'cancelled') return `Cancelled ${formatTime(job.finished_at || job.created_at)}`;
  return job.error || 'This download did not finish. Retry to try again.';
}

// Memoized so a single job's progress update does not re-render sibling rows.
// The row derives its own actions from job status, letting the surface pass one
// stable onCancel/onRetry/onOpenItem reference to every row. A refused retry
// (queue full, storage low/offline) is shown on the row it belongs to.
export const JobRow = memo(function JobRow({ job, onCancel, onRetry, onOpenItem }: { job: DownloadJob; onCancel?: (job: DownloadJob) => void; onRetry?: (job: DownloadJob) => Promise<void>; onOpenItem?: (libraryItemId: string) => void }) {
  const [retryError, setRetryError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const progress = jobProgress(job);
  const active = ACTIVE.includes(job.status);
  // Unknown progress shows its status word only — never a fake percentage.
  const progressKnown = active && (typeof job.progress === 'number' || Boolean(job.downloaded_bytes && job.total_bytes));
  const title = job.title || readString(job.preview_snapshot, 'title') || 'Untitled download';
  const openId = job.status === 'completed' ? job.outputs?.find((output) => output.library_item_id)?.library_item_id : null;
  const attempts = job.attempts || [];
  const retry = () => {
    setRetryError(null);
    setRetrying(true);
    onRetry?.(job).catch((error: unknown) => setRetryError(error instanceof Error ? error.message : 'Unable to retry download')).finally(() => setRetrying(false));
  };
  return (
    <li className="g-list-row g-job">
      <span className="g-job-thumb">{job.artwork_url ? <Artwork alt="" src={job.artwork_url} /> : <Download aria-hidden="true" />}</span>
      <div className="g-job-copy">
        <span className="g-list-row-title g-clamp-2" title={title}>{title}</span>
        <span className="g-list-row-meta">{detail(job)}</span>
        {progressKnown ? <ProgressBar label={`${Math.round(progress)} percent downloaded`} value={progress} /> : null}
        <StatusText tone={TONE[job.status] ?? 'muted'}>{STATUS_LABEL[job.status] ?? job.status}</StatusText>
        {job.status === 'completed' && job.format_selection?.preset === 'best_editable' && job.format_resolution?.fallback_reason ? <span className="g-list-row-meta">Not editable: source has no H.264</span> : null}
        {retryError ? <span className="g-list-row-meta is-error" role="alert">{retryError}</span> : null}
      </div>
      <div className="g-list-row-actions">
        {openId && onOpenItem ? <Button icon={<Library />} onClick={() => onOpenItem(openId)} variant="quiet">Open</Button> : null}
        {active && onCancel ? <IconButton icon={<X />} label={`Cancel ${title}`} onClick={() => onCancel(job)} title="Cancel download" /> : null}
        {RETRYABLE.includes(job.status) && onRetry ? <IconButton busy={retrying} icon={<RotateCcw />} label={`Retry ${title}`} onClick={retry} title="Retry download" /> : null}
      </div>
      {attempts.length ? <details className="g-job-attempts"><summary className="g-text-button">{attempts.length} earlier attempt{attempts.length === 1 ? '' : 's'}</summary><ol>{attempts.map((attempt, index) => <li key={index}>{STATUS_LABEL[attempt.status as JobStatus] || attempt.status}{attempt.finished_at ? ` ${formatTime(attempt.finished_at)}` : ''}{attempt.error ? ` — ${attempt.error}` : ''}</li>)}</ol></details> : null}
    </li>
  );
});

const recordingApi = { listLiveRecordings };
type SessionGate = Parameters<typeof usePolledSnapshot>[1];

/**
 * Live recordings, newest first, one bounded page at a time. Polls (via the shared
 * snapshot poller) only until everything shown has finished.
 */
export function LiveRecordingActivity({ session, onOpenItem, api = recordingApi }: { session: SessionGate; onOpenItem?: (libraryItemId: string) => void; api?: typeof recordingApi }) {
  const [recordings, setRecordings] = useState<LiveRecording[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [pages, setPages] = useState(1);
  const [attempt, setAttempt] = useState(0);

  usePolledSnapshot(`${pages}:${attempt}`, session, async (isCurrent) => {
    try {
      // Re-read every page already shown so paging never loses rows on refresh.
      let next: string | null = null;
      const items: LiveRecording[] = [];
      for (let page = 0; page < pages; page += 1) {
        const result = await api.listLiveRecordings(next ? { cursor: next } : {});
        items.push(...result.items);
        next = result.next_cursor;
        if (!next) break;
      }
      if (!isCurrent()) return false;
      setRecordings(items);
      setCursor(next);
      setError(false);
      return items.every((recording) => isLiveRecordingTerminal(recording.status));
    } catch {
      if (isCurrent()) setError(true);
      return true;
    }
  });

  // Nothing while the first answer is out: most members have no recordings, and a skeleton that collapses would pull the sections below it up.
  if ((!recordings || !recordings.length) && !error) return null;
  return (
    <section aria-labelledby="live-recordings-title" className="g-downloads-section">
      <h2 id="live-recordings-title">Live recordings{recordings ? <span className="g-label">{recordings.length}</span> : null}</h2>
      <ul>
        {recordings?.map((recording) => {
          const summary = summarizeLiveRecording(recording);
          const note = summary.waitingNote || summary.endNote || summary.limitsNote;
          return (
            <li className="g-list-row g-job" key={recording.id}>
              <span className="g-job-thumb"><Radio aria-hidden="true" /></span>
              <div className="g-job-copy">
                <span className="g-list-row-title g-clamp-2" title={recording.title || recording.source_url}>{recording.title || 'Live broadcast'}</span>
                <span className="g-list-row-meta">{[note, summary.restartNote, recording.kept ? 'Kept — exempt from automatic cleanup.' : null].filter(Boolean).join(' ')}</span>
                <StatusText tone={isLiveRecordingTerminal(recording.status) ? 'muted' : 'attention'}>{summary.headline}</StatusText>
              </div>
              <div className="g-list-row-actions">{summary.libraryItemId && onOpenItem ? <Button icon={<Library />} onClick={() => onOpenItem(summary.libraryItemId as string)} variant="quiet">Open</Button> : null}</div>
            </li>
          );
        })}
      </ul>
      {error ? <ErrorState onRetry={() => setAttempt((value) => value + 1)} title="Live recordings are unavailable right now." /> : null}
      {cursor && !error ? <div className="g-downloads-more"><Button onClick={() => setPages((value) => value + 1)}>Load more</Button></div> : null}
    </section>
  );
}

export function DownloadsSurface({
  jobs,
  onCancel,
  onRetry,
  onClear,
  onOpenItem,
  loadState,
  problem,
  onRetryLoad,
  onSignIn,
  retryingLoad,
  playlistActivity,
  recordingActivity,
  hasMoreJobs = false,
  loadingMoreJobs = false,
  loadMoreJobsError = null,
  onLoadMoreJobs,
}: {
  jobs: DownloadJob[];
  onCancel: (job: DownloadJob) => void;
  onRetry: (job: DownloadJob) => Promise<void>;
  onClear: () => void;
  onOpenItem?: (libraryItemId: string) => void;
  loadState: CollectionLoadState;
  problem: CollectionLoadProblem | null;
  onRetryLoad: () => void;
  onSignIn: () => void;
  retryingLoad: boolean;
  playlistActivity?: ReactNode;
  recordingActivity?: ReactNode;
  hasMoreJobs?: boolean;
  loadingMoreJobs?: boolean;
  loadMoreJobsError?: string | null;
  onLoadMoreJobs?: () => void;
}) {
  const active = jobs.filter((job) => ACTIVE.includes(job.status));
  const attention = jobs.filter((job) => ATTENTION.includes(job.status));
  const finished = jobs.filter((job) => !ACTIVE.includes(job.status) && !ATTENTION.includes(job.status));
  const loadMoreJobs = onLoadMoreJobs ?? (() => {});
  const listed = loadState !== 'loading' && loadState !== 'offline' && loadState !== 'failed';
  const pagingActive = hasMoreJobs && listed;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const recoveryButtonRef = useRef<HTMLButtonElement>(null);
  const wasRetryingRef = useRef(false);
  const restoreFocusRef = useRef(false);
  const retryLoad = () => { restoreFocusRef.current = true; onRetryLoad(); };
  useEffect(() => {
    if (wasRetryingRef.current && !retryingLoad && restoreFocusRef.current) {
      if (loadState === 'ready' || loadState === 'empty') headingRef.current?.focus();
      else recoveryButtonRef.current?.focus();
      restoreFocusRef.current = false;
    }
    wasRetryingRef.current = retryingLoad;
  }, [loadState, retryingLoad]);
  const rows = (list: DownloadJob[]) => <ul>{list.map((job) => <JobRow job={job} key={job.id} onCancel={onCancel} onOpenItem={onOpenItem} onRetry={onRetry} />)}</ul>;
  const section = (title: string, list: DownloadJob[]) => (list.length ? <section aria-label={title} className="g-downloads-section"><h2>{title} <span className="g-label">{list.length}</span></h2>{rows(list)}</section> : null);
  return (
    <div aria-busy={loadState === 'loading' || retryingLoad} className="surface gallery g-downloads">
      <Masthead
        actions={loadState === 'ready' && finished.some((job) => job.status === 'completed') ? <Button onClick={onClear} title="Removes saved entries from this list. The files stay in your Library.">Clear finished</Button> : undefined}
        headingRef={headingRef}
        meta={`${active.length} downloading · ${attention.length} needs attention · ${finished.length} finished`}
        title="Downloads"
      />
      {loadState === 'stale' ? <StatusText tone="attention">Activity may be out of date</StatusText> : null}
      {loadState === 'loading' ? <Skeleton count={3} label="Loading downloads…" shape="row" /> : null}
      {(loadState === 'offline' || loadState === 'failed') ? <CollectionRecovery buttonRef={recoveryButtonRef} label="Downloads" onRetry={retryLoad} onSignIn={onSignIn} problem={problem} retrying={retryingLoad} state={loadState} /> : null}
      {loadState === 'stale' ? <StaleCollectionNotice buttonRef={recoveryButtonRef} label="Downloads" onRetry={retryLoad} onSignIn={onSignIn} problem={problem} retrying={retryingLoad} /> : null}
      {listed && !jobs.length ? <EmptyState action={<Button onClick={() => openPalette({ mode: 'link' })} variant="primary">Add a link</Button>} body={loadState === 'stale' ? 'Vault activity may have changed while the connection was unavailable.' : 'Links you add and channels you follow arrive here.'} size="page" title={loadState === 'stale' ? 'No active downloads in the last-known snapshot' : 'Nothing is downloading.'} /> : null}
      {listed ? section('Downloading', active) : null}
      {listed ? section('Needs attention', attention) : null}
      {listed ? section('Finished', finished) : null}
      {pagingActive ? (
        <div className="g-downloads-more">
          {loadMoreJobsError
            ? <ErrorState onRetry={loadMoreJobs} title={loadMoreJobsError} />
            : <Button busy={loadingMoreJobs} onClick={loadMoreJobs}>Show more</Button>}
        </div>
      ) : null}
      {recordingActivity}
      {playlistActivity}
    </div>
  );
}
