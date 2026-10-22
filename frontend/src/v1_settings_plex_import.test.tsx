import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getPlexImport: vi.fn(), previewPlexImport: vi.fn(), runPlexImport: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { PlexHistoryImport } = await import('./features/settings/PlexHistoryImport');
const { sectionById } = await import('./features/settings/registry');
const { SettingsHostContext } = await import('./features/settings/settingsHost');

afterEach(() => vi.resetAllMocks());

const member = { id: 'm1', username: 'alice', role: 'viewer', is_active: true } as never;
const summary = { watched: 12, in_progress: 2, favorites: 7, up_to_date: 4, unmatched: 1, unmatched_names: ['Heat (1995)'] };

function setup(server: string | null = 'http://192.168.1.20:32400') {
  api.getPlexImport.mockResolvedValue({ server });
  render(<SettingsHostContext.Provider value={{ user: member }}><PlexHistoryImport /></SettingsHostContext.Provider>);
}

describe('Watch history from Plex', () => {
  it('is registered beside the Plex server address', () => {
    expect(sectionById('privacy')!.entries.map((entry) => entry.id)).toContain('privacy.plex-history');
    expect(sectionById('media')!.entries.map((entry) => entry.id)).toContain('media.plex-import');
  });

  it('previews and imports with a masked token, omits unsupported favorites copy, then clears the token', async () => {
    setup();
    api.previewPlexImport.mockResolvedValue(summary);
    api.runPlexImport.mockResolvedValue(summary);
    const token = await screen.findByLabelText('Plex token') as HTMLInputElement;
    expect(token.type).toBe('password');
    expect(token.maxLength).toBe(4096);
    expect(screen.getByRole('link', { name: 'How to find your Plex token' }).getAttribute('href')).toContain('support.plex.tv');
    expect(screen.getByText('Favorites and watchlists are not transferred from Plex.')).toBeTruthy();
    await userEvent.type(token, ' plex-owner-token ');
    await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(api.previewPlexImport).toHaveBeenCalledWith('plex-owner-token');
    const dialog = await screen.findByRole('dialog', { name: 'Import from Plex?' });
    expect(within(dialog).getByRole('list', { name: 'What would change' }).textContent).toContain('In progress2');
    expect(within(dialog).queryByText(/favorite/i)).toBeNull();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Import history' }));
    await waitFor(() => expect(api.runPlexImport).toHaveBeenCalledWith('plex-owner-token'));
    expect((await screen.findByRole('status')).textContent).toBe('Imported from Plex: 12 watched, 2 in progress.');
    expect(token.value).toBe('');
  });

  it('requires a configured server and clears the token when an import fails', async () => {
    setup(null);
    expect(await screen.findByText('Ask a vault owner to set the Plex server address.')).toBeTruthy();
    expect(screen.queryByLabelText('Plex token')).toBeNull();

    setup();
    api.previewPlexImport.mockResolvedValue(summary);
    api.runPlexImport.mockRejectedValue(new Error('Plex did not answer.'));
    const token = await screen.findByLabelText('Plex token') as HTMLInputElement;
    await userEvent.type(token, 'temporary-token');
    await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
    const dialog = await screen.findByRole('dialog', { name: 'Import from Plex?' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Import history' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Plex did not answer.');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(token.value).toBe('');
    await waitFor(() => expect(token).toBe(document.activeElement));
  });
});
