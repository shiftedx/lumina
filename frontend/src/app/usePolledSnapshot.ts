import { useEffect, useRef } from 'react';

const POLL_MS = 30_000;

type SessionGate = { captureSessionToken: () => number; isSessionTokenCurrent: (token: number) => boolean };

/**
 * Runs `load` now and every `intervalMs` (default 30 s) while `key` is non-null; a new key restarts it. Ticks are skipped
 * while the tab is hidden and caught up once it is visible again. `load` applies results only while
 * `isCurrent()` holds, and resolves `true` once the snapshot is settled to stop polling.
 */
export function usePolledSnapshot(key: string | null, session: SessionGate, load: (isCurrent: () => boolean) => Promise<boolean | void>, intervalMs = POLL_MS) {
  const loadRef = useRef(load);
  loadRef.current = load;
  const { captureSessionToken, isSessionTokenCurrent } = session;
  useEffect(() => {
    if (key === null) return undefined;
    let active = true;
    let settled = false;
    let missed = false;
    const token = captureSessionToken();
    const isCurrent = () => active && isSessionTokenCurrent(token);
    const run = () => { void loadRef.current(isCurrent).then((stop) => { if (stop && isCurrent()) settled = true; }); };
    const tick = () => { if (settled) return; if (document.hidden) missed = true; else run(); };
    const onVisibility = () => { if (!document.hidden && missed && !settled) { missed = false; run(); } };
    run();
    const interval = window.setInterval(tick, intervalMs);
    document.addEventListener('visibilitychange', onVisibility);
    return () => { active = false; window.clearInterval(interval); document.removeEventListener('visibilitychange', onVisibility); };
  }, [key, captureSessionToken, isSessionTokenCurrent, intervalMs]);
}
