/**
 * Local real-user metrics. Samples go into a 500-entry ring buffer, are posted
 * every 30 s in chunks of 200, and on pagehide through sendBeacon. A sample is only a metric, a fixed label
 * (built with the helpers below) and a number: nothing names a member, title or URL. Posting is best effort.
 */
import { beaconClientMetrics, sendClientMetrics } from './api';
import type { ClientMetricName, ClientMetricSample } from './types';

export type MetricImageKind = 'poster' | 'backdrop' | 'still' | 'logo' | 'square';
export const imageLoadLabel = (kind: MetricImageKind, cache: 'hit' | 'net'): string => `${kind}:${cache}`;
export type DetailHeroLabel = 'click' | 'deep_link';
export const imageFailedLabel = (kind: MetricImageKind, status: '404' | 'transient' | 'exhausted'): string => `${kind}:${status}`;
export type TtffLabel = 'direct' | 'direct_resume' | 'transcode_hw' | 'transcode_sw' | 'remux';

export const MAX_SAMPLES = 500;
export const FLUSH_INTERVAL_MS = 30_000;
/** 200 samples stay far below the endpoint's 32 KB body cap. */
export const FLUSH_CHUNK = 200;
const MAX_VALUE = 3_600_000;
const PLAY_INTENT_KEEP_MS = 5_000;
const PLAY_INTENT_TTL_MS = 60_000;

let navigationAt = 0;
let playIntentAt: number | null = null;
const buffer: ClientMetricSample[] = [];
let started = false;

function takeChunks(): ClientMetricSample[][] {
  const chunks: ClientMetricSample[][] = [];
  while (buffer.length) chunks.push(buffer.splice(0, FLUSH_CHUNK));
  return chunks;
}

function start(): void {
  if (started || typeof window === 'undefined') return;
  started = true;
  window.setInterval(() => { void flushMetrics(); }, FLUSH_INTERVAL_MS);
  window.addEventListener('pagehide', () => { for (const chunk of takeChunks()) beaconClientMetrics(chunk); });
}

export function recordMetric(metric: ClientMetricName, label: string, value: number): void {
  if (!Number.isFinite(value) || value < 0) return;
  start();
  buffer.push({ metric, label, value: Math.min(Math.round(value), MAX_VALUE) });
  if (buffer.length > MAX_SAMPLES) buffer.splice(0, buffer.length - MAX_SAMPLES);
}

/** Post everything buffered now; a failed post drops its samples. */
export async function flushMetrics(): Promise<void> {
  await Promise.all(takeChunks().map((chunk) => sendClientMetrics(chunk).catch(() => undefined)));
}

/** The app calls this on every route change; wall and title metrics measure from it. */
export function markNavigation(now = performance.now()): void {
  navigationAt = now;
}

export function sinceNavigation(now = performance.now()): number {
  return Math.max(0, now - navigationAt);
}

/** Play was activated: time to first frame starts here. `keepRecent` keeps a mark made in the last 5 s. */
export function notePlayIntent({ keepRecent = false }: { keepRecent?: boolean } = {}, now = performance.now()): void {
  if (keepRecent && playIntentAt !== null && now - playIntentAt < PLAY_INTENT_KEEP_MS) return;
  playIntentAt = now;
}

/** The pending Play time, cleared; null when there is none or it is older than 60 s. */
export function takePlayIntent(now = performance.now()): number | null {
  const at = playIntentAt;
  playIntentAt = null;
  return at !== null && now - at <= PLAY_INTENT_TTL_MS ? at : null;
}

type FrameVideo = HTMLMediaElement & { requestVideoFrameCallback?: (callback: (now: number) => void) => number; cancelVideoFrameCallback?: (handle: number) => void };

/** Calls `onFrame` once, at the first presented video frame (requestVideoFrameCallback), else at `playing`. Returns a cancel. */
export function onFirstFrame(media: HTMLMediaElement, onFrame: (at: number) => void): () => void {
  const video = media as FrameVideo;
  let done = false;
  let handle: number | null = null;
  const cleanup = () => {
    media.removeEventListener('playing', onPlaying);
    if (handle !== null) video.cancelVideoFrameCallback?.(handle);
  };
  const fire = (at: number) => {
    if (done) return;
    done = true;
    cleanup();
    onFrame(at);
  };
  function onPlaying() { fire(performance.now()); }
  if (typeof video.requestVideoFrameCallback === 'function') handle = video.requestVideoFrameCallback((now) => fire(now));
  else media.addEventListener('playing', onPlaying);
  return () => { done = true; cleanup(); };
}

/** Start counting long tasks (> 50 ms); the returned function stops and returns the count. */
export function startLongTaskCount(): () => number {
  if (typeof PerformanceObserver === 'undefined' || !PerformanceObserver.supportedEntryTypes?.includes('longtask')) return () => 0;
  let count = 0;
  const longOnes = (entries: PerformanceEntry[]) => entries.filter((entry) => entry.duration > 50).length;
  const observer = new PerformanceObserver((list) => { count += longOnes(list.getEntries()); });
  observer.observe({ type: 'longtask' });
  return () => {
    count += longOnes(observer.takeRecords());
    observer.disconnect();
    return count;
  };
}
