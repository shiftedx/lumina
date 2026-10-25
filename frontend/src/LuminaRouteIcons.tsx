import type { SVGProps } from 'react';

import { LuminaSpriteIcon, type LuminaIconName } from './luminaIcons';

export const luminaRouteIconNames = [
  'home',
  'explore',
  'live',
  'requests',
  'subscriptions',
  'library',
  'music',
  'books',
  'photos',
  'downloads',
  'settings',
] as const;

export type LuminaRouteIconName = (typeof luminaRouteIconNames)[number];

export type LuminaRouteIconProps = Omit<SVGProps<SVGSVGElement>, 'aria-label' | 'aria-labelledby' | 'children'> & {
  route: LuminaRouteIconName;
  selected?: boolean;
};

const routeGlyphs: Record<LuminaRouteIconName, LuminaIconName | null> = {
  home: 'home',
  explore: 'discover',
  live: 'television',
  requests: 'requests',
  subscriptions: 'bookmark',
  library: 'library',
  music: 'music',
  // The approved set does not yet include a book glyph. Keep the existing geometry
  // distinct from Library until the canonical icon package gains one.
  books: null,
  photos: 'photos',
  downloads: 'download',
  settings: 'user-settings',
};

const selectedRouteGlyphs = new Set<LuminaIconName>(['home', 'library', 'requests', 'user-settings']);

/** A decorative route glyph; its labelled button or link provides the accessible name. */
export function LuminaRouteIcon({ route, selected = false, ...props }: LuminaRouteIconProps) {
  const glyph = routeGlyphs[route];
  if (glyph) {
    return (
      <LuminaSpriteIcon
        {...props}
        name={glyph}
        size={22}
        variant={selected && selectedRouteGlyphs.has(glyph) ? 'solid' : 'outline'}
      />
    );
  }
  return (
    <svg
      aria-hidden="true"
      fill="none"
      focusable="false"
      height="22"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.7"
      viewBox="0 0 24 24"
      width="22"
      {...props}
    >
      <path d="M12 5.5C8.5 3.5 5 3.25 2.5 4.5v15c2.5-1.25 6-1 9.5 1 3.5-2 7-2.25 9.5-1v-15c-2.5-1.25-6-1-9.5 1Z" />
      <path d="M12 5.5v15M5.5 7.5l3.5.5M5.5 10.5l3.5.5M15 8l3.5-.5M15 11l3.5-.5" />
    </svg>
  );
}
