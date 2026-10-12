/// <reference types="node" />
/** Success criterion 3: every text token meets its stated ratio on every surface it is used on. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

// Relative to frontend/ (the vitest cwd); CSS `?raw` imports come back empty under vitest (see v1_themes.test.tsx).
const css = readFileSync('src/styles/tokens.css', 'utf8');

/** Hue <= 20 or >= 330 degrees with saturation > 40% (the guard that used to live in redColours.test.ts). */
function isRedOrPink(r: number, g: number, b: number): boolean {
  const max = Math.max(r, g, b) / 255; const min = Math.min(r, g, b) / 255; const delta = max - min;
  if (delta === 0) return false;
  const saturation = delta / (1 - Math.abs(max + min - 1));
  const hue = (((max === r / 255 ? ((g - b) / 255) / delta : max === g / 255 ? 2 + ((b - r) / 255) / delta : 4 + ((r - g) / 255) / delta) * 60) + 360) % 360;
  return saturation > 0.4 && (hue <= 20 || hue >= 330);
}

function block(selector: RegExp): Record<string, string> {
  const match = selector.exec(css);
  if (!match) throw new Error(`No block for ${selector}`);
  const body = css.slice(match.index + match[0].length, css.indexOf('}', match.index));
  return Object.fromEntries([...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}
const dark = block(/^:root \{/m);
const light = { ...dark, ...block(/^:root\[data-theme='light'\] \{/m) };

function luminance(hex: string): number {
  const value = hex.replace('#', '');
  const full = value.length === 3 ? value.split('').map((c) => c + c).join('') : value;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function ratio(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

// [text token, surfaces, minimum] — the spec 1.2 "Use" column.
const PAIRS: Array<[string, string[], number]> = [
  ['--g-ink', ['--g-paper'], 15],
  ['--g-ink', ['--g-paper-2', '--g-paper-3'], 7],
  ['--g-ink-2', ['--g-paper', '--g-paper-2', '--g-paper-3'], 7],
  ['--g-ink-3', ['--g-paper', '--g-paper-2', '--g-paper-3'], 4.5],
  ['--g-gold-ink', ['--g-paper', '--g-paper-2', '--g-paper-3'], 4.5],
  ['--g-danger', ['--g-paper', '--g-paper-2', '--g-paper-3'], 4.5],
  ['--g-success', ['--g-paper'], 5.5],
  ['--g-success', ['--g-paper-2', '--g-paper-3'], 4.5],
  ['--g-focus', ['--g-paper', '--g-paper-2', '--g-paper-3'], 3],
];

describe('tokens.css contrast', () => {
  for (const [name, theme] of [['dark', dark], ['light', light]] as const) {
    it(`every text token meets its ratio on every surface (${name})`, () => {
      for (const [text, surfaces, minimum] of PAIRS) {
        for (const surface of surfaces) {
          expect(ratio(theme[text], theme[surface]), `${name} ${text} on ${surface}`).toBeGreaterThanOrEqual(minimum);
        }
      }
      expect(ratio(theme['--g-on-gold'], theme['--g-gold']), `${name} on-gold`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(theme['--g-paper'], theme['--g-danger']), `${name} paper on danger`).toBeGreaterThanOrEqual(6);
    });
  }

  it('the overlay set reads on the letterbox and the panel base in every theme', () => {
    expect(ratio(dark['--g-art-paper'], '#15130f'), 'LiveBadge ink on its chip').toBeGreaterThanOrEqual(12);
    expect(ratio(dark['--g-overlay-ink'], '#0f0e0c')).toBeGreaterThanOrEqual(7);
    expect(ratio(dark['--g-overlay-ink-2'], '#0f0e0c')).toBeGreaterThanOrEqual(7);
    expect(ratio(dark['--g-gold'], dark['--g-overlay-bg'])).toBeGreaterThanOrEqual(3);
    expect(light['--g-overlay-ink']).toBe(dark['--g-overlay-ink']);
    expect(light['--g-art-paper']).toBe(dark['--g-art-paper']);
  });

  it('defines no red or pink token apart from danger (the live surfaces must stay red-free, plan Review Focus)', () => {
    // Only hex, rgb() and color-mix() are used, so the hue check below (and check-styles' redHits) can parse every literal.
    expect(css.replace(/\/\*[\s\S]*?\*\//g, '')).not.toMatch(/\b(?:hsla?|hwb|lab|lch|oklab|oklch|color)\(/);
    for (const [name, value] of [...Object.entries(dark), ...Object.entries(light)]) {
      if (name.startsWith('--g-danger')) continue;
      for (const [, hex] of value.matchAll(/#([0-9a-f]{6}|[0-9a-f]{3})\b/gi)) {
        const full = hex.length === 3 ? [...hex].map((c) => c + c).join('') : hex;
        const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
        expect(isRedOrPink(r, g, b), `${name}: #${hex}`).toBe(false);
      }
    }
  });

  it('defines every token name', () => {
    for (const token of ['--g-art-paper', '--g-art-chip', '--g-paper-3', '--g-wash', '--g-selected', '--g-on-gold', '--g-danger-wash', '--g-success', '--g-scrim-modal',
      '--g-overlay-panel', '--g-overlay-halo', '--g-overlay-hover', '--g-caption-box', '--g-elev-1', '--g-elev-2', '--g-type-h3',
      '--g-type-ui', '--g-type-ui-strong', '--g-type-small', '--g-type-mono', '--g-control', '--g-topbar', '--g-sidebar',
      '--g-focus-width', '--g-z-menu', '--g-z-toast', '--g-pop', '--g-dialog', '--g-toast', '--g-radius-round', '--g-reading-width']) {
      expect(dark[token], token).toBeTruthy();
    }
  });
});

describe('a11y regressions guarded in CSS', () => {
  it('reduced motion removes transitions outright: a .01ms transition still paints one frame of the old colour, which axe reads as ink on a dark hero', () => {
    expect(readFileSync('src/styles/base.css', 'utf8')).toMatch(/prefers-reduced-motion: reduce\)\s*\{[^}]*transition: none !important/);
  });
});
