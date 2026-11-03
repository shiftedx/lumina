import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { UserProfile } from '../types';
import type { ServerNavigation } from '../types';
import type { Surface } from './routes';
import { AppShell } from './AppShell';

const libraryActivity = vi.hoisted(() => ({ useLibraryActivity: vi.fn(() => ({ state: 'loading', activity: null })) }));
vi.mock('./useLibraryActivity', () => libraryActivity);

const user = { id: 'u1', username: 'a', display_name: 'A', role: 'admin', is_active: true } as UserProfile;
const allNavigation: ServerNavigation = { categories: ['movies', 'tv', 'anime', 'youtube', 'recordings', 'music', 'books', 'photos'], features: { streaming: true, requests: true } };
function Host({ focused = false, navigation = allNavigation, onNavigate = vi.fn(), onManageServer = vi.fn(), onVideoView = vi.fn(), surface = 'home' as Surface, managingServer = false, mobile = false, videoView = undefined as string | undefined } = {}) {
  const [open, setOpen] = useState(false);
  return <AppShell navigation={navigation} activity={{ state: 'ready', retrying: false, active: 0 }} announcer={null} compact={false} focused={focused} mainRef={createRef()} menuButtonRef={createRef()} mobile={mobile}
    moreItems={null} navigationOpen={open} onAddLink={vi.fn()} onCloseNavigation={() => setOpen(false)} onMenu={() => setOpen((value) => !value)} onNavigate={onNavigate} onSearch={vi.fn()} onSettings={vi.fn()} onManageServer={onManageServer}
    onSignOut={vi.fn()} onTheme={vi.fn()} surface={surface} videoView={videoView} onVideoView={onVideoView} managingServer={managingServer} theme="system" user={user}><h1>Page</h1></AppShell>;
}

describe('anchored navigation', () => {
  it('lets a focused reader own its controls while retaining the skip link and main landmark', () => {
    render(<Host focused />);
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toBeTruthy();
    expect(screen.getByRole('main', { name: 'Main content' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Open navigation' })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Mobile primary navigation' })).toBeNull();
  });

  it('reserves the shared header search position for settings without media tabs', async () => {
    const { rerender } = render(<Host surface="settings" mobile />);
    expect(screen.queryByRole('navigation', { name: 'Mobile primary navigation' })).toBeNull();
    expect(screen.getByRole('button', { name: /^Search Lumina/ })).toBeTruthy();
    expect(screen.getByText('My settings', { exact: true })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open navigation' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /account menu/ })).toBeTruthy();
    rerender(<Host surface="settings" managingServer mobile />);
    expect(screen.getByText('Manage server', { exact: true })).toBeTruthy();
  });

  it.each([['movies', 'Movies'], ['shows', 'Shows'], ['anime', 'Anime'], ['youtube', 'YouTube'], ['recordings', 'Recordings']])('names the active %s library in the global header', (view, label) => {
    render(<Host surface="video" videoView={view} />);
    expect(screen.getByRole('banner').textContent).toContain(label);
    expect(screen.getByRole('link', { name: 'Lumina home' })).toBeTruthy();
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

  it('closes the menu when opening an enabled category or Manage Server', async () => {
    const onNavigate = vi.fn(); const onManageServer = vi.fn(); const onVideoView = vi.fn();
    render(<Host onNavigate={onNavigate} onManageServer={onManageServer} onVideoView={onVideoView} />);
    const trigger = screen.getByRole('button', { name: 'Open navigation' });
    await userEvent.click(trigger);
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Navigation' })).getByRole('button', { name: 'Movies' }));
    expect(onVideoView).toHaveBeenCalledWith('movies');
    expect(onNavigate).not.toHaveBeenCalledWith('video');
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('button', { name: 'Manage Server' }));
    expect(onManageServer).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('keeps the mobile category contextual and falls back to the first enabled video library', () => {
    const navigation: ServerNavigation = { categories: ['anime'], features: { streaming: false, requests: false } };
    render(<Host mobile navigation={navigation} surface="video" videoView="movies" />);
    const tabs = screen.getByRole('navigation', { name: 'Mobile primary navigation' });
    expect(within(tabs).getByRole('button', { name: 'Anime' }).getAttribute('aria-current')).toBe('page');
    expect(within(tabs).queryByRole('button', { name: 'Streaming' })).toBeNull();
    expect(within(tabs).queryByRole('button', { name: 'Requests' })).toBeNull();
  });
});
