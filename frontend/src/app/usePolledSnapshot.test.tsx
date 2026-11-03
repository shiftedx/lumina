import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { usePolledSnapshot } from './usePolledSnapshot';

const session = { captureSessionToken: () => 1, isSessionTokenCurrent: () => true };
const setHidden = (hidden: boolean) => Object.defineProperty(document, 'hidden', { configurable: true, value: hidden });

afterEach(() => { Reflect.deleteProperty(document, 'hidden'); vi.useRealTimers(); });

it('skips ticks while hidden, catches up once visible, and stops after a settled load', async () => {
  vi.useFakeTimers();
  setHidden(false);
  let settle = false;
  const load = vi.fn(async () => settle);
  renderHook(() => usePolledSnapshot('home', session, load));
  expect(load).toHaveBeenCalledTimes(1);

  setHidden(true);
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(load).toHaveBeenCalledTimes(1);

  setHidden(false);
  settle = true;
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
  expect(load).toHaveBeenCalledTimes(2);
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(load).toHaveBeenCalledTimes(2);
});

it('polls at the interval it is given, and every 30 s by default', async () => {
  vi.useFakeTimers();
  setHidden(false);
  const home = vi.fn(async () => undefined);
  const live = vi.fn(async () => undefined);
  renderHook(() => usePolledSnapshot('home', session, home));
  renderHook(() => usePolledSnapshot('home-live:member-1:0', session, live, 60_000));
  expect([home.mock.calls.length, live.mock.calls.length]).toEqual([1, 1]);
  await act(() => vi.advanceTimersByTimeAsync(30_000));
  expect([home.mock.calls.length, live.mock.calls.length]).toEqual([2, 1]);
  await act(() => vi.advanceTimersByTimeAsync(30_000));
  expect([home.mock.calls.length, live.mock.calls.length]).toEqual([3, 2]);
  // Hidden: the 60 s poll skips its tick and catches up once visible, as the 30 s one does.
  setHidden(true);
  await act(() => vi.advanceTimersByTimeAsync(120_000));
  expect(live).toHaveBeenCalledTimes(2);
  setHidden(false);
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
  expect(live).toHaveBeenCalledTimes(3);
});

it('coalesces manual and timer refreshes, then aborts obsolete work', async () => {
  vi.useFakeTimers();
  setHidden(false);
  const pending: { signal: AbortSignal; resolve: () => void }[] = [];
  const load = vi.fn((_isCurrent: () => boolean, signal: AbortSignal) => new Promise<void>((resolve) => {
    pending.push({ signal, resolve });
  }));
  const view = renderHook(({ key }) => usePolledSnapshot(key, session, load), { initialProps: { key: 'home' } });
  expect(load).toHaveBeenCalledTimes(1);

  act(() => { view.result.current(); view.result.current(); });
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(load).toHaveBeenCalledTimes(1);

  await act(async () => { pending[0].resolve(); });
  expect(load).toHaveBeenCalledTimes(2);
  view.rerender({ key: 'library' });
  expect(pending[1].signal.aborted).toBe(true);
  expect(load).toHaveBeenCalledTimes(3);
  view.unmount();
  expect(pending[2].signal.aborted).toBe(true);
});

it('settles a manual refresh after its own fresh load, not later queued ticks', async () => {
  vi.useFakeTimers();
  setHidden(false);
  const pending: (() => void)[] = [];
  const load = vi.fn(() => new Promise<void>((resolve) => { pending.push(resolve); }));
  const view = renderHook(() => usePolledSnapshot('home', session, load));
  let settled = false;
  const refreshed = view.result.current().then(() => { settled = true; });
  await act(async () => { pending[0](); });
  expect(load).toHaveBeenCalledTimes(2);

  await act(() => vi.advanceTimersByTimeAsync(30_000));
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => { pending[1](); await refreshed; });
  expect(settled).toBe(true);
  expect(load).toHaveBeenCalledTimes(3);
});

it('settles an active manual refresh on cleanup even if its loader ignores abort', async () => {
  const load = vi.fn()
    .mockResolvedValueOnce(undefined)
    .mockReturnValueOnce(new Promise<void>(() => undefined));
  const view = renderHook(() => usePolledSnapshot('home', session, load));
  await act(async () => { await Promise.resolve(); });
  const refreshed = view.result.current();
  view.unmount();
  await expect(refreshed).resolves.toBeUndefined();
});
