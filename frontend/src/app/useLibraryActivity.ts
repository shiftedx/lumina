import { useEffect, useState } from 'react';

import { getLibraryActivity } from '../api';
import type { LibraryActivity } from '../types';

const POLL_MS = 5_000;

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

    const run = () => {
      if (!active || pending || document.hidden) {
        if (document.hidden) missed = true;
        return;
      }
      pending = true;
      missed = false;
      controller = new AbortController();
      const requestController = controller;
      void getLibraryActivity(requestController.signal).then(
        (activity) => { if (active && !requestController.signal.aborted) setStored({ userId, state: 'ready', activity }); },
        () => { if (active && !requestController.signal.aborted) setStored({ userId, state: 'failed', activity: null }); },
      ).finally(() => {
        if (controller === requestController) controller = null;
        pending = false;
        if (active && missed && !document.hidden) run();
      });
    };
    const tick = () => run();
    const onVisibility = () => {
      if (document.hidden) {
        missed = true;
        controller?.abort();
      }
      else if (missed) {
        setStored(loading(userId));
        run();
      }
    };

    if (!document.hidden) run();
    const interval = window.setInterval(tick, POLL_MS);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [userId]);

  if (stored.userId !== (userId ?? null)) return { state: 'loading', activity: null };
  return { state: stored.state, activity: stored.activity };
}
