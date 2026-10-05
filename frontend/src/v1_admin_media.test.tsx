import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import type { MediaServerSettings } from './types';

const api = vi.hoisted(() => ({
  getMediaServerSettings: vi.fn(), updateMediaServerSettings: vi.fn(), testTmdbKey: vi.fn(), refreshAllMetadata: vi.fn(), listUnmatchedTitles: vi.fn(),
  runTranscodeDiagnostics: vi.fn(), searchTitleMatches: vi.fn(), identifyTitle: vi.fn(), unmatchTitle: vi.fn(), refreshTitleMetadata: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { mediaServerChanges } = await import('./features/settings/sections/media');
const { sectionById } = await import('./features/settings/registry');
const { SectionRows } = await import('./features/settings/SettingRow');
const { SettingsHostContext } = await import('./features/settings/settingsHost');
const { TMDB_ATTRIBUTION } = await import('./features/titles/titleModel');

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
afterEach(() => vi.resetAllMocks());

const settings: MediaServerSettings = { jellyfin_enabled: false, jellyfin_url: 'https://vault.example', has_tmdb_key: true, metadata_language: 'en-US', introdb_enabled: false, hwaccel: 'auto', max_playback_sessions: 3, transcode_cache_gb: 10, jellyfin_import_url: null };
const draft = { tmdb_api_key: '', clear_tmdb_key: false, metadata_language: 'en-US', introdb_enabled: false, jellyfin_enabled: false, hwaccel: 'auto' as const, max_playback_sessions: '3', transcode_cache_gb: '10', jellyfin_import_url: '' };
const user = { id: 'admin', username: 'owner', role: 'admin', is_active: true } as never;

function setup(...ids: Array<'media' | 'transcoding'>) {
  api.getMediaServerSettings.mockResolvedValue(settings);
  api.listUnmatchedTitles.mockResolvedValue([{ id: 'm9', type: 'movie', name: 'Arival', year: 2016, genres: [], added_at: '2026-09-01T00:00:00Z', user_data: { played: false, is_favorite: false, position_seconds: 0 } }]);
  render(<SettingsHostContext.Provider value={{ user, onMessage: vi.fn() }}>{ids.map((id) => <SectionRows key={id} section={sectionById(id)!} />)}</SettingsHostContext.Provider>);
}

describe('Media server and Playback & transcoding sections', () => {
  it('sends only changed fields and never echoes the TMDB key', () => {
    expect(mediaServerChanges(settings, draft)).toEqual({});
    expect(mediaServerChanges(settings, { ...draft, tmdb_api_key: 'abc123', max_playback_sessions: '2', jellyfin_enabled: true })).toEqual({ tmdb_api_key: 'abc123', max_playback_sessions: 2, jellyfin_enabled: true });
    expect(mediaServerChanges(settings, { ...draft, tmdb_api_key: 'ignored', clear_tmdb_key: true })).toEqual({ tmdb_api_key: '' });
    expect(mediaServerChanges(settings, { ...draft, metadata_language: ' de-DE ', hwaccel: 'qsv', transcode_cache_gb: '25' })).toEqual({ metadata_language: 'de-DE', hwaccel: 'qsv', transcode_cache_gb: 25 });
  });

  it('saves the Jellyfin server members import from, and an empty address turns importing off', async () => {
    expect(mediaServerChanges({ ...settings, jellyfin_import_url: 'http://jf.lan:8096' }, { ...draft, jellyfin_import_url: '' })).toEqual({ jellyfin_import_url: '' });
    setup('media');
    api.updateMediaServerSettings.mockResolvedValue({ ...settings, jellyfin_import_url: 'http://192.168.1.20:8096' });
    await userEvent.type(await screen.findByRole('textbox', { name: 'Jellyfin server address' }), 'http://192.168.1.20:8096');
    await userEvent.click(screen.getByRole('button', { name: 'Save media server settings' }));
    await waitFor(() => expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ jellyfin_import_url: 'http://192.168.1.20:8096' }));
    expect(screen.getByRole('button', { name: 'About Jellyfin server to import from' })).toBeTruthy();
  });

  it('saves a new TMDB key, clears the field, tests it and credits TMDB', async () => {
    setup('media');
    api.updateMediaServerSettings.mockResolvedValue(settings);
    api.testTmdbKey.mockResolvedValue({ ok: true });
    const key = await screen.findByLabelText(/^TMDB API key/) as HTMLInputElement;
    const save = screen.getByRole('button', { name: 'Save media server settings' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(key.placeholder).toMatch(/Saved key hidden/);
    await userEvent.type(key, 'abc123');
    await userEvent.click(save);
    await waitFor(() => expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ tmdb_api_key: 'abc123' }));
    expect(((await screen.findByLabelText(/^TMDB API key/)) as HTMLInputElement).value).toBe('');
    await userEvent.click(screen.getByRole('button', { name: 'Test key' }));
    expect(await screen.findByText('The key works.')).toBeTruthy();
    expect(screen.getByText(TMDB_ATTRIBUTION)).toBeTruthy();
  });

  it('turns on apps like Infuse and shows the address and the steps', async () => {
    setup('media');
    api.updateMediaServerSettings.mockResolvedValue({ ...settings, jellyfin_enabled: true });
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    await userEvent.click(await screen.findByRole('switch', { name: 'Let apps like Infuse connect' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save media server settings' }));
    await waitFor(() => expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ jellyfin_enabled: true }));
    const address = await screen.findByRole('textbox', { name: 'Server address for apps' });
    expect((address as HTMLInputElement).value).toBe('https://vault.example');
    expect(within(screen.getByRole('list', { name: 'Connect Infuse' })).getAllByRole('listitem')).toHaveLength(4);
    await userEvent.click(screen.getByRole('button', { name: 'Copy address' }));
    expect(writeText).toHaveBeenCalledWith('https://vault.example');
    // Apps connect with host:port alone: no /jellyfin ending to type and no proxy workaround.
    expect(screen.queryByText(/\/jellyfin|jf\./)).toBeNull();
  });

  it('falls back to this page’s origin when no public URL is configured', async () => {
    api.getMediaServerSettings.mockResolvedValue({ ...settings, jellyfin_enabled: true, jellyfin_url: null });
    api.listUnmatchedTitles.mockResolvedValue([]);
    render(<SettingsHostContext.Provider value={{ user, onMessage: vi.fn() }}><SectionRows section={sectionById('media')!} /></SettingsHostContext.Provider>);
    expect(((await screen.findByRole('textbox', { name: 'Server address for apps' })) as HTMLInputElement).value).toBe(window.location.origin);
  });

  it('explains the conversion limit in ⓘ and runs diagnostics', async () => {
    setup('transcoding');
    api.runTranscodeDiagnostics.mockResolvedValue({ hwaccel: 'auto', active: 'none', probe_ok: false, probe_error: 'qsv: no device', tonemap: 'software', hardware_disabled: false, software_fallbacks: 2, sessions: { video_sw: 1, remux: 2 }, throttled: 0, cache_bytes: 2 * 1024 ** 3, cache_cap_bytes: 10 * 1024 ** 3, speeds: {}, recent_errors: [] });
    expect(await screen.findByText(/remuxes and audio-only conversions have their own separate limit of 16/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Run diagnostics' }));
    const facts = await screen.findByRole('list', { name: 'Diagnostics' });
    expect(facts.textContent).toContain('Software only');
    expect(facts.textContent).toContain('qsv: no device');
    expect(facts.textContent).toContain('2.0 GB of 10 GB');
  });

  it('keeps the two forms apart: each Save sends only its own fields', async () => {
    setup('media', 'transcoding');
    api.updateMediaServerSettings.mockResolvedValue({ ...settings, hwaccel: 'qsv' });
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Hardware acceleration' }), 'qsv');
    await userEvent.clear(screen.getByRole('textbox', { name: 'Details language' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Details language' }), 'de-DE');
    await userEvent.click(screen.getByRole('button', { name: 'Save playback and transcoding settings' }));
    await waitFor(() => expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ hwaccel: 'qsv' }));
    expect((screen.getByRole('button', { name: 'Save media server settings' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('opens Fix match for an unmatched title', async () => {
    setup('media');
    api.searchTitleMatches.mockResolvedValue([]);
    await userEvent.click(await screen.findByRole('button', { name: 'Identify Arival (2016)' }));
    expect(screen.getByRole('dialog', { name: 'Fix match' })).toBeTruthy();
    expect(api.searchTitleMatches).toHaveBeenCalledWith('m9', 'Arival', 2016);
    expect(document.querySelector('form dialog')).toBeNull();
  });
});
