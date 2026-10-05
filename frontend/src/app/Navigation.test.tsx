import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { MobileTabs, navGroups, Sidebar } from './Navigation';

describe('Sidebar', () => {
  it('groups Watch and Vault, without Music or the profile', () => {
    expect(navGroups.map((group) => [group.label, group.items.map((item) => item.label)])).toEqual([
      ['Watch', ['Home', 'Library', 'Streaming', 'Requests']],
      ['Vault', ['Downloads', 'Settings']],
    ]);
    render(<Sidebar activeDownloads={2} collapsed={false} mobile={false} onClose={vi.fn()} onNavigate={vi.fn()} open={false} surface="library" />);
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).queryByRole('button', { name: /Music/ })).toBeNull();
    expect(within(nav).getByRole('button', { name: 'Library' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('button', { name: 'Downloads, 2 active' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /settings$/ })).toBeNull();
  });

  it('collapsed on desktop: out of the tab order and the accessibility tree, and back the moment it opens', () => {
    const props = { activeDownloads: 0, mobile: false, onClose: vi.fn(), onNavigate: vi.fn(), open: false, surface: 'home' as const };
    const { container, rerender } = render(<Sidebar {...props} collapsed />);
    const aside = container.querySelector('#primary-navigation')!;
    expect(aside.getAttribute('aria-hidden')).toBe('true');
    expect(aside.hasAttribute('inert')).toBe(true);
    expect(aside.classList.contains('is-hidden')).toBe(true);
    rerender(<Sidebar {...props} collapsed={false} />);
    expect(aside.hasAttribute('aria-hidden')).toBe(false);
    expect(aside.hasAttribute('inert')).toBe(false);
    expect(aside.classList.contains('is-hidden')).toBe(false);
  });

  it('drawer mode: closed is hidden, open is a dialog that focuses Home, Escape closes it', async () => {
    const onClose = vi.fn();
    const props = { activeDownloads: 0, mobile: true, onClose, onNavigate: vi.fn(), surface: 'home' as const };
    const { rerender } = render(<Sidebar {...props} open={false} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    rerender(<Sidebar {...props} open />);
    expect(screen.getByRole('dialog', { name: 'Mobile navigation' })).toBeTruthy();
    await vi.waitFor(() => expect(document.activeElement).toBe(within(screen.getByRole('navigation', { name: 'Primary' })).getByRole('button', { name: 'Home' })));
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe('Requests badge', () => {
  it('shows an admin how many requests are waiting, and nothing at zero', () => {
    const props = { activeDownloads: 0, collapsed: false, mobile: false, onClose: vi.fn(), onNavigate: vi.fn(), open: false, surface: 'home' as const };
    const { rerender } = render(<Sidebar {...props} pendingRequests={3} />);
    expect(screen.getByRole('button', { name: 'Requests, 3 waiting' })).toBeTruthy();
    rerender(<Sidebar {...props} pendingRequests={0} />);
    expect(screen.getByRole('button', { name: 'Requests' })).toBeTruthy();
  });
});

describe('Streaming is active on every Streaming page', () => {
  it.each(['streaming', 'subscriptions'] as const)('highlights Streaming (and no other item) for %s', (surface) => {
    render(<Sidebar activeDownloads={0} collapsed={false} mobile={false} onClose={vi.fn()} onNavigate={vi.fn()} open={false} surface={surface} />);
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).getAllByRole('button').filter((button) => button.getAttribute('aria-current') === 'page').map((button) => button.textContent)).toEqual(['Streaming']);
  });

  it('marks the phone Streaming tab', () => {
    render(<MobileTabs activeDownloads={0} moreOpen={false} onMore={vi.fn()} onNavigate={vi.fn()} onSearch={vi.fn()} surface="streaming" />);
    expect(screen.getByRole('button', { name: 'Streaming' }).getAttribute('aria-current')).toBe('page');
  });
});

describe('MobileTabs', () => {
  it('has Home, Library, Streaming, Search and More; Search opens the palette, More opens the drawer', async () => {
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
