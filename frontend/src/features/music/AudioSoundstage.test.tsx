import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { albumSummary, artistSummary, titleDetail, titlePage } from '../../test/galleryFixtures';
import type { MusicTitleInfo } from './musicTypes';
import { AudioSoundstage, FEATURED_LIMIT } from './AudioSoundstage';
import { AudioSurface } from './AudioSurface';

const mocks = vi.hoisted(() => ({ list: vi.fn(), title: vi.fn(), play: vi.fn() }));
vi.mock('../../api', async (original) => ({ ...(await original<typeof import('../../api')>()), listTitles: mocks.list, getTitle: mocks.title }));
vi.mock('./musicSession', () => ({ playMusic: mocks.play }));
vi.mock('../gallery/GalleryArt', () => ({ GalleryArt: ({ card, art, className }: { card: { name: string }; art?: { url: string }; className?: string }) => <span className={className} data-art-url={art?.url} data-testid={`art-${card.name}`}>{card.name}</span> }));
vi.mock('../gallery/LibraryTabs', () => ({ useLibrarySections: () => ({ sections: null }) }));
vi.mock('../gallery/MusicTab', () => ({ MusicTab: ({ state }: { state?: string }) => <div data-state={state} data-testid="music-browser">Music browser</div> }));
vi.mock('./MusicCuration', () => ({ MusicCuration: () => <div>Music curation</div> }));
vi.mock('./MusicPlaylists', () => ({ MusicPlaylists: () => <div>Playlist workspace</div> }));

const music: MusicTitleInfo = {
  release_type: null, edition: null, label: null, catalogue_number: null, country: null, original_year: null,
  origin: 'Detroit, Michigan', active_since: '2007', active_until: null, styles: [], mood: [], discography: [], candidates: [], identified: true,
};
const artistA = artistSummary('artist-a', { name: 'Mara Vale', child_count: 3, genres: ['Soul', 'Jazz'], overview: 'A warm, restless voice carried by brass and late-night rhythm.' });
const artistB = artistSummary('artist-b', { name: 'North Arcade', child_count: 2, genres: ['Electronic'] });
const detailA = titleDetail(artistA, { music, children: [albumSummary('release-a', { name: 'Amber Rooms', year: 2021 }), albumSummary('release-b', { name: 'Night Window', year: 2019 })] });
const detailB = titleDetail(artistB, { music: { ...music, origin: 'Reykjavík', active_since: '2012' }, children: [albumSummary('release-c', { name: 'Soft Machines' })] });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.list.mockResolvedValue(titlePage([artistA, artistB]));
  mocks.title.mockImplementation(async (id: string) => id === artistB.id ? detailB : detailA);
  mocks.play.mockResolvedValue(undefined);
});

describe('AudioSoundstage', () => {
  it('uses a distinct artist background and credits its photograph instead of a release cover', async () => {
    mocks.title.mockResolvedValue({ ...detailA, backdrop: { ...detailA.poster, url: '/artist-landscape' }, music: { ...music,
      artwork_credits: [{ image_type: 'Backdrop', source_url: 'https://commons.wikimedia.org/wiki/File:Concert.jpg', author: 'Photographer', license: 'CC BY-SA 4.0' }],
    } });
    const view = render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u1" />);
    await screen.findByRole('link', { name: 'Photo · Photographer · CC BY-SA 4.0' });
    expect(view.container.querySelector('.audio-soundstage-art')?.getAttribute('data-art-url')).toBe('/artist-landscape');
    expect(screen.queryByRole('button', { name: /Featured release:/ })).toBeNull();
  });
  it('uses a real release cover when the selected artist has no portrait', async () => {
    const open = vi.fn();
    mocks.list.mockResolvedValue(titlePage([{ ...artistA, poster: null }]));
    mocks.title.mockResolvedValue({ ...detailA, poster: null });
    render(<AudioSoundstage onOpenArtist={open} owner="u1" />);
    const release = await screen.findByRole('button', { name: 'Featured release: Amber Rooms' });
    await userEvent.click(release);
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ id: 'release-a', type: 'album' }));
  });
  it('loads one bounded recent-artist page, then paints real details and releases', async () => {
    render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u1" />);
    expect(screen.getByText('Preparing the soundstage…')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Mara Vale' })).toBeTruthy();
    expect(mocks.list).toHaveBeenCalledWith({ type: 'artist', limit: FEATURED_LIMIT, sort: 'created' });
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(mocks.title).toHaveBeenCalledWith('artist-a');
    expect(await screen.findByText('Detroit, Michigan · Active since 2007')).toBeTruthy();
    expect(screen.getByText(/warm, restless voice/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Amber Rooms/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Night Window/ })).toBeTruthy();
  });

  it('plays, shuffles, and opens the actual selected artist', async () => {
    const open = vi.fn();
    render(<AudioSoundstage onOpenArtist={open} owner="member-1" />);
    await screen.findByRole('heading', { name: 'Mara Vale' });
    await userEvent.click(screen.getByRole('button', { name: 'Play artist' }));
    await userEvent.click(screen.getByRole('button', { name: 'Shuffle' }));
    expect(mocks.play).toHaveBeenNthCalledWith(1, 'member-1', { title_id: 'artist-a' }, undefined, 0, false);
    expect(mocks.play).toHaveBeenNthCalledWith(2, 'member-1', { title_id: 'artist-a' }, undefined, 0, true);
    await userEvent.click(screen.getByRole('button', { name: 'Open artist' }));
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ id: 'artist-a', name: 'Mara Vale' }));
  });

  it('keeps a late detail response from replacing a newly selected artist', async () => {
    let finishA!: (value: typeof detailA) => void;
    mocks.title.mockImplementation((id: string) => id === 'artist-a' ? new Promise((resolve) => { finishA = resolve; }) : Promise.resolve(detailB));
    render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u1" />);
    await screen.findByRole('heading', { name: 'Mara Vale' });
    await userEvent.click(screen.getByRole('button', { name: 'Feature North Arcade' }));
    expect(await screen.findByRole('heading', { name: 'North Arcade' })).toBeTruthy();
    expect(await screen.findByText('Reykjavík · Active since 2012')).toBeTruthy();
    finishA(detailA);
    await Promise.resolve();
    expect(screen.getByRole('heading', { name: 'North Arcade' })).toBeTruthy();
    expect(screen.queryByText('Detroit, Michigan · Active since 2007')).toBeNull();
  });

  it('ignores a stale owner list response', async () => {
    let finishOld!: (value: ReturnType<typeof titlePage>) => void;
    const old = new Promise<ReturnType<typeof titlePage>>((resolve) => { finishOld = resolve; });
    mocks.list.mockReturnValueOnce(old).mockResolvedValueOnce(titlePage([artistB]));
    const view = render(<AudioSoundstage onOpenArtist={vi.fn()} owner="old" />);
    view.rerender(<AudioSoundstage onOpenArtist={vi.fn()} owner="new" />);
    expect(await screen.findByRole('heading', { name: 'North Arcade' })).toBeTruthy();
    finishOld(titlePage([artistA]));
    await Promise.resolve();
    expect(screen.queryByRole('heading', { name: 'Mara Vale' })).toBeNull();
  });

  it('has useful empty and retryable failure states', async () => {
    mocks.list.mockResolvedValueOnce(titlePage([]));
    const view = render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u1" />);
    expect(await screen.findByRole('heading', { name: 'Your soundstage is waiting' })).toBeTruthy();
    expect(screen.getByText(/Artists will appear/)).toBeTruthy();
    view.unmount();

    mocks.list.mockRejectedValueOnce(new Error('The artist shelf is unavailable.')).mockResolvedValueOnce(titlePage([artistA]));
    render(<AudioSoundstage onOpenArtist={vi.fn()} owner="u2" />);
    expect(await screen.findByText('The artist shelf is unavailable.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { name: 'Mara Vale' })).toBeTruthy();
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
    await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith({ type: 'artist', limit: FEATURED_LIMIT, sort: 'created' }));
  });

  it('falls back to the music landing when a viewer follows a saved manage link', async () => {
    mocks.list.mockResolvedValue(titlePage([]));
    render(<AudioSurface onItemChanged={vi.fn()} onOpenTitle={vi.fn()} onPlay={vi.fn()} onWallChange={vi.fn()} user={{ id: 'viewer', role: 'member', can_edit_details: false }} wall="pane=manage" />);
    expect(screen.queryByText('Music curation')).toBeNull();
    expect(screen.getByRole('link', { name: 'Music' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByTestId('music-browser').getAttribute('data-state')).toBe('');
  });
});
