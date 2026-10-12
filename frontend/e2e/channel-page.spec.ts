/** Browser: the channel page in light and dark, desktop and phone. */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { CHANNEL_ID, libraryChannelsBody, mockChannelPage, mockLive, mockRemoteArtwork, type ChannelMockOptions } from './live-youtube-mock';
import { mockApi, signIn } from './lumina-mock';

async function open(page: Page, path = `/channel/youtube/${CHANNEL_ID}`, options: ChannelMockOptions = {}, viewport = { width: 1440, height: 900 }, theme: 'light' | 'dark' = 'dark', savedChannels = 0) {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: theme });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false, theme });
  await mockRemoteArtwork(page);
  await mockLive(page);
  const requests = await mockChannelPage(page, options);
  await page.route(/\/api\/library\/channels(\?|$)/, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(libraryChannelsBody(savedChannels)) }));
  await page.goto(path);
  await signIn(page);
  return requests;
}

for (const theme of ['light', 'dark'] as const) {
  for (const [label, width, height] of [['desktop', 1440, 900], ['phone', 390, 844]] as const) {
    test(`the channel page reads cleanly in ${theme} at ${label} width`, async ({ page }) => {
      await open(page, undefined, {}, { width, height }, theme, 2);
      await expect(page.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeVisible();
      await expect(page.locator('.g-channel-wall .g-remote-card').first()).toBeVisible();
      const results = await new AxeBuilder({ page }).include('main').analyze();
      expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/visual/channel-${theme}-${label}.png` });
      const left = page.getByRole('button', { name: 'Scroll Channel tabs left' });
      const right = page.getByRole('button', { name: 'Scroll Channel tabs right' });
      if (label === 'phone') {
        await expect(left).toBeDisabled();
        await expect(right).toBeEnabled();
      } else {
        await expect(left).toHaveCount(0);
        await expect(right).toHaveCount(0);
      }
    });
  }
}

test('tabs live in the address, Show more asks for 120 once', async ({ page }) => {
  const requests = await open(page, undefined, { patch: { has_more: true } });
  await page.getByRole('tab', { name: 'Live' }).click();
  await expect(page).toHaveURL(new RegExp(`/channel/youtube/${CHANNEL_ID}\\?tab=live$`));
  await page.getByRole('tab', { name: 'Videos' }).click();
  await page.getByRole('button', { name: 'Show more' }).click();
  await expect.poll(() => requests.some((request) => request.tab === 'videos' && request.limit === '120')).toBe(true);
  expect(requests.filter((request) => request.limit === '120').length).toBe(1);
  await expect(page.getByText('Showing the latest 120 · Open on YouTube for more')).toBeVisible();
});

test('a slow YouTube shows the header at once and the error at the end', async ({ page }) => {
  await open(page, undefined, { status: 504, delayMs: 300 });
  await expect(page.getByText('Lumina couldn\'t reach YouTube for this channel.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('a handle address resolves and replaces itself', async ({ page }) => {
  await open(page, `/channel?url=${encodeURIComponent('https://www.youtube.com/@harborfilms')}`);
  await expect(page).toHaveURL(new RegExp(`/channel/youtube/${CHANNEL_ID}$`));
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeVisible();
});

test('the tabs and actions walk by keyboard alone', async ({ page }) => {
  await open(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeVisible();
  await page.getByRole('tab', { name: 'Videos' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Live' })).toBeFocused();
  await expect(page).toHaveURL(/tab=live/);
});

test('phone channel tabs expose both overflow ends and keep the selected label visible', async ({ page }) => {
  await open(page, undefined, {}, { width: 390, height: 844 }, 'light', 2);
  const row = page.getByRole('tablist', { name: 'Channel' });
  const left = page.getByRole('button', { name: 'Scroll Channel tabs left' });
  const right = page.getByRole('button', { name: 'Scroll Channel tabs right' });
  await expect(left).toBeDisabled();
  await expect(right).toBeEnabled();
  expect((await left.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect((await right.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await right.click();
  await expect.poll(() => row.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  await expect(left).toBeEnabled();
  await row.evaluate((element) => { element.scrollLeft = element.scrollWidth; element.dispatchEvent(new Event('scroll')); });
  await expect(right).toBeDisabled();
  await page.getByRole('tab', { name: /In your library/ }).click();
  const selected = page.getByRole('tab', { name: /In your library/ });
  const [rowBox, selectedBox] = await Promise.all([row.boundingBox(), selected.boundingBox()]);
  expect(selectedBox!.x).toBeGreaterThanOrEqual(rowBox!.x);
  expect(selectedBox!.x + selectedBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
});

test('the active Channel tab stays visible when desktop shrinks to phone without moving the page', async ({ page }) => {
  await open(page, `/channel/youtube/${CHANNEL_ID}?tab=library`, {}, { width: 1440, height: 900 }, 'dark', 2);
  await page.evaluate(() => {
    document.documentElement.style.overflowAnchor = 'none';
    document.body.style.overflowAnchor = 'none';
    window.scrollTo(0, 300);
  });
  await page.setViewportSize({ width: 390, height: 844 });
  const pageTop = await page.evaluate(() => window.scrollY);
  const row = page.getByRole('tablist', { name: 'Channel' });
  const active = page.getByRole('tab', { name: /In your library/ });
  await expect.poll(async () => {
    const [rowBox, activeBox] = await Promise.all([row.boundingBox(), active.boundingBox()]);
    return activeBox!.x >= rowBox!.x && activeBox!.x + activeBox!.width <= rowBox!.x + rowBox!.width;
  }).toBe(true);
  expect(await page.evaluate(() => window.scrollY)).toBe(pageTop);
});
