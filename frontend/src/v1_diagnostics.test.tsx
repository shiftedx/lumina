import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AdminDiagnostics as Report } from './api';

const api = vi.hoisted(() => ({ getAdminDiagnostics: vi.fn() }));
vi.mock('./api', () => api);

const { AdminDiagnostics } = await import('./features/admin/AdminDiagnostics');

afterEach(() => { vi.resetAllMocks(); });

const report: Report = {
  generated_at: '2026-09-24T10:00:00Z',
  status: 'degraded',
  versions: { lumina: '1.0.0', python: '3.11.9', yt_dlp: '2026.09.01', ffmpeg: '7.1', node: null },
  runtime: { ffmpeg_available: true, js_runtime_available: false, yt_dlp_ejs_available: true },
  storage_roots: [{ label: 'Family NAS', mode: 'external', enabled: true, state: 'offline', checked_at: null }],
  queue: { jobs_by_status: { running: 1, queued: 4 }, concurrency: 2, event_streams: 3 },
  maintenance_sweeps: { consecutive_failures: 2, last_error: "OperationalError('disk I/O error')", last_success_at: null, last_failure_at: '2026-09-24T09:59:00Z' },
  persistence: {},
  recent_errors: [{ source: 'download', at: '2026-09-24T09:00:00Z', message: 'HTTP Error 403 at [path] token=[redacted]' }],
  ai: { enabled: true, ok: false, model_available: false, error: 'Local AI endpoint is unreachable', asr_configured: false },
};

describe('admin diagnostics', () => {
  it('shows the redacted report and copies it to the clipboard', async () => {
    const browser = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    api.getAdminDiagnostics.mockResolvedValue(report);
    render(<AdminDiagnostics />);
    expect(await screen.findByText('Degraded')).not.toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('disk I/O error');
    expect(screen.getByText('HTTP Error 403 at [path] token=[redacted]')).not.toBeNull();
    expect(screen.getByText('Local AI endpoint is unreachable')).not.toBeNull();
    // userEvent.setup() installs its own clipboard; replace it after setup.
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    await browser.click(screen.getByRole('button', { name: /Copy report/ }));
    expect(JSON.parse(writeText.mock.calls[0][0] as string)).toEqual(report);
    expect(await screen.findByText(/Report copied/)).not.toBeNull();
  });

  it('keeps a load error actionable', async () => {
    api.getAdminDiagnostics.mockRejectedValue(new Error('Admin access required'));
    render(<AdminDiagnostics />);
    expect((await screen.findByRole('alert')).textContent).toContain('Admin access required');
    expect((screen.getByRole('button', { name: /Copy report/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('reads as tables and status words, with logs and code in the mono style', async () => {
    api.getAdminDiagnostics.mockResolvedValue(report);
    render(<AdminDiagnostics />);
    await screen.findByText('Degraded');
    expect(document.querySelector('table.g-table')).not.toBeNull();
    expect(document.querySelector('.admin-counts, .admin-metrics, .admin-code')).toBeNull();
    expect(document.querySelector('pre.g-log')).not.toBeNull();
    expect(screen.getByText('Degraded').closest('.g-status')?.querySelector('svg')).not.toBeNull();
  });
});
