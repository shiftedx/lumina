import { describe, expect, it } from 'vitest';

import { heroGeometry, validFraming } from './HeroFrame';

const desktop = { width: 1920, height: 760 };
const ready = (regions: unknown[]) => ({ state: 'ready', width: 1920, height: 1080, regions });

describe('hero framing geometry', () => {
  it('keeps a material group of faces together with headroom and body context', () => {
    const geometry = heroGeometry(ready([
      { kind: 'face', x: .52, y: .15, width: .12, height: .18, weight: .9 },
      { kind: 'face', x: .7, y: .22, width: .1, height: .16, weight: .7 },
    ]), desktop, 'home');
    expect(geometry.mode).toBe('cover');
    expect(geometry.x).toBeGreaterThanOrEqual(0);
    expect(geometry.x).toBeLessThanOrEqual(100);
    expect(geometry.y).toBeGreaterThanOrEqual(0);
    expect(geometry.zoom).toBeLessThanOrEqual(1.06);
  });

  it('keeps a difficult portrait full bleed with a bounded crop', () => {
    const geometry = heroGeometry({ state: 'ready', width: 900, height: 1600, regions: [{ kind: 'face', x: .35, y: .1, width: .3, height: .32, weight: 1 }] }, desktop);
    expect(geometry.mode).toBe('cover');
    // Tight framing has no room for more zoom without clipping the protected headroom.
    expect(geometry.zoom).toBe(1);
  });

  it('brings a lower subject into view without adding borders', () => {
    const framing = heroGeometry(ready([{ kind: 'subject', x: .39, y: .64, width: .18, height: .28, weight: .42 }]), desktop);
    expect(framing.mode).toBe('cover');
    expect(framing.y).toBeGreaterThan(80);
    expect(framing.zoom).toBeGreaterThan(1);
    expect(framing.zoom).toBeLessThanOrEqual(1.06);
  });

  it('rejects malformed model output before it reaches layout', () => {
    expect(validFraming({ state: 'ready', width: Infinity, height: 1080, regions: [] })).toEqual({ state: 'unavailable' });
    expect(heroGeometry({ state: 'ready', width: 1920, height: 1080, regions: [{ kind: 'face', x: .9, y: .2, width: .4, height: .2, weight: 1 }] }, desktop).mode).toBe('cover');
  });
});
