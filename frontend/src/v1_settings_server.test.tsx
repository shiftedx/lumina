import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listUsers: vi.fn(), listInvitations: vi.fn(), listBackups: vi.fn(), deleteBackup: vi.fn(), getRecordingRetention: vi.fn(), updateRecordingRetention: vi.fn(),
  listStorageRoots: vi.fn(), getStorageRules: vi.fn(), listImports: vi.fn(), startImport: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { SETTINGS_SECTIONS, sectionById } = await import('./features/settings/registry');
const { SectionRows } = await import('./features/settings/SettingRow');
const { SettingsHostContext } = await import('./features/settings/settingsHost');

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
afterEach(() => vi.resetAllMocks());
const admin = { id: 'u1', username: 'dana', display_name: 'Dana', role: 'admin', is_active: true } as never;

function renderSection(id: Parameters<typeof sectionById>[0]) {
  return render(<SettingsHostContext.Provider value={{ user: admin, onMessage: vi.fn() }}><SectionRows section={sectionById(id)!} /></SettingsHostContext.Provider>);
}
function libraryApis() {
  api.getRecordingRetention.mockResolvedValue({ keep_days: 30, max_gb: 0 });
  api.listStorageRoots.mockResolvedValue([]);
  api.getStorageRules.mockResolvedValue({ revision: 0, default_root_id: null, rules: [] });
  api.listImports.mockResolvedValue([]);
}

describe('Server sections', () => {
  it('re-houses every admin screen in the spec order', () => {
    expect(SETTINGS_SECTIONS.filter((section) => section.group !== 'you').map((section) => section.id)).toEqual(['overview', 'activity', 'members', 'library', 'media', 'requests', 'transcoding', 'ai', 'tasks', 'backups', 'diagnostics']);
    expect(sectionById('library')!.entries.map((entry) => entry.id)).toEqual(['library.storage', 'library.retention', 'library.automation', 'library.imports', 'library.anime', 'library.editing', 'library.artwork']);
    expect(sectionById('backups')!.entries.map((entry) => entry.id)).toEqual(['backups.list', 'backups.schedule']);
  });

  it('gives the automatic-backup schedule its own row', async () => {
    api.listBackups.mockResolvedValue({ backups: [], schedule: { daily: true, keep: 7 } });
    renderSection('backups');
    const row = document.querySelector('[data-setting-id="backups.schedule"]') as HTMLElement;
    expect(await within(row).findByRole('switch', { name: /every day/ })).toBeTruthy();
    expect(within(row).getByRole('button', { name: 'About Automatic backups' })).toBeTruthy();
    expect(document.querySelectorAll('form[aria-label="Automatic backups"]')).toHaveLength(1);
  });

  it('deleting a backup asks first, names its date, and does nothing on cancel', async () => {
    api.listBackups.mockResolvedValue({ backups: [{ name: 'b1', kind: 'manual', created_at: '2026-09-01T10:00:00Z', size: 1_000_000, counts: {} }], schedule: { daily: true, keep: 7 } });
    api.deleteBackup.mockResolvedValue(undefined);
    renderSection('backups');
    const open = await screen.findByRole('button', { name: /^Delete backup from / });
    await userEvent.click(open);
    const dialog = screen.getByRole('dialog', { name: 'Delete this backup?' });
    expect(within(dialog).getByText(/^The copy from .+ is removed from the server and cannot be restored from\.$/)).toBeTruthy();
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(api.deleteBackup).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(open);
    await userEvent.click(open);
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete backup' }));
    await waitFor(() => expect(api.deleteBackup).toHaveBeenCalledWith('b1'));
  });

  it('shows recording cleanup once, as its own named row, beside storage and imports', async () => {
    libraryApis();
    renderSection('library');
    expect(await screen.findByRole('form', { name: 'Live recording cleanup' })).toBeTruthy();
    expect(screen.getAllByRole('form', { name: 'Live recording cleanup' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'About Imports' })).toBeTruthy();
  });

  it('a run started by Import now appears under Imports without a page reload', async () => {
    libraryApis();
    api.listStorageRoots.mockResolvedValue([{ id: 'rips', label: 'Old rips', path: '/media/rips', mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 0, observation: { state: 'available' } }]);
    api.startImport.mockResolvedValue({ id: 'run-1' });
    const browser = userEvent.setup();
    renderSection('library');
    await browser.click(await screen.findByRole('button', { name: 'Actions for Old rips' }));
    await browser.click(screen.getByRole('menuitem', { name: 'Import' }));
    await browser.click(screen.getByRole('button', { name: 'Import now' }));
    await waitFor(() => expect(api.listImports).toHaveBeenCalledTimes(2));
  });
});
