import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';
import { mockPreview, mockReco } from './reco-mock';
import { pickedSnapshot, recoEntry } from '../src/test/recoFixtures';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/** Watch composition for a web video: 16:9 player with Up next beside it (under it in theater), the editorial column under the player, tools in the column. */

async function openWatch(page: Page, viewport: { width: number; height: number }) {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await page.waitForTimeout(300);
}

const box = async (page: Page, selector: string) => {
  const rect = await page.locator(selector).first().boundingBox();
  if (!rect) throw new Error(`${selector} is not rendered`);
  return rect;
};

test('saved YouTube playback keeps a ranked Up next and moves between saved and streamed videos', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false, autoplay_up_next: false });
  const streamed = recoEntry('streamed-next', 0, { title: 'A walk along the coast' });
  const saved = recoEntry('synthetic-1', 1, { title: item.title, webpage_url: item.webpage_url, saved_item_id: item.id });
  const log = await mockReco(page, { upNext: pickedSnapshot(0, { items: [streamed, saved] }) });
  let previews = 0;
  page.on('request', (request) => { if (new URL(request.url()).pathname === '/api/preview') previews += 1; });
  await mockPreview(page, {
    title: streamed.title, webpage_url: streamed.webpage_url,
    capabilities: streamed.capabilities,
    raw: { id: streamed.id, extractor: 'youtube', uploader: streamed.uploader, duration: 90 },
    playback: { status: 'ready', stream_id: 'watch-continuity', transport: 'progressive', media_kind: 'video', playback_url: '/api/remote-streams/watch-continuity/content', content_type: SYNTHETIC_MEDIA_TYPE, has_video: true, has_audio: true, seekable: true, renditions: [] },
  });
  await page.route('**/api/remote-streams/**', (route) => route.request().method() === 'DELETE'
    ? route.fulfill({ status: 204, body: '' })
    : route.fulfill({ status: 200, contentType: SYNTHETIC_MEDIA_TYPE, body: Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64') }));
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  const next = page.locator('.g-up-next').getByRole('button', { name: /^A walk along the coast/ });
  await expect(next).toBeVisible();
  expect(log.upNext[0]).toMatchObject({ source_url: item.webpage_url, source_id: item.remote_id, source: 'youtube' });
  await expect(page.locator('.g-up-next').getByRole('button', { name: item.title })).toHaveCount(0);
  await expect.poll(() => page.locator('video').evaluate((media: HTMLVideoElement) => media.currentTime)).toBeGreaterThan(0.2);
  await page.locator('video').evaluate((media: HTMLVideoElement) => media.pause());
  const autoplay = page.getByRole('switch', { name: 'Autoplay next video' });
  await autoplay.check();
  await expect(autoplay).toBeChecked();
  await expect(page.locator('.g-up-next')).toContainText(`Next: ${streamed.title}`);
  await page.screenshot({ path: 'test-results/visual/watch-saved-up-next-desktop.png' });
  await autoplay.uncheck();
  await page.setViewportSize({ width: 390, height: 844 });
  await next.scrollIntoViewIfNeeded();
  await expect(next).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: 'test-results/visual/watch-saved-up-next-phone.png' });
  await next.click();
  await expect(page.getByRole('heading', { level: 1, name: streamed.title! })).toBeVisible();
  await expect(page.locator('video')).toHaveAttribute('src', /remote-streams/);
  await page.locator('.g-up-next').getByRole('button', { name: new RegExp(`^${item.title}`) }).click();
  await expect(page).toHaveURL(new RegExp(`/watch/library/${item.id}`));
  await expect(page.locator('.g-watch-kicker')).toHaveText('YouTube · In your library');
  expect(previews).toBe(1);
});

test('test_watch_geometry', async ({ page }) => {
  await openWatch(page, { width: 1536, height: 960 });
  // Standard: Up next shares the row with the player; the column sits under the player.
  let player = await box(page, '.player-frame');
  expect(Math.abs(player.height - player.width * 9 / 16)).toBeLessThanOrEqual(2);
  let side = await box(page, '.g-watch-side');
  expect(side.x).toBeGreaterThanOrEqual(player.x + player.width);
  expect(side.y).toBeLessThan(player.y + player.height);
  expect((await box(page, '.g-watch-column h1')).y).toBeGreaterThanOrEqual(player.y + player.height);
  expect((await box(page, '.watch-tools')).y).toBeGreaterThan(player.y + player.height);
  await page.screenshot({ path: 'test-results/visual/watch-web-standard.png' });

  // Theater: the player takes the width; the column and side column sit under it.
  await page.locator('body').press('t');
  await expect(page.locator('.watch-layout.theater')).toBeVisible();
  await page.waitForTimeout(300);
  player = await box(page, '.player-frame');
  const column = await box(page, '.g-watch-column');
  side = await box(page, '.g-watch-side');
  expect(column.y).toBeGreaterThanOrEqual(player.y + player.height);
  expect(side.y).toBeGreaterThanOrEqual(player.y + player.height);
  expect(side.x).toBeGreaterThan(column.x + column.width);
  await page.screenshot({ path: 'test-results/visual/watch-web-theater.png' });
});

test('test_watch_small_layout', async ({ page }) => {
  for (const width of [390, 320]) {
    await openWatch(page, { width, height: 844 });
    const player = await box(page, '.player-frame');
    expect(player.x).toBe(0);
    expect(player.width).toBe(width);
    const column = await box(page, '.g-watch-column');
    const side = await box(page, '.g-watch-side');
    expect(side.y).toBeGreaterThanOrEqual(column.y + column.height);
    const tabs = page.getByRole('tablist', { name: 'Video tools' });
    const tabBox = await tabs.boundingBox();
    expect(tabBox && tabBox.x + tabBox.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    await expect(page.getByRole('tab', { name: 'Notes', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Share' })).toBeVisible();
  }
});

test('test_watch_preserves_current_play', async ({ page }) => {
  await openWatch(page, { width: 1536, height: 960 });
  const video = page.locator('video').first();
  await video.evaluate((media: HTMLVideoElement) => { media.dataset.identity = 'original'; });
  // Let the synthetic clip play briefly, then hold its position.
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => { if (media.paused) void media.play().catch(() => undefined); return media.currentTime; })).toBeGreaterThan(0.3);
  const position = await video.evaluate((media: HTMLVideoElement) => { media.pause(); return media.currentTime; });

  const notes = page.getByRole('tab', { name: 'Notes', exact: true });
  await notes.click();
  await expect(page.getByRole('region', { name: 'Notes', exact: true })).toBeVisible();
  await notes.press('ArrowLeft');
  // Moments sits right before Notes; Home jumps back to the first tool.
  await expect(page.getByRole('tab', { name: 'Moments' })).toBeFocused();
  await page.keyboard.press('Home');
  await expect(page.getByRole('tab', { name: 'Summary' })).toBeFocused();
  await expect(page.getByRole('tab', { name: 'Summary' })).toHaveAttribute('aria-selected', 'true');

  expect(await video.evaluate((media: HTMLVideoElement) => [media.dataset.identity, media.currentTime])).toEqual(['original', position]);
});

test('test_watch_tools_tablist_not_gallery_tabs_spacing', async ({ page }) => {
  await openWatch(page, { width: 1536, height: 960 });
  const tablist = page.getByRole('tablist', { name: 'Video tools' });
  await expect(tablist).toBeVisible();
  // `.gallery .g-tabs` (24px margin-bottom, 45px min-height) must not leak into the watch tools tablist.
  // The 24px gap is the web-video column's own rule (watch.css), unchanged by this task.
  const style = await tablist.evaluate((el) => {
    const s = getComputedStyle(el);
    return { gap: s.columnGap, margin: s.marginBottom, minHeight: s.minHeight };
  });
  expect(style).toEqual({ gap: '24px', margin: '0px', minHeight: '0px' });
});
