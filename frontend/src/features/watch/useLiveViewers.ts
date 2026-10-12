/**
 * Live viewers on the watch page: read from the cached live snapshot every 60 s while the
 * page is visible and the source is live (never a per-page provider probe). When the entry leaves the snapshot the last
 * count stays, labelled with when it was seen, and is dropped 10 minutes after that.
 */
import { useEffect, useState } from 'react';

import { getLiveDiscovery } from '../../api';

export const VIEWERS_POLL_MS = 60_000;
export const VIEWERS_TTL_MS = 600_000;

type Viewers = { count: number | null; seenAt: number; absent: boolean };

export function useLiveViewers(sourceUrl: string | null, live: boolean, initial: number | null): { count: number | null; asOf: number | null } {
  // Keyed by source so a new source shows its own `initial` on the first render, with no reset effect.
  const [byUrl, setByUrl] = useState<Record<string, Viewers>>({});
  const key = sourceUrl ?? '';
  const state = byUrl[key] ?? { count: initial, seenAt: 0, absent: false };
  useEffect(() => {
    if (!live || !sourceUrl) return undefined;
    let active = true;
    let missed = false;
    const startedAt = Date.now();
    const read = () => {
      getLiveDiscovery().then((snapshot) => {
        if (!active) return;
        const entry = [...snapshot.hero, ...snapshot.items].find((item) => item.webpage_url === sourceUrl);
        setByUrl((all) => {
          const current = all[sourceUrl] ?? { count: initial, seenAt: startedAt, absent: false };
          return { ...all, [sourceUrl]: entry?.view_count != null ? { count: entry.view_count, seenAt: Date.now(), absent: false } : { ...current, absent: true } };
        });
      }).catch(() => { /* keep the last count */ });
    };
    const tick = () => { if (document.hidden) missed = true; else read(); };
    const onVisible = () => { if (!document.hidden && missed) { missed = false; read(); } };
    const timer = window.setInterval(tick, VIEWERS_POLL_MS);
    document.addEventListener('visibilitychange', onVisible);
    return () => { active = false; window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible); };
  }, [live, sourceUrl]); // eslint-disable-line react-hooks/exhaustive-deps
  if (state.absent && Date.now() - state.seenAt > VIEWERS_TTL_MS) return { count: null, asOf: null };
  return { count: state.count, asOf: state.absent ? state.seenAt : null };
}
