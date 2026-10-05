import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { UserProfile } from '../types';
import { MEMBER_PICKER_EVENT, onCommand } from './commands';
import { TopBar, type TopBarProps } from './TopBar';

// The ring size comes from the gallery styles: pinned here so this test does not depend on them.
vi.mock('../features/auth/memberPicker', () => ({ useDeviceRingSize: () => 1 }));

const user = { id: 'u1', username: 'dana', display_name: 'Dana Lee', role: 'admin', is_active: true } as UserProfile;
const props = (patch: Partial<TopBarProps> = {}): TopBarProps => ({
  user, mobile: false, drawer: false, compact: false, menuLabel: 'Close navigation', menuExpanded: true, menuButtonRef: createRef(),
  activity: { state: 'ready', retrying: false, active: 2 }, theme: 'system',
  onMenu: vi.fn(), onMenuKeyDown: vi.fn(), onHome: vi.fn(), onSearch: vi.fn(), onAddLink: vi.fn(), onActivity: vi.fn(), onSettings: vi.fn(), onTheme: vi.fn(), onSignOut: vi.fn(),
  ...patch,
});

describe('TopBar', () => {
  it('has the menu button, a home brand link and a search trigger named with the shortcut', async () => {
    const p = props();
    render(<TopBar {...p} />);
    expect(screen.getByRole('button', { name: 'Close navigation' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('link', { name: 'Lumina' }).getAttribute('href')).toBe('/');
    await userEvent.click(screen.getByRole('button', { name: /^Search Lumina, (⌘K|Ctrl K)$/ }));
    expect(p.onSearch).toHaveBeenCalledOnce();
  });

  it('keeps the activity labels and opens Downloads', async () => {
    const p = props();
    render(<TopBar {...p} />);
    await userEvent.click(screen.getByRole('button', { name: '2 active downloads' }));
    expect(p.onActivity).toHaveBeenCalledOnce();
  });

  it('explains stale activity in a popover', async () => {
    const onActivity = vi.fn();
    render(<TopBar {...props({ activity: { state: 'stale', retrying: false, active: 0 }, onActivity })} />);
    await userEvent.click(screen.getByRole('button', { name: 'Download activity may be out of date' }));
    // jsdom treats the popover as hidden, which empties its computed name: assert the label attribute instead.
    const panel = screen.getByRole('dialog', { hidden: true });
    expect(panel.getAttribute('aria-label')).toBe('Download activity');
    expect(panel.textContent).toContain('Download activity may be out of date');
    await userEvent.click(screen.getByRole('button', { name: 'Open Downloads', hidden: true })); // the way out of a stale state stays reachable
    expect(onActivity).toHaveBeenCalledOnce();
  });

  it('offers the profile menu: switch member, settings, theme and sign out', async () => {
    const p = props({ theme: 'dark' });
    const picker = vi.fn();
    const stop = onCommand(MEMBER_PICKER_EVENT, picker);
    render(<TopBar {...p} />);
    await userEvent.click(screen.getByRole('button', { name: 'Dana Lee, account menu' }));
    expect(screen.getByRole('group', { name: 'Dana Lee · Vault owner', hidden: true })).toBeTruthy();
    expect(screen.getByRole('menuitemradio', { name: /Dark/, hidden: true }).getAttribute('aria-checked')).toBe('true');
    await userEvent.click(screen.getByRole('menuitem', { name: 'Switch member…', hidden: true }));
    expect(picker).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole('button', { name: 'Dana Lee, account menu' }));
    await userEvent.click(screen.getByRole('menuitemradio', { name: /Light/, hidden: true }));
    expect(p.onTheme).toHaveBeenCalledWith('light');
    await userEvent.click(screen.getByRole('button', { name: 'Dana Lee, account menu' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Sign out', hidden: true }));
    expect(p.onSignOut).toHaveBeenCalledOnce();
    stop();
  });

  it('is icon-only for Add link on phones and shows only the sun when compact', () => {
    render(<TopBar {...props({ mobile: true, drawer: true, compact: true })} />);
    expect(screen.getByRole('button', { name: 'Add a link' }).textContent).not.toContain('Add link');
    expect(screen.getByText('Lumina').className).toContain('sr-only');
  });
});
