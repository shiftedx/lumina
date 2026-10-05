import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { facets, titlePage } from '../../test/galleryFixtures';
import { FilterDrawer } from './FilterDrawer';
import { DEFAULT_WALL, parseWallQuery, type WallQuery } from './galleryModel';

beforeAll(() => {
  // jsdom has no modal dialog; model the two methods the drawer uses, including the close event.
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) { this.removeAttribute('open'); this.dispatchEvent(new Event('close')); };
});
afterEach(() => vi.restoreAllMocks());

function drawer(query: WallQuery = DEFAULT_WALL, phone = false) {
  vi.spyOn(api, 'getTitleFacets').mockResolvedValue(facets);
  const list = vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([], { total: 12 }));
  const onApply = vi.fn();
  const onClose = vi.fn();
  render(<FilterDrawer onApply={onApply} onClose={onClose} phone={phone} query={query} wall="movies" />);
  return { list, onApply, onClose };
}

describe('FilterDrawer', () => {
  it('lists the facets with counts and counts the pending choice 300 ms after it changes', async () => {
    const { list } = drawer();
    const dialog = screen.getByRole('dialog', { name: 'Filters' });
    expect(dialog.hasAttribute('open')).toBe(true);
    const drama = await screen.findByRole('checkbox', { name: 'Drama 30' });
    expect(screen.getByRole('checkbox', { name: '4K 8' })).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: 'SD 0' })).toBeTruthy();
    expect(screen.getByLabelText('From').getAttribute('placeholder')).toBe('1954');
    expect(screen.getByLabelText('To').getAttribute('placeholder')).toBe('2026');
    await userEvent.click(drama);
    await userEvent.type(screen.getByLabelText('From'), '1990');
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(
      { category: 'movies', sort: 'created', unwatched: false, in_progress: false, favorites: false, genre: ['Drama'], year_from: 1990, year_to: null, resolution: [], limit: 1 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(await screen.findByRole('button', { name: 'Show 12 titles' })).toBeTruthy();
  });

  it('applies the pending choice on Show and hands control back', async () => {
    const { onApply, onClose } = drawer(parseWallQuery('genre=Drama&res=4k&from=1990'));
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Adventure 12' }));
    await userEvent.click(screen.getByRole('button', { name: /^Show/ }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ genre: ['Drama', 'Adventure'], res: ['4k'], from: 1990, to: null }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('clears every facet with Clear all', async () => {
    const { onApply } = drawer(parseWallQuery('unwatched=1&genre=Drama&res=4k&from=1990&to=1999'));
    await screen.findByRole('checkbox', { name: 'Drama 30' });
    await userEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    expect((screen.getByRole('checkbox', { name: 'Drama 30' }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText('From') as HTMLInputElement).value).toBe('');
    await userEvent.click(screen.getByRole('button', { name: /^Show/ }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ unwatched: true, genre: [], res: [], from: null, to: null }));
  });

  it('puts the quick chips first in the phone sheet, where Clear all clears them too', async () => {
    const { onApply } = drawer(parseWallQuery('unwatched=1'), true);
    const first = screen.getByRole('dialog').querySelector('fieldset') as HTMLElement;
    expect(within(first).getByRole('button', { name: 'Unwatched' }).getAttribute('aria-pressed')).toBe('true');
    await userEvent.click(within(first).getByRole('button', { name: 'Favorites' }));
    expect(within(first).getByRole('button', { name: 'Favorites' }).getAttribute('aria-pressed')).toBe('true');
    await userEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    await userEvent.click(screen.getByRole('button', { name: /^Show/ }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ unwatched: false, progress: false, fav: false }));
  });

  it('announces the count politely and explains why genres past the tenth are unavailable', async () => {
    const ten = ['Drama', ...Array.from({ length: 9 }, (_, index) => `G${index}`)].map((genre) => `genre=${genre}`).join('&');
    drawer(parseWallQuery(ten));
    const adventure = await screen.findByRole('checkbox', { name: 'Adventure 12' });
    expect((adventure as HTMLInputElement).disabled).toBe(true);
    expect(adventure.getAttribute('aria-describedby')).toBeTruthy();
    expect(document.getElementById(adventure.getAttribute('aria-describedby') as string)?.textContent).toBe('You can choose up to 10 genres.');
    expect(screen.getByRole('checkbox', { name: 'Drama 30' }).hasAttribute('aria-describedby')).toBe(false);
    const show = await screen.findByRole('button', { name: 'Show 12 titles' });
    expect(show.querySelector('[aria-live="polite"]')?.textContent).toBe('Show 12 titles');
  });

  it('says so when the facets cannot load, and still offers year and resolution', async () => {
    vi.spyOn(api, 'getTitleFacets').mockRejectedValue(new Error('down'));
    vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([], { total: 3 }));
    render(<FilterDrawer onApply={vi.fn()} onClose={vi.fn()} phone={false} query={DEFAULT_WALL} wall="shows" />);
    expect(await screen.findByText('Filters are unavailable right now.')).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: '1080p 0' })).toBeTruthy();
  });

  it('asks the albums facets, counts albums, and has no Resolution section or phone chips', async () => {
    const facetsSpy = vi.spyOn(api, 'getTitleFacets').mockResolvedValue({ ...facets, resolutions: [] });
    const list = vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([], { total: 12 }));
    render(<FilterDrawer onApply={vi.fn()} onClose={vi.fn()} phone query={DEFAULT_WALL} wall="albums" />);
    await screen.findByRole('checkbox', { name: 'Drama 30' });
    expect(facetsSpy).toHaveBeenCalledWith({ type: 'album' }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.queryByRole('group', { name: 'Resolution' })).toBeNull();
    expect(screen.queryByRole('group', { name: 'Show' })).toBeNull();
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ type: 'album', limit: 1 }), expect.anything()));
    expect(await screen.findByRole('button', { name: 'Show 12 albums' })).toBeTruthy();
  });
});
