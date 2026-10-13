import { expect, test } from '@playwright/test';
import { mockApi, user } from './lumina-mock';

test('desktop media setup keeps its actions below the form after restoring a scan', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, {});
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending', vault_setup_pending: true } } }));
  await page.route('**/api/admin/onboarding/media', (route) => route.fulfill({ json: { mount_parents: ['/media'] } }));
  await page.route('**/api/admin/storage/roots', (route) => route.fulfill({ json: [{ id: 'fixture-root', label: 'Family films', path: '/media/films', mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 0, observation: { state: 'available' } }] }));
  await page.route('**/api/admin/imports', (route) => route.fulfill({ json: [{ id: 'fixture-scan', root_id: 'fixture-root', state: 'running', visibility: 'shared', counters: {}, coverage: 'incomplete', error: null, created_at: '2026-01-01T00:00:00Z', finished_at: null }] }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Scan underway' })).toBeVisible();
  const addBox = await page.getByRole('button', { name: 'Add and scan' }).boundingBox();
  const actionsBox = await page.locator('.g-vault-actions').boundingBox();
  expect(actionsBox!.y).toBeGreaterThanOrEqual(addBox!.y + addBox!.height);
});

test('owner adds a read-only share and retries its private scan in the welcome journey', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await mockApi(page, {});
  const roots: { id: string; label: string; path: string; mode: string; enabled: boolean; minimum_free_bytes: number; artifact_count: number; observation: { state: string } }[] = [];
  let rootCreates = 0;
  let scanAttempts = 0;
  let mediaPending = true;
  const owner = () => ({ ...user, onboarding_status: 'pending', vault_setup_pending: mediaPending });
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: owner() } }));
  await page.route('**/api/admin/onboarding/media', (route) => route.fulfill({ json: { mount_parents: ['/media'] } }));
  await page.route('**/api/admin/storage/roots', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: roots });
    const payload = route.request().postDataJSON();
    expect(payload).toEqual({ label: 'Family films', container_path: '/media/films', mode: 'external', minimum_free_bytes: 0 });
    rootCreates++;
    const root = { id: 'fixture-root', label: payload.label, path: payload.container_path, mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 0, observation: { state: 'available' } };
    roots.push(root);
    return route.fulfill({ status: 201, json: root });
  });
  await page.route('**/api/admin/imports', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] });
    expect(route.request().postDataJSON()).toEqual({ root_id: 'fixture-root', visibility: 'private' });
    scanAttempts++;
    return scanAttempts === 1
      ? route.fulfill({ status: 500, json: { detail: 'Indexing is temporarily unavailable' } })
      : route.fulfill({ status: 202, json: { id: 'fixture-scan', root_id: 'fixture-root', state: 'running', visibility: 'private', counters: {}, coverage: 'incomplete', error: null, created_at: '2026-01-01T00:00:00Z', finished_at: null } });
  });
  await page.route('**/api/admin/onboarding/media/complete', (route) => {
    mediaPending = false;
    return route.fulfill({ json: owner() });
  });
  await page.goto('/');
  await expect(page.getByLabel('Mounted path')).toBeVisible();
  const addBox = await page.getByRole('button', { name: 'Add and scan' }).boundingBox();
  const actionsBox = await page.locator('.g-vault-actions').boundingBox();
  expect(addBox!.y + addBox!.height).toBeLessThanOrEqual(900);
  expect(actionsBox!.y).toBeGreaterThanOrEqual(addBox!.y + addBox!.height);
  const help = page.locator('.g-vault-mount-help summary');
  await help.click();
  await help.focus();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('region', { name: 'Docker Compose mount example' })).toBeFocused();
  await help.click();
  await page.getByLabel('Folder name').fill('Family films');
  await page.getByLabel('Mounted path').fill('/media/films');
  await page.getByRole('radio', { name: 'Private', exact: true }).check();
  await page.getByRole('button', { name: 'Add and scan' }).click();
  await expect(page.getByRole('region', { name: 'Media folders' })).toContainText('Family films');
  await page.getByRole('button', { name: 'Retry scan' }).click();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Welcome to Lumina' })).toBeVisible();
  expect(rootCreates).toBe(1);
  expect(scanAttempts).toBe(2);
});

test('first account resumes media setup before the welcome and persists the choice to finish later', async ({ page }) => {
  await mockApi(page, {});
  let accountCreated = false;
  let signedIn = false;
  let mediaPending = true;
  let completeAttempts = 0;
  const owner = () => ({ ...user, onboarding_status: 'pending', vault_setup_pending: mediaPending });
  await page.route('**/api/bootstrap/status', (route) => route.fulfill({ json: { needs_setup: !accountCreated } }));
  await page.route('**/api/bootstrap/admin', (route) => {
    accountCreated = true;
    return route.fulfill({ json: owner() });
  });
  await page.route('**/api/session/login', (route) => {
    signedIn = true;
    return route.fulfill({ json: { user: owner() } });
  });
  await page.route('**/api/session/me', (route) => route.fulfill(signedIn ? { json: { user: owner() } } : { status: 401, json: { detail: 'not authenticated' } }));
  await page.route('**/api/admin/onboarding/media', (route) => route.fulfill({ json: { mount_parents: ['/media'] } }));
  await page.route('**/api/admin/storage/roots', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/admin/imports', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/admin/onboarding/media/complete', (route) => {
    completeAttempts++;
    if (completeAttempts === 1) return route.fulfill({ status: 500, json: { detail: 'Setup could not be saved' } });
    mediaPending = false;
    return route.fulfill({ json: owner() });
  });

  await page.goto('/');
  await page.getByLabel('Username').fill('fixture-owner');
  await page.getByLabel('Display name').fill('Fixture owner');
  await page.getByLabel('Password', { exact: true }).fill('fixture-long-passphrase');
  await page.getByRole('button', { name: 'Create household vault' }).click();
  await expect(page.getByRole('heading', { name: 'Give your vault a home.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Welcome to Lumina' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Give your vault a home.' })).toBeVisible();
  await page.getByRole('button', { name: 'Set up media later' }).click();
  await expect(page.getByText('Setup could not be saved')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Give your vault a home.' })).toBeVisible();
  await page.getByRole('button', { name: 'Set up media later' }).click();
  await expect(page.getByRole('heading', { name: 'Welcome to Lumina' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Welcome to Lumina' })).toBeVisible();
  expect(completeAttempts).toBe(2);
});

for (const role of ['admin', 'viewer']) {
  test(`${role} member without the first-owner marker enters personal onboarding directly`, async ({ page }) => {
    await mockApi(page, {});
    let mediaReads = 0;
    await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, role, onboarding_status: 'pending', vault_setup_pending: false } } }));
    await page.route('**/api/admin/onboarding/media', (route) => { mediaReads++; return route.fulfill({ status: 403, json: { detail: 'forbidden' } }); });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Welcome to Lumina' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Give your vault a home.' })).toHaveCount(0);
    expect(mediaReads).toBe(0);
  });
}
