/**
 * Pure preference helpers for the desktop navigation menu. The caller owns
 * browser storage and member-settings I/O; this module only defines precedence.
 */
export const SIDEBAR_COLLAPSED_STORAGE_KEY = 'lumina.sidebar-collapsed';

export type SidebarPreferenceSource = 'member' | 'local' | 'default';

export type SidebarPreference = {
  collapsed: boolean;
  source: SidebarPreferenceSource;
};

export type SidebarPreferenceInput = {
  /** The persisted per-member `ui_prefs.sidebar_collapsed` value. */
  memberPreference?: unknown;
  /** Whether the member-settings read completed, even if it had no sidebar field. */
  memberSettingsAvailable?: boolean;
  /** A startup or offline local-storage value, never a replacement for member settings. */
  localFallback?: unknown;
};

function booleanPreference(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

/**
 * Gives the durable member preference priority, then an offline/startup fallback,
 * and defaults a member with neither preference to the hidden menu.
 */
export function resolveSidebarPreference({ memberPreference, memberSettingsAvailable = false, localFallback }: SidebarPreferenceInput): SidebarPreference {
  const member = booleanPreference(memberPreference);
  if (member !== undefined) return { collapsed: member, source: 'member' };

  if (memberSettingsAvailable) return { collapsed: true, source: 'default' };

  const local = booleanPreference(localFallback);
  if (local !== undefined) return { collapsed: local, source: 'local' };

  return { collapsed: true, source: 'default' };
}

/** Extracts the optional durable value without treating malformed data as a choice. */
export function sidebarCollapsedPreferenceFromUiPrefs(uiPrefs: unknown): boolean | undefined {
  if (!uiPrefs || typeof uiPrefs !== 'object' || Array.isArray(uiPrefs)) return undefined;
  return booleanPreference((uiPrefs as Record<string, unknown>).sidebar_collapsed);
}

/** Produces a settings-payload fragment while retaining future UI preference fields. */
export function withSidebarCollapsedPreference(uiPrefs: unknown, collapsed: boolean): Record<string, unknown> {
  const existing = uiPrefs && typeof uiPrefs === 'object' && !Array.isArray(uiPrefs)
    ? uiPrefs as Record<string, unknown>
    : {};
  return { ...existing, sidebar_collapsed: collapsed };
}

/** Serializes only explicit booleans for the caller's local startup/offline cache. */
export function sidebarCollapsedStorageValue(collapsed: boolean): '1' | '0' {
  return collapsed ? '1' : '0';
}

/** Ignores stale or malformed cache values rather than manufacturing a preference. */
export function sidebarCollapsedFromStorageValue(value: string | null | undefined): boolean | undefined {
  if (value === '1') return true;
  if (value === '0') return false;
  return undefined;
}
