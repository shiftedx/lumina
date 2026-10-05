import { useCallback, useEffect } from 'react';

/** Persisted under the member-owned `ui_prefs` object. */
export const THEATER_MODE_UI_PREF_KEY = 'theater_mode';

export type TheaterModePreferenceSource = 'member' | 'local' | 'default';

export type TheaterModePreference = {
  theaterMode: boolean;
  source: TheaterModePreferenceSource;
};

export type TheaterModePreferenceInput = {
  /** The persisted per-member `ui_prefs.theater_mode` value. */
  memberPreference?: unknown;
  /** Whether member settings have loaded, even when they lack this preference. */
  memberSettingsAvailable?: boolean;
  /** A caller-owned startup fallback; it is never written by this module. */
  localFallback?: unknown;
};

function booleanPreference(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

/**
 * Resolves the durable member choice before a caller-owned startup fallback.
 * A member with loaded settings but no choice starts in the standard layout.
 */
export function resolveTheaterModePreference({
  memberPreference,
  memberSettingsAvailable = false,
  localFallback,
}: TheaterModePreferenceInput): TheaterModePreference {
  const member = booleanPreference(memberPreference);
  if (member !== undefined) return { theaterMode: member, source: 'member' };

  if (memberSettingsAvailable) return { theaterMode: false, source: 'default' };

  const local = booleanPreference(localFallback);
  if (local !== undefined) return { theaterMode: local, source: 'local' };

  return { theaterMode: false, source: 'default' };
}

/** Reads only an explicit persisted choice and ignores malformed preference data. */
export function theaterModePreferenceFromUiPrefs(uiPrefs: unknown): boolean | undefined {
  if (!uiPrefs || typeof uiPrefs !== 'object' || Array.isArray(uiPrefs)) return undefined;
  return booleanPreference((uiPrefs as Record<string, unknown>)[THEATER_MODE_UI_PREF_KEY]);
}

/** Adds the theater choice without discarding other current or future UI preferences. */
export function withTheaterModePreference(uiPrefs: unknown, theaterMode: boolean): Record<string, unknown> {
  const existing = uiPrefs && typeof uiPrefs === 'object' && !Array.isArray(uiPrefs)
    ? uiPrefs as Record<string, unknown>
    : {};
  return { ...existing, [THEATER_MODE_UI_PREF_KEY]: theaterMode };
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (typeof Element === 'undefined' || !(target instanceof Element)) return false;
  if ((target as HTMLElement).isContentEditable || (target as HTMLElement).contentEditable === 'true') return true;
  const editable = target.closest('input, textarea, select, [contenteditable]');
  return Boolean(editable && editable.getAttribute('contenteditable') !== 'false');
}

/** T toggles theater mode when it will not steal typing or browser shortcuts. */
export function isTheaterModeShortcut(event: Pick<KeyboardEvent, 'altKey' | 'ctrlKey' | 'defaultPrevented' | 'key' | 'metaKey' | 'target'>): boolean {
  return !event.defaultPrevented
    && !event.altKey
    && !event.ctrlKey
    && !event.metaKey
    && event.key.toLowerCase() === 't'
    && !isEditableTarget(event.target);
}

/** Installs the watch-only shortcut while a theater control is mounted. */
export function useTheaterModeShortcut(onToggle: () => void, enabled = true): void {
  useEffect(() => {
    if (!enabled || typeof document === 'undefined') return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTheaterModeShortcut(event)) return;
      event.preventDefault();
      onToggle();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [enabled, onToggle]);
}

export type TheaterModeControlProps = {
  theaterMode: boolean;
  onTheaterModeChange: (theaterMode: boolean) => void;
  /** Allows a containing modal or another keyboard owner to opt out. */
  shortcutEnabled?: boolean;
};

/**
 * A controlled player-extension control. Its parent owns persistence and the
 * layout class, so switching never remounts the native media element.
 */
export function TheaterModeControl({
  theaterMode,
  onTheaterModeChange,
  shortcutEnabled = true,
}: TheaterModeControlProps) {
  const toggle = useCallback(() => onTheaterModeChange(!theaterMode), [onTheaterModeChange, theaterMode]);
  useTheaterModeShortcut(toggle, shortcutEnabled);
  const label = theaterMode ? 'Exit theater mode' : 'Enter theater mode';

  return (
    <button
      aria-label={label}
      aria-pressed={theaterMode}
      className="theater-mode-toggle"
      data-tooltip={label}
      data-theater-mode={theaterMode ? 'theater' : 'standard'}
      onClick={toggle}
      style={{ minHeight: 44, minWidth: 44 }}
      title={label}
      type="button"
    >
      <svg aria-hidden="true" fill="none" focusable="false" height="20" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="20">
        <rect height="14" rx="1.5" width="18" x="3" y="5" />
        <path d="M3 8h18M3 16h18" />
      </svg>
    </button>
  );
}
