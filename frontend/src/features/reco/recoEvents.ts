/**
 * Impression and open reporting. Events go into a 500-entry ring buffer in
 * memory only, are posted every 30 s in chunks of 200, and on pagehide through sendBeacon (the CSRF token travels in the
 * body). An event is a kind, the served list's id and the item's opaque key: nothing names a member, title or channel.
 * Like `perfMetrics.ts`, posting is best effort and a failed post drops its events.
 */
import { beaconRecoEvents, sendRecoEvents } from '../../api';
import type { RecoAnnotation, RecoClientEventKind, RecoEventIn } from '../../types';

export const RECO_MAX_EVENTS = 500;
export const RECO_FLUSH_INTERVAL_MS = 30_000;
export const RECO_FLUSH_CHUNK = 200;
export const IMPRESSION_THRESHOLD = 0.5;
export const IMPRESSION_DWELL_MS = 1_000;
/** The server's `age_ms` ceiling: an older event makes it refuse the whole batch. */
const MAX_AGE_MS = 900_000;
/** Impression dedupe is per page view; the set is cleared rather than grown without bound. */
const SEEN_LIMIT = 5_000;

type Pending = { kind: RecoClientEventKind; list_id: string; key: string; at: number };

let pending: Pending[] = [];
const seen = new Set<string>();
let interval: number | null = null;

/** Everything buffered and still fresh, as wire events aged at this moment, in chunks the server accepts. */
function takeChunks(): RecoEventIn[][] {
  const at = Date.now();
  const events = pending
    .filter((event) => at - event.at <= MAX_AGE_MS)
    .map((event): RecoEventIn => ({ kind: event.kind, list_id: event.list_id, key: event.key, age_ms: Math.max(0, Math.round(at - event.at)) }));
  pending = [];
  const chunks: RecoEventIn[][] = [];
  for (let from = 0; from < events.length; from += RECO_FLUSH_CHUNK) chunks.push(events.slice(from, from + RECO_FLUSH_CHUNK));
  return chunks;
}

const onPageHide = () => { for (const chunk of takeChunks()) beaconRecoEvents(chunk); };

function start(): void {
  if (interval !== null || typeof window === 'undefined') return;
  interval = window.setInterval(() => { void flushRecoEvents(); }, RECO_FLUSH_INTERVAL_MS);
  window.addEventListener('pagehide', onPageHide);
}

function enqueue(kind: RecoClientEventKind, reco: RecoAnnotation): void {
  start();
  pending.push({ kind, list_id: reco.list_id, key: reco.key, at: Date.now() });
  if (pending.length > RECO_MAX_EVENTS) pending = pending.slice(-RECO_MAX_EVENTS);
}

/** A card was on screen (half visible for a second). Deduped per (list, item) for the page view. */
export function recordRecoImpression(reco: RecoAnnotation): void {
  const id = `${reco.list_id}:${reco.key}`;
  if (seen.has(id)) return;
  if (seen.size >= SEEN_LIMIT) seen.clear();
  seen.add(id);
  enqueue('impression', reco);
}

/** The card was opened. Every open counts: the server attributes plays to the latest one. */
export function recordRecoOpen(reco: RecoAnnotation): void {
  enqueue('open', reco);
}

/** Post everything buffered now. A failed post drops its events. */
export async function flushRecoEvents(): Promise<void> {
  await Promise.all(takeChunks().map((chunk) => sendRecoEvents(chunk).catch(() => undefined)));
}

/** The signed-in member changed (or signed out): the next one inherits no buffered event and no dedupe. */
export function resetRecoEvents(): void {
  pending = [];
  seen.clear();
  if (interval !== null) {
    window.clearInterval(interval);
    window.removeEventListener('pagehide', onPageHide);
    interval = null;
  }
}
