import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { librarySections } from '../../test/galleryFixtures';
import type { LibrarySections } from '../../types';
import { forgetStoredSections, LibraryTabs, readStoredSections, useLibrarySections } from './LibraryTabs';

function Harness({ current = 'movies', isAdmin = false, onOpen = vi.fn() }: Partial<Parameters<typeof LibraryTabs>[0]>) {
  const { sections } = useLibrarySections('member-1');
  return <div className="gallery"><LibraryTabs current={current} isAdmin={isAdmin} onOpen={onOpen} sections={sections} /><button type="button">Refresh</button></div>;
}
const links = () => within(screen.getByRole('navigation', { name: 'Library tools' })).getAllByRole('link');
const names = () => links().map((link) => link.textContent);
const answer = (patch: Partial<LibrarySections> = {}) => vi.spyOn(api, 'getLibrarySections').mockResolvedValue(librarySections(patch));

beforeEach(() => window.localStorage.clear());
afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

describe('Library tools', () => {
  it('shows only Collections and Deleted for an owner, with the active destination marked', async () => {
    answer({ deleted: 0 });
    render(<Harness current="deleted" isAdmin />);
    expect(names()).toEqual(['Collections', 'Deleted']);
    expect(links().map((link) => link.getAttribute('href'))).toEqual(['/library/collections', '/library/deleted']);
    expect(screen.getByRole('link', { name: 'Deleted' }).getAttribute('aria-current')).toBe('page');
  });

  it('shows Deleted to a member once they have deleted files', async () => {
    answer({ deleted: 3 });
    render(<Harness current="collections" />);
    expect(names()).toEqual(['Collections']);
    expect(screen.getByRole('link', { name: 'Collections' }).getAttribute('aria-current')).toBe('page');
    await waitFor(() => expect(names()).toEqual(['Collections', 'Deleted']));
  });

  it('renders at once from the stored answer, then follows the server and stores it', async () => {
    window.localStorage.setItem('lumina.sections.member-1', JSON.stringify(librarySections({ deleted: 12 })));
    let finish: (sections: LibrarySections) => void = () => undefined;
    vi.spyOn(api, 'getLibrarySections').mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<Harness />);
    expect(names()).toContain('Deleted');
    finish(librarySections({ deleted: 0 }));
    await waitFor(() => expect(names()).not.toContain('Deleted'));
    expect(readStoredSections('member-1')?.deleted).toBe(0);
  });

  it('ignores a stored value of another shape until the server answers', () => {
    window.localStorage.setItem('lumina.sections.member-1', JSON.stringify({ deleted: 2 }));
    vi.spyOn(api, 'getLibrarySections').mockReturnValue(new Promise(() => undefined));
    render(<Harness />);
    expect(names()).toEqual(['Collections']);
  });

  it('works when localStorage throws', async () => {
    const denied = () => { throw new DOMException('The operation is insecure.', 'SecurityError'); };
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(denied);
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(denied);
    vi.spyOn(window.localStorage, 'removeItem').mockImplementation(denied);
    answer({ deleted: 2 });
    render(<Harness />);
    expect(names()).toEqual(['Collections']);
    await waitFor(() => expect(names()).toContain('Deleted'));
    expect(readStoredSections('member-1')).toBeNull();
    expect(() => forgetStoredSections('member-1')).not.toThrow();
  });

  it('follows a plain click in the app and leaves a modified click to the browser', () => {
    answer();
    const onOpen = vi.fn();
    render(<Harness onOpen={onOpen} />);
    const collections = screen.getByRole('link', { name: 'Collections' });
    expect(fireEvent.click(collections)).toBe(false);
    expect(onOpen).toHaveBeenCalledWith('collections');
    onOpen.mockClear();
    expect(fireEvent.click(collections, { ctrlKey: true })).toBe(true);
    expect(fireEvent.click(collections, { metaKey: true })).toBe(true);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('moves along the row with Left and Right, stops at its ends, and leaves it with Down', () => {
    answer();
    render(<Harness isAdmin />);
    const collections = screen.getByRole('link', { name: 'Collections' });
    collections.focus();
    fireEvent.keyDown(collections, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(collections);
    fireEvent.keyDown(collections, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Deleted' }));
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Refresh' }));
  });

  it('refreshes visible library sections when returning to the app after server changes', async () => {
    const fetch = vi.spyOn(api, 'getLibrarySections').mockResolvedValueOnce(librarySections({ deleted: 0 })).mockResolvedValueOnce(librarySections({ deleted: 2 }));
    render(<Harness />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(screen.queryByRole('link', { name: 'Deleted' })).toBeNull();
    fireEvent(document, new Event('visibilitychange'));
    await screen.findByRole('link', { name: 'Deleted' });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
