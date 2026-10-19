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
  return render(<SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} user={viewer} {...props} />);
}
const nav = () => screen.getByRole('navigation', { name: 'Settings sections' });
const personal = ['Profile & security', 'Playback', 'Display', 'Discovery & streaming', 'Downloads', 'Privacy & connected apps', 'About Lumina'];
const server = ['Overview & activity', 'Household', 'Library & storage', 'Connections', 'AI features', 'Maintenance', 'About Lumina'];

describe('separate settings spaces', () => {
  it('shows only six personal categories, with About in the footer and no repeated card grid', () => {
    shell();
    expect(within(nav()).getAllByRole('link').map((link) => link.textContent)).toEqual(personal);
    expect(screen.getByRole('heading', { level: 1, name: 'My Settings' })).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Settings spaces' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'All settings' })).toBeNull();
  });

  it('keeps owners’ personal preferences separate from their six server categories', async () => {
    api.listUsers.mockResolvedValue([admin]);
    shell({ user: admin });
    expect(within(nav()).getAllByRole('link').map((link) => link.textContent)).toEqual(personal);
    await userEvent.click(screen.getByRole('link', { name: 'Manage Server' }));
    expect(within(nav()).getAllByRole('link').map((link) => link.textContent)).toEqual(server);
    expect(screen.getByRole('heading', { level: 1, name: 'Manage Server' })).toBeTruthy();
    expect(screen.getByText('Entire household')).toBeTruthy();
  });

  it('opens focused pages and restores focus to the category heading', async () => {
    function Host() {
      const [location, setLocation] = useState<SettingsLocation>({ section: 'playback', page: 'watching', space: 'personal' });
      return <SettingsSurface {...location} formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onLocation={setLocation} user={viewer} />;
    }
    render(<Host />);
    await userEvent.click(within(nav()).getByRole('link', { name: 'Profile & security' }));
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2, name: 'Profile & security' }));
    expect(screen.queryByLabelText('Current password')).toBeNull();
    await userEvent.click(screen.getByRole('link', { name: 'Security' }));
    expect(screen.getByLabelText('Current password')).toBeTruthy();
  });

  it('opens legacy server links in Manage Server without hiding tasks behind a global advanced toggle', async () => {
    api.listAdminTasks.mockResolvedValue({ items: [], counts: {}, next_cursor: null });
    shell({ user: admin, section: 'tasks' });
    expect(screen.getByRole('heading', { level: 1, name: 'Manage Server' })).toBeTruthy();
    expect(within(nav()).getByRole('link', { name: 'Maintenance' }).getAttribute('aria-current')).toBe('page');
    expect(await screen.findByRole('heading', { name: 'Background tasks' })).toBeTruthy();
    expect(screen.queryByRole('switch', { name: 'Show advanced settings' })).toBeNull();
  });

  it('refuses owner-only links for a member without fetching admin data', () => {
    shell({ section: 'members' });
    expect(screen.getByRole('status').textContent).toContain('for vault owners');
    expect(api.listUsers).not.toHaveBeenCalled();
    expect(within(nav()).getAllByRole('link').map((link) => link.textContent)).toEqual(personal);
  });

  it('keeps Members, Invitations and server addresses on distinct pages', async () => {
    api.listUsers.mockResolvedValue([admin]);
    api.listInvitations.mockResolvedValue([]);
    const onLocation = vi.fn();
    shell({ user: admin, section: 'members', onLocation });
    expect(await screen.findByRole('list', { name: 'Household members' })).toBeTruthy();
    expect(api.listInvitations).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox', { name: 'Public address' })).toBeNull();
    await userEvent.click(screen.getByRole('link', { name: 'Invitations' }));
    expect(onLocation).toHaveBeenCalledWith({ section: 'members', page: 'invitations', space: 'server' });
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

  it('guards category, subpage, space and search changes before discarding drafts', async () => {
    const onLocation = vi.fn();
    function Dirty() { useUnsavedChanges(true, 'Playback'); return null; }
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<><Dirty /><SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onLocation={onLocation} section="playback" user={admin} /></>);
    await userEvent.click(within(nav()).getByRole('link', { name: 'Display' }));
    await userEvent.click(screen.getByRole('link', { name: 'Subtitles' }));
    await userEvent.click(screen.getByRole('link', { name: 'Manage Server' }));
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search settings' }), 'x');
    expect(confirm).toHaveBeenCalledTimes(4);
    expect(onLocation).not.toHaveBeenCalled();
    expect(screen.queryByRole('region', { name: 'Search results' })).toBeNull();
  });

  it('keeps advanced playback tuning locally disclosed', async () => {
    shell({ section: 'playback', remotePlaybackCache: { enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 } });
    const row = document.getElementById('setting-playback-stream-cache')!;
    expect(row.closest('details')?.open).toBe(false);
    await userEvent.click(screen.getByText('Advanced options', { selector: 'summary' }));
    expect(row.closest('details')?.open).toBe(true);
    expect(screen.queryByRole('heading', { name: 'Captions' })).toBeNull();
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
