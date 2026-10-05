import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { PlaybackProgress } from './types';
import { useAuthenticatedWorkspace } from './workspace';

const fixtures = vi.hoisted(() => ({
  settings: {
    id: 'settings-one',
    resolved_download_defaults: {
      format_selection: { preset: 'best', output_container: 'mp4', subtitles: false },
      output_profile: { base_path: null, subdir: '', template: '', organize_by: 'downloads' },
    },
    resolved_automation_defaults: {},
    ui_prefs: {},
  },
  continueWatching: [
    { id: 'pb-1', item_id: 'lib-1', item: { id: 'lib-1', title: 'Kept film', status: 'available' }, position_seconds: 120, duration_seconds: 600, completed: false },
  ],
}));

const continueWatching = fixtures.continueWatching as unknown as PlaybackProgress[];

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  connectEvents: vi.fn().mockReturnValue({ close: vi.fn(), onopen: null, onerror: null }),
  getRuntimeHealth: vi.fn().mockResolvedValue({ status: 'ok', version: '1.0.0' }),
  getSession: vi.fn().mockResolvedValue({ user: { id: 'member-one', username: 'one', display_name: 'One', role: 'viewer', is_active: true } }),
  listJobs: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
  listLibrary: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
  getMySettings: vi.fn().mockResolvedValue(fixtures.settings),
  listAutomations: vi.fn().mockResolvedValue([]),
  listContinueWatching: vi.fn().mockResolvedValue(fixtures.continueWatching),
}));

describe('continue watching preload', () => {
  it('carries continue watching in the authenticated bundle so it is present at first ready paint', async () => {
    const snapshots: Array<{ stage: string; count: number }> = [];
    const { result } = renderHook(() => {
      const workspace = useAuthenticatedWorkspace({ onError: vi.fn() });
      snapshots.push({ stage: workspace.state.authStage, count: workspace.state.continueWatching.length });
      return workspace;
    });

    await waitFor(() => expect(result.current.state.authStage).toBe('ready'));
    expect(result.current.state.continueWatching).toEqual(continueWatching);

    // The shelf must exist the moment Home can first paint: there must be no
    // render where the session is ready but continue watching is still empty
    // (which would insert the shelf later and shift the layout).
    const firstReady = snapshots.find((snapshot) => snapshot.stage === 'ready');
    expect(firstReady?.count).toBe(continueWatching.length);
  });
});
