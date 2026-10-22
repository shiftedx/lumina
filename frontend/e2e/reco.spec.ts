import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { LIST_ID, pickedSnapshot, recoTitle, suppressionList } from '../src/test/recoFixtures';
import { homeMovie, mockHome } from './home-mock';
import { mockApi, signIn } from './lumina-mock';
import { mockPopular, mockPreview, mockReco, type RecoMock } from './reco-mock';

/** The surfaces over the mocked API (reco-mock.ts). */

async function openHome(page: Page, options: RecoMock = {}, viewport = { width: 1440, height: 900 }) {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  await mockHome(page);
  const log = await mockReco(page, options);
  const homeRequests = { count: 0 };
  page.on('request', (request) => { if (new URL(request.url()).pathname === '/api/discovery/home') homeRequests.count += 1; });
  await page.goto('/');
  await signIn(page);
  return { log, homeRequests };
}
const picked = (page: Page) => page.locator('[data-shelf-section="picked_for_you"]');

test.describe('Show fewer and Settings', () => {
  test('Show fewer from a channel says so, and Settings lists it with when it is back to normal', async ({ page }) => {
    const { log } = await openHome(page);
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    await page.getByRole('menuitem', { name: /^Show fewer from / }).click();
    await expect(picked(page).getByText(/^Showing fewer from /)).toBeVisible();
    expect(log.suppressed[0]).toMatchObject({ scope: 'fewer' });
    await page.goto('/settings/discovery?page=hidden');
    await expect(page.getByRole('heading', { level: 3, name: 'Showing fewer: channel' })).toBeVisible();
    await expect(page.getByText(/^Back to normal on /)).toBeVisible();
  });
});

test.describe('Explore', () => {
  test('For you sits under the live teaser with reasons, categories follow the member\'s order, and a rail card has a menu without a reason', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApi(page, { sidebar_collapsed: false });
    await mockHome(page);
    await mockPopular(page);
    await mockReco(page);
    await page.goto('/streaming');
    await signIn(page);
    await expect(page.getByRole('heading', { name: 'For you' })).toBeVisible();
    await expect(page.getByText('Because you finished Harbor walk at dawn')).toHaveCount(0);
    const headings = await page.getByRole('heading', { level: 2 }).allTextContents();
    expect(headings.findIndex((text) => text.startsWith('Music'))).toBeLessThan(headings.findIndex((text) => text.startsWith('Gaming')));
    await page.getByRole('button', { name: 'More options for Music video one' }).click();
    await expect(page.getByRole('menuitem')).toHaveCount(7);
    await expect(page.locator('.reco-reason')).toHaveCount(0);
  });
});

/** I1: the menu list is not clipped by the horizontal rail that holds its card. The last item is on screen and is what a click there would hit. */
async function expectMenuFullyVisible(page: Page) {
  const menu = page.getByRole('menu');
  await expect(menu).toBeVisible();
  const hit = await menu.evaluate((list) => {
    const box = list.getBoundingClientRect();
    const last = list.querySelector('[role="menuitem"]:last-child')!.getBoundingClientRect();
    const top = document.elementFromPoint(last.left + last.width / 2, last.top + last.height / 2);
    // A top-layer panel, so no scroller clips it (the rail cannot scroll it into view either), and nothing around it scrolled.
    const topLayer = list.matches(':popover-open');
    const scrolled = Array.from(document.querySelectorAll('.h-row, .g-rail-scroller, .t-row')).some((row) => row.scrollTop !== 0);
    return { inside: box.top >= 0 && box.left >= 0 && box.bottom <= window.innerHeight && box.right <= window.innerWidth, lastHit: Boolean(top && list.contains(top)), topLayer, scrolled };
  });
  expect(hit).toEqual({ inside: true, lastHit: true, topLayer: true, scrolled: false });
}

test.describe('The menu list in a horizontal rail', () => {
  test('is fully visible and unclipped in a Home row', async ({ page }) => {
    await openHome(page);
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    await expectMenuFullyVisible(page);
  });

  test('is fully visible and unclipped in an Explore rail', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApi(page, { sidebar_collapsed: false });
    await mockHome(page);
    await mockPopular(page);
    await mockReco(page);
    await page.goto('/streaming');
    await signIn(page);
    await page.getByRole('button', { name: 'More options for Music video one' }).click();
    await expectMenuFullyVisible(page);
  });

  test('is a borderless top-layer panel with a paper background in a Home row', async ({ page }) => {
    await openHome(page);
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    const style = await page.getByRole('menu').evaluate((list) => {
      const css = getComputedStyle(list);
      return { position: css.position, filled: css.backgroundColor !== 'rgba(0, 0, 0, 0)', border: css.borderTopWidth, isMenuClass: list.classList.contains('g-menu') };
    });
    expect(style).toEqual({ position: 'fixed', filled: true, border: '0px', isMenuClass: true });
  });
});

test.describe('The menu list on a 375 px phone', () => {
  test('stays 8px inside both edges for the first-column card', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await mockApi(page, { sidebar_collapsed: false });
    await mockHome(page);
    await mockPopular(page);
    await mockReco(page);
    await page.goto('/streaming');
    await signIn(page);
    // A long channel name widens the list to its 280px cap; force that width so the card's right edge (~285px) would put it past x = 0 without the clamp.
    await page.addStyleTag({ content: '.g-menu { min-width: 340px; max-width: 340px; }' });
    await page.getByRole('button', { name: /^More options for / }).first().click();
    const box = await page.getByRole('menu').evaluate((list) => { const r = list.getBoundingClientRect(); return { left: r.left, right: window.innerWidth - r.right }; });
    expect(box.left).toBeGreaterThanOrEqual(8);
    expect(box.right).toBeGreaterThanOrEqual(8);
  });
});

test.describe('Tab from an open menu', () => {
  test('lands on the next focusable after the menu button, not outside the content', async ({ page }) => {
    await openHome(page);
    const button = picked(page).getByRole('button', { name: /^More options for / }).first();
    await button.click();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('menu')).toHaveCount(0);
    const next = await page.evaluate(() => ({ inPicked: Boolean(document.activeElement?.closest('[data-shelf-section="picked_for_you"]')), label: document.activeElement?.getAttribute('aria-label') ?? '' }));
    expect(next.inPicked).toBe(true);
    expect(next.label).not.toBe(await button.getAttribute('aria-label'));
  });
});

test.describe('Following a channel you had shown fewer of', () => {
  test('the confirmation says we will recommend it again, and the suppression list is re-read', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApi(page, { sidebar_collapsed: false });
    await mockPreview(page);
    await mockReco(page, { suppressions: suppressionList({
      fewer: [{ id: 'f1', scope: 'fewer', target_key: 'harbor films', title: null, channel_name: 'Harbor Films', source: 'youtube', created_at: '2026-09-20T10:00:00Z', recovers_at: '2027-02-07T10:00:00Z' }],
    }) });
    await page.route('**/api/automations', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      const body = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'auto-1', user_id: 'member-1', created_at: '2026-09-30T10:00:00Z', updated_at: '2026-09-30T10:00:00Z', ...body }) });
    });
    let suppressionReads = 0;
    page.on('request', (request) => { if (request.method() === 'GET' && new URL(request.url()).pathname === '/api/discovery/suppressions') suppressionReads += 1; });
    await page.goto(`/watch?url=${encodeURIComponent('https://www.youtube.com/watch?v=lumina00001')}`);
    await signIn(page);
    await expect.poll(() => suppressionReads).toBeGreaterThanOrEqual(1);
    const before = suppressionReads;
    await page.getByRole('button', { name: /^Follow/ }).first().click();
    await expect(page.getByText("Following Harbor Films. We'll recommend Harbor Films again.")).toBeVisible();
    await expect.poll(() => suppressionReads).toBeGreaterThan(before);
  });

});

test.describe('Events', () => {
  test('a card seen for a second is reported, and what is left goes by beacon with the token in the body on pagehide', async ({ page }) => {
    const { log } = await openHome(page);
    await expect(picked(page).getByRole('button', { name: /^More options for / }).first()).toBeVisible();
    await page.waitForTimeout(1_300);
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    await expect.poll(() => log.beacons).toBeGreaterThanOrEqual(1);
    expect(log.events.some((event) => event.kind === 'impression' && event.list_id === LIST_ID)).toBe(true);
    expect(log.events.every((event) => event.age_ms >= 0 && event.age_ms <= 900_000)).toBe(true);
  });

  test('opening a recommendation reports an open', async ({ page }) => {
    const { log } = await openHome(page);
    await picked(page).locator('[data-remote-key] button[data-focus-item]').first().click();
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    await expect.poll(() => log.events.some((event) => event.kind === 'open' && event.list_id === LIST_ID)).toBe(true);
  });
});

test.describe('Accessibility', () => {
  const serious = (results: Awaited<ReturnType<AxeBuilder['analyze']>>) => results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(', ')}`);

  for (const scheme of ['dark', 'light'] as const) {
    test(`Home with a menu open has no serious axe findings (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await openHome(page);
      await picked(page).getByRole('button', { name: /^More options for / }).first().click();
      await expect(page.getByRole('menu')).toBeVisible();
      expect(serious(await new AxeBuilder({ page }).analyze())).toEqual([]);
    });
  }

  test('Explore, the watch page and a title page with a menu open have no serious axe findings', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mockApi(page, { sidebar_collapsed: false });
    await mockHome(page, { newest: [homeMovie(1)] });
    await mockPopular(page);
    await mockPreview(page);
    await mockReco(page, { similar: [recoTitle(1, 0), recoTitle(2, 1)], upNext: pickedSnapshot(4) });
    await page.goto('/streaming');
    await signIn(page);
    await page.getByRole('button', { name: /^More options for Picked for you 1/ }).click();
    expect(serious(await new AxeBuilder({ page }).analyze())).toEqual([]);
    await page.keyboard.press('Escape');

    await page.goto(`/watch?url=${encodeURIComponent('https://www.youtube.com/watch?v=lumina00001')}`);
    await page.getByRole('region', { name: 'Up next' }).getByRole('button', { name: /^More options for / }).first().click();
    expect(serious(await new AxeBuilder({ page }).analyze())).toEqual([]);

    await page.goto('/title/home-movie-1');
    await page.getByRole('region', { name: 'More like this' }).getByRole('button', { name: /^More options for / }).first().click();
    expect(serious(await new AxeBuilder({ page }).analyze())).toEqual([]);
  });
});
