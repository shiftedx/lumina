import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { liveEntry, liveSnapshot } from '../../test/remoteFixtures';
import { useLiveViewers, VIEWERS_POLL_MS } from './useLiveViewers';

const api = vi.hoisted(() => ({ getLiveDiscovery: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const url = 'https://www.youtube.com/watch?v=l1';
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); });
afterEach(() => vi.useRealTimers());

describe('useLiveViewers', () => {
  it('starts from the opened entry and follows the snapshot every 60 s', async () => {
    api.getLiveDiscovery.mockResolvedValue(liveSnapshot({ items: [liveEntry('l1', { view_count: 13_000 })] }));
    const { result } = renderHook(() => useLiveViewers(url, true, 12_400));
    expect(result.current).toEqual({ count: 12_400, asOf: null });
    await act(async () => { vi.advanceTimersByTime(VIEWERS_POLL_MS); });
    await flush();
    expect(result.current).toEqual({ count: 13_000, asOf: null });
  });

  it('keeps the last count with its time when the entry leaves, and drops it after 10 minutes', async () => {
    api.getLiveDiscovery.mockResolvedValue(liveSnapshot({ items: [] }));
    const { result } = renderHook(() => useLiveViewers(url, true, 12_400));
    const seen = Date.now();
    await act(async () => { vi.advanceTimersByTime(VIEWERS_POLL_MS); });
    await flush();
    expect(result.current).toEqual({ count: 12_400, asOf: seen });
    for (let minute = 0; minute < 10; minute += 1) {
      await act(async () => { vi.advanceTimersByTime(VIEWERS_POLL_MS); });
      await flush();
    }
    expect(result.current.count).toBeNull();
  });

  it('keeps the last count when a read fails', async () => {
    api.getLiveDiscovery.mockResolvedValueOnce(liveSnapshot({ items: [liveEntry('l1', { view_count: 13_000 })] })).mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useLiveViewers(url, true, 12_400));
    await act(async () => { vi.advanceTimersByTime(VIEWERS_POLL_MS); });
    await flush();
    await act(async () => { vi.advanceTimersByTime(VIEWERS_POLL_MS); });
    await flush();
    expect(result.current).toEqual({ count: 13_000, asOf: null });
  });

  it('shows a new source\'s own initial count on the first render', () => {
    api.getLiveDiscovery.mockResolvedValue(liveSnapshot());
    const renders: Array<number | null> = [];
    const { rerender } = renderHook(({ source, initial }) => { const value = useLiveViewers(source, true, initial); renders.push(value.count); return value; }, { initialProps: { source: url, initial: 5 } });
    rerender({ source: `${url}2`, initial: 9 });
    expect(renders).not.toContain(null);
    expect(renders[1]).toBe(9);
  });

  it('keeps the count with an "as of" time when the entry has no view_count', async () => {
    api.getLiveDiscovery.mockResolvedValue(liveSnapshot({ items: [liveEntry('l1', { view_count: null })] }));
    const { result } = renderHook(() => useLiveViewers(url, true, 12_400));
    const seen = Date.now();
    await act(async () => { vi.advanceTimersByTime(VIEWERS_POLL_MS); });
    await flush();
    expect(result.current).toEqual({ count: 12_400, asOf: seen });
  });

  it('never reads while hidden or when the source is not live, and catches up on return', async () => {
    api.getLiveDiscovery.mockResolvedValue(liveSnapshot());
    renderHook(() => useLiveViewers(url, false, 1));
    await act(async () => { vi.advanceTimersByTime(3 * VIEWERS_POLL_MS); });
    expect(api.getLiveDiscovery).not.toHaveBeenCalled();
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    renderHook(() => useLiveViewers(url, true, 1));
    await act(async () => { vi.advanceTimersByTime(2 * VIEWERS_POLL_MS); });
    expect(api.getLiveDiscovery).not.toHaveBeenCalled();
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(api.getLiveDiscovery).toHaveBeenCalledTimes(1);
  });
});
