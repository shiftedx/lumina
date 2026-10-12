import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MediaServerSettings } from './types';
import type { SettingsLocation } from './features/settings/SettingsSurface';
const api = vi.hoisted(() => ({ getMediaServerSettings: vi.fn(), updateMediaServerSettings: vi.fn(), listBackups: vi.fn(), getAiConfig: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { SettingsSurface } = await import('./features/settings/SettingsSurface');
afterEach(() => { vi.resetAllMocks(); vi.restoreAllMocks(); });
const base = { id: 'u1', username: 'dana', display_name: 'Dana', is_active: true, bio: '', created_at: '', updated_at: '' };
const viewer = { ...base, role: 'viewer' as const };
const admin = { ...base, role: 'admin' as const };
const media: MediaServerSettings = { jellyfin_enabled: false, jellyfin_url: null, has_tmdb_key: false, metadata_language: 'en-US', introdb_enabled: false, hwaccel: 'auto', max_playback_sessions: 3, transcode_cache_gb: 10 };
const prefs = { normalizeLoudness: false, autoSkip: { intro: false, credits: false, recap: false }, profanity: { enabled: false, words: [] } };
function shell(owner = false, start: SettingsLocation = { section: null, page: null, space: 'personal' }) {
  const onPlaybackPrefsChange = vi.fn();
  function Host() {
    const [location, setLocation] = useState(start);
    return <SettingsSurface {...location} formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onLocation={setLocation} onPlaybackPrefsChange={onPlaybackPrefsChange} playbackPrefs={prefs} user={owner ? admin : viewer} />;
  }
  render(<Host />);
  return { onPlaybackPrefsChange };
}
const search = () => screen.getByRole('searchbox', { name: 'Search settings' });
const results = () => screen.getByRole('region', { name: 'Search results' });

describe('Settings destination search', () => {
  it('locates a setting without mounting forms, then opens its focused page', async () => {
    const props = shell();
    await userEvent.type(search(), 'sound');
    expect(within(results()).getByRole('link', { name: /Even out loudness/ }).getAttribute('href')).toContain('/settings/playback?page=watching#setting-playback-loudness');
    expect(within(results()).queryByRole('switch')).toBeNull();
    await userEvent.click(within(results()).getByRole('link', { name: /Even out loudness/ }));
    expect(screen.queryByRole('region', { name: 'Search results' })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('switch', { name: 'Even out loudness' }));
    await userEvent.click(screen.getByRole('switch', { name: 'Even out loudness' }));
    expect(props.onPlaybackPrefsChange).toHaveBeenCalledWith({ normalizeLoudness: true });
  });

  it('finds keywords and explanations and clears the query after opening a result', async () => {
    shell();
    await userEvent.type(search(), 'undo');
    await userEvent.click(within(results()).getByRole('link', { name: /Skip automatically/ }));
    expect(screen.getByRole('heading', { name: 'Skip automatically' })).toBeTruthy();
    expect((search() as HTMLInputElement).value).toBe('');
  });

  it('does not expose or fetch owner-only settings for a member', async () => {
    shell();
    await userEvent.type(search(), 'backup');
    expect(within(results()).getByRole('status').textContent).toBe('No settings match');
    expect(api.listBackups).not.toHaveBeenCalled();
    expect(api.getAiConfig).not.toHaveBeenCalled();
  });

  it('finds server settings from the personal space without making their API requests until opened', async () => {
    api.getMediaServerSettings.mockResolvedValue(media);
    api.updateMediaServerSettings.mockResolvedValue({ ...media, transcode_cache_gb: 20 });
    shell(true);
    await userEvent.type(search(), 'conversion cache');
    expect(api.getMediaServerSettings).not.toHaveBeenCalled();
    await userEvent.click(within(results()).getByRole('link', { name: /Conversion cache/ }));
    const cache = await screen.findByRole('spinbutton', { name: 'Conversion cache (GB)' });
    expect(screen.getByRole('heading', { level: 1, name: 'Manage Server' })).toBeTruthy();
    await userEvent.clear(cache);
    await userEvent.type(cache, '20');
    await userEvent.click(screen.getByRole('button', { name: 'Save playback and transcoding settings' }));
    await waitFor(() => expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ transcode_cache_gb: 20 }));
  });

  it('keeps a draft when replacing its page with search is declined', async () => {
    api.getMediaServerSettings.mockResolvedValue(media);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    shell(true, { section: 'transcoding', page: 'transcoding', space: 'server' });
    const cache = await screen.findByRole('spinbutton', { name: 'Conversion cache (GB)' });
    await userEvent.clear(cache);
    await userEvent.type(cache, '30');
    await userEvent.type(search(), 'x');
    expect(confirm).toHaveBeenCalledTimes(1);
    expect((search() as HTMLInputElement).value).toBe('');
    expect((cache as HTMLInputElement).value).toBe('30');
  });

  it('opens a searched advanced row inside its local disclosure', async () => {
    shell();
    await userEvent.type(search(), 'recent stream cache');
    await userEvent.click(within(results()).getByRole('link', { name: /Recent stream cache/ }));
    expect(document.getElementById('setting-playback-stream-cache')?.closest('details')?.open).toBe(true);
  });
});
