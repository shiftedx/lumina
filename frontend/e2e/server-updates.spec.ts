import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`server release checks retain cached updates during an outage at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mockApi(page, {});
    let checks = 0;
    let state = {
      current_version: '0.3.4', latest_release: null as null | { version: string; url: string; published_at: string },
      status: 'not_checked', checked_at: null as string | null, error: null as string | null,
      checking: false, automatic_checks: true, email_notifications: false, email_configured: true,
    };
    await page.route('**/api/admin/updates', async (route) => {
      if (route.request().method() === 'PATCH') state = { ...state, ...route.request().postDataJSON() };
      await route.fulfill({ json: state });
    });
    await page.route('**/api/admin/updates/check', async (route) => {
      checks += 1;
      const pending = { ...state, checking: true };
      state = checks === 1 ? {
        ...state, status: 'update_available', checked_at: '2026-10-18T12:00:00Z',
        latest_release: { version: '0.4.0', url: 'https://github.com/shiftedx/lumina/releases/tag/v0.4.0', published_at: '2026-10-18T10:00:00Z' },
      } : { ...state, error: 'Unable to check GitHub for updates.' };
      await route.fulfill({ status: 202, json: pending });
    });
    await page.goto('/manage/diagnostics?page=updates');
    await signIn(page);
    await expect(page.getByText('Lumina has not checked for updates yet.')).toBeVisible();
    await page.getByRole('button', { name: 'Check for updates', exact: true }).click();
    await expect(page.getByRole('link', { name: 'Read 0.4.0 release notes' })).toHaveAttribute('href', 'https://github.com/shiftedx/lumina/releases/tag/v0.4.0');
    await expect(page.getByText('Lumina 0.4.0 is available.', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Check for updates', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('Unable to check GitHub for updates.');
    await expect(page.getByRole('link', { name: 'Read 0.4.0 release notes' })).toBeVisible();
    await expect(page.getByText('Lumina 0.4.0 is available.', { exact: true })).toBeVisible();
    await page.getByRole('switch', { name: 'Check automatically each day' }).click();
    await expect(page.getByRole('switch', { name: 'Check automatically each day' })).not.toBeChecked();
    expect(state.automatic_checks).toBe(false);
    await expect(page.getByRole('link', { name: 'Open upgrade guide' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath('updates.png'), fullPage: true });
  });
}

test('admin overview update notification opens the update controls', async ({ page }) => {
  await mockApi(page, {});
  const updates = {
    current_version: '0.3.4', latest_release: { version: '0.4.0', url: 'https://github.com/shiftedx/lumina/releases/tag/v0.4.0', published_at: '2026-10-18T10:00:00Z' },
    status: 'update_available', checked_at: '2026-10-18T12:00:00Z', error: null, checking: false,
    automatic_checks: true, email_notifications: false, email_configured: false,
  };
  await page.route('**/api/admin/updates', (route) => route.fulfill({ json: updates }));
  await page.route('**/api/admin/overview', (route) => route.fulfill({ json: {
    generated_at: '2026-10-18T12:00:00Z', jobs_by_status: {}, recent_failures: [], failure_window_days: 7,
    concurrency: 1, max_active_jobs_per_user: 25, min_free_disk_mb: 2048, library_free_bytes: null,
    library_items_by_status: {}, roots: [], event_streams: 0, persistence: {}, updates,
  } }));
  await page.goto('/manage');
  await signIn(page);
  await expect(page.getByText('Lumina 0.4.0 is available.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Review update', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Check for updates', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Read 0.4.0 release notes' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Check for updates', exact: true })).toBeVisible();
});
