import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resolveArtworkUrl } from '../../Artwork';
import { movieSummary, seriesSummary, userData } from '../../test/galleryFixtures';
import type { TitleSummary } from '../../types';
import { renditionUrl } from './galleryModel';
import * as loader from './imageLoader';
import { INTENT_DELAY_MS, PosterCard } from './PosterCard';
import * as cache from './titleCache';

function pointer(fine: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string) => ({ matches: fine && query.includes('pointer: fine'), media: query, addEventListener() {}, removeEventListener() {} }),
  });
}
const card = (title: TitleSummary, caption = true) => render(<PosterCard caption={caption} onOpen={vi.fn()} priority={2} sizes="180px" title={title} />);

beforeEach(() => { loader.resetImageLoader(); pointer(true); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('PosterCard', () => {
  it('marks an unwatched movie with the gold corner and names it in words', () => {
    const { container } = card(movieSummary());
    const button = screen.getByRole('button', { name: 'Northern Lantern, 2019, unwatched' });
    expect(button.querySelector('.g-marker-triangle')?.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('.g-caption')?.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('.g-caption-title')?.textContent).toBe('Northern Lantern');
    expect(container.querySelector('.g-caption-year')?.textContent).toBe('2019');
  });

  it('shows the progress bar without the corner, the episode count up to 99+, and nothing once watched', () => {
    const started = card(movieSummary('m1', { user_data: userData({ position_seconds: 3360, duration_seconds: 6720 }) }));
    expect(started.container.querySelector<HTMLElement>('.g-marker-progress > span')?.style.width).toBe('50%');
    expect(started.container.querySelector('.g-marker-triangle')).toBeNull();
    expect(card(seriesSummary('s1')).container.querySelector('.g-marker-count')?.textContent).toBe('4');
    expect(card(seriesSummary('s2', { user_data: userData({ unplayed_count: 120 }) })).container.querySelector('.g-marker-count')?.textContent).toBe('99+');
    const watched = card(movieSummary('m2', { user_data: userData({ played: true }) }), false);
    expect(watched.container.querySelector('.g-marker-triangle, .g-marker-count, .g-marker-progress')).toBeNull();
    expect(watched.container.querySelector('.g-caption')).toBeNull();
  });

  it('remembers the summary for the title page before opening it', async () => {
    const onOpen = vi.fn((title: TitleSummary) => expect(cache.summaryFor(title.id)?.id).toBe('movie-7'));
    render(<PosterCard onOpen={onOpen} priority={2} sizes="180px" title={movieSummary('movie-7')} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'movie-7' }));
  });

  it('warms the backdrop and the detail after 150 ms of hover or focus, on desktop pointers only', () => {
    vi.useFakeTimers();
    const prefetchImage = vi.spyOn(loader, 'prefetchImage').mockImplementation(() => undefined);
    const prefetchTitle = vi.spyOn(cache, 'prefetchTitle').mockImplementation(() => undefined);
    const onFocus = vi.fn();
    const title = movieSummary('movie-8');
    render(<PosterCard onFocus={onFocus} onOpen={vi.fn()} priority={2} sizes="180px" title={title} />);
    const button = screen.getByRole('button');
    fireEvent.pointerEnter(button);
    act(() => { vi.advanceTimersByTime(INTENT_DELAY_MS - 1); });
    fireEvent.pointerLeave(button);
    act(() => { vi.advanceTimersByTime(10); });
    expect(prefetchTitle).not.toHaveBeenCalled();
    fireEvent.focus(button);
    expect(onFocus).toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(INTENT_DELAY_MS); });
    expect(prefetchImage).toHaveBeenCalledWith(resolveArtworkUrl(renditionUrl(title.backdrop, 960)), 3);
    expect(prefetchTitle).toHaveBeenCalledWith('movie-8');
    fireEvent.blur(button);
    pointer(false);
    prefetchTitle.mockClear();
    fireEvent.focus(button);
    act(() => { vi.advanceTimersByTime(INTENT_DELAY_MS); });
    expect(prefetchTitle).not.toHaveBeenCalled();
  });

  it('in select mode shows a checkbox state and passes the click event to onOpen', () => {
    const onOpen = vi.fn();
    render(<PosterCard onOpen={onOpen} priority={2} selectable selected sizes="180px" title={movieSummary()} />);
    const button = screen.getByRole('button', { name: /Northern Lantern/ });
    expect(button.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(button, { shiftKey: true });
    expect(onOpen.mock.calls[0][1].shiftKey).toBe(true);
    expect(button.querySelector('.g-select-box')).toBeTruthy();
  });

  it('is unchanged outside select mode', () => {
    const { container } = card(movieSummary());
    expect(container.querySelector('.g-select-box')).toBeNull();
    expect(screen.getByRole('button').getAttribute('aria-pressed')).toBeNull();
  });
});
