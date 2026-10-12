import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AppEvent, LibraryItem } from './types';
import { isLibraryHint, LIBRARY_HINT_REFETCH_MS, useAuthenticatedWorkspace } from './workspace';

/** #167: a member with library limits gets a content-free hint; the workspace refetches its own page, debounced. */
const api = vi.hoisted(() => ({ connectEvents: vi.fn(), listLibrary: vi.fn() }));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  connectEvents: api.connectEvents,
  getRuntimeHealth: vi.fn().mockResolvedValue({ status: 'ok', version: '1.0.0' }),
  getSession: vi.fn().mockResolvedValue({ user: { id: 'kid', username: 'kid', display_name: 'Kid', role: 'viewer', is_active: true } }),
  listJobs: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
  listLibrary: api.listLibrary,
  getMySettings: vi.fn().mockResolvedValue(null),
  listAutomations: vi.fn().mockResolvedValue([]),
  updateMySettings: vi.fn(),
}));

const row = (id: string, downloaded_at: string) => ({ id, title: id, status: 'completed', downloaded_at, created_at: downloaded_at } as unknown as LibraryItem);
const hint: AppEvent = { type: 'library_item_upserted', payload: { broadcast: true } };

describe('library hints for members with library limits', () => {
  it('tells a content-free hint from an item event', () => {
    expect(isLibraryHint(hint)).toBe(true);
    expect(isLibraryHint({ type: 'library_item_missing', payload: { broadcast: true } })).toBe(true);
    expect(isLibraryHint({ type: 'library_item_upserted', payload: { broadcast: true, item: row('a', '2026-10-01T00:00:00Z') } })).toBe(false);
    expect(isLibraryHint({ type: 'job_completed', payload: { broadcast: true } })).toBe(false);
  });

  it('refetches the visible library once for a burst of hints and shows the new rows', async () => {
    let onEvent: (event: AppEvent) => void = () => undefined;
    api.connectEvents.mockImplementation((handler: (event: AppEvent) => void) => { onEvent = handler; return { close: vi.fn(), onopen: null, onerror: null }; });
    const old = row('old', '2026-10-01T00:00:00Z');
    api.listLibrary.mockResolvedValue({ items: [old], next_cursor: null });
    const { result } = renderHook(() => useAuthenticatedWorkspace({ onError: vi.fn() }));
    await waitFor(() => expect(result.current.state.authStage).toBe('ready'));
    await waitFor(() => expect(api.connectEvents).toHaveBeenCalled());
    const before = api.listLibrary.mock.calls.length;

    api.listLibrary.mockResolvedValue({ items: [row('newest', '2026-10-03T00:00:00Z'), row('newer', '2026-10-02T00:00:00Z'), old], next_cursor: null });
    act(() => { onEvent(hint); onEvent(hint); onEvent({ type: 'library_item_missing', payload: { broadcast: true } }); });
    expect(api.listLibrary.mock.calls.length).toBe(before); // debounced, not one fetch per hint

    await waitFor(() => expect(result.current.state.library.map((item) => item.id)).toEqual(['newest', 'newer', 'old']), { timeout: LIBRARY_HINT_REFETCH_MS + 2000 });
    expect(api.listLibrary.mock.calls.length).toBe(before + 1);
    expect(api.listLibrary).toHaveBeenLastCalledWith({ limit: expect.any(Number) }, { background: true });
  });
});
