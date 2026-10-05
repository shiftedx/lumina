/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync('src/styles/base.css', 'utf8').replace(/\s+/g, ' ');

describe('base.css', () => {
  it('draws one focus ring from the tokens on every interactive element', () => {
    expect(css).toContain(':where(a, button, input, select, textarea, summary, [tabindex]):focus-visible { outline: var(--g-focus-width) solid var(--g-focus); outline-offset: 3px; }');
  });
  it('keeps focus targets clear of the sticky top bar (WCAG 2.4.11)', () => {
    expect(css).toMatch(/\.lumina-main :focus-visible, #main-content \{ scroll-margin-top: calc\(var\(--g-topbar\) \+ 16px/);
  });
  it('stops every transition and animation under reduced motion, once', () => {
    expect(css).toContain('@media (prefers-reduced-motion: reduce) { *, *::before, *::after, ::backdrop { animation: none !important; transition: none !important; scroll-behavior: auto !important; }');
    expect(css.match(/prefers-reduced-motion/g)).toHaveLength(1);
  });
  it('declares a metric-matched serif fallback', () => {
    expect(css).toMatch(/@font-face \{ font-family: 'Newsreader Fallback'; src: local\('Georgia'\); size-adjust: [\d.]+%; ascent-override: [\d.]+%; descent-override: [\d.]+%; line-gap-override: 0%; \}/);
  });
});
