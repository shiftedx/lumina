import { useEffect, useRef } from 'react';

/** Poll delays for an in-flight job: 1s growing ×1.5 up to 10s. */
export const pollDelay = (attempt: number) => Math.min(10_000, 1000 * 1.5 ** attempt);

/**
 * Polls `fetchJob(jobId)` with backoff while `jobId` is set, handing each result to `onUpdate`
 * and stopping once its state is in `done`. Failed polls retry on the same schedule.
 */
export function usePollJob<T extends { state: string }>(jobId: string | null, fetchJob: (id: string) => Promise<T>, done: ReadonlySet<string>, onUpdate: (next: T) => void) {
  const latest = useRef({ fetchJob, onUpdate });
  latest.current = { fetchJob, onUpdate };
  useEffect(() => {
    if (!jobId) return;
    let attempt = 0;
    let timer = 0;
    let cancelled = false;
    const poll = () => {
      timer = window.setTimeout(async () => {
        try {
          const next = await latest.current.fetchJob(jobId);
          if (cancelled) return;
          latest.current.onUpdate(next);
          if (!done.has(next.state)) poll();
        } catch {
          if (!cancelled) poll();
        }
      }, pollDelay(attempt++));
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobId, done]);
}
