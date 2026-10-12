import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getJellyfinImport: vi.fn(), previewJellyfinHousehold: vi.fn(), runJellyfinHousehold: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { JellyfinHouseholdImport } = await import('./features/settings/JellyfinHouseholdImport');
const { sectionById } = await import('./features/settings/registry');

afterEach(() => vi.resetAllMocks());

const counts = (watched: number) => ({ watched, in_progress: 0, favorites: 0, up_to_date: 0, unmatched: 0, unmatched_names: [] });
const row = (jellyfin_id: string, jellyfin_name: string, action: 'import' | 'create' | 'skip', lumina_username: string | null, extra = {}) => ({
  jellyfin_id, jellyfin_name, action, lumina_username, disabled: false, reason: null, error: null, summary: counts(1), reset_url: null, reset_expires_at: null, ...extra,
});
const PAIRED = 'root is already paired with the Lumina member dana.';
const PREVIEW = { members: [
  row('jf-root', 'root', 'import', 'dana', { summary: counts(40) }),
  row('jf-carol', 'Carol', 'create', 'carol', { summary: counts(12) }),
  row('jf-dave', 'Dave', 'create', 'dave', { disabled: true }),
  row('jf-dana', 'Dana', 'skip', null, { reason: PAIRED, summary: null }),
] };
const RESULT = { members: [
  row('jf-root', 'root', 'skip', null, { reason: 'Not selected.', summary: null }),
  row('jf-carol', 'Carol', 'create', 'carol', { summary: counts(12), reset_url: 'http://lumina.test/#reset=abc', reset_expires_at: '2026-09-29T12:00:00Z' }),
  row('jf-dave', 'Dave', 'skip', null, { reason: 'Not selected.', summary: null }),
  row('jf-dana', 'Dana', 'skip', null, { reason: PAIRED, summary: null }),
] };

function setup(server: string | null = 'http://192.168.1.20:8096') {
  api.getJellyfinImport.mockResolvedValue({ server });
  render(<JellyfinHouseholdImport />);
}

async function preview() {
  await userEvent.type(await screen.findByLabelText('Jellyfin administrator username'), 'root');
  await userEvent.type(screen.getByLabelText('Jellyfin administrator password'), 'jf-admin-pw');
  await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
}

describe('Bring members over from Jellyfin', () => {
  it('is a Members row', () => {
    expect(sectionById('members')!.entries.map((entry) => entry.id)).toContain('members.jellyfin');
  });

  it('points at Media server when no Jellyfin address is set', async () => {
    setup(null);
    expect(await screen.findByText('Set the Jellyfin server address under Media server first.')).toBeTruthy();
    expect(screen.queryByLabelText('Jellyfin administrator password')).toBeNull();
  });

  it('previews the pairing, brings the ticked users over, shows each new member link once, then forgets the password', async () => {
    setup();
    api.previewJellyfinHousehold.mockResolvedValue(PREVIEW);
    api.runJellyfinHousehold.mockResolvedValue(RESULT);
    await preview();
    const dialog = await screen.findByRole('dialog', { name: 'Bring members over from Jellyfin?' });
    expect(api.previewJellyfinHousehold).toHaveBeenCalledWith('root', 'jf-admin-pw');
    expect(within(dialog).getByText(/Create member carol/)).toBeTruthy();
    expect(within(dialog).getByText(/40 watched/)).toBeTruthy();
    expect(within(dialog).getByText(new RegExp(PAIRED))).toBeTruthy();
    const box = (name: RegExp) => within(dialog).getByRole('checkbox', { name }) as HTMLInputElement;
    expect([box(/^root/), box(/^Carol/), box(/^Dave/), box(/^Dana/)].map((input) => input.checked)).toEqual([true, true, false, false]);
    expect(box(/^Dana/).disabled).toBe(true);
    await userEvent.click(box(/^root/));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Bring members over' }));
    expect(api.runJellyfinHousehold).toHaveBeenCalledWith('root', 'jf-admin-pw', ['jf-carol']);
    const done = await screen.findByRole('dialog', { name: 'Members brought over' });
    expect((within(done).getByLabelText('Password link for carol') as HTMLInputElement).value).toBe('http://lumina.test/#reset=abc');
    await userEvent.click(within(done).getByRole('button', { name: 'Done' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((screen.getByLabelText('Jellyfin administrator password') as HTMLInputElement).value).toBe('');
  });

  it('closes the preview on a failed import, keeps the password for a retry and shows the error by the form', async () => {
    setup();
    api.previewJellyfinHousehold.mockResolvedValue(PREVIEW);
    api.runJellyfinHousehold.mockRejectedValue(new Error('Jellyfin did not answer.'));
    await preview();
    const dialog = await screen.findByRole('dialog', { name: 'Bring members over from Jellyfin?' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Bring members over' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Jellyfin did not answer.');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((screen.getByLabelText('Jellyfin administrator password') as HTMLInputElement).value).toBe('jf-admin-pw');
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Preview' })));
  });

  it('says a member whose save failed was not brought over', async () => {
    setup();
    api.previewJellyfinHousehold.mockResolvedValue(PREVIEW);
    api.runJellyfinHousehold.mockResolvedValue({ members: [row('jf-carol', 'Carol', 'create', 'carol', { summary: null, error: 'Lumina could not save this member.' })] });
    await preview();
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Bring members over' }));
    const done = await screen.findByRole('dialog', { name: 'Members brought over' });
    expect(within(done).getByText(/Not brought over/)).toBeTruthy();
    expect(within(done).queryByText(/Create member carol/)).toBeNull();
    expect(within(done).getByText('Lumina could not save this member.')).toBeTruthy();
  });

  it('shows a refused sign-in next to the form', async () => {
    setup();
    api.previewJellyfinHousehold.mockRejectedValue(new Error('That Jellyfin account is not an administrator. Sign in with a Jellyfin administrator account.'));
    await preview();
    expect((await screen.findByRole('alert')).textContent).toContain('not an administrator');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
