import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** Import wizard, live progress and partial results with per-file reasons. */

const observed = (state: string) => ({ state, checked_at: '2026-09-24T09:30:00Z', free_bytes: 1e11, total_bytes: 5e11 });
const roots = [
  { id: 'fast', label: 'Fast SSD', path: '/media/fast', mode: 'managed', enabled: true, minimum_free_bytes: 0, artifact_count: 10, observation: observed('available') },
  { id: 'rips', label: 'Old DVD rips', path: '/media/imports/dvd', mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 37, observation: observed('available') },
  { id: 'nas', label: 'Archive array (basement NAS, shelf 2)', path: '/media/nas/archive/long-term-storage/household-video-collection', mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 0, observation: observed('identity_mismatch') },
];
const run = (patch: Record<string, unknown>) => ({
  id: 'run-1', root_id: 'rips', state: 'running', visibility: 'private', counters: { inspected: 412, indexed: 380, unchanged: 30, skipped: 2 }, coverage: 'complete', error: null,
  created_at: '2026-09-24T10:00:00Z', finished_at: null, status_url: '/api/admin/imports/run-1', root_observation: {}, ...patch,
});
const partial = run({ state: 'partial', coverage: 'incomplete', finished_at: '2026-09-24T10:12:00Z', counters: { inspected: 1204, indexed: 1150, updated: 12, relinked: 3, unchanged: 30, skipped: 4, failed: 5 } });
const entries = [
  { relative_path: 'Films/The Very Long Director’s Cut Title That Keeps Going (1998) [Remastered 4K HDR]/movie.mkv', outcome: 'failed', error: 'unreadable', library_item_id: null },
  { relative_path: 'Series/Show/Season 1/link-to-elsewhere.mkv', outcome: 'skipped', error: 'symlink', library_item_id: null },
  { relative_path: 'Music/Artist/Album/01 - Song.flac', outcome: 'review', error: 'possible_move', library_item_id: 'item-1' },
];

async function mockImports(page: Page, runs: unknown[], current?: unknown) {
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/admin/storage/roots', (route) => route.fulfill(json(roots)));
  await page.route('**/api/admin/imports', (route) => route.fulfill(route.request().method() === 'POST' ? json(run({ counters: {}, visibility: JSON.parse(route.request().postData() || '{}').visibility }), 202) : json(runs)));
  await page.route('**/api/admin/imports/run-1', (route) => route.fulfill(json(current ?? runs[0])));
  await page.route('**/api/admin/imports/run-1/entries*', (route) => {
    const outcome = new URL(route.request().url()).searchParams.get('outcome');
    return route.fulfill(json(entries.filter((entry) => !outcome || entry.outcome === outcome)));
  });
}

test('import wizard, running and partial', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockImports(page, [], run({}));
  await page.goto('/admin/imports');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Library & storage' })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Archive array/ })).toBeDisabled();
  const imports = page.locator('[data-setting-id="library.imports"]');
  await expect(imports.getByText('Different disk mounted')).toBeVisible();

  // Household is the default. Keyboard-only: pick Private instead and start.
  // Inside Settings, Up/Down move between rows rather than the browser's
  // native radio-group selection, so OK (Enter) is what checks the focused radio.
  await expect(page.getByRole('radio', { name: /Household/ })).toBeChecked();
  await page.getByRole('radio', { name: /Household/ }).focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('radio', { name: /Private/ })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('radio', { name: /Private/ })).toBeChecked();
  await expect(page.getByText(/will read/)).toContainText('private');

  await page.getByRole('button', { name: 'Start import' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText(/total is not known/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cancel import' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('import partial with entries', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockImports(page, [partial, run({ id: 'run-0', state: 'failed', error: 'offline', created_at: '2026-09-20T10:00:00Z' })]);
  await page.goto('/admin/imports');
  await signIn(page);
  await expect(page.getByText(/Partial\. Some files could not be checked/)).toBeVisible();
  await expect(page.getByRole('region', { name: 'Files that need attention' }).getByRole('table')).toContainText('never followed');
  await expect(page.getByRole('button', { name: 'Rescan' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'View in Library' })).toHaveAttribute('href', '/library');
  await page.getByRole('combobox', { name: 'Show', exact: true }).selectOption('failed');
  await expect(page.getByRole('region', { name: 'Files that need attention' }).getByRole('table').getByRole('row')).toHaveCount(2);
  expect(errors).toEqual([]);
});

test('a scan that would mark many files missing waits for confirmation', async ({ page }) => {
  let confirmed = false;
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  const pending = run({ state: 'needs_confirmation', finished_at: '2026-09-24T10:12:00Z', counters: { inspected: 1204, missing_candidates: 1180, available: 1204 } });
  await mockImports(page, [pending], pending);
  await page.route('**/api/admin/imports/run-1/confirm', (route) => { confirmed = true; return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(run({ state: 'running' })) }); });
  await page.goto('/admin/imports');
  await signIn(page);
  await expect(page.getByText('1180 of 1204 files would be marked missing. If a drive is unmounted, remount it. Otherwise confirm.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Rescan' })).toBeVisible();
  await page.getByRole('button', { name: 'Confirm missing files' }).click();
  await page.getByRole('dialog', { name: 'Mark 1180 files as missing?' }).getByRole('button', { name: 'Mark as missing' }).click();
  await expect.poll(() => confirmed).toBe(true);
});
