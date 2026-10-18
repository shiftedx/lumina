import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { albumSummary, artistSummary, titleDetail, titlePage } from '../../test/galleryFixtures';
import { AudioSoundstage, ALBUM_SOURCE_LIMIT, SHOWCASE_LIMIT } from './AudioSoundstage';
import { AudioSurface } from './AudioSurface';

const mocks = vi.hoisted(() => ({ list: vi.fn(), title: vi.fn() }));
vi.mock('../../api', async (original) => ({ ...(await original<typeof import('../../api')>()), listTitles: mocks.list, getTitle: mocks.title }));
vi.mock('../gallery/GalleryArt', () => ({ GalleryArt: ({ card, art, className }: { card: { name: string }; art?: { url: string }; className?: string }) => <span className={className} data-art-url={art?.url} data-testid={`art-${card.name}`}>{card.name}</span> }));
vi.mock('../gallery/LibraryTabs', () => ({ useLibrarySections: () => ({ sections: null }) }));
vi.mock('../gallery/MusicTab', () => ({ MusicTab: ({ state }: { state?: string }) => <div data-state={state} data-testid="music-browser">Music browser</div> }));
vi.mock('./MusicCuration', () => ({ MusicCuration: () => <div>Music curation</div> }));
vi.mock('./MusicPlaylists', () => ({ MusicPlaylists: () => <div>Playlist workspace</div> }));

const albumA = albumSummary('album-a', { name: 'Amber Rooms', artist_name: 'Mara Vale', year: 2021 });
const albumA2 = albumSummary('album-a2', { name: 'Blue Hours', artist_name: 'Mara Vale', year: 2018 });
const albumB = albumSummary('album-b', { name: 'Night Window', artist_name: 'North Arcade', year: 2019 });
const albumB2 = albumSummary('album-b2', { name: 'Soft Machines', artist_name: 'North Arcade', year: 2016 });
const albumC = albumSummary('album-c', { name: 'Side Streets', artist_name: 'June Static', year: 2022 });
const albumD = albumSummary('album-d', { name: 'Glass Current', artist_name: 'The Lanterns', year: 2020 });
const albumE = albumSummary('album-e', { name: 'After Image', artist_name: 'Low Relay', year: 2017 });
const albumF = albumSummary('album-f', { name: 'Low Light', artist_name: 'Marble Choir', year: 2015 });
const collection = [albumA, albumA2, albumB, albumB2, albumC, albumD, albumE, albumF];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.title.mockReset();
  mocks.list.mockResolvedValue(titlePage(collection));
});

describe('AudioSoundstage', () => {
  it('loads one bounded album page and prioritizes different artists in the showcase', async () => {
    render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u1" />);
    expect(screen.getByText('Loading albums…')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'A room full of records' })).toBeTruthy();
    expect(mocks.list).toHaveBeenCalledWith({ type: 'album', limit: ALBUM_SOURCE_LIMIT, sort: 'created' });
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(screen.getByText('On this shelf: 6 albums · 6 artists')).toBeTruthy();
    expect(screen.getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      'Amber Rooms by Mara Vale, 2021, 12 tracks',
      'Night Window by North Arcade, 2019, 12 tracks',
      'Side Streets by June Static, 2022, 12 tracks',
      'Glass Current by The Lanterns, 2020, 12 tracks',
      'After Image by Low Relay, 2017, 12 tracks',
      'Low Light by Marble Choir, 2015, 12 tracks',
    ]);
  });

  it('supplements a one-artist recent page with bounded covers from other artists', async () => {
    const recentAlbums = Array.from({ length: ALBUM_SOURCE_LIMIT }, (_, index) => albumSummary(`mara-${index}`, { name: `Mara Release ${index + 1}`, artist_name: 'Mara Vale', parent_id: 'artist-mara' }));
    const artists = [
      artistSummary('artist-mara', { name: 'Mara Vale' }),
      artistSummary('artist-b', { name: 'North Arcade' }),
      artistSummary('artist-c', { name: 'June Static' }),
      artistSummary('artist-d', { name: 'The Lanterns' }),
      artistSummary('artist-e', { name: 'Low Relay' }),
      artistSummary('artist-f', { name: 'Marble Choir' }),
    ];
    const coverAlbums = [albumB, albumC, albumD, albumE, albumF];
    const details = new Map(coverAlbums.map((album, index) => [artists[index + 1].id, titleDetail(artists[index + 1], { children: [album] })]));
    mocks.list.mockResolvedValueOnce(titlePage(recentAlbums)).mockResolvedValueOnce(titlePage(artists));
    mocks.title.mockImplementation((id: string) => Promise.resolve(details.get(id)));

    render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u1" />);
    expect(await screen.findByRole('button', { name: /Low Light by Marble Choir/ })).toBeTruthy();
    expect(mocks.list).toHaveBeenNthCalledWith(1, { type: 'album', limit: ALBUM_SOURCE_LIMIT, sort: 'created' });
    expect(mocks.list).toHaveBeenNthCalledWith(2, { type: 'artist', limit: SHOWCASE_LIMIT, sort: 'created' });
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(mocks.title.mock.calls.map(([id]) => id)).toEqual(artists.slice(1).map((artist) => artist.id));
    expect(screen.getAllByRole('button')).toHaveLength(SHOWCASE_LIMIT);
    expect(screen.getByRole('button', { name: /Mara Release 1 by Mara Vale/ })).toBeTruthy();
    for (const album of coverAlbums) expect(screen.getByRole('button', { name: new RegExp(`${album.name} by ${album.artist_name}`) })).toBeTruthy();
  });

  it('retains available albums when supplemental artist details fail and ignores late supplements after an owner change', async () => {
    let finish!: (value: ReturnType<typeof titleDetail>) => void;
    const artist = artistSummary('extra-artist', { name: 'June Static' });
    mocks.list.mockResolvedValueOnce(titlePage([albumA]))
      .mockResolvedValueOnce(titlePage([artist]))
      .mockResolvedValueOnce(titlePage(collection));
    mocks.title.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const view = render(<AudioSoundstage onOpenArtist={vi.fn()} owner="old" />);
    await waitFor(() => expect(mocks.title).toHaveBeenCalledWith(artist.id));
    expect(screen.getByRole('button', { name: /Amber Rooms by Mara Vale/ })).toBeTruthy();
    view.rerender(<AudioSoundstage onOpenArtist={vi.fn()} owner="new" />);
    await screen.findByRole('button', { name: /Low Light by Marble Choir/ });
    finish(titleDetail(artist, { children: [albumSummary('stale', { name: 'Stale supplement' })] }));
    await Promise.resolve();
    expect(screen.queryByRole('button', { name: /Stale supplement/ })).toBeNull();
    view.unmount();

    mocks.list.mockResolvedValueOnce(titlePage([albumA])).mockResolvedValueOnce(titlePage([artist]));
    mocks.title.mockRejectedValueOnce(new Error('Artist unavailable'));
    render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u1" />);
    expect(await screen.findByRole('button', { name: /Amber Rooms by Mara Vale/ })).toBeTruthy();
    await waitFor(() => expect(mocks.title).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('opens the selected album from the collection hero', async () => {
    const open = vi.fn();
    render(<AudioSoundstage onOpenArtist={open} owner="u1" />);
    await screen.findByRole('heading', { name: 'A room full of records' });
    await userEvent.click(screen.getByRole('button', { name: /Amber Rooms by Mara Vale/ }));
    expect(open).toHaveBeenCalledWith(albumA);
  });

  it('keeps accessible loading, empty, and retryable failure states', async () => {
    let finish!: (value: ReturnType<typeof titlePage>) => void;
    mocks.list.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u1" />);
    expect(screen.getByRole('status').textContent).toBe('Loading albums…');
    finish(titlePage([]));
    expect(await screen.findByRole('heading', { name: 'No albums yet' })).toBeTruthy();
    expect(screen.getByText('Albums will appear here when music joins your library.')).toBeTruthy();

    const view = render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u2" />);
    view.unmount();
    mocks.list.mockRejectedValueOnce(new Error('The album shelf is unavailable.')).mockResolvedValueOnce(titlePage([albumA]));
    render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u3" />);
    expect(await screen.findByText('The album shelf is unavailable.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { name: 'A room full of records' })).toBeTruthy();
  });

  it('ignores a stale album list response after the owner changes', async () => {
    let finishOld!: (value: ReturnType<typeof titlePage>) => void;
    const old = new Promise<ReturnType<typeof titlePage>>((resolve) => { finishOld = resolve; });
    mocks.list.mockReturnValueOnce(old).mockResolvedValueOnce(titlePage([albumB]));
    const view = render(<AudioSoundstage onOpenArtist={vi.fn()} owner="old" />);
    view.rerender(<AudioSoundstage onOpenArtist={vi.fn()} owner="new" />);
    expect(await screen.findByRole('button', { name: /Night Window by North Arcade/ })).toBeTruthy();
    finishOld(titlePage([albumA]));
    await Promise.resolve();
    expect(screen.getByRole('button', { name: /Night Window by North Arcade/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Amber Rooms by Mara Vale/ })).toBeNull();
  });
});

describe('AudioSurface navigation', () => {
  it('keeps Manage music for detail editors and keeps the soundstage on the default pane', async () => {
    mocks.list.mockResolvedValue(titlePage([]));
    const common = { wall: '', onWallChange: vi.fn(), onOpenTitle: vi.fn(), onPlay: vi.fn(), onItemChanged: vi.fn() };
    const view = render(<AudioSurface {...common} user={{ id: 'viewer', role: 'member', can_edit_details: false }} />);
    expect(screen.queryByRole('link', { name: 'Manage music' })).toBeNull();
    expect(screen.getByTestId('music-browser')).toBeTruthy();
    view.rerender(<AudioSurface {...common} user={{ id: 'editor', role: 'member', can_edit_details: true }} />);
    expect(screen.getByRole('link', { name: 'Manage music' })).toBeTruthy();
    await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith({ type: 'album', limit: ALBUM_SOURCE_LIMIT, sort: 'created' }));
  });

  it('falls back to the music landing when a viewer follows a saved manage link', async () => {
    mocks.list.mockResolvedValue(titlePage([]));
    render(<AudioSurface onItemChanged={vi.fn()} onOpenTitle={vi.fn()} onPlay={vi.fn()} onWallChange={vi.fn()} user={{ id: 'viewer', role: 'member', can_edit_details: false }} wall="pane=manage" />);
    expect(screen.queryByText('Music curation')).toBeNull();
    expect(screen.getByRole('link', { name: 'Music' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByTestId('music-browser').getAttribute('data-state')).toBe('');
  });
});
