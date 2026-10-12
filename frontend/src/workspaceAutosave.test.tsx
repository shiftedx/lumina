import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { updateMySettings } from './api';
import type { UserSettings } from './types';
import { useAuthenticatedWorkspace } from './workspace';

const apiMocks = vi.hoisted(() => ({
  connectEvents: vi.fn(),
  getMySettings: vi.fn(),
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
  getSession: vi.fn().mockResolvedValue({ user: { id: 'member-one', username: 'one', display_name: 'One', role: 'viewer', is_active: true } }),
  listJobs: vi.fn().mockResolvedValue([]),
  listLibrary: vi.fn().mockResolvedValue([]),
  getMySettings: apiMocks.getMySettings,
  listAutomations: vi.fn().mockResolvedValue([]),
  updateMySettings: apiMocks.updateMySettings,
}));

describe('mounted workspace preference persistence', () => {
  it('autosaves global acquisition defaults through the member settings API', async () => {
    apiMocks.connectEvents.mockReturnValue({ close: vi.fn(), onopen: null, onerror: null });
    apiMocks.getMySettings.mockResolvedValue(settings);
    apiMocks.updateMySettings.mockResolvedValue(settings);
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
