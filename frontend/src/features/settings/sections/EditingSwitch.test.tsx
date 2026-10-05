import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getMediaServerSettings: vi.fn(), updateMediaServerSettings: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));

import { EditingSwitch } from './EditingSwitch';
import { LIBRARY_SECTION } from './library';
import { SettingRow } from '../SettingRow';

/** The switch is named by its row's heading. */
const inRow = () => <SettingRow id="library.editing" info="About editing." label={NAME} layout="switch"><EditingSwitch /></SettingRow>;

const NAME = 'Let household members edit details';
beforeEach(() => { api.getMediaServerSettings.mockReset(); api.updateMediaServerSettings.mockReset(); });

describe('EditingSwitch', () => {
  it('loads the flag and saves a flip at once', async () => {
    api.getMediaServerSettings.mockResolvedValue({ members_edit_metadata: false });
    api.updateMediaServerSettings.mockResolvedValue({ members_edit_metadata: true });
    render(inRow());
    const toggle = await screen.findByRole('switch', { name: NAME });
    await waitFor(() => expect((toggle as HTMLInputElement).disabled).toBe(false));
    expect((toggle as HTMLInputElement).checked).toBe(false);
    await userEvent.click(toggle);
    expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ members_edit_metadata: true });
    await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true));
  });
  it('puts the switch back and says so when the save fails', async () => {
    api.getMediaServerSettings.mockResolvedValue({ members_edit_metadata: false });
    api.updateMediaServerSettings.mockRejectedValue(new Error('x'));
    render(inRow());
    const toggle = await screen.findByRole('switch', { name: NAME });
    await waitFor(() => expect((toggle as HTMLInputElement).disabled).toBe(false));
    await userEvent.click(toggle);
    expect((await screen.findByRole('alert')).textContent).toBe('Lumina could not save this setting. Try again.');
    expect((toggle as HTMLInputElement).checked).toBe(false);
  });
  it('shows a load failure instead of a switch', async () => {
    api.getMediaServerSettings.mockRejectedValue('x');
    render(inRow());
    expect((await screen.findByRole('alert')).textContent).toBe('Unable to load this setting.');
    expect(screen.queryByRole('switch')).toBeNull();
  });
  it('is registered as library.editing and findable by "members"', () => {
    const entry = LIBRARY_SECTION.entries.find((candidate) => candidate.id === 'library.editing');
    expect(entry?.Control).toBe(EditingSwitch);
    expect(entry?.keywords).toContain('members');
  });
});
