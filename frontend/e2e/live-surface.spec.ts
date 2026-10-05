/** Browser: /live in light and dark, desktop and phone, by keyboard alone. */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { caps, liveItems, liveSnapshotBody, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { mockApi, signIn } from './lumina-mock';

const snapshot = liveSnapshotBody({
  hero: [liveItems(2, { uploader_id: null }, 'follow')[0]],
  items: [
    ...liveItems(24),
    { ...liveItems(1, {}, 'up')[0], kind: 'video', capabilities: caps('youtube', 'upcoming', { can_schedule: true, scheduled_start: '2026-09-29T21:30:00Z' }) },
    { ...liveItems(1, {}, 'end')[0], kind: 'video', capabilities: caps('youtube', 'post_live') },
  ],
});

async function open(page: Page, { width = 1440, height = 900, theme = 'dark' as 'light' | 'dark', motion = 'reduce' as 'reduce' | 'no-preference', body = snapshot } = {}) {
  await page.emulateMedia({ reducedMotion: motion, colorScheme: theme });
  await page.setViewportSize({ width, height });
  await mockApi(page, { sidebar_collapsed: false, theme });
  await mockRemoteArtwork(page);
  const live = await mockLive(page, body);
  await page.goto('/streaming/live');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Streaming' })).toBeVisible();
  return live;
}

for (const theme of ['light', 'dark'] as const) {
  for (const [label, width, height] of [['desktop', 1440, 900], ['phone', 390, 844]] as const) {
    test(`/live reads cleanly in ${theme} at ${label} width, with no axe violations`, async ({ page }) => {
      await open(page, { width, height, theme });
      await expect(page.locator('.g-live-hero')).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Upcoming' })).toBeVisible();
      const results = await new AxeBuilder({ page }).include('main').analyze();
      expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, 'no horizontal page scroll').toBeLessThanOrEqual(0);
      await page.screenshot({ fullPage: false, path: `test-results/visual/live-${theme}-${label}.png` });
    });
  }
}

test('nothing animates under reduced motion, and the live dot pulses when motion is allowed', async ({ page }) => {
  await open(page, { motion: 'reduce' });
  const running = () => page.evaluate(() => document.getAnimations().filter((animation) => animation.playState === 'running').length);
  expect(await running()).toBe(0);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect.poll(running).toBeGreaterThan(0);
  const animated = await page.evaluate(() => [...new Set(document.getAnimations().map((animation) => ((animation as CSSAnimation).animationName)))].filter(Boolean));
  expect(animated).toEqual(['g-live']);
});

test('no red anywhere on /live', async ({ page }) => {
  await open(page);
  const reds = await page.evaluate(() => [...document.querySelectorAll('main *')].filter((element) => {
    const style = getComputedStyle(element);
    return [style.color, style.backgroundColor, style.borderColor].some((value) => /rgba?\((19[0-9]|2[0-5][0-9]), (2[0-9]|3[0-9]|4[0-9]), (5[0-9]|6[0-9]|7[0-9])/.test(value));
  }).length);
  expect(reds).toBe(0);
});

test('a keyboard-only walk of /live: hero, then rails, then Record, then back', async ({ page }) => {
  await open(page);
  await page.getByRole('heading', { level: 1, name: 'Streaming' }).focus();
  await page.keyboard.press('ArrowDown');
  // The Streaming page's h1 sits above the embedded screen, so Down reaches the nearest stop, which is in the hero (Record or Watch).
  await expect(page.locator('.g-live-hero :focus')).toHaveCount(1);
  for (let step = 0; step < 8 && !(await page.evaluate(() => Boolean(document.activeElement?.closest('section.g-rail .g-remote-card')))); step += 1) await page.keyboard.press('ArrowDown');
  const firstRail = await page.evaluate(() => document.activeElement?.closest('section.g-rail')?.getAttribute('data-rail-key'));
  expect(firstRail).toBeTruthy();
  const index = await page.evaluate(() => {
    const card = document.activeElement?.closest('.g-remote-card');
    return card ? [...(card.parentElement?.children ?? [])].indexOf(card) : -1;
  });
  expect(index).toBeGreaterThanOrEqual(0);
  const cardAt = (at: number) => page.locator(`section.g-rail[data-rail-key="${firstRail}"] .g-remote-card`).nth(at);
  await page.keyboard.press('ArrowRight');
  await expect(cardAt(index + 1).locator('button.g-still')).toBeFocused();
  await page.keyboard.press('Tab'); // the card's ⋯ art menu is a Tab stop between the still and Record
  await expect(cardAt(index + 1).getByRole('button', { name: /^More options for / })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(cardAt(index + 1).locator('.g-remote-action')).toBeFocused();
  await page.keyboard.press('ArrowDown');
  const next = await page.evaluate(() => document.activeElement?.closest('section.g-rail, .g-schedule')?.getAttribute('data-rail-key') ?? 'schedule');
  expect(next).not.toBe(firstRail);
  await page.keyboard.press('ArrowUp');
  expect(await page.evaluate(() => document.activeElement?.closest('section.g-rail')?.getAttribute('data-rail-key'))).toBe(firstRail);
  await cardAt(index + 1).locator('button.g-still').focus(); // Up lands on the column's still or its Record stop; Enter must open, so start from the still
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/watch\?url=/);
  await page.goBack();
  await expect(page.getByRole('heading', { level: 1, name: 'Streaming' })).toBeVisible();
});

test('arrow keys reach Recordings and See all, Enter opens the wall and Escape leaves it', async ({ page }) => {
  await open(page, { body: liveSnapshotBody({ hero: snapshot.hero, items: liveItems(60) }) }); // 30 a category: past the 24 a rail shows, so See all is offered
  await page.locator('.g-live-hero button.g-live-watch').focus();
  await page.keyboard.press('ArrowUp');
  await expect(page.getByRole('link', { name: 'Recordings →' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  // Down from the schedule enters the first rail at its header, where See all is the only stop.
  for (let step = 0; step < 8 && !(await page.evaluate(() => Boolean(document.activeElement?.closest('section.g-rail')))); step += 1) await page.keyboard.press('ArrowDown');
  const rail = await page.evaluate(() => document.activeElement?.closest('section.g-rail')?.getAttribute('data-rail-key'));
  expect(rail).toBeTruthy();
  await expect(page.locator(`section.g-rail[data-rail-key="${rail}"] .g-rail-all`)).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/streaming\/live\?rail=/);
  await expect(page.locator('.g-rail-wall')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/\/streaming\/live$/);
  await expect(page.locator('.g-live-hero')).toBeVisible();
});

test('the Recordings link keeps focus and the page never errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page);
  const link = page.getByRole('link', { name: 'Recordings →' });
  await link.focus();
  await expect(link).toBeFocused();
  await expect(link).toHaveAttribute('href', '/library/recordings');
  expect(errors).toEqual([]);
});

test('a 30 s refresh keeps the focused card in place, and shows it ENDED when it left', async ({ page }) => {
  await page.clock.install();
  const live = await open(page);
  const card = page.locator('section.g-rail[data-rail-key="gaming"] .g-remote-card').nth(1);
  await card.locator('button.g-still').focus();
  const key = await card.getAttribute('data-remote-key');
  const box = await card.boundingBox();
  live.set({ ...snapshot, items: snapshot.items.filter((item) => item.webpage_url !== key) });
  await page.clock.runFor(30_000);
  await expect.poll(() => live.requests.length).toBeGreaterThan(1);
  await expect(card.locator('button.g-still')).toBeFocused();
  await expect(card.locator('.g-live')).toHaveText('ENDED');
  expect(await card.boundingBox()).toEqual(box);
});

test('counts show per rail and overall; a See all wall keeps loading to its last page without moving', async ({ page }) => {
  await open(page);
  await expect(page.getByText(/12,400 live now/).first()).toBeVisible();
  await expect(page.locator('section.g-rail[data-rail-key="gaming"] .g-rail-head')).toContainText('1,240 live');
  await page.locator('section.g-rail[data-rail-key="gaming"] .g-rail-all').click();
  await expect(page).toHaveURL(/\?rail=gaming/);
  const cards = page.locator('.g-rail-wall .g-remote-card');
  await expect(cards).toHaveCount(52); // 12 snapshot cards plus the first page of 40
  await page.locator('.g-rail-more').scrollIntoViewIfNeeded();
  await expect(cards).toHaveCount(72); // plus the last 20, none doubled
  await expect(page.locator('.g-rail-more')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/\/streaming\/live$/);
});
