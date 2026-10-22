import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** A completed download opens its registered library item. */

const at = '2026-07-20T10:00:00Z';
const jobs = [
  { id: 'job-done', source_url: 'https://example.test/done', status: 'completed', title: 'Evening concert recording', outputs: [{ library_item_id: item.id, root_label: 'Archive disk', folder: 'Video UHD' }], created_at: at, finished_at: at },
];

async function open(page: Page, scheme: 'light' | 'dark', viewport: { width: number; height: number }) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  const json = (body: unknown) => ({ contentType: 'application/json', body: JSON.stringify(body) });
  await page.route(/\/api\/jobs(\?|$)/, (route) => route.fulfill(json({ items: jobs, next_cursor: null })));
  await page.route(/\/api\/live-recordings(\?|$)/, (route) => route.fulfill(json({ items: [], next_cursor: null })));
  await page.route(/\/api\/acquisition-batches(\?|$)/, (route) => route.fulfill(json([])));
  await page.goto('/downloads');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Downloads' })).toBeVisible();
}

test('test_completed_download_opens_watch_route', async ({ page }) => {
  await open(page, 'light', { width: 1536, height: 960 });
  const done = page.locator('.g-job').filter({ hasText: 'Evening concert recording' });
  await done.getByRole('button', { name: 'Open' }).click();
  await expect(page).toHaveURL(/\/watch\/library\/library-1/);
});
