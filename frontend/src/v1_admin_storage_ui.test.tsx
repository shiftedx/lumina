import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => {
  class ApiRequestError extends Error { constructor(message: string, public status: number) { super(message); } }
  return {
    ApiRequestError, listStorageRoots: vi.fn(), createStorageRoot: vi.fn(), updateStorageRoot: vi.fn(), probeStorageRoot: vi.fn(),
    deleteStorageRoot: vi.fn(), getStorageRules: vi.fn(), saveStorageRules: vi.fn(), resolveStorage: vi.fn(), startImport: vi.fn(),
    getRecordingRetention: vi.fn(() => new Promise(() => {})), updateRecordingRetention: vi.fn(),
  };
});
vi.mock('./api', () => api);

const { AdminStorage } = await import('./features/admin/AdminStorage');
const { confirmLeaveSettings } = await import('./features/settings/unsavedChanges');

afterEach(() => { vi.resetAllMocks(); });

const root = (id: string, label: string, mode: 'managed' | 'external', state = 'available') => ({
  id, label, path: `/media/${id}`, mode, enabled: true, minimum_free_bytes: 0, artifact_count: 3, observation: { state, free_bytes: 5e9, total_bytes: 1e10, checked_at: '2026-09-24T10:00:00Z' },
});
const fast = root('fast', 'Fast SSD', 'managed');
const bulk = root('bulk', 'Bulk HDD', 'managed', 'offline');
const imports = root('imports', 'Old rips', 'external');
const rule = (id: string, priority: number, patch = {}) => ({ id, enabled: true, priority, sources: [], media_kinds: [], min_height: null, max_height: null, target_root_id: 'fast', relative_template: '{source}', ...patch });

function setup(rules = [rule('uhd', 10, { min_height: 2160 }), rule('audio', 20, { media_kinds: ['audio'], target_root_id: 'bulk' })]) {
  api.listStorageRoots.mockResolvedValue([fast, bulk, imports]);
  api.getStorageRules.mockResolvedValue({ revision: 4, default_root_id: null, rules });
  render(<AdminStorage />);
  return userEvent.setup();
}

describe('admin storage', () => {
  it('shows honest root state and keeps external roots visibly distinct', async () => {
    setup();
    const card = await screen.findByRole('row', { name: /Bulk HDD/ });
    expect(within(card).getByText('Offline')).not.toBeNull();
    expect(within(card).getByText(/mounted into the container/)).not.toBeNull();
    expect(within(screen.getByRole('row', { name: /Old rips/ })).getByText('External · read-only')).not.toBeNull();
  });

  it('roots are table rows with a mono path, free space as a progress bar and an actions menu', async () => {
    setup();
    const row = (await screen.findByRole('row', { name: /Fast SSD/ })) as HTMLElement;
    expect(within(row).getByText('/media/fast', { selector: '.g-mono' })).toBeTruthy();
    expect(within(row).getByRole('progressbar')).toBeTruthy();
    expect(within(row).getByRole('button', { name: 'Actions for Fast SSD' })).toBeTruthy();
    expect(document.querySelector('.admin-root-card, .admin-state')).toBeNull();
  });

  it('removing a storage root asks first', async () => {
    const browser = setup();
    api.deleteStorageRoot.mockResolvedValue(undefined);
    const actions = await screen.findByRole('button', { name: 'Actions for Fast SSD' });
    await browser.click(actions);
    await browser.click(screen.getByRole('menuitem', { name: 'Remove' }));
    const dialog = screen.getByRole('dialog', { name: 'Remove Fast SSD?' });
    expect(within(dialog).getByText(/no files are deleted/)).toBeTruthy();
    await browser.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(api.deleteStorageRoot).not.toHaveBeenCalled();
    await waitFor(() => expect(document.activeElement).toBe(actions));
    await browser.click(actions);
    await browser.click(screen.getByRole('menuitem', { name: 'Remove' }));
    await browser.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(api.deleteStorageRoot).toHaveBeenCalledWith('fast'));
  });

  it('test_route_editor_roundtrip: reorder, edit height, save with revision, then dry-run', async () => {
    const browser = setup();
    await browser.click(await screen.findByRole('button', { name: 'Move rule 2 up' }));
    const first = screen.getByRole('group', { name: 'Rule 1' });
    await browser.type(within(first).getByLabelText('Min height (px)'), '720');
    api.saveStorageRules.mockImplementation(async (payload) => ({ revision: 5, default_root_id: payload.default_root_id, rules: payload.rules }));
    await browser.click(screen.getByRole('button', { name: 'Save rules' }));
    expect(api.saveStorageRules).toHaveBeenCalledWith({
      expected_revision: 4, default_root_id: null,
      rules: [rule('audio', 10, { media_kinds: ['audio'], target_root_id: 'bulk', min_height: 720 }), rule('uhd', 20, { min_height: 2160 })],
    });
    expect(await screen.findByText(/Saved\. New downloads use these rules/)).not.toBeNull();
    expect((within(screen.getByRole('group', { name: 'Rule 1' })).getByLabelText('Min height (px)') as HTMLInputElement).value).toBe('720');

    api.resolveStorage.mockResolvedValue({ rule_id: 'uhd', rule_revision: 5, root_id: 'fast', root_label: 'Fast SSD', relative_path: 'youtube', reason: 'Matched rule uhd.', can_admit: true });
    const height = screen.getByLabelText('Actual height (px)');
    await browser.clear(height);
    await browser.type(height, '2160');
    await browser.click(screen.getByRole('button', { name: 'Test' }));
    expect(api.resolveStorage).toHaveBeenCalledWith({ source: 'youtube', media_kind: 'video', height: 2160 });
    expect(await screen.findByText('Rule 2 matched. Rules revision 5.')).not.toBeNull();
  });

  it('test_route_conflict_preserves_draft: a 409 keeps unsaved edits and offers reload', async () => {
    const browser = setup();
    const folder = within(await screen.findByRole('group', { name: 'Rule 1' })).getByLabelText('Folder inside root');
    await browser.clear(folder);
    await browser.type(folder, 'uhd/{{height}');
    api.saveStorageRules.mockRejectedValue(new api.ApiRequestError('Storage rules changed since you loaded them.', 409));
    await browser.click(screen.getByRole('button', { name: 'Save rules' }));
    expect(await screen.findByText(/changed elsewhere/)).not.toBeNull();
    expect((folder as HTMLInputElement).value).toBe('uhd/{height}');
    expect((screen.getByRole('button', { name: 'Save rules' }) as HTMLButtonElement).disabled).toBe(true);
    await browser.click(screen.getByRole('button', { name: 'Reload rules' }));
    await waitFor(() => expect(api.getStorageRules).toHaveBeenCalledTimes(2));
  });

  it('validates a rule inline before saving', async () => {
    const browser = setup();
    const first = await screen.findByRole('group', { name: 'Rule 1' });
    await browser.type(within(first).getByLabelText('Max height (px)'), '1080');
    expect(within(first).getByRole('alert').textContent).toMatch(/Minimum height must not be above maximum/);
    expect((screen.getByRole('button', { name: 'Save rules' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('test_external_target_disabled_server_denied: external roots are never offered; the server refusal is shown', async () => {
    const browser = setup([rule('crafted', 10, { target_root_id: 'imports' })]);
    const first = await screen.findByRole('group', { name: 'Rule 1' });
    const target = within(first).getByLabelText('Save to');
    expect(within(target).queryByRole('option', { name: 'Old rips' })).toBeNull();
    expect(within(first).getByRole('alert').textContent).toMatch(/Choose a managed root/);
    expect(within(screen.getByLabelText('Default destination')).queryByRole('option', { name: 'Old rips' })).toBeNull();

    await browser.selectOptions(target, 'fast');
    api.saveStorageRules.mockRejectedValue(new api.ApiRequestError('Rules may only target registered managed roots.', 422));
    await browser.click(screen.getByRole('button', { name: 'Save rules' }));
    expect(await screen.findByText('Rules may only target registered managed roots.')).not.toBeNull();
  });

  it('shows add-root validation inline and explains a refused removal', async () => {
    const browser = setup();
    await browser.type(await screen.findByLabelText('Container path'), '/etc');
    await browser.type(screen.getByLabelText('Label'), 'Nope');
    api.createStorageRoot.mockRejectedValue(new api.ApiRequestError('Storage root must be inside a configured mount parent (LUMINA_STORAGE_MOUNT_PARENTS).', 422));
    await browser.click(screen.getByRole('button', { name: 'Add root' }));
    const problem = await screen.findByText(/inside a configured mount parent/);
    expect(screen.getByLabelText('Container path').getAttribute('aria-describedby')).toBe(problem.id);
    expect(screen.getByLabelText('Container path').getAttribute('aria-invalid')).toBe('true');

    await browser.click(screen.getByRole('button', { name: 'Actions for Fast SSD' }));
    await browser.click(screen.getByRole('menuitem', { name: 'Remove' }));
    api.deleteStorageRoot.mockRejectedValue(new api.ApiRequestError('Storage rules still route media to this root; change them first.', 409));
    await browser.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }));
    expect((await screen.findByText(/change them first\. Disable it instead/)).closest('[role="alert"]')).toBeTruthy();
  });

  it('asks before leaving unsaved routing rules', async () => {
    const browser = setup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await browser.click(await screen.findByRole('button', { name: 'Move rule 2 up' }));
    expect(confirmLeaveSettings()).toBe(false);
    expect(confirm).toHaveBeenCalledWith('You have unsaved changes in Library & storage. Leave without saving?');
    confirm.mockRestore();
  });

  it('offers Import now after adding an external root, for the household by default', async () => {
    const browser = setup();
    api.createStorageRoot.mockResolvedValue(root('films', 'Film drive', 'external'));
    api.startImport.mockResolvedValue({ id: 'run-9' });
    await browser.type(await screen.findByLabelText('Container path'), '/media/films');
    await browser.type(screen.getByLabelText('Label'), 'Film drive');
    await browser.selectOptions(screen.getByLabelText('Mode'), 'external');
    await browser.click(screen.getByRole('button', { name: 'Add root' }));
    const step = await screen.findByRole('group', { name: /^Import Film drive now\?/ });
    const card = step.closest('tr') as HTMLElement;
    expect((within(step).getByRole('radio', { name: /Household/ }) as HTMLInputElement).checked).toBe(true);
    expect(document.activeElement).toBe(within(step).getByRole('button', { name: 'Import now' }));
    await browser.click(within(step).getByRole('button', { name: 'Import now' }));
    expect(api.startImport).toHaveBeenCalledWith({ root_id: 'films', visibility: 'shared' });
    expect(await within(card).findByText('Import started. Its progress shows under Imports.')).toBeTruthy();
    expect(within(card).queryByRole('group', { name: /now\?/ })).toBeNull();
  });

  it('declining leaves an Import button on the root, where Private is available and failures are explained', async () => {
    const browser = setup();
    const actions = await screen.findByRole('button', { name: 'Actions for Old rips' });
    expect(screen.queryByRole('group', { name: /now\?/ })).toBeNull();
    await browser.click(actions);
    await browser.click(screen.getByRole('menuitem', { name: 'Import' }));
    const card = screen.getByRole('group', { name: /^Import Old rips now\?/ }).closest('tr') as HTMLElement;
    await browser.click(within(card).getByRole('radio', { name: /Private/ }));
    api.startImport.mockRejectedValueOnce(new Error('An import is already running for this root.'));
    await browser.click(within(card).getByRole('button', { name: 'Import now' }));
    expect(api.startImport).toHaveBeenCalledWith({ root_id: 'imports', visibility: 'private' });
    expect((await within(card).findByRole('alert')).textContent).toBe('An import is already running for this root.');
    await browser.click(within(card).getByRole('button', { name: 'Not now' }));
    expect(screen.queryByRole('group', { name: /now\?/ })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Actions for Old rips' })));
  });

  it('adding a managed root offers no import (only external roots are imported)', async () => {
    const browser = setup();
    api.createStorageRoot.mockResolvedValue(root('ssd2', 'Second SSD', 'managed'));
    await browser.type(await screen.findByLabelText('Container path'), '/media/ssd2');
    await browser.type(screen.getByLabelText('Label'), 'Second SSD');
    await browser.click(screen.getByRole('button', { name: 'Add root' }));
    await browser.click(await screen.findByRole('button', { name: 'Actions for Second SSD' }));
    expect(screen.queryByRole('group', { name: /now\?/ })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'Import' })).toBeNull();
  });
});
