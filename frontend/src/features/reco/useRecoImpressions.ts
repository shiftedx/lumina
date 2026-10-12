/**
 * Impressions: one IntersectionObserver per served list. A card counts once it has been at
 * least half visible for a second with the document visible; leaving early or hiding the tab resets the dwell. The hook
 * returns `observe(reco)`, which gives the ref callback for one card; the callback for a key is the same function on
 * every render, because a new one would detach and re-attach the element and restart its dwell.
 */
import { createContext, useContext, useEffect, useMemo } from 'react';

import type { RecoAnnotation } from '../../types';
import { IMPRESSION_DWELL_MS, IMPRESSION_THRESHOLD, recordRecoImpression } from './recoEvents';

export type RecoObserve = (reco: RecoAnnotation | null | undefined) => (element: Element | null) => void;
type RefCallback = (element: Element | null) => void;
type Tracked = { reco: RecoAnnotation; visible: boolean; timer: ReturnType<typeof setTimeout> | null };

const NO_REF: RefCallback = () => undefined;
const documentVisible = () => typeof document === 'undefined' || document.visibilityState === 'visible';

/** The observer, timers and ref callbacks of one list. Created lazily, so a disposed tracker can serve again. */
function createTracker(listId: string) {
  const tracked = new Map<Element, Tracked>();
  const refs = new Map<string, RefCallback>();
  const attached = new Map<string, Element>();
  let observer: IntersectionObserver | null = null;
  let listening = false;

  const stop = (entry: Tracked) => {
    if (entry.timer !== null) clearTimeout(entry.timer);
    entry.timer = null;
  };
  const arm = (entry: Tracked) => {
    stop(entry);
    if (!entry.visible || !documentVisible()) return;
    entry.timer = setTimeout(() => {
      entry.timer = null;
      recordRecoImpression(entry.reco);
    }, IMPRESSION_DWELL_MS);
  };
  const onVisibility = () => { for (const entry of tracked.values()) (documentVisible() ? arm : stop)(entry); };
  const ensureObserver = (): IntersectionObserver | null => {
    if (observer || typeof IntersectionObserver === 'undefined') return observer;
    observer = new IntersectionObserver((entries) => {
      for (const report of entries) {
        const entry = tracked.get(report.target);
        if (!entry) continue;
        // isIntersecting is true at any overlap; only half or more counts.
        entry.visible = report.isIntersecting && report.intersectionRatio >= IMPRESSION_THRESHOLD;
        arm(entry);
      }
    }, { threshold: IMPRESSION_THRESHOLD });
    if (!listening) {
      listening = true;
      document.addEventListener('visibilitychange', onVisibility);
    }
    return observer;
  };
  const detach = (key: string) => {
    const element = attached.get(key);
    if (!element) return;
    const entry = tracked.get(element);
    if (entry) stop(entry);
    tracked.delete(element);
    attached.delete(key);
    observer?.unobserve(element);
  };

  const refFor = (reco: RecoAnnotation): RefCallback => {
    const existing = refs.get(reco.key);
    if (existing) return existing;
    const callback: RefCallback = (element) => {
      detach(reco.key);
      if (!element) return;
      const watching = ensureObserver();
      if (!watching) return;
      tracked.set(element, { reco, visible: false, timer: null });
      attached.set(reco.key, element);
      watching.observe(element);
    };
    refs.set(reco.key, callback);
    return callback;
  };

  return {
    observe: ((reco) => (!reco || reco.list_id !== listId ? NO_REF : refFor(reco))) as RecoObserve,
    dispose() {
      for (const entry of tracked.values()) stop(entry);
      tracked.clear();
      attached.clear();
      refs.clear();
      observer?.disconnect();
      observer = null;
      if (listening) document.removeEventListener('visibilitychange', onVisibility);
      listening = false;
    },
  };
}

/** `observe(reco)` for the cards of the list `listId` (the `list_id` its annotations carry); null watches nothing. */
export function useRecoImpressions(listId: string | null): RecoObserve {
  const tracker = useMemo(() => (listId === null ? null : createTracker(listId)), [listId]);
  useEffect(() => () => tracker?.dispose(), [tracker]);
  return tracker?.observe ?? (() => NO_REF);
}

const Context = createContext<RecoObserve | null>(null);
/** Hands a list's `observe` to the art menus inside it, which register their own root. */
export const RecoImpressionScope = Context.Provider;
/** For a leaf under a scope: the ref that registers it as a card of that list, or undefined outside any scope. */
export function useRecoImpressionRef(reco: RecoAnnotation | null | undefined): RefCallback | undefined {
  return useContext(Context)?.(reco);
}
