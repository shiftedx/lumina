import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MediaServerSettings, MediaServerSettingsUpdate } from '../../types';
import { restoreAllStore, saveAllStore } from '../gallery/allStore';

const api = vi.hoisted(() => ({ getMediaServerSettings: vi.fn(), updateMediaServerSettings: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { AnimeFolders, animeFolderProblem, RESORT_POLL_MS, RESORTED_NOTE_MS } = await import('./AnimeFolders');

const settings = (patch: Partial<MediaServerSettings> = {}): MediaServerSettings => ({
  jellyfin_enabled: true, jellyfin_url: 'https://vault.example', has_tmdb_key: true, metadata_language: 'en-US', introdb_enabled: false, hwaccel: 'auto',
  max_playback_sessions: 3, transcode_cache_gb: 10, jellyfin_import_url: null, anime_folders: ['Anime'], recategorising: false, ...patch,
});
const flush = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe('animeFolderProblem', () => {
  it('refuses what the row refuses, in its words', () => {
    const listed = ['Anime'];
    expect(animeFolderProblem('   ', listed)).toBe('Enter a folder name.');
    expect(animeFolderProblem('TV/Anime', listed)).toBe('Use one folder name, not a path.');
    expect(animeFolderProblem('TV\\Anime', listed)).toBe('Use one folder name, not a path.');
    expect(animeFolderProblem('x'.repeat(65), listed)).toBe('Folder names are at most 64 characters.');
    expect(animeFolderProblem('..', listed)).toBe('Choose a real folder name.');
    expect(animeFolderProblem(' . ', listed)).toBe('Choose a real folder name.');
    expect(animeFolderProblem(' ANIME ', listed)).toBe('That folder is already listed.');
    expect(animeFolderProblem('Cartoons', Array.from({ length: 20 }, (_, index) => `Folder ${index}`))).toBe('Up to 20 folder names.');
    expect(animeFolderProblem(' Cartoons ', listed)).toBeNull();
    expect(animeFolderProblem('x'.repeat(64), listed)).toBeNull();
  });
});

describe('AnimeFolders', () => {
  it('adds a trimmed name with Enter, removes by name, and saves the list', async () => {
    api.getMediaServerSettings.mockResolvedValue(settings());
    api.updateMediaServerSettings.mockImplementation(async (update: MediaServerSettingsUpdate) => settings({ anime_folders: update.anime_folders ?? [], recategorising: true }));
    render(<AnimeFolders />);
    const field = await screen.findByRole('textbox', { name: 'Folder name' });
    expect(field.getAttribute('maxlength')).toBe('64');
    await userEvent.type(field, '  Cartoons {Enter}');
    expect(within(screen.getByRole('list', { name: 'Anime folders' })).getAllByRole('listitem').map((item) => item.firstChild?.textContent)).toEqual(['Anime', 'Cartoons']);
    expect((field as HTMLInputElement).value).toBe('');
    await userEvent.click(screen.getByRole('button', { name: 'Remove Anime' }));
    expect(document.activeElement).toBe(field);
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ anime_folders: ['Cartoons'] });
    expect(await screen.findByText('Re-sorting titles into Anime…')).toBeTruthy();
  });

  it('shows each problem under the field and keeps the name for fixing', async () => {
    api.getMediaServerSettings.mockResolvedValue(settings());
    render(<AnimeFolders />);
    const field = await screen.findByRole('textbox', { name: 'Folder name' });
    await userEvent.type(field, 'anime');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    const problem = screen.getByText('That folder is already listed.');
    expect(field.getAttribute('aria-describedby')).toBe(problem.id);
    expect(field.getAttribute('aria-invalid')).toBe('true');
    expect((field as HTMLInputElement).value).toBe('anime');
    await userEvent.type(field, 's');
    expect(screen.queryByText('That folder is already listed.')).toBeNull();
    expect(api.updateMediaServerSettings).not.toHaveBeenCalled();
  });

  it('builds the list and the field from the shared primitives, and Enter adds', async () => {
    api.getMediaServerSettings.mockResolvedValue(settings());
    render(<AnimeFolders />);
    const field = await screen.findByRole('textbox', { name: 'Folder name' });
    expect(field.closest('.g-field')).toBeTruthy();
    expect(screen.getByRole('list', { name: 'Anime folders' }).className).toContain('g-list');
    await userEvent.type(field, 'Cartoons{Enter}');
    expect(within(screen.getByRole('list', { name: 'Anime folders' })).getByText('Cartoons')).toBeTruthy();
  });

  it('warns that an empty list removes the Anime tab, and Discard brings the saved list back', async () => {
    api.getMediaServerSettings.mockResolvedValue(settings());
    render(<AnimeFolders />);
    await userEvent.click(await screen.findByRole('button', { name: 'Remove Anime' }));
    expect(screen.getByText('Nothing is sorted into Anime. The Anime tab and Jellyfin\'s Anime library disappear.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.getByRole('button', { name: 'Remove Anime' })).toBeTruthy();
  });

  it('follows a running re-sort every 2 s, then says "Titles re-sorted." for 10 s', async () => {
    vi.useFakeTimers();
    api.getMediaServerSettings
      .mockResolvedValueOnce(settings({ recategorising: true }))
      .mockResolvedValueOnce(settings({ recategorising: true }))
      .mockResolvedValue(settings());
    saveAllStore({ spotlight: null, slices: {}, scrollY: 0, focus: null });
    render(<AnimeFolders />);
    await flush();
    expect(screen.getByText('Re-sorting titles into Anime…')).toBeTruthy();
    await flush(RESORT_POLL_MS);
    expect(api.getMediaServerSettings).toHaveBeenCalledTimes(2);
    expect(screen.getByText('Re-sorting titles into Anime…')).toBeTruthy();
    await flush(RESORT_POLL_MS);
    expect(screen.getByText('Titles re-sorted.')).toBeTruthy();
    expect(restoreAllStore()).toBeNull(); // the All landing asks again after a re-sort
    await flush(RESORTED_NOTE_MS);
    expect(screen.queryByText('Titles re-sorted.')).toBeNull();
    await flush(RESORT_POLL_MS * 3);
    expect(api.getMediaServerSettings).toHaveBeenCalledTimes(3);
  });
});
