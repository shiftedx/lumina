import { useEffect, useState } from 'react';

import { getLibraryActivity } from '../api';
import type { LibraryActivity } from '../types';

const ACTIVE_POLL_MS = 15_000;
const IDLE_POLL_MS = 60_000;

const hasActiveWork = (activity: LibraryActivity) => Boolean(
  activity.scanning_roots || activity.discovering_roots || activity.indexing_roots || activity.preparing_roots || activity.stalled_roots,
);

export type LibraryActivitySnapshot = {
  state: 'loading' | 'ready' | 'failed';
  activity: LibraryActivity | null;
};

type StoredSnapshot = LibraryActivitySnapshot & { userId: string | null };
const loading = (userId: string | null): StoredSnapshot => ({ userId, state: 'loading', activity: null });

/** Polls the redacted household scan summary only while an authenticated member can see the page. */
export function useLibraryActivity(userId: string | undefined): LibraryActivitySnapshot {
  const [stored, setStored] = useState<StoredSnapshot>(() => loading(userId ?? null));

  useEffect(() => {
    const key = userId ?? null;
    setStored(loading(key));
    if (!userId) return undefined;

    let active = true;
    let pending = false;
    let missed = document.hidden;
    let controller: AbortController | null = null;
    let timer: number | null = null;

    const schedule = (activity: LibraryActivity | null) => {
      if (!active || document.hidden) return;
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(run, activity && hasActiveWork(activity) ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    };

    const run = () => {
      if (!active || pending || document.hidden) {
        if (document.hidden) missed = true;
        return;
      }
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      pending = true;
      missed = false;
      controller = new AbortController();
      const requestController = controller;
      void getLibraryActivity(requestController.signal).then(
        (activity) => {
          if (active && !requestController.signal.aborted) {
            setStored({ userId, state: 'ready', activity });
            schedule(activity);
          }
        },
        () => {
          if (active && !requestController.signal.aborted) {
            setStored({ userId, state: 'failed', activity: null });
            schedule(null);
          }
        },
      ).finally(() => {
        if (controller === requestController) controller = null;
        pending = false;
        if (active && missed && !document.hidden) run();
      });
    };
    const onVisibility = () => {
      if (document.hidden) {
        missed = true;
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        controller?.abort();
      }
      else if (missed) {
        setStored(loading(userId));
        run();
      }
    };

    if (!document.hidden) run();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      active = false;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [userId]);

  if (stored.userId !== (userId ?? null)) return { state: 'loading', activity: null };
  return { state: stored.state, activity: stored.activity };
}
