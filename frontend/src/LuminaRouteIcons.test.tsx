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
});
