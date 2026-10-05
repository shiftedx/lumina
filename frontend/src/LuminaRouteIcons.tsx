import type { ReactNode, SVGProps } from 'react';

export const luminaRouteIconNames = [
  'home',
  'explore',
  'live',
  'requests',
  'subscriptions',
  'library',
  'music',
  'downloads',
  'settings',
] as const;

export type LuminaRouteIconName = (typeof luminaRouteIconNames)[number];

export type LuminaRouteIconProps = Omit<SVGProps<SVGSVGElement>, 'aria-label' | 'aria-labelledby' | 'children'> & {
  route: LuminaRouteIconName;
};

const routeGlyphs: Record<LuminaRouteIconName, ReactNode> = {
  home: <>
    <path d="m4 11.5 8-6.5 8 6.5v8.25a1.25 1.25 0 0 1-1.25 1.25H5.25A1.25 1.25 0 0 1 4 19.75Z" />
    <path d="M9.25 21v-5.5h5.5V21M12 2.75v-1M7.2 4.1l-.7-.7M16.8 4.1l.7-.7" />
  </>,
  explore: <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="m15.9 8.1-2.3 4.1-4.1 2.3 2.3-4.1Z" />
    <path d="M12 1.75v1.5M12 20.75v1.5M1.75 12h1.5M20.75 12h1.5" />
  </>,
  live: <>
    <circle cx="12" cy="13" r="2.25" />
    <path d="M8.46 16.54a5 5 0 0 1 0-7.08M15.54 9.46a5 5 0 0 1 0 7.08" />
    <path d="M5.63 19.37a9 9 0 0 1 0-12.74M18.37 6.63a9 9 0 0 1 0 12.74" />
    <path d="M12 2.75v-1" />
  </>,
  requests: <>
    <path d="M5.5 7h13A1.5 1.5 0 0 1 20 8.5v.75a2.75 2.75 0 0 0 0 5.5v.75a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 15.5v-.75a2.75 2.75 0 0 0 0-5.5V8.5A1.5 1.5 0 0 1 5.5 7Z" />
    <path d="M12 9.75v4.5M9.75 12h4.5" />
    <path d="M12 4.25v-1.5M8.4 5.1 7.35 4.05M15.6 5.1l1.05-1.05" />
  </>,
  subscriptions: <>
    <path d="M4 9.25h16v9.5A2.25 2.25 0 0 1 17.75 21h-11.5A2.25 2.25 0 0 1 4 18.75Z" />
    <path d="M7 9.25V7a5 5 0 0 1 10 0v2.25M8.25 15.25h7.5M12 12.75v5" />
    <path d="M12 3.25V1.75M8.4 4.65l-1.05-1.05M15.6 4.65l1.05-1.05" />
  </>,
  library: <>
    <path d="M4 5.25A2.25 2.25 0 0 1 6.25 3H19a1 1 0 0 1 1 1v15.75a1.25 1.25 0 0 1-1.25 1.25H6.25A2.25 2.25 0 0 1 4 18.75Z" />
    <path d="M4 6.25A2.25 2.25 0 0 1 6.25 8.5H20M8.25 12h7.5M8.25 16h5" />
    <path d="M16.5 5.75v-1M14.65 6.45l-.7-.7M18.35 6.45l.7-.7" />
  </>,
  music: <>
    <path d="M8 18.5V7.25l9-2.25v11.25" />
    <path d="M8 9.5 17 7.25M5.5 20.75a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM14.5 18.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" />
    <path d="M12 3.25v-1.5M9.25 4.1l-1.05-1.05M14.75 4.1 15.8 3.05" />
  </>,
  downloads: <>
    <path d="M12 3v10.25M8.25 9.5 12 13.25 15.75 9.5M4 17.25v2.5A1.25 1.25 0 0 0 5.25 21h13.5A1.25 1.25 0 0 0 20 19.75v-2.5" />
    <path d="M6.5 17.25h11M12 1.75v.75M8.4 3.4 7.35 2.35M15.6 3.4l1.05-1.05" />
  </>,
  settings: <>
    <path d="M12 8.25a3.75 3.75 0 1 0 0 7.5 3.75 3.75 0 0 0 0-7.5Z" />
    <path d="m19.15 13.75 1.1 1.1-2.1 3.65-1.55-.55a7.75 7.75 0 0 1-1.9 1.1L14.4 21h-4.2l-.3-1.95a7.75 7.75 0 0 1-1.9-1.1l-1.55.55-2.1-3.65 1.1-1.1a7.75 7.75 0 0 1 0-2.2l-1.1-1.1L6.45 6.8 8 7.35a7.75 7.75 0 0 1 1.9-1.1l.3-1.95h4.2l.3 1.95a7.75 7.75 0 0 1 1.9 1.1l1.55-.55 2.1 3.65-1.1 1.1a7.75 7.75 0 0 1 0 2.2Z" />
    <path d="M12 2.25v-1" />
  </>,
};

/** A decorative route glyph; its labelled button or link provides the accessible name. */
export function LuminaRouteIcon({ route, ...props }: LuminaRouteIconProps) {
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
      {routeGlyphs[route]}
    </svg>
  );
}
