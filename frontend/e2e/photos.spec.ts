import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { mockApi, signIn } from './lumina-mock';
import { landscapePng } from './photo-fixtures';

async function photoLibrary(page: Page, count = 600, theme: 'light' | 'dark' = 'dark') {
  await mockApi(page, { theme });
  const images = Array.from({ length: 4 }, (_, seed) => landscapePng(seed));
  const portraits = Array.from({ length: 4 }, (_, seed) => landscapePng(seed, 640, 960));
  const photos = Array.from({ length: count }, (_, index) => ({
    id: `photo-${index}`, title: `Landscape ${index + 1}`, filename: `landscape-${index + 1}.png`, content_type: 'image/png',
    width: index % 5 === 0 ? 640 : 960, height: index % 5 === 0 ? 960 : 640, captured_at: '2026-10-01T12:00:00', added_at: '2026-10-02T12:00:00',
    camera: 'Synthetic camera', bytes: images[index % images.length].length, favorite: false, visibility: 'private',
    thumbnail_url: `/api/photos/photo-${index}/image?width=400`, preview_url: `/api/photos/photo-${index}/image?width=1200`,
    original_url: `/api/photos/photo-${index}/original`, deleted_at: null as string | null, album_ids: [], person_ids: [], can_edit: true,
  }));
  const requests: URL[] = [];
  await page.route((url) => url.pathname.startsWith('/api/photos') || url.pathname.startsWith('/api/photo-'), async (route) => {
    const url = new URL(route.request().url()); requests.push(url);
    const json = (value: unknown) => route.fulfill({ json: value });
    if (/\/image$/.test(url.pathname)) {
      const index = Number(/photo-(\d+)/.exec(url.pathname)?.[1] ?? 0);
      return route.fulfill({ contentType: 'image/png', body: (index % 5 === 0 ? portraits : images)[index % images.length] });
    }
    if (url.pathname === '/api/photo-albums' || url.pathname === '/api/photo-people') return json({ items: [] });
    const match = /^\/api\/photos\/photo-(\d+)(\/restore)?$/.exec(url.pathname);
    if (match) {
      const photo = photos[Number(match[1])];
      if (match[2]) photo.deleted_at = null;
      else if (route.request().method() === 'PATCH') {
        const patch = route.request().postDataJSON() as { favorite?: boolean; deleted?: boolean };
        if (patch.favorite !== undefined) photo.favorite = patch.favorite;
        if (patch.deleted !== undefined) photo.deleted_at = patch.deleted ? '2026-10-24T12:00:00' : null;
      }
      return json(photo);
    }
    if (url.pathname === '/api/photos') {
      const deleted = url.searchParams.get('deleted') === 'true';
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const filtered = photos.filter((photo) => Boolean(photo.deleted_at) === deleted && (!url.searchParams.has('favorite') || photo.favorite) && (!q || photo.title.toLowerCase().includes(q)));
      const start = Number(url.searchParams.get('cursor') ?? 0); const end = start + Number(url.searchParams.get('limit') ?? 100);
      return json({ items: filtered.slice(start, end), total: filtered.length, next_cursor: end < filtered.length ? String(end) : null });
    }
    return route.fallback();
  });
  return requests;
}

for (const viewport of [{ label: 'desktop', width: 1440, height: 1000 }, { label: 'phone', width: 390, height: 844 }]) {
  test(`Photos supports a bounded timeline, immersive keyboard viewing, favorites and restore on ${viewport.label}`, async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const errors: string[] = []; page.on('pageerror', (error) => errors.push(error.message));
    const requests = await photoLibrary(page);
    await page.goto('/photos'); await signIn(page);
    await expect(page.getByRole('heading', { name: 'Photos', exact: true })).toBeVisible();
    const tile = page.getByTestId('photo-tile');
    await expect(tile.first()).toBeVisible();
    await expect.poll(() => tile.first().locator('img').evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    await expect.poll(() => tile.count()).toBeGreaterThan(5);
    const initialTiles = await tile.count();
    expect(initialTiles).toBeLessThan(70);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: testInfo.outputPath(`photos-${viewport.label}.png`), fullPage: false });

    const first = page.getByRole('button', { name: 'View Landscape 1', exact: true });
    await first.click();
    await expect(page.getByRole('dialog', { name: 'Photo viewer' })).toBeVisible();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Photo viewer' })).toBeHidden();
    await expect(first).toBeFocused();
    expect(requests.filter((url) => url.pathname.endsWith('/original'))).toEqual([]);

    await page.getByRole('button', { name: 'Add Landscape 1 to favorites', exact: true }).click();
    await page.getByRole('button', { name: 'Favorites', exact: true }).click();
    await expect(page).toHaveURL(/favorite=true/);
    await expect(tile).toHaveCount(1);
    await page.getByRole('button', { name: 'Move Landscape 1 to trash', exact: true }).click();
    await page.getByRole('button', { name: 'Trash', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Restore Landscape 1', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Restore Landscape 1', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Trash is empty' })).toBeVisible();
    await page.getByRole('button', { name: 'Timeline', exact: true }).click();
    await expect(tile.first()).toBeVisible();
    for (let step = 0; step < 7; step += 1) {
      await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
      await page.waitForTimeout(150);
    }
    expect(await tile.count()).toBeLessThan(70);
    expect(requests.filter((url) => url.pathname === '/api/photos' && url.searchParams.has('cursor')).length).toBeGreaterThan(0);
    const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(accessibility.violations.filter((violation) => ['serious', 'critical'].includes(violation.impact ?? '')).map((violation) => violation.id)).toEqual([]);
    expect(errors).toEqual([]);
    await testInfo.attach('photo-window-observation.json', { body: JSON.stringify({ syntheticPhotos: 600, viewport, initialTiles, finalTiles: await tile.count(), catalogPages: requests.filter((url) => url.pathname === '/api/photos').length, derivativeRequests: requests.filter((url) => url.pathname.endsWith('/image')).length, originalRequests: requests.filter((url) => url.pathname.endsWith('/original')).length }), contentType: 'application/json' });
  });
}

test('People requires opt-in, acquires the local model, and opens an editable person filter', async ({ page }) => {
  await photoLibrary(page, 3);
  let enabled = false; let downloaded = false; let name: string | null = null;
  let acknowledged = false;
  const person = () => ({ id: 'person-1', name, hidden: false, photo_count: 3, face_count: 3, thumbnail_url: '/api/photos/photo-0/image?width=400', revision: 1 });
  const settings = () => ({ enabled, auto_group: true, consented_at: enabled ? '2026-10-24T12:00:00' : null, model: { id: 'face-model', state: downloaded ? 'ready' : 'absent', reason: null, bytes_done: downloaded ? 123 : 0, bytes_total: 123 }, jobs: downloaded ? { succeeded: 3 } : enabled ? { queued: 3 } : {}, pipeline_signature: 'synthetic-test' });
  await page.route((url) => url.pathname === '/api/photo-faces/settings' || url.pathname === '/api/photo-people' || url.pathname === '/api/photo-people/person-1' || url.pathname === '/api/admin/models/face-model/download', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/photo-faces/settings') {
      if (route.request().method() === 'PUT') {
        const payload = route.request().postDataJSON() as { enabled: boolean; acknowledge_biometric_processing?: boolean };
        enabled = payload.enabled; acknowledged ||= payload.acknowledge_biometric_processing === true;
      }
      return route.fulfill({ json: settings() });
    }
    if (path.endsWith('/download')) { downloaded = true; return route.fulfill({ json: settings().model }); }
    if (path.endsWith('/person-1')) { name = (route.request().postDataJSON() as { name: string }).name; return route.fulfill({ json: person() }); }
    return route.fulfill({ json: { items: downloaded ? [person()] : [], next_offset: null } });
  });
  await page.goto('/photos/people'); await signIn(page);
  await page.getByRole('switch', { name: 'Find people in photos' }).click();
  const consent = page.getByRole('dialog', { name: 'Turn on local face analysis?' });
  await expect(consent.getByRole('button', { name: 'Turn on', exact: true })).toBeDisabled();
  await consent.getByRole('checkbox').check();
  await consent.getByRole('button', { name: 'Turn on', exact: true }).click();
  expect(acknowledged).toBe(true);
  await page.getByRole('button', { name: 'Download face model', exact: true }).click();
  await page.getByRole('button', { name: 'Refresh people', exact: true }).click();
  await page.getByRole('button', { name: 'Add name', exact: true }).click();
  const naming = page.getByRole('dialog', { name: 'Name this person' });
  await naming.getByLabel('Name', { exact: true }).fill('Alex');
  await naming.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('button', { name: /Alex.*3 photos/ }).click();
  await expect(page).toHaveURL(/person=person-1/);
  await expect(page.getByRole('region', { name: 'Photos timeline' })).toBeVisible();
});


test('photo details keeps readable controls and contained layout in the light theme on a phone', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' });
  await photoLibrary(page, 3, 'light');
  await page.route('**/api/photos/photo-0/faces', (route) => route.fulfill({ json: { items: [{ id: 'face-1', person_id: null, box: { x: .1, y: .1, width: .2, height: .2 }, confidence: .95, thumbnail_url: '/api/photos/photo-0/image?width=400' }] } }));
  await page.route((url) => url.pathname === '/api/photo-people', async (route) => route.fulfill({ json: route.request().method() === 'POST' ? { person: { id: 'named-person', name: 'Mara', hidden: false, photo_count: 1, face_count: 1, thumbnail_url: null, revision: 1 } } : { items: [], next_offset: null } }));
  await page.goto('/photos'); await signIn(page);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.getByRole('button', { name: 'View Landscape 1', exact: true }).click();
  await page.getByRole('button', { name: 'Show photo details', exact: true }).click();
  const panel = page.getByRole('complementary', { name: 'Photo details' });
  await expect(panel.getByLabel('Title', { exact: true })).toHaveValue('Landscape 1');
  const violations = (await new AxeBuilder({ page }).include('.ph-viewer-panel').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
  await testInfo.attach('viewer-contrast.json', { body: JSON.stringify(violations), contentType: 'application/json' });
  expect(violations.filter((violation) => ['serious', 'critical'].includes(violation.impact ?? '')).map((violation) => violation.id)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath('photo-details-light-phone.png') });
  await panel.getByRole('button', { name: 'Name person', exact: true }).click();
  const nameDialog = page.getByRole('dialog', { name: 'Name this person' });
  await expect(nameDialog.getByLabel('Name', { exact: true })).toBeFocused();
  await nameDialog.getByLabel('Name', { exact: true }).fill('Mara');
  await nameDialog.getByRole('button', { name: 'Save person', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Photo viewer' })).toBeVisible();
  await page.keyboard.press('Escape');
});
