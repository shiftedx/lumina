import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { episodeSummary, movieSummary, seriesSummary, stillItem, titleArt, titleDetail } from '../../test/galleryFixtures';
import type { PlaybackProgress, TitleDetail, TitleSummary } from '../../types';
import { HomeHero, WARM_AFTER_MS } from './HomeHero';
import type { HeroPick } from './homeModel';

const prefetch = vi.hoisted(() => ({ prefetchPlaybackOptions: vi.fn(), speculativeStart: vi.fn(), cancelSpeculativeStart: vi.fn() }));
vi.mock('../../playbackPrefetch', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../playbackPrefetch')>()), ...prefetch }));
const cache = vi.hoisted(() => ({ loadTitle: vi.fn() }));
vi.mock('../gallery/titleCache', async (importOriginal) => ({ ...(await importOriginal<typeof import('../gallery/titleCache')>()), ...cache }));
const metrics = vi.hoisted(() => ({ recordMetric: vi.fn() }));
vi.mock('../../perfMetrics', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../perfMetrics')>()), ...metrics }));

const AT = '2026-09-28T20:00:00Z';
const entry = (title: TitleSummary): PlaybackProgress => ({
  id: `pb-${title.id}`, user_id: 'member-1', item_id: `item-${title.id}`, position_seconds: 1200, duration_seconds: 2640, completed: false,
  last_watched_at: AT, created_at: AT, updated_at: AT, item: stillItem(`item-${title.id}`), title,
});
const continuePick = (title = episodeSummary(2, 4)): HeroPick => ({ kind: 'continue', entry: entry(title), title });
const flush = () => act(async () => { for (let tick = 0; tick < 3; tick += 1) await Promise.resolve(); });

function renderHero(pick: HeroPick, detail: TitleDetail | Error, props: { ready?: boolean } = {}) {
  cache.loadTitle.mockImplementation(async () => { if (detail instanceof Error) throw detail; return detail; });
  const onPlay = vi.fn();
  const onOpenTitle = vi.fn();
  const view = render(<HomeHero onOpenTitle={onOpenTitle} onPlay={onPlay} ready={props.ready ?? true} slides={[pick]} />);
  return { ...view, onPlay, onOpenTitle };
}

beforeEach(() => { Object.values(prefetch).forEach((mock) => mock.mockReset()); cache.loadTitle.mockReset(); metrics.recordMetric.mockReset(); });
afterEach(() => { vi.useRealTimers(); Reflect.deleteProperty(navigator, 'connection'); });

describe('HomeHero', () => {
  it('Resume plays the Continue entry\'s item; Details opens the anchor once loaded', async () => {
    const series = titleDetail(seriesSummary('series-1'));
    const { onPlay, onOpenTitle } = renderHero(continuePick(), series);
    expect(cache.loadTitle).toHaveBeenCalledWith('series-1');
    expect(screen.getByText('Continue watching').className).toContain('h-hero-kicker');
    expect(screen.getByRole('heading', { level: 2, name: 'Harbor Lights' })).toBeTruthy();
    expect(screen.getByText('S2 · E4 · Episode 4 · 24 min left')).toBeTruthy();
    expect((document.querySelector('.h-hero-progress > span') as HTMLElement).style.width).toMatch(/^45\.45/);
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    expect(onPlay).toHaveBeenCalledWith('item-ep-2-4');
    await flush();
    fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(onOpenTitle).toHaveBeenCalledWith(series);
  });

  it('Play uses the newest title\'s primary action, and a reason replaces the button', async () => {
    const movie = movieSummary('m', { play_item_id: 'item-m' });
    const { onPlay, unmount } = renderHero({ kind: 'newest', title: movie }, titleDetail(movie));
    expect(screen.getByText('New in your library')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Play' })).toBeNull(); // the action needs the detail
    await flush();
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(onPlay).toHaveBeenCalledWith('item-m');
    unmount();
    renderHero({ kind: 'newest', title: movie }, titleDetail(movie, { play_item_id: null }));
    await flush();
    expect(screen.queryByRole('button', { name: 'Play' })).toBeNull();
    expect(screen.getByText('No playable file is available right now.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Details' })).toBeTruthy();
  });

  it('keeps the summary field, headline and actions when the detail fails, and times the field as the finished hero', async () => {
    const { onPlay } = renderHero(continuePick(), new Error('503'));
    await flush();
    expect(document.querySelector('.h-slide')?.className).toContain('is-field');
    expect(screen.getByRole('heading', { level: 2, name: 'Harbor Lights' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    expect(onPlay).toHaveBeenCalledWith('item-ep-2-4');
    expect(metrics.recordMetric).toHaveBeenCalledTimes(1);
    expect(metrics.recordMetric).toHaveBeenCalledWith('home_hero_ms', 'home', expect.any(Number));
  });

  it('draws the backdrop, else the poster panel, else the colour field', async () => {
    const movie = movieSummary('m');
    const { unmount } = renderHero({ kind: 'newest', title: movie }, titleDetail(movie));
    await flush();
    expect(document.querySelector('.h-slide.is-backdrop .h-hero-art')).not.toBeNull();
    unmount();
    const plain = movieSummary('p', { backdrop: null });
    const second = renderHero({ kind: 'newest', title: plain }, titleDetail(plain));
    await flush();
    expect(document.querySelector('.h-slide.is-poster .h-hero-poster')).not.toBeNull();
    expect(document.querySelector('.h-slide.is-poster .h-hero-blur')).not.toBeNull();
    second.unmount();
    const bare = movieSummary('b', { backdrop: null, poster: null });
    renderHero({ kind: 'newest', title: bare }, titleDetail(bare));
    await flush();
    expect(document.querySelector('.h-slide.is-field')).not.toBeNull();
  });

  it('warms the playback decision once, after the first screen and a second, and never under Save-Data', async () => {
    vi.useFakeTimers();
    const { rerender, onOpenTitle, onPlay } = renderHero(continuePick(), titleDetail(seriesSummary('series-1')), { ready: false });
    await act(() => vi.advanceTimersByTimeAsync(2 * WARM_AFTER_MS));
    expect(prefetch.prefetchPlaybackOptions).not.toHaveBeenCalled();
    rerender(<HomeHero onOpenTitle={onOpenTitle} onPlay={onPlay} ready slides={[continuePick()]} />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(prefetch.prefetchPlaybackOptions).toHaveBeenCalledTimes(1);
    expect(prefetch.prefetchPlaybackOptions).toHaveBeenCalledWith('item-ep-2-4');
    rerender(<HomeHero onOpenTitle={onOpenTitle} onPlay={onPlay} ready slides={[continuePick()]} />);
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    expect(prefetch.prefetchPlaybackOptions).toHaveBeenCalledTimes(1);
  });

  it('does not warm before a second has passed since mount, nor with Save-Data on', async () => {
    vi.useFakeTimers();
    renderHero(continuePick(), titleDetail(seriesSummary('series-1')));
    await act(() => vi.advanceTimersByTimeAsync(WARM_AFTER_MS - 1));
    expect(prefetch.prefetchPlaybackOptions).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(prefetch.prefetchPlaybackOptions).toHaveBeenCalledTimes(1);
    Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } });
    prefetch.prefetchPlaybackOptions.mockReset();
    renderHero(continuePick(episodeSummary(1, 1)), titleDetail(seriesSummary('series-1')));
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    expect(prefetch.prefetchPlaybackOptions).not.toHaveBeenCalled();
  });

  it('400 ms of focus or hover on Resume may start the conversion early; blur, leave and unmount cancel', async () => {
    vi.useFakeTimers();
    const { unmount } = renderHero(continuePick(), titleDetail(seriesSummary('series-1')));
    const resume = screen.getByRole('button', { name: 'Resume' });
    fireEvent.focus(resume);
    await act(() => vi.advanceTimersByTimeAsync(399));
    expect(prefetch.speculativeStart).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(prefetch.speculativeStart).toHaveBeenCalledWith('item-ep-2-4');
    fireEvent.blur(resume);
    expect(prefetch.cancelSpeculativeStart).toHaveBeenLastCalledWith('item-ep-2-4');
    fireEvent.pointerEnter(resume);
    fireEvent.pointerLeave(resume);
    expect(prefetch.cancelSpeculativeStart).toHaveBeenCalledTimes(2);
    fireEvent.pointerEnter(resume);
    unmount();
    expect(prefetch.cancelSpeculativeStart).toHaveBeenCalledTimes(3);
  });

  it('composes a portrait backdrop as a poster instead of stretching it', async () => {
    const movie = movieSummary('tall', { backdrop: titleArt('tall', 'Backdrop', { width: 1000, height: 1500 }) });
    renderHero({ kind: 'newest', title: movie }, titleDetail(movie));
    await flush();
    expect(document.querySelector('.h-slide.is-poster .h-hero-poster')).not.toBeNull();
  });
});

describe('HomeHero carousel', () => {
  const slides = (): HeroPick[] => [
    continuePick(),
    { kind: 'recommended', title: movieSummary('rec-1', { name: 'Quiet Harbour' }), reason: 'Because you watched Harbor Lights' },
    { kind: 'recommended', title: movieSummary('rec-2', { name: 'Far Field' }), reason: null },
  ];
  function renderCarousel() {
    cache.loadTitle.mockImplementation(async (id: string) => titleDetail(id === 'series-1' ? seriesSummary('series-1') : movieSummary(id, { name: id === 'rec-1' ? 'Quiet Harbour' : 'Far Field' })));
    return render(<HomeHero onOpenTitle={vi.fn()} onPlay={vi.fn()} ready slides={slides()} />);
  }
  const slide = () => screen.getByRole('tabpanel');

  it('labels the carousel and its slides, and pips are tabs that change slide', async () => {
    renderCarousel();
    await flush();
    expect(screen.getByRole('region', { name: 'Featured' }).getAttribute('aria-roledescription')).toBe('carousel');
    expect(slide().getAttribute('aria-label')).toBe('1 of 3');
    expect(slide().getAttribute('aria-roledescription')).toBe('slide');
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((tab) => tab.getAttribute('aria-label'))).toEqual(['Harbor Lights', 'Quiet Harbour', 'Far Field']);
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(document.querySelector('.h-hero-stage')?.getAttribute('aria-live')).toBe('off');
    fireEvent.click(tabs[1]);
    await flush();
    expect(slide().getAttribute('aria-label')).toBe('2 of 3');
    expect(screen.getByText('Recommended for you')).toBeTruthy();
    expect(screen.getByText('Because you watched Harbor Lights')).toBeTruthy();
    // A member's change is announced politely; the automatic advance is not.
    expect(document.querySelector('.h-hero-stage')?.getAttribute('aria-live')).toBe('polite');
  });

  it('arrow keys on the pips and the step buttons move both ways, wrapping', async () => {
    renderCarousel();
    await flush();
    fireEvent.keyDown(screen.getAllByRole('tab')[0], { key: 'ArrowLeft' });
    expect(slide().getAttribute('aria-label')).toBe('3 of 3');
    expect(document.activeElement).toBe(screen.getAllByRole('tab')[2]);
    fireEvent.click(screen.getByRole('button', { name: 'Next', hidden: true }));
    expect(slide().getAttribute('aria-label')).toBe('1 of 3');
    fireEvent.click(screen.getByRole('button', { name: 'Previous', hidden: true }));
    expect(slide().getAttribute('aria-label')).toBe('3 of 3');
  });

  it('advances when the current pip fills, pauses while hovered or focused, and swipes on touch', async () => {
    renderCarousel();
    await flush();
    // jsdom has no AnimationEvent, so React listens for the prefixed name.
    act(() => { document.querySelector('.h-pip-fill')?.dispatchEvent(new Event(typeof AnimationEvent === 'undefined' ? 'webkitAnimationEnd' : 'animationend', { bubbles: true })); });
    expect(slide().getAttribute('aria-label')).toBe('2 of 3');
    expect(document.querySelector('.h-hero-stage')?.getAttribute('aria-live')).toBe('off');
    const hero = screen.getByRole('region', { name: 'Featured' });
    fireEvent.pointerEnter(hero, { pointerType: 'mouse' });
    expect(hero.className).toContain('is-paused');
    fireEvent.pointerLeave(hero);
    expect(hero.className).not.toContain('is-paused');
    fireEvent.pointerDown(hero, { pointerType: 'touch', clientX: 300 });
    fireEvent.pointerUp(hero, { pointerType: 'touch', clientX: 200 });
    expect(slide().getAttribute('aria-label')).toBe('3 of 3');
  });
});
