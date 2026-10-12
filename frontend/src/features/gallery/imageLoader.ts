/**
 * Every gallery image load goes through here. Implementation: cap 4, or 12
 * over h2; four priority classes; fast-scroll suppression; dropping queued loads on unmount.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

/** 1 hero and first screen, 2 other in-viewport, 3 prefetch ahead, 4 overscan behind. */
export type LoadPriority = 1 | 2 | 3 | 4;
export type ImageSlot = { granted: boolean; fetchPriority: 'high' | 'low'; settle: () => void };
export type LoaderStats = { cap: number; inFlight: number; queued: number; maxInFlight: number; dropped: number };
export type SlotHandle = { update: (priority: LoadPriority, position: number) => void; release: () => void };

export const fetchPriorityOf = (priority: LoadPriority): 'high' | 'low' => (priority <= 2 ? 'high' : 'low');

/** Faster than this many viewport heights per second, only classes 1 and 2 load. */
const FAST_SCROLL = 3;
const PREFETCH_RESUME_MS = 120;
const PREFETCH_MEMORY = 500;

type Entry = { priority: LoadPriority; position: number; seq: number; granted: boolean; done: boolean; onGrant: () => void };

let cap: number | null = null;
const queue = new Set<Entry>();
let inFlight = 0;
let maxInFlight = 0;
let dropped = 0;
let sequence = 0;
let fast = false;
let resumeTimer: ReturnType<typeof setTimeout> | null = null;
const prefetched = new Set<string>();
/** Prefetches still waiting for a slot, so a repeat request can raise their class. */
type QueuedPrefetch = { priority: LoadPriority; slot?: SlotHandle };
const queuedPrefetch = new Map<string, QueuedPrefetch>();

/** 4 over HTTP/1.1, leaving 2 of the browser's 6 connections for API calls; 12 over h2/h3. */
function capacity(): number {
  if (cap === null) {
    const navigation = globalThis.performance?.getEntriesByType?.('navigation')[0] as PerformanceNavigationTiming | undefined;
    cap = /^h[23]/.test(navigation?.nextHopProtocol ?? '') ? 12 : 4;
  }
  return cap;
}

const before = (a: Entry, b: Entry): number => a.priority - b.priority || a.position - b.position || a.seq - b.seq;

// a linear scan per grant; the queue only ever holds a few rendered rows of images.
function pump(): void {
  while (inFlight < capacity()) {
    let next: Entry | null = null;
    for (const entry of queue) if ((!fast || entry.priority <= 2) && (next === null || before(entry, next) < 0)) next = entry;
    if (next === null) return;
    queue.delete(next);
    next.granted = true;
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    next.onGrant();
  }
}

function finish(entry: Entry): void {
  if (entry.done) return;
  entry.done = true;
  if (entry.granted) {
    inFlight -= 1;
    pump();
  } else if (queue.delete(entry)) {
    dropped += 1;
  }
}

/** Queue one load. `onGrant` runs when a slot is free (possibly at once); `release` frees it, or drops it if still queued. */
export function requestSlot(priority: LoadPriority, position: number, onGrant: () => void): SlotHandle {
  const entry: Entry = { priority, position, seq: (sequence += 1), granted: false, done: false, onGrant };
  queue.add(entry);
  pump();
  return {
    update: (nextPriority, nextPosition) => {
      if (entry.granted || entry.done || (entry.priority === nextPriority && entry.position === nextPosition)) return;
      entry.priority = nextPriority;
      entry.position = nextPosition;
      pump();
    },
    release: () => finish(entry),
  };
}

/**
 * Ask for a load slot for `src` (null = nothing to load). Assign `src` to the <img> only once `granted`, and
 * call `settle()` when it loads or errors. Unmount or a new `src` drops a queued request and frees a held slot.
 * `position` orders requests inside one class: row * 1000 + column (top-to-bottom, left-to-right).
 */
export function useImageSlot(src: string | null, priority: LoadPriority, position = 0): ImageSlot {
  const [granted, setGranted] = useState<string | null>(null);
  const handle = useRef<{ src: string; slot: SlotHandle } | null>(null);
  useEffect(() => {
    if (src === null) return undefined;
    const slot = requestSlot(priority, position, () => setGranted(src));
    handle.current = { src, slot };
    return () => {
      slot.release();
      if (handle.current?.slot === slot) handle.current = null;
      setGranted((current) => (current === src ? null : current));
    };
  }, [src]); // priority and position changes go through update() below, without re-queueing
  useEffect(() => { handle.current?.slot.update(priority, position); }, [priority, position]);
  // Only the key that was granted may free its slot: a late load event from an earlier src must not free the new one.
  const settle = useCallback(() => {
    if (granted === src && handle.current?.src === src) handle.current.slot.release();
  }, [granted, src]);
  return { granted: src !== null && granted === src, fetchPriority: fetchPriorityOf(priority), settle };
}

/** Load `src` into the browser cache through the queue (intent prefetch at 3, title page hero at 1). */
export function prefetchImage(src: string, priority: LoadPriority): void {
  const waiting = queuedPrefetch.get(src);
  if (waiting !== undefined) {
    if (priority < waiting.priority) {
      waiting.priority = priority;
      waiting.slot?.update(priority, 0);
    }
    return;
  }
  if (prefetched.has(src)) return;
  prefetched.add(src);
  if (prefetched.size > PREFETCH_MEMORY) prefetched.delete(prefetched.values().next().value as string);
  const request: QueuedPrefetch = { priority };
  queuedPrefetch.set(src, request);
  const slot = requestSlot(priority, 0, () => {
    if (queuedPrefetch.get(src) === request) queuedPrefetch.delete(src);
    const image = new Image();
    image.decoding = 'async';
    image.setAttribute('fetchpriority', fetchPriorityOf(request.priority));
    image.onload = () => slot.release();
    image.onerror = () => { prefetched.delete(src); slot.release(); };
    image.src = src;
  });
  request.slot = slot;
}

/** WallGrid reports scroll speed in viewport heights per second; above 3 only classes 1 and 2 load. */
export function setScrollVelocity(viewportsPerSecond: number): void {
  if (viewportsPerSecond <= FAST_SCROLL) return;
  fast = true;
  if (resumeTimer !== null) clearTimeout(resumeTimer);
  resumeTimer = setTimeout(() => {
    resumeTimer = null;
    fast = false;
    pump();
  }, PREFETCH_RESUME_MS);
}

export function loaderStats(): LoaderStats {
  return { cap: capacity(), inFlight, queued: queue.size, maxInFlight, dropped };
}

/** Tests only: forget every request and detect the protocol again. */
export function resetImageLoader(): void {
  queue.clear();
  prefetched.clear();
  queuedPrefetch.clear();
  inFlight = 0;
  maxInFlight = 0;
  dropped = 0;
  fast = false;
  cap = null;
  if (resumeTimer !== null) clearTimeout(resumeTimer);
  resumeTimer = null;
}

if (import.meta.env.DEV && typeof window !== 'undefined') {
  (window as Window & { __luminaImageLoader?: { stats: () => LoaderStats } }).__luminaImageLoader = { stats: loaderStats };
}
