/**
 * Settings grouped into clear sections (Profile/Appearance/Playback/
 * Downloads/Discovery/Data), a display-name edit, "Export my data", and the
 * Sidebar-collapsed localStorage access wrapped in try/catch so a
 * denied/throwing storage (private browsing, quota) never breaks startup.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ exportMyData: vi.fn(), listConnectedApps: vi.fn().mockResolvedValue([]), getTwoFactor: vi.fn().mockResolvedValue({ enabled: false, recovery_codes_left: 0, required: false }), getJellyfinImport: vi.fn().mockResolvedValue({ server: null }), requestJson: vi.fn().mockResolvedValue({ email: null, enabled: true }) }));
vi.mock('./api', () => api);

import { SettingsSurface } from './features/settings/SettingsSurface';
import { createInitialWorkspaceState } from './workspace';
import type { UserProfile } from './types';

afterEach(() => { vi.clearAllMocks(); });

const user: UserProfile = {
  id: 'member-1',
  username: 'alexandria',
  display_name: 'Alexandria',
  role: 'viewer',
  is_active: true,
  onboarding_status: 'completed',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

describe('Settings surface: grouped sections', () => {
  it('test_settings_grouped_sections: renders one heading and one nav link per group', () => {
    render(<SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} user={user} />);
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    for (const [id, label] of [['account', 'Profile'], ['appearance', 'Display'], ['discovery', 'Home & discovery'], ['playback', 'Playback'], ['downloads', 'Downloads'], ['apps', 'Connected apps'], ['privacy', 'Privacy & data'], ['about', 'About']]) {
      expect(within(nav).getByRole('link', { name: label }).getAttribute('href')).toBe(`/settings/${id}`);
    }
    expect(screen.getByRole('region', { name: 'All settings' })).toBeTruthy();
  });

  it('test_profile_display_name_edit: saves a trimmed display name through the callback prop', async () => {
    const browser = userEvent.setup();
    const onUpdateDisplayName = vi.fn().mockResolvedValue(undefined);
    render(<SettingsSurface formatPreset="best" section="account" onFormatChange={vi.fn()} onLogout={vi.fn()} onUpdateDisplayName={onUpdateDisplayName} user={user} />);

    const input = screen.getByLabelText('Display name');
    await browser.clear(input);
    await browser.type(input, '  New Name  ');
    await browser.click(screen.getByRole('button', { name: 'Save name' }));

    await waitFor(() => expect(onUpdateDisplayName).toHaveBeenCalledWith('New Name'));
    await waitFor(() => expect(screen.getByText('Display name updated.')).toBeTruthy());
  });

  it('test_data_export_download: Export my data fetches the bundle and offers a file download', async () => {
    const browser = userEvent.setup();
    api.exportMyData.mockResolvedValue({ exported_at: '2026-01-01T00:00:00Z', user: { username: 'alexandria' } });
    const createObjectURL = vi.fn().mockReturnValue('blob:mock');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    render(<SettingsSurface formatPreset="best" section="privacy" onFormatChange={vi.fn()} onLogout={vi.fn()} user={user} />);
    await browser.click(screen.getByRole('button', { name: /Export my data/ }));

    await waitFor(() => expect(api.exportMyData).toHaveBeenCalled());
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock');

    clickSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  it('offers the streaming quality ceilings with Best available as the default', async () => {
    const browser = userEvent.setup();
    const onPlaybackMaxHeightChange = vi.fn();
    render(<SettingsSurface formatPreset="best" section="playback" onFormatChange={vi.fn()} onLogout={vi.fn()} onPlaybackMaxHeightChange={onPlaybackMaxHeightChange} user={user} />);
    const select = screen.getByRole('combobox', { name: 'Maximum quality' }) as HTMLSelectElement;

    expect([...select.options].map((option) => option.text)).toEqual(['Best available', 'Up to 1440p', 'Up to 1080p', 'Up to 720p', 'Data saver (up to 480p)']);
    expect(select.value).toBe('');
    await browser.selectOptions(select, '480');
    expect(onPlaybackMaxHeightChange).toHaveBeenLastCalledWith(480);
  });
});

describe('Sidebar-collapsed local storage guard', () => {
  it('test_sidebar_storage_denied_falls_back: a throwing localStorage never breaks startup state', () => {
    const throwing: Storage = {
      length: 0,
      clear: () => { throw new Error('denied'); },
      key: () => { throw new Error('denied'); },
      getItem: () => { throw new Error('denied'); },
      removeItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('denied'); },
    };
    vi.stubGlobal('localStorage', throwing);

    expect(() => createInitialWorkspaceState()).not.toThrow();
    expect(createInitialWorkspaceState().preferences.sidebarCollapsed).toBe(true);

    vi.unstubAllGlobals();
  });
});
