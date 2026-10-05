import { render, screen, within } from '@testing-library/react';
import axe from 'axe-core';
import { describe, expect, it, vi } from 'vitest';

import type { AdminDiagnostics as Report } from '../../api';
import type { LibraryAutomationDiagnostics } from '../../types';

import { recoDiagnostics, recoSurfaceStats } from '../../test/recoFixtures';
import { LibraryAutomationPanel } from './LibraryAutomationPanel';

const api = vi.hoisted(() => ({ getAdminDiagnostics: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { AdminDiagnostics, RecommendationsPanel } = await import('./AdminDiagnostics');

describe('Diagnostics: Recommendations', () => {
  it('shows the north star, one row per surface, and the pool health', () => {
    render(<RecommendationsPanel reco={recoDiagnostics()} />);
    expect(screen.getByRole('region', { name: 'Recommendations' })).toBeTruthy();
    expect(screen.getByText('Plays that started from a recommendation').nextElementSibling?.textContent).toBe('38.0 %');
    expect(screen.getByText(/Target 40 % by day 42/)).toBeTruthy();
    const row = screen.getByRole('row', { name: /Home · Picked for you/ });
    expect(row.textContent).toContain('1,240');
    expect(row.textContent).toContain('6.5 % click rate');
    expect(row.textContent).toContain('71 %');
    expect(row.textContent).toContain('3.1 % · 5.5 %');
    expect(screen.getByText('Members with a pool').nextElementSibling?.textContent).toBe('3');
    expect(screen.getByText('Remote vectors ready').nextElementSibling?.textContent).toBe('93.0 %');
    expect(screen.getByText('Oldest refresh').nextElementSibling?.textContent).toBe('3 h ago');
  });

  it('blanks a rate that has no data as a dash, never as zero', () => {
    const blank = recoSurfaceStats({ surface: 'up_next', impressions: 12, opens: 3, ctr: null, plays: 1, play_through_median: null, completion_rate: null, negative_rate: null, explore_play_rate: null, exploit_play_rate: null });
    render(<RecommendationsPanel reco={recoDiagnostics({ surfaces: [blank], reco_share_of_remote_plays: null })} />);
    const cells = within(screen.getByRole('row', { name: /Up next/ })).getAllByRole('cell').map((cell) => cell.textContent);
    expect(cells).toEqual(['12', '3', '1', '—', '—', '—', '— · —']);
    expect(screen.getByText('Plays that started from a recommendation').nextElementSibling?.textContent).toBe('—');
  });

  it('says so when the switch is off', () => {
    render(<RecommendationsPanel reco={recoDiagnostics({ enabled: false })} />);
    expect(screen.getByRole('status').textContent).toContain('switched off');
  });

  it('renders without data when the server sent none', () => {
    render(<RecommendationsPanel reco={null} />);
    expect(screen.getByText(/No recommendation figures yet/)).toBeTruthy();
  });

  it('sits on the primitives: tables and stat figures, no legacy panel classes', () => {
    render(<RecommendationsPanel reco={recoDiagnostics()} />);
    const panel = screen.getByRole('region', { name: 'Recommendations' });
    expect(panel.querySelector('.admin-panel, .admin-counts, .admin-table')).toBeNull();
    expect(panel.querySelector('.g-stat-strip .g-stat-figure')?.textContent).toBe('38.0 %');
    expect(within(panel).getByRole('table', { name: 'Recommendations by surface' }).classList.contains('g-table')).toBe(true);
    expect(within(panel).getByRole('heading', { name: 'Recommendations' }).className).toContain('g-panel-title');
  });

  it('omits the Explore · Popular row while it has no data, and the switch-off note does not claim events are recorded', () => {
    const empty = recoSurfaceStats({ surface: 'explore_popular', impressions: 0, opens: 0, ctr: null, plays: 0 });
    render(<RecommendationsPanel reco={recoDiagnostics({ surfaces: [empty], enabled: false })} />);
    expect(screen.queryByRole('row', { name: /Explore · Popular/ })).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('switched off');
    expect(screen.getByRole('status').textContent).not.toContain('still recorded');
  });

  it('takes the provider-call budget from one named constant', () => {
    render(<RecommendationsPanel reco={recoDiagnostics()} />);
    expect(screen.getByText('Provider calls, 24 h').nextElementSibling?.textContent).toMatch(/ of 300$/);
  });
});

const automation = (over: Partial<LibraryAutomationDiagnostics> = {}): LibraryAutomationDiagnostics => ({
  poller: { heartbeat_at: '2026-10-02T10:00:00Z', stalled: false, last_error: null },
  driver: { active_run_id: null, queued: 2 },
  runs_24h: { manual: 1, scheduled: 4, watch: 9, needs_confirmation: 0, failed: 0 },
  roots: [{
    label: 'Family NAS', schedule: 'nightly', watch: true, state: 'idle', dirs_watched: 19455, pending_files: 3, last_pass_at: '2026-10-02T09:00:00Z',
    last_pass_seconds: 61.2, stats_last_pass: 100, watch_errors_last_pass: 2, last_change_at: null, last_dispatch_at: null, last_skip: { at: '2026-10-02T08:00:00Z', reason: 'scanning' }, next_scan_at: null,
  }],
  ...over,
});
const report = (library_automation?: LibraryAutomationDiagnostics | null): Report => ({
  generated_at: '2026-10-02T10:00:00Z', status: 'ok', versions: {}, runtime: {}, storage_roots: [], queue: { jobs_by_status: {}, concurrency: 2, event_streams: 0 },
  maintenance_sweeps: { consecutive_failures: 0, last_error: null, last_success_at: null, last_failure_at: null }, persistence: {}, recent_errors: [],
  ai: { enabled: false, asr_configured: false }, library_automation,
});

describe('Diagnostics: Library automation', () => {
  it('shows Library automation Healthy with the 24 h scan count', async () => {
    api.getAdminDiagnostics.mockResolvedValue(report(automation()));
    render(<AdminDiagnostics />);
    const label = await screen.findByText('Library automation', { selector: 'dt' });
    expect(label.nextElementSibling?.textContent).toBe('Healthy');
    expect(label.nextElementSibling?.nextElementSibling?.textContent).toBe('14 scans in 24 h');
  });

  it('shows Stalled in the danger tone when the poller is stalled', async () => {
    api.getAdminDiagnostics.mockResolvedValue(report(automation({ poller: { heartbeat_at: null, stalled: true, last_error: null } })));
    render(<AdminDiagnostics />);
    const label = await screen.findByText('Library automation', { selector: 'dt' });
    expect(label.nextElementSibling?.textContent).toBe('Stalled');
    expect(label.nextElementSibling?.querySelector('.g-status')?.className).toContain('danger');
  });

  it('shows Not reported when the report has no library_automation', async () => {
    api.getAdminDiagnostics.mockResolvedValue(report());
    render(<AdminDiagnostics />);
    const label = await screen.findByText('Library automation', { selector: 'dt' });
    expect(label.nextElementSibling?.textContent).toBe('Not reported');
  });

  it('renders a per-root row with watched folders, last pass seconds and next scan', () => {
    render(<LibraryAutomationPanel data={automation()} />);
    const cells = within(screen.getByRole('row', { name: /Family NAS/ })).getAllByRole('cell').map((cell) => cell.textContent ?? '');
    expect(cells.slice(0, 5)).toEqual(['Nightly', 'On', 'Idle' + ' · Skipped: scanning', '19455', '3']);
    expect(cells[5]).toContain('61.2 s');
    expect(cells[5]).toContain('2 errors');
    expect(cells[7]).toBe('—');
    expect(screen.getByText(/Manual 1 · Scheduled 4 · Watched 9 · Needs confirmation 0 · Failed 0/)).toBeTruthy();
    expect(screen.getByText('Running 0 · queued 2')).toBeTruthy();
  });

  it('shows dashes for null times and renders the server error exactly as given', () => {
    render(<LibraryAutomationPanel data={automation({ poller: { heartbeat_at: null, stalled: false, last_error: 'Cannot read /mnt/nas/x' } })} />);
    expect(screen.getByRole('alert').textContent).toBe('Folder watching error: Cannot read /mnt/nas/x');
    expect(within(screen.getByRole('row', { name: /Family NAS/ })).getAllByRole('cell')[7].textContent).toBe('—');
    expect(within(screen.getByRole('row', { name: /Family NAS/ })).getAllByRole('cell')[6].textContent).toBe('—');
  });

  it('shows the empty note when the report has no library_automation', () => {
    render(<LibraryAutomationPanel data={null} />);
    expect(screen.getByText(/No library automation figures yet/)).toBeTruthy();
  });

  it('has no ARIA, name or table-header violations', async () => {
    const { container } = render(<LibraryAutomationPanel data={automation()} />);
    const results = await axe.run(container, { runOnly: ['aria-allowed-attr', 'scope-attr-valid', 'td-has-header', 'th-has-data-cells', 'heading-order'] });
    expect(results.violations).toEqual([]);
  });
});
