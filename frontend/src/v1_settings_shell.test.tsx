import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listUsers: vi.fn(), listInvitations: vi.fn(), listConnectedApps: vi.fn(), listBackups: vi.fn(), listAdminTasks: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
import type { SettingsSectionId } from './app/routes';
const { SettingsSurface } = await import('./features/settings/SettingsSurface');
const { Sidebar } = await import('./app/Navigation');
const { useUnsavedChanges } = await import('./features/settings/unsavedChanges');
const ORDER = ['account', 'appearance', 'discovery', 'streaming', 'playback', 'downloads', 'apps', 'privacy', 'about', 'overview', 'activity', 'members', 'library', 'media', 'requests', 'transcoding', 'ai', 'tasks', 'backups', 'diagnostics'];

afterEach(() => { vi.resetAllMocks(); vi.restoreAllMocks(); });
const base = { id: 'u1', username: 'dana', display_name: 'Dana', is_active: true, bio: '', created_at: '', updated_at: '', onboarding_status: 'completed' } as const;
const admin = { ...base, role: 'admin' as const };
const viewer = { ...base, role: 'viewer' as const };
function shell(props: Partial<Parameters<typeof SettingsSurface>[0]> = {}) {
  api.listConnectedApps.mockResolvedValue([]);
  return render(<SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} user={viewer} {...props} />);
}

describe('Settings shell', () => {
  it('shows a member the You group and a Settings home when no section is chosen', () => {
    shell();
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual(['Profile', 'Display', 'Home & discovery', 'Streaming', 'Playback', 'Downloads', 'Connected apps', 'Privacy & data', 'About']);
    expect(within(nav).queryByText('Server')).toBeNull();
    expect(within(nav).getAllByRole('link').some((link) => link.hasAttribute('aria-current'))).toBe(false);
    const home = screen.getByRole('region', { name: 'All settings' });
    expect(within(home).getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual(['You']);
    const card = within(home).getByRole('link', { name: 'Profile' });
    expect(card.getAttribute('href')).toBe('/settings/account');
    expect(document.getElementById(card.getAttribute('aria-describedby') ?? '')?.textContent).toBe('Your name, sign-in and password.');
    expect(document.querySelector('.g-settings')?.getAttribute('data-view')).toBe('list');
  });

  it('gives a vault owner a Settings home of You, Server and Advanced cards that open their section', async () => {
    const onSection = vi.fn();
    shell({ user: admin, onSection });
    const home = screen.getByRole('region', { name: 'All settings' });
    expect(within(home).getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual(['You', 'Server', 'Advanced']);
    // Transcoding, Tasks and Diagnostics are all advanced rows: they wait for Show advanced settings.
    expect(within(home).getAllByRole('link')).toHaveLength(17);
    expect(within(home).queryByRole('link', { name: 'Transcoding' })).toBeNull();
    await userEvent.click(screen.getByRole('switch', { name: 'Show advanced settings' }));
    expect(within(home).getAllByRole('link')).toHaveLength(20);
    await userEvent.click(within(home).getByRole('link', { name: 'Dashboard' }));
    expect(onSection).toHaveBeenCalledWith('overview');
  });

  it('moves focus to the opened section heading when a home card unmounts', async () => {
    function Host() {
      const [section, setSection] = useState<SettingsSectionId | null>(null);
      return <SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onSection={setSection} section={section} user={viewer} />;
    }
    api.listConnectedApps.mockResolvedValue([]);
    render(<Host />);
    const card = within(screen.getByRole('region', { name: 'All settings' })).getByRole('link', { name: 'Profile' });
    card.focus();
    await userEvent.keyboard('{Enter}');
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2, name: 'Profile' }));
  });

  it('heads a section with its title alone, no kicker (impeccable craft floor)', () => {
    api.listBackups.mockResolvedValue({ backups: [], schedule: { daily: true, keep: 7 } });
    shell({ user: admin, section: 'backups' });
    const region = screen.getByRole('region', { name: 'Backups' });
    expect(region.querySelector('.g-kicker')).toBeNull();
    expect(within(region).getByRole('heading', { level: 2, name: 'Backups' })).toBeTruthy();
  });

  it('opens an all-advanced section by link with a note and a switch, and tags advanced rows', async () => {
    api.listAdminTasks.mockResolvedValue({ items: [], counts: {}, next_cursor: null });
    shell({ user: admin, section: 'tasks' });
    expect(screen.getByRole('note').textContent).toContain('advanced setting');
    expect(document.querySelector('[data-setting-id="tasks.list"] .g-setting-tag')?.textContent).toBe('Advanced');
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getByRole('link', { name: 'Tasks' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).queryByRole('link', { name: 'Diagnostics' })).toBeNull();
    await userEvent.click(within(screen.getByRole('note')).getByRole('switch', { name: 'Show advanced settings' }));
    expect(screen.queryByRole('note')).toBeNull();
    expect(within(nav).getByRole('link', { name: 'Diagnostics' })).toBeTruthy();
  });

  it('refuses a Server deep link for a member, with no admin request', () => {
    shell({ section: 'members' });
    expect(screen.getByRole('status').textContent).toContain('for vault owners');
    expect(api.listUsers).not.toHaveBeenCalled();
  });

  it('gives vault owners both groups, and section links route in-app', async () => {
    api.listUsers.mockResolvedValue([admin]);
    api.listInvitations.mockResolvedValue([]);
    const onSection = vi.fn();
    shell({ user: admin, section: 'members', onSection, advanced: true });
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual(ORDER.map((id) => `/settings/${id}`));
    expect(within(nav).getByRole('link', { name: 'Members' }).getAttribute('aria-current')).toBe('page');
    expect(document.querySelector('.g-settings')?.getAttribute('data-view')).toBe('detail');
    expect(await screen.findByRole('list', { name: 'Household members' })).toBeTruthy();
    await userEvent.click(within(nav).getByRole('link', { name: 'Backups' }));
    expect(onSection).toHaveBeenCalledWith('backups');
    await userEvent.click(screen.getByRole('button', { name: 'All settings' }));
    expect(onSection).toHaveBeenLastCalledWith(null);
  });

  it('opens a member page from the Members list, and the Members link returns to the list', async () => {
    api.listUsers.mockResolvedValue([admin]);
    const onMember = vi.fn();
    const view = shell({ user: admin, section: 'members', onMember });
    await userEvent.click(await within(await screen.findByRole('list', { name: 'Household members' })).findByRole('link', { name: /Dana/ }));
    expect(onMember).toHaveBeenCalledWith('u1');
    view.unmount();
    shell({ user: admin, section: 'members', member: 'u1', onMember });
    expect(await screen.findByRole('heading', { level: 2, name: 'Dana' })).toBeTruthy();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Profile', 'Activity']);
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Settings sections' })).getByRole('link', { name: 'Members' }));
    expect(onMember).toHaveBeenLastCalledWith(null);
  });

  it('asks before leaving a section with unsaved edits, and stays when declined', async () => {
    const onSection = vi.fn();
    function Dirty() { useUnsavedChanges(true, 'Media server'); return null; }
    api.listConnectedApps.mockResolvedValue([]);
    render(<><Dirty /><SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onSection={onSection} section="account" user={viewer} /></>);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    await userEvent.click(screen.getByRole('link', { name: 'Playback' }));
    expect(onSection).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('link', { name: 'Playback' }));
    expect(onSection).toHaveBeenCalledWith('playback');
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it('puts a Settings entry in the primary sidebar for everyone', () => {
    const props = { activeDownloads: 0, mobile: false, onClose: vi.fn(), onNavigate: vi.fn(), open: false, surface: 'home' as const };
    render(<Sidebar {...props} />);
    expect(screen.queryByRole('button', { name: 'Administration' })).toBeNull();
    screen.getByRole('button', { name: 'Settings' }).click();
    expect(props.onNavigate).toHaveBeenCalledWith('settings');
  });
});

describe('editorial Settings shell', () => {
  it('has a Settings masthead, labelled groups, and a gold-ruled current link', () => {
    shell({ section: 'playback' });
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getByText('You', { selector: '.g-label' })).toBeTruthy();
    const current = within(nav).getByRole('link', { name: 'Playback' });
    expect(current.getAttribute('aria-current')).toBe('page');
    expect(current.classList.contains('g-settings-link')).toBe(true);
  });

  it('searches through a labelled field and heads each section editorially', () => {
    shell({ section: 'playback' });
    expect(screen.getByRole('searchbox', { name: 'Search settings' })).toBeTruthy();
    const section = screen.getByRole('region', { name: 'Playback' });
    expect(within(section).getByRole('heading', { level: 2, name: 'Playback' })).toBeTruthy();
  });

  it('keeps the member note for Server sections', () => {
    shell({ section: 'members' });
    expect(screen.getByRole('status').textContent).toContain('This part of Settings is for vault owners.');
  });
});

describe('Settings controls keep their keyboard behaviour', () => {
  it('Enter toggles a Switch in the pane', async () => {
    const onAutoplayUpNextChange = vi.fn();
    shell({ section: 'playback', autoplayUpNext: false, onAutoplayUpNextChange });
    screen.getByRole('switch', { name: 'Autoplay next video' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(onAutoplayUpNextChange).toHaveBeenCalledWith(true);
  });

  it('segmented controls keep their Left and Right', async () => {
    const onThemeChange = vi.fn();
    shell({ section: 'appearance', theme: 'system', onThemeChange });
    screen.getByRole('radio', { name: 'System' }).focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(onThemeChange).toHaveBeenCalledWith('light');
  });
});
