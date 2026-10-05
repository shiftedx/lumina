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
  await page.getByRole('button', { name: 'Home', exact: true }).and(page.locator('#primary-navigation button')).click();
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
