import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getPlexImport: vi.fn(), previewPlexHousehold: vi.fn(), runPlexHousehold: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { PlexHouseholdImport } = await import('./features/settings/PlexHouseholdImport');
const { sectionById } = await import('./features/settings/registry');

afterEach(() => vi.resetAllMocks());

const summary = (watched: number) => ({ watched, in_progress: 1, favorites: 9, up_to_date: 2, unmatched: 0, unmatched_names: [] });
const row = (plex_id: string, plex_name: string, action: 'import' | 'create' | 'skip', lumina_username: string | null, extra = {}) => ({
  plex_id, plex_name, action, lumina_username, disabled: false, reason: null, error: null, summary: summary(1), reset_url: null, reset_expires_at: null, ...extra,
});
const previewResult = { members: [
  row('plex-owner', 'Owner', 'import', 'owner', { summary: summary(40) }),
  row('plex-sam', 'Sam', 'create', 'sam'),
  row('plex-pat', 'Pat', 'create', 'pat', { error: 'History is unavailable.', summary: null }),
  row('plex-lee', 'Lee', 'create', 'lee', { disabled: true }),
] };

function setup() {
  api.getPlexImport.mockResolvedValue({ server: 'http://192.168.1.20:32400' });
  render(<PlexHouseholdImport />);
}

async function preview() {
  await userEvent.type(await screen.findByLabelText('Plex server owner token'), ' plex-owner-token ');
  await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
  return screen.findByRole('dialog', { name: 'Bring members over from Plex?' });
}

describe('Bring members over from Plex', () => {
  it('is a Members row', () => {
    expect(sectionById('members')!.entries.map((entry) => entry.id)).toContain('members.plex');
  });

  it('previews accessible users, leaves unavailable histories unselected, imports the chosen IDs, and retains reset links when a later row fails', async () => {
    setup();
    expect((await screen.findByLabelText('Plex server owner token') as HTMLInputElement).maxLength).toBe(4096);
    api.previewPlexHousehold.mockResolvedValue(previewResult);
    api.runPlexHousehold.mockResolvedValue({ members: [
      row('plex-owner', 'Owner', 'import', 'owner', { summary: summary(40) }),
      row('plex-sam', 'Sam', 'create', 'sam', { reset_url: 'http://lumina.test/#reset=abc', reset_expires_at: '2026-10-22T12:00:00Z' }),
      row('plex-pat', 'Pat', 'create', 'pat', { error: 'History is unavailable.', summary: null }),
    ] });
    const dialog = await preview();
    expect(api.previewPlexHousehold).toHaveBeenCalledWith('plex-owner-token');
    const box = (name: RegExp) => within(dialog).getByRole('checkbox', { name }) as HTMLInputElement;
    expect([box(/^Owner/), box(/^Sam/), box(/^Pat/), box(/^Lee/)].map((input) => input.checked)).toEqual([true, true, false, false]);
    expect([box(/^Pat/), box(/^Lee/)].map((input) => input.disabled)).toEqual([true, true]);
    expect(within(dialog).queryByText(/favorite/i)).toBeNull();
    await userEvent.click(box(/^Owner/));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Bring members over' }));
    expect(api.runPlexHousehold).toHaveBeenCalledWith('plex-owner-token', ['plex-sam']);
    const done = await screen.findByRole('dialog', { name: 'Members brought over' });
    expect((within(done).getByLabelText('Password link for sam') as HTMLInputElement).value).toBe('http://lumina.test/#reset=abc');
    expect(within(done).getByText('History is unavailable.')).toBeTruthy();
    expect((screen.getByLabelText('Plex server owner token') as HTMLInputElement).value).toBe('');
  });

  it('clears the token when importing fails', async () => {
    setup();
    api.previewPlexHousehold.mockResolvedValue(previewResult);
    api.runPlexHousehold.mockRejectedValue(new Error('Plex did not answer.'));
    const dialog = await preview();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Bring members over' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Plex did not answer.');
    expect((screen.getByLabelText('Plex server owner token') as HTMLInputElement).value).toBe('');
  });
});
