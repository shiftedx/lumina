import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TitleDetail, TitleSummary, TitleUserData } from './types';

const api = vi.hoisted(() => ({
  getTitle: vi.fn(), listSeasonEpisodes: vi.fn(), listSimilarTitles: vi.fn(), setTitleWatched: vi.fn(), setFavorite: vi.fn(),
  getRecap: vi.fn(), requestRecap: vi.fn(), addToWatchQueue: vi.fn(), getWatchQueue: vi.fn(),
  searchTitleMatches: vi.fn(), identifyTitle: vi.fn(), unmatchTitle: vi.fn(), refreshTitleMetadata: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));

const { IdentifyDialog } = await import('./features/titles/IdentifyDialog');

beforeAll(() => {
  // jsdom has no modal dialog; model the two methods the component uses.
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
afterEach(() => vi.resetAllMocks());

export const userData = (patch: Partial<TitleUserData> = {}): TitleUserData => ({ played: false, is_favorite: false, position_seconds: 0, ...patch });
export const summary = (patch: Partial<TitleSummary>): TitleSummary => ({ id: 't', type: 'movie', name: 'Title', genres: [], added_at: '2026-09-01T00:00:00Z', user_data: userData(), ...patch });
export const detail = (patch: Partial<TitleDetail>): TitleDetail => ({ ...summary({}), studios: [], provider_ids: {}, people: [], versions: [], extras: [], children: [], has_recap: false, ...patch });

describe('IdentifyDialog', () => {
  it('searches with the prefilled name and year, chooses a match, and closes', async () => {
    api.searchTitleMatches.mockResolvedValue([{ tmdb_id: 329865, name: 'Arrival', year: 2016, overview: 'Linguist meets visitors.', score: 0.93 }]);
    api.identifyTitle.mockResolvedValue({ title_id: 'm1' });
    const onChanged = vi.fn();
    const onClose = vi.fn();
    render(<IdentifyDialog onChanged={onChanged} onClose={onClose} title={{ id: 'm1', name: 'Arival', year: 2016, type: 'movie' }} />);
    const dialog = screen.getByRole('dialog', { name: 'Fix match' });
    expect(dialog.hasAttribute('open')).toBe(true);
    await waitFor(() => expect(api.searchTitleMatches).toHaveBeenCalledWith('m1', 'Arival', 2016));
    await userEvent.clear(within(dialog).getByLabelText('Name'));
    await userEvent.type(within(dialog).getByLabelText('Name'), 'Arrival');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(api.searchTitleMatches).toHaveBeenLastCalledWith('m1', 'Arrival', 2016));
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Choose Arrival (2016)' }));
    expect(api.identifyTitle).toHaveBeenCalledWith('m1', 329865);
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith('Matched to Arrival. Details and artwork update in a moment.'));
    expect(onClose).toHaveBeenCalled();
  });

  it('is a Dialog: Escape closes it and focus returns to the opener', async () => {
    api.searchTitleMatches.mockResolvedValue([]);
    const opener = document.createElement('button');
    document.body.append(opener);
    opener.focus();
    const onClose = vi.fn();
    const view = render(<IdentifyDialog onChanged={vi.fn()} onClose={onClose} title={{ id: 'm1', name: 'Arrival', year: 2016, type: 'movie' }} />);
    const dialog = screen.getByRole('dialog', { name: 'Fix match' });
    expect(dialog.classList.contains('g-dialog')).toBe(true);
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(onClose).toHaveBeenCalled();
    view.unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('reports a failed search, offers Unmatch, and closes on Escape', async () => {
    api.searchTitleMatches.mockRejectedValue(new Error('TMDB is not configured'));
    api.unmatchTitle.mockResolvedValue({ title_id: 's1' });
    const onClose = vi.fn();
    const onChanged = vi.fn();
    render(<IdentifyDialog onChanged={onChanged} onClose={onClose} title={{ id: 's1', name: 'Show', year: null, type: 'series' }} />);
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'TMDB is not configured');
    await userEvent.click(screen.getByRole('button', { name: 'Unmatch' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith('Match removed. Lumina now uses the file and NFO details only.'));
    onClose.mockClear();
    fireEvent(screen.getByRole('dialog', { hidden: true }), new Event('cancel', { cancelable: true }));
    expect(onClose).toHaveBeenCalled();
  });
});
