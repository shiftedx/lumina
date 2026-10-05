import { describe, expect, it } from 'vitest';

import {
  resolveSidebarPreference,
  sidebarCollapsedPreferenceFromUiPrefs,
  sidebarCollapsedStorageValue,
  sidebarCollapsedFromStorageValue,
  withSidebarCollapsedPreference,
} from './sidebarModel';

describe('sidebar preference model', () => {
  it('starts members without any saved preference with the navigation hidden', () => {
    expect(resolveSidebarPreference({ memberPreference: undefined, localFallback: false, memberSettingsAvailable: true }))
      .toEqual({ collapsed: true, source: 'default' });
  });

  it('preserves an explicit member preference ahead of a local startup fallback', () => {
    expect(resolveSidebarPreference({ memberPreference: false, localFallback: true }))
      .toEqual({ collapsed: false, source: 'member' });
    expect(resolveSidebarPreference({ memberPreference: true, localFallback: false }))
      .toEqual({ collapsed: true, source: 'member' });
  });

  it('uses the local value only while member settings are unavailable', () => {
    expect(resolveSidebarPreference({ memberPreference: undefined, localFallback: false }))
      .toEqual({ collapsed: false, source: 'local' });
  });

  it('round-trips only explicit local boolean values', () => {
    expect(sidebarCollapsedFromStorageValue('1')).toBe(true);
    expect(sidebarCollapsedFromStorageValue('0')).toBe(false);
    expect(sidebarCollapsedFromStorageValue('collapsed')).toBeUndefined();
    expect(sidebarCollapsedStorageValue(true)).toBe('1');
    expect(sidebarCollapsedStorageValue(false)).toBe('0');
  });

  it('reads and updates only the sidebar field in member UI preferences', () => {
    expect(sidebarCollapsedPreferenceFromUiPrefs({ sidebar_collapsed: false })).toBe(false);
    expect(sidebarCollapsedPreferenceFromUiPrefs({ sidebar_collapsed: 'false' })).toBeUndefined();
    expect(withSidebarCollapsedPreference({ theme: 'dawn' }, true))
      .toEqual({ theme: 'dawn', sidebar_collapsed: true });
  });
});
