/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getJellyfinImport: vi.fn(), previewJellyfinImport: vi.fn(), runJellyfinImport: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const settingsCss = readFileSync('src/features/settings/settings.css', 'utf8');
const uiCss = readFileSync('src/ui/ui.css', 'utf8');
const { JellyfinHistoryImport } = await import('./features/settings/JellyfinHistoryImport');
const { sectionById } = await import('./features/settings/registry');
const { SettingsHostContext } = await import('./features/settings/settingsHost');

afterEach(() => vi.resetAllMocks());

const member = { id: 'm1', username: 'alice', role: 'viewer', is_active: true } as never;
const owner = { id: 'a1', username: 'owner', role: 'admin', is_active: true } as never;
const summary = { watched: 12, in_progress: 2, favorites: 3, up_to_date: 4, unmatched: 2, unmatched_names: ['Heat (1995)', 'Show S03E01'] };

function setup(user = member, server: string | null = 'http://192.168.1.20:8096') {
  api.getJellyfinImport.mockResolvedValue({ server });
  render(<SettingsHostContext.Provider value={{ user }}><JellyfinHistoryImport /></SettingsHostContext.Provider>);
}

async function preview() {
  await userEvent.type(await screen.findByLabelText('Jellyfin username'), 'alice');
  await userEvent.type(screen.getByLabelText('Jellyfin password'), 'jf-secret-pw');
  await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
  return screen.findByRole('dialog', { name: 'Import from Jellyfin?' });
}

describe('Watch history from Jellyfin', () => {
  it('is a Privacy & data row, and its address is a Media server row', () => {
    expect(sectionById('privacy')!.entries.map((entry) => entry.id)).toContain('privacy.jellyfin-history');
    expect(sectionById('media')!.entries.map((entry) => entry.id)).toContain('media.jellyfin-import');
  });

  it('tells a member to ask a vault owner when no server is set, and a vault owner where to set it', async () => {
    setup(member, null);
    expect(await screen.findByText('Ask a vault owner to set the Jellyfin server address.')).toBeTruthy();
    expect(screen.queryByLabelText('Jellyfin password')).toBeNull();
  });

  it('points a vault owner at Media server when no server is set', async () => {
    setup(owner, null);
    expect(await screen.findByText('Set the Jellyfin server address under Media server first.')).toBeTruthy();
  });

  it('previews, imports with the same sign-in, then forgets the password', async () => {
    setup();
    api.previewJellyfinImport.mockResolvedValue(summary);
    api.runJellyfinImport.mockResolvedValue(summary);
    const dialog = await preview();
    expect(api.previewJellyfinImport).toHaveBeenCalledWith('alice', 'jf-secret-pw');
    const counts = within(dialog).getByRole('list', { name: 'What would change' });
    expect(counts.textContent).toContain('Watched12');
    expect(counts.textContent).toContain('Already up to date4');
    expect(within(dialog).getByText('Heat (1995)')).toBeTruthy();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Import history' }));
    await waitFor(() => expect(api.runJellyfinImport).toHaveBeenCalledWith('alice', 'jf-secret-pw'));
    expect((await screen.findByRole('status')).textContent).toBe('Imported from Jellyfin: 12 watched, 2 in progress, 3 new favorites.');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((screen.getByLabelText('Jellyfin password') as HTMLInputElement).value).toBe('');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview' })).toBe(document.activeElement));
  });

  it('clears the password after a failed import, and leaves it running for a failed preview', async () => {
    setup();
    api.previewJellyfinImport.mockResolvedValue(summary);
    api.runJellyfinImport.mockRejectedValue(new Error('The import could not run.'));
    const dialog = await preview();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Import history' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect((screen.getByLabelText('Jellyfin password') as HTMLInputElement).value).toBe('');
  });

  it('shows a refused sign-in in the row and opens no dialog', async () => {
    setup();
    api.previewJellyfinImport.mockRejectedValue(new Error('Jellyfin did not accept that username and password.'));
    await userEvent.type(await screen.findByLabelText('Jellyfin username'), 'alice');
    await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Jellyfin did not accept that username and password.');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Esc and the TV Back key cancel without importing, clear the password and return focus to Preview', async () => {
    setup();
    api.previewJellyfinImport.mockResolvedValue(summary);
    fireEvent(await preview(), new Event('cancel', { cancelable: true }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect((screen.getByLabelText('Jellyfin password') as HTMLInputElement).value).toBe('');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview' })).toBe(document.activeElement));
    await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
    fireEvent.keyDown(within(await screen.findByRole('dialog', { name: 'Import from Jellyfin?' })).getByRole('button', { name: 'Cancel' }), { key: 'GoBack' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect((screen.getByLabelText('Jellyfin password') as HTMLInputElement).value).toBe('');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview' })).toBe(document.activeElement));
    expect(api.runJellyfinImport).not.toHaveBeenCalled();
  });

  it('offers no import when everything is already up to date', async () => {
    setup();
    api.previewJellyfinImport.mockResolvedValue({ watched: 0, in_progress: 0, favorites: 0, up_to_date: 9, unmatched: 0, unmatched_names: [] });
    const dialog = await preview();
    expect(within(dialog).getByText('Everything Lumina has is already up to date.')).toBeTruthy();
    expect((within(dialog).getByRole('button', { name: 'Import history' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('a Settings dialog is built by the Dialog primitive and is opaque', async () => {
    setup();
    api.previewJellyfinImport.mockResolvedValue(summary);
    const dialog = await preview();
    expect(dialog.classList.contains('g-dialog')).toBe(true);
    expect(settingsCss).not.toMatch(/\.setting-dialog/);
    const block = /\.g-dialog\s*\{([^}]*)\}/.exec(uiCss)?.[1];
    expect(block).toBeTruthy();
    expect(block).toMatch(/background:\s*var\(--g-paper-3\)/);
    expect(block).not.toMatch(/rgba?\(|transparent|color-mix|hsla?\(/);
  });
});
