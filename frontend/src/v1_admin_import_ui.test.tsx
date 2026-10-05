import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listStorageRoots: vi.fn(), probeStorageRoot: vi.fn(), listImports: vi.fn(), getImport: vi.fn(), startImport: vi.fn(),
  listImportEntries: vi.fn(), cancelImport: vi.fn(), resumeImport: vi.fn(), confirmImport: vi.fn(),
}));
vi.mock('./api', () => api);

const { AdminImports } = await import('./features/admin/AdminImports');

afterEach(() => { vi.resetAllMocks(); vi.useRealTimers(); });

const root = (id: string, label: string, state: string, mode = 'external') => ({
  id, label, path: `/media/${id}`, mode, enabled: true, minimum_free_bytes: 0, artifact_count: 0, observation: { state },
});
const run = (patch = {}) => ({
  id: 'run-1', root_id: 'rips', state: 'succeeded', visibility: 'private', counters: { inspected: 12, indexed: 9 }, coverage: 'complete', error: null,
  created_at: '2026-09-24T10:00:00Z', finished_at: '2026-09-24T10:05:00Z', ...patch,
});

function setup(runs: unknown[] = []) {
  api.listStorageRoots.mockResolvedValue([root('fast', 'Fast SSD', 'available', 'managed'), root('rips', 'DVD rips', 'available'), root('nas', 'Old NAS', 'offline')]);
  api.listImports.mockResolvedValue(runs);
  api.listImportEntries.mockResolvedValue([]);
  render(<AdminImports />);
  return userEvent.setup();
}

describe('admin import UI', () => {
  it('test_import_wizard_confirmation: only reachable external roots, explicit read-only + visibility before admission', async () => {
    const browser = setup();
    const offline = await screen.findByRole('radio', { name: /Old NAS/ });
    expect((offline as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByRole('radio', { name: /Fast SSD/ })).toBeNull();
    expect((screen.getByRole('radio', { name: /DVD rips/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('radio', { name: /Household/ }) as HTMLInputElement).checked).toBe(true);
    await browser.click(screen.getByRole('radio', { name: /Private/ }));
    expect(screen.getByText(/will read/).textContent).toMatch(/DVD rips.*read-only.*private/);
    api.startImport.mockResolvedValue(run({ state: 'running', counters: {}, visibility: 'private' }));
    api.getImport.mockResolvedValue(run({ visibility: 'private' }));
    await browser.click(screen.getByRole('button', { name: 'Start import' }));
    expect(api.startImport).toHaveBeenCalledWith({ root_id: 'rips', visibility: 'private' });
    expect(await screen.findByText(/total is not known/)).not.toBeNull();
    expect(await screen.findByText(/Every file in the folder was checked/, {}, { timeout: 3000 })).not.toBeNull();
    expect(api.getImport).toHaveBeenCalledWith('run-1');
    expect(screen.getByRole('link', { name: 'View in Library' }).getAttribute('href')).toBe('/library');
  });

  it('imports for the household unless you choose Private', async () => {
    const browser = setup();
    api.startImport.mockResolvedValue(run({ state: 'running', counters: {}, visibility: 'shared' }));
    api.getImport.mockResolvedValue(run({ visibility: 'shared' }));
    await browser.click(await screen.findByRole('button', { name: 'Start import' }));
    expect(api.startImport).toHaveBeenCalledWith({ root_id: 'rips', visibility: 'shared' });
    const radios = screen.getAllByRole('radio', { name: /Household|Private/ }).map((radio) => radio.closest('label')?.textContent?.split(' — ')[0]);
    expect(radios).toEqual(['Household', 'Private']);
  });

  it('test_import_partial_results_actionable: failed/skipped entries with reasons, filter, rescan', async () => {
    const partial = run({ state: 'partial', coverage: 'incomplete', counters: { inspected: 5, indexed: 3, failed: 1, skipped: 1 } });
    const browser = setup([partial]);
    api.listImportEntries.mockResolvedValue([
      { relative_path: 'Films/locked.mkv', outcome: 'failed', error: 'unreadable', library_item_id: null },
      { relative_path: 'Films/link.mkv', outcome: 'skipped', error: 'symlink', library_item_id: null },
    ]);
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Films/locked.mkv')).not.toBeNull();
    expect(within(table).getByText(/never followed/)).not.toBeNull();
    expect(screen.getByText(/Partial\. Some files could not be checked/)).not.toBeNull();
    await browser.selectOptions(screen.getByLabelText('Show'), 'failed');
    await waitFor(() => expect(api.listImportEntries).toHaveBeenLastCalledWith('run-1', 'failed'));
    api.startImport.mockResolvedValue(run({ id: 'run-2', state: 'running', counters: {} }));
    api.getImport.mockResolvedValue(run({ id: 'run-2' }));
    await browser.click(screen.getByRole('button', { name: 'Rescan' }));
    expect(api.startImport).toHaveBeenCalledWith({ root_id: 'rips', visibility: 'private' });
  });

  it('test_import_offline_recovery: an offline failure is explained and resume continues the same run', async () => {
    const browser = setup([run({ state: 'failed', coverage: 'incomplete', error: 'offline', counters: { inspected: 40, indexed: 40 } })]);
    expect(await screen.findByText(/not mounted\. Nothing was marked missing/)).not.toBeNull();
    api.resumeImport.mockResolvedValue(run({ state: 'running', counters: { inspected: 40, indexed: 40 } }));
    api.getImport.mockResolvedValue(run({ counters: { inspected: 90, indexed: 50, unchanged: 40 } }));
    await browser.click(screen.getByRole('button', { name: 'Resume' }));
    expect(api.resumeImport).toHaveBeenCalledWith('run-1');
    await screen.findByText(/Every file in the folder was checked/, {}, { timeout: 3000 });
    expect(api.startImport).not.toHaveBeenCalled();
    expect(screen.getByText('90')).not.toBeNull();
  });

  it('marking files missing asks first and does nothing on cancel', async () => {
    const pending = run({ state: 'needs_confirmation', counters: { inspected: 40, missing_candidates: 37, available: 40 } });
    api.confirmImport.mockResolvedValue(run({ state: 'running' }));
    api.getImport.mockResolvedValue(run({ state: 'succeeded' }));
    const browser = setup([pending]);
    expect(await screen.findByText('37 of 40 files would be marked missing. If a drive is unmounted, remount it. Otherwise confirm.')).toBeTruthy();
    expect(screen.getByText('Needs your confirmation')).toBeTruthy();
    const ask = await screen.findByRole('button', { name: 'Confirm missing files' });
    await browser.click(ask);
    const dialog = screen.getByRole('dialog', { name: 'Mark 37 files as missing?' });
    expect(within(dialog).getByText('They come back if a later scan finds them again.')).toBeTruthy();
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }));
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(api.confirmImport).not.toHaveBeenCalled();
    await waitFor(() => expect(document.activeElement).toBe(ask));
    await browser.click(ask);
    await browser.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Mark as missing' }));
    await waitFor(() => expect(api.confirmImport).toHaveBeenCalledWith('run-1'));
  });

  it('rescans from a run that needs confirmation', async () => {
    api.startImport.mockResolvedValue(run({ state: 'running', counters: {} }));
    const browser = setup([run({ state: 'needs_confirmation', counters: { missing_candidates: 3, available: 4 } })]);
    await browser.click(await screen.findByRole('button', { name: 'Rescan' }));
    await waitFor(() => expect(api.startImport).toHaveBeenCalledWith({ root_id: 'rips', visibility: 'private' }));
  });
});
