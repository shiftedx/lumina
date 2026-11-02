import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { UserProfile } from '../types';
import type { Surface } from './routes';
import { AppShell } from './AppShell';

const libraryActivity = vi.hoisted(() => ({ useLibraryActivity: vi.fn(() => ({ state: 'loading', activity: null })) }));
vi.mock('./useLibraryActivity', () => libraryActivity);

const user = { id: 'u1', username: 'a', display_name: 'A', role: 'admin', is_active: true } as UserProfile;
function Host({ focused = false, onNavigate = vi.fn(), onManageServer = vi.fn(), surface = 'home' as Surface, managingServer = false, mobile = false } = {}) {
  const [open, setOpen] = useState(false);
  return <AppShell activity={{ state: 'ready', retrying: false, active: 0 }} announcer={null} compact={false} focused={focused} mainRef={createRef()} menuButtonRef={createRef()} mobile={mobile}
    moreItems={null} navigationOpen={open} onAddLink={vi.fn()} onCloseNavigation={() => setOpen(false)} onMenu={() => setOpen((value) => !value)} onNavigate={onNavigate} onSearch={vi.fn()} onSettings={vi.fn()} onManageServer={onManageServer}
    onSignOut={vi.fn()} onTheme={vi.fn()} surface={surface} managingServer={managingServer} theme="system" user={user}><h1>Page</h1></AppShell>;
}

describe('anchored navigation', () => {
  it('lets a focused reader own its controls while retaining the skip link and main landmark', () => {
    render(<Host focused />);
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toBeTruthy();
    expect(screen.getByRole('main', { name: 'Main content' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Open navigation' })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Mobile primary navigation' })).toBeNull();
  });

  it('gives settings one local search and no media tabs while keeping global navigation and Account', async () => {
    const { rerender } = render(<Host surface="settings" mobile />);
    expect(screen.queryByRole('navigation', { name: 'Mobile primary navigation' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Search Lumina/ })).toBeNull();
    expect(screen.getByText('My settings', { exact: true })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open navigation' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /account menu/ })).toBeTruthy();
    rerender(<Host surface="settings" managingServer mobile />);
    expect(screen.getByText('Manage server', { exact: true })).toBeTruthy();
  });

  it('scopes library activity polling to the signed-in member', () => {
    render(<Host />);
    expect(libraryActivity.useLibraryActivity).toHaveBeenCalledWith('u1');
  });

  it('opens by keyboard from the header, focuses Home, and restores the trigger after cancel', async () => {
    render(<Host />);
    const trigger = screen.getByRole('button', { name: 'Open navigation' });
    expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
    trigger.focus();
    await userEvent.keyboard('{Enter}');
    const dialog = screen.getByRole('dialog', { name: 'Navigation' });
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Home' }));
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Navigation' })).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
  });

  it('stays non-modal, toggles from its trigger and dismisses when clicking the page', async () => {
    render(<Host />);
    const trigger = screen.getByRole('button', { name: 'Open navigation' });
    await userEvent.click(trigger);
    const panel = screen.getByRole('dialog', { name: 'Navigation' });
    expect(panel.tagName).toBe('DIV');
    expect(panel.getAttribute('popover')).toBe('manual');
    expect(panel.getAttribute('aria-modal')).toBeNull();
    await userEvent.click(trigger);
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('heading', { name: 'Page' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('closes the menu when opening Video or Manage Server', async () => {
    const onNavigate = vi.fn(); const onManageServer = vi.fn();
    render(<Host onNavigate={onNavigate} onManageServer={onManageServer} />);
    const trigger = screen.getByRole('button', { name: 'Open navigation' });
    await userEvent.click(trigger);
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Navigation' })).getByRole('button', { name: 'All video' }));
    expect(onNavigate).toHaveBeenCalledWith('video');
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('button', { name: 'Manage Server' }));
    expect(onManageServer).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
