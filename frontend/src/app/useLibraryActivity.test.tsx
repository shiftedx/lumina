import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { LibraryActivity } from '../types';
import { useLibraryActivity } from './useLibraryActivity';

const api = vi.hoisted(() => ({ getLibraryActivity: vi.fn() }));
vi.mock('../api', () => api);

const idle: LibraryActivity = {
  scanning_roots: 0,
  discovering_roots: 0,
  indexing_roots: 0,
  preparing_roots: 0,
  stalled_roots: 0,
  waiting_files: 0,
  waiting_error_files: 0,
  discovered_files: 0,
  completed_files: 0,
  total_files: null,
  progress: null,
  inspected_files: 0,
  directories_listed: 0,
  directories_total: null,
};
const setHidden = (hidden: boolean) => Object.defineProperty(document, 'hidden', { configurable: true, value: hidden });

beforeEach(() => {
  api.getLibraryActivity.mockReset();
  setHidden(false);
});

afterEach(() => {
  Reflect.deleteProperty(document, 'hidden');
  vi.useRealTimers();
});

describe('useLibraryActivity', () => {
  it('polls every 15 seconds while active and every 60 seconds while idle without overlapping requests', async () => {
    vi.useFakeTimers();
    let finish!: (value: LibraryActivity) => void;
    api.getLibraryActivity.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })).mockResolvedValue(idle);
    const { result } = renderHook(() => useLibraryActivity('member-1'));

    expect(result.current.state).toBe('loading');
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(1);

    await act(async () => { finish(idle); await Promise.resolve(); });
    expect(result.current).toEqual({ state: 'ready', activity: idle });
    await act(() => vi.advanceTimersByTimeAsync(59_999));
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(2);

    const active = { ...idle, scanning_roots: 1, discovering_roots: 1 };
    api.getLibraryActivity.mockResolvedValue(active);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(3);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(4);
  });

  it('waits while hidden, aborts hidden work, and catches up when visible', async () => {
    vi.useFakeTimers();
    setHidden(true);
    api.getLibraryActivity.mockImplementation((signal?: AbortSignal) => new Promise<LibraryActivity>((resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      if (!signal) resolve(idle);
    }));
    renderHook(() => useLibraryActivity('member-1'));
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(api.getLibraryActivity).not.toHaveBeenCalled();

    setHidden(false);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(1);
    const signal = api.getLibraryActivity.mock.calls[0][0] as AbortSignal;
    setHidden(true);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(signal.aborted).toBe(true);
    await act(() => Promise.resolve());

    setHidden(false);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await act(() => Promise.resolve());
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(2);
  });

  it('invalidates cached progress on visibility resume until the refresh succeeds', async () => {
    const active = { ...idle, scanning_roots: 1, indexing_roots: 1, completed_files: 37, total_files: 100, progress: 37 };
    let finishRefresh!: (value: LibraryActivity) => void;
    api.getLibraryActivity.mockResolvedValueOnce(active).mockImplementationOnce(() => new Promise((resolve) => { finishRefresh = resolve; }));
    const { result } = renderHook(() => useLibraryActivity('member-1'));
    await waitFor(() => expect(result.current).toEqual({ state: 'ready', activity: active }));

    setHidden(true);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    setHidden(false);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(result.current).toEqual({ state: 'loading', activity: null });
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(2);

    await act(async () => { finishRefresh(idle); await Promise.resolve(); });
    expect(result.current).toEqual({ state: 'ready', activity: idle });
  });

  it('clears stale state and aborts in-flight work on member switch and unmount', async () => {
    const signals: AbortSignal[] = [];
    api.getLibraryActivity.mockImplementation((signal?: AbortSignal) => new Promise<LibraryActivity>((_resolve, reject) => {
      if (signal) {
        signals.push(signal);
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      }
    }));
    const { result, rerender, unmount } = renderHook(({ userId }) => useLibraryActivity(userId), { initialProps: { userId: 'member-1' as string | undefined } });
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(1);

    rerender({ userId: 'member-2' });
    expect(signals[0].aborted).toBe(true);
    expect(result.current).toEqual({ state: 'loading', activity: null });
    expect(api.getLibraryActivity).toHaveBeenCalledTimes(2);

    unmount();
    expect(signals[1].aborted).toBe(true);
  });

  it('does not poll without an authenticated member and exposes non-abort failures', async () => {
    const { result, rerender } = renderHook(({ userId }) => useLibraryActivity(userId), { initialProps: { userId: undefined as string | undefined } });
    expect(api.getLibraryActivity).not.toHaveBeenCalled();
    api.getLibraryActivity.mockRejectedValue(new Error('offline'));
    rerender({ userId: 'member-1' });
    await waitFor(() => expect(result.current.state).toBe('failed'));
    expect(result.current.activity).toBeNull();
  });
});
