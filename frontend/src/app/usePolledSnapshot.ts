import { useCallback, useEffect, useRef } from 'react';

const POLL_MS = 30_000;

type SessionGate = { captureSessionToken: () => number; isSessionTokenCurrent: (token: number) => boolean };

/**
 * Runs `load` now and every `intervalMs` (default 30 s) while `key` is non-null; a new key restarts it. Ticks are skipped
 * while the tab is hidden and caught up once it is visible again. `load` applies results only while
 * `isCurrent()` holds, and resolves `true` once the snapshot is settled to stop polling.
 */
export function usePolledSnapshot(key: string | null, session: SessionGate, load: (isCurrent: () => boolean, signal: AbortSignal) => Promise<boolean | void>, intervalMs: number | null = POLL_MS, refreshOnVisible = false): () => Promise<void> {
  const loadRef = useRef(load);
  const refreshRef = useRef<() => Promise<void>>(() => Promise.resolve());
  loadRef.current = load;
  const { captureSessionToken, isSessionTokenCurrent } = session;
  useEffect(() => {
    if (key === null) {
      refreshRef.current = () => Promise.resolve();
      return undefined;
    }
    let active = true;
    let settled = false;
    let missed = false;
    let queued = false;
    let queuedWaiters: (() => void)[] = [];
    let activeWaiters: (() => void)[] = [];
    let finishActive: (() => void) | null = null;
    let controller: AbortController | null = null;
    const token = captureSessionToken();
    const isCurrent = () => active && isSessionTokenCurrent(token);
    const resolve = (waiters: (() => void)[]) => waiters.forEach((done) => done());
    const run = (): Promise<void> => {
      if (!active || settled) return Promise.resolve();
      if (controller) { queued = true; return Promise.resolve(); }
      const waiters = queuedWaiters;
      queuedWaiters = [];
      activeWaiters = waiters;
      queued = false;
      const next = new AbortController();
      controller = next;
      const complete = new Promise<void>((done) => { finishActive = done; });
      void loadRef.current(isCurrent, next.signal).then((stop) => {
        if (stop && isCurrent()) settled = true;
      }, () => undefined).then(() => undefined).finally(() => {
        if (controller !== next) return;
        controller = null;
        finishActive?.();
        finishActive = null;
        resolve(activeWaiters);
        activeWaiters = [];
        if (active && queued && !settled && !document.hidden) { void run(); }
        else {
          if (!active || settled) { resolve(queuedWaiters); queuedWaiters = []; }
        }
      });
      return complete;
    };
    const refresh = () => {
      if (!active || settled) return Promise.resolve();
      if (!controller) return run();
      queued = true;
      return new Promise<void>((done) => queuedWaiters.push(done));
    };
    const tick = () => { if (settled) return; if (document.hidden) missed = true; else run(); };
    const onVisibility = () => { if (!document.hidden && (refreshOnVisible || missed || queued) && !settled) { missed = false; run(); } };
    refreshRef.current = refresh;
    run();
    const interval = intervalMs === null ? null : window.setInterval(tick, intervalMs);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      active = false;
      queued = false;
      controller?.abort();
      finishActive?.();
      finishActive = null;
      resolve(activeWaiters);
      activeWaiters = [];
      resolve(queuedWaiters);
      queuedWaiters = [];
      if (refreshRef.current === refresh) refreshRef.current = () => Promise.resolve();
      if (interval !== null) window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [key, captureSessionToken, isSessionTokenCurrent, intervalMs, refreshOnVisible]);
  return useCallback(() => refreshRef.current(), []);
}
