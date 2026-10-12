import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react';
import { AudioLines, ChevronDown, ChevronUp, LoaderCircle, LocateFixed, RotateCcw, Search } from 'lucide-react';
import {
  ApiRequestError, type AsrJob, type EnrichmentCapabilities, getEnrichmentCapabilities, getLatestAsrJob, listTranscriptCues, listTranscripts,
  requestAsrTranscript, searchTranscript, type Transcript, type TranscriptCue,
} from '../../api';
import { formatDuration } from '../../utils';
import { usePollJob } from './usePollJob';

/** What members may request (AI summaries, speech recognition); null until known or when there is no library item. */
export function useEnrichment(active: boolean): EnrichmentCapabilities | null {
  const [caps, setCaps] = useState<EnrichmentCapabilities | null>(null);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    getEnrichmentCapabilities(controller.signal).then(setCaps).catch(() => undefined);
    return () => controller.abort();
  }, [active]);
  return active ? caps : null;
}

/** Transcripts of a library item; null while loading or when unavailable (the tab then stays hidden). Bump `revision` to reload. */
export function useTranscripts(itemId: string | undefined, revision = 0): Transcript[] | null {
  const [state, setState] = useState<{ itemId?: string; transcripts: Transcript[] | null }>({ transcripts: null });
  useEffect(() => {
    if (!itemId) return;
    const controller = new AbortController();
    listTranscripts(itemId, controller.signal)
      .then((transcripts) => setState({ itemId, transcripts: Array.isArray(transcripts) ? transcripts : [] }))
      .catch(() => { if (!controller.signal.aborted) setState({ itemId, transcripts: [] }); });
    return () => controller.abort();
  }, [itemId, revision]);
  return itemId && state.itemId === itemId ? state.transcripts : null;
}

export const cueTime = (ms: number) => formatDuration(Math.floor(ms / 1000));

const sourceLabel = (kind: string) => (kind === 'asr' ? 'Local speech recognition' : 'Source captions');

function languageName(code: string) {
  try {
    return new Intl.DisplayNames(undefined, { type: 'language' }).of(code) || code;
  } catch {
    return code;
  }
}

function highlight(text: string, query: string): ReactNode {
  if (!query) return text;
  const lower = text.toLowerCase();
  const needle = query.toLowerCase();
  const parts: ReactNode[] = [];
  let from = 0;
  for (let at = lower.indexOf(needle); at >= 0; at = lower.indexOf(needle, at + needle.length)) {
    parts.push(text.slice(from, at), <mark key={at}>{text.slice(at, at + needle.length)}</mark>);
    from = at + needle.length;
  }
  parts.push(text.slice(from));
  return parts;
}

/** Index of the cue playing at `ms` (last cue starting at or before it), or -1. Cues are in time order. */
export function activeCueIndex(cues: TranscriptCue[], ms: number): number {
  let low = 0;
  let high = cues.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (cues[mid].start_ms <= ms) { found = mid; low = mid + 1; } else high = mid - 1;
  }
  return found;
}

type Pages = { id: string; cues: TranscriptCue[]; cursor: string | null; queue: Promise<void> };

type PanelProps = { transcripts: Transcript[]; currentTime: number; onSeek: (seconds: number) => void };

/** Transcript tool; with speech recognition on, offers a local transcript when there is none or only captions. */
export function TranscriptPanel({ itemId, asr = false, onGenerated, ...props }: PanelProps & { itemId?: string; asr?: boolean; onGenerated?: () => void }) {
  const offer = asr && itemId && !props.transcripts.some((entry) => entry.source_kind === 'asr');
  return (
    <>
      {offer ? <GenerateTranscript hasCaptions={props.transcripts.length > 0} itemId={itemId} onGenerated={onGenerated ?? (() => undefined)} /> : null}
      {props.transcripts.length ? <TranscriptView {...props} /> : null}
    </>
  );
}

const ASR_DONE = new Set(['succeeded', 'failed', 'canceled', 'interrupted']);
const ASR_FAILED: Record<string, string> = { failed: 'Transcription failed', canceled: 'Transcription was cancelled', interrupted: 'Transcription was interrupted by a restart' };

function GenerateTranscript({ itemId, hasCaptions, onGenerated }: { itemId: string; hasCaptions: boolean; onGenerated: () => void }) {
  const [job, setJob] = useState<AsrJob | null | undefined>(undefined);
  const [problem, setProblem] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getLatestAsrJob(itemId, controller.signal).then(setJob).catch(() => { if (!controller.signal.aborted) setJob(null); });
    return () => controller.abort();
  }, [itemId]);

  const jobId = job && !ASR_DONE.has(job.state) ? job.id : null;
  usePollJob(jobId, () => getLatestAsrJob(itemId), ASR_DONE, (next) => { setJob(next); if (next.state === 'succeeded') onGenerated(); });

  async function start() {
    setProblem(null);
    setStarting(true);
    try {
      const next = await requestAsrTranscript(itemId);
      setJob(next);
      if (next.state === 'succeeded') onGenerated();
    } catch (error) {
      setProblem(error instanceof ApiRequestError && error.status === 409
        ? 'Speech recognition isn’t set up on this vault. Ask an admin to turn this on.'
        : error instanceof Error ? error.message : 'Could not start a transcript.');
    } finally {
      setStarting(false);
    }
  }

  if (job === undefined) return null;
  const failed = job && ASR_FAILED[job.state] ? job : null;
  return (
    <div className="summary-panel">
      {jobId ? (
        <p className="summary-state" role="status"><LoaderCircle className="spin" /> Transcribing the audio on your local server… Long videos can take several minutes.</p>
      ) : (
        <>
          {!hasCaptions && !failed ? <p className="summary-state"><strong>No captions for this video.</strong> Generate a transcript from its audio with your local speech recognition server.</p> : null}
          {failed ? <p className="summary-state" role="alert">{ASR_FAILED[failed.state]}{failed.error ? `: ${failed.error}` : '.'}</p> : null}
          {problem ? <p className="summary-state" role="alert">{problem}</p> : null}
          <button className="g-button" disabled={starting} onClick={() => void start()} type="button">
            {failed ? <RotateCcw /> : <AudioLines />}{failed ? 'Retry transcript' : hasCaptions ? 'Generate a speech transcript' : 'Generate transcript'}
          </button>
        </>
      )}
    </div>
  );
}

function TranscriptView({ transcripts, currentTime, onSeek }: PanelProps) {
  const [selectedId, setSelectedId] = useState(transcripts[0].id);
  const transcript = transcripts.find((entry) => entry.id === selectedId) || transcripts[0];
  const pages = useRef<Pages>({ id: '', cues: [], cursor: '', queue: Promise.resolve() });
  const [cues, setCues] = useState<TranscriptCue[]>([]);
  const [error, setError] = useState(false);
  const [follow, setFollow] = useState(true);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState<{ query: string; matches: TranscriptCue[]; index: number } | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const programmaticScroll = useRef(false);

  // Pages load strictly in order (cursor = last ordinal), serialized on one queue per revision.
  // loaded cues stay in the DOM (content-visibility skips offscreen layout); window the list if 20k-cue transcripts feel slow.
  function loadWhile(need: (loaded: TranscriptCue[]) => boolean) {
    const state = pages.current;
    state.queue = state.queue.then(async () => {
      while (state.cursor !== null && need(state.cues) && pages.current === state) {
        const page = await listTranscriptCues(state.id, state.cursor || null);
        if (pages.current !== state) return;
        state.cues = [...state.cues, ...page.items];
        state.cursor = page.next_cursor;
        setCues(state.cues);
      }
    }).catch(() => { if (pages.current === state) setError(true); });
    return state.queue;
  }

  useEffect(() => {
    pages.current = { id: transcript.id, cues: [], cursor: '', queue: Promise.resolve() };
    setCues([]);
    setError(false);
    setSearch(null);
    void loadWhile((loaded) => loaded.length === 0);
  }, [transcript.id]);

  const nowMs = currentTime * 1000;
  const active = activeCueIndex(cues, nowMs);
  const currentMatch = search?.matches[search.index];

  useEffect(() => {
    if (!follow || error) return;
    const last = pages.current.cues.at(-1);
    if (last && last.end_ms < nowMs) void loadWhile((loaded) => (loaded.at(-1)?.end_ms ?? 0) < nowMs);
  }, [follow, nowMs, error]);

  function scrollToOrdinal(ordinal: number) {
    const list = listRef.current;
    const row = list?.querySelector<HTMLElement>(`[data-ordinal="${ordinal}"]`);
    if (!list || !row) return;
    const top = Math.max(0, row.offsetTop - list.clientHeight / 3);
    if (Math.abs(list.scrollTop - top) < 1) return;
    programmaticScroll.current = true;
    list.scrollTo({ top });
  }

  useEffect(() => {
    if (follow && active >= 0) scrollToOrdinal(cues[active].ordinal);
  }, [follow, active, cues]);

  useEffect(() => {
    if (currentMatch) scrollToOrdinal(currentMatch.ordinal);
  }, [currentMatch, cues.length]);

  function onListScroll() {
    const list = listRef.current;
    if (!list) return;
    if (programmaticScroll.current) programmaticScroll.current = false;
    else setFollow(false);
    if (list.scrollHeight - list.scrollTop - list.clientHeight < 400) {
      const loaded = pages.current.cues.length;
      void loadWhile((next) => next.length === loaded);
    }
  }

  async function runSearch(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q) { setSearch(null); return; }
    try {
      const matches = await searchTranscript(transcript.id, q);
      setSearch({ query: q, matches, index: 0 });
      if (matches.length) goToMatch(matches[0]);
    } catch {
      setSearch({ query: q, matches: [], index: 0 });
    }
  }

  function goToMatch(match: TranscriptCue) {
    setFollow(false);
    void loadWhile((loaded) => loaded.length <= match.ordinal);
  }

  function step(delta: number) {
    if (!search?.matches.length) return;
    const index = (search.index + delta + search.matches.length) % search.matches.length;
    setSearch({ ...search, index });
    goToMatch(search.matches[index]);
  }

  return (
    <div className="transcript-panel">
      <div className="transcript-toolbar">
        {transcripts.length > 1 ? (
          <label className="transcript-version g-select">
            <span className="sr-only">Transcript language</span>
            <select onChange={(event) => setSelectedId(event.currentTarget.value)} value={transcript.id}>
              {transcripts.map((entry) => <option key={entry.id} value={entry.id}>{languageName(entry.language)} · {sourceLabel(entry.source_kind)} · rev {entry.revision}</option>)}
            </select>
          </label>
        ) : <p className="transcript-version">{languageName(transcript.language)} · {sourceLabel(transcript.source_kind)}</p>}
        <form className="transcript-search" onSubmit={(event) => void runSearch(event)} role="search">
          <Search aria-hidden="true" />
          <input aria-label="Search transcript" onChange={(event) => setQuery(event.currentTarget.value)} placeholder="Search transcript" type="search" value={query} />
          {search ? (
            <>
              <span aria-live="polite" className="transcript-search-count">{search.matches.length ? `${search.index + 1} of ${search.matches.length}${search.matches.length === 100 ? '+' : ''}` : 'No matches'}</span>
              <button aria-label="Previous match" className="g-icon-button" disabled={!search.matches.length} onClick={() => step(-1)} type="button"><ChevronUp /></button>
              <button aria-label="Next match" className="g-icon-button" disabled={!search.matches.length} onClick={() => step(1)} type="button"><ChevronDown /></button>
            </>
          ) : null}
        </form>
        <button aria-pressed={follow} className="g-button transcript-follow" onClick={() => setFollow(!follow)} type="button"><LocateFixed />{follow ? 'Following playback' : 'Follow playback'}</button>
      </div>
      {error ? (
        <p className="transcript-state" role="alert">Couldn’t load this transcript. <button className="g-text-button" onClick={() => { setError(false); void loadWhile((loaded) => loaded.length === 0); }} type="button">Try again</button></p>
      ) : !cues.length ? <p className="transcript-state">Loading transcript…</p> : null}
      <ol aria-label="Transcript" className="transcript-cues" onScroll={onListScroll} ref={listRef}>
        {cues.map((cue, index) => (
          <li data-ordinal={cue.ordinal} key={cue.ordinal}>
            <button aria-current={index === active ? 'true' : undefined} className={cue.ordinal === currentMatch?.ordinal ? 'current-match' : undefined} onClick={() => onSeek(cue.start_ms / 1000)} type="button">
              <span className="transcript-time">{cueTime(cue.start_ms)}</span>
              <span>{highlight(cue.text, search?.query || '')}</span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
