import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AdminOverview as Overview } from './types';

const api = vi.hoisted(() => ({ getAdminOverview: vi.fn(), getAdminSettings: vi.fn(), updateAdminSettings: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));

const { AdminOverview } = await import('./features/admin/AdminOverview');
const { sectionById } = await import('./features/settings/registry');
const { SectionRows } = await import('./features/settings/SettingRow');
const { SettingsHostContext } = await import('./features/settings/settingsHost');
const admin = { id: 'u1', username: 'dana', role: 'admin', is_active: true } as never;

afterEach(() => { vi.resetAllMocks(); });

const overview: Overview = {
  generated_at: '2026-09-24T10:00:00Z',
  jobs_by_status: { completed: 7, queued: 2, running: 1, failed: 1 },
  recent_failures: [{ reason: 'HTTP Error 403: Forbidden', count: 3, last_at: '2026-09-23T10:00:00Z' }],
  failure_window_days: 7,
  concurrency: 2,
  max_active_jobs_per_user: 25,
  min_free_disk_mb: 2048,
  library_free_bytes: null,
  library_items_by_status: { available: 40, missing: 2 },
  roots: [
    { id: 'a', label: 'Media', mode: 'managed', enabled: true, state: 'available', checked_at: '2026-09-24T09:00:00Z', free_bytes: 1024 ** 3, total_bytes: 4 * 1024 ** 3, minimum_free_bytes: 0, artifact_count: 12, artifact_bytes: 5 * 1024 ** 2 },
    { id: 'b', label: 'NAS', mode: 'external', enabled: true, state: null, checked_at: null, free_bytes: null, total_bytes: null, minimum_free_bytes: 0, artifact_count: 0, artifact_bytes: 0 },
  ],
  event_streams: 3,
  persistence: { job_update: { count: 10, errors: 1, busy_timeouts: 0, total_ms: 20, max_wait_ms: 4.4 } },
};
const settings = { concurrency: 2, max_active_jobs_per_user: 25, min_free_disk_mb: 2048 };

describe('admin overview', () => {
  it('shows exact aggregates and labels unavailable sensors instead of zero', async () => {
    api.getAdminOverview.mockResolvedValue(overview);
    api.getAdminSettings.mockResolvedValue(settings);
    render(<AdminOverview />);
    const metrics = await screen.findByText('Library items');
    expect(metrics.parentElement?.textContent).toContain('42');
    expect(screen.getByText('Active downloads').parentElement?.textContent).toContain('3');
    expect(screen.getByText('Live event streams').parentElement?.textContent).toContain('3');
    expect(screen.getByText('Library volume free space:', { exact: false }).textContent).toContain('unavailable');
    const nas = screen.getByRole('row', { name: /NAS/ });
    expect(within(nas).getByText('Not probed')).not.toBeNull();
    expect(within(nas).getByText('Unavailable')).not.toBeNull();
    expect(screen.getByText('HTTP Error 403: Forbidden')).not.toBeNull();
  });

  it('Overview is a stat strip of tabular figures with labels, and states are words with an icon', async () => {
    api.getAdminOverview.mockResolvedValue(overview);
    api.getAdminSettings.mockResolvedValue(settings);
    render(<AdminOverview />);
    await screen.findByText('Library items');
    const figures = document.querySelectorAll('.g-stat-strip .g-stat-figure');
    expect(figures.length).toBeGreaterThanOrEqual(3);
    expect(figures.length).toBeLessThanOrEqual(5);
    for (const figure of figures) expect(figure.previousElementSibling?.classList.contains('g-label')).toBe(true);
    expect(screen.getByText('Library items').parentElement?.textContent).toContain('42');
    for (const status of document.querySelectorAll('.g-status')) expect(status.querySelector('svg')).not.toBeNull();
    expect(document.querySelector('.admin-state, .admin-metrics')).toBeNull();
  });

  it('keeps the error actionable and retries on refresh', async () => {
    const browser = userEvent.setup();
    api.getAdminOverview.mockRejectedValueOnce(new Error('Admin access required')).mockResolvedValueOnce(overview);
    api.getAdminSettings.mockResolvedValue(settings);
    render(<AdminOverview />);
    expect((await screen.findByRole('alert')).textContent).toContain('Admin access required');
    await browser.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Library items')).not.toBeNull();
  });

  it('saves download limits from their own rows, keeps entered values on failure and refreshes the overview', async () => {
    const browser = userEvent.setup();
    api.getAdminOverview.mockResolvedValue(overview);
    api.getAdminSettings.mockResolvedValue(settings);
    api.updateAdminSettings.mockRejectedValueOnce(new Error('Input should be less than or equal to 8')).mockResolvedValueOnce({ ...settings, concurrency: 4 });
    render(<SettingsHostContext.Provider value={{ user: admin }}><SectionRows section={sectionById('overview')!} /></SettingsHostContext.Provider>);
    const concurrency = await screen.findByRole('spinbutton', { name: 'Simultaneous downloads' });
    const save = screen.getByRole('button', { name: 'Save download limits' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'About Downloads at once' })).toBeTruthy();
    await browser.clear(concurrency);
    await browser.type(concurrency, '4');
    await browser.click(save);
    expect((await screen.findByText('Input should be less than or equal to 8')).closest('[role="alert"]')).not.toBeNull();
    expect((concurrency as HTMLInputElement).value).toBe('4');
    await browser.click(save);
    expect(await screen.findByText(/^Saved\./)).not.toBeNull();
    expect(api.updateAdminSettings).toHaveBeenLastCalledWith({ concurrency: 4, max_active_jobs_per_user: 25, min_free_disk_mb: 2048 });
    expect(api.getAdminOverview).toHaveBeenCalledTimes(2);
  });

  it('saves extra source ports only when changed, and refuses text that is not port numbers before sending', async () => {
    const browser = userEvent.setup();
    api.getAdminOverview.mockResolvedValue(overview);
    api.getAdminSettings.mockResolvedValue({ ...settings, extra_source_ports: [8000] });
    api.updateAdminSettings.mockResolvedValue({ ...settings, extra_source_ports: [8000, 8080] });
    render(<SettingsHostContext.Provider value={{ user: admin }}><SectionRows section={sectionById('overview')!} /></SettingsHostContext.Provider>);
    const ports = await screen.findByRole('textbox', { name: 'Extra allowed source ports' }) as HTMLInputElement;
    expect(ports.value).toBe('8000');
    const save = screen.getByRole('button', { name: 'Save download limits' });
    await browser.type(ports, ', icecast');
    await browser.click(save);
    expect(await screen.findByText(/Enter port numbers separated by commas/)).not.toBeNull();
    expect(api.updateAdminSettings).not.toHaveBeenCalled();
    await browser.clear(ports);
    await browser.type(ports, '8080 8000');
    await browser.click(save);
    expect(await screen.findByText(/^Saved\./)).not.toBeNull();
    expect(api.updateAdminSettings).toHaveBeenLastCalledWith({ concurrency: 2, max_active_jobs_per_user: 25, min_free_disk_mb: 2048, extra_source_ports: [8080, 8000] });
    expect(ports.value).toBe('8000, 8080');
  });
});
