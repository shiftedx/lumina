import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MediaServerSettings } from './types';

const api = vi.hoisted(() => ({ getMediaServerSettings: vi.fn(), updateMediaServerSettings: vi.fn(), listBackups: vi.fn(), listConnectedApps: vi.fn(), listStorageRoots: vi.fn(), getStorageRules: vi.fn(), getRecordingRetention: vi.fn(), listImports: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { SettingsSurface } = await import('./features/settings/SettingsSurface');

afterEach(() => { vi.resetAllMocks(); vi.restoreAllMocks(); });
const base = { id: 'u1', username: 'dana', display_name: 'Dana', is_active: true } as const;
const viewer = { ...base, role: 'viewer' } as never;
const admin = { ...base, role: 'admin' } as never;
const media: MediaServerSettings = { jellyfin_enabled: false, jellyfin_url: null, has_tmdb_key: false, metadata_language: 'en-US', introdb_enabled: false, hwaccel: 'auto', max_playback_sessions: 3, transcode_cache_gb: 10 };
const prefs = { normalizeLoudness: false, autoSkip: { intro: false, credits: false, recap: false }, profanity: { enabled: false, words: [] } };

function shell(user: never, extra: Record<string, unknown> = {}) {
  api.listConnectedApps.mockResolvedValue([]);
  api.listBackups.mockResolvedValue({ backups: [], schedule: { daily: true, keep: 7 } });
  api.listStorageRoots.mockResolvedValue([{ id: 'fast', label: 'Fast SSD', path: '/media/fast', mode: 'managed', enabled: true, minimum_free_bytes: 0, artifact_count: 0, observation: { state: 'available', free_bytes: 5e9, total_bytes: 1e10, checked_at: '2026-09-24T10:00:00Z' } }]);
  api.getStorageRules.mockResolvedValue({ revision: 1, default_root_id: null, rules: [] });
  api.getRecordingRetention.mockResolvedValue({ keep_days: 30, max_gb: 0 });
  api.listImports.mockResolvedValue([]);
  const props = { user, formatPreset: 'best', onFormatChange: vi.fn(), onLogout: vi.fn(), playbackPrefs: prefs, onPlaybackPrefsChange: vi.fn(), onPlaybackMaxHeightChange: vi.fn(), ...extra };
  const { rerender } = render(<SettingsSurface {...props} />);
  return Object.assign(props, { rerender: (section: string) => rerender(<SettingsSurface {...props} section={section as never} />) });
}
const search = () => screen.getByRole('searchbox', { name: 'Search settings' });

describe('Settings search', () => {
  it('finds a row by its label and changes it right in the results', async () => {
    const props = shell(viewer);
    await userEvent.type(search(), 'sound');
    const results = screen.getByRole('region', { name: 'Search results' });
    expect(within(results).getByRole('heading', { level: 2, name: 'Playback' })).toBeTruthy();
    await userEvent.click(within(results).getByRole('switch', { name: 'Even out loudness' }));
    expect(props.onPlaybackPrefsChange).toHaveBeenCalledWith({ normalizeLoudness: true });
    expect(document.querySelector('.g-settings')?.getAttribute('data-view')).toBe('detail');
  });

  it('drops the search when browser Back opens another section', async () => {
    const props = shell(viewer, { section: 'account' });
    await userEvent.type(search(), 'sound');
    expect(screen.getByRole('region', { name: 'Search results' })).toBeTruthy();
    props.rerender('playback');  // popstate: LuminaApp confirmed the leave and changed the section prop
    expect(screen.queryByRole('region', { name: 'Search results' })).toBeNull();
    expect((search() as HTMLInputElement).value).toBe('');
  });

  it('finds rows by ⓘ text and by keyword', async () => {
    shell(viewer);
    await userEvent.type(search(), 'undo');
    expect(screen.getByRole('button', { name: 'About Skip automatically' })).toBeTruthy();
    await userEvent.clear(search());
    await userEvent.type(search(), 'resolution');
    expect(screen.getByRole('combobox', { name: 'Maximum quality' })).toBeTruthy();
  });

  it('says so when nothing matches, and never shows Server rows to a member', async () => {
    shell(viewer);
    await userEvent.type(search(), 'backup');
    expect(screen.getByRole('status').textContent).toBe('No settings match');
    expect(api.listBackups).not.toHaveBeenCalled();
  });

  it('lets a vault owner change a Save-form setting in the results, sending only that field', async () => {
    api.getMediaServerSettings.mockResolvedValue(media);
    api.updateMediaServerSettings.mockResolvedValue({ ...media, transcode_cache_gb: 20 });
    shell(admin);
    await userEvent.type(search(), 'conversion cache');
    const results = screen.getByRole('region', { name: 'Search results' });
    const cache = await within(results).findByRole('spinbutton', { name: 'Conversion cache (GB)' });
    await userEvent.clear(cache);
    await userEvent.type(cache, '20');
    await userEvent.click(within(results).getByRole('button', { name: 'Save playback and transcoding settings' }));
    await waitFor(() => expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ transcode_cache_gb: 20 }));
  });

  it('never drops unsaved edits: a dirty section stays pinned in the results until saved or discarded', async () => {
    api.getMediaServerSettings.mockResolvedValue(media);
    const confirm = vi.spyOn(window, 'confirm');
    shell(admin);
    await userEvent.type(search(), 'conversion cache');
    const cache = await screen.findByRole('spinbutton', { name: 'Conversion cache (GB)' });
    await userEvent.clear(cache);
    await userEvent.type(cache, '20');
    await userEvent.type(search(), 'zzz');
    expect(screen.getByText('No settings match')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: 'Transcoding' })).toBeTruthy();
    expect(screen.getByText('Unsaved changes. This section stays here until you save or discard.')).toBeTruthy();
    expect((screen.getByRole('spinbutton', { name: 'Conversion cache (GB)' }) as HTMLInputElement).value).toBe('20');
    expect(screen.getByRole('combobox', { name: 'Hardware acceleration' })).toBeTruthy();
    expect(confirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.queryByRole('heading', { level: 2, name: 'Transcoding' })).toBeNull();
    expect(api.updateMediaServerSettings).not.toHaveBeenCalled();
  });

  it('asks before a search replaces a section with unsaved edits', async () => {
    api.getMediaServerSettings.mockResolvedValue(media);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    shell(admin, { section: 'transcoding' });
    const cache = await screen.findByRole('spinbutton', { name: 'Conversion cache (GB)' });
    await userEvent.clear(cache);
    await userEvent.type(cache, '30');
    await userEvent.type(search(), 'x');
    expect(confirm).toHaveBeenCalledTimes(1);
    expect((search() as HTMLInputElement).value).toBe('');
    expect((screen.getByRole('spinbutton', { name: 'Conversion cache (GB)' }) as HTMLInputElement).value).toBe('30');
  });

  it('keeps every row of a dirty section while it still matches, so a row-owned draft is never unmounted', async () => {
    api.getMediaServerSettings.mockResolvedValue(media);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    shell(admin);
    await userEvent.type(search(), 'storage');
    // "Add rule" appears once the rules load, but stays disabled until the storage roots (a separate
    // fetch) load too; wait for both, so the click never lands on a still-disabled button.
    const addRule = await screen.findByRole('button', { name: 'Add rule' });
    await waitFor(() => expect((addRule as HTMLButtonElement).disabled).toBe(false));
    await userEvent.click(addRule);
    await userEvent.type(search(), ' cleanup');
    expect(screen.getByRole('button', { name: 'Delete rule 1' })).toBeTruthy();
    expect(screen.queryByText('Unsaved changes. This section stays here until you save or discard.')).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'All settings' }));
    expect(confirm).toHaveBeenCalledTimes(1);
  });
});
