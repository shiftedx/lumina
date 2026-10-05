/**
 * /live: masthead and kicker, a sticky toolbar with the provider chips, the availability
 * band, a hero for the most-watched followed stream (else the most-watched overall), Also live from your follows, the
 * Upcoming schedule, one rail per category and Recently ended. A 30 s refresh (LuminaApp's usePolledSnapshot) merges
 * in place: the focused card never moves, and one that left shows ENDED until focus leaves its rail.
 */
import { useCanDownload } from '../access/access';
import { CircleAlert, LoaderCircle } from 'lucide-react';
import { type FocusEvent, type KeyboardEvent, memo, type ReactNode, type RefObject, useEffect, useMemo, useRef, useState } from 'react';

import { createLiveRecording, getLiveWall, listLiveRecordings } from '../../api';
import { findChannelBySource } from '../../channelSubscriptions';
import { canRecordFromNow, canScheduleUpcoming, isLiveRecordingTerminal, liveRecordingStatusLabel } from '../../liveRecording';
import { SOURCE_LABELS } from '../../luminaModel';
import type { LibraryItem, LiveRecording, LiveSnapshot, RemoteEntry, SearchSource, SourceAutomation, YouTubeSearchResult } from '../../types';
import { formatCompactNumber, liveCountLabel } from '../../utils';
import { channelPagePath, followAppLink, rememberChannel, youtubeChannelId } from '../channels/channelMention';
import { FollowSettingsDialog, type FollowSettingsDialogProps } from '../channels/FollowSettingsDialog';
import { GalleryArt } from '../gallery/GalleryArt';
import { LiveBadge } from '../gallery/LiveBadge';
import { remoteArt, remoteColour, remoteLabel, remoteProvider } from '../gallery/remoteModel';
import { StillRail } from '../gallery/StillRail';
import { useMediaQuery } from '../gallery/WallGrid';
import { isBackKey, moveFocus } from '../media/focusNav';
import { clockTime, entryKey, liveKicker, liveNoticeLines, liveView, mergeRail, providerChecks, type RailMemory, startedAgo, UPCOMING_SHOWN, upcomingGroups } from './liveModel';

export const RECORD_BOUNDARY = 'Recording starts from the moment Lumina connects. Nothing earlier is imported. Chat is saved alongside where the provider offers it.';
const BOUNDARY_ID = 'live-record-boundary';

export type LiveSurfaceProps = {
  snapshot: LiveSnapshot | null;
  error: string | null;
  onOpen: (item: YouTubeSearchResult) => void;
  onQueue: (item: YouTubeSearchResult) => void;
  isQueueing: (item: YouTubeSearchResult) => boolean;
  library: LibraryItem[];
  /** The member's follows: the hero's Follow settings link finds its follow here. */
  channels?: SourceAutomation[];
  /** Try again: forces one poll. */
  onRetry?: () => void;
  /** "See all" state, in the address (?rail=); local when LuminaApp does not pass it. */
  rail?: string | null;
  onRailChange?: (rail: string | null) => void;
  /** The hero's Follow settings open the drawer in place; without it the link goes to the follow's page. */
  settings?: Omit<FollowSettingsDialogProps, 'automation' | 'onClose'>;
  /** Inside the Streaming page: no masthead, and the page's provider switch replaces the chips. */
  embedded?: boolean;
  provider?: SearchSource | 'all';
};

type Focus = { rail: string | null; card: string | null };
type RecordState = { recordings: Record<string, LiveRecording>; starting: string | null; error: string | null };

/** Record / Recording / Schedule / Scheduled for one entry; null when its capabilities allow neither. */
function RecordAction({ item, state, onRecord }: { item: RemoteEntry; state: RecordState; onRecord: (item: RemoteEntry) => void }) {
  if (!useCanDownload()) return null;
  const schedule = canScheduleUpcoming(item.capabilities);
  const record = !schedule && canRecordFromNow(item.capabilities);
  const recording = item.webpage_url ? state.recordings[item.webpage_url] : undefined;
  if (recording && !isLiveRecordingTerminal(recording.status)) {
    return <p className="g-label g-record-status" role="status"><span aria-hidden="true" className="g-record-dot" />{schedule ? 'Scheduled' : liveRecordingStatusLabel(recording.status)}</p>;
  }
  if (!record && !schedule) return null;
  const busy = state.starting === item.webpage_url;
  return (
    <button aria-describedby={BOUNDARY_ID} className="g-text-button g-button-text g-remote-action" data-focus-item disabled={busy} onClick={() => onRecord(item)} title={RECORD_BOUNDARY} type="button">
      {busy ? <LoaderCircle aria-hidden="true" className="spin" /> : null}{schedule ? 'Schedule' : 'Record'}
    </button>
  );
}

function LiveHero({ item, fromFollows, follow, action, onOpen, onSettings, watchRef }: { onSettings?: () => void; item: RemoteEntry; fromFollows: boolean; follow: SourceAutomation | null; action: ReactNode; onOpen: (item: RemoteEntry) => void; watchRef: RefObject<HTMLButtonElement | null> }) {
  const channelId = remoteProvider(item) === 'youtube' ? youtubeChannelId(item) : null;
  const provider = SOURCE_LABELS[remoteProvider(item)];
  const line = [item.view_count ? `${formatCompactNumber(item.view_count)} watching` : null, provider, startedAgo(item.published_at)].filter(Boolean).join(' · ');
  return (
    <section aria-labelledby="live-hero-name" className="g-live-hero" data-remote-key={entryKey(item)}>
      <div className="g-live-hero-art"><GalleryArt alt="" art={remoteArt(item.artwork_url)} card={null} colour={remoteColour(item)} kind="backdrop" priority={1} sizes="100vw" /></div>
      <div className="g-live-hero-copy">
        {fromFollows ? null : <p className="g-label g-live-hero-kicker">Most watched right now</p>}
        <LiveBadge state="live" surface="art" />
        <h2 className="g-live-hero-name" id="live-hero-name">{item.uploader || item.title || 'Live stream'}</h2>
        <p className="g-live-hero-title">{item.title}</p>
        {line ? <p className="g-label">{line}</p> : null}
        <div className="g-live-hero-actions" data-focus-row>
          <button aria-label={`Watch ${remoteLabel(item)}`} className="g-button is-primary g-live-watch" data-focus-item onClick={() => onOpen(item)} ref={watchRef} type="button">Watch</button>
          {action}
          {channelId ? <a className="g-button" data-focus-item href={channelPagePath(channelId)} onClick={(event) => { rememberChannel({ id: channelId, name: item.uploader || '' }); followAppLink(event); }}>Open channel</a> : null}
          {follow ? (onSettings
            ? <button className="g-button" data-focus-item onClick={onSettings} type="button">Follow settings</button>
            : <a className="g-button" data-focus-item href={`/subscriptions/${follow.id}`} onClick={followAppLink}>Follow settings</a>) : null}
        </div>
      </div>
    </section>
  );
}

function Schedule({ entries, actionFor, onOpen }: { entries: RemoteEntry[]; actionFor: (item: RemoteEntry) => ReactNode; onOpen: (item: RemoteEntry) => void }) {
  const [all, setAll] = useState(false);
  let budget = all ? Number.POSITIVE_INFINITY : UPCOMING_SHOWN;
  return (
    <section aria-labelledby="live-upcoming" className="g-schedule">
      <h2 id="live-upcoming">Upcoming</h2>
      {upcomingGroups(entries).map((group) => {
        if (budget <= 0) return null;
        const rows = group.entries.slice(0, budget);
        budget -= rows.length;
        return (
          <div className="g-schedule-day" key={group.key}>
            <h3 className="g-label">{group.label}</h3>
            <ul>
              {rows.map((item) => (
                <li className="g-schedule-row" data-focus-row data-remote-key={entryKey(item)} key={entryKey(item)}>
                  <span className="g-label g-schedule-time">{clockTime(item.capabilities?.scheduled_start) ?? '—'}</span>
                  <button aria-label={remoteLabel(item)} className="g-schedule-open" data-focus-item onClick={() => onOpen(item)} type="button">
                    <span className="g-schedule-still"><GalleryArt alt="" art={remoteArt(item.artwork_url)} card={{ name: item.title || '' }} colour={remoteColour(item)} kind="still" priority={3} sizes="120px" /></span>
                    <span className="g-schedule-copy"><span className="g-schedule-title">{item.title}</span>{item.uploader ? <span className="g-label">{item.uploader}</span> : null}</span>
                  </button>
                  {actionFor(item)}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      {!all && entries.length > UPCOMING_SHOWN ? <button className="g-text-button" data-focus-item onClick={() => setAll(true)} type="button">Show all {entries.length}</button> : null}
    </section>
  );
}

function LiveSurfaceView({ snapshot, error, onOpen, channels = [], onRetry, rail: railProp, onRailChange, settings, embedded, provider: providerProp }: LiveSurfaceProps) {
  const [settingsFor, setSettingsFor] = useState<string | null>(null);
  const [chosen, setProvider] = useState<SearchSource | 'all'>('all');
  const provider = providerProp ?? chosen;
  const phone = useMediaQuery('(max-width: 599px)');
  const canDownload = useCanDownload();
  const [record, setRecord] = useState<RecordState>({ recordings: {}, starting: null, error: null });
  const [localRail, setLocalRail] = useState<string | null>(null);
  const rail = onRailChange ? railProp ?? null : localRail;
  const changeRail = onRailChange ?? setLocalRail;
  const [focus, setFocus] = useState<Focus>({ rail: null, card: null });
  const memory = useRef(new Map<string, RailMemory>());
  const watchRef = useRef<HTMLButtonElement>(null);
  // Watch takes focus only when the page is entered by keyboard; otherwise the shell focuses the h1.
  const [keyboardEntry] = useState(() => { try { return document.activeElement?.matches(':focus-visible') ?? false; } catch { return false; } });
  const heroFocused = useRef(false);

  // Durable recording state: one page, once per visit (no polling here: Downloads follows recordings).
  useEffect(() => {
    let active = true;
    listLiveRecordings().then((page) => {
      if (!active) return;
      const bySource: Record<string, LiveRecording> = {};
      for (const recording of [...page.items].reverse()) bySource[recording.source_url] = recording;
      setRecord((current) => ({ ...current, recordings: bySource }));
    }).catch(() => { /* Cards simply offer their actions. */ });
    return () => { active = false; };
  }, []);

  async function startRecording(item: RemoteEntry) {
    const sourceUrl = item.webpage_url;
    if (!sourceUrl) return;
    setRecord((current) => ({ ...current, starting: sourceUrl, error: null }));
    try {
      const created = await createLiveRecording({ source_url: sourceUrl, start_intent: 'live_edge', fallback_policy: 'allow_live_edge' });
      setRecord((current) => ({ ...current, starting: null, recordings: { ...current.recordings, [sourceUrl]: created } }));
    } catch (failure) {
      setRecord((current) => ({ ...current, starting: null, error: failure instanceof Error ? failure.message : 'Lumina could not start this recording.' }));
    }
  }

  const view = liveView(snapshot, provider);
  const rails = useMemo(() => {
    const lists = [
      ...(view.alsoFollowed.length ? [{ key: 'follows', label: 'Also live from your follows', liveCount: null, entries: view.alsoFollowed }] : []),
      ...view.rails,
    ];
    const next = new Map<string, RailMemory>();
    const merged = lists.map((list) => {
      const kept = mergeRail(memory.current.get(list.key), list.entries, focus.rail === list.key ? focus.card : null);
      next.set(list.key, kept);
      return { ...list, entries: kept.entries, ended: kept.ended };
    });
    memory.current = next; // a whole rail that vanishes while focused is dropped with it; keep its memory if that is ever noticed
    return merged;
  }, [view, focus.rail, focus.card]);

  useEffect(() => {
    if (keyboardEntry && view.hero && !heroFocused.current && !rail) {
      heroFocused.current = true;
      watchRef.current?.focus();
    }
  }, [keyboardEntry, view.hero, rail]);

  const onFocusIn = (event: FocusEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    setFocus({ rail: target.closest('[data-rail-key]')?.getAttribute('data-rail-key') ?? null, card: target.closest('[data-remote-key]')?.getAttribute('data-remote-key') ?? null });
  };
  const onFocusOut = (event: FocusEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocus({ rail: null, card: null });
  };

  const liveTotal = liveCountLabel(snapshot?.live_total, phone, 'live now');
  const failedChecks = providerChecks(snapshot);
  const hasContent = Boolean(view.hero || view.upcoming.length || view.ended.length || rails.length);
  const notices = liveNoticeLines(snapshot, hasContent);
  const actionFor = (item: RemoteEntry) => <RecordAction item={item} onRecord={(entry) => void startRecording(entry)} state={record} />;
  const describedByFor = (item: RemoteEntry) => (canDownload && (canRecordFromNow(item.capabilities) || canScheduleUpcoming(item.capabilities)) ? BOUNDARY_ID : undefined);
  const follow = view.hero?.uploader_url ? findChannelBySource(channels, view.hero.uploader_url) ?? null : null;
  // The dialog is bound to the follow it opened on, not to whichever follow leads the hero after a poll.
  const settingsFollow = settingsFor ? channels.find((channel) => channel.id === settingsFor) ?? null : null;
  useEffect(() => { if (settingsFor && !settingsFollow) setSettingsFor(null); }, [settingsFor, settingsFollow]);
  const railProps = { actionsFor: actionFor, describedByFor, onOpen, onSeeAll: (key: string) => changeRail(key), showProvider: view.providers.length > 1 };

  // Back leaves "See all" as the browser Back does (the shell keeps the rail in the address).
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (rail && isBackKey(event)) { event.preventDefault(); if (onRailChange) window.history.back(); else changeRail(null); return; }
    moveFocus(event);
  };

  let body: ReactNode;
  const expanded = rail ? rails.find((list) => list.key === rail) ?? null : null;
  const wallKey = expanded && expanded.key !== 'follows' ? expanded.key : null;
  const loadWall = useMemo(() => (wallKey ? (cursor: string | null) => getLiveWall(wallKey, cursor) : undefined), [wallKey]);
  const [followsRail, categoryRails] = rails[0]?.key === 'follows' ? [rails[0], rails.slice(1)] : [null, rails];
  if (expanded) {
    body = <StillRail {...railProps} eager endedKeys={expanded.ended} expanded heading={expanded.label} items={expanded.entries} liveCount={expanded.liveCount} loadPage={loadWall} railKey={expanded.key} />;
  } else if (hasContent) {
    body = (
      <>
        {view.hero ? <LiveHero action={actionFor(view.hero)} follow={view.heroFromFollows ? follow : null} fromFollows={view.heroFromFollows} item={view.hero} onOpen={onOpen} onSettings={settings && follow ? () => setSettingsFor(follow.id) : undefined} watchRef={watchRef} /> : null}
        {followsRail ? <StillRail {...railProps} eager endedKeys={followsRail.ended} heading={followsRail.label} items={followsRail.entries} kicker={`${followsRail.entries.length} live`} railKey={followsRail.key} /> : null}
        {view.upcoming.length ? <Schedule actionFor={actionFor} entries={view.upcoming} onOpen={onOpen} /> : null}
        {categoryRails.map((list, index) => <StillRail {...railProps} eager={index === 0 && !view.hero && !followsRail} endedKeys={list.ended} heading={list.label} items={list.entries} key={list.key} liveCount={list.liveCount} railKey={list.key} />)}
        {view.ended.length ? <StillRail {...railProps} actionsFor={undefined} heading="Recently ended" items={view.ended} max={12} railKey="ended" /> : null}
      </>
    );
  } else if (provider !== 'all' && failedChecks.has(provider)) {
    const tried = clockTime(failedChecks.get(provider));
    body = <div className="g-empty-result"><p className="g-live-empty-title">{SOURCE_LABELS[provider]} could not be checked</p>{tried ? <p className="g-label">Last tried {tried}</p> : null}</div>;
  } else if (error || snapshot?.state === 'failed') {
    body = (
      <div className="g-inline-error" role="alert">
        <p className="g-live-empty-title">Live is taking a pause.</p>
        <p className="g-label">{error || snapshot?.error || 'Live discovery is temporarily unavailable.'}</p>
        {onRetry ? <button className="g-button g-button-text" data-focus-item onClick={onRetry} type="button">Try again</button> : null}
      </div>
    );
  } else if (snapshot && snapshot.state !== 'loading') {
    body = <div className="g-empty-result"><p className="g-live-empty-title">Nobody is live right now.</p><p>New streams appear as Lumina checks again, every few minutes.</p></div>;
  } else {
    body = (
      <div aria-busy="true" aria-label="Loading live streams" className="g-live-loading" role="status">
        <span className="g-live-hero-slot" />
        {[0, 1].map((row) => <div className="g-rail-slots" key={row}>{[0, 1, 2, 3].map((slot) => <span className="g-still-slot" key={slot} />)}</div>)}
      </div>
    );
  }

  return (
    <div className={embedded ? 'g-live-surface' : 'surface gallery g-live-surface'} onBlur={onFocusOut} onFocus={onFocusIn} onKeyDown={onKeyDown}>
      {embedded ? null : (
        <header className="g-masthead">
          <h1 tabIndex={-1}>Live</h1>
          {snapshot ? <p aria-live="polite" className="g-label g-kicker">{liveKicker(snapshot, view, phone)}</p> : null}
        </header>
      )}
      <div className={embedded ? undefined : 'g-toolbar'} style={embedded ? { textAlign: 'right' } : undefined}>
        {view.providers.length > 1 && !embedded ? (
          <div aria-label="Provider" className="g-chips" role="group">
            {(['all', ...view.providers] as const).map((key) => <button aria-pressed={provider === key} className="g-chip" data-focus-item key={key} onClick={() => setProvider(key)} type="button">{key === 'all' ? 'All' : SOURCE_LABELS[key]}</button>)}
          </div>
        ) : null}
        {embedded && liveTotal ? <span className="g-label g-live-total">{liveTotal}</span> : null}
        <a className="g-text-button g-live-recordings" data-focus-item href="/library/recordings" onClick={followAppLink}>Recordings →</a>
      </div>
      {notices.length ? <div className="g-notices" role="status">{notices.map((line) => <p className="g-label" key={line}><CircleAlert aria-hidden="true" /> {line}</p>)}</div> : null}
      {record.error ? <p className="g-live-error" role="alert">{record.error}</p> : null}
      <p hidden id={BOUNDARY_ID}>{RECORD_BOUNDARY}</p>
      {body}
      {settings && settingsFollow ? <FollowSettingsDialog {...settings} automation={settingsFollow} onClose={() => setSettingsFor(null)} /> : null}
    </div>
  );
}

export const LiveSurface = memo(LiveSurfaceView);
