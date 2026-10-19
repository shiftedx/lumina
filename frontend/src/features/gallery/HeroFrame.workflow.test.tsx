import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getHeroFraming: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

import { HeroFrame } from './HeroFrame';

let intersection: ((visible: boolean) => void) | null = null;
class TestIntersectionObserver {
  constructor(callback: IntersectionObserverCallback) { intersection = (visible) => callback([{ isIntersecting: visible } as IntersectionObserverEntry], this as unknown as IntersectionObserver); }
  disconnect() {}
  observe() {}
  unobserve() {}
  takeRecords() { return []; }
  root = null;
  rootMargin = '0px';
  thresholds = [];
}
class TestResizeObserver {
  constructor(private callback: ResizeObserverCallback) {}
  disconnect() {}
  observe(target: Element) { this.callback([{ target, contentRect: { width: 1920, height: 760 } as DOMRectReadOnly } as ResizeObserverEntry], this as unknown as ResizeObserver); }
  unobserve() {}
}
const ready = { state: 'ready' as const, width: 1920, height: 1080, regions: [{ kind: 'face' as const, x: .55, y: .15, width: .12, height: .18, weight: 1 }] };
const child = <img className="rq-img" src="/sharp.jpg" />;

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 1920, 760));
  vi.stubGlobal('IntersectionObserver', TestIntersectionObserver);
  vi.stubGlobal('ResizeObserver', TestResizeObserver);
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  intersection = null;
  api.getHeroFraming.mockReset();
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('HeroFrame request lifecycle', () => {
  it('keeps the displayed crop stable when cold analysis finishes late, then reuses it next visit', async () => {
    let settle: ((value: typeof ready) => void) | undefined;
    api.getHeroFraming.mockReturnValue(new Promise<typeof ready>((resolve) => { settle = resolve; }));
    const view = render(<HeroFrame src="/late-analysis">{child}</HeroFrame>);
    await act(async () => { intersection?.(true); await Promise.resolve(); });
    expect(api.getHeroFraming).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    const frame = view.container.querySelector('.hero-frame') as HTMLElement;
    const initialCrop = frame.style.cssText;
    await act(async () => { settle?.(ready); await Promise.resolve(); });
    expect(frame.style.cssText).toBe(initialCrop);
    view.unmount();
    const nextVisit = render(<HeroFrame src="/late-analysis">{child}</HeroFrame>);
    await act(async () => { intersection?.(true); await Promise.resolve(); });
    expect((nextVisit.container.querySelector('.hero-frame') as HTMLElement).style.cssText).not.toBe(initialCrop);
  });

  it('does not leave polling work behind when an active hero unmounts', async () => {
    api.getHeroFraming.mockResolvedValue({ state: 'pending' });
    const view = render(<HeroFrame src="/first"><>{child}</></HeroFrame>);
    await act(async () => { intersection?.(true); await Promise.resolve(); });
    expect(api.getHeroFraming).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(api.getHeroFraming).toHaveBeenCalledTimes(1);
  });

  it('uses a quick answer before reveal, while pending and failed answers reveal the conservative crop without polling', async () => {
    api.getHeroFraming.mockResolvedValueOnce(ready);
    const quick = render(<HeroFrame src="/quick-ready">{child}</HeroFrame>);
    await act(async () => { intersection?.(true); await Promise.resolve(); });
    expect((quick.container.querySelector('.hero-frame') as HTMLElement).dataset.heroRevealed).toBe('true');
    expect((quick.container.querySelector('.hero-frame') as HTMLElement).style.getPropertyValue('--hero-focus-y')).toBe('0%');
    quick.unmount();

    api.getHeroFraming.mockReset().mockResolvedValue({ state: 'pending' });
    const pending = render(<HeroFrame src="/pending">{child}</HeroFrame>);
    await act(async () => { intersection?.(true); await Promise.resolve(); await vi.advanceTimersByTimeAsync(2_000); });
    expect(api.getHeroFraming).toHaveBeenCalledTimes(1);
    expect((pending.container.querySelector('.hero-frame') as HTMLElement).dataset.heroRevealed).toBe('true');
    pending.unmount();

    api.getHeroFraming.mockReset().mockRejectedValue(new Error('offline'));
    const failed = render(<HeroFrame src="/failed">{child}</HeroFrame>);
    await act(async () => { intersection?.(true); await Promise.resolve(); });
    expect((failed.container.querySelector('.hero-frame') as HTMLElement).dataset.heroRevealed).toBe('true');
    failed.unmount();
    api.getHeroFraming.mockReset();
    const failedRevisit = render(<HeroFrame src="/failed">{child}</HeroFrame>);
    expect((failedRevisit.container.querySelector('.hero-frame') as HTMLElement).dataset.heroRevealed).toBe('true');
    expect(api.getHeroFraming).not.toHaveBeenCalled();
    failedRevisit.unmount();

    api.getHeroFraming.mockReset();
    const disabled = render(<HeroFrame enabled={false} src="/disabled">{child}</HeroFrame>);
    expect((disabled.container.querySelector('.hero-frame') as HTMLElement).dataset.heroRevealed).toBe('true');
    expect(api.getHeroFraming).not.toHaveBeenCalled();
  });

  it('aborts while hidden, resumes visibly, and never applies a previous source crop', async () => {
    let settleFirst: ((value: typeof ready) => void) | undefined;
    const first = new Promise<typeof ready>((resolve) => { settleFirst = resolve; });
    api.getHeroFraming.mockImplementation((src: string) => src === '/first' ? first : Promise.resolve({ state: 'unavailable' }));
    const view = render(<HeroFrame src="/first">{child}</HeroFrame>);
    await act(async () => { intersection?.(true); await Promise.resolve(); });
    const firstSignal = api.getHeroFraming.mock.calls[0][1].signal as AbortSignal;
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(firstSignal.aborted).toBe(true);

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await Promise.resolve(); });
    view.rerender(<HeroFrame src="/second">{child}</HeroFrame>);
    await act(async () => { settleFirst?.(ready); await Promise.resolve(); });

    const frame = view.container.querySelector('.hero-frame') as HTMLElement;
    expect(api.getHeroFraming.mock.calls.map(([src]) => src)).toContain('/second');
    expect(frame.className).toContain('is-cover');
    expect(frame.style.getPropertyValue('--hero-focus-x')).toBe('50%');
  });
});
