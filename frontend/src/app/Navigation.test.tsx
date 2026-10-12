import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { MobileTabs, navGroups, NavigationPopover } from './Navigation';

describe('NavigationPopover', () => {
  it('groups Watch and Vault, without Music or the profile', () => {
    expect(navGroups.map((group) => [group.label, group.items.map((item) => item.label)])).toEqual([
      ['Watch', ['Home', 'Library', 'Streaming', 'Requests']],
      ['Vault', ['Downloads', 'My Settings']],
    ]);
    render(<NavigationPopover activeDownloads={2} onClose={vi.fn()} onNavigate={vi.fn()} open surface="library" />);
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).queryByRole('button', { name: /Music/ })).toBeNull();
    expect(within(nav).getByRole('button', { name: 'Library' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('button', { name: 'Downloads, 2 active' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /settings$/ })).toBeNull();
  });

  it('keeps navigation out of the page until its menu opens, focuses Home and closes on cancel', () => {
    const onClose = vi.fn();
    const props = { activeDownloads: 0, onClose, onNavigate: vi.fn(), surface: 'home' as const };
    const { rerender } = render(<NavigationPopover {...props} open={false} />);
    expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
    rerender(<NavigationPopover {...props} open />);
    const dialog = screen.getByRole('dialog', { name: 'Navigation' });
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Home' }));
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('closes before opening a destination, preserving all the existing badges and destinations', async () => {
    const onClose = vi.fn(); const onNavigate = vi.fn();
    render(<NavigationPopover activeDownloads={0} onClose={onClose} onNavigate={onNavigate} open surface="home" />);
    await userEvent.click(screen.getByRole('button', { name: 'Library' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onNavigate).toHaveBeenCalledWith('library');
    expect(onClose.mock.invocationCallOrder[0]).toBeLessThan(onNavigate.mock.invocationCallOrder[0]);
  });
});

describe('Requests badge', () => {
  it('shows an admin how many requests are waiting, and nothing at zero', () => {
    const props = { activeDownloads: 0, onClose: vi.fn(), onNavigate: vi.fn(), open: true, surface: 'home' as const };
    const { rerender } = render(<NavigationPopover {...props} pendingRequests={3} />);
    expect(screen.getByRole('button', { name: 'Requests, 3 waiting' })).toBeTruthy();
    rerender(<NavigationPopover {...props} pendingRequests={0} />);
    expect(screen.getByRole('button', { name: 'Requests' })).toBeTruthy();
  });
});

describe('Streaming is active on every Streaming page', () => {
  it.each(['streaming', 'subscriptions'] as const)('highlights Streaming (and no other item) for %s', (surface) => {
    render(<NavigationPopover activeDownloads={0} onClose={vi.fn()} onNavigate={vi.fn()} open surface={surface} />);
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).getAllByRole('button').filter((button) => button.getAttribute('aria-current') === 'page').map((button) => button.textContent)).toEqual(['Streaming']);
  });

  it('marks the phone Streaming tab', () => {
    render(<MobileTabs activeDownloads={0} moreOpen={false} onMore={vi.fn()} onNavigate={vi.fn()} onSearch={vi.fn()} surface="streaming" />);
    expect(screen.getByRole('button', { name: 'Streaming' }).getAttribute('aria-current')).toBe('page');
  });
});

describe('MobileTabs', () => {
  it('has Home, Library, Streaming, Search and More; Search opens the palette, More opens the menu', async () => {
    const onSearch = vi.fn(); const onMore = vi.fn();
    render(<MobileTabs activeDownloads={3} moreOpen={false} onMore={onMore} onNavigate={vi.fn()} onSearch={onSearch} surface="home" />);
    const tabs = screen.getByRole('navigation', { name: 'Mobile primary navigation' });
    expect(within(tabs).getAllByRole('button').map((button) => button.textContent)).toEqual(['Home', 'Library', 'Streaming', 'Search', 'More3']);
    expect(within(tabs).getByRole('button', { name: 'Home' }).getAttribute('aria-current')).toBe('page');
    await userEvent.click(within(tabs).getByRole('button', { name: 'Search' }));
    expect(onSearch).toHaveBeenCalledOnce();
    await userEvent.click(within(tabs).getByRole('button', { name: 'More, 3 active downloads' }));
    expect(onMore).toHaveBeenCalledOnce();
  });
});
