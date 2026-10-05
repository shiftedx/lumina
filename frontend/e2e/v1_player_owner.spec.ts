import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/** One persistent player: Watch and the mini-player share a single media element. */

type Tagged = HTMLVideoElement & { __s48?: string };

async function openWatch(page: Page, viewport = { width: 1536, height: 960 }, scheme: 'light' | 'dark' = 'dark') {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  const checkpoints: number[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && new URL(request.url()).pathname === `/api/library/${item.id}/playback`) {
      checkpoints.push((request.postDataJSON() as { position_seconds: number }).position_seconds);
    }
  });
  // Byte-range media so the browser treats the clip as seekable.
  const media = Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64');
  await page.route(`**/api/library/${item.id}/media`, async (route) => {
    const [, start = '0', end] = /bytes=(\d*)-(\d*)/.exec(route.request().headers().range || '') || [];
    const from = Number(start) || 0;
    const to = end ? Number(end) : media.length - 1;
    await route.fulfill({ status: 206, headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${from}-${to}/${media.length}`, 'Content-Type': SYNTHETIC_MEDIA_TYPE }, body: media.subarray(from, to + 1) });
  });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  const video = page.locator('video');
  await expect(video).toHaveCount(1);
  await video.evaluate(async (media: Tagged) => {
    if (media.readyState < 1) await new Promise((resolve) => media.addEventListener('loadedmetadata', resolve, { once: true }));
    media.pause();
    media.currentTime = 1.2;
    media.__s48 = 'owner';
  });
  return { video, checkpoints };
}

async function goHome(page: Page, mobile = false) {
  await page.getByRole('button', { name: 'Home', exact: true }).and(page.locator(mobile ? '.g-mobile-tabs button' : '#primary-navigation button')).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('region', { name: /^Mini player: / })).toBeVisible();
}

test('test_one_media_instance', async ({ page }) => {
  const { video } = await openWatch(page);
  await goHome(page);
  await expect(video).toHaveCount(1);
  expect(await video.evaluate((media: Tagged) => [media.__s48, media.currentTime > 1.1])).toEqual(['owner', true]);

  await page.getByRole('button', { name: 'Expand player' }).click();
  await expect(page).toHaveURL(new RegExp(`/watch/library/${item.id}$`));
  await expect(page.getByRole('region', { name: /^Mini player: / })).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1, name: item.title })).toBeVisible();
  await expect(video).toHaveCount(1);
  expect(await video.evaluate((media: Tagged) => [media.__s48, media.currentTime > 1.1])).toEqual(['owner', true]);
});

test('test_miniplayer_keyboard_and_progress_saving', async ({ page }) => {
  const { video, checkpoints } = await openWatch(page);
  await goHome(page);
  const mini = page.getByRole('region', { name: /^Mini player: / });
  const play = mini.getByRole('button', { name: 'Play', exact: true });
  await play.focus();
  await page.keyboard.press('Enter');
  await expect(mini.getByRole('button', { name: 'Pause', exact: true })).toBeFocused();
  await video.evaluate((media: HTMLVideoElement) => { media.currentTime = 1.2; });
  await page.keyboard.press('Enter');
  await expect(mini.getByRole('button', { name: 'Play', exact: true })).toBeVisible();
  // Pausing in the mini-player writes the checkpoint exactly as Watch does.
  await expect.poll(() => checkpoints.at(-1)).toBe(1);
  // Escape is left to global handlers; it does not close or expand the player.
  await page.keyboard.press('Escape');
  await expect(mini).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
});

test('test_miniplayer_close_releases', async ({ page }) => {
  const { video } = await openWatch(page);
  await goHome(page);
  await page.getByRole('button', { name: 'Close player' }).click();
  await expect(video).toHaveCount(0);
  await expect(page.getByRole('region', { name: /^Mini player: / })).toHaveCount(0);
});

test('test_logout_silences_player', async ({ page }) => {
  const { video } = await openWatch(page);
  await page.getByRole('button', { name: /, account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'Settings' }).click();
  await expect(page.getByRole('region', { name: /^Mini player: / })).toBeVisible();
  await page.getByRole('button', { name: /, account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await expect(page.locator('.g-auth')).toBeVisible();
  await expect(video).toHaveCount(0);
  await signIn(page);
  await expect(page.getByRole('region', { name: /^Mini player: / })).toHaveCount(0);
  await expect(video).toHaveCount(0);
});

// Width-dependent: on phones the mini-player must sit above the mobile tab bar.
for (const viewport of [{ width: 1536, height: 960 }, { width: 390, height: 844 }]) {
  test(`mini-player over Home ${viewport.width}`, async ({ page }) => {
    await openWatch(page, viewport);
    await goHome(page, viewport.width < 600);
    const box = await page.getByRole('region', { name: /^Mini player: / }).boundingBox();
    expect(box && box.x + box.width).toBeLessThanOrEqual(viewport.width);
    if (viewport.width < 600) {
      const tabs = await page.locator('.g-mobile-tabs').boundingBox();
      expect(box && tabs && box.y + box.height).toBeLessThanOrEqual(tabs?.y ?? 0);
    }
    await expect(page.locator('.watch-mini [data-player-state="ready"]')).toHaveCount(1);
  });
}
