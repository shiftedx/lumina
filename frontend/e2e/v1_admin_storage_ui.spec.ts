import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** Storage roots, ordered routing rules and the dry-run tester. */

const observed = (state: string, free: number | null = 812e9, total: number | null = 2e12) => ({ state, checked_at: '2026-09-24T09:30:00Z', free_bytes: free, total_bytes: total });
const roots = [
  { id: 'fast', label: 'Fast SSD', path: '/media/fast', mode: 'managed', enabled: true, minimum_free_bytes: 50 * 1024 ** 3, artifact_count: 1284, observation: observed('available') },
  { id: 'bulk', label: 'Archive array (basement NAS, shelf 2)', path: '/media/nas/archive/long-term-storage/household-video-collection', mode: 'managed', enabled: true, minimum_free_bytes: 0, artifact_count: 0, observation: observed('offline', null, null) },
  { id: 'rips', label: 'Old DVD rips', path: '/media/imports/dvd', mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 37, observation: observed('available', 1e11, 5e11) },
];
const rules = {
  revision: 3,
  default_root_id: 'fast',
  rules: [
    { id: 'uhd', enabled: true, priority: 10, sources: [], media_kinds: ['video'], min_height: 2160, max_height: null, target_root_id: 'bulk', relative_template: '{source}/{height}' },
    { id: 'music', enabled: true, priority: 20, sources: ['youtube', 'soundcloud'], media_kinds: ['audio'], min_height: null, max_height: null, target_root_id: 'fast', relative_template: 'music/{source}' },
  ],
};

async function mockStorage(page: Page, rulesStatus = 200) {
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/admin/storage/roots', (route) => route.fulfill(route.request().method() === 'POST'
    ? json({ detail: 'Storage root must be inside a configured mount parent (LUMINA_STORAGE_MOUNT_PARENTS).' }, 422)
    : json(roots)));
  await page.route('**/api/admin/storage/rules', (route) => route.fulfill(route.request().method() === 'PUT'
    ? json({ detail: 'Storage rules changed since you loaded them; reload and try again.' }, 409)
    : rulesStatus === 200 ? json(rules) : json({ detail: 'Storage rules are temporarily unavailable.' }, rulesStatus)));
  await page.route('**/api/admin/storage/resolve', (route) => route.fulfill(json({ rule_id: 'uhd', rule_revision: 3, root_id: 'bulk', root_label: 'Archive array (basement NAS, shelf 2)', relative_path: 'youtube/2160p', reason: 'Matched rule uhd.', can_admit: false })));
}

test('test_storage_mobile_forms', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockStorage(page);
  await page.goto('/admin/storage');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Library & storage' })).toBeVisible();
  await expect(page.getByRole('row', { name: /Archive array/ }).getByText('Offline')).toBeVisible();

  // Keyboard-only reorder: focus stays on the moved rule's control.
  await page.getByRole('button', { name: 'Move rule 2 up' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('group', { name: 'Rule 1' }).getByLabel('Folder inside root')).toHaveValue('music/{source}');
  await expect(page.getByRole('button', { name: 'Move rule 1 down' })).toBeFocused();

  await page.getByRole('button', { name: 'Test' }).click();
  await expect(page.getByText('cannot receive files right now')).toBeVisible();

  await page.getByLabel('Container path').fill('/etc/secrets');
  await page.getByLabel('Label', { exact: true }).fill('Nope');
  await page.getByRole('button', { name: 'Add root' }).click();
  await expect(page.getByText(/inside a configured mount parent/)).toBeVisible();

  await page.getByRole('button', { name: 'Save rules' }).click();
  await expect(page.getByText(/changed elsewhere/)).toBeVisible();
  await expect(page.getByRole('group', { name: 'Rule 1' }).getByLabel('Folder inside root')).toHaveValue('music/{source}');

  expect(errors).toEqual([]);
});

test('storage rules load failure is actionable', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockStorage(page, 503);
  await page.goto('/admin/storage');
  await signIn(page);
  await expect(page.getByRole('alert').first()).toContainText('temporarily unavailable');
  await expect(page.getByRole('button', { name: 'Reload rules' })).toBeVisible();
});

test('adding an external root offers Import now, shared with the household by default', async ({ page }) => {
  const errors: string[] = [];
  const started: unknown[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockStorage(page);
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  const added = { id: 'films', label: 'Film drive', path: '/media/films', mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 0, observation: observed('available') };
  await page.route('**/api/admin/storage/roots', (route) => route.fulfill(route.request().method() === 'POST' ? json(added, 201) : json(roots)));
  await page.route('**/api/admin/imports', (route) => {
    if (route.request().method() !== 'POST') return route.fulfill(json([]));
    started.push(route.request().postDataJSON());
    return route.fulfill(json({ id: 'run-9', root_id: 'films', state: 'running', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-09-24T10:00:00Z', finished_at: null, status_url: '/api/admin/imports/run-9', root_observation: {} }, 202));
  });
  await page.goto('/admin/storage');
  await signIn(page);
  await page.getByLabel('Container path').fill('/media/films');
  await page.getByLabel('Label', { exact: true }).fill('Film drive');
  await page.getByLabel('Mode').selectOption({ label: 'External — read-only, for imports' });
  await page.getByRole('button', { name: 'Add root' }).click();
  const card = page.getByRole('row').filter({ has: page.getByRole('group', { name: /^Import Film drive now\?/ }) });
  await expect(card.getByRole('radio', { name: /Household/ })).toBeChecked();
  await expect(card.getByRole('button', { name: 'Import now' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Import started. Its progress shows under Imports.')).toBeVisible();
  expect(started).toEqual([{ root_id: 'films', visibility: 'shared' }]);
  expect(errors).toEqual([]);
});
