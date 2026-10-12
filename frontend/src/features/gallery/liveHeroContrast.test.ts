/**
 * The Live hero's copy stays readable in both themes. The copy is white (--g-on-art) where it
 * sits on the art, so the art must fade to a dark scrim under the light theme, not to the near-white light paper; on
 * phones the copy sits under the art on paper, so every hero action takes the ink.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(join(__dirname, 'gallery.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

/** The declarations of `selector` (exact, comma lists split) within `scope` text, last one winning. */
function declared(scope: string, selector: string, property: string): string | undefined {
  let value: string | undefined;
  for (const [, selectors, body] of scope.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!selectors.split(',').map((s) => s.trim()).includes(selector)) continue;
    for (const declaration of body.split(';')) {
      const [name, ...rest] = declaration.split(':');
      if (name.trim() === property) value = rest.join(':').trim();
    }
  }
  return value;
}

/** The body of the `@media (max-width: 599px)` block that lays out the Live hero. */
function phoneBlock(): string {
  for (const match of css.matchAll(/@media \(max-width: 599px\) \{/g)) {
    let depth = 1;
    let i = match.index! + match[0].length;
    const start = i;
    for (; depth > 0; i++) depth += css[i] === '{' ? 1 : css[i] === '}' ? -1 : 0;
    const body = css.slice(start, i - 1);
    if (body.includes('.g-live-hero-copy')) return body;
  }
  throw new Error('no phone block for the Live hero');
}

describe('Live hero contrast', () => {
  it('fades to the dark scrim under the light theme, where the paper is near white', () => {
    expect(declared(css, ":root[data-theme='light'] .gallery .g-live-hero::after", 'background')).toBe('var(--g-scrim)');
  });

  it('gives the hero actions ink on phones, where the copy sits on paper', () => {
    const phone = phoneBlock();
    expect(declared(phone, '.gallery .g-live-hero-actions .g-remote-action', 'color')).toBe('var(--g-ink)');
    expect(declared(phone, '.gallery .g-live-hero-actions .g-record-status', 'color')).toBe('var(--g-ink)');
    expect(declared(phone, '.gallery .g-live-hero-actions .g-remote-action:disabled', 'color')).toBe('var(--g-ink-3)');
  });
});
