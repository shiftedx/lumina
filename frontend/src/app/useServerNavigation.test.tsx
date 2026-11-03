import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import * as api from '../api';
import type { ServerNavigation } from '../types';
import { LibraryTabs } from '../features/gallery/LibraryTabs';
import { MobileTabs, NavigationPopover } from './Navigation';
import { useServerNavigation } from './useServerNavigation';

const available: ServerNavigation = { categories: ['movies', 'books', 'recordings'], features: { streaming: false, requests: true } };
const setHidden = (hidden: boolean) => Object.defineProperty(document, 'hidden', { configurable: true, value: hidden });
afterEach(() => { Reflect.deleteProperty(document, 'hidden'); vi.useRealTimers(); vi.restoreAllMocks(); });

it('uses server capabilities across desktop, mobile and library navigation, even with stale section counts', () => {
  const sections = { movies: 0, shows: 5, anime: 0, albums: 4, artists: 1, saved_audio: 0, youtube: 0, recordings: 1, deleted: 0 };
  render(<><NavigationPopover navigation={available} activeDownloads={0} onClose={vi.fn()} onNavigate={vi.fn()} open surface="home" /><MobileTabs navigation={available} activeDownloads={0} moreOpen={false} onMore={vi.fn()} onNavigate={vi.fn()} onSearch={vi.fn()} surface="home" /><LibraryTabs navigation={available} current="all" isAdmin={false} onOpen={vi.fn()} sections={sections} /></>);
  const primary = within(screen.getByRole('navigation', { name: 'Primary' }));
  expect(primary.getByRole('button', { name: 'Movies' })).toBeTruthy();
  expect(primary.getByRole('button', { name: 'Books' })).toBeTruthy();
  expect(primary.queryByRole('button', { name: 'Shows' })).toBeNull();
  expect(primary.queryByRole('button', { name: 'Streaming' })).toBeNull();
  expect(primary.getByRole('button', { name: 'Requests' })).toBeTruthy();
  const mobile = within(screen.getByRole('navigation', { name: 'Mobile primary navigation' }));
  expect(mobile.queryByRole('button', { name: 'Streaming' })).toBeNull();
  expect(mobile.getByRole('button', { name: 'Requests' })).toBeTruthy();
  const library = within(screen.getByRole('navigation', { name: 'Library' }));
  expect(library.getByRole('link', { name: 'Movies' })).toBeTruthy();
  expect(library.queryByRole('link', { name: 'Shows' })).toBeNull();
  expect(library.queryByRole('link', { name: 'Music' })).toBeNull();
  expect(library.getByRole('link', { name: 'Recordings' })).toBeTruthy();
});

it('fails closed for a new member', async () => {
  let resolveSecond: (value: ServerNavigation) => void = () => undefined;
  const fetch = vi.spyOn(api, 'getServerNavigation').mockResolvedValueOnce(available).mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve; })).mockResolvedValueOnce(available);
  function Harness({ member }: { member: string }) {
    const { navigation } = useServerNavigation(member);
    return <span>{navigation?.features.requests ? 'enabled' : 'hidden'}</span>;
  }
  const view = render(<Harness member="one" />);
  await screen.findByText('enabled');
  view.rerender(<Harness member="two" />);
  expect(screen.getByText('hidden')).toBeTruthy();
  resolveSecond({ categories: [], features: { streaming: false, requests: false } });
  await screen.findByText('hidden');
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('does not fetch a signed-out session and recognizes a recordings-only library', async () => {
  const fetch = vi.spyOn(api, 'getServerNavigation').mockResolvedValue(available);
  function Harness() { const { navigation } = useServerNavigation(''); return <span>{navigation ? 'loaded' : 'signed out'}</span>; }
  render(<Harness />);
  expect(screen.getByText('signed out')).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  const { isNavigationSurfaceAvailable } = await import('./useServerNavigation');
  expect(isNavigationSurfaceAvailable({ categories: ['recordings'], features: { streaming: false, requests: false } }, 'video')).toBe(true);
});

it('aborts the navigation request when the member changes', async () => {
  const pending: { signal: AbortSignal | undefined; resolve: (value: ServerNavigation) => void }[] = [];
  const fetch = vi.spyOn(api, 'getServerNavigation').mockImplementation(({ signal } = {}) => new Promise((resolve) => {
    pending.push({ signal, resolve });
  }));
  function Harness({ member }: { member: string }) {
    const { navigation } = useServerNavigation(member);
    return <span>{navigation?.features.requests ? 'enabled' : 'hidden'}</span>;
  }
  const view = render(<Harness member="one" />);
  expect(fetch).toHaveBeenCalledTimes(1);
  view.rerender(<Harness member="two" />);
  expect(pending[0].signal?.aborted).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(2);
  pending[0].resolve(available);
  expect(screen.getByText('hidden')).toBeTruthy();
  pending[1].resolve(available);
  await screen.findByText('enabled');
});

it('runs one navigation catch-up after a hidden interval', async () => {
  vi.useFakeTimers();
  setHidden(false);
  const pending: ((value: ServerNavigation) => void)[] = [];
  const fetch = vi.spyOn(api, 'getServerNavigation').mockImplementation(() => new Promise((resolve) => { pending.push(resolve); }));
  function Harness() {
    const { navigation } = useServerNavigation('one');
    return <span>{navigation?.features.requests ? 'enabled' : 'hidden'}</span>;
  }
  render(<Harness />);
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { pending[0](available); });
  expect(screen.getByText('enabled')).toBeTruthy();

  setHidden(true);
  await act(() => vi.advanceTimersByTimeAsync(30_000));
  expect(fetch).toHaveBeenCalledTimes(1);
  setHidden(false);
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => { pending[1](available); });
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('runs one navigation refresh after a short background visit', async () => {
  const pending: ((value: ServerNavigation) => void)[] = [];
  const fetch = vi.spyOn(api, 'getServerNavigation').mockImplementation(() => new Promise((resolve) => { pending.push(resolve); }));
  function Harness() {
    const { navigation } = useServerNavigation('one');
    return <span>{navigation?.features.requests ? 'enabled' : 'hidden'}</span>;
  }
  render(<Harness />);
  await act(async () => { pending[0](available); });
  expect(screen.getByText('enabled')).toBeTruthy();

  setHidden(true);
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
  setHidden(false);
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => { pending[1](available); });
  expect(fetch).toHaveBeenCalledTimes(2);
});
