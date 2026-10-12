import { useCallback, useEffect, useRef } from 'react';

import { prefetchRemote } from '../../api';

/** A card rested on this long is a likely click. Shorter passes (a mouse sweep) ask for nothing. */
export const INTENT_DWELL_MS = 600;
// Under the server's 120 s preview cache: within it a second ask would only re-hit a warm entry.
const ASKED_TTL_MS = 60_000;
const asked = new Map<string, number>();

/** Tests: forget which sources were already asked for. */
export function resetPlaybackIntent() {
  asked.clear();
}

function ask(sourceUrl: string) {
  const now = Date.now();
  if (now - (asked.get(sourceUrl) ?? -Infinity) < ASKED_TTL_MS) return;
  if (asked.size >= 200) asked.clear(); // crude bound; an LRU only if members browse hundreds of cards a minute
  asked.set(sourceUrl, now);
  prefetchRemote(sourceUrl).catch(() => undefined); // a warm-up never reports; the click does
}

/**
 * Pre-resolve a remote source while the member hovers or focuses its card, so the click usually finds
 * the extraction done. The server also dedupes per source and caps running extractions per member.
 */
export function usePlaybackIntent(sourceUrl: string | null) {
  const timer = useRef<ReturnType<typeof globalThis.setTimeout> | undefined>(undefined);
  const cancel = useCallback(() => {
    globalThis.clearTimeout(timer.current);
    timer.current = undefined;
  }, []);
  const arm = useCallback(() => {
    if (!sourceUrl || timer.current !== undefined) return;
    timer.current = globalThis.setTimeout(() => {
      timer.current = undefined;
      ask(sourceUrl);
    }, INTENT_DWELL_MS);
  }, [sourceUrl]);
  useEffect(() => cancel, [cancel]);
  return { onPointerEnter: arm, onPointerLeave: cancel, onFocus: arm, onBlur: cancel };
}
