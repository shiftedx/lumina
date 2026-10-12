import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type ImageSlot, type LoadPriority, loaderStats, prefetchImage, requestSlot, resetImageLoader, setScrollVelocity, useImageSlot } from './imageLoader';

const originalEntries = Object.getOwnPropertyDescriptor(performance, 'getEntriesByType');

function protocol(nextHopProtocol: string) {
  Object.defineProperty(performance, 'getEntriesByType', { configurable: true, value: () => [{ nextHopProtocol }] });
  resetImageLoader();
}

function Probe({ src, priority = 2 }: { src: string | null; priority?: LoadPriority }) {
  const slot = useImageSlot(src, priority);
  return <img alt="" src={slot.granted && src ? src : undefined} />;
}
const srcOf = (container: HTMLElement) => container.querySelector('img')?.getAttribute('src') ?? null;

beforeEach(() => protocol('http/1.1'));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  if (originalEntries) Object.defineProperty(performance, 'getEntriesByType', originalEntries);
  else delete (performance as Partial<Performance>).getEntriesByType;
  resetImageLoader();
});

function stubImage(): HTMLImageElement[] {
  const created: HTMLImageElement[] = [];
  const Original = window.Image;
  vi.stubGlobal('Image', class extends Original { constructor() { super(); created.push(this); } });
  return created;
}

describe('image loader', () => {
  it('lets four loads run over HTTP/1.1 and twelve over h2', () => {
    const granted: number[] = [];
    const slots = Array.from({ length: 6 }, (_, index) => requestSlot(2, index, () => granted.push(index)));
    expect(granted).toEqual([0, 1, 2, 3]);
    slots[0].release();
    expect(granted).toEqual([0, 1, 2, 3, 4]);
    expect(loaderStats()).toMatchObject({ cap: 4, inFlight: 4, queued: 1, maxInFlight: 4 });
    protocol('h2');
    const more: number[] = [];
    for (let index = 0; index < 14; index += 1) requestSlot(3, index, () => more.push(index));
    expect(more).toHaveLength(12);
    expect(loaderStats().cap).toBe(12);
  });

  it('grants by class, then top-to-bottom, left-to-right', () => {
    const order: string[] = [];
    const busy = [0, 1, 2, 3].map((index) => requestSlot(2, index, () => undefined));
    requestSlot(4, 0, () => order.push('behind'));
    requestSlot(3, 2005, () => order.push('ahead, row 2'));
    requestSlot(3, 1003, () => order.push('ahead, row 1'));
    requestSlot(1, 9999, () => order.push('hero'));
    busy.forEach((slot) => slot.release());
    expect(order).toEqual(['hero', 'ahead, row 1', 'ahead, row 2', 'behind']);
  });

  it('re-sorts a queued load whose row moved into view', () => {
    const order: string[] = [];
    const busy = [0, 1, 2, 3].map((index) => requestSlot(2, index, () => undefined));
    const late = requestSlot(4, 0, () => order.push('now in view'));
    requestSlot(3, 0, () => order.push('prefetch'));
    late.update(2, 0);
    busy[0].release();
    expect(order).toEqual(['now in view']);
  });

  it('holds prefetch while flinging and resumes 120 ms after the scroll slows', () => {
    vi.useFakeTimers();
    const order: string[] = [];
    setScrollVelocity(5);
    requestSlot(3, 0, () => order.push('prefetch'));
    requestSlot(2, 0, () => order.push('visible'));
    expect(order).toEqual(['visible']);
    setScrollVelocity(1);
    act(() => { vi.advanceTimersByTime(119); });
    expect(order).toEqual(['visible']);
    act(() => { vi.advanceTimersByTime(1); });
    expect(order).toEqual(['visible', 'prefetch']);
  });

  it('assigns src only once granted and drops queued loads that unmount', () => {
    const held = [0, 1, 2, 3].map((index) => requestSlot(2, index, () => undefined));
    const queued = render(<Probe src="/a.webp" />);
    expect(srcOf(queued.container)).toBeNull();
    queued.unmount();
    expect(loaderStats()).toMatchObject({ queued: 0, dropped: 1, inFlight: 4 });
    act(() => held[0].release());
    const next = render(<Probe src="/b.webp" />);
    expect(srcOf(next.container)).toBe('/b.webp');
    next.unmount();
    expect(loaderStats().inFlight).toBe(3);
  });

  it('never reports a new src as granted before its own slot', () => {
    const held = [0, 1, 2].map((index) => requestSlot(2, index, () => undefined));
    const view = render(<Probe src="/a.webp" />);
    expect(srcOf(view.container)).toBe('/a.webp');
    requestSlot(1, 0, () => undefined);
    view.rerender(<Probe src="/b.webp" />);
    expect(srcOf(view.container)).toBeNull();
    act(() => held[0].release());
    expect(srcOf(view.container)).toBe('/b.webp');
  });

  it('prefetches through the queue, once per URL', () => {
    const created = stubImage();
    const held = [0, 1, 2, 3].map((index) => requestSlot(2, index, () => undefined));
    prefetchImage('/art/backdrop-960.webp', 3);
    expect(created).toHaveLength(0);
    held[0].release();
    expect(created).toHaveLength(1);
    expect(created[0].getAttribute('src')).toBe('/art/backdrop-960.webp');
    expect(created[0].getAttribute('fetchpriority')).toBe('low');
    prefetchImage('/art/backdrop-960.webp', 3);
    expect(loaderStats().queued).toBe(0);
  });

  it('raises a queued prefetch when the same URL is asked for at a higher class', () => {
    const created = stubImage();
    const order: string[] = [];
    const held = [0, 1, 2, 3].map((index) => requestSlot(2, index, () => undefined));
    prefetchImage('/art/hero.webp', 3);
    requestSlot(2, 9999, () => order.push('visible'));
    prefetchImage('/art/hero.webp', 1);
    expect(loaderStats().queued).toBe(2);
    held[0].release();
    expect(order).toEqual([]);
    expect(created).toHaveLength(1);
    expect(created[0].getAttribute('fetchpriority')).toBe('high');
    prefetchImage('/art/hero.webp', 1);
    expect(loaderStats().queued).toBe(1);
  });

  it('ignores a settle from the previous src once the key has changed', () => {
    const slots: ImageSlot[] = [];
    function Keeper({ src }: { src: string }) {
      slots.push(useImageSlot(src, 2));
      return null;
    }
    [0, 1, 2].forEach((index) => requestSlot(2, index, () => undefined));
    const view = render(<Keeper src="/a.webp" />);
    const staleSettle = slots[slots.length - 1].settle;
    view.rerender(<Keeper src="/b.webp" />);
    expect(slots[slots.length - 1].granted).toBe(true);
    expect(loaderStats().inFlight).toBe(4);
    act(() => staleSettle());
    expect(loaderStats().inFlight).toBe(4);
    act(() => slots[slots.length - 1].settle());
    expect(loaderStats().inFlight).toBe(3);
  });
});
