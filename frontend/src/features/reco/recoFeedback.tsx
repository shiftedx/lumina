/**
 * The feedback state of recommendation cards: one provider, mounted by LuminaApp, owns
 * the Undo window of every card so no surface gains a prop. A choice saves a suppression, then the card is
 * replaced in place by its Undo row; Undo restores that row; when the window closes the card is gone for good (until
 * the member changes or a restore in Settings resets it) and the app drops it from its local lists and re-fetches.
 *
 */
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import type { RecoAnnotation, RemoteEntry, SuppressRecommendationInput } from '../../types';
import { type RecoFeedback, remoteTarget, type RecoTarget, suppressionInput, targetKey, undoMessage } from './recoModel';
import { InlineNotice } from '../../ui';
import './reco.css';

type Hidden = { feedback: RecoFeedback; target: RecoTarget; suppressionId: string; rail?: string };

export type RecoFeedbackApi = {
  hidden: ReadonlyMap<string, Hidden>;
  gone: ReadonlySet<string>;
  generation: number;
  feedback: (kind: RecoFeedback, target: RecoTarget, reco: RecoAnnotation | null, rail?: string) => Promise<void>;
  undo: (key: string) => Promise<void>;
  expire: (key: string) => void;
};

export type RecoFeedbackProviderProps = {
  suppress: (input: SuppressRecommendationInput) => Promise<{ id: string }>;
  restore: (id: string) => Promise<void>;
  onExpired: (entry: { feedback: RecoFeedback; target: RecoTarget }) => void;
  onError: (message: string) => void;
  resetKey: string;
  children: ReactNode;
};

const Context = createContext<RecoFeedbackApi | null>(null);
export const useRecoFeedback = (): RecoFeedbackApi | null => useContext(Context);
export const useRecoGeneration = (): number => useContext(Context)?.generation ?? 0;

export function RecoFeedbackProvider({ suppress, restore, onExpired, onError, resetKey, children }: RecoFeedbackProviderProps) {
  const latest = useRef({ suppress, restore, onExpired, onError });
  latest.current = { suppress, restore, onExpired, onError };
  // The ref is the truth (so two calls in one tick see each other); the state mirrors it for rendering.
  const hiddenRef = useRef<ReadonlyMap<string, Hidden>>(new Map());
  const [hidden, setHidden] = useState(hiddenRef.current);
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const [generation, setGeneration] = useState(0);
  const saving = useRef(new Set<string>());
  // Bumped on every reset: a save or Undo that started under an earlier member must not land in this one.
  const epoch = useRef(0);
  const commit = (next: ReadonlyMap<string, Hidden>) => { hiddenRef.current = next; setHidden(next); };

  // A different member, or a restore in Settings: nothing hidden or gone carries over.
  useEffect(() => {
    hiddenRef.current = new Map();
    setHidden(hiddenRef.current);
    setGone(new Set());
    saving.current.clear();
    epoch.current += 1;
  }, [resetKey]);

  const feedback = useCallback<RecoFeedbackApi['feedback']>(async (kind, target, reco, rail) => {
    const key = targetKey(target);
    if (saving.current.has(key) || hiddenRef.current.has(key)) return;
    saving.current.add(key);
    const started = epoch.current;
    try {
      const saved = await latest.current.suppress(suppressionInput(kind, target, reco));
      if (epoch.current === started) commit(new Map(hiddenRef.current).set(key, { feedback: kind, target, suppressionId: saved.id, rail }));
    } catch {
      if (epoch.current === started) latest.current.onError('Could not save that. Try again.');
    } finally {
      if (epoch.current === started) saving.current.delete(key);
    }
  }, []);

  const undo = useCallback(async (key: string) => {
    const entry = hiddenRef.current.get(key);
    if (!entry) return;
    const started = epoch.current;
    try {
      await latest.current.restore(entry.suppressionId);
      if (epoch.current !== started) return;
      const next = new Map(hiddenRef.current);
      next.delete(key);
      commit(next);
    } catch {
      if (epoch.current === started) latest.current.onError('Could not undo that. Try again.');
    }
  }, []);

  const expire = useCallback((key: string) => {
    const entry = hiddenRef.current.get(key);
    if (!entry) return; // undone, or already expired
    const next = new Map(hiddenRef.current);
    next.delete(key);
    commit(next);
    setGone((current) => new Set(current).add(key));
    setGeneration((value) => value + 1);
    latest.current.onExpired({ feedback: entry.feedback, target: entry.target });
  }, []);

  const api = useMemo<RecoFeedbackApi>(() => ({ hidden, gone, generation, feedback, undo, expire }), [hidden, gone, generation, feedback, undo, expire]);
  return <Context.Provider value={api}>{children}</Context.Provider>;
}

/** What a card's art menu needs to offer feedback: the target, the server's reason (null on category rails) and the rail that owns its Undo row. */
export type RecoArt = { target: RecoTarget; reco: RecoAnnotation | null; rail?: string };
const ArtContext = createContext<RecoArt | null>(null);
/** For the art menu of the card inside: null outside a recommendation list. */
export const useRecoArt = (): RecoArt | null => useContext(ArtContext);
/** Marks the one card inside as a recommendation; no markup of its own. A null value is no scope. */
export function RecoArtScope({ value, children }: { value: RecoArt | null; children: ReactNode }) {
  return <ArtContext.Provider value={value}>{children}</ArtContext.Provider>;
}

export function RecoCard({ target, reco, children }: { target: RecoTarget; reco: RecoAnnotation | null | undefined; children: ReactNode }) {
  const api = useRecoFeedback();
  const key = targetKey(target);
  const entry = api?.hidden.get(key);
  const gone = api?.gone.has(key) ?? false;
  const holder = useRef<HTMLDivElement>(null);
  const wasHidden = useRef(false);
  // After Undo the card is back: put focus on its main control (the Undo button that had it is gone).
  useEffect(() => {
    if (wasHidden.current && !entry && !gone) holder.current?.querySelector<HTMLElement>('[data-focus-item]')?.focus();
    wasHidden.current = Boolean(entry);
  }, [entry, gone]);
  // A remote card is always recommendable-feedback; a title only when the server annotated it.
  const value = useMemo(() => (reco != null || target.kind === 'remote' ? { target, reco: reco ?? null } : null), [target, reco]);
  if (api && entry) return <InlineNotice action={{ label: 'Undo', onAction: () => { void api.undo(key); } }} message={undoMessage(entry.feedback, entry.target)} onExpire={() => api.expire(key)} />;
  if (gone) return null;
  return <div className="reco-card" ref={holder}><RecoArtScope value={api ? value : null}>{children}</RecoArtScope></div>;
}

export function useRecoFilter<T>(items: readonly T[], targetOf: (item: T) => RecoTarget, keepHidden = false): T[] {
  const api = useRecoFeedback();
  if (!api) return [...items];
  return items.filter((item) => {
    const key = targetKey(targetOf(item));
    return !api.gone.has(key) && (keepHidden || !api.hidden.has(key));
  });
}

/** A surface that lists one item in several rails names which rail owns its Undo row (the first) until a rail records where the member acted; others render none. */
const OwnersContext = createContext<ReadonlyMap<string, string> | null>(null);
export const RecoRowOwnersProvider = OwnersContext.Provider;
export function rowOwners(rails: readonly { key: string; targets: readonly RecoTarget[] }[]): Map<string, string> {
  const owners = new Map<string, string>();
  for (const rail of rails) for (const target of rail.targets) if (!owners.has(targetKey(target))) owners.set(targetKey(target), rail.key);
  return owners;
}

/** After Undo the card is back in the rail that owned the row; the Undo button that had focus is gone. */
function focusRestored(rail: string | undefined, key: string): void {
  window.requestAnimationFrame(() => {
    const cards = Array.from(document.querySelectorAll<HTMLElement>('[data-remote-key]')).filter((element) => element.getAttribute('data-remote-key') === key);
    const card = cards.find((element) => element.closest('[data-rail-key]')?.getAttribute('data-rail-key') === rail) ?? cards[0];
    card?.querySelector<HTMLElement>('[data-focus-item]')?.focus();
  });
}

export function RecoHiddenRows({ targets, rail }: { targets: readonly RecoTarget[]; rail?: string }) {
  const api = useRecoFeedback();
  const owners = useContext(OwnersContext);
  if (!api) return null;
  const rows = targets.map((target) => ({ key: targetKey(target), entry: api.hidden.get(targetKey(target)) }))
    .filter((row) => row.entry && (!owners || !rail || (row.entry!.rail ?? owners.get(row.key)) === rail));
  if (!rows.length) return null;
  return (
    <>
      {rows.map(({ key, entry }) => (
        <InlineNotice action={{ label: 'Undo', onAction: () => { void api.undo(key).then(() => focusRestored(entry!.rail ?? rail, key)); } }} key={key} message={undoMessage(entry!.feedback, entry!.target)} onExpire={() => api.expire(key)} />
      ))}
    </>
  );
}

export function useAutoplayEligible(): (item: RemoteEntry) => boolean {
  const api = useRecoFeedback();
  return useCallback((item) => {
    if (item.reco?.slot === 'explore') return false;
    if (!api) return true;
    const key = targetKey(remoteTarget(item));
    return !api.hidden.has(key) && !api.gone.has(key);
  }, [api]);
}
