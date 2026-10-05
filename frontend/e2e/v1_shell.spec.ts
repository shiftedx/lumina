import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** Real browser routes and the extracted responsive shell. */

const primary = (page: Page) => page.getByRole('navigation', { name: 'Primary' });

test('test_route_reload_back_forward', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/watch/library/${item.id}$`));

  await page.reload();
  await expect(page.locator('video').first()).toBeVisible();

  await primary(page).getByRole('button', { name: 'Library' }).click();
  await expect(page).toHaveURL(/\/library$/);
  await primary(page).getByRole('button', { name: 'Streaming' }).click();
  await expect(page).toHaveURL(/\/streaming$/);

  await page.goBack();
  await expect(page.getByRole('heading', { level: 1, name: 'Library' })).toBeVisible();
  await page.goBack();
  await expect(page.locator('video').first()).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('heading', { level: 1, name: 'Library' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Library' })).toBeVisible();
});

test('the old Live, Explore and Subscriptions addresses land on Streaming', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false });
  for (const [old, canonical] of [['/live?rail=gaming', '/streaming/live?rail=gaming'], ['/explore?q=lofi', '/streaming/search?q=lofi'], ['/subscriptions', '/streaming/channels']]) {
    await page.goto(old);
    if (old.startsWith('/live')) await signIn(page);
    await expect.poll(() => new URL(page.url()).pathname + new URL(page.url()).search).toBe(canonical);
  }
});

const surfaces = [
  { name: 'home', path: '/' },
  { name: 'explore', path: '/streaming' },
  { name: 'library', path: '/library' },
  { name: 'watch', path: `/watch/library/${item.id}` },
  { name: 'settings', path: '/settings' },
];

// Width-dependent: the search field must stay inside narrow phone viewports.
for (const viewport of [{ width: 1536, height: 960 }, { width: 390, height: 844 }, { width: 360, height: 800 }]) {
  test(`test_shell_surfaces ${viewport.width}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize(viewport);
    await mockApi(page, { sidebar_collapsed: false });
    await page.goto('/');
    await signIn(page);
    for (const surface of surfaces) {
      await page.goto(surface.path);
      await expect(page.locator('main h1').first()).toBeVisible();
      if (surface.name === 'watch') await expect(page.locator('video').first()).toBeVisible();
      await page.waitForTimeout(300);
      // No page-level horizontal scroll, and the search field stays inside the viewport.
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), `${surface.name} overflow`).toBeLessThanOrEqual(0);
      const trigger = await page.getByRole('button', { name: /^Search Lumina, / }).evaluate((element) => element.getBoundingClientRect().right);
      expect(trigger, `${surface.name} search trigger inside the viewport`).toBeLessThanOrEqual(viewport.width);
    }
    expect(errors).toEqual([]);
  });
}
