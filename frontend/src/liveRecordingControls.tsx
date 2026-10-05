import { useCallback, useEffect, useRef, useState } from 'react';
import { CalendarClock, CircleStop, Clock, History, LoaderCircle, Radio, Video, X } from 'lucide-react';

import { cancelLiveRecording, createLiveRecording, getLiveRecording, keepLiveRecording, listLiveRecordings, stopLiveRecording } from './api';
import {
  canRecordFromNow,
  forwardOnlyRecordingBoundary,
  canScheduleUpcoming,
  isFromStartAvailable,
  isLiveRecordingTerminal,
  summarizeLiveRecording,
} from './liveRecording';
import { capabilityActionMessage } from './sourceCapabilities';
import type { LiveRecordingFallbackPolicy, LiveRecordingStartIntent, LiveRecording, MediaSourceCapabilities } from './types';

/**
 * "Record from now" for a currently-live source AND "Schedule recording" for an
 * upcoming broadcast (issue #98), plus a panel that renders the durable
 * recording's TWO sibling outputs (media + chat) and their partial outcomes
 * HONESTLY. From-start is expressed as member INTENT (record from the beginning
 * if available / record from the live edge / wait and choose), never a raw
 * yt-dlp flag, and an incomplete beginning is surfaced as an explicit
 * partial-history condition rather than a false "complete recording".
 */

const POLL_INTERVAL_MS = 3000;

interface LiveRecordingControlsProps {
  sourceUrl: string;
  capabilities?: MediaSourceCapabilities | null;
}

export function LiveRecordingControls({ sourceUrl, capabilities }: LiveRecordingControlsProps) {
  const canRecord = canRecordFromNow(capabilities);
  const canSchedule = canScheduleUpcoming(capabilities);
  const fromStartAvailable = isFromStartAvailable(capabilities);
  const [recording, setRecording] = useState<LiveRecording | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // From-start member intent (issue #98). Defaults to the safe live-edge behavior;
  // the member opts into the best-effort from-start capture deliberately.
  const [startIntent, setStartIntent] = useState<LiveRecordingStartIntent>('live_edge');
  const [requireChoice, setRequireChoice] = useState(false);
  // The effective from-start intent drives the honest MEDIA claim in the
  // forward-only boundary (chat stays forward-only regardless); it updates live
  // as the intent radio toggles because startIntent is component state.
  const fromStartSelected = fromStartAvailable && startIntent === 'from_start';
  const recordingRef = useRef<LiveRecording | null>(null);
  recordingRef.current = recording;

  // On the current source, adopt an existing recording (e.g. after a reload or a
  // restart-recovered recording or waiter) so the member sees its state.
  useEffect(() => {
    let cancelled = false;
    setRecording(null);
    setError(null);
    setBusy(false);
    setStartIntent('live_edge');
    setRequireChoice(false);
    // The list is paginated (issue #111): walk the keyset cursor until this
    // source's most recent recording is found (pages are newest-first, so the
    // first match wins) or the pages are exhausted — never silently truncate
    // to the first page.
    (async () => {
      let cursor: string | null = null;
      do {
        const page = await listLiveRecordings(cursor ? { cursor } : {});
        if (cancelled) return;
        const match = page.items.find((item) => item.source_url === sourceUrl);
        if (match) {
          setRecording(match);
          return;
        }
        cursor = page.next_cursor;
      } while (cursor);
    })().catch(() => {
      // Listing failure never breaks Watch; the record action stays available.
    });
    return () => {
      cancelled = true;
    };
  }, [sourceUrl]);

  // Follow a non-terminal recording (including the waiting phase) forward until it
  // reaches a terminal outcome.
  const recordingId = recording?.id ?? null;
  const recordingStatus = recording?.status ?? null;
  useEffect(() => {
    if (!recordingId || !recordingStatus || isLiveRecordingTerminal(recordingStatus)) return;
    let cancelled = false;
    const timer = setInterval(() => {
      getLiveRecording(recordingId)
        .then((next) => {
          if (!cancelled) setRecording(next);
        })
        .catch(() => {
          // A transient poll failure never stops the recording nor breaks Watch.
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [recordingId, recordingStatus]);

  const submit = useCallback(
    async (intent: LiveRecordingStartIntent, fallback: LiveRecordingFallbackPolicy) => {
      setBusy(true);
      setError(null);
      try {
        setRecording(
          await createLiveRecording({
            source_url: sourceUrl,
            start_intent: intent,
            fallback_policy: fallback,
          }),
        );
      } catch {
        setError(canSchedule ? 'Lumina could not schedule this broadcast.' : 'Lumina could not start recording this source.');
      } finally {
        setBusy(false);
      }
    },
    [sourceUrl, canSchedule],
  );

  const onRecord = useCallback(() => {
    const fallback: LiveRecordingFallbackPolicy =
      startIntent === 'from_start' && requireChoice ? 'require_choice' : 'allow_live_edge';
    return submit(startIntent, fallback);
  }, [submit, startIntent, requireChoice]);

  // When from-start was unavailable and the member chose to wait, they can resolve
  // it by deliberately recording from the edge (a fresh recording — the prior one
  // is terminal, so idempotency allows it).
  const onRecordFromEdge = useCallback(() => submit('live_edge', 'allow_live_edge'), [submit]);

  const onStop = useCallback(async () => {
    const current = recordingRef.current;
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      setRecording(await stopLiveRecording(current.id));
    } catch {
      setError('Could not stop the recording.');
    } finally {
      setBusy(false);
    }
  }, []);

  const onKeep = useCallback(async (kept: boolean) => {
    const current = recordingRef.current;
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      setRecording(await keepLiveRecording(current.id, kept));
    } catch {
      setError('Could not change whether this recording is kept.');
    } finally {
      setBusy(false);
    }
  }, []);

  const onCancel = useCallback(async () => {
    const current = recordingRef.current;
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      setRecording(await cancelLiveRecording(current.id));
    } catch {
      setError('Could not cancel the recording.');
    } finally {
      setBusy(false);
    }
  }, []);

  // A live or upcoming source Lumina cannot record/schedule still shows a DISABLED
  // affordance with its stable reason; only an inapplicable lifecycle is hidden.
  const isUpcoming = capabilities?.lifecycle === 'upcoming';
  const disabledReason = isUpcoming
    ? capabilityActionMessage(capabilities?.schedule_reason ?? 'upcoming_schedule_not_supported', 'schedule')
    : capabilityActionMessage(capabilities?.record_reason, 'record');
  const canAct = canRecord || canSchedule;
  const showDisabledAffordance = !canAct && (capabilities?.lifecycle === 'live' || isUpcoming);
  if (!recording && !canAct && !showDisabledAffordance) return null;

  const actionLabel = canSchedule ? 'Schedule recording' : 'Record from now';
  const ActionIcon = canSchedule ? CalendarClock : Video;

  return (
    <section
      aria-label="Live recording"
      className="live-recording-controls"
      data-recording-status={recording?.status ?? 'idle'}
    >
      {recording ? (
        <LiveRecordingPanel busy={busy} onCancel={onCancel} onKeep={onKeep} onStop={onStop} onRecordFromEdge={onRecordFromEdge} recording={recording} />
      ) : (
        <div className="live-recording-start">
          <div className="live-recording-start-copy">
            <h2>
              {canSchedule ? <CalendarClock aria-hidden /> : <Radio aria-hidden />} {canSchedule ? 'Schedule this broadcast' : 'Record from now'}
            </h2>
            {canAct ? (
              canSchedule ? (
                <p>Lumina will wait for this upcoming broadcast and record it — together with its chat — when it goes live.</p>
              ) : (
                <>
                  <p>Save this live broadcast to your library from the moment Lumina connects, together with its chat.</p>
                  {/* The forward-only boundary is presented BEFORE recording starts
                      and is INTENT-AWARE. Chat is always forward-only (captured only
                      from connect); the media claim is honest about from-start, which
                      is reachable on a live source too — when the member selects the
                      best-effort from-start intent it reaches back to the broadcast
                      beginning. The note updates live as the intent radio toggles. */}
                  <p className="live-recording-forward-only" role="note">
                    {forwardOnlyRecordingBoundary(capabilities, { fromStartSelected })}
                  </p>
                </>
              )
            ) : null}
          </div>
          {canAct && fromStartAvailable ? (
            <StartIntentChooser
              startIntent={startIntent}
              requireChoice={requireChoice}
              onIntentChange={setStartIntent}
              onRequireChoiceChange={setRequireChoice}
            />
          ) : null}
          <button
            aria-describedby={!canAct && disabledReason ? 'live-recording-unavailable' : undefined}
            className="g-button is-primary"
            disabled={!canAct || busy}
            onClick={() => void onRecord()}
            type="button"
          >
            {busy ? <LoaderCircle aria-hidden className="spin" /> : <ActionIcon aria-hidden />} {actionLabel}
          </button>
          {!canAct && disabledReason ? (
            <p className="capability-note" id="live-recording-unavailable" role="status">
              {disabledReason}
            </p>
          ) : null}
        </div>
      )}
      {error ? (
        <p className="live-recording-error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

/**
 * The from-start intent chooser (issue #98): expose product INTENT, never raw
 * yt-dlp flags. A native radio group + checkbox so it is keyboard- and
 * screen-reader-navigable; the fieldset legend names the choice.
 */
function StartIntentChooser({
  startIntent,
  requireChoice,
  onIntentChange,
  onRequireChoiceChange,
}: {
  startIntent: LiveRecordingStartIntent;
  requireChoice: boolean;
  onIntentChange: (intent: LiveRecordingStartIntent) => void;
  onRequireChoiceChange: (value: boolean) => void;
}) {
  return (
    <fieldset className="live-recording-intent">
      <legend>Where should recording start?</legend>
      <label className="g-check">
        <input
          type="radio"
          name="live-recording-start-intent"
          checked={startIntent === 'from_start'}
          onChange={() => onIntentChange('from_start')}
        />
        <span>
          Record from the beginning if available <em>(experimental)</em>
        </span>
      </label>
      <label className="g-check">
        <input
          type="radio"
          name="live-recording-start-intent"
          checked={startIntent === 'live_edge'}
          onChange={() => onIntentChange('live_edge')}
        />
        <span>Record from the live edge</span>
      </label>
      {startIntent === 'from_start' ? (
        <label className="g-check live-recording-intent-fallback">
          <input
            type="checkbox"
            checked={requireChoice}
            onChange={(event) => onRequireChoiceChange(event.target.checked)}
          />
          <span>If the beginning isn’t available, don’t record from the edge — let me choose.</span>
        </label>
      ) : null}
    </fieldset>
  );
}

function LiveRecordingPanel({
  recording,
  busy,
  onStop,
  onCancel,
  onRecordFromEdge,
  onKeep,
}: {
  recording: LiveRecording;
  busy: boolean;
  onStop: () => void;
  onCancel: () => void;
  onRecordFromEdge: () => void;
  onKeep: (kept: boolean) => void;
}) {
  const summary = summarizeLiveRecording(recording);
  const active = !isLiveRecordingTerminal(recording.status);
  const waiting = recording.status === 'waiting';
  return (
    <div className={`live-recording-panel tone-${summary.tone}`}>
      <header className="live-recording-header">
        <h2>
          <Radio aria-hidden /> Live recording
        </h2>
        <span className="live-recording-status-badge" data-motion="none" data-status={recording.status}>
          {summary.headline}
        </span>
      </header>
      {summary.waitingNote ? (
        <p className="live-recording-waiting" role="status" data-motion="none">
          {waiting ? <Clock aria-hidden /> : null}
          {summary.waitingNote}
        </p>
      ) : null}
      {/* Both sibling outputs are always shown so a partial outcome is honest and
          never collapses to a single done/failed line. */}
      <dl className="live-recording-outputs">
        <div className={`live-recording-output media-${recording.media?.status ?? 'unknown'}`}>
          <dt>Video</dt>
          <dd aria-live="polite">{summary.mediaSummary}</dd>
        </div>
        <div className={`live-recording-output chat-${recording.chat?.status ?? 'unknown'}`}>
          <dt>Chat</dt>
          <dd aria-live="polite">{summary.chatSummary}</dd>
        </div>
      </dl>
      {summary.originSummary ? (
        // role="status" so where capture began is announced when it resolves,
        // matching the sibling waitingNote live region.
        <p className="live-recording-origin" data-motion="none" role="status">
          {summary.originSummary}
        </p>
      ) : null}
      {summary.historyNote ? (
        // The explicit partial-history disclosure: from-start is best-effort and
        // never silently claims complete history. role="status" (a live region,
        // unlike role="note") guarantees this honesty line is announced when it
        // appears, matching the sibling waitingNote.
        <p className="live-recording-history-note" role="status">
          <History aria-hidden /> {summary.historyNote}
        </p>
      ) : null}
      {[summary.endNote, summary.restartNote, summary.limitsNote].filter(Boolean).map((note) => (
        <p className="live-recording-note" data-motion="none" key={note}>
          {note}
        </p>
      ))}
      {!active && summary.libraryItemId ? (
        <label className="live-recording-keep">
          <input checked={recording.kept === true} disabled={busy} onChange={(event) => onKeep(event.target.checked)} type="checkbox" />
          <span>Keep this recording — automatic cleanup never removes it.</span>
        </label>
      ) : null}
      {active ? (
        <div className="live-recording-actions">
          <button className="g-button" disabled={busy} onClick={() => onStop()} type="button">
            <CircleStop aria-hidden /> {waiting ? 'Cancel schedule' : 'Stop & save'}
          </button>
          {!waiting ? (
            <button className="g-button" disabled={busy} onClick={() => onCancel()} type="button">
              <X aria-hidden /> Cancel
            </button>
          ) : null}
        </div>
      ) : null}
      {summary.awaitingFallbackChoice ? (
        <div className="live-recording-fallback-choice">
          <p role="status">Recording from the beginning wasn’t available. You can record from the live edge instead.</p>
          <button className="g-button" disabled={busy} onClick={() => onRecordFromEdge()} type="button">
            <Video aria-hidden /> Record from the live edge
          </button>
        </div>
      ) : null}
    </div>
  );
}
