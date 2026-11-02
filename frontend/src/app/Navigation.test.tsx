import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { ServerNavigation } from '../types';
import { MobileTabs, navGroups, NavigationPopover } from './Navigation';

const enabledNavigation: ServerNavigation = {
  categories: ['movies', 'tv', 'anime', 'youtube', 'recordings', 'music', 'books', 'photos'],
  features: { streaming: true, requests: true },
};

describe('NavigationPopover', () => {
  it('makes each enabled video library a primary destination, without an aggregate All video item', () => {
    expect(navGroups.map((group) => [group.label, group.items.map((item) => item.label)])).toEqual([
      ['Watch', ['Home', 'Movies', 'Shows', 'Anime', 'YouTube', 'Recordings', 'Music', 'Books', 'Photos', 'Streaming', 'Requests']],
      ['Vault', ['Downloads', 'My Settings']],
    ]);
    render(<NavigationPopover navigation={enabledNavigation} activeDownloads={2} onClose={vi.fn()} onNavigate={vi.fn()} open surface="audio" />);
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).queryByRole('button', { name: 'Library' })).toBeNull();
    expect(within(nav).queryByRole('button', { name: 'All video' })).toBeNull();
    expect(within(nav).getByRole('button', { name: 'Photos' })).toBeTruthy();
    expect(within(nav).getByRole('button', { name: 'Music' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('button', { name: 'Downloads, 2 active' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /settings$/ })).toBeNull();
  });

  it('keeps navigation out of the page until its menu opens, focuses Home and closes on cancel', () => {
    const onClose = vi.fn();
    const props = { navigation: enabledNavigation, activeDownloads: 0, onClose, onNavigate: vi.fn(), surface: 'home' as const };
    const { rerender } = render(<NavigationPopover {...props} open={false} />);
    expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
    rerender(<NavigationPopover {...props} open />);
    const dialog = screen.getByRole('dialog', { name: 'Navigation' });
    within(dialog).getByRole('button', { name: 'Home' }).focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('closes before opening an enabled category destination', async () => {
    const onClose = vi.fn(); const onNavigate = vi.fn(); const onVideoView = vi.fn();
    render(<NavigationPopover navigation={enabledNavigation} activeDownloads={0} onClose={onClose} onNavigate={onNavigate} onVideoView={onVideoView} open surface="home" />);
    await userEvent.click(screen.getByRole('button', { name: 'Anime' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onVideoView).toHaveBeenCalledWith('anime');
    expect(onNavigate).not.toHaveBeenCalled();
    expect(onClose.mock.invocationCallOrder[0]).toBeLessThan(onVideoView.mock.invocationCallOrder[0]);
  });

  it('uses the member-visible server configuration to hide disabled category destinations', () => {
    const restricted: ServerNavigation = { categories: ['movies', 'youtube'], features: { streaming: false, requests: true } };
    render(<NavigationPopover navigation={restricted} activeDownloads={0} onClose={vi.fn()} onNavigate={vi.fn()} open surface="video" videoView="movies" />);
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).getByRole('button', { name: 'Movies' })).toBeTruthy();
    expect(within(nav).getByRole('button', { name: 'YouTube' })).toBeTruthy();
    expect(within(nav).queryByRole('button', { name: /^(Shows|Anime|Recordings|All video|Streaming)$/ })).toBeNull();
    expect(within(nav).getByRole('button', { name: 'Requests' })).toBeTruthy();
  });
});

describe('Requests badge', () => {
  it('shows an admin how many requests are waiting, and nothing at zero', () => {
    const props = { navigation: enabledNavigation, activeDownloads: 0, onClose: vi.fn(), onNavigate: vi.fn(), open: true, surface: 'home' as const };
    const { rerender } = render(<NavigationPopover {...props} pendingRequests={3} />);
    expect(screen.getByRole('button', { name: 'Requests, 3 waiting' })).toBeTruthy();
    rerender(<NavigationPopover {...props} pendingRequests={0} />);
    expect(screen.getByRole('button', { name: 'Requests' })).toBeTruthy();
  });
});

describe('Streaming is active on every Streaming page', () => {
  it.each(['streaming', 'subscriptions'] as const)('highlights Streaming (and no other item) for %s', (surface) => {
    render(<NavigationPopover navigation={enabledNavigation} activeDownloads={0} onClose={vi.fn()} onNavigate={vi.fn()} open surface={surface} />);
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).getAllByRole('button').filter((button) => button.getAttribute('aria-current') === 'page').map((button) => button.textContent)).toEqual(['Streaming']);
  });

  it('keeps Streaming in the full navigation when it is not a phone tab', () => {
    render(<NavigationPopover navigation={enabledNavigation} activeDownloads={0} onClose={vi.fn()} onNavigate={vi.fn()} open surface="streaming" />);
    expect(screen.getByRole('button', { name: 'Streaming' }).getAttribute('aria-current')).toBe('page');
  });
});

describe('MobileTabs', () => {
  it('has Home, Video, Audio, Books and More; each media surface navigates directly', async () => {
    const onNavigate = vi.fn(); const onMore = vi.fn();
    render(<MobileTabs navigation={enabledNavigation} activeDownloads={3} moreOpen={false} onMore={onMore} onNavigate={onNavigate} onSearch={vi.fn()} surface="audio" />);
    const tabs = screen.getByRole('navigation', { name: 'Mobile primary navigation' });
    expect(within(tabs).getAllByRole('button').map((button) => button.textContent)).toEqual(['Home', 'Music', 'Streaming', 'Requests', 'Browse3']);
    expect(within(tabs).getByRole('button', { name: 'Music' }).getAttribute('aria-current')).toBe('page');
    await userEvent.click(within(tabs).getByRole('button', { name: 'Requests' }));
    expect(onNavigate).toHaveBeenCalledWith('requests');
    await userEvent.click(within(tabs).getByRole('button', { name: 'Browse, 3 active downloads' }));
    expect(onMore).toHaveBeenCalledOnce();
  });

  it.each(['video', 'audio', 'books'] as const)('marks %s as the current mobile media surface', (surface) => {
    render(<MobileTabs navigation={enabledNavigation} activeDownloads={0} moreOpen={false} onMore={vi.fn()} onNavigate={vi.fn()} onSearch={vi.fn()} surface={surface} />);
    const label = surface === 'video' ? 'Movies' : surface === 'audio' ? 'Music' : 'Books';
    expect(screen.getByRole('button', { name: label }).getAttribute('aria-current')).toBe('page');
  });
});

it('opens category routes and only dismisses mouse navigation after its grace period', () => {
  vi.useFakeTimers();
  const onClose = vi.fn(); const onVideoView = vi.fn();
  const { unmount } = render(<NavigationPopover navigation={enabledNavigation} activeDownloads={0} onClose={onClose} onNavigate={vi.fn()} onVideoView={onVideoView} open surface="video" videoView="shows" />);
  const panel = screen.getByRole('dialog', { name: 'Navigation' });
  expect(screen.getByRole('button', { name: 'Shows' }).getAttribute('aria-current')).toBe('page');
  fireEvent.click(screen.getByRole('button', { name: 'Movies' }));
  expect(onVideoView).toHaveBeenCalledWith('movies');
  onClose.mockClear();
  fireEvent.pointerLeave(panel, { pointerType: 'mouse' });
  vi.advanceTimersByTime(699); expect(onClose).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1); expect(onClose).toHaveBeenCalledOnce();
  onClose.mockClear();
  panel.showPopover();
  screen.getByRole('button', { name: 'Home' }).focus();
  fireEvent.keyDown(panel, { key: 'ArrowDown' });
  fireEvent.pointerLeave(panel, { pointerType: 'mouse' });
  vi.advanceTimersByTime(700); expect(onClose).not.toHaveBeenCalled();
  fireEvent.keyDown(panel, { key: 'Escape' }); expect(onClose).toHaveBeenCalledOnce();
  unmount(); vi.useRealTimers();
});
