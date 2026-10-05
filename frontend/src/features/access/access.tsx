/** Member access (2.8.0): what the signed-in member may see and when. Hiding is presentation only; the server enforces. */
import { createContext, useContext, useEffect, useRef, useState } from 'react';

import { getMyAccess } from '../../api';
import { Button, EmptyState } from '../../ui';
import { ACCESS_STOP_EVENT, type AccessStop } from './accessEvents';

export type BlockedStreaming = 'youtube' | 'twitch' | 'kick' | 'live' | 'open_search';
export interface MyAccess {
  sections: string[] | null;
  blocked_streaming: BlockedStreaming[];
  followed_only: boolean;
  allowed_now: boolean;
  until: string | null;
  remaining_minutes: number | null;
  /** False hides every save, download, acquire and record action; absent means allowed. */
  can_download?: boolean;
}
export interface AccessValue {
  access: MyAccess | null;
  /** Set by a 403 screen_time_up / outside_hours (start or mid-play); cleared once a refresh says watching is allowed. */
  stop: 'screen_time_up' | 'outside_hours' | null;
  refresh: () => void;
}

const REFRESH_MS = 3 * 60_000;
export const LOW_MINUTES = 5;
const AccessContext = createContext<AccessValue>({ access: null, stop: null, refresh: () => undefined });
export const AccessProvider = AccessContext.Provider;
export const useAccess = () => useContext(AccessContext);

export const useCanDownload = () => useAccess().access?.can_download !== false;
export const providerBlocked = (access: MyAccess | null, kind: BlockedStreaming) => Boolean(access?.blocked_streaming.includes(kind));
export const allStreamingBlocked = (access: MyAccess | null) => (['youtube', 'twitch', 'kick'] as const).every((kind) => providerBlocked(access, kind));
/** The reason watching is stopped right now, or null. */
export function watchGate(value: AccessValue): 'screen_time_up' | 'outside_hours' | null {
  const { access, stop } = value;
  if (access && !access.allowed_now) return 'outside_hours';
  if (access?.remaining_minutes === 0) return 'screen_time_up';
  return stop;
}

const stringList = (value: unknown) => (Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : null);
/** Any 2xx body becomes a well-formed MyAccess; anything missing or malformed reads as unrestricted (the server is the gate). */
export function normalizeAccess(raw: unknown): MyAccess {
  const body = (raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}) as Record<string, unknown>;
  return {
    sections: stringList(body.sections),
    blocked_streaming: (stringList(body.blocked_streaming) ?? []) as BlockedStreaming[],
    followed_only: body.followed_only === true,
    allowed_now: body.allowed_now !== false,
    until: typeof body.until === 'string' ? body.until : null,
    remaining_minutes: typeof body.remaining_minutes === 'number' && Number.isFinite(body.remaining_minutes) ? body.remaining_minutes : null,
    can_download: body.can_download !== false,
  };
}

/** Loads /api/me/access at sign-in, every few minutes, and whenever the server reports an access code. Fails open: the server is the gate. */
export const DOWNLOADS_NOT_ALLOWED = "Saving to the vault isn't available on this account";

export function useMyAccess(userId: string | undefined, onNotice?: (message: string) => void): AccessValue {
  const [access, setAccess] = useState<MyAccess | null>(null);
  const [stop, setStop] = useState<AccessValue['stop']>(null);
  const [tick, setTick] = useState(0);
  const refresh = useRef(() => setTick((value) => value + 1)).current;
  useEffect(() => {
    if (!userId) { setAccess(null); setStop(null); return undefined; }
    let live = true;
    getMyAccess().then((raw) => {
      if (!live) return;
      const next = normalizeAccess(raw);
      setAccess(next);
      if (next.allowed_now && (next.remaining_minutes === null || next.remaining_minutes > 0)) setStop(null);
    }, () => undefined);
    return () => { live = false; };
  }, [userId, tick]);
  useEffect(() => {
    if (!userId) return undefined;
    const timer = window.setInterval(refresh, REFRESH_MS);
    const onStop = (event: Event) => {
      const code = (event as CustomEvent<AccessStop>).detail;
      if (code === 'screen_time_up' || code === 'outside_hours') setStop(code);
      else if (code === 'downloads_not_allowed') { onNotice?.(DOWNLOADS_NOT_ALLOWED); setAccess((current) => current && { ...current, can_download: false }); } // playback goes on; the save actions disappear
      refresh();
    };
    window.addEventListener(ACCESS_STOP_EVENT, onStop);
    return () => { window.clearInterval(timer); window.removeEventListener(ACCESS_STOP_EVENT, onStop); };
  }, [userId, refresh, onNotice]);
  return { access, stop, refresh };
}

const clock = (until: string | null) => {
  const date = until ? new Date(until) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : null;
};

/** A calm, full-page state: focus lands here so a screen reader hears it; no admin detail. */
export function AccessState({ title, body, onHome }: { title: string; body?: string; onHome?: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <div className="surface g-access-state" ref={ref} role="status" tabIndex={-1}>
      <EmptyState action={onHome ? <Button onClick={onHome} variant="quiet">Back to Home</Button> : undefined} body={body} size="page" title={title} />
    </div>
  );
}

export function WatchStopState({ gate, until, onHome }: { gate: 'screen_time_up' | 'outside_hours'; until: string | null; onHome: () => void }) {
  if (gate === 'screen_time_up') return <AccessState body="Back tomorrow." onHome={onHome} title="That's today's watching time" />;
  const at = clock(until);
  return <AccessState onHome={onHome} title={at ? `Outside your viewing hours — back at ${at}` : 'Outside your viewing hours'} />;
}

/** A quiet note over the player when little time is left; announced once per minute change. */
export function TimeLeftNotice({ minutes }: { minutes: number | null }) {
  if (minutes === null || minutes <= 0 || minutes > LOW_MINUTES) return null;
  return <p className="g-access-notice" role="status">{minutes === 1 ? '1 minute' : `${minutes} minutes`} of watching left today</p>;
}

export function BlockedSurface({ what }: { what: string }) {
  return <AccessState title={`${what} isn't available right now`} />;
}
