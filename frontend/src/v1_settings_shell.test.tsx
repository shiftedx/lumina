import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listUsers: vi.fn(), listInvitations: vi.fn(), listConnectedApps: vi.fn(), listBackups: vi.fn(), listAdminTasks: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { SettingsSurface } = await import('./features/settings/SettingsSurface');
const { NavigationPopover } = await import('./app/Navigation');
const { useUnsavedChanges } = await import('./features/settings/unsavedChanges');
import type { SettingsLocation } from './features/settings/SettingsSurface';

beforeEach(() => vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false })));
afterEach(() => { vi.resetAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const base = { id: 'u1', username: 'dana', display_name: 'Dana', is_active: true, bio: '', created_at: '', updated_at: '', onboarding_status: 'completed' } as const;
const admin = { ...base, role: 'admin' as const };
const viewer = { ...base, role: 'viewer' as const };
function shell(props: Partial<Parameters<typeof SettingsSurface>[0]> = {}) {
  api.listConnectedApps.mockResolvedValue([]);
  api.listBackups.mockResolvedValue({ backups: [], schedule: { daily: false, keep: 7 } });
  api.listAdminTasks.mockResolvedValue({ items: [], counts: {}, next_cursor: null });
  return render(<SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} user={viewer} {...props} />);
}
const nav = () => screen.getByRole('navigation', { name: 'Settings sections' });
const categoryNames = () => within(nav()).getAllByRole('link').map((link) => link.getAttribute('aria-label') ?? link.textContent);
const personal = ['Profile & security', 'Playback', 'Display', 'Discovery & streaming', 'Downloads', 'Privacy & connected apps', 'About Lumina'];
const server = ['Overview & activity', 'Household', 'Library & storage', 'Connections', 'AI features', 'Maintenance', 'About Lumina'];

describe('flat settings workspaces', () => {
  it('opens playback controls immediately, with all personal areas directly available', () => {
    shell({ onPlaybackMaxHeightChange: vi.fn() });
    expect(categoryNames()).toEqual(personal);
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'Maximum quality' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Captions' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'All settings' })).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Settings page' })).toBeNull();
  });

  it('exposes personal and household areas together for owners without preloading other areas', () => {
    shell({ user: admin });
    expect(categoryNames()).toEqual([...personal.slice(0, -1), ...server]);
    expect(api.listUsers).not.toHaveBeenCalled();
    expect(api.listBackups).not.toHaveBeenCalled();
  });

  it('shows every playback group without another navigation step', async () => {
    shell({ onPlaybackMaxHeightChange: vi.fn() });
    const quality = screen.getByRole('combobox', { name: 'Maximum quality' });
    expect(quality.closest('details')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Captions' })).toBeTruthy();
    await userEvent.click(screen.getByRole('link', { name: 'Subtitles' }));
    expect(document.activeElement?.id).toBe('settings-group-captions');
    expect(screen.getByRole('combobox', { name: 'Maximum quality' })).toBe(quality);
  });

  it('opens an area directly and focuses its heading, with its security inputs already present', async () => {
    function Host() {
      const [location, setLocation] = useState<SettingsLocation>({ section: 'playback', page: 'watching', space: 'personal' });
      return <SettingsSurface {...location} formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onLocation={setLocation} user={viewer} />;
    }
    render(<Host />);
    await userEvent.click(within(nav()).getByRole('link', { name: 'Profile & security' }));
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2, name: 'Profile & security' }));
    expect(screen.getByLabelText('Current password')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Two-step verification' })).toBeTruthy();
  });

  it('keeps legacy server links and advanced controls directly accessible', async () => {
    shell({ user: admin, section: 'tasks' });
    expect(within(nav()).getByRole('link', { name: 'Maintenance' }).getAttribute('aria-current')).toBe('page');
    expect(await screen.findByRole('heading', { name: 'Background tasks' })).toBeTruthy();
    expect(screen.queryByText('Advanced options', { selector: 'summary' })).toBeNull();
  });

  it('refuses owner-only links for a member without fetching admin data', () => {
    shell({ section: 'members' });
    expect(screen.getByRole('status').textContent).toContain('for vault owners');
    expect(api.listUsers).not.toHaveBeenCalled();
    expect(within(nav()).getAllByRole('link').map((link) => link.textContent)).toEqual(personal);
  });

  it('puts members and invitations in the same workspace while addresses remain in Connections', async () => {
    api.listUsers.mockResolvedValue([admin]);
    api.listInvitations.mockResolvedValue([]);
    shell({ user: admin, section: 'members' });
    expect(await screen.findByRole('list', { name: 'Household members' })).toBeTruthy();
    expect(api.listInvitations).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Invite someone' })).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: 'Public address' })).toBeNull();
  });

  it('opens a member and returns to the members list through its category', async () => {
    api.listUsers.mockResolvedValue([admin]);
    const onMember = vi.fn();
    const view = shell({ user: admin, section: 'members', onMember });
    await userEvent.click(await within(await screen.findByRole('list', { name: 'Household members' })).findByRole('link', { name: /Dana/ }));
    expect(onMember).toHaveBeenCalledWith('u1');
    view.unmount();
    shell({ user: admin, section: 'members', member: 'u1', onMember });
    expect(await screen.findByRole('heading', { level: 2, name: 'Dana' })).toBeTruthy();
    await userEvent.click(within(nav()).getByRole('link', { name: 'Household' }));
    expect(onMember).toHaveBeenLastCalledWith(null);
  });

  it('guards leaving an area and search while allowing jumps that retain the same draft', async () => {
    const onLocation = vi.fn();
    function Dirty() { useUnsavedChanges(true, 'Playback'); return null; }
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<><Dirty /><SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onLocation={onLocation} section="playback" user={admin} /></>);
    await userEvent.click(within(nav()).getByRole('link', { name: 'Display' }));
    await userEvent.click(screen.getByRole('link', { name: 'Subtitles' }));
    await userEvent.click(within(nav()).getByRole('link', { name: 'Library & storage' }));
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search settings' }), 'x');
    expect(confirm).toHaveBeenCalledTimes(3);
    expect(onLocation).not.toHaveBeenCalled();
    expect(screen.queryByRole('region', { name: 'Search results' })).toBeNull();
  });

  it('keeps playback tuning visible alongside captions and skipping', () => {
    shell({ section: 'playback', remotePlaybackCache: { enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 } });
    expect(document.getElementById('setting-playback-stream-cache')?.closest('details')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Captions' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Skip automatically' })).toBeTruthy();
  });

  it('puts owner-only Manage Server navigation beside Settings', async () => {
    const onManageServer = vi.fn();
    render(<NavigationPopover activeDownloads={0} onClose={vi.fn()} onNavigate={vi.fn()} onManageServer={onManageServer} open surface="settings" managingServer />);
    const serverButton = screen.getByRole('button', { name: 'Manage Server' });
    expect(serverButton.getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('button', { name: 'My Settings' }).hasAttribute('aria-current')).toBe(false);
    await userEvent.click(serverButton);
    expect(onManageServer).toHaveBeenCalled();
  });
});

describe('Settings keyboard controls', () => {
  it('Enter toggles a Switch in the pane', async () => {
    const onAutoplayUpNextChange = vi.fn();
    shell({ section: 'playback', autoplayUpNext: false, onAutoplayUpNextChange });
    screen.getByRole('switch', { name: 'Autoplay next video' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(onAutoplayUpNextChange).toHaveBeenCalledWith(true);
  });
  it('keeps Left on a settings dropdown instead of moving into navigation', async () => {
    shell({ section: 'playback', onPlaybackMaxHeightChange: vi.fn() });
    const quality = screen.getByRole('combobox', { name: 'Maximum quality' });
    quality.focus();
    await userEvent.keyboard('{ArrowLeft}');
    expect(document.activeElement).toBe(quality);
  });
  it('segmented controls retain their Left and Right behavior', async () => {
    const onThemeChange = vi.fn();
    shell({ section: 'appearance', theme: 'system', onThemeChange });
    screen.getByRole('radio', { name: 'System' }).focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(onThemeChange).toHaveBeenCalledWith('light');
  });
});
