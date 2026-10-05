/** The sidebar must be visible in the frame it opens. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync('src/app/shell.css', 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
// Innermost rules only: `@media (...) { .a { … } }` yields `.a`.
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selector, body]) => ({ selector: selector.trim(), body: body.trim() }));
const find = (selector: string) => rules.filter((rule) => rule.selector === selector);

describe('shell.css sidebar', () => {
  it('hides the desktop sidebar with display: none, never a visibility that a transition can hold', () => {
    const hidden = find('.g-sidebar.is-hidden');
    expect(hidden).toHaveLength(1);
    expect(hidden[0].body).toContain('display: none');
    expect(hidden[0].body).not.toContain('visibility');
  });

  it('the drawer hides with visibility only on a delayed, close-only transition, and shows with no delay', () => {
    const closed = find('.g-sidebar').find((rule) => rule.body.includes('visibility: hidden'));
    expect(closed?.body).toMatch(/transition: transform var\(--g-drawer\), visibility 0s linear var\(--g-drawer\)/);
    const open = find('.g-sidebar.is-open')[0];
    expect(open.body).toContain('visibility: visible');
    expect(open.body).toContain('transition-delay: 0s');
  });

  it('no sidebar rule outside the drawer sets visibility or a transition', () => {
    const outside = rules.filter((rule) => /^\.g-sidebar\b/.test(rule.selector) && rule.selector !== '.g-sidebar.is-open')
      .filter((rule) => !rule.body.includes('visibility: hidden') && !rule.body.includes('transition: none !important'));
    for (const rule of outside) expect(rule.body).not.toMatch(/visibility|transition/);
  });

  it('turns every sidebar transition off under reduced motion (the app-wide reduced-motion rule is the only other source)', () => {
    expect(css).toMatch(/prefers-reduced-motion: reduce\)\s*\{\s*\.g-sidebar\s*\{\s*transition: none !important;/);
  });

  it('skip link and brand link are at least a 44px (--g-control) target', () => {
    const control = readFileSync('src/styles/tokens.css', 'utf8').match(/--g-control:\s*(\d+)px/);
    expect(Number(control?.[1])).toBeGreaterThanOrEqual(44);
    expect(find('.g-skip-link')[0].body).toContain('min-height: var(--g-control)');
    const brand = find('.g-topbar-brand')[0].body;
    expect(brand).toContain('min-width: var(--g-control)');
    expect(brand).toContain('min-height: var(--g-control)');
  });

  it('the drawer clips horizontal overflow so a long label cannot scroll it sideways', () => {
    const drawer = find('.g-sidebar').find((rule) => rule.body.includes('visibility: hidden'));
    expect(drawer?.body).toContain('overflow-x: hidden');
  });
});
