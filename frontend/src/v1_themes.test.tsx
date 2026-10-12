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
  it('saves the choice in ui_prefs and the boot hint, and restores it on the next load', () => {
    const state = createInitialWorkspaceState();
    state.preferences.theme = 'light';
    state.userSettings = { ui_prefs: { sidebar_collapsed: true } } as never;
    expect(workspaceSettingsUpdate(state).ui_prefs).toMatchObject({ sidebar_collapsed: true, theme: 'light' });

    applyTheme('light', false);
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
    expect(storedThemePreference()).toBe('light');
    expect(createInitialWorkspaceState().preferences.theme).toBe('light');
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

  it('boot script resolves the saved choice before the app loads', () => {
    const boot = read('public/theme-boot.js');
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light');
    new Function(boot)();
    expect(document.documentElement.dataset.theme).toBe('light');
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
  });

  it('settings exposes an accessible theme choice', () => {
    const onThemeChange = vi.fn();
    render(<SettingsSurface formatPreset="best" onFormatChange={() => undefined} onLogout={() => undefined} onThemeChange={onThemeChange} section="appearance" theme="system" user={user} />);
    expect((screen.getByRole('radio', { name: 'System' }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));
    expect(onThemeChange).toHaveBeenCalledWith('light');
  });
});
