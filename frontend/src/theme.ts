/** Member theme choice, persisted under `ui_prefs.theme`. */
export type ThemePreference = 'system' | 'light' | 'dark';

export const THEME_UI_PREF_KEY = 'theme';
/** Boot hint read by public/theme-boot.js before first paint; holds only the choice. */
export const THEME_STORAGE_KEY = 'lumina.theme';

function isThemePreference(value: unknown): value is ThemePreference {
  return value === 'system' || value === 'light' || value === 'dark';
}

export function themePreferenceFromUiPrefs(uiPrefs: unknown): ThemePreference | undefined {
  if (!uiPrefs || typeof uiPrefs !== 'object' || Array.isArray(uiPrefs)) return undefined;
  const value = (uiPrefs as Record<string, unknown>)[THEME_UI_PREF_KEY];
  return isThemePreference(value) ? value : undefined;
}

export function storedThemePreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(value) ? value : 'system';
  } catch {
    return 'system';
  }
}

/** Resolves the choice against the OS scheme, sets `data-theme` and refreshes the boot hint. */
export function applyTheme(preference: ThemePreference, prefersLight: boolean): void {
  document.documentElement.dataset.theme = preference === 'system' ? (prefersLight ? 'light' : 'dark') : preference;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Storage denied: the theme still renders, it just cannot pre-paint next load.
  }
}
