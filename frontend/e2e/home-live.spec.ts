import { expect, test, type Page } from '@playwright/test';

import type { LiveSnapshot } from '../src/types';
import { liveEntry, mockHome } from './home-mock';
import { mockApi, signIn } from './lumina-mock';

/** The Live shelf's order, refresh cadence, notices and reduced motion (mocked API). */

async function openHome(page: Page, live: Partial<LiveSnapshot>) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, { sidebar_collapsed: false });
  const requests = await mockHome(page, { live });
  await page.goto('/');
  await signIn(page);
  await expect(page.getByRole('region', { name: 'Live now' })).toBeVisible();
  return requests;
}
const livePolls = (requests: string[]) => requests.filter((request) => request.startsWith('GET /api/discovery/live')).length;
const setHidden = (page: Page, hidden: boolean) => page.evaluate((value) => {
  Object.defineProperty(document, 'hidden', { configurable: true, value });
  document.dispatchEvent(new Event('visibilitychange'));
}, hidden);

test('followed channels live now come first, then popular live, each stream once', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openHome(page, {
    hero: [liveEntry('mine')],
    items: [liveEntry('mine'), liveEntry('big', { source: 'youtube', source_label: 'YouTube', webpage_url: 'https://www.youtube.com/watch?v=big' })],
  });
  const shelf = page.getByRole('region', { name: 'Live now' });
  await expect(shelf.locator('.h-card')).toHaveCount(2);
  await expect(shelf.locator('.h-card').first().locator('[data-focus-item]')).toHaveAccessibleName('Live: Live stream mine, Channel mine, 12,400 watching on Twitch, a channel you follow');
  await expect(shelf.locator('.h-card').nth(1)).toContainText('YOUTUBE');
  await expect(shelf.locator('.g-live').first()).toHaveText('LIVE');
  await expect(shelf.getByRole('button', { name: 'See all' })).toBeVisible();
});

test('refreshes every 60 s while visible, and never while the tab is hidden', async ({ page }) => {
  await page.clock.install();
  const requests = await openHome(page, { items: [liveEntry('a')] });
  // E-I4: the shelf's region is visible before its own fetch is logged (NearGate renders the placeholder region
  // first), so sampling the baseline here without waiting can catch it mid-request and flake under load.
  await expect.poll(() => livePolls(requests)).toBeGreaterThanOrEqual(1);
  const first = livePolls(requests);
  await page.clock.runFor(60_000);
  await expect.poll(() => livePolls(requests)).toBe(first + 1);
  await setHidden(page, true);
  await page.clock.runFor(180_000);
  await page.waitForTimeout(300);
  expect(livePolls(requests)).toBe(first + 1);
  await setHidden(page, false);
  await expect.poll(() => livePolls(requests)).toBe(first + 2);
});

test('a provider notice shows under the heading and is not re-announced by the next poll', async ({ page }) => {
  await page.clock.install();
  await openHome(page, { items: [liveEntry('a')], twitch_available: false });
  const notice = page.getByRole('region', { name: 'Live now' }).getByText('Twitch streams are temporarily unavailable.');
  await expect(notice).toBeVisible();
  expect(await notice.evaluate((node) => Boolean(node.closest('[aria-live], [role="status"], [role="alert"]')))).toBe(false);
  await notice.evaluate((node) => { (node as HTMLElement & { __kept?: boolean }).__kept = true; });
  await page.clock.runFor(60_000);
  await page.waitForTimeout(300);
  // The same node survives the refresh: nothing was re-inserted for a screen reader to read again.
  expect(await notice.evaluate((node) => (node as HTMLElement & { __kept?: boolean }).__kept === true)).toBe(true);
});

test('the LIVE dot pulses only when motion is allowed', async ({ page }) => {
  await openHome(page, { items: [liveEntry('a')] });
  const dot = page.locator('[data-shelf-section="live"] .g-live-dot').first();
  await expect(dot).toBeVisible();
  expect(await dot.evaluate((node) => getComputedStyle(node).animationName)).toBe('g-live');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(() => dot.evaluate((node) => getComputedStyle(node).animationName)).toBe('none');
});
