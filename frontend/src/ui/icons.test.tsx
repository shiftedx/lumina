import { readFileSync } from 'node:fs';
import { createRef } from 'react';
import { render } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Check, Heart, Play } from '../luminaIcons';

describe('Lumina icon compatibility adapter', () => {
  it('resolves every approved adapter mapping and solid request against the generated sprite', () => {
    const adapter = readFileSync('src/luminaIcons.tsx', 'utf8');
    const sprite = readFileSync('src/ui/lumina-media-sprite.svg', 'utf8');
    const symbols = new Set([...sprite.matchAll(/<symbol id="([^"]+)"/g)].map((match) => match[1]));
    const mapped = [...adapter.matchAll(/^export const \w+ = luminaIcon\('([^']+)'/gm)].map((match) => match[1]);
    expect(mapped).toHaveLength(93);
    for (const name of mapped) expect(symbols.has(`lumina-${name}`), name).toBe(true);

    const solidBlock = adapter.match(/const solidIcons = new Set<LuminaIconName>\(\[([\s\S]*?)\]\);/)?.[1] ?? '';
    const solids = [...solidBlock.matchAll(/'([^']+)'/g)].map((match) => match[1]);
    expect(solids).toHaveLength(40);
    for (const name of solids) expect(symbols.has(`lumina-${name}-solid`), `${name}-solid`).toBe(true);
    for (const name of ['home', 'library', 'requests', 'user-settings']) {
      expect(symbols.has(`lumina-${name}-selected`), `${name}-selected`).toBe(true);
    }
  });

  it('keeps Lucide classes, caller classes, sizing, and forwarded SVG refs', () => {
    const ref = createRef<SVGSVGElement>();
    const { container } = render(<Play className="transport-icon" ref={ref} size={31} />);
    expect(ref.current).toBe(container.querySelector('svg'));
    expect([...ref.current!.classList]).toEqual(['lumina-icon-sprite', 'lucide', 'lucide-play', 'transport-icon']);
    expect(ref.current?.getAttribute('width')).toBe('31');
    expect(ref.current?.getAttribute('height')).toBe('31');
  });

  it('uses solid approved artwork when callers previously requested a fill', () => {
    const html = renderToStaticMarkup(<Heart fill="currentColor" />);
    expect(html).toContain('lucide lucide-heart');
    expect(html).toMatch(/<use href="[^"]+#lumina-favorite-solid"><\/use>/);
  });

  it('keeps decorative icons hidden and meaningful standalone icons labelled', () => {
    expect(renderToStaticMarkup(<Check />)).toContain('aria-hidden="true"');
    const labelled = renderToStaticMarkup(<Check aria-label="Verified" />);
    expect(labelled).toContain('role="img"');
    expect(labelled).toContain('aria-label="Verified"');
    expect(labelled).not.toContain('aria-hidden="true"');
  });
});
