import { navigateApp } from './lumina-mock';
import { expect, test, type Page } from '@playwright/test';
import { mockHome } from './home-mock';
import { item, mockApi, signIn } from './lumina-mock';
import { mockReco } from './reco-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/** The player chrome, captions and the mini player over the mocked API. */

async function openWatch(page: Page, uiPrefs: Record<string, unknown> = { sidebar_collapsed: false }) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, uiPrefs);
  const media = Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64');
  await page.route(`**/api/library/${item.id}/media`, async (route) => {
    const [, start = '0', end] = /bytes=(\d*)-(\d*)/.exec(route.request().headers().range || '') || [];
    const from = Number(start) || 0;
    const to = end ? Number(end) : media.length - 1;
    await route.fulfill({ status: 206, headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${from}-${to}/${media.length}`, 'Content-Type': SYNTHETIC_MEDIA_TYPE }, body: media.subarray(from, to + 1) });
  });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video')).toHaveCount(1);
}

async function goHome(page: Page) {
  await navigateApp(page, 'Home');
  await expect(page.getByRole('region', { name: /^Mini player: / })).toBeVisible();
}

test('seek fill is gold and overlay focus draws a ring', async ({ page }) => {
  await openWatch(page);
  const gold = await page.evaluate(() => { const probe = document.createElement('i'); probe.style.color = 'var(--g-gold)'; document.body.append(probe); const value = getComputedStyle(probe).color; probe.remove(); return value; });
  const seek = page.getByLabel('Seek');
  await expect(seek).toHaveCSS('background-image', new RegExp(gold.replace(/[()]/g, '\\$&')));
  await page.keyboard.press('Shift'); // keyboard modality: a bare focus() on a range input is not :focus-visible
  await seek.focus();
  expect(await seek.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe('none');
});

test('captions follow the member preference', async ({ page }) => {
  await openWatch(page, { sidebar_collapsed: false, captions: { size: 'large', background: 'box' } });
  await expect(page.locator('.lumina-player')).toHaveCSS('--caption-scale', '1.3');
});

test('reduced motion: no animation after showing the mini player', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openWatch(page);
  await goHome(page);
  expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
});

test('the mini player keeps the one media element', async ({ page }) => {
  await openWatch(page);
  await goHome(page);
  expect(await page.locator('video, audio').count()).toBe(1);
});

test('forced colours: the seek fill and focus ring stay visible', async ({ page }) => {
  await page.emulateMedia({ forcedColors: 'active' });
  await openWatch(page);
  const seek = page.getByLabel('Seek');
  expect(await seek.evaluate((element) => getComputedStyle(element).backgroundImage)).not.toBe('none');
  await page.keyboard.press('Shift'); // keyboard modality: a bare focus() on a range input is not :focus-visible
  await seek.focus();
  expect(await seek.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe('none');
});

test('a reco menu opens above the mini player', async ({ page }) => {
  await openWatch(page);
  // After openWatch: its mockApi routes are newer and would otherwise win over the Home and reco mocks.
  await mockHome(page);
  await mockReco(page);
  await goHome(page);
  await page.getByRole('button', { name: /^More options for / }).last().click();
  const box = (await page.getByRole('menu').boundingBox())!;
  const topmost = await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('.g-menu') !== null, { x: box.x + box.width / 2, y: box.y + box.height / 2 });
  expect(topmost).toBe(true);
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 320, height: 740 }]) {
  test(`casting stays in player settings and follows receiver availability at ${viewport.width}px`, async ({ page }) => {
    await page.addInitScript(() => {
      let state = 'NO_DEVICES_AVAILABLE';
      const listeners = new Set<() => void>();
      const context = {
        getCastState: () => state,
        requestSession: async () => { throw new Error('Receiver selection was canceled or network access was denied.'); },
        addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
        removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
      };
      Object.defineProperty(window, 'cast', { value: { framework: { CastContext: { getInstance: () => context }, CastContextEventType: { CAST_STATE_CHANGED: 'caststatechanged' } } } });
      const chrome = (window as Window & { chrome?: { cast?: object } }).chrome;
      if (chrome) chrome.cast = {};
      else Object.defineProperty(window, 'chrome', { value: { cast: {} } });
      window.addEventListener('test-cast-availability', (event) => {
        state = (event as CustomEvent<string>).detail;
        listeners.forEach((listener) => listener());
      });
      Object.defineProperty(HTMLMediaElement.prototype, 'webkitShowPlaybackTargetPicker', { value: () => {} });
    });
    await openWatch(page);
    await page.setViewportSize(viewport);
    const player = page.locator('.lumina-player');
    await player.hover();
    await expect(page.getByText('Chromecast preview')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Enable Chromecast' })).toHaveCount(0);
    await expect(player.getByRole('button', { name: 'Cast to Chromecast' })).toHaveCount(0);
    await expect(player.getByRole('button', { name: 'AirPlay', exact: true })).toHaveCount(0);
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent('test-cast-availability', { detail: 'NOT_CONNECTED' }));
      document.querySelector('video')!.dispatchEvent(Object.assign(new Event('webkitplaybacktargetavailabilitychanged'), { availability: 'available' }));
    });
    await expect(player.getByRole('button', { name: 'Cast to Chromecast' })).toHaveCount(0);
    await expect(player.getByRole('button', { name: 'AirPlay', exact: true })).toHaveCount(0);
    const timestamp = (await player.getByLabel('Playback time').boundingBox())!;
    const settings = player.getByRole('button', { name: 'Playback settings' });
    const control = (await settings.boundingBox())!;
    expect(control.y >= timestamp.y + timestamp.height || control.x >= timestamp.x + timestamp.width).toBeTruthy();
    await page.screenshot({ path: `../output/playwright/casting-${viewport.width}.png` });
    await settings.click();
    const menu = player.getByRole('group', { name: 'Playback settings' });
    await expect(menu.getByRole('button', { name: 'Cast to Chromecast' })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'AirPlay', exact: true })).toBeVisible();
    await page.screenshot({ path: `../output/playwright/casting-menu-${viewport.width}.png` });
    await menu.getByRole('button', { name: 'Cast to Chromecast' }).click();
    await expect(menu.getByRole('alert')).toHaveText('Receiver selection was canceled or network access was denied.');
    await expect(menu.getByRole('button', { name: 'Cast to Chromecast' })).toBeEnabled();
    await page.keyboard.press('Escape');
    await expect(settings).toBeFocused();
    await expect(menu).toBeHidden();
    await settings.click();
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent('test-cast-availability', { detail: 'NO_DEVICES_AVAILABLE' }));
      document.querySelector('video')!.dispatchEvent(Object.assign(new Event('webkitplaybacktargetavailabilitychanged'), { availability: 'not-available' }));
    });
    await expect(player.getByRole('button', { name: 'Cast to Chromecast' })).toHaveCount(0);
    await expect(player.getByRole('button', { name: 'AirPlay', exact: true })).toHaveCount(0);
  });
}
