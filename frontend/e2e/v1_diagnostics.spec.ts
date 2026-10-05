import { expect, test } from '@playwright/test';
import { recoDiagnostics, recoSurfaceStats } from '../src/test/recoFixtures';
import { mockApi, signIn } from './lumina-mock';

/** Admin diagnostics: redacted report with copy-to-clipboard. */

const report = {
  generated_at: '2026-09-24T10:00:00Z',
  status: 'degraded',
  versions: { lumina: '1.0.0', python: '3.11.9', yt_dlp: '2026.09.01', ffmpeg: '7.1', node: 'v22.9.0' },
  runtime: { ffmpeg_available: true, js_runtime_available: true, yt_dlp_ejs_available: true },
  storage_roots: [
    { label: 'Main library', mode: 'managed', enabled: true, state: 'available', checked_at: '2026-09-24T09:40:00Z' },
    { label: 'Family NAS — Jellyfin movies and home videos archive', mode: 'external', enabled: true, state: 'offline', checked_at: '2026-09-24T09:40:00Z' },
  ],
  queue: { jobs_by_status: { running: 1, queued: 4, completed: 180 }, concurrency: 2, event_streams: 3 },
  maintenance_sweeps: { consecutive_failures: 0, last_error: null, last_success_at: '2026-09-24T09:59:00Z', last_failure_at: null },
  persistence: {},
  recent_errors: [
    { source: 'download', at: '2026-09-24T09:00:00Z', message: 'ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.' },
    { source: 'import', at: '2026-09-23T21:14:00Z', message: 'Permission denied: [path]' },
    { source: 'summary', at: '2026-09-22T08:02:00Z', message: 'Local AI endpoint timed out' },
  ],
  ai: { enabled: true, ok: true, model_available: true, error: null, asr_configured: false },
};

test('test_admin_diagnostics', async ({ page, context }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.route('**/api/admin/diagnostics', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(report) }));
  await page.goto('/admin/diagnostics');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Diagnostics' })).toBeVisible();
  await expect(page.getByText('Degraded', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Copy report/ }).click();
  await expect(page.getByText(/Report copied/)).toBeVisible();
  expect(JSON.parse(await page.evaluate(() => navigator.clipboard.readText()))).toEqual(report);
  expect(errors).toEqual([]);
});

test('test_admin_diagnostics_recommendations', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  const quiet = recoSurfaceStats({ surface: 'up_next', impressions: 12, opens: 3, ctr: null, plays: 1, play_through_median: null, completion_rate: null, negative_rate: null, explore_play_rate: null, exploit_play_rate: null });
  const withReco = { ...report, recommendations: recoDiagnostics({ surfaces: [recoSurfaceStats(), quiet] }) };
  await page.route('**/api/admin/diagnostics', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(withReco) }));
  await page.goto('/admin/diagnostics');
  await signIn(page);
  const panel = page.getByRole('region', { name: 'Recommendations', exact: true });
  await expect(panel.getByRole('heading', { level: 3, name: 'Recommendations' })).toBeVisible();
  await expect(panel.getByText('38.0 %').first()).toBeVisible();
  await expect(panel.getByRole('row', { name: /Home · Picked for you/ })).toContainText('6.5 % click rate');
  await expect(panel.getByRole('row', { name: /Up next/ })).toContainText('—');
  await expect(panel.getByText('Members with a pool')).toBeVisible();
  expect(errors).toEqual([]);
});
