import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TabScroller } from './TabScroller';

let resize: ResizeObserverCallback[];

beforeEach(() => {
  resize = [];
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resize.push(callback); }
    observe() {}
    disconnect() {}
  });
});

describe('TabScroller', () => {
  it('shows truthful end controls only for full-row overflow and scrolls without moving the window', () => {
    render(<TabScroller activeKey="one" ariaLabel="Example" role="tablist" rowClassName="g-tabs"><button aria-selected="true" role="tab">One</button><button aria-selected="false" role="tab">Two</button></TabScroller>);
    const frame = document.querySelector<HTMLElement>('.g-tab-scroller');
    if (!frame) throw new Error('TabScroller frame did not render');
    const row = screen.getByRole('tablist', { name: 'Example' });
    let scrollWidth = 600;
    let scrollLeft = 0;
    Object.defineProperties(frame, { clientWidth: { configurable: true, get: () => 300 } });
    Object.defineProperties(row, {
      clientWidth: { configurable: true, get: () => 212 },
      scrollLeft: { configurable: true, get: () => scrollLeft, set: (value) => { scrollLeft = value; } },
      scrollWidth: { configurable: true, get: () => scrollWidth },
    });
    row.scrollBy = vi.fn(({ left }) => { scrollLeft += Number(left); fireEvent.scroll(row); });
    act(() => resize.forEach((notify) => notify([], {} as ResizeObserver)));
    const left = screen.getByRole<HTMLButtonElement>('button', { name: 'Scroll Example tabs left' });
    const right = screen.getByRole<HTMLButtonElement>('button', { name: 'Scroll Example tabs right' });
    expect(left.disabled).toBe(true);
    expect(right.disabled).toBe(false);
    fireEvent.click(right);
    expect(row.scrollBy).toHaveBeenCalledOnce();
    expect(scrollLeft).toBeGreaterThan(0);
    expect(left.disabled).toBe(false);
    scrollLeft = scrollWidth - row.clientWidth;
    fireEvent.scroll(row);
    expect(right.disabled).toBe(true);

    // The buttons shrink the row to 212px, but 290px fits the full 300px frame: controls must disappear.
    scrollWidth = 290;
    act(() => resize.forEach((notify) => notify([], {} as ResizeObserver)));
    expect(screen.queryByRole('button', { name: 'Scroll Example tabs left' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Scroll Example tabs right' })).toBeNull();
  });
});
