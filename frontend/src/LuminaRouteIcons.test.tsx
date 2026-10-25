import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { LuminaRouteIcon, luminaRouteIconNames } from './LuminaRouteIcons';

describe('Lumina route icons', () => {
  it('keeps every route icon hidden from assistive technology inside labelled controls', () => {
    for (const route of luminaRouteIconNames) {
      const html = renderToStaticMarkup(<LuminaRouteIcon route={route} />);
      expect(html).toContain('aria-hidden="true"');
      expect(html).toContain('focusable="false"');
    }
  });

  it('includes a Books icon for the independent Books destination', () => {
    expect(luminaRouteIconNames).toContain('books');
    expect(renderToStaticMarkup(<LuminaRouteIcon route="books" />)).toContain('<path');
  });

  it('uses the approved selected artwork when navigation exposes active state', () => {
    const outline = renderToStaticMarkup(<LuminaRouteIcon route="home" />);
    const selected = renderToStaticMarkup(<LuminaRouteIcon route="home" selected />);
    expect(selected).not.toBe(outline);
    expect(outline).toMatch(/#lumina-home"/);
    expect(selected).toMatch(/#lumina-home-solid"/);
  });

  it('resolves every sprite-backed route state to an exported symbol', () => {
    const sprite = readFileSync('src/ui/lumina-media-sprite.svg', 'utf8');
    const symbols = new Set([...sprite.matchAll(/<symbol id="([^"]+)"/g)].map((match) => match[1]));
    for (const route of luminaRouteIconNames) {
      for (const selected of [false, true]) {
        const html = renderToStaticMarkup(<LuminaRouteIcon route={route} selected={selected} />);
        const symbol = html.match(/#(lumina-[^"]+)/)?.[1];
        if (route === 'books') expect(symbol).toBeUndefined();
        else expect(symbols.has(symbol ?? ''), `${route}:${selected ? 'selected' : 'outline'}`).toBe(true);
      }
    }
  });
});
