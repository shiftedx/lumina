import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './api';
import { forgetAllStore } from './features/gallery/allStore';
import { forgetWallStores } from './features/gallery/wallPages';
import { LibraryBrowser, type LibraryBrowserProps } from './features/library/LibraryBrowser';
import { librarySections, stillItem, titlePage } from './test/galleryFixtures';
import { ToastProvider } from './ui';
import type { TitleSummary } from './types';

const member = { id: 'member-1', role: 'viewer' };

function browser(overrides: Partial<LibraryBrowserProps> = {}) {
  const props: LibraryBrowserProps = { currentUser: member, onItemChanged: vi.fn(), onOpenTitle: vi.fn(), onPlay: vi.fn(), onPlayAt: vi.fn(), onViewChange: vi.fn(), onWallChange: vi.fn(), view: 'all', ...overrides };
  return { props, ...render(<ToastProvider><LibraryBrowser {...props} /></ToastProvider>) };
}
const series = (id: string, name: string): TitleSummary => ({ id, type: 'series', name, year: 2021, genres: [], added_at: '2026-09-01T00:00:00Z', poster_url: null, user_data: { played: false, is_favorite: false, position_seconds: 0, unplayed_count: 3 } });

beforeEach(() => {
  forgetAllStore();
  forgetWallStores();
  window.localStorage.clear();
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} unobserve() {} });
  vi.spyOn(api, 'getLibrarySections').mockResolvedValue(librarySections({ anime: 0 }));
  vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([]));
  vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: [], next_cursor: null });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('Library lenses', () => {
  it('opens on the All landing under the tab row, and hands tab clicks to the shell', async () => {
    const { props } = browser();
    expect(screen.getByRole('heading', { level: 1, name: 'Library' })).toBeTruthy();
    await userEvent.click(await within(screen.getByRole('navigation', { name: 'Library' })).findByRole('link', { name: 'Shows' }));
    expect(props.onViewChange).toHaveBeenCalledWith('shows');
  });

  it('test_library_series_and_unclassified: the Shows lens shows one poster per series and opens its page', async () => {
    const harbor = series('series-1', 'Blue Harbor');
    const titles = vi.mocked(api.listTitles).mockResolvedValue({ items: [harbor], next_cursor: null, total: 1, start_index: 0 });
    const { props } = browser({ view: 'shows' });
    expect(screen.getByRole('navigation', { name: 'Library' })).toBeTruthy();
    await userEvent.click(await screen.findByRole('button', { name: 'Blue Harbor, 3 unwatched episodes' }));
    expect(props.onOpenTitle).toHaveBeenCalledWith(harbor);
    expect(titles).toHaveBeenCalledWith(expect.objectContaining({ category: 'shows', sort: 'created', cursor: null, limit: 60 }), expect.anything());
    expect(screen.queryByRole('combobox', { name: 'Source' })).toBeNull();
  });

  it('title posters in the Shows lens are reachable by arrow keys, no Tab needed', async () => {
    vi.mocked(api.listTitles).mockResolvedValue({ items: [series('series-1', 'Blue Harbor'), series('series-2', 'Arcadia')], next_cursor: null, total: 2, start_index: 0 });
    browser({ view: 'shows' });
    const card = await screen.findByRole('button', { name: 'Blue Harbor, 3 unwatched episodes' });
    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowRight' });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Arcadia, 3 unwatched episodes' })));
  });

  it('mounts YouTube and Recordings as still walls, counted from sections', async () => {
    const list = vi.mocked(api.listLibrary).mockResolvedValue({ items: [stillItem()], next_cursor: null });
    const youtube = browser({ view: 'youtube' });
    expect(await screen.findByText('318 videos · Sorted by recently added')).toBeTruthy();
    expect(await screen.findByRole('button', { name: /^Harbor walk at dawn, / })).toBeTruthy();
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ kind: 'video', limit: 60 }));
    youtube.unmount();
    browser({ view: 'recordings' });
    expect(await screen.findByText('12 recordings · Sorted by recently added')).toBeTruthy();
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'recording' }));
  });

  it('mounts the Music tab under the tab row', () => {
    browser({ view: 'music' });
    expect(screen.getByRole('heading', { level: 1, name: 'Music' })).toBeTruthy();
    expect(screen.getByRole('navigation', { name: 'Library' })).toBeTruthy();
  });

  it('lists deleted files in the gallery and restores one', async () => {
    const gone = stillItem('gone', { title: 'Old clip', status: 'missing', media_state: 'quarantined', user_id: 'member-1' });
    vi.mocked(api.listLibrary).mockResolvedValue({ items: [gone], next_cursor: null });
    const restore = vi.spyOn(api, 'restoreLibraryFile').mockResolvedValue({ ...gone, status: 'available', media_state: 'available' });
    const { props } = browser({ view: 'deleted' });
    expect(screen.getByRole('heading', { level: 1, name: 'Deleted' })).toBeTruthy();
    expect(screen.getByText("Files deleted from Lumina's storage wait here until the recovery window ends, then they are removed for good.")).toBeTruthy();
    expect(document.querySelector('.g-kicker')).toBeNull();
    await userEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    expect(restore).toHaveBeenCalledWith('gone');
    expect(await screen.findByText('Restored “Old clip”.')).toBeTruthy();
    await waitFor(() => expect(screen.queryByText('Old clip', { selector: '.g-deleted-title' })).toBeNull());
    expect(props.onItemChanged).toHaveBeenCalledWith(expect.objectContaining({ status: 'available' }));
    expect(api.listLibrary).toHaveBeenCalledWith(expect.objectContaining({ status: 'missing' }));
  });

  it('restore is busy, succeeds once, and a failure toasts the reason', async () => {
    const gone = stillItem('gone', { title: 'Old clip', status: 'missing', media_state: 'quarantined', user_id: 'member-1' });
    vi.mocked(api.listLibrary).mockResolvedValue({ items: [gone], next_cursor: null });
    let fail: (error: Error) => void = () => {};
    const restore = vi.spyOn(api, 'restoreLibraryFile').mockReturnValue(new Promise((_, reject) => { fail = reject; }));
    browser({ view: 'deleted' });
    const button = await screen.findByRole('button', { name: /Restore/ });
    await userEvent.click(button);
    await userEvent.click(button);
    expect(restore).toHaveBeenCalledTimes(1);
    expect(button.getAttribute('aria-busy')).toBe('true');
    fail(new Error('the file is no longer on disk'));
    expect((await screen.findByRole('alert')).textContent).toContain('Restore failed: the file is no longer on disk');
    expect(screen.getByText('Old clip', { selector: '.g-deleted-title' })).toBeTruthy();
  });

  it('says so when nothing is deleted', async () => {
    browser({ view: 'deleted' });
    expect(await screen.findByText('Nothing deleted.')).toBeTruthy();
  });
});
