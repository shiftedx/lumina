import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** Admin overview with truthful aggregates. */

const overview = {
  generated_at: '2026-09-24T10:00:00Z',
  jobs_by_status: { completed: 184, queued: 3, running: 2, failed: 6 },
  recent_failures: [
    { reason: 'ERROR: [youtube] Sign in to confirm your age. This video may be inappropriate for some users.', count: 4, last_at: '2026-09-23T21:14:00Z' },
    { reason: 'HTTP Error 403: Forbidden', count: 2, last_at: '2026-09-22T08:02:00Z' },
  ],
  failure_window_days: 7,
  concurrency: 2,
  max_active_jobs_per_user: 25,
  min_free_disk_mb: 2048,
  library_free_bytes: 812 * 1024 ** 3,
  library_items_by_status: { available: 1268, missing: 4 },
  roots: [
    { id: 'a', label: 'Main library', mode: 'managed', enabled: true, state: 'available', checked_at: '2026-09-24T09:40:00Z', free_bytes: 812 * 1024 ** 3, total_bytes: 3.6 * 1024 ** 4, minimum_free_bytes: 0, artifact_count: 1190, artifact_bytes: 1.9 * 1024 ** 4 },
    { id: 'b', label: 'Family NAS — Jellyfin movies and home videos archive', mode: 'external', enabled: true, state: 'offline', checked_at: '2026-09-24T09:40:00Z', free_bytes: null, total_bytes: null, minimum_free_bytes: 0, artifact_count: 82, artifact_bytes: 402 * 1024 ** 3 },
  ],
  event_streams: 3,
  persistence: { job_update: { count: 5120, errors: 0, busy_timeouts: 1, total_ms: 9000, max_wait_ms: 38.2 } },
};

async function mockAdmin(page: Page, overrides: Record<string, unknown> = {}) {
  await page.route('**/api/admin/overview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...overview, ...overrides }) }));
  await page.route('**/api/admin/settings', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ concurrency: 2, max_active_jobs_per_user: 25, min_free_disk_mb: 2048 }) }));
}

test('test_admin_overview', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false, settings_advanced: true }); // the download limits are advanced rows
  await mockAdmin(page);
  await page.goto('/admin');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Dashboard', exact: true })).toBeVisible();
  await expect(page.getByText('1272', { exact: true })).toBeVisible();
  await expect(page.getByRole('row', { name: /Family NAS/ }).getByText('Offline')).toBeVisible();
  await expect(page.getByRole('spinbutton', { name: /Simultaneous downloads/ })).toHaveValue('2');
  expect(errors).toEqual([]);
});
