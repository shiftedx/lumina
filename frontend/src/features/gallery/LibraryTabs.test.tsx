import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { librarySections } from '../../test/galleryFixtures';
import type { LibrarySections } from '../../types';
import { forgetStoredSections, type LibraryPlace, LibraryTabs, readStoredSections, useLibrarySections } from './LibraryTabs';

function Harness({ current = 'all', isAdmin = false, onOpen = vi.fn() }: { current?: LibraryPlace; isAdmin?: boolean; onOpen?: (place: LibraryPlace) => void }) {
  const { sections } = useLibrarySections('member-1');
  return <div className="gallery"><LibraryTabs current={current} isAdmin={isAdmin} onOpen={onOpen} sections={sections} /><button type="button">Sort</button></div>;
}
const links = () => within(screen.getByRole('navigation', { name: 'Library' })).queryAllByRole('link');
const names = () => links().map((link) => link.textContent);
const answer = (patch: Partial<LibrarySections> = {}) => vi.spyOn(api, 'getLibrarySections').mockResolvedValue(librarySections(patch));

beforeEach(() => window.localStorage.clear());
afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

describe('LibraryTabs', () => {
  it('shows All and every tab with something to see, in tab order', async () => {
    answer({ anime: 0, recordings: 0 });
    render(<Harness />);
    await waitFor(() => expect(names()).toEqual(['All', 'Movies', 'Shows', 'Music', 'YouTube', 'Collections']));
    expect(links().map((link) => link.getAttribute('href'))).toEqual(['/library', '/library/movies', '/library/shows', '/library/music', '/library/youtube', '/library/collections']);
  });

  it('shows Music for saved audio alone, and hides it with neither albums nor saved audio', async () => {
    answer({ albums: 0, artists: 0, saved_audio: 2 });
    const first = render(<Harness />);
    await waitFor(() => expect(names()).toContain('Music'));
    first.unmount();
    window.localStorage.clear();
    answer({ albums: 0, artists: 0, saved_audio: 0 });
    render(<Harness />);
    await waitFor(() => expect(names()).toEqual(['All', 'Movies', 'Shows', 'Anime', 'YouTube', 'Recordings', 'Collections']));
  });

  it('marks the current tab, and renders no tab for a hidden lens opened by its link', async () => {
    answer({ anime: 0 });
    const first = render(<Harness current="shows" />);
    await waitFor(() => expect(screen.getByRole('link', { name: 'Shows' }).getAttribute('aria-current')).toBe('page'));
    expect(screen.getByRole('link', { name: 'All' }).getAttribute('aria-current')).toBeNull();
    first.unmount();
    render(<Harness current="anime" />);
    await waitFor(() => expect(names()).toContain('Movies'));
    expect(names()).not.toContain('Anime');
    expect(links().some((link) => link.getAttribute('aria-current') === 'page')).toBe(false);
  });

  it('shows the quiet Deleted link to a vault owner, or to a member with deleted files', async () => {
    answer({ deleted: 0 });
    const member = render(<Harness />);
    await waitFor(() => expect(names()).toContain('Movies'));
    expect(names()).not.toContain('Deleted');
    member.unmount();
    render(<Harness current="deleted" isAdmin />);
    await waitFor(() => expect(screen.getByRole('link', { name: 'Deleted' }).getAttribute('href')).toBe('/library/deleted'));
    expect(screen.getByRole('link', { name: 'Deleted' }).getAttribute('aria-current')).toBe('page');
  });

  it('shows Deleted to a member once they have deleted files', async () => {
    answer({ deleted: 3 });
    render(<Harness />);
    await waitFor(() => expect(names()).toContain('Deleted'));
  });

  it('renders at once from the stored answer, then follows the server and stores it', async () => {
    window.localStorage.setItem('lumina.sections.member-1', JSON.stringify(librarySections({ recordings: 12 })));
    let finish: (sections: LibrarySections) => void = () => undefined;
    vi.spyOn(api, 'getLibrarySections').mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<Harness />);
    expect(names()).toContain('Recordings'); // synchronously, before any answer
    finish(librarySections({ recordings: 0 }));
    await waitFor(() => expect(names()).not.toContain('Recordings'));
    expect(readStoredSections('member-1')?.recordings).toBe(0);
  });

  it('ignores a stored value of another shape and shows only All until the answer', () => {
    window.localStorage.setItem('lumina.sections.member-1', JSON.stringify({ movies: 'lots' }));
    vi.spyOn(api, 'getLibrarySections').mockReturnValue(new Promise(() => undefined));
    render(<Harness />);
    expect(names()).toEqual(['All', 'Collections']);
    expect(screen.getByRole('navigation', { name: 'Library' }).className).toBe('g-tabs');
  });

  it('works when localStorage throws', async () => {
    const denied = () => { throw new DOMException('The operation is insecure.', 'SecurityError'); };
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(denied);
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(denied);
    vi.spyOn(window.localStorage, 'removeItem').mockImplementation(denied);
    answer();
    render(<Harness />);
    expect(names()).toEqual(['All', 'Collections']);
    await waitFor(() => expect(names()).toContain('Recordings'));
    expect(readStoredSections('member-1')).toBeNull();
    expect(() => forgetStoredSections('member-1')).not.toThrow();
  });

  it('opens a tab in the app on a plain click and leaves a modified click to the browser', async () => {
    answer();
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const movies = await screen.findByRole('link', { name: 'Movies' });
    expect(fireEvent.click(movies)).toBe(false); // default prevented
    expect(onOpen).toHaveBeenCalledWith('movies');
    onOpen.mockClear();
    expect(fireEvent.click(movies, { ctrlKey: true })).toBe(true);
    expect(fireEvent.click(movies, { metaKey: true })).toBe(true);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('moves along the row with Left and Right, stops at its ends, and leaves it with Down', async () => {
    answer();
    render(<Harness />);
    const all = screen.getByRole('link', { name: 'All' });
    await screen.findByRole('link', { name: 'Movies' });
    all.focus();
    fireEvent.keyDown(all, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(all);
    fireEvent.keyDown(all, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Movies' }));
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Sort' }));
  });

  it('offers a quiet Collections link before Deleted, even when Deleted is hidden', async () => {
    answer({ deleted: 0 });
    const onOpen = vi.fn();
    const first = render(<Harness current="collections" onOpen={onOpen} />);
    await waitFor(() => expect(names()).toContain('Collections'));
    const link = screen.getByRole('link', { name: 'Collections' });
    expect(link.getAttribute('href')).toBe('/library/collections');
    expect(link.getAttribute('aria-current')).toBe('page');
    expect(names()).not.toContain('Deleted');
    fireEvent.click(link);
    expect(onOpen).toHaveBeenCalledWith('collections');
    first.unmount();
    answer({ deleted: 3 });
    render(<Harness />);
    await waitFor(() => expect(names().slice(-2)).toEqual(['Collections', 'Deleted']));
  });
});
