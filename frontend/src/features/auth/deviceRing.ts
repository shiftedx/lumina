import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { getDeviceMembers } from '../../api';
import type { DeviceMember } from '../../types';

// One shared answer per page: the top bar, the picker host and the auth screen read the same ring.
let members: DeviceMember[] | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

// Every request that reads or rewrites the ring cookie runs one at a time (security re-review M-R2): a device-members
// answer still in flight around a switch, forget or sign-in would rewrite the ring without the member just chosen.
let tail: Promise<unknown> = Promise.resolve();
export function ringCall<T>(run: () => Promise<T>): Promise<T> {
  const next = tail.then(run, run);
  tail = next.catch(() => undefined);
  return next;
}

export function refreshDeviceRing(): Promise<void> {
  // A failed or slow answer counts as an empty ring, so sign-in is never blocked.
  inflight ??= ringCall(() => getDeviceMembers({ timeoutMs: 3000 }))
    .then((next) => { members = Array.isArray(next) ? next : []; }, () => { members = []; })
    .finally(() => { inflight = null; emit(); });
  return inflight;
}

/** Forget the cached ring (sign-in, sign-out, switch): the next reader asks again. */
export function resetDeviceRing(): void {
  members = null;
  emit();
}

export function useDeviceRing(enabled = true): { members: DeviceMember[] | null; refresh: () => Promise<void> } {
  const snapshot = useSyncExternalStore(subscribe, () => members);
  useEffect(() => { if (enabled && snapshot === null) void refreshDeviceRing(); }, [enabled, snapshot]);
  return { members: snapshot, refresh: useCallback(() => refreshDeviceRing(), []) };
}
