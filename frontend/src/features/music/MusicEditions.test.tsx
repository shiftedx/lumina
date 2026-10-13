import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { albumSummary } from '../../test/galleryFixtures';
import { MusicEditions } from './MusicEditions';

const mocks = vi.hoisted(() => ({ editions: vi.fn(), identify: vi.fn() }));
vi.mock('../../api', async (original) => ({ ...(await original<typeof import('../../api')>()), getMusicEditions: mocks.editions, identifyMusic: mocks.identify }));

const owned = albumSummary('owned-deluxe', { name: 'Amber Rooms — Deluxe', year: 2024, artist_name: 'Mara Vale' });
const release = { id: '11111111-2222-4333-8444-555555555555', name: 'Amber Rooms', date: '2024-04-12', country: 'US', edition: 'Deluxe', label: 'Evening Light' };
const page = (extra: Partial<{ owned: typeof owned[]; items: typeof release[]; next_offset: number | null; refreshing: boolean; error: string | null }> = {}) => ({ owned: [owned], items: [release], next_offset: null, refreshing: false, error: null, ...extra });

beforeEach(() => { vi.clearAllMocks(); mocks.editions.mockResolvedValue(page()); mocks.identify.mockResolvedValue({ queued: true }); });
afterEach(() => vi.useRealTimers());

describe('MusicEditions', () => {
  it('opens owned albums inside Lumina and external editions at canonical release URLs', async () => {
    const open = vi.fn();
    render(<MusicEditions albumId="album-1" identified onOpenTitle={open} />);
    await userEvent.click(await screen.findByRole('button', { name: /Amber Rooms — Deluxe/ }));
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ id: 'owned-deluxe' }));
    const external = screen.getByRole('link', { name: /Open Amber Rooms release/ });
    expect(external.getAttribute('href')).toBe(`https://musicbrainz.org/release/${release.id}`);
    expect(screen.queryByRole('button', { name: 'Select edition' })).toBeNull();
  });

  it('shows a truthful unidentified state without rendering public releases', async () => {
    mocks.editions.mockResolvedValue(page({ owned: [], items: [] }));
    render(<MusicEditions albumId="album-unmatched" identified={false} onOpenTitle={vi.fn()} />);
    expect(await screen.findByText(/not matched to a MusicBrainz release group/)).toBeTruthy();
    expect(screen.queryByText('Other known releases')).toBeNull();
  });

  it('drops an old album poll when the album id changes', async () => {
    vi.useFakeTimers();
    mocks.editions.mockImplementation(async (id: string) => id === 'old' ? page({ refreshing: true }) : page({ owned: [albumSummary('new-owned', { name: 'New edition' })], items: [] }));
    const view = render(<MusicEditions albumId="old" identified onOpenTitle={vi.fn()} />);
    await act(async () => { await Promise.resolve(); });
    view.rerender(<MusicEditions albumId="new" identified onOpenTitle={vi.fn()} />);
    await act(async () => { await Promise.resolve(); vi.advanceTimersByTime(6000); await Promise.resolve(); });
    expect(screen.getByText('New edition')).toBeTruthy();
    expect(mocks.editions.mock.calls.filter(([id]) => id === 'old')).toHaveLength(1);
  });

  it('recovers from an actionable load error and paginates with the returned cursor', async () => {
    mocks.editions.mockRejectedValueOnce(new Error('MusicBrainz is unavailable.')).mockResolvedValueOnce(page({ next_offset: 100 })).mockResolvedValueOnce(page({ owned: [], items: [{ ...release, id: '66666666-7777-4888-8999-000000000000', name: 'Second pressing' }] }));
    render(<MusicEditions albumId="album-1" identified onOpenTitle={vi.fn()} />);
    expect((await screen.findByRole('alert')).textContent).toContain('MusicBrainz is unavailable.');
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Load more editions' }));
    await screen.findByText('Second pressing');
    expect(mocks.editions).toHaveBeenLastCalledWith('album-1', 100);
    expect(screen.getByText('Amber Rooms')).toBeTruthy();
  });

  it('requires an explicit admin selection and applies the release UUID while protecting manual edits', async () => {
    const refreshed = vi.fn();
    render(<MusicEditions albumId="album-1" canEdit identified onOpenTitle={vi.fn()} onRefreshed={refreshed} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Select edition' }));
    expect(screen.getByText(/protected manual edits stay protected/)).toBeTruthy();
    expect(mocks.identify).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Use this edition’s metadata' }));
    await waitFor(() => expect(mocks.identify).toHaveBeenCalledWith('album-1', release.id));
    await waitFor(() => expect(refreshed).toHaveBeenCalledTimes(1));
  });
});
