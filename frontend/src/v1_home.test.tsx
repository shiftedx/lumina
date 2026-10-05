import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { HomeEditorProps } from './features/home/HomeEditor';
import { forgetHomeCache } from './features/home/homeCache';
import { HomeSurface, type HomeSurfaceProps } from './features/home/HomeSurface';
import type { LiveShelfProps } from './features/home/LiveShelf';
import { ToastProvider } from './ui';
import { forgetTitles } from './features/gallery/titleCache';
import { resetWatchQueue } from './features/watch/WatchQueue';
import { episodeSummary, movieSummary, stillItem, titleDetail } from './test/galleryFixtures';
import type { PlaybackProgress, TitleSummary, UserProfile, WatchQueue, YouTubeSearchResult } from './types';

const api = vi.hoisted(() => ({
  listNextUp: vi.fn(), listTitles: vi.fn(), getHomeTitleRows: vi.fn(), listHouseholdCollections: vi.fn(), getHouseholdCollection: vi.fn(),
  dismissNextUp: vi.fn(), getWatchQueue: vi.fn(), getTitle: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const metrics = vi.hoisted(() => ({ recordMetric: vi.fn() }));
vi.mock('./perfMetrics', async (importOriginal) => ({ ...(await importOriginal<typeof import('./perfMetrics')>()), ...metrics }));
// The editor and the Live shelf are tested separately; this file tests the page through their frozen props.
const live = vi.hoisted(() => ({ mounted: vi.fn() }));
vi.mock('./features/home/LiveShelf', async () => {
  const { useEffect } = await import('react');
  return {
    LiveShelf: ({ onStatus }: LiveShelfProps) => {
      live.mounted();
      useEffect(() => onStatus('empty'), []); // eslint-disable-line react-hooks/exhaustive-deps -- a stand-in that reports once
      return null;
    },
  };
});
vi.mock('./features/home/HomeEditor', () => ({
  HomeEditor: ({ shelves, onChange, onDone }: HomeEditorProps) => (
    <div className="h-editor">
      <ol aria-label="Home shelves">{shelves.map((shelf) => <li key={shelf.id}>{shelf.id}</li>)}</ol>
      <button onClick={() => onChange(null)} type="button">Reset to default</button>
      <button onClick={onDone} type="button">Done</button>
    </div>
  ),
}));

const AT = '2026-09-28T20:00:00Z';
const user = { id: 'user-1', username: 'one', display_name: 'One Person', role: 'viewer', is_active: true } as UserProfile;
const entry = (title: TitleSummary | null, patch: Partial<PlaybackProgress> = {}): PlaybackProgress => ({
  id: `pb-${title?.id ?? 'video'}`, user_id: user.id, item_id: `item-${title?.id ?? 'video'}`, position_seconds: 1200, duration_seconds: 2640, completed: false,
  last_watched_at: AT, created_at: AT, updated_at: AT, item: stillItem(`item-${title?.id ?? 'video'}`, { title_id: title?.id ?? null }), title, ...patch,
});
const queue = (entries: WatchQueue['entries']): WatchQueue => ({ revision: 1, limit: 500, entries });
const remote = (id: string): YouTubeSearchResult => ({ id, title: `Followed ${id}`, uploader: 'Creator', webpage_url: `https://example.test/f/${id}`, duration: 610 });
const sections = () => [...document.querySelectorAll('[data-shelf-section]')].map((section) => section.getAttribute('data-shelf-section'));

function renderHome(overrides: Partial<HomeSurfaceProps> = {}) {
  const props: HomeSurfaceProps = {
    channels: [], continueWatching: [], homeShelves: null, isQueueing: () => false, library: [], libraryProblem: null, libraryRetrying: false, libraryState: 'ready',
    onHomeShelvesChange: vi.fn(), onNavigate: vi.fn(), onOpenLibrary: vi.fn(), onOpenRemote: vi.fn(), onOpenRoute: vi.fn(), onOpenTitle: vi.fn(), onPlay: vi.fn(),
    onQueueRemote: vi.fn(), onRetryLibrary: vi.fn(), onSignIn: vi.fn(), recentLibrary: [], subscriptionOutcomes: [], subscriptionVideos: [], user, ...overrides,
  };
  return { ...render(<ToastProvider><HomeSurface {...props} /></ToastProvider>), props };
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.listNextUp.mockResolvedValue([]);
  api.listTitles.mockResolvedValue({ items: [] });
  api.getHomeTitleRows.mockResolvedValue({ rows: [] });
  api.listHouseholdCollections.mockResolvedValue([]);
  api.getWatchQueue.mockResolvedValue(queue([]));
  api.dismissNextUp.mockResolvedValue(undefined);
  api.getTitle.mockImplementation(async (id: string) => titleDetail(movieSummary(id)));
  metrics.recordMetric.mockReset();
  live.mounted.mockReset();
});
afterEach(() => { resetWatchQueue(); forgetHomeCache(); forgetTitles(); });

describe('Home page', () => {
  it('shows shelves in the member\'s order, skips hidden ones without requesting them, and hides empty ones', async () => {
    api.listTitles.mockImplementation(async (query: { category?: string; type?: string }) => ({ items: query.category === 'anime' ? [movieSummary('anime-1', { name: 'Spirited' })] : query.type ? [] : [movieSummary('new-1')] }));
    renderHome({ continueWatching: [entry(episodeSummary(2, 4))], homeShelves: [{ id: 'new_anime', visible: true }, { id: 'next_up', visible: false }, { id: 'live', visible: false }] });
    await waitFor(() => expect(sections()).toEqual(['new_anime', 'continue', 'new_in_library']));
    expect(api.listNextUp).not.toHaveBeenCalled();
    expect(live.mounted).not.toHaveBeenCalled();
    expect(document.querySelector('.h-hero')).not.toBeNull();
  });

  it('greets the member by first name under today\'s date, and records the first screen once', async () => {
    renderHome({ continueWatching: [entry(episodeSummary(2, 4))] });
    expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/^Good (morning|afternoon|evening), One$/);
    await waitFor(() => expect(metrics.recordMetric).toHaveBeenCalledWith('home_first_screen_ms', 'home', expect.any(Number)));
    expect(metrics.recordMetric.mock.calls.filter(([name]) => name === 'home_first_screen_ms')).toHaveLength(1);
  });

  it('with no Continue title, the hero is the newest title, fetched once for the hero and its shelf', async () => {
    api.listTitles.mockImplementation(async (query: { category?: string; type?: string }) => ({ items: query.category || query.type ? [] : [movieSummary('new-1', { name: 'Fresh Arrival' })] }));
    renderHome({ continueWatching: [entry(null)] });
    expect(await screen.findByText('New in your library', { selector: '.h-hero-kicker' })).toBeTruthy();
    await waitFor(() => expect(sections()).toContain('new_in_library'));
    expect(api.listTitles.mock.calls.filter(([query]) => !query.category && !query.type)).toHaveLength(1);
  });

  it('with no Continue title, a return visit paints the cached hero at once instead of popping in (P-M1)', async () => {
    api.listTitles.mockImplementation(async (query: { category?: string; type?: string }) => ({ items: query.category || query.type ? [] : [movieSummary('new-1', { name: 'Fresh Arrival' })] }));
    const { unmount } = renderHome({ continueWatching: [entry(null)] });
    await screen.findByText('New in your library', { selector: '.h-hero-kicker' });
    unmount();
    renderHome({ continueWatching: [entry(null)] });
    // Seeded from the session cache: present in the very first commit, with no await for the /api/titles round-trip.
    expect(screen.getByText('New in your library', { selector: '.h-hero-kicker' })).toBeTruthy();
  });

  it('a fresh member gets one start card and no Edit home', async () => {
    const onOpenRoute = vi.fn();
    renderHome({ libraryState: 'empty', onOpenRoute, onOpenQueued: vi.fn(), onPersonalize: vi.fn(), interests: { categories: [{ key: 'music', label: 'Music' }], selected_keys: [] } });
    // The personalize prompt shares the heading until every shelf has settled empty; Discover something is the start card's alone.
    await screen.findByRole('button', { name: 'Discover something' });
    expect(screen.getByRole('heading', { name: 'Make Home yours' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit home' })).toBeNull();
    expect(sections()).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Discover something' }));
    expect(onOpenRoute).toHaveBeenCalledWith({ surface: 'streaming', view: 'home' });
    expect(screen.getByRole('button', { name: 'Choose interests' })).toBeTruthy();
  });

  it('keeps Edit home when a shelf is hidden, even if everything else settles empty (P-I2)', async () => {
    // A hidden shelf's content is unknown, so hiding the only non-empty title shelf must not flip Home to "fresh".
    renderHome({ libraryState: 'empty', homeShelves: [{ id: 'new_in_library', visible: false }] });
    // Settle: every visible fetched shelf has resolved empty, so without the fix "fresh" would now flip true.
    await waitFor(() => expect(sections()).toEqual([]));
    expect(screen.getByRole('button', { name: 'Edit home' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Make Home yours' })).toBeNull();
  });

  it('shows Try again when the watchlist fails to load, and retries it (P-I3)', async () => {
    api.getWatchQueue.mockRejectedValueOnce(new Error('offline'));
    renderHome({ onOpenQueued: vi.fn() });
    const watchlist = await screen.findByRole('region', { name: 'Your watchlist' });
    expect(within(watchlist).getByText('Lumina could not load Your watchlist.')).toBeTruthy();
    api.getWatchQueue.mockResolvedValueOnce(queue([]));
    fireEvent.click(within(watchlist).getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(api.getWatchQueue).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(within(watchlist).queryByText('Lumina could not load Your watchlist.')).toBeNull());
  });

  it('offers the personalize prompt when interests exist and none are chosen', async () => {
    const onPersonalize = vi.fn();
    renderHome({ continueWatching: [entry(episodeSummary(2, 4))], interests: { categories: [{ key: 'music', label: 'Music' }], selected_keys: [] }, onPersonalize });
    const prompt = screen.getByRole('region', { name: 'Make Home yours' });
    fireEvent.click(within(prompt).getByRole('button', { name: 'Choose interests' }));
    expect(onPersonalize).toHaveBeenCalledTimes(1);
  });

  it('removes a Continue title with Undo', async () => {
    const onHide = vi.fn(async () => undefined);
    const onRestore = vi.fn(async () => undefined);
    const continuing = entry(episodeSummary(1, 3));
    renderHome({ continueWatching: [continuing], onHideContinueWatching: onHide, onRestoreContinueWatching: onRestore });
    fireEvent.click(screen.getByRole('button', { name: 'Remove Harbor Lights from Continue watching' }));
    expect(onHide).toHaveBeenCalledWith(continuing);
    expect(await screen.findByText('Removed from Continue watching.')).toBeTruthy();
    expect(screen.getByText('Removed from Continue watching.').closest('.g-toast-region')).not.toBeNull(); // the global toast, not a Home-local one
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(onRestore).toHaveBeenCalledWith(continuing));
  });

  it('dismisses a Next up show, and puts it back if the server refuses', async () => {
    api.listNextUp.mockResolvedValue([episodeSummary(2, 5)]);
    api.dismissNextUp.mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined);
    renderHome();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Harbor Lights from Next up' }));
    expect(await screen.findByText('Lumina could not remove that. Try again.')).toBeTruthy();
    expect(api.dismissNextUp).toHaveBeenCalledWith('series-1');
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Harbor Lights from Next up' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Next up' })).toBeNull());
  });

  it('says when followed channels need attention, under the follows shelf', async () => {
    const onOpenRoute = vi.fn();
    renderHome({ onOpenRoute, subscriptionVideos: [remote('x')], subscriptionOutcomes: [{ channel: { id: 'c' }, status: 'failed', items: [], channelArtworkUrl: null, message: null } as never] });
    const follows = await screen.findByRole('region', { name: 'New from your follows' }); // the body is unpainted (and out of the accessibility tree) until the hero is decided
    expect(follows.textContent).toContain('1 followed channel needs attention.');
    const reviewChannels = within(follows).getByRole('button', { name: 'Review channels' });
    expect(reviewChannels.hasAttribute('data-focus-item')).toBe(true); // E-I3/P-M5: reachable by arrow keys / a TV remote
    fireEvent.click(reviewChannels);
    expect(onOpenRoute).toHaveBeenCalledWith({ surface: 'streaming', view: 'channels' });
    expect(within(follows).getByRole('button', { name: 'Followed x, Creator · 10:10' })).toBeTruthy();
  });

  it('keeps the library recovery states inside Recently saved', async () => {
    const onRetryLibrary = vi.fn();
    const { unmount } = renderHome({ libraryState: 'offline', onRetryLibrary });
    const saved = await screen.findByRole('region', { name: 'Recently saved' });
    expect(saved.textContent).toContain('Library is unavailable');
    const tryAgain = within(saved).getByRole('button', { name: 'Try again' });
    expect(tryAgain.hasAttribute('data-focus-item')).toBe(true); // P-M5: the library recovery buttons are reachable too
    fireEvent.click(tryAgain);
    expect(onRetryLibrary).toHaveBeenCalledTimes(1);
    unmount();
    renderHome({ libraryState: 'stale', recentLibrary: [stillItem('v1')] });
    expect((await screen.findByRole('region', { name: 'Recently saved' })).textContent).toContain('Showing last-known library');
  });

  it('lists only web-saved media in Recently saved; titled movies and episodes have their own shelves', async () => {
    renderHome({ recentLibrary: [stillItem('v1', { title: 'A saved video' }), stillItem('m1', { title: 'A movie file', title_id: 'movie-1', kind: 'movie' })] });
    const saved = await screen.findByRole('region', { name: 'Recently saved' });
    expect(saved.textContent).toContain('A saved video');
    expect(saved.textContent).not.toContain('A movie file');
  });

  it('mounts the Live shelf only while it is visible, and never in edit mode', async () => {
    renderHome({ continueWatching: [entry(episodeSummary(2, 4))] });
    expect(live.mounted).toHaveBeenCalled();
    live.mounted.mockReset();
    fireEvent.click(screen.getByRole('button', { name: 'Edit home' }));
    expect(live.mounted).not.toHaveBeenCalled();
  });
});

describe('Home edit mode', () => {
  it('Edit home enters edit mode in place; Done returns focus to it and says the layout is saved', () => {
    renderHome({ continueWatching: [entry(episodeSummary(2, 4))] });
    fireEvent.click(screen.getByRole('button', { name: 'Edit home' }));
    expect(screen.getByRole('button', { name: 'Done', pressed: true })).toBeTruthy();
    expect(document.querySelector('.h-hero')).toBeNull();
    expect(sections()).toEqual([]);
    expect(screen.getByRole('list', { name: 'Home shelves' }).children).toHaveLength(13);
    fireEvent.click(within(document.querySelector('.h-editor') as HTMLElement).getByRole('button', { name: 'Done' }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Edit home' }));
    expect(screen.getByText('Home layout saved.')).toBeTruthy();
    expect(document.querySelector('.h-hero')).not.toBeNull();
  });

  it('Reset saves the default and offers Undo, which puts the previous layout back', () => {
    const onHomeShelvesChange = vi.fn();
    const layout = [{ id: 'recent_music' as const, visible: true }];
    renderHome({ continueWatching: [entry(episodeSummary(2, 4))], homeShelves: layout, onHomeShelvesChange });
    fireEvent.click(screen.getByRole('button', { name: 'Edit home' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reset to default' }));
    expect(onHomeShelvesChange).toHaveBeenLastCalledWith(null);
    expect(screen.getByText('Home reset to the default layout.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onHomeShelvesChange).toHaveBeenLastCalledWith(layout);
  });

  it('the greeting\'s arrows: Down reaches the hero\'s Resume, Up reaches Edit home', async () => {
    renderHome({ continueWatching: [entry(episodeSummary(2, 4))] });
    const greetingHeading = screen.getByRole('heading', { level: 1 });
    greetingHeading.focus();
    fireEvent.keyDown(greetingHeading, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Resume' }));
    greetingHeading.focus();
    fireEvent.keyDown(greetingHeading, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Edit home' }));
    await act(async () => { await Promise.resolve(); });
  });
});
