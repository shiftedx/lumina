/// <reference types="node" />
// @vitest-environment jsdom

/** Semantic light/dark/system themes. */
import { readFileSync } from 'node:fs';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsSurface } from './features/settings/SettingsSurface';
import { applyTheme, storedThemePreference, THEME_STORAGE_KEY, themePreferenceFromUiPrefs } from './theme';
import { createInitialWorkspaceState, workspaceSettingsUpdate } from './workspace';
import type { UserProfile } from './types';

// Relative to frontend/ (the vitest cwd); CSS `?raw` imports come back empty under vitest.
const read = (path: string) => readFileSync(path, 'utf8');

function installStorage(storage: Pick<Storage, 'getItem' | 'setItem'>) {
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storage });
}

afterEach(() => {
  delete document.documentElement.dataset.theme;
});

const user: UserProfile = {
  id: 'member-1', username: 'alexandria', display_name: 'Alexandria', role: 'admin', is_active: true,
  onboarding_status: 'completed', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

describe('test_theme_no_flash_and_persists', () => {
  it.each(['light', 'oled'] as const)('saves %s in ui_prefs and the boot hint, and restores it on the next load', (choice) => {
    const state = createInitialWorkspaceState();
    state.preferences.theme = choice;
    state.userSettings = { ui_prefs: { sidebar_collapsed: true } } as never;
    expect(workspaceSettingsUpdate(state).ui_prefs).toMatchObject({ sidebar_collapsed: true, theme: choice });

    applyTheme(choice, false);
    expect(document.documentElement.dataset.theme).toBe(choice);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe(choice);
    expect(storedThemePreference()).toBe(choice);
    expect(createInitialWorkspaceState().preferences.theme).toBe(choice);
    expect(themePreferenceFromUiPrefs({ theme: choice })).toBe(choice);
    expect(themePreferenceFromUiPrefs({ theme: 'dark' })).toBe('dark');
    expect(themePreferenceFromUiPrefs({ theme: 'neon' })).toBeUndefined();
  });

  it('keeps rendering when localStorage is denied', () => {
    const deny = () => { throw new DOMException('denied', 'SecurityError'); };
    installStorage({ getItem: deny, setItem: deny });
    expect(storedThemePreference()).toBe('system');
    const boot = read('public/theme-boot.js');
    expect(() => new Function(boot)()).not.toThrow();
    expect(() => applyTheme('dark', true)).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it.each(['light', 'oled'] as const)('boot script resolves %s before the app loads', (choice) => {
    const boot = read('public/theme-boot.js');
    window.localStorage.setItem(THEME_STORAGE_KEY, choice);
    new Function(boot)();
    expect(document.documentElement.dataset.theme).toBe(choice);
    expect(read('index.html')).toMatch(/<head>[\s\S]*<script src="\/theme-boot.js"><\/script>[\s\S]*<\/head>/);
  });
});

describe('test_theme_system_changes', () => {
  it('follows the OS only in system mode', () => {
    applyTheme('system', true);
    expect(document.documentElement.dataset.theme).toBe('light');
    applyTheme('system', false);
    expect(document.documentElement.dataset.theme).toBe('dark');
    applyTheme('light', false);
    expect(document.documentElement.dataset.theme).toBe('light');
    applyTheme('dark', true);
    expect(document.documentElement.dataset.theme).toBe('dark');
    applyTheme('oled', true);
    expect(document.documentElement.dataset.theme).toBe('oled');
  });

  it('settings exposes an accessible theme choice', () => {
    const onThemeChange = vi.fn();
    render(<SettingsSurface formatPreset="best" onFormatChange={() => undefined} onLogout={() => undefined} onThemeChange={onThemeChange} section="appearance" theme="system" user={user} />);
    expect((screen.getByRole('radio', { name: 'System' }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));
    expect(onThemeChange).toHaveBeenCalledWith('light');
    fireEvent.click(screen.getByRole('radio', { name: 'OLED' }));
    expect(onThemeChange).toHaveBeenLastCalledWith('oled');
  });
});
