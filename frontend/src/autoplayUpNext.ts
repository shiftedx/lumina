/** Persisted under the member-owned `ui_prefs` object. */
export const AUTOPLAY_UP_NEXT_UI_PREF_KEY = 'autoplay_up_next';

function booleanPreference(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

/** Reads only an explicit persisted choice and ignores malformed preference data. */
export function autoplayUpNextPreferenceFromUiPrefs(uiPrefs: unknown): boolean | undefined {
  if (!uiPrefs || typeof uiPrefs !== 'object' || Array.isArray(uiPrefs)) return undefined;
  return booleanPreference((uiPrefs as Record<string, unknown>)[AUTOPLAY_UP_NEXT_UI_PREF_KEY]);
}

/** Adds the autoplay choice without discarding other current or future UI preferences. */
export function withAutoplayUpNextPreference(uiPrefs: unknown, autoplayUpNext: boolean): Record<string, unknown> {
  const existing = uiPrefs && typeof uiPrefs === 'object' && !Array.isArray(uiPrefs)
    ? uiPrefs as Record<string, unknown>
    : {};
  return { ...existing, [AUTOPLAY_UP_NEXT_UI_PREF_KEY]: autoplayUpNext };
}
