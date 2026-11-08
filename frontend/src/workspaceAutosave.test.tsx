import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { updateMySettings } from './api';
import type { AppEvent, UserSettings } from './types';
import { useAuthenticatedWorkspace } from './workspace';

const apiMocks = vi.hoisted(() => ({
  connectEvents: vi.fn(),
  getMySettings: vi.fn(),
  getSession: vi.fn(),
  updateMySettings: vi.fn(),
}));

const settings = {
  id: 'settings-one',
  resolved_download_defaults: {
    format_selection: { preset: 'best', output_container: 'mp4', subtitles: false },
    output_profile: { base_path: null, subdir: '', template: '', organize_by: 'downloads' },
  },
  resolved_automation_defaults: {},
  ui_prefs: { sidebar_collapsed: false },
} as unknown as UserSettings;

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  connectEvents: apiMocks.connectEvents,
  getRuntimeHealth: vi.fn().mockResolvedValue({ status: 'ok', version: '1.0.0' }),
  getSession: apiMocks.getSession,
  listJobs: vi.fn().mockResolvedValue([]),
  listLibrary: vi.fn().mockResolvedValue([]),
  getMySettings: apiMocks.getMySettings,
  listAutomations: vi.fn().mockResolvedValue([]),
  updateMySettings: apiMocks.updateMySettings,
}));

const memberOne = { id: 'member-one', username: 'one', display_name: 'One', role: 'viewer', is_active: true };
const memberTwo = { id: 'member-two', username: 'two', display_name: 'Two', role: 'viewer', is_active: true };

function prepareAutosave(settingsSnapshot = settings) {
  apiMocks.connectEvents.mockReset();
  apiMocks.getMySettings.mockReset().mockResolvedValue(settingsSnapshot);
  apiMocks.getSession.mockReset().mockResolvedValue({ user: memberOne });
  apiMocks.updateMySettings.mockReset().mockResolvedValue(settingsSnapshot);
  apiMocks.connectEvents.mockReturnValue({ close: vi.fn(), onopen: null, onerror: null });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => { resolve = nextResolve; reject = nextReject; });
  return { promise, resolve, reject };
}

afterEach(() => vi.useRealTimers());

describe('mounted workspace preference persistence', () => {
  it('autosaves global acquisition defaults through the member settings API', async () => {
    prepareAutosave();
    const { result } = renderHook(() => useAuthenticatedWorkspace({ onError: vi.fn() }));
    await waitFor(() => expect(result.current.state.authStage).toBe('ready'));

    act(() => result.current.dispatch({
      type: 'preferences/patch',
      patch: { formatPreset: 'best_1080p', outputContainer: 'mkv', downloadSubtitles: true, outputFolder: 'family/protected' },
    }));

    await waitFor(() => expect(updateMySettings).toHaveBeenCalledWith(expect.objectContaining({
      download_defaults: expect.objectContaining({
        format_selection: expect.objectContaining({ preset: 'best_1080p', output_container: 'mkv', subtitles: true }),
        output_profile: expect.objectContaining({ base_path: null, subdir: 'family/protected' }),
      }),
    })), { timeout: 2000 });
  });
});

it('saves the chosen music browser through the authenticated member endpoint without replacing sibling ui preferences', async () => {
  const saved = { ...settings, ui_prefs: { ...settings.ui_prefs, music_browse: { view: 'albums', future: true }, home_shelves: [] } };
  prepareAutosave(saved);
  const { result } = renderHook(() => useAuthenticatedWorkspace({ onError: vi.fn() }));
  await waitFor(() => expect(result.current.state.authStage).toBe('ready'));
  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { musicBrowseView: 'artists' } }));
  await waitFor(() => expect(updateMySettings).toHaveBeenCalledWith(expect.objectContaining({ ui_prefs: expect.objectContaining({ music_browse: { view: 'artists', future: true } }) })), { timeout: 2000 });
  const payload = apiMocks.updateMySettings.mock.calls.at(-1)?.[0];
  expect(payload.ui_prefs).not.toHaveProperty('home_shelves');
});

it('serializes an in-flight edit and saves a revert against its acknowledged baseline', async () => {
  prepareAutosave();
  const first = deferred<UserSettings>();
  apiMocks.updateMySettings.mockReturnValueOnce(first.promise).mockResolvedValueOnce(settings);
  const { result } = renderHook(() => useAuthenticatedWorkspace({ onError: vi.fn() }));
  await waitFor(() => expect(result.current.state.authStage).toBe('ready'));
  vi.useFakeTimers();

  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { playerVolume: 0.25 } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { playerVolume: 1 } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  expect(apiMocks.updateMySettings).toHaveBeenCalledTimes(1);

  await act(async () => { first.resolve({ ...settings, ui_prefs: { ...settings.ui_prefs, player_volume: 0.25 } }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(apiMocks.updateMySettings).toHaveBeenCalledTimes(2);
  expect(apiMocks.updateMySettings.mock.calls[1]?.[0]).toEqual({ ui_prefs: { player_volume: 1 } });
});

it('retains a failed draft until a later edit saves it', async () => {
  prepareAutosave();
  const first = deferred<UserSettings>();
  const onError = vi.fn();
  apiMocks.updateMySettings.mockReturnValueOnce(first.promise).mockResolvedValueOnce(settings);
  const { result } = renderHook(() => useAuthenticatedWorkspace({ onError }));
  await waitFor(() => expect(result.current.state.authStage).toBe('ready'));
  vi.useFakeTimers();

  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { playerVolume: 0.4 } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  await act(async () => { first.reject(new Error('save failed')); });
  expect(result.current.state.preferences.playerVolume).toBe(0.4);
  expect(onError).toHaveBeenCalledWith('save failed');

  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { autoplayUpNext: false } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  expect(apiMocks.updateMySettings).toHaveBeenCalledTimes(2);
  expect(apiMocks.updateMySettings.mock.calls[1]?.[0]).toEqual({ ui_prefs: { player_volume: 0.4, autoplay_up_next: false } });
});

it('does not apply a stale save after the member changes', async () => {
  prepareAutosave();
  const first = deferred<UserSettings>();
  const settingsTwo = { ...settings, id: 'settings-two', ui_prefs: { sidebar_collapsed: true } };
  apiMocks.updateMySettings.mockReturnValueOnce(first.promise);
  const { result } = renderHook(() => useAuthenticatedWorkspace({ onError: vi.fn() }));
  await waitFor(() => expect(result.current.state.authStage).toBe('ready'));
  vi.useFakeTimers();

  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { playerVolume: 0.4 } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  apiMocks.getSession.mockResolvedValue({ user: memberTwo });
  apiMocks.getMySettings.mockResolvedValue(settingsTwo);
  await act(async () => { result.current.reset(); await result.current.loadAuthenticated(); });
  await act(async () => { first.resolve({ ...settings, ui_prefs: { ...settings.ui_prefs, player_volume: 0.4 } }); });

  expect(result.current.state.currentUser?.id).toBe(memberTwo.id);
  expect(result.current.state.userSettings?.id).toBe(settingsTwo.id);
});

it('does not leave autosave writing after a same-member reload', async () => {
  prepareAutosave();
  const first = deferred<UserSettings>();
  apiMocks.updateMySettings.mockReturnValueOnce(first.promise).mockResolvedValueOnce(settings);
  const { result } = renderHook(() => useAuthenticatedWorkspace({ onError: vi.fn() }));
  await waitFor(() => expect(result.current.state.authStage).toBe('ready'));
  vi.useFakeTimers();

  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { playerVolume: 0.4 } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  await act(async () => { await result.current.loadAuthenticated(); });
  await act(async () => { first.resolve({ ...settings, ui_prefs: { ...settings.ui_prefs, player_volume: 0.4 } }); });
  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { autoplayUpNext: false } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });

  expect(apiMocks.updateMySettings).toHaveBeenCalledTimes(2);
  expect(apiMocks.updateMySettings.mock.calls[1]?.[0]).toEqual({ ui_prefs: { autoplay_up_next: false } });
});

it('keeps an SSE snapshot and only sends keys changed in this browser', async () => {
  prepareAutosave();
  const first = deferred<UserSettings>();
  let onEvent: ((event: AppEvent) => void) | null = null;
  apiMocks.connectEvents.mockImplementation((handler: (event: AppEvent) => void) => {
    onEvent = handler;
    return { close: vi.fn(), onopen: null, onerror: null };
  });
  apiMocks.updateMySettings.mockReturnValueOnce(first.promise).mockResolvedValueOnce(settings);
  const { result } = renderHook(() => useAuthenticatedWorkspace({ onError: vi.fn() }));
  await waitFor(() => expect(result.current.state.authStage).toBe('ready'));
  await waitFor(() => expect(onEvent).not.toBeNull());
  vi.useFakeTimers();

  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { theme: 'dark' } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  const external = { ...settings, ui_prefs: { ...settings.ui_prefs, home_shelves: [{ kind: 'continue_watching' }] } };
  await act(async () => { onEvent?.({ type: 'settings_updated', payload: { user_id: memberOne.id, settings: external } }); });
  await act(async () => { first.resolve({ ...settings, ui_prefs: { ...settings.ui_prefs, theme: 'dark' } }); });

  expect(result.current.state.userSettings?.ui_prefs.home_shelves).toEqual([{ kind: 'continue_watching' }]);
  act(() => result.current.dispatch({ type: 'preferences/patch', patch: { autoplayUpNext: false } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  expect(apiMocks.updateMySettings.mock.calls[1]?.[0]).toEqual({ ui_prefs: { autoplay_up_next: false } });
});
