import { useEffect, useState } from 'react';
import { LoaderCircle, RotateCcw, Sparkles } from 'lucide-react';
import { ApiRequestError, getLatestSummary, getSummary, requestSummary, type Summary, type Transcript } from '../../api';
import { cueTime } from './TranscriptPanel';
import { usePollJob } from './usePollJob';

const DONE = new Set(['succeeded', 'failed', 'canceled', 'interrupted']);

/**
 * Summary tool. Never generates on open (only on an explicit click) and keeps the previous
 * result visible while a new one runs; every point links to the transcript cue it cites.
 */
export function SummaryPanel({ itemId, transcripts, onSeek, aiEnabled, onLatest }: { itemId: string; transcripts: Transcript[] | null; onSeek: (seconds: number) => void; aiEnabled?: boolean; onLatest?: (summary: Summary | null) => void }) {
  const [latest, setLatest] = useState<Summary | null | undefined>(undefined);
  const [job, setJob] = useState<Summary | null>(null);
  const [problem, setProblem] = useState<{ aiOff?: boolean; message: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getLatestSummary(itemId, controller.signal).then((summary) => setLatest(summary?.state === 'succeeded' ? summary : null)).catch(() => { if (!controller.signal.aborted) setLatest(null); });
    return () => controller.abort();
  }, [itemId]);

  // Moments and the idea graph read this latest summary instead of fetching it again.
  useEffect(() => { if (latest !== undefined) onLatest?.(latest); }, [latest]);

  const jobId = job && !DONE.has(job.state) ? job.id : null;
  usePollJob(jobId, getSummary, DONE, (next) => { if (next.state === 'succeeded') setLatest(next); setJob(next); });

  async function generate() {
    setProblem(null);
    try {
      const summary = await requestSummary(itemId);
      setJob(summary);
      if (summary.state === 'succeeded') setLatest(summary);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not start a summary.';
      setProblem({ aiOff: error instanceof ApiRequestError && error.status === 409 && message.includes('AI'), message });
    }
  }

  const source = latest ? transcripts?.find((entry) => entry.id === latest.transcript_id) : undefined;
  const stale = Boolean(source && transcripts?.some((entry) => entry.language === source.language && entry.source_kind === source.source_kind && entry.revision > source.revision));
  const running = Boolean(jobId);
  const failed = job && (job.state === 'failed' || job.state === 'canceled' || job.state === 'interrupted') ? job : null;
  const aiOff = aiEnabled === false || Boolean(problem?.aiOff);
  const canGenerate = Boolean(transcripts?.length) && !aiOff && !running && (!latest || stale || failed);

  if (latest === undefined || transcripts === null) return <p className="summary-state">Loading summary…</p>;

  return (
    <div className="summary-panel">
      {latest ? (
        <article className="summary-card">
          <p className="summary-provenance">
            Generated locally · {latest.model_id}{latest.completed_at ? ` · ${new Date(latest.completed_at).toLocaleDateString()}` : ''}
            {stale ? <strong className="summary-stale"> · From an older transcript</strong> : null}
          </p>
          {latest.overview ? <p className="summary-overview">{latest.overview}</p> : null}
          {latest.key_points.length ? (
            <>
              <h3>Key points</h3>
              <ul className="summary-points">
                {latest.key_points.map((point) => (
                  <li key={`${point.start_ms}-${point.text}`}>
                    <span>{point.text}</span>
                    <button aria-label={`Play evidence at ${cueTime(point.start_ms)}`} className="g-chip" onClick={() => onSeek(point.start_ms / 1000)} type="button">{cueTime(point.start_ms)}</button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {latest.dropped_points ? <p className="summary-note">{latest.dropped_points} point{latest.dropped_points === 1 ? '' : 's'} omitted: no supporting transcript evidence.</p> : null}
          {latest.chapters.length ? (
            <>
              <h3>Chapters</h3>
              <ol className="summary-chapters">
                {latest.chapters.map((chapter) => (
                  <li key={chapter.cue_ordinal}><button onClick={() => onSeek(chapter.start_ms / 1000)} type="button"><span className="transcript-time">{cueTime(chapter.start_ms)}</span>{chapter.title}</button></li>
                ))}
              </ol>
            </>
          ) : null}
        </article>
      ) : !transcripts.length ? (
        <p className="summary-state"><strong>No transcript for this video.</strong> Summaries are built only from a transcript, and this video has no captions yet.</p>
      ) : !running && !aiOff ? <p className="summary-state">No summary yet. Generate one from the transcript on your local model.</p> : null}
      {aiOff && !running ? (
        <p className="summary-state" role={problem ? 'alert' : undefined}>Local AI isn’t set up, so new summaries are unavailable. Ask an admin to set up the assistant server in Settings › AI & models.</p>
      ) : problem ? <p className="summary-state" role="alert">{problem.message}</p> : null}
      {running ? <p className="summary-state" role="status"><LoaderCircle className="spin" /> Generating summary on your local model…</p> : null}
      {failed ? <p className="summary-state" role="alert">{failed.state === 'interrupted' ? 'The summary was interrupted by a restart.' : failed.state === 'canceled' ? 'The summary was cancelled by an admin.' : `The summary failed${failed.error ? `: ${failed.error}` : '.'}`}</p> : null}
      {canGenerate ? (
        <button className="g-button" onClick={() => void generate()} type="button">
          {failed ? <RotateCcw /> : <Sparkles />}{failed ? 'Retry summary' : stale ? 'Summarize the latest transcript' : 'Generate summary'}
        </button>
      ) : null}
    </div>
  );
}
