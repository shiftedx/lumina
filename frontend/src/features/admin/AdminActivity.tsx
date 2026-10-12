import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Square } from 'lucide-react';

import { getActivityHistory, getAdminActivity, listUsers, stopActivitySession } from '../../api';
import type { ActivityHistoryRow, ActivityServer, ActivitySession, AdminActivity, UserProfile } from '../../types';
import { usePolledSnapshot } from '../../app/usePolledSnapshot';
import { Button, ConfirmDialog, EmptyState, ErrorState, Input, ProgressBar, Select, StatusText } from '../../ui';
import { errorMessage, formatDateTime, formatDuration, formatSpeed } from '../../utils';
import { audioLine, clientLine, gigabytes, HARDWARE_LABELS, hardwareStatus, METHOD_LABELS, NOT_AVAILABLE, playingFor, relativeTime, uptimeLabel, videoLine } from './activityModel';
import './activity.css';

const POLL_MS = 3000;
const GATE = { captureSessionToken: () => 0, isSessionTokenCurrent: () => true }; // the section unmounts on sign-out; `isCurrent` covers that
const PAGE = 50;

type ActivityState = { data: AdminActivity | null; error: string | null; refresh: () => Promise<void> };
const ActivityContext = createContext<ActivityState | null>(null);
const useActivity = () => {
  const value = useContext(ActivityContext);
  if (!value) throw new Error('Activity rows render inside the Activity section.');
  return value;
};

/** Polls the live snapshot every 3 s (paused while the tab is hidden); a failed poll keeps the last good one. */
export function ActivityProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<AdminActivity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const load = useCallback(async (isCurrent: () => boolean = () => live.current) => {
    try {
      const next = await getAdminActivity();
      if (isCurrent()) { setData(next); setError(null); }
    } catch (failure) {
      if (isCurrent()) setError(errorMessage(failure, 'Unable to load activity.'));
    }
  }, []);
  usePolledSnapshot('activity', GATE, load, POLL_MS);
  return <ActivityContext.Provider value={{ data, error, refresh: () => load() }}>{children}</ActivityContext.Provider>;
}

const Badge = ({ children }: { children: ReactNode }) => <span className="activity-badge">{children}</span>;

function SessionRow({ session, onStop }: { session: ActivitySession; onStop: (session: ActivitySession) => void }) {
  const video = videoLine(session.video);
  const audio = audioLine(session.audio);
  const percent = session.position_seconds != null && session.duration_seconds ? (session.position_seconds / session.duration_seconds) * 100 : null;
  return (
    <li className="activity-session">
      {session.artwork_url ? <img alt="" className="activity-art" loading="lazy" src={session.artwork_url} /> : <span aria-hidden="true" className="activity-art" />}
      <div className="activity-main">
        <p className="activity-title">{session.title}</p>
        {session.subtitle ? <p className="activity-sub">{session.subtitle}</p> : null}
        <p className="activity-sub">{session.user.name} · {clientLine(session.client)}</p>
        <div className="activity-badges">
          <Badge>{METHOD_LABELS[session.method]}</Badge>
          {session.hardware ? <Badge>{HARDWARE_LABELS[session.hardware]}</Badge> : null}
          {session.speed != null ? <Badge>{session.speed.toFixed(1)}×</Badge> : null}
          {session.throttled ? <Badge>Throttled</Badge> : null}
        </div>
        {video || audio ? <p className="activity-sub">{[video, audio ? `Audio ${audio}` : ''].filter(Boolean).join(' · ')}</p> : null}
        {percent !== null ? <ProgressBar label={`${session.title} progress`} value={percent} /> : null}
        <p className="activity-sub g-tabular">
          {session.position_seconds != null ? `${formatDuration(session.position_seconds)} / ${formatDuration(session.duration_seconds)} · ` : ''}{playingFor(session.started_at)}
        </p>
      </div>
      {session.stoppable ? <Button aria-label={`Stop ${session.title}`} icon={<Square />} onClick={() => onStop(session)} variant="danger">Stop</Button> : null}
    </li>
  );
}

export function ActivityNowPlaying() {
  const { data, error, refresh } = useActivity();
  const [pending, setPending] = useState<ActivitySession | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setStopError(null);
    try {
      await stopActivitySession(pending.id);
      setPending(null);
      await refresh();
    } catch (failure) {
      const gone = (failure as { status?: number }).status === 404; // already ended
      if (gone) { setPending(null); await refresh(); } else setStopError(errorMessage(failure, 'Unable to stop this stream.'));
    } finally { setBusy(false); }
  }
  if (!data) return error ? <ErrorState onRetry={() => { void refresh(); }} title="Activity is unavailable" body={error} /> : <p aria-busy="true" className="g-setting-note">Loading…</p>;
  return (
    <div>
      {error ? <StatusText tone="danger"><span role="alert">{error} Showing the last update.</span></StatusText> : null}
      {data.sessions.length ? <ul className="activity-list">{data.sessions.map((session) => <SessionRow key={session.id} onStop={(next) => { setStopError(null); setPending(next); }} session={session} />)}</ul> : <EmptyState title="Nothing is playing right now." />}
      <ConfirmDialog
        body={<><p>Stop this stream? {pending?.user.name} will see that the server owner stopped it.</p>{stopError ? <p role="alert"><StatusText tone="danger">{stopError}</StatusText></p> : null}</>}
        busy={busy} confirmLabel="Stop stream" danger onCancel={() => setPending(null)} onConfirm={() => { void confirm(); }} open={pending !== null} title={`Stop ${pending?.title ?? 'stream'}?`}
      />
    </div>
  );
}

const Stat = ({ label, value, note }: { label: string; value: string | null; note?: string }) => (
  <div className="g-stat">
    <dt className="g-label">{label}</dt>
    <dd className={value === null ? 'g-stat-note activity-na' : 'g-stat-figure'}>{value ?? NOT_AVAILABLE}</dd>
    {note && value !== null ? <dd className="g-stat-note">{note}</dd> : null}
  </div>
);
const pct = (value: number | null) => (value === null ? null : `${value.toFixed(0)} %`);

export function ActivityServerStats() {
  const { data } = useActivity();
  if (!data) return <p aria-busy="true" className="g-setting-note">Loading…</p>;
  const server: ActivityServer = data.server;
  const [tone, text] = hardwareStatus(server.hardware);
  return (
    <div>
      <dl className="g-stat-strip activity-stats">
        <Stat label="CPU" value={pct(server.cpu_percent)} />
        <Stat label="Memory" note={server.memory ? `of ${gigabytes(server.memory.total_bytes)}` : undefined} value={server.memory ? gigabytes(server.memory.used_bytes) : null} />
        <Stat label="Load average" value={server.load_average ? server.load_average[0].toFixed(2) : null} note={server.load_average ? `5 min ${server.load_average[1].toFixed(2)} · 15 min ${server.load_average[2].toFixed(2)}` : undefined} />
        <Stat label="Transcoder CPU" value={pct(server.transcoder_cpu_percent)} />
        <Stat label="ffmpeg processes" value={String(server.ffmpeg_processes)} />
        <Stat label="Uptime" value={server.uptime_seconds == null ? null : uptimeLabel(server.uptime_seconds)} />
      </dl>
      <p><StatusText tone={tone}>{text}</StatusText></p>
      <p className="g-setting-note">{server.hardware.fallbacks} fallback{server.hardware.fallbacks === 1 ? '' : 's'} to software encoding since the server started.</p>
    </div>
  );
}

const RECORDING_STATUS = { queued: 'Queued', live: 'Recording live', stopping: 'Stopping', finalizing: 'Finishing up' } as const;

export function ActivityBackground() {
  const { data } = useActivity();
  if (!data) return <p aria-busy="true" className="g-setting-note">Loading…</p>;
  if (!data.downloads.length && !data.recordings.length) return <EmptyState title="No downloads or recordings are running." />;
  return (
    <ul className="activity-list">
      {data.downloads.map((job) => (
        <li className="activity-job" key={`d-${job.id}`}>
          <p className="activity-title">{job.title ?? 'Download'}</p>
          <p className="activity-sub">Download · {job.user.name}{job.speed_bytes ? ` · ${formatSpeed(job.speed_bytes)}` : ''}{job.progress != null ? ` · ${Math.round(job.progress)} %` : ''}</p>
          <ProgressBar label={`${job.title ?? 'Download'} progress`} value={job.progress} />
        </li>
      ))}
      {data.recordings.map((job) => (
        <li className="activity-job" key={`r-${job.id}`}>
          <p className="activity-title">{job.title ?? 'Live recording'}</p>
          <p className="activity-sub">Recording · {job.user.name} · {RECORDING_STATUS[job.status]}</p>
        </li>
      ))}
    </ul>
  );
}

const useDebounced = (value: string, ms: number) => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => { const timer = window.setTimeout(() => setDebounced(value), ms); return () => window.clearTimeout(timer); }, [value, ms]);
  return debounced;
};

export function ActivityHistory() {
  const [members, setMembers] = useState<UserProfile[]>([]);
  const [userId, setUserId] = useState('');
  const [text, setText] = useState('');
  const q = useDebounced(text.trim(), 300);
  const [rows, setRows] = useState<ActivityHistoryRow[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  useEffect(() => { let live = true; listUsers().then((list) => { if (live) setMembers(list); }, () => undefined); return () => { live = false; }; }, []);
  const load = useCallback((before: string | null) => {
    const token = ++request.current;
    setLoading(true);
    setError(null);
    getActivityHistory({ userId, q, before, limit: PAGE }).then((page) => {
      if (token !== request.current) return;
      setRows((current) => (before ? [...current, ...page.items] : page.items));
      setNext(page.next_before);
    }).catch((failure: unknown) => { if (token === request.current) setError(errorMessage(failure, 'Unable to load history.')); })
      .finally(() => { if (token === request.current) setLoading(false); });
  }, [userId, q]);
  useEffect(() => { load(null); return () => { request.current += 1; }; }, [load]);
  // Members who appear in history but are no longer listed (removed accounts) stay filterable.
  const known = new Map<string, string>(members.map((member) => [member.id, member.display_name || member.username]));
  for (const row of rows) if (!known.has(row.user.id)) known.set(row.user.id, row.user.name);
  return (
    <div>
      <div className="activity-filters">
        <label className="activity-filter"><span className="g-label">Member</span>
          <Select onChange={(event) => setUserId(event.target.value)} value={userId}><option value="">Everyone</option>{[...known].map(([id, name]) => <option key={id} value={id}>{name}</option>)}</Select>
        </label>
        <label className="activity-filter"><span className="g-label">Search</span>
          <Input onChange={(event) => setText(event.target.value)} placeholder="Title or episode" type="search" value={text} />
        </label>
      </div>
      {error ? <ErrorState onRetry={() => load(null)} title="History is unavailable" body={error} /> : null}
      {rows.length ? (
        <div className="g-table-scroll">
          <table className="g-table activity-history">
            <caption className="sr-only">Playback history, newest first</caption>
            <thead><tr><th scope="col">When</th><th scope="col">Member</th><th scope="col">Title</th><th scope="col">Client</th><th scope="col">Method</th><th scope="col">Watched</th></tr></thead>
            <tbody>{rows.map((row) => (
              <tr key={row.id}>
                <td><time dateTime={row.ended_at} title={formatDateTime(row.ended_at)}>{relativeTime(row.ended_at)}</time></td>
                <td>{row.user.name}</td>
                <td><span className="activity-title">{row.title}</span>{row.subtitle ? <small>{row.subtitle}</small> : null}</td>
                <td>{clientLine(row.client)}</td>
                <td>{METHOD_LABELS[row.method]}{row.hardware ? <small>{HARDWARE_LABELS[row.hardware]}</small> : null}</td>
                <td className="g-tabular">{formatDuration(row.watched_seconds)}{row.stopped_by_admin ? <small>Stopped by owner</small> : null}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : !loading && !error ? <EmptyState title={q || userId ? 'Nothing matches.' : 'No playback history yet.'} /> : null}
      {loading ? <p aria-busy="true" className="g-setting-note">Loading…</p> : null}
      <p className="g-setting-note">History is kept for 90 days.</p>
      {next && !loading ? <Button onClick={() => load(next)}>Load more</Button> : null}
    </div>
  );
}
