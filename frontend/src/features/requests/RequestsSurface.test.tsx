import axe from 'axe-core';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from '../../api';
import { ToastProvider } from '../../ui';
import type { RequestsRoute } from '../../app/routes';
import type { UserProfile } from '../../types';
import { anime, animeDetail, catalogHome, movie, quotas, request, showDetail } from './requestsFixtures';
import { RequestsSurface } from './RequestsSurface';

const api = vi.hoisted(() => ({
  getCatalogHome: vi.fn(), getCatalogTitle: vi.fn(), getQuotas: vi.fn(), createRequest: vi.fn(), listRequests: vi.fn(), cancelRequest: vi.fn(),
  getCatalogList: vi.fn(), getGenres: vi.fn(), searchCatalog: vi.fn(), getAnimeSeason: vi.fn(), getAnimeSchedule: vi.fn(), approveRequest: vi.fn(), declineRequest: vi.fn(), retryRequest: vi.fn(),
}));
vi.mock('./requestsApi', async (importOriginal) => ({ ...(await importOriginal<typeof import('./requestsApi')>()), ...api }));

const member = { id: 'u-1', username: 'dana', display_name: 'Dana', role: 'viewer', is_active: true } as UserProfile;
const admin = { ...member, id: 'u-0', role: 'admin' } as UserProfile;
const session = { captureSessionToken: () => 1, isSessionTokenCurrent: () => true };

function renderSurface(route: RequestsRoute = { surface: 'requests', view: 'discover' }, user = member) {
  const onRoute = vi.fn();
  const onOpenRoute = vi.fn();
  const view = render(<RequestsSurface onOpenRoute={onOpenRoute} onRoute={onRoute} route={route} session={session} user={user} />);
  return { ...view, onRoute, onOpenRoute };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getCatalogHome.mockResolvedValue(catalogHome);
  api.getQuotas.mockResolvedValue({ quotas });
  api.getCatalogTitle.mockImplementation((kind: string) => Promise.resolve(kind === 'anime' ? animeDetail : showDetail));
  api.getGenres.mockResolvedValue({ genres: [] });
});

describe('Discover', () => {
  it('has no axe violations with the hero, the anime season rail and the other rails', async () => {
    const { container } = renderSurface();
    await screen.findByRole('heading', { name: 'This season in anime · Fall 2026' });
    expect(screen.getByRole('region', { name: 'Featured' })).toBeTruthy();
    // The anime season rail comes first among the rails.
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent).slice(1, 3)).toEqual(['This season in anime · Fall 2026', 'Trending']);
    const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
    expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target).join(', ')}`)).toEqual([]);
  });

  it('shows status ribbons: a download ring, and an available title opens the Library', async () => {
    const { onOpenRoute } = renderSurface();
    const rail = (await screen.findByRole('heading', { name: 'Trending' })).closest('section')!;
    expect(within(rail).getByText('Downloading 42%')).toBeTruthy();
    await userEvent.click(within(rail).getByRole('button', { name: /^Spirited Away/ }));
    expect(onOpenRoute).toHaveBeenCalledWith({ surface: 'library', titleId: 'title-7' });
  });

  it('opens a title from its card, and See all follows the rail', async () => {
    const { onRoute } = renderSurface();
    const rail = (await screen.findByRole('heading', { name: 'Popular films' })).closest('section')!;
    await userEvent.click(within(rail).getByRole('button', { name: /^The Matrix/ }));
    expect(onRoute).toHaveBeenLastCalledWith({ surface: 'requests', view: 'title', kind: 'movie', id: 603 });
    await userEvent.click(within(rail).getByRole('button', { name: 'See all' }));
    expect(onRoute).toHaveBeenLastCalledWith({ surface: 'requests', view: 'movies' }, undefined);
  });

  it('explains disabled Requests calmly, and gives admins the way to Settings', async () => {
    api.getCatalogHome.mockRejectedValue(new ApiRequestError('requests_disabled', 503, null, null, { detail: 'requests_disabled' }));
    const { onOpenRoute } = renderSurface(undefined, admin);
    await userEvent.click(await screen.findByRole('button', { name: 'Open Settings → Requests' }));
    expect(onOpenRoute).toHaveBeenCalledWith(expect.objectContaining({ surface: 'settings' }));
  });

  it('tells a member without a TMDB key to ask an admin', async () => {
    api.getCatalogHome.mockRejectedValue(new ApiRequestError('tmdb_not_configured', 503, null, null, { detail: 'tmdb_not_configured' }));
    renderSurface();
    expect(await screen.findByText(/An admin needs to finish setting up Requests/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Settings/ })).toBeNull();
  });
});

describe('the request sheet', () => {
  it('requests a film straight away, with the quota and route lines, and updates the ribbon optimistically', async () => {
    let resolve: (value: unknown) => void = () => undefined;
    api.createRequest.mockReturnValue(new Promise((done) => { resolve = done; }));
    renderSurface();
    const rail = (await screen.findByRole('heading', { name: 'Popular films' })).closest('section')!;
    await userEvent.click(within(rail).getByRole('button', { name: 'Request The Matrix' }));
    const sheet = await screen.findByRole('dialog', { name: 'Request The Matrix' });
    expect(await within(sheet).findByText('Will be sent straight to Radarr')).toBeTruthy();
    expect(within(sheet).getByText('No request limit')).toBeTruthy();
    await userEvent.click(within(sheet).getByRole('button', { name: 'Request' }));
    expect(api.createRequest).toHaveBeenCalledWith({ kind: 'movie', tmdb_id: 603 });
    expect(within(rail).getByText('Approved')).toBeTruthy(); // optimistic, before the server answers
    resolve(request({ kind: 'movie', media_type: 'movie', key: 'movie:603', status: 'processing', progress: 0.1, seasons: null }));
    await waitFor(() => expect(within(rail).getByText('Downloading 10%')).toBeTruthy());
  });

  it('asks an anime series for seasons and English dub or Japanese + subtitles, and remembers the choice', async () => {
    api.createRequest.mockResolvedValue(request({ kind: 'anime', key: anime.key, status: 'pending' }));
    renderSurface();
    const rail = (await screen.findByRole('heading', { name: 'This season in anime · Fall 2026' })).closest('section')!;
    await userEvent.click(within(rail).getByRole('button', { name: 'Request Attack on Titan' }));
    const sheet = await screen.findByRole('dialog', { name: 'Request Attack on Titan' });
    expect(await within(sheet).findByText('3 of 10 left this week')).toBeTruthy();
    expect(within(sheet).getByText("Needs an admin's approval")).toBeTruthy();
    const send = within(sheet).getByRole('button', { name: 'Request' });
    expect(send.getAttribute('aria-disabled')).toBe('true'); // no language chosen yet
    await userEvent.click(within(sheet).getByRole('radio', { name: /Japanese \+ subtitles/ }));
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Choose' }));
    await userEvent.click(within(sheet).getByRole('checkbox', { name: 'Season 1' }));
    await userEvent.click(send);
    expect(api.createRequest).toHaveBeenCalledWith({ kind: 'anime', anilist_id: 16498, tmdb_id: 1429, tvdb_id: 267440, seasons: [1], language: 'sub' });
    expect(window.localStorage.getItem('lumina:requests:language:u-1')).toBe('sub');
    await waitFor(() => expect(within(rail).getByText('Requested')).toBeTruthy());
  });

  it('reverts the ribbon and says why when the quota is used up', async () => {
    api.createRequest.mockRejectedValue(new ApiRequestError('quota_exceeded', 429, null, null, { detail: 'quota_exceeded' }));
    renderSurface();
    const rail = (await screen.findByRole('heading', { name: 'Popular films' })).closest('section')!;
    await userEvent.click(within(rail).getByRole('button', { name: 'Request The Matrix' }));
    const sheet = await screen.findByRole('dialog', { name: 'Request The Matrix' });
    await within(sheet).findByText('No request limit');
    await userEvent.click(within(sheet).getByRole('button', { name: 'Request' }));
    expect((await within(sheet).findByRole('alert')).textContent).toMatch(/request limit/);
    expect(within(rail).queryByText('Approved')).toBeNull();
  });
});

describe('engine answers', () => {
  it('reveals the dub/sub choice when the engine says a show is anime, then resubmits with it', async () => {
    api.createRequest.mockRejectedValueOnce(new ApiRequestError('language_required', 422, null, null, { detail: 'language_required' }))
      .mockResolvedValueOnce(request({ kind: 'anime', status: 'pending' }));
    renderSurface({ surface: 'requests', view: 'title', kind: 'show', id: 1399 });
    await userEvent.click(await screen.findByRole('button', { name: 'Request' }));
    const sheet = await screen.findByRole('dialog', { name: 'Request Game of Thrones' });
    await within(sheet).findByText('3 of 10 left this week');
    expect(within(sheet).queryByRole('radio', { name: /English dub/ })).toBeNull();
    await userEvent.click(within(sheet).getByRole('button', { name: 'Request' }));
    expect((await within(sheet).findByRole('alert')).textContent).toMatch(/This one is anime/);
    await userEvent.click(within(sheet).getByRole('radio', { name: /English dub/ }));
    await userEvent.click(within(sheet).getByRole('button', { name: 'Request' }));
    expect(api.createRequest).toHaveBeenLastCalledWith({ kind: 'show', tmdb_id: 1399, tvdb_id: 121361, seasons: 'all', language: 'dub' });
  });

  it('confirms a seasons follow-up as its own request', async () => {
    const partial = { ...showDetail, status: { state: 'partially_available' as const, request_id: 'r-1' } };
    api.getCatalogTitle.mockResolvedValue(partial);
    api.createRequest.mockResolvedValue(request({ id: 'r-9', seasons: [2], status: 'pending' }));
    render(<ToastProvider><RequestsSurface onOpenRoute={vi.fn()} onRoute={vi.fn()} route={{ surface: 'requests', view: 'title', kind: 'show', id: 1399 }} session={session} user={member} /></ToastProvider>);
    await userEvent.click(await screen.findByRole('button', { name: 'Request more' }));
    const sheet = await screen.findByRole('dialog', { name: 'Request Game of Thrones' });
    await within(sheet).findByText('3 of 10 left this week');
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Latest' }));
    await userEvent.click(within(sheet).getByRole('button', { name: 'Request' }));
    expect(api.createRequest).toHaveBeenCalledWith({ kind: 'show', tmdb_id: 1399, tvdb_id: 121361, seasons: [2] });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByText('Partly in your vault')).toBeTruthy(); // the title keeps its own status
    expect(await screen.findByText('Asked for season 2 of Game of Thrones; an admin will review.')).toBeTruthy();
  });
});

describe('My requests', () => {
  it('lists requests with their timeline and lets a pending one be cancelled', async () => {
    api.listRequests.mockResolvedValue({ items: [request(), request({ id: 'r-2', status: 'processing', progress: 0.3, title: 'Dune', key: 'movie:1', kind: 'movie', media_type: 'movie', seasons: null })], page: 1, total_pages: 1, counts: {} });
    api.cancelRequest.mockResolvedValue(undefined);
    renderSurface({ surface: 'requests', view: 'mine' });
    expect(await screen.findByText('Downloading 30%')).toBeTruthy();
    expect(api.listRequests).toHaveBeenCalledWith({ scope: 'mine' });
    await userEvent.click(screen.getByRole('button', { name: 'Cancel request' }));
    expect(api.cancelRequest).toHaveBeenCalledWith('r-1');
  });

  it('keeps Manage for admins', async () => {
    renderSurface({ surface: 'requests', view: 'discover' }, member);
    expect(screen.queryByRole('button', { name: 'Manage' })).toBeNull();
    renderSurface({ surface: 'requests', view: 'manage' }, member);
    expect(screen.getByText('This page is for admins')).toBeTruthy();
    expect(api.listRequests).not.toHaveBeenCalled();
  });
});

describe('Manage', () => {
  it('approves from the queue and declines with a reason', async () => {
    api.listRequests.mockImplementation(({ status }: { status?: string }) => Promise.resolve({ items: status === 'pending' ? [request()] : [], page: 1, total_pages: 1, counts: {} }));
    api.approveRequest.mockResolvedValue(request({ status: 'approved' }));
    api.declineRequest.mockResolvedValue(request({ status: 'declined' }));
    renderSurface({ surface: 'requests', view: 'manage' }, admin);
    await userEvent.click(await screen.findByRole('button', { name: 'Approve' }));
    expect(api.approveRequest).toHaveBeenCalledWith('r-1');
    await userEvent.click(screen.getByRole('button', { name: 'Decline' }));
    const dialog = await screen.findByRole('dialog', { name: 'Decline Game of Thrones?' });
    await userEvent.type(within(dialog).getByRole('textbox'), 'Already on the shelf');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Decline' }));
    expect(api.declineRequest).toHaveBeenCalledWith('r-1', 'Already on the shelf');
  });
});

it('a title page shows the movie and its trailer button', async () => {
  api.getCatalogTitle.mockResolvedValue({ ...showDetail, ...movie, seasons: [], trailers: showDetail.trailers });
  renderSurface({ surface: 'requests', view: 'title', kind: 'movie', id: 603 });
  expect(await screen.findByRole('heading', { level: 1, name: 'The Matrix' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Trailer' })).toBeTruthy();
});
