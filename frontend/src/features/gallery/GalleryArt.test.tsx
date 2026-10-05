import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resolveArtworkUrl } from '../../Artwork';
import { PREVIEW, titleArt } from '../../test/galleryFixtures';
import { GalleryArt, type GalleryArtProps, REATTEMPT_AFTER_MS } from './GalleryArt';
import { GALLERY_RETRY_DELAYS_MS, renditionUrl } from './galleryModel';
import { loaderStats, resetImageLoader } from './imageLoader';

const metrics = vi.hoisted(() => ({ recordMetric: vi.fn() }));
vi.mock('../../perfMetrics', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../perfMetrics')>()), recordMetric: metrics.recordMetric }));

let status: number | undefined;
const element = (patch: Partial<GalleryArtProps> = {}) => (
  <GalleryArt alt="" art={titleArt('movie-1', 'Primary')} card={{ name: 'Northern Lantern', year: 2019 }} colour={{ colour: '#2a3b4c', fromPalette: false }} kind="poster" priority={2} sizes="180px" {...patch} />
);
const image = (container: HTMLElement) => container.querySelector<HTMLImageElement>('.g-art-image');

beforeEach(() => {
  resetImageLoader();
  status = undefined;
  Object.defineProperty(performance, 'getEntriesByName', { configurable: true, value: () => (status === undefined ? [] : [{ responseStatus: status }]) });
});
afterEach(() => { vi.useRealTimers(); metrics.recordMetric.mockReset(); });

describe('GalleryArt', () => {
  it('paints colour and blurred preview first, then reveals the sharp image with its srcset, never dimmed', async () => {
    const art = titleArt('movie-1', 'Primary');
    const { container } = render(element({ art }));
    expect(container.querySelector<HTMLElement>('.g-art')?.style.backgroundColor).toBe('rgb(42, 59, 76)');
    expect(container.querySelector('.g-art-preview')?.getAttribute('src')).toBe(PREVIEW);
    const sharp = image(container)!;
    expect(sharp.getAttribute('src')).toBe(resolveArtworkUrl(renditionUrl(art, 240)));
    expect(sharp.getAttribute('srcset')).toBe(`${resolveArtworkUrl(renditionUrl(art, 240))} 240w, ${resolveArtworkUrl(renditionUrl(art, 480))} 480w`);
    expect(sharp.getAttribute('fetchpriority')).toBe('high');
    expect(sharp.getAttribute('sizes')).toBe('180px');
    expect(sharp.className).not.toContain('is-shown');
    await act(async () => { fireEvent.load(sharp); });
    expect(image(container)?.className).toContain('is-shown');
    expect(container.querySelector('.g-art-preview')).toBeNull();
    expect(container.querySelector('[data-artwork-loading], .g-card')).toBeNull();
    expect(metrics.recordMetric).toHaveBeenCalledWith('image_load_ms', 'poster:net', expect.any(Number));
  });

  it('keeps the preview under the crossfade until it ends, and drops it at once under reduced motion', async () => {
    const computed = window.getComputedStyle;
    vi.spyOn(window, 'getComputedStyle').mockImplementation((node) => ({ ...computed(node), transitionDuration: '0.25s' }) as CSSStyleDeclaration);
    let reduce = false;
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: reduce && query.includes('reduce') }));
    try {
      const first = render(element());
      await act(async () => { fireEvent.load(image(first.container)!); });
      expect(first.container.querySelector('.g-art-preview')).not.toBeNull();
      fireEvent.transitionEnd(image(first.container)!);
      expect(first.container.querySelector('.g-art-preview')).toBeNull();
      first.unmount();
      reduce = true;
      const { container } = render(element({ art: titleArt('movie-3', 'Primary') }));
      await act(async () => { fireEvent.load(image(container)!); });
      expect(container.querySelector('.g-art-preview')).toBeNull();
    } finally {
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    }
  });

  it('uses the original, without srcset, when the image has no rendition', () => {
    const art = titleArt('movie-9', 'Primary', { rendition: null, widths: [] });
    const { container } = render(element({ art }));
    expect(image(container)?.getAttribute('src')).toBe(resolveArtworkUrl(art.url));
    expect(image(container)?.hasAttribute('srcset')).toBe(false);
  });

  it('retries a transient failure on the schedule, keeping the preview and never showing a placeholder', () => {
    vi.useFakeTimers();
    status = 503;
    const { container } = render(element({ art: titleArt('movie-2', 'Primary') }));
    fireEvent.error(image(container)!);
    expect(image(container)).toBeNull();
    expect(container.querySelector('.g-art-preview')).not.toBeNull();
    expect(container.querySelector('.g-card')).toBeNull();
    expect(metrics.recordMetric).toHaveBeenCalledWith('image_failed', 'poster:transient', 1);
    act(() => { vi.advanceTimersByTime(GALLERY_RETRY_DELAYS_MS[0] - 1); });
    expect(image(container)).toBeNull();
    act(() => { vi.advanceTimersByTime(1); });
    expect(image(container)).not.toBeNull();
  });

  it('shows the typographic card at once on a 404, in the card colour with readable text', () => {
    status = 404;
    const onFail = vi.fn();
    const onSettled = vi.fn();
    const { container } = render(element({ art: titleArt('movie-3', 'Primary'), colour: { colour: '#3d4a3f', fromPalette: true }, onFail, onSettled }));
    fireEvent.error(image(container)!);
    const card = container.querySelector<HTMLElement>('.g-card')!;
    expect(card.querySelector('.g-card-name')?.textContent).toBe('Northern Lantern');
    expect(card.querySelector('.g-card-year')?.textContent).toBe('2019');
    expect(card.style.color).toBe('rgb(244, 241, 234)');
    expect(card.style.backgroundColor).toBe('rgb(61, 74, 63)');
    expect(container.querySelector('.g-art-preview')).toBeNull();
    expect(onFail).toHaveBeenCalledTimes(1);
    expect(onSettled).toHaveBeenCalledWith('card');
    expect(metrics.recordMetric).toHaveBeenCalledWith('image_failed', 'poster:404', 1);
  });

  it('shows the card after exhausted retries and re-attempts once on a mount 60 s on', () => {
    vi.useFakeTimers();
    status = 503;
    const flaky = titleArt('movie-4', 'Primary');
    const first = render(element({ art: flaky }));
    for (const delay of GALLERY_RETRY_DELAYS_MS) {
      fireEvent.error(image(first.container)!);
      act(() => { vi.advanceTimersByTime(delay); });
    }
    fireEvent.error(image(first.container)!);
    expect(first.container.querySelector('.g-card')).not.toBeNull();
    expect(metrics.recordMetric).toHaveBeenCalledWith('image_failed', 'poster:exhausted', 1);
    first.unmount();
    vi.setSystemTime(Date.now() + REATTEMPT_AFTER_MS - 1_000);
    const soon = render(element({ art: flaky }));
    expect(soon.container.querySelector('.g-card')).not.toBeNull();
    soon.unmount();
    vi.setSystemTime(Date.now() + 1_000);
    const later = render(element({ art: flaky }));
    expect(image(later.container)).not.toBeNull();
    fireEvent.error(image(later.container)!);
    expect(later.container.querySelector('.g-card')).not.toBeNull();
  });

  it('starts clean when the URL changes: A fails, then B, then A loads again (deferred minor 1)', () => {
    status = 404;
    const a = titleArt('movie-5', 'Primary');
    const b = titleArt('movie-6', 'Primary');
    const view = render(element({ art: a }));
    fireEvent.error(image(view.container)!);
    expect(view.container.querySelector('.g-card')).not.toBeNull();
    status = undefined;
    view.rerender(element({ art: b }));
    expect(image(view.container)?.getAttribute('src')).toBe(resolveArtworkUrl(renditionUrl(b, 240)));
    view.rerender(element({ art: a }));
    expect(image(view.container)?.getAttribute('src')).toBe(resolveArtworkUrl(renditionUrl(a, 240)));
  });

  it('renders the card at once without artwork, and an empty colour field for a card-less image', () => {
    const { container } = render(element({ art: null, colour: { colour: '#563c3c', fromPalette: true } }));
    expect(container.querySelector('.g-card-name')?.textContent).toBe('Northern Lantern');
    const logo = render(element({ art: null, kind: 'logo', card: null }));
    expect(logo.container.querySelector('.g-art')?.childElementCount).toBe(0);
  });
  it('reports a card it starts with: no artwork, or a URL that exhausted its retries under 60 s ago', () => {
    vi.useFakeTimers();
    const onFail = vi.fn();
    const onSettled = vi.fn();
    const none = render(element({ art: null, kind: 'logo', card: null, onFail, onSettled }));
    expect(onFail).toHaveBeenCalledTimes(1);
    expect(onSettled).toHaveBeenCalledWith('card');
    none.rerender(element({ art: null, kind: 'logo', card: null, onFail, onSettled }));
    expect(onFail).toHaveBeenCalledTimes(1);
    none.unmount();
    status = 503;
    const logo = titleArt('movie-10', 'Logo');
    const first = render(element({ art: logo, kind: 'logo', card: null }));
    for (const delay of GALLERY_RETRY_DELAYS_MS) {
      fireEvent.error(image(first.container)!);
      act(() => { vi.advanceTimersByTime(delay); });
    }
    fireEvent.error(image(first.container)!);
    first.unmount();
    onFail.mockClear();
    onSettled.mockClear();
    render(element({ art: logo, kind: 'logo', card: null, onFail, onSettled }));
    expect(onFail).toHaveBeenCalledTimes(1);
    expect(onSettled).toHaveBeenCalledTimes(1);
    expect(onSettled).toHaveBeenCalledWith('card');
  });

  it('frees its loader slot on load and on error, so the next queued image starts', async () => {
    status = 404;
    const views = Array.from({ length: 6 }, (_, index) => render(element({ art: titleArt(`movie-q${index}`, 'Primary') })));
    expect(views.map((view) => image(view.container) !== null)).toEqual([true, true, true, true, false, false]);
    await act(async () => { fireEvent.load(image(views[0].container)!); });
    expect(image(views[4].container)).not.toBeNull();
    fireEvent.error(image(views[1].container)!);
    expect(image(views[5].container)).not.toBeNull();
    expect(loaderStats().inFlight).toBe(4);
  });

  it('paints its colour while queued with no preview, never an empty box', () => {
    Array.from({ length: 4 }, (_, index) => render(element({ art: titleArt(`movie-f${index}`, 'Primary') })));
    const { container } = render(element({ art: titleArt('movie-q', 'Primary', { preview: null, dominant: null }) }));
    const art = container.querySelector<HTMLElement>('.g-art')!;
    expect(art.innerHTML).toBe('');
    expect(art.style.backgroundColor).toBe('rgb(42, 59, 76)');
  });
});
