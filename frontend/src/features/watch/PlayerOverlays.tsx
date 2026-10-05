import { useEffect, useRef, useState } from 'react';
import { SkipForward, X } from 'lucide-react';

import { Artwork } from '../../Artwork';
import { Button, IconButton, TextButton } from '../../ui';

import type { AutoSkipPref, MediaSegment, RecapResponse } from '../../types';
import { episodeCode } from '../titles/titleModel';
import { activeSegment, upNextAt } from './playerSegments';

export const UP_NEXT_COUNTDOWN_SECONDS = 10;
export const SKIP_NOTICE_MS = 3000;
export const RECAP_WINDOW_SECONDS = 90;
/** What plays next: `autoplay` false (a collection's next film) offers it but never starts it on its own. */
export type UpNext = { label: string; action: string; still?: string | null; autoplay: boolean; onPlay: () => void };
type Skippable = keyof AutoSkipPref;

const segmentKey = (segment: MediaSegment) => `${segment.type}:${segment.start_seconds}`;

/** Focus a control that just appeared, unless the member is busy elsewhere on the page (notes, search). */
function focusIfIdle(button: HTMLButtonElement | null) {
  if (!button) return;
  const active = document.activeElement;
  const player = button.closest('[data-lumina-player]');
  if (!active || active === document.body || (player?.contains(active) ?? true)) button.focus({ preventScroll: true });
}

export function PlayerOverlays({ currentTime, duration, segments, autoSkip, upNext, autoplay, recap, onSeek, onCancelUpNext }: {
  currentTime: number;
  duration: number | null;
  segments: MediaSegment[];
  autoSkip: AutoSkipPref;
  upNext: UpNext | null;
  autoplay: boolean;
  recap: RecapResponse | null;
  onSeek: (seconds: number) => void;
  onCancelUpNext: () => void;
}) {
  // Each segment is auto-skipped at most once per play: Undo or a seek back plays it (no skip loop).
  const skippedRef = useRef(new Set<string>());
  const playedNextRef = useRef(false);
  const skipRef = useRef<HTMLButtonElement>(null);
  const playNowRef = useRef<HTMLButtonElement>(null);
  const [notice, setNotice] = useState<{ type: Skippable; from: number } | null>(null);
  const [upNextDismissed, setUpNextDismissed] = useState(false);
  const [recapDismissed, setRecapDismissed] = useState(false);
  const [countdown, setCountdown] = useState<number | null>(null);

  const active = activeSegment(currentTime, segments);
  const activeKey = active ? segmentKey(active) : null;
  const creditsBelongToUpNext = active?.type === 'credits' && upNext !== null;
  const upNextStart = upNext ? upNextAt(duration, segments) : null;
  const inUpNext = upNext !== null && upNextStart !== null && currentTime >= upNextStart;
  const showUpNext = inUpNext && !upNextDismissed;
  const counting = showUpNext && autoplay && Boolean(upNext?.autoplay);
  const showSkip = active !== null && !creditsBelongToUpNext && notice === null;
  const showRecap = !recapDismissed && Boolean(recap?.suggest_preroll) && currentTime < RECAP_WINDOW_SECONDS && Boolean(recap?.points.length || recap?.fallback.length);

  useEffect(() => {
    if (!active || !activeKey || creditsBelongToUpNext || skippedRef.current.has(activeKey)) return;
    if (!autoSkip[active.type as Skippable]) return;
    skippedRef.current.add(activeKey);
    setNotice({ type: active.type as Skippable, from: currentTime });
    onSeek(active.end_seconds);
  }, [activeKey]); // eslint-disable-line react-hooks/exhaustive-deps -- act once per segment entry

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(null), SKIP_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!showUpNext) { setUpNextDismissed(false); playedNextRef.current = false; }
  }, [upNextStart !== null && currentTime < upNextStart]); // eslint-disable-line react-hooks/exhaustive-deps -- re-arm after a seek back before the card

  useEffect(() => {
    if (!counting) { setCountdown(null); return undefined; }
    setCountdown(UP_NEXT_COUNTDOWN_SECONDS);
    const timer = window.setInterval(() => setCountdown((value) => (value === null ? null : Math.max(0, value - 1))), 1000);
    return () => window.clearInterval(timer);
  }, [counting]);

  useEffect(() => {
    if (countdown !== 0 || playedNextRef.current || !upNext) return;
    playedNextRef.current = true;
    upNext.onPlay();
  }, [countdown]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (showSkip) focusIfIdle(skipRef.current); }, [activeKey, showSkip]);
  useEffect(() => { if (showUpNext) focusIfIdle(playNowRef.current); }, [showUpNext]);

  function skip() {
    if (!active || !activeKey) return;
    skippedRef.current.add(activeKey);
    onSeek(active.end_seconds);
  }
  function undo() {
    if (!notice) return;
    onSeek(notice.from);
    setNotice(null);
  }
  function cancel() {
    setUpNextDismissed(true);
    setCountdown(null);
    onCancelUpNext();
  }

  return (
    <>
      {showRecap && recap ? (
        <section aria-label="Previously on" className="player-card">
          <p className="g-label">Previously on</p>
          <ul>
            {recap.points.length
              ? recap.points.slice(0, 3).map((point, index) => <li key={index}>{point.text}</li>)
              : recap.fallback.slice(0, 2).map((entry) => <li key={entry.episode_id}><strong>{[episodeCode(entry), entry.name].filter(Boolean).join(' · ')}</strong>{entry.overview ? ` ${entry.overview}` : ''}</li>)}
          </ul>
          <IconButton data-focus-item icon={<X />} label="Dismiss recap" onClick={() => setRecapDismissed(true)} variant="overlay" />
        </section>
      ) : null}
      {notice ? <div className="player-card player-notice-card" role="status">Skipped {notice.type} · <TextButton data-focus-item onClick={undo}>Undo</TextButton></div> : null}
      {showSkip && active ? <Button className="player-skip" data-focus-item icon={<SkipForward />} onClick={skip} ref={skipRef} variant="secondary">Skip {active.type}</Button> : null}
      {inUpNext && upNextDismissed && upNext ? <Button className="player-skip" data-focus-item icon={<SkipForward />} onClick={() => { playedNextRef.current = true; upNext.onPlay(); }} variant="secondary">{upNext.action}</Button> : null}
      {showUpNext && upNext ? (
        <section aria-label={upNext.action} className="player-card player-next-card">
          {upNext.still ? <Artwork alt="" className="player-next-still" src={upNext.still} /> : null}
          <p className="player-card-title">Next: {upNext.label}</p>
          {countdown !== null ? <p className="player-card-meta"><span aria-hidden="true">Playing in {countdown} s</span><span className="sr-only">Plays automatically in {UP_NEXT_COUNTDOWN_SECONDS} seconds.</span></p> : null}
          <div className="player-card-actions">
            <Button data-focus-item onClick={() => { playedNextRef.current = true; upNext.onPlay(); }} ref={playNowRef} variant="primary">Play now</Button>
            <Button data-focus-item onClick={cancel} variant="quiet">Cancel</Button>
          </div>
        </section>
      ) : null}
    </>
  );
}
