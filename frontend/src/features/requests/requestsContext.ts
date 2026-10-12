import { createContext, useContext, useEffect, useState } from 'react';

import type { RequestsRoute } from '../../app/routes';
import type { UserProfile } from '../../types';
import type { CatalogItem, CatalogStatus } from './requestsApi';

export type SessionGate = { captureSessionToken: () => number; isSessionTokenCurrent: (token: number) => boolean };
export type RequestsActions = {
  user: UserProfile;
  session: SessionGate;
  /** The admin queue changed (the nav badge refreshes). */
  onQueueChanged: () => void;
  isAdmin: boolean;
  /** Another Requests page; `replace` keeps one history entry (search typing). */
  go: (route: RequestsRoute, replace?: boolean) => void;
  openTitle: (item: CatalogItem, options?: { trailer?: boolean }) => void;
  openLibrary: (titleId: string) => void;
  openSettings: () => void;
  /** Opens the request sheet for this title. */
  ask: (item: CatalogItem) => void;
  /** The item's status with this visit's optimistic updates applied. */
  statusOf: (item: CatalogItem) => CatalogStatus;
};

export const RequestsContext = createContext<RequestsActions | null>(null);
export function useRequests(): RequestsActions {
  const value = useContext(RequestsContext);
  if (!value) throw new Error('useRequests outside the Requests surface');
  return value;
}

/** A clock for countdowns, ticking once a minute. */
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export type Loaded<T> = { data: T | null; error: unknown; loading: boolean; reload: () => void };
/** Loads once per `key` (abortable); a new key starts over, `reload` retries. */
export function useLoad<T>(key: string, load: (signal: AbortSignal) => Promise<T>): Loaded<T> {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; data: T | null; error: unknown }>({ key: '', data: null, error: null });
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => { if (!controller.signal.aborted) setState({ key, data, error: null }); },
      (error) => { if (!controller.signal.aborted) setState({ key, data: null, error }); },
    );
    return () => controller.abort();
  }, [key, attempt]); // eslint-disable-line react-hooks/exhaustive-deps -- `key` names the load
  const current = state.key === key;
  return { data: current ? state.data : null, error: current ? state.error : null, loading: !current, reload: () => setAttempt((value) => value + 1) };
}
