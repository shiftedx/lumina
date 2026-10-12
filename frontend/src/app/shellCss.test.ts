/** The sidebar must be visible in the frame it opens. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync('src/app/shell.css', 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
// Innermost rules only: `@media (...) { .a { … } }` yields `.a`.
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selector, body]) => ({ selector: selector.trim(), body: body.trim() }));
const find = (selector: string) => rules.filter((rule) => rule.selector === selector);

describe('shell.css navigation', () => {
  it('reserves no sidebar gutter and never slides navigation in from the side', () => {
    expect(find('.lumina-main').some((rule) => rule.body.includes('margin-left: 0'))).toBe(true);
    expect(css).not.toMatch(/--shell-offset|translateX|\.g-sidebar/);
  });

  it('skip link and brand link are at least a 44px (--g-control) target', () => {
    const control = readFileSync('src/styles/tokens.css', 'utf8').match(/--g-control:\s*(\d+)px/);
    expect(Number(control?.[1])).toBeGreaterThanOrEqual(44);
    expect(find('.g-skip-link')[0].body).toContain('min-height: var(--g-control)');
    const brand = find('.g-topbar-brand')[0].body;
    expect(brand).toContain('min-width: var(--g-control)');
    expect(brand).toContain('min-height: var(--g-control)');
  });

});
