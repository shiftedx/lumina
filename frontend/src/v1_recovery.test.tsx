import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listBackups: vi.fn(),
  createBackup: vi.fn(),
  verifyBackup: vi.fn(),
  deleteBackup: vi.fn(),
  updateBackupSchedule: vi.fn(),
  downloadBackup: vi.fn(),
}));
vi.mock('./api', () => api);

const { AdminBackups, BackupSchedule } = await import('./features/admin/AdminBackups');

afterEach(() => { vi.resetAllMocks(); });

const backup = {
  name: 'lumina-20260924T100000Z-manual', kind: 'manual' as const, created_at: '2026-09-24T10:00:00+00:00', app_version: '1.0.0',
  schema_version: 1, size: 4096, sha256: 'x', counts: { users: 3, library_items: 42, library_notes: 5 },
};

describe('admin backups', () => {
  it('lists, verifies, and deletes only after confirmation', async () => {
    const browser = userEvent.setup();
    api.listBackups.mockResolvedValueOnce({ backups: [backup], schedule: { daily: true, keep: 7 } }).mockResolvedValue({ backups: [], schedule: { daily: true, keep: 7 } });
    api.verifyBackup.mockResolvedValue({ ok: false, problems: ['The copy does not match its recorded checksum'] });
    api.deleteBackup.mockResolvedValue(undefined);
    render(<AdminBackups />);
    const row = await screen.findByRole('listitem', { name: /Manual backup/ });
    expect(row.textContent).toContain('3 members');
    await browser.click(within(row).getByRole('button', { name: /Verify/ }));
    expect(await within(row).findByText(/recorded checksum/)).not.toBeNull();
    await browser.click(within(row).getByRole('button', { name: /Delete backup from/ }));
    expect(api.deleteBackup).not.toHaveBeenCalled();
    await browser.click(screen.getByRole('button', { name: 'Delete backup' }));
    expect(api.deleteBackup).toHaveBeenCalledWith(backup.name);
    expect(await screen.findByText(/No backups yet/)).not.toBeNull();
  });

  it('downloads only after the admin re-enters their password', async () => {
    const browser = userEvent.setup();
    const createObjectURL = vi.fn().mockReturnValue('blob:mock');
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() });
    api.listBackups.mockResolvedValue({ backups: [backup], schedule: { daily: true, keep: 7 } });
    api.downloadBackup.mockRejectedValueOnce(new Error('Password is incorrect.')).mockResolvedValue(new Blob(['db']));
    render(<AdminBackups />);
    const row = await screen.findByRole('listitem', { name: /Manual backup/ });
    await browser.click(within(row).getByRole('button', { name: /Download backup from/ }));
    expect(api.downloadBackup).not.toHaveBeenCalled();
    await browser.type(screen.getByLabelText(/^Your password/), 'wrong');
    await browser.click(screen.getByRole('button', { name: 'Download backup' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Password is incorrect');
    expect(createObjectURL).not.toHaveBeenCalled();
    await browser.clear(screen.getByLabelText(/^Your password/));
    await browser.type(screen.getByLabelText(/^Your password/), 'right');
    await browser.click(screen.getByRole('button', { name: 'Download backup' }));
    expect(api.downloadBackup).toHaveBeenLastCalledWith(backup.name, 'right');
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(screen.queryByLabelText('Your password')).toBeNull();
    vi.unstubAllGlobals();
  });

  it('keeps a failed create actionable and saves the schedule', async () => {
    const browser = userEvent.setup();
    api.listBackups.mockResolvedValue({ backups: [], schedule: { daily: true, keep: 7 } });
    api.createBackup.mockRejectedValue(new Error('A backup is already being created'));
    api.updateBackupSchedule.mockResolvedValue({ daily: false, keep: 3 });
    render(<><AdminBackups /><BackupSchedule /></>);
    await browser.click(await screen.findByRole('button', { name: /Back up now/ }));
    expect((await screen.findByRole('alert')).textContent).toContain('already being created');
    await browser.click(screen.getByRole('switch', { name: /every day/ }));
    const keep = screen.getByRole('spinbutton', { name: /to keep/ });
    await browser.clear(keep);
    await browser.type(keep, '3');
    await browser.click(screen.getByRole('button', { name: 'Save schedule' }));
    expect(api.updateBackupSchedule).toHaveBeenCalledWith({ daily: false, keep: 3 });
  });
});
