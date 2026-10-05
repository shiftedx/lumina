/**
 * Playback start warm-up.
 *
 * - `prefetchPlaybackOptions` fetches an item's playback decision into a 60 s cache (the request also puts its
 *   probe first on the server). The player takes it once.
 * - `speculativeStart` starts the conversion session early when the decision needs one and the server has a free
 *   slot. It starts at the resume point with no request body, so the player's own start gets the same session
 *   back from the server. Never on a phone or with reduced data; stopped when the page closes without Play, or
 *   30 s after start (or after Play) unless the player adopted it. One early session at a time.
 * - The server keeps one conversion per member device ("web" for every tab), so an early start would replace a
 *   conversion already playing. While a player holds one (`holdConversion`: this tab's mini player, or another
 *   tab via a shared Web Lock or, on plain http where Web Locks do not exist, a localStorage heartbeat), nothing
 *   starts early. When neither can see other tabs, nothing starts early either.
 */
import { getLocalPlaybackOptions, getPlaybackProgress, startLocalPlaybackSession, stopLocalPlaybackSession } from './api';
import { startPoint } from './playbackModel';
import type { LocalPlaybackOptions, LocalPlaybackSession } from './types';

export const OPTIONS_TTL_MS = 60_000;
export const SPECULATIVE_TTL_MS = 30_000;
export const CONVERSION_LOCK = 'lumina-playing-conversion';
export const HOLD_KEY = 'lumina.conversionHold.';
export const MAX_DECISIONS = 50;
export const FORGET_WAIT_MS = 1_000;
export const HOLD_FRESH_MS = 90_000; // a hidden tab's timers may fire only once a minute
const HOLD_BEAT_MS = 5_000;
const PHONE = '(max-width: 599px), (pointer: coarse)';

type Cached = { at: number; promise: Promise<LocalPlaybackOptions>; data: LocalPlaybackOptions | null };
// `dead`: stopped, cancelled or adopted, so a request not yet sent must never go out.
type Early = { itemId: string; promise: Promise<LocalPlaybackSession | null>; claimed: boolean; dead: boolean; timer: ReturnType<typeof setTimeout> | null };

const decisions = new Map<string, Cached>();
let early: Early | null = null;
let conversionsHere = 0;
const tabHold = `${HOLD_KEY}${Math.random().toString(36).slice(2)}`;
let heartbeat: ReturnType<typeof setInterval> | null = null;

function beat(): void {
  try { window.localStorage.setItem(tabHold, String(Date.now())); } catch { /* the lock or the fail-closed check covers it */ }
}

function startBeat(): void {
  beat();
  heartbeat = setInterval(beat, HOLD_BEAT_MS);
  window.addEventListener('pagehide', unbeat, { once: true });
}

function unbeat(): void {
  if (heartbeat) clearInterval(heartbeat);
  heartbeat = null;
  try { window.localStorage.removeItem(tabHold); } catch { /* nothing was written */ }
}

// pagehide dropped the heartbeat; a page restored from the back-forward cache still holds its conversion.
if (typeof window !== 'undefined') {
  window.addEventListener('pageshow', (event) => {
    if ((event as PageTransitionEvent).persisted && conversionsHere > 0 && !heartbeat) startBeat();
  });
}

function fresh(itemId: string): Cached | null {
  const hit = decisions.get(itemId);
  return hit && Date.now() - hit.at < OPTIONS_TTL_MS ? hit : null;
}

/** Title page open: prefetch the primary action's playback options (also prioritises its probe). */
export function prefetchPlaybackOptions(itemId: string): void {
  if (fresh(itemId)) return;
  const entry: Cached = { at: Date.now(), promise: getLocalPlaybackOptions(itemId), data: null };
  entry.promise.then((data) => { entry.data = data; }, () => { if (decisions.get(itemId) === entry) decisions.delete(itemId); });
  for (const [key, value] of decisions) if (entry.at - value.at >= OPTIONS_TTL_MS) decisions.delete(key);
  decisions.delete(itemId);
  decisions.set(itemId, entry);
  if (decisions.size > MAX_DECISIONS) decisions.delete(decisions.keys().next().value as string);
}

/** The prefetched decision for the player, once; null when there is none or it is older than 60 s. */
export function takePrefetchedOptions(itemId: string): Promise<LocalPlaybackOptions> | null {
  const hit = fresh(itemId);
  decisions.delete(itemId);
  return hit?.promise ?? null;
}

/** The player is playing a conversion; call the returned function when it stops. Blocks early starts in every tab. */
export function holdConversion(): () => void {
  conversionsHere += 1;
  if (!heartbeat) startBeat();
  let release = (): void => undefined;
  if ('locks' in navigator) {
    const done = new Promise<void>((resolve) => { release = resolve; });
    void navigator.locks.request(CONVERSION_LOCK, { mode: 'shared' }, () => done).catch(() => undefined);
  }
  let held = true;
  return () => {
    if (!held) return;
    held = false;
    conversionsHere -= 1;
    if (conversionsHere === 0) unbeat();
    release();
  };
}

/** Another tab's fresh heartbeat; null when localStorage cannot be read. Stale ones (a crashed tab) are swept. */
function heldElsewhere(): boolean | null {
  try {
    const storage = window.localStorage;
    const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index)).filter((key): key is string => key?.startsWith(HOLD_KEY) ?? false);
    const stale = keys.filter((key) => !(Date.now() - Number(storage.getItem(key)) < HOLD_FRESH_MS));
    for (const key of stale) storage.removeItem(key);
    return keys.length > stale.length;
  } catch {
    return null;
  }
}

async function conversionPlaying(): Promise<boolean> {
  if (conversionsHere > 0) return true;
  const stored = heldElsewhere();
  if (stored) return true;
  if (!('locks' in navigator)) return stored === null; // plain http: fail closed when localStorage is unusable too
  const state = await navigator.locks.query().catch(() => null);
  if (!state) return stored === null;
  return [...(state.held ?? []), ...(state.pending ?? [])].some((lock) => lock.name === CONVERSION_LOCK);
}

function mayStartEarly(): boolean {
  const phone = typeof window.matchMedia === 'function' && window.matchMedia(PHONE).matches;
  const saveData = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData === true;
  return !phone && !saveData && conversionsHere === 0;
}

/** Settles once the stop request (if any) has been sent and answered. */
function stop(entry: Early | null): Promise<void> {
  if (!entry) return Promise.resolve();
  entry.dead = true;
  if (entry.timer) clearTimeout(entry.timer);
  if (early === entry) early = null;
  return entry.promise.then((session) => (session ? stopLocalPlaybackSession(session.session_id).catch(() => undefined) : undefined));
}

/** 400 ms of hover or focus on the primary action: maybe start the conversion session early. */
export function speculativeStart(itemId: string): void {
  const decision = fresh(itemId)?.data;
  if (early?.itemId === itemId || !decision || !mayStartEarly()) return;
  // free_video_slots counts video transcodes only, so it gates only a transcode; a remux never waits on it.
  // The options cannot tell an audio-only transcode apart, so every transcode is gated (fewer early starts, never more).
  const converts = decision.mode === 'remux' || (decision.mode === 'transcode' && (decision.free_video_slots ?? 0) > 0);
  if (!converts) return;
  stop(early);
  const entry: Early = { itemId, claimed: false, dead: false, timer: null, promise: Promise.resolve(null) };
  // A session that arrives after the entry died is stopped (or kept, when adopted) by stop's and adopt's own handlers.
  entry.promise = conversionPlaying()
    .then(async (playing) => {
      if (playing) { stop(entry); return null; } // the retry after it ends may start early again
      const progress = await getPlaybackProgress(itemId).catch(() => null);
      if (entry.dead || conversionsHere > 0) return null; // the player took its hold (Play) while this was pending
      // No body: it must equal the player's request. If a remembered subtitle or quality default is ever sent by
      // the player on its first start, this must predict that same body, or the early session is never reused.
      return startLocalPlaybackSession(itemId, startPoint(progress && !progress.completed ? progress.position_seconds : 0, decision.facts?.duration));
    })
    .catch(() => null);
  entry.timer = setTimeout(() => stop(entry), SPECULATIVE_TTL_MS);
  early = entry;
}

/**
 * Pointer or focus left the primary action, or the page closed. Ignored after Play claimed the early session.
 * With no item (Play at a time): an unclaimed early session for any item stops.
 */
export function cancelSpeculativeStart(itemId?: string): void {
  if (early && !early.claimed && (itemId === undefined || early.itemId === itemId)) stop(early);
}

/** Play pressed (the app's openRoute): keep the early session for the player, which has 30 s to adopt it. */
export function claimSpeculativeStart(itemId: string): void {
  const entry = early;
  if (!entry) return;
  if (entry.itemId !== itemId) { stop(entry); return; }
  entry.claimed = true;
  if (entry.timer) clearTimeout(entry.timer);
  entry.timer = setTimeout(() => stop(entry), SPECULATIVE_TTL_MS);
}

/** The player has its session: an early one with the same id is now the player's; any other early one stops. */
export function adoptSpeculativeStart(itemId: string, sessionId: string): void {
  const entry = early;
  if (!entry) return;
  entry.dead = true;
  if (entry.timer) clearTimeout(entry.timer);
  early = null;
  void entry.promise.then((session) => {
    if (session && (entry.itemId !== itemId || session.session_id !== sessionId)) void stopLocalPlaybackSession(session.session_id).catch(() => undefined);
  });
}

/**
 * Sign-out or member switch: the early session stops, claimed or not, and no decision carries over. Sign-out awaits
 * it so the stop goes out while the session cookie still works, but never longer than FORGET_WAIT_MS.
 */
export function forgetPlaybackWarmup(): Promise<void> {
  const stopped = stop(early);
  decisions.clear();
  return Promise.race([stopped, new Promise<void>((resolve) => { setTimeout(resolve, FORGET_WAIT_MS); })]);
}
