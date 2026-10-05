import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** Downloads: running/attention/finished jobs, destinations, attempts, live recordings, batch saves. */

const at = '2026-07-20T10:00:00Z';
const jobs = [
  { id: 'job-run', source_url: 'https://example.test/run', status: 'running', title: 'A long documentary about river deltas and the people who map them every single season', progress: 42, downloaded_bytes: 420 * 1024 ** 2, speed: 6 * 1024 ** 2, eta: 95, created_at: at },
  { id: 'job-wait', source_url: 'https://example.test/wait', status: 'queued', title: 'Waiting for a free slot', created_at: at },
  { id: 'job-fail', source_url: 'https://example.test/fail', status: 'failed', title: 'A video that went private', error: 'This video is private. Lumina only saves public media.', attempts: [{ status: 'interrupted', error: 'Lumina restarted before this finished.', finished_at: '2026-07-20T09:00:00Z' }], created_at: at },
  { id: 'job-done', source_url: 'https://example.test/done', status: 'completed', title: 'Evening concert recording', outputs: [{ library_item_id: item.id, root_label: 'Archive disk', folder: 'Video UHD' }], created_at: at, finished_at: at },
];
const recordings = [
  { id: 'rec-live', source_url: 'https://example.test/live', title: 'Community build night', status: 'live', stop_requested: false, cancel_requested: false, media: { status: 'recording' }, chat: { status: 'capturing' }, max_runtime_seconds: 21600, max_bytes: 8 * 1024 ** 3, created_at: at },
  { id: 'rec-part', source_url: 'https://example.test/part', title: 'Marathon finish line', status: 'partial', stop_requested: false, cancel_requested: false, media: { status: 'partial', library_item_id: item.id, end_reason: 'disk_low' }, chat: { status: 'completed' }, kept: true, created_at: at },
];
const batch = {
  id: 'batch-1', source_url: 'https://example.test/list', source_title: 'Weekend cooking playlist', source_provenance: {}, status: 'partial', selected_count: 3, queued_count: 0, duplicate_count: 0, completed_count: 2, failed_count: 1, progress: 67,
  format_selection: {}, output_profile: {}, created_at: at, updated_at: at, finished_at: at,
  entries: [
    { id: 'e1', batch_id: 'batch-1', selection_index: 0, title: 'Sourdough basics', status: 'completed', progress: 100 },
    { id: 'e2', batch_id: 'batch-1', selection_index: 1, title: 'Knife skills', status: 'failed', progress: 0, error: 'This video is unavailable.' },
  ],
};

async function open(page: Page, scheme: 'light' | 'dark', viewport: { width: number; height: number }) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  const json = (body: unknown) => ({ contentType: 'application/json', body: JSON.stringify(body) });
  await page.route(/\/api\/jobs(\?|$)/, (route) => route.fulfill(json({ items: jobs, next_cursor: null })));
  await page.route(/\/api\/live-recordings(\?|$)/, (route) => route.fulfill(json({ items: recordings, next_cursor: null })));
  await page.route(/\/api\/acquisition-batches(\?|$)/, (route) => route.fulfill(json([batch])));
  await page.goto('/downloads');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Downloads' })).toBeVisible();
}

test('test_download_outcomes', async ({ page }) => {
  await open(page, 'light', { width: 1536, height: 960 });
  const done = page.locator('.g-job').filter({ hasText: 'Evening concert recording' });
  await expect(done.getByText('Saved to Archive disk › Video UHD')).toBeVisible();
  const failed = page.locator('.g-job').filter({ hasText: 'A video that went private' });
  await expect(failed.getByRole('button', { name: 'Retry A video that went private' })).toBeVisible();
  await failed.getByText('1 earlier attempt').click();
  await expect(failed.getByText(/Lumina restarted before this finished/)).toBeVisible();
  await expect(page.locator('.g-job').filter({ hasText: 'river deltas' }).getByRole('button', { name: /Retry/ })).toHaveCount(0);
  await expect(page.locator('.g-job').filter({ hasText: 'Waiting for a free slot' }).getByRole('progressbar')).toHaveCount(0);
  await expect(page.getByText('Recorded with a partial outcome')).toBeVisible();
  await done.getByRole('button', { name: 'Open' }).click();
  await expect(page).toHaveURL(/\/watch\/library\/library-1/);
});

test('test_downloads_visual', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page, 'dark', { width: 1536, height: 960 });
  await expect(page.getByText('Community build night')).toBeVisible();
  await expect(page.getByText('Weekend cooking playlist')).toBeVisible();
  expect(errors).toEqual([]);
});
