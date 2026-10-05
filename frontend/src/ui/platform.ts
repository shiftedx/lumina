type PlatformNavigator = Pick<Navigator, 'platform'> & { userAgentData?: { platform?: string } };

export function isApplePlatform(nav: PlatformNavigator = navigator): boolean {
  return /mac|iphone|ipad|ipod/i.test(nav.userAgentData?.platform || nav.platform || '');
}

/** The palette shortcut as printed on the key caps. */
export const modKeyLabel = (nav?: PlatformNavigator): string => (isApplePlatform(nav) ? '⌘K' : 'Ctrl K');
