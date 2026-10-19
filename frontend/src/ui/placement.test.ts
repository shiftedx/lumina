import { afterEach, describe, expect, it, vi } from 'vitest';
import { place } from './placement';

const box = (left: number, top: number, width: number, height: number): DOMRect => ({
  bottom: top + height,
  height,
  left,
  right: left + width,
  top,
  width,
  x: left,
  y: top,
  toJSON: () => ({}),
});

afterEach(() => vi.restoreAllMocks());

describe('place', () => {
  it('measures a fit-content panel from the viewport inset before aligning it', () => {
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(320);
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(800);
    const trigger = document.createElement('button');
    const panel = document.createElement('div');
    trigger.getBoundingClientRect = () => box(40, 200, 240, 40);
    panel.getBoundingClientRect = () => {
      const left = Number.parseFloat(panel.style.left) || 40;
      return box(left, 244, Math.min(304, 320 - left), 120);
    };

    place(panel, trigger, 'start');

    expect(panel.style.left).toBe('8px');
    expect(panel.getBoundingClientRect().right).toBeLessThanOrEqual(312);
  });

  it('keeps a panel visible when its trigger is beyond the viewport', () => {
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(320);
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(800);
    const trigger = document.createElement('button');
    const panel = document.createElement('div');
    trigger.getBoundingClientRect = () => box(40, 1000, 240, 40);
    panel.getBoundingClientRect = () => box(8, Number.parseFloat(panel.style.top) || 1000, 240, 120);

    place(panel, trigger, 'start');

    expect(panel.style.top).toBe('672px');
  });
});
