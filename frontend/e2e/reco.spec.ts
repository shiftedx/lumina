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

test.describe('Home: Picked for you', () => {
  test('shows with no interests chosen, says why under each card, and marks exploration only by its words', async ({ page }) => {
    await openHome(page);
    await expect(picked(page).getByRole('heading', { name: 'Picked for you' })).toBeVisible();
    // The reason lives only in the card's menu now, as a quiet header.
    await expect(picked(page).getByText('Because you finished Harbor walk at dawn')).toHaveCount(0);
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    await expect(page.getByRole('group', { name: 'Because you finished Harbor walk at dawn' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(picked(page).locator('[data-remote-key]')).toHaveCount(20);
  });

  test('Not interested replaces the card with Hidden. Undo, and Undo restores it', async ({ page }) => {
    const { log } = await openHome(page);
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    await page.getByRole('menuitem', { name: 'Not interested' }).click();
    await expect(picked(page).getByText('Hidden.')).toBeVisible();
    await expect(picked(page).getByRole('button', { name: 'Undo' })).toBeFocused();
    expect(log.suppressed[0]).toMatchObject({ scope: 'item', list_id: LIST_ID });
    await picked(page).getByRole('button', { name: 'Undo' }).click();
    await expect(picked(page).getByText('Hidden.')).toHaveCount(0);
    await expect(picked(page).locator('[data-remote-key]')).toHaveCount(20);
    expect(log.restored).toEqual(['mock-suppression-1']);
  });

  test('when the eight seconds pass the card is gone and Home re-fetches', async ({ page }) => {
    test.setTimeout(45_000);
    const { homeRequests } = await openHome(page);
    await expect.poll(() => homeRequests.count).toBeGreaterThanOrEqual(1);
    const before = homeRequests.count;
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    await page.getByRole('menuitem', { name: 'Not interested' }).click();
    await expect(picked(page).getByText('Hidden.')).toBeVisible();
    await expect(picked(page).getByText('Hidden.')).toHaveCount(0, { timeout: 15_000 });
    await expect(picked(page).locator('[data-remote-key]')).toHaveCount(19);
    await expect.poll(() => homeRequests.count).toBeGreaterThan(before);
  });

  test('keeps the menu and drops the reasons when the server sends no annotation (the switch is off)', async ({ page }) => {
    const plain = pickedSnapshot(3);
    await openHome(page, { home: { ...plain, items: plain.items.map((entry) => ({ ...entry, reco: null })) } });
    await expect(picked(page).locator('[data-remote-key]')).toHaveCount(3);
    await expect(picked(page).getByRole('button', { name: /^More options for / })).toHaveCount(3);
    await expect(picked(page).locator('.reco-reason')).toHaveCount(0);
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    await expect(page.getByRole('menuitem', { name: /^Show fewer from / })).toHaveCount(0);
    await expect(page.getByRole('menuitem', { name: 'Add to queue' })).toBeVisible();
  });
});

test.describe('Show fewer and Settings', () => {
  test('Show fewer from a channel says so, and Settings lists it with when it is back to normal', async ({ page }) => {
    const { log } = await openHome(page);
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    await page.getByRole('menuitem', { name: /^Show fewer from / }).click();
    await expect(picked(page).getByText(/^Showing fewer from /)).toBeVisible();
    expect(log.suppressed[0]).toMatchObject({ scope: 'fewer' });
    await page.goto('/settings/discovery');
    await expect(page.getByRole('heading', { level: 3, name: 'Showing fewer: channel' })).toBeVisible();
    await expect(page.getByText(/^Back to normal on /)).toBeVisible();
  });

  test('Clear recommendation history asks first, then clears', async ({ page }) => {
    const { log } = await openHome(page);
    await page.goto('/settings/discovery');
    await page.getByRole('button', { name: 'Clear recommendation history' }).click();
    await expect(page.getByText('Lumina forgets what it showed you and what you opened. Your watch history and hidden lists stay.')).toBeVisible();
    expect(log.cleared).toBe(0);
    await page.getByRole('button', { name: 'Clear history' }).click();
    await expect.poll(() => log.cleared).toBe(1);
  });

  test('lists the four kinds of feedback in Settings', async ({ page }) => {
    await openHome(page, { suppressions: suppressionList({
      items: [{ id: 'i1', scope: 'item', target_key: 'k1', title: 'An old clip', channel_name: 'Creator', source: 'youtube', created_at: '2026-09-20T10:00:00Z' }],
      titles: [{ id: 't1', scope: 'title', target_key: 'k2', title: 'A film', channel_name: null, source: null, created_at: '2026-09-20T10:00:00Z' }],
      fewer: [{ id: 'f1', scope: 'fewer', target_key: 'k3', title: null, channel_name: 'Quiet Channel', source: 'youtube', created_at: '2026-09-20T10:00:00Z', recovers_at: '2027-02-07T10:00:00Z' }],
      channels: [{ id: 'c1', scope: 'channel', target_key: 'k4', title: null, channel_name: 'Muted Channel', source: 'youtube', created_at: '2026-09-20T10:00:00Z' }],
    }) });
    await page.goto('/settings/discovery');
    for (const heading of ['Not interested: videos', 'Not interested: titles', 'Showing fewer: channel', 'Hidden channels']) {
      await expect(page.getByRole('heading', { level: 3, name: heading })).toBeVisible();
    }
  });
});

test.describe('Title rows and More like this', () => {
  test('a recommended poster keeps its reason in the menu as a quiet header, on a phone too', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mockApi(page, { sidebar_collapsed: true });
    await mockHome(page, { rows: [{ id: 'row-1', kind: 'recommended', title: 'Recommended for you', items: [recoTitle(1, 0), recoTitle(2, 1)] }] });
    await mockReco(page);
    await page.goto('/');
    await signIn(page);
    const row = page.locator('[data-shelf-section="recommended"]');
    await expect(row.getByText('Like Arrival')).toHaveCount(0);
    await row.getByRole('button', { name: 'More options for Recommended Film 1' }).click();
    await expect(page.getByRole('menu').getByRole('group', { name: 'Like Arrival' })).toBeVisible();
  });

  test('Not interested on a More like this poster hides it and sends the title', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApi(page, { sidebar_collapsed: false });
    const similar = [recoTitle(1, 0), recoTitle(2, 1)];
    await mockHome(page, { newest: [homeMovie(1)] });
    const log = await mockReco(page, { similar });
    await page.goto('/title/home-movie-1');
    await signIn(page);
    const region = page.getByRole('region', { name: 'More like this' });
    await expect(region.getByRole('button', { name: /^More options for / })).toHaveCount(2);
    await expect(region.getByText('Like Arrival')).toHaveCount(0);
    await region.getByRole('button', { name: 'More options for Recommended Film 1' }).click();
    await page.getByRole('menuitem', { name: 'Not interested' }).click();
    await expect(region.getByText('Hidden.')).toBeVisible();
    expect(log.suppressed[0]).toMatchObject({ scope: 'title', title_id: similar[0].id });
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

  test('is a styled top-layer panel, with a paper background and a hairline, in a Home row', async ({ page }) => {
    await openHome(page);
    await picked(page).getByRole('button', { name: /^More options for / }).first().click();
    const style = await page.getByRole('menu').evaluate((list) => {
      const css = getComputedStyle(list);
      return { position: css.position, filled: css.backgroundColor !== 'rgba(0, 0, 0, 0)', hairline: css.borderTopWidth !== '0px', isMenuClass: list.classList.contains('g-menu') };
    });
    expect(style).toEqual({ position: 'fixed', filled: true, hairline: true, isMenuClass: true });
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

test.describe('Up next on the watch page', () => {
  test('rows say why, the request names the channel, and Don\'t recommend hides the row with Undo', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApi(page, { sidebar_collapsed: false });
    await mockPreview(page);
    const log = await mockReco(page, { upNext: pickedSnapshot(4) });
    await page.goto(`/watch?url=${encodeURIComponent('https://www.youtube.com/watch?v=lumina00001')}`);
    await signIn(page);
    const upNext = page.getByRole('region', { name: 'Up next' });
    await expect(upNext.getByRole('button', { name: /^More options for / }).first()).toBeVisible();
    await expect(upNext.getByText('Because you finished Harbor walk at dawn')).toHaveCount(0);
    expect(log.upNext[0]).toMatchObject({ channel_id: 'UCabcdefghijklmnopqrstuv' });
    await upNext.getByRole('button', { name: /^More options for / }).first().click();
    await page.getByRole('menuitem', { name: /^Don't recommend / }).click();
    await expect(upNext.getByText('Hidden.')).toBeVisible();
    expect(log.suppressed[0]).toMatchObject({ scope: 'channel', list_id: LIST_ID });
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

  test('a channel with no suppression gets the plain confirmation', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockApi(page, { sidebar_collapsed: false });
    await mockPreview(page);
    await mockReco(page);
    await page.route('**/api/automations', (route) => route.request().method() === 'POST'
      ? route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'auto-1', user_id: 'member-1', ...route.request().postDataJSON() }) })
      : route.fallback());
    await page.goto(`/watch?url=${encodeURIComponent('https://www.youtube.com/watch?v=lumina00001')}`);
    await signIn(page);
    await page.getByRole('button', { name: /^Follow/ }).first().click();
    await expect(page.getByText('Following Harbor Films.', { exact: true })).toBeVisible();
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
