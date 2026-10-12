import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { resolveArtworkUrl } from '../../Artwork';
import { albumDetail, albumSummary, artistSummary, episodeSummary, keyScenes, movieSummary, seriesSummary, titleDetail, userData } from '../../test/galleryFixtures';
import { recoTitle } from '../../test/recoFixtures';
import type { TitleDetail, TitleSummary } from '../../types';
import { RecoFeedbackProvider } from '../reco/recoFeedback';
import type { GalleryArtProps } from './GalleryArt';
import { backdropWidthFor, readableAccent, renditionUrl } from './galleryModel';

const api = vi.hoisted(() => ({
  getTitle: vi.fn(), listSeasonEpisodes: vi.fn(), listSimilarTitles: vi.fn(), setTitleWatched: vi.fn(), setFavorite: vi.fn(), getRecap: vi.fn(), requestRecap: vi.fn(),
  getEpisodeSummaries: vi.fn(), getKeyScenes: vi.fn(), addToWatchQueue: vi.fn(), getWatchQueue: vi.fn(), searchTitleMatches: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const art = vi.hoisted(() => ({ props: [] as GalleryArtProps[] }));
vi.mock('./GalleryArt', () => ({
  GalleryArt: (props: GalleryArtProps) => {
    art.props.push(props);
    return props.alt ? <span aria-label={props.alt} data-art={props.kind} role="img" /> : <span aria-hidden="true" data-art={props.kind} />;
  },
}));
const loader = vi.hoisted(() => ({ prefetchImage: vi.fn() }));
vi.mock('./imageLoader', async (importOriginal) => ({ ...(await importOriginal<typeof import('./imageLoader')>()), prefetchImage: loader.prefetchImage }));
const metrics = vi.hoisted(() => ({ recordMetric: vi.fn() }));
vi.mock('../../perfMetrics', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../perfMetrics')>()), recordMetric: metrics.recordMetric, sinceNavigation: () => 420 }));
vi.mock('../../playbackPrefetch', () => ({ prefetchPlaybackOptions: vi.fn(), speculativeStart: vi.fn(), cancelSpeculativeStart: vi.fn() }));

const { ApiRequestError } = await import('../../api');
const { GalleryTitlePage } = await import('./GalleryTitlePage');
const { SPECULATE_AFTER_MS } = await import('./TitleActions');
const { forgetTitle, rememberSummary } = await import('./titleCache');

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
beforeEach(() => {
  api.listSimilarTitles.mockResolvedValue([]);
  api.listSeasonEpisodes.mockResolvedValue([]);
  api.getKeyScenes.mockResolvedValue({ available: false, scenes: [] });
  api.getEpisodeSummaries.mockResolvedValue({ available: false, items: [] });
  api.getRecap.mockRejectedValue(new Error('404'));
});
afterEach(() => {
  vi.useRealTimers(); // a test that fails while on fake timers must not leave them to the next
  vi.resetAllMocks();
  art.props.length = 0;
  for (const id of ['movie-1', 'movie-click', 'movie-long', 'plain-1', 'series-1', 'boxset-1', 'album-1', 'artist-1']) forgetTitle(id);
});

const noop = () => undefined;
const page = (overrides: Partial<Parameters<typeof GalleryTitlePage>[0]> = {}) => (
  <GalleryTitlePage id="movie-1" onBack={noop} onEdit={noop} onOpenTitle={noop} onPlay={noop} onSearch={noop} onSeason={noop} season={null} user={{ role: 'viewer' }} {...overrides} />
);
const movie = titleDetail(movieSummary('movie-1', {
  official_rating: 'PG-13', overview: 'A girl and her grandfather carry a lamp across the ice.', play_item_id: 'v4k', user_data: userData({ position_seconds: 2530, resume_item_id: 'v4k' }),
}), {
  versions: [{ item_id: 'v4k', height: 2160, hdr: true, file_size: 58 * 1024 ** 3 }, { item_id: 'v1080', height: 1080, hdr: false, file_size: 8 * 1024 ** 3 }],
  extras: [{ item_id: 'trailer-1', extra_type: 'trailer', name: 'Trailer', duration_seconds: 120 }],
  people: [{ name: 'Ines Varga', type: 'Director' }, { name: 'Mara Quill', role: 'Keeper', type: 'Actor' }],
});
const season = (n: number): TitleSummary => movieSummary(`season-${n}`, { type: 'season', name: n ? `Season ${n}` : 'Specials', index_number: n, parent_id: 'series-1' });
const episode = (s: number, e: number, patch: Partial<TitleSummary> = {}) => episodeSummary(s, e, { play_item_id: `i${s}${e}`, ...patch });
const show = (patch: Partial<TitleDetail> = {}) => titleDetail(seriesSummary('series-1', { user_data: userData({ last_watched_at: '2026-09-01T12:00:00', unplayed_count: 4 }) }), {
  children: [season(0), season(2), season(1)], play_next: episode(2, 4), ...patch,
});
const lastArt = (kind: GalleryArtProps['kind']) => art.props.filter((props) => props.kind === kind).at(-1);
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe('GalleryTitlePage: hero, headline and states', () => {
  it('paints the clicked summary at once, fills in the detail, focuses the headline and times the hero as a click', async () => {
    const summary = movieSummary('movie-click');
    rememberSummary(summary);
    let arrive: (detail: TitleDetail) => void = noop;
    api.getTitle.mockReturnValue(new Promise<TitleDetail>((resolve) => { arrive = resolve; }));
    const { container } = render(page({ id: 'movie-click' }));
    expect(screen.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeTruthy();
    expect(container.querySelector('.t-meta')?.textContent).toBe('2019 · 1h 52m · Adventure');
    expect(screen.queryByRole('button', { name: 'Play' })).toBeNull();
    expect(loader.prefetchImage).toHaveBeenCalledWith(resolveArtworkUrl(renditionUrl(summary.backdrop, backdropWidthFor(window.innerWidth))), 1);
    await act(async () => arrive(titleDetail(summary, { official_rating: 'PG' })));
    expect(container.querySelector('.t-meta')?.textContent).toBe('2019 · PG · 1h 52m · Adventure');
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1, name: 'Northern Lantern' }));
    expect(screen.getByRole('button', { name: 'Play' })).toBeTruthy();
    const hero = lastArt('backdrop');
    expect([hero?.priority, hero?.sizes]).toEqual([1, '100vw']);
    act(() => { hero?.onSettled?.('image'); hero?.onSettled?.('image'); });
    expect(metrics.recordMetric).toHaveBeenCalledTimes(1);
    expect(metrics.recordMetric).toHaveBeenCalledWith('detail_hero_ms', 'click', 420);
  });

  it('shows a busy paper field on a deep link until the detail arrives, then times the hero as a deep link', async () => {
    let arrive: (detail: TitleDetail) => void = noop;
    api.getTitle.mockReturnValue(new Promise<TitleDetail>((resolve) => { arrive = resolve; }));
    const { container } = render(page());
    expect(container.querySelector('.t-page')?.getAttribute('aria-busy')).toBe('true');
    expect(container.querySelector('.t-hero.is-empty')).toBeTruthy();
    expect(screen.queryByRole('heading')).toBeNull();
    await act(async () => arrive(movie));
    expect(await screen.findByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeTruthy();
    expect(loader.prefetchImage).not.toHaveBeenCalled();
    act(() => lastArt('backdrop')?.onSettled?.('card'));
    expect(metrics.recordMetric).toHaveBeenCalledWith('detail_hero_ms', 'deep_link', 420);
  });

  it('makes a colour field hero with the headline on it when there is no backdrop', async () => {
    api.getTitle.mockResolvedValue(titleDetail(movieSummary('plain-1', { backdrop: null, backdrop_url: null })));
    const { container } = render(page({ id: 'plain-1' }));
    const heading = await screen.findByRole('heading', { level: 1, name: 'Northern Lantern' });
    const hero = container.querySelector('.t-hero') as HTMLElement;
    expect(container.querySelector('.t-page')?.classList.contains('is-plain')).toBe(true);
    expect(hero.contains(heading)).toBe(true);
    expect(hero.style.backgroundColor).toBe('rgb(42, 59, 76)');
    expect(art.props.some((props) => props.kind === 'backdrop')).toBe(false);
    expect(metrics.recordMetric).toHaveBeenCalledWith('detail_hero_ms', 'deep_link', 420);
  });

  it('sets a long name in its logo art, and falls back to the words when the logo fails', async () => {
    const name = 'The Lamp Keepers of the Northern Ice';
    api.getTitle.mockResolvedValue(titleDetail(movieSummary('movie-long', { name })));
    const { container } = render(page({ id: 'movie-long' }));
    expect(await screen.findByRole('heading', { level: 1, name })).toBeTruthy();
    const logo = lastArt('logo');
    expect([logo?.priority, logo?.alt, logo?.colour.colour]).toEqual([1, name, 'transparent']);
    act(() => logo?.onFail?.());
    expect(container.querySelector('[data-art="logo"]')).toBeNull();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(name);
  });

  it('keeps a short name as words even when a logo exists', async () => {
    api.getTitle.mockResolvedValue(movie);
    render(page());
    await screen.findByRole('heading', { level: 1, name: 'Northern Lantern' });
    expect(art.props.some((props) => props.kind === 'logo')).toBe(false);
  });

  it('drops a capital into a lede that starts with a letter, keeps the tagline, and tints the page', async () => {
    api.getTitle.mockResolvedValue({ ...movie, tagline: 'Carry the light.', overview: 'Keepers carry the lamp north. '.repeat(6) }); // long enough for a drop cap
    const first = render(page());
    await screen.findByRole('heading', { level: 1 });
    expect(first.container.querySelector('.t-lede')?.classList.contains('has-dropcap')).toBe(true);
    expect(screen.getByText('Carry the light.')).toBeTruthy();
    expect((first.container.querySelector('.t-page') as HTMLElement).style.getPropertyValue('--g-accent')).toBe(readableAccent('#c08a4b', 'dark'));
    first.unmount();
    forgetTitle('movie-1');
    api.getTitle.mockResolvedValue({ ...movie, overview: '“Carry it,” she said.' });
    const second = render(page());
    await screen.findByRole('heading', { level: 1 });
    expect(second.container.querySelector('.t-lede')?.classList.contains('has-dropcap')).toBe(false);
  });

  it('says so when the title is not visible, and goes back on Escape (ported)', async () => {
    api.getTitle.mockRejectedValue(new ApiRequestError('Not found', 404));
    const onBack = vi.fn();
    render(page({ onBack }));
    expect(await screen.findByRole('heading', { level: 1, name: 'This title is not in your library' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1 }))); // focus lands in a passive effect, after the heading is findable
    fireEvent.keyDown(screen.getByRole('heading', { level: 1 }), { key: 'Escape' });
    expect(onBack).toHaveBeenCalledWith(null, null);
  });

  it('offers Try again when the title fails to load, and reloads it (ported)', async () => {
    api.getTitle.mockRejectedValueOnce(new ApiRequestError('boom', 500)).mockResolvedValueOnce(movie);
    render(page());
    expect(await screen.findByRole('heading', { level: 1, name: 'Lumina could not load this title' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeTruthy();
    expect(api.getTitle).toHaveBeenCalledTimes(2);
  });

  it('keeps the page and says so inline when the reload after a change fails', async () => {
    api.getTitle.mockResolvedValueOnce(show()).mockRejectedValueOnce(new ApiRequestError('boom', 500)).mockResolvedValueOnce(show());
    api.setTitleWatched.mockResolvedValue(userData({ played: true }));
    render(page({ id: 'series-1' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Mark watched' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Lumina could not refresh this title. Try again');
    expect(screen.getByRole('heading', { level: 1, name: 'Harbor Lights' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Lumina could not load this title' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await settle();
    expect(api.getTitle).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('refetches instead of patching when the member changes something after a failed refresh', async () => {
    const favorite = show({ user_data: userData({ last_watched_at: '2026-09-01T12:00:00', unplayed_count: 4, is_favorite: true }) });
    api.getTitle.mockResolvedValueOnce(show()).mockRejectedValueOnce(new ApiRequestError('boom', 500)).mockResolvedValueOnce(favorite);
    api.setTitleWatched.mockResolvedValue(userData({ played: true }));
    api.setFavorite.mockResolvedValue(undefined);
    render(page({ id: 'series-1' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Mark watched' }));
    await screen.findByRole('alert');
    await userEvent.click(screen.getByRole('button', { name: 'Favorite' }));
    await settle();
    expect(api.setFavorite).toHaveBeenCalledWith('series-1', true);
    expect(api.getTitle).toHaveBeenCalledTimes(3);
    expect(screen.getByRole('button', { name: 'Favorite' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('goes back to its lens from the back button', async () => {
    api.getTitle.mockResolvedValue(movie);
    const onBack = vi.fn();
    render(page({ onBack }));
    await userEvent.click(await screen.findByRole('button', { name: 'Back to Movies' }));
    expect(onBack).toHaveBeenCalledWith('movie', null);
  });
});

describe('GalleryTitlePage: body and sections', () => {
  it('fills the side column and links the collection the title belongs to', async () => {
    const boxset = movieSummary('boxset-1', { type: 'boxset', name: 'Lantern Collection' });
    api.getTitle.mockResolvedValue({ ...movie, boxset });
    const onOpenTitle = vi.fn();
    const { container } = render(page({ onOpenTitle }));
    await screen.findByRole('heading', { level: 1 });
    expect([...container.querySelectorAll('.t-side dt')].map((term) => term.textContent)).toEqual(['Directed by', 'Starring', 'In your library', 'Part of']);
    expect(container.querySelector('.t-side')?.textContent).toContain('4K HDR · 1080p · 66 GB');
    await userEvent.click(screen.getByRole('button', { name: 'Lantern Collection' }));
    expect(onOpenTitle).toHaveBeenCalledWith(boxset);
  });

  it('orders season tabs with Specials last, follows the season in the address, and labels the action from Next up (ported)', async () => {
    api.getTitle.mockResolvedValue(show());
    api.listSeasonEpisodes.mockImplementation(async (_id: string, number: number) => [episode(number, 1, { user_data: userData({ played: true }) }), episode(number, 2, { user_data: userData({ position_seconds: 1300, duration_seconds: 2600 }) })]);
    const onSeason = vi.fn();
    const onPlay = vi.fn();
    render(page({ id: 'series-1', onPlay, onSeason, season: 1 }));
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Season 1', 'Season 2', 'Specials']);
    expect(screen.getByRole('tablist', { name: 'Seasons' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Seasons' })).toBeNull();
    expect(screen.getByRole('tab', { name: 'Season 1' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('button', { name: 'Play S2 · E4' })).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'Play 1. Episode 1, S1 · E1, watched' })).toBeTruthy();
    await userEvent.click(screen.getByRole('tab', { name: 'Specials' }));
    expect(onSeason).toHaveBeenCalledWith(0);
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Season 1' }), { key: 'ArrowRight' });
    expect(onSeason).toHaveBeenLastCalledWith(2);
    await userEvent.click(screen.getByRole('button', { name: /^Play 2\. Episode 2, S1 · E2/ }));
    expect(onPlay).toHaveBeenCalledWith('i12');
  });

  it('offers a season menu and Edit details to editors only, handing the editor the season or title id', async () => {
    api.getTitle.mockResolvedValue(show());
    api.listSeasonEpisodes.mockResolvedValue([]);
    const onEdit = vi.fn();
    const first = render(page({ id: 'series-1', onEdit, season: 1 }));
    await screen.findAllByRole('tab');
    expect(screen.queryByRole('button', { name: 'More for season' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit details' })).toBeNull();
    first.unmount();
    render(page({ id: 'series-1', onEdit, season: 1, user: { role: 'viewer', can_edit_details: true } }));
    await userEvent.click(await screen.findByRole('button', { name: 'More for season' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Edit season details' }));
    expect(onEdit).toHaveBeenCalledWith('season-1');
    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    expect(onEdit).toHaveBeenLastCalledWith('series-1');
  });

  it('shows The story so far only for a show the member is in the middle of', async () => {
    api.getRecap.mockResolvedValue({ episode_title_id: 'ep-2-4', state: 'fallback', points: [], fallback: [{ episode_id: 'ep-2-3', name: 'Episode 3', season_number: 2, index_number: 3, overview: 'The ferry is late again.' }], suggest_preroll: false });
    api.getTitle.mockResolvedValue(show());
    const first = render(page({ id: 'series-1' }));
    const story = await screen.findByRole('region', { name: 'The story so far' });
    expect(story.parentElement?.classList.contains('t-main')).toBe(true);
    expect(story.previousElementSibling?.classList.contains('t-actions')).toBe(true);
    expect(api.getRecap).toHaveBeenCalledWith('ep-2-4');
    first.unmount();
    forgetTitle('series-1');
    api.getRecap.mockClear();
    api.getTitle.mockResolvedValue(show({ user_data: userData({ last_watched_at: '2026-09-01T12:00:00', unplayed_count: 0 }) }));
    render(page({ id: 'series-1' }));
    await screen.findByRole('heading', { level: 1, name: 'Harbor Lights' });
    await settle();
    expect(api.getRecap).not.toHaveBeenCalled();
    expect(screen.queryByRole('region', { name: 'The story so far' })).toBeNull();
  });

  it('asks for key scenes on movies and shows only, plays from a quote, and shows a boxset’s collection instead of its cast', async () => {
    api.getKeyScenes.mockResolvedValue(keyScenes);
    api.getTitle.mockResolvedValue(movie);
    const onPlay = vi.fn();
    const first = render(page({ onPlay }));
    await userEvent.click(await screen.findByRole('button', { name: 'Play from 12:41' }));
    expect(onPlay).toHaveBeenCalledWith('item-movie-1', 761);
    first.unmount();
    api.getKeyScenes.mockClear();
    api.getTitle.mockResolvedValue(titleDetail(movieSummary('boxset-1', { type: 'boxset', name: 'Lantern Collection' }), {
      children: [movieSummary('movie-2', { name: 'Lantern Returns' }), movieSummary('movie-3', { name: 'Lantern Rises' })], people: [{ name: 'Mara Quill', type: 'Actor' }],
    }));
    render(page({ id: 'boxset-1' }));
    const collection = await screen.findByRole('region', { name: 'In this collection' });
    expect(within(collection).getAllByRole('button').map((button) => button.getAttribute('aria-label')?.split(',')[0])).toEqual(['Lantern Returns', 'Lantern Rises']);
    expect(screen.queryByRole('region', { name: 'Cast' })).toBeNull();
    expect(api.getKeyScenes).not.toHaveBeenCalled();
  });

  it('shows performers with their photo in Cast and credits director, writer and producer beside the story', async () => {
    const portrait = '/api/art/sig/person-1/Primary/key-240.webp';
    api.getTitle.mockResolvedValue({ ...movie, people: [
      { name: 'Mara Quill', role: 'Keeper', type: 'Actor', image_url: portrait },
      { name: 'Andre Coutu', role: 'Post Producer', type: 'Producer' },
      // NFO <director>/<credits> arrive after the cast (title_metadata._credits), with their photo when one matched
      { id: 'person-9', name: 'Ines Varga', type: 'Director', image_url: '/api/art/sig/person-9/Primary/key-240.webp' },
      { name: 'Wren Hale', type: 'Writer' },
    ] });
    render(page());
    const cast = await screen.findByRole('region', { name: 'Cast' });
    expect(within(cast).getAllByRole('button')).toHaveLength(1);
    expect(within(cast).getByRole('button', { name: /Mara Quill/ }).querySelector('img')?.getAttribute('src')).toBe(resolveArtworkUrl(portrait));
    for (const crew of [/Andre Coutu/, /Ines Varga/, /Wren Hale/]) expect(within(cast).queryByRole('button', { name: crew })).toBeNull();
    for (const text of ['Produced by', 'Andre Coutu', 'Directed by', 'Ines Varga', 'Written by', 'Wren Hale']) expect(screen.getByText(text)).toBeTruthy();
  });

  it('lists up to 20 people who search when chosen, extras without the trailer, and More like this', async () => {
    const people = Array.from({ length: 25 }, (_, index) => ({ name: `Person ${index + 1}`, role: index ? null : 'Keeper', type: 'Actor' as const }));
    const featurette = { item_id: 'extra-1', extra_type: 'featurette' as const, name: 'Making the lamp', duration_seconds: 300 };
    api.getTitle.mockResolvedValue({ ...movie, people, extras: [...movie.extras, featurette] });
    const similar = movieSummary('movie-9', { name: 'Southern Lantern' });
    api.listSimilarTitles.mockResolvedValue([similar]);
    const onSearch = vi.fn();
    const onPlay = vi.fn();
    const onOpenTitle = vi.fn();
    render(page({ onOpenTitle, onPlay, onSearch }));
    const cast = await screen.findByRole('region', { name: 'Cast' });
    expect(within(cast).getAllByRole('button')).toHaveLength(20);
    await userEvent.click(within(cast).getByRole('button', { name: /^Person 1(?!\d)/ }));
    expect(onSearch).toHaveBeenCalledWith('Person 1');
    const extras = screen.getByRole('region', { name: 'Extras' });
    expect(within(extras).getAllByRole('button').map((button) => button.textContent)).toEqual(['Making the lampFeaturette · 5:00']);
    await userEvent.click(within(extras).getByRole('button'));
    expect(onPlay).toHaveBeenCalledWith('extra-1');
    await userEvent.click(await screen.findByRole('button', { name: /^Southern Lantern/ }));
    expect(onOpenTitle).toHaveBeenCalledWith(similar);
  });

  it('puts a reason and a menu under each More like this poster when the server annotated them, and hides one on Not interested', async () => {
    api.getTitle.mockResolvedValue(movie);
    const similar = [recoTitle(1, 0), recoTitle(2, 1)];
    api.listSimilarTitles.mockResolvedValue(similar);
    const suppress = vi.fn().mockResolvedValue({ id: 'sup-9' });
    render(<RecoFeedbackProvider onError={vi.fn()} onExpired={vi.fn()} resetKey="m" restore={vi.fn()} suppress={suppress}>{page()}</RecoFeedbackProvider>);
    await screen.findAllByRole('button', { name: /^More options for Recommended Film/ });
    expect(screen.queryByText('Like Arrival')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'More options for Recommended Film 1' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'title', title_id: similar[0].id }));
    expect(await screen.findByText('Hidden.')).toBeTruthy();
  });

  it('shows no AI section, no error and no empty frame with every AI feature off', async () => {
    api.getRecap.mockResolvedValue({ episode_title_id: 'ep-2-4', state: 'fallback', points: [], fallback: [], suggest_preroll: false });
    api.getTitle.mockResolvedValue(show());
    api.listSeasonEpisodes.mockResolvedValue([episode(2, 1, { user_data: userData({ played: true }) })]);
    render(page({ id: 'series-1' }));
    await screen.findByRole('button', { name: 'Play 1. Episode 1, S2 · E1, watched' });
    await settle();
    expect(screen.queryByRole('region', { name: 'The story so far' })).toBeNull();
    expect(screen.queryByRole('region', { name: /Key scenes/ })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('The keepers argue about the lamp.')).toBeTruthy();
    expect(api.getEpisodeSummaries).toHaveBeenCalledWith('series-1', 2, { timeoutMs: 8000 });
  });

  it('has no structural accessibility violations on a series page (ported)', async () => {
    api.getTitle.mockResolvedValue(show({ people: [{ name: 'Ines Varga', type: 'Creator' }, { name: 'Mara Quill', role: 'Keeper', type: 'Actor' }] }));
    api.listSeasonEpisodes.mockResolvedValue([episode(2, 1)]);
    const { container } = render(page({ id: 'series-1' }));
    await screen.findByRole('button', { name: /^Play 1\. Episode 1, S2 · E1/ });
    const results = await axe.run(container, { runOnly: ['aria-allowed-attr', 'aria-prohibited-attr', 'aria-required-children', 'aria-required-parent', 'aria-valid-attr-value', 'button-name', 'definition-list', 'dlitem', 'image-alt', 'label', 'list', 'listitem', 'nested-interactive'] });
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });

  it('Up/Down leave the version radios for the actions without changing the version (ported)', async () => {
    api.getTitle.mockResolvedValue(movie);
    // jsdom has no layout: versions on one line, actions on the line below, everything else at the top.
    const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const peers = [...(this.parentElement?.closest('[data-focus-row]') ?? this.parentElement ?? this).querySelectorAll('[data-focus-item]')];
      const top = this.closest('.t-versions') ? 100 : this.closest('.t-actions') ? 200 : 0;
      return { left: peers.indexOf(this) * 120, top, width: 100, height: 48, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
    render(page());
    const radio = await screen.findByRole('radio', { name: '4K HDR · 58 GB' });
    radio.focus();
    expect(fireEvent.keyDown(radio, { key: 'ArrowDown' })).toBe(false);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Resume at 42:10' }));
    expect((radio as HTMLInputElement).checked).toBe(true);
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(radio);
    expect(fireEvent.keyDown(radio, { key: 'ArrowRight' })).toBe(true);
    rect.mockRestore();
  });

  it('the page is reachable by arrow keys from load, no Tab needed (ported)', async () => {
    api.getTitle.mockResolvedValue(show());
    render(page({ id: 'series-1' }));
    const heading = await screen.findByRole('heading', { level: 1, name: 'Harbor Lights' });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    fireEvent.keyDown(heading, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Play S2 · E4' }));
    expect((await screen.findByRole('tab', { name: 'Season 2' })).hasAttribute('data-focus-item')).toBe(true);
  });

  it('Play hands the app the warmed item with no start, so the player resumes where the early session started, and the page closing after the claim cancels nothing new', async () => {
    const warm = await import('../../playbackPrefetch');
    const claim = vi.fn();
    const onPlay = vi.fn(() => claim());
    api.getTitle.mockResolvedValue(movie);
    const { unmount } = render(page({ onPlay }));
    const resume = await screen.findByRole('button', { name: 'Resume at 42:10' });
    vi.useFakeTimers();
    fireEvent.pointerEnter(resume);
    act(() => { vi.advanceTimersByTime(SPECULATE_AFTER_MS); });
    vi.useRealTimers();
    expect(warm.speculativeStart).toHaveBeenCalledWith('v4k');
    fireEvent.click(resume);
    // No startSeconds: the player reads the /playback resume point, exactly what speculativeStart started from.
    expect(onPlay.mock.calls).toEqual([['v4k']]);
    expect(warm.cancelSpeculativeStart).not.toHaveBeenCalled();
    unmount();
    expect(warm.cancelSpeculativeStart).toHaveBeenCalledWith('v4k');
    expect(claim.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(warm.cancelSpeculativeStart).mock.invocationCallOrder[0]);
  });
});

describe('GalleryTitlePage: the way back', () => {
  it('Escape before the detail arrives goes back to the clicked title’s wall, not the library', async () => {
    rememberSummary(seriesSummary('series-1'));
    api.getTitle.mockReturnValue(new Promise(() => undefined));
    const onBack = vi.fn();
    const view = render(page({ id: 'series-1', onBack }));
    fireEvent.keyDown(view.container.querySelector('.t-page') as HTMLElement, { key: 'Escape' });
    expect(onBack).toHaveBeenCalledWith('series', null);
  });

  it('a failed load of a clicked title goes back to its wall', async () => {
    rememberSummary(seriesSummary('series-1'));
    api.getTitle.mockRejectedValue(new ApiRequestError('boom', 500));
    const onBack = vi.fn();
    render(page({ id: 'series-1', onBack }));
    fireEvent.click(await screen.findByRole('button', { name: 'Back to your library' }));
    expect(onBack).toHaveBeenCalledWith('series', null);
  });

  it('Up from the headline reaches Back, arrows reach Part of, and the seasons are headed Episodes', async () => {
    const boxset = movieSummary('boxset-1', { type: 'boxset', name: 'Lantern Collection' });
    api.getTitle.mockResolvedValue({ ...movie, boxset });
    const first = render(page());
    const heading = await screen.findByRole('heading', { level: 1 });
    expect(fireEvent.keyDown(heading, { key: 'ArrowUp' })).toBe(false);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Back to Movies' }));
    expect(screen.getByRole('button', { name: 'Lantern Collection' }).hasAttribute('data-focus-item')).toBe(true);
    first.unmount();
    api.getTitle.mockResolvedValue(show());
    render(page({ id: 'series-1' }));
    const episodes = await screen.findByRole('region', { name: 'Episodes' });
    expect(within(episodes).getByRole('tablist', { name: 'Seasons' })).toBeTruthy();
  });
});

describe('GalleryTitlePage: music and the Back lens', () => {
  it('draws a clicked album as the album page, with Back to Music, and never asks for similar titles', async () => {
    rememberSummary(albumSummary());
    api.getTitle.mockResolvedValue(albumDetail());
    const onBack = vi.fn();
    render(page({ id: 'album-1', onBack }));
    expect(screen.getByRole('heading', { level: 1, name: 'Album One' })).toBeTruthy();
    expect(await screen.findByRole('button', { name: /^Play 1\. Song 1/ })).toBeTruthy();
    expect(api.listSimilarTitles).not.toHaveBeenCalled();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1 }))); // focus lands in a passive effect, after the tracks are findable
    await userEvent.click(screen.getByRole('button', { name: 'Back to Music' }));
    expect(onBack).toHaveBeenCalledWith('album', null);
  });

  it('draws a deep-linked artist as the artist page once it arrives', async () => {
    api.getTitle.mockResolvedValue(titleDetail(artistSummary(), { children: [albumSummary()] }));
    render(page({ id: 'artist-1' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Artist A' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Album One by Artist A, 2019, 12 tracks' })).toBeTruthy();
    expect(api.listSimilarTitles).not.toHaveBeenCalled(); // music has no More like this, deep-linked too
  });

  it('labels Back with the title’s own lens: an anime movie goes back to Anime', async () => {
    api.getTitle.mockResolvedValue(titleDetail(movieSummary('movie-1', { category: 'anime' })));
    const onBack = vi.fn();
    render(page({ onBack }));
    await userEvent.click(await screen.findByRole('button', { name: 'Back to Anime' }));
    expect(onBack).toHaveBeenCalledWith('movie', 'anime');
  });
});
