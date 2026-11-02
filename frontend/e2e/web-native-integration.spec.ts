import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';
import { mockGalleryWall } from './gallery-mock';
import { mockGalleryTitle } from './gallery-title-mock';
import { albumSummary, movieSummary, titleDetail } from '../src/test/galleryFixtures';

/** Cross-surface shell contracts against production routes, with synthetic visible media. */
for (const width of [1440, 768, 390, 320]) {
  test(`on-demand navigation and contextual movie search at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 680 ? 844 : 900 });
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    await mockApi(page, {});
    await mockGalleryWall(page, { total: 30, artwork: true });
    await page.goto('/video/movies');
    await signIn(page);
    await expect(page.getByRole('heading', { level: 1, name: 'Movies' })).toBeAttached();
    if (width < 680) await expect(page.getByRole('navigation', { name: 'Library', exact: true }).getByRole('link', { name: 'YouTube', exact: true })).toBeInViewport({ ratio: 1 });
    const dialog = page.getByRole('dialog', { name: 'Navigation', exact: true });
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Search Lumina/ })).toHaveCount(0);
    await page.keyboard.press('Control+k');
    await expect(page.locator('main input[type="search"]')).toBeFocused();
    await expect(page.getByRole('dialog', { name: 'Search Lumina' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    await expect(dialog).toBeVisible();
    if (width < 680) await expect(dialog.getByRole('button', { name: 'Add a link', exact: true })).toBeVisible();
    for (const name of ['Movies', 'Shows', 'Music', 'Books', 'Photos', 'Streaming', 'Requests', 'Downloads', 'My Settings']) await expect(dialog.getByRole('button', { name, exact: true })).toBeVisible();
    const before = await page.locator('main').boundingBox();
    await page.screenshot({ path: `../output/web-native/menu-${width}.png` });
    await dialog.getByRole('button', { name: 'Shows', exact: true }).click();
    await expect(page).toHaveURL(/\/video\/shows$/);
    await expect(dialog).toHaveCount(0);
    expect((await page.locator('main').boundingBox())?.width).toBe(before?.width);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../output/web-native/shows-${width}.png` });
    await page.getByRole('button', { name: 'Open navigation', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(dialog.getByRole('button', { name: 'Home', exact: true })).toBeFocused();
    await page.mouse.move(1000, 850);
    await page.waitForTimeout(750);
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Open navigation', exact: true })).toBeFocused();
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    await dialog.hover();
    await page.mouse.move(width - 1, 800);
    await expect(dialog).toHaveCount(0, { timeout: 2000 });
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    await dialog.getByRole('button', { name: 'Close menu', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Open navigation', exact: true })).toBeFocused();
    if (width < 680) await expect(page.getByRole('navigation', { name: 'Mobile primary navigation' }).getByRole('button', { name: 'Browse', exact: true })).toBeVisible();
  });
}

for (const width of [1440, 390, 320]) {
  test(`Music remembers the member view while explicit album routes and filters win at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    const preferences = { music_browse: { view: 'artists' }, theme: 'dark' };
    await mockApi(page, preferences);
    await mockGalleryWall(page, { total: 12, albums: 12, artists: 6 });
    await page.route('**/api/music/albums/recently-played?*', (route) => route.fulfill({ json: { items: [albumSummary('album-0000', { name: 'A Title 0' })] } }));
    await page.goto('/audio');
    await signIn(page);
    await expect(page.getByRole('button', { name: 'Artists', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('searchbox', { name: 'Search music', exact: true })).toBeVisible();
    await page.locator('.audio-tools > summary').click();
    await expect(page.getByRole('link', { name: 'Playlists & mixes', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Offline listening', exact: true })).toBeVisible();
    await page.locator('.audio-tools > summary').click();
    await expect(page.getByRole('button', { name: /^Search Lumina/ })).toHaveCount(0);
    await page.getByRole('button', { name: 'Albums', exact: true }).click();
    await expect(page).toHaveURL(/\/audio\?view=albums$/);
    await expect.poll(() => preferences.music_browse.view).toBe('albums');
    await expect(page.getByRole('heading', { name: 'Recently added', exact: true })).toBeVisible();
    await page.screenshot({ path: `../output/web-native/music-browse-${width}.png`, fullPage: true });
    await page.getByText('All albums', { exact: true }).click();
    await expect(page).toHaveURL(/catalog=1/);
    await expect(page.getByRole('combobox', { name: 'Sort', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Artists', exact: true }).click();
    await expect.poll(() => preferences.music_browse.view).toBe('artists');
    await page.goto('/audio?view=albums');
    await expect(page.getByRole('button', { name: 'Albums', exact: true })).toHaveAttribute('aria-pressed', 'true');
    expect(preferences.music_browse.view).toBe('artists');
    await page.goto('/audio?sort=name');
    await expect(page.getByRole('button', { name: 'Albums', exact: true })).toHaveAttribute('aria-pressed', 'true');
    const overflow = await page.evaluate(() => [...document.querySelectorAll('body *')].map((node) => ({ class: node.className, right: node.getBoundingClientRect().right })).filter((node) => node.right > innerWidth + 1).slice(0, 15));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), JSON.stringify(overflow)).toBe(true);
    const requested = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/search' && new URL(request.url()).searchParams.get('q') === 'Album');
    await page.getByRole('searchbox', { name: 'Search music', exact: true }).fill('Album');
    await page.getByRole('searchbox', { name: 'Search music', exact: true }).press('Enter');
    expect(new URL((await requested).url()).searchParams.get('scope')).toBe('music');
    expect(preferences.music_browse.view).toBe('artists');
  });
}

for (const width of [1440, 390]) {
  test(`movie artwork details restores its exact hero origin at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    await mockApi(page, {});
    await mockGalleryTitle(page, { detail: titleDetail(movieSummary('movie-0000')) });
    await mockGalleryWall(page, { total: 30, artwork: true });
    await page.goto('/video/movies'); await signIn(page);
    const details = page.locator('[data-browse-details]');
    await expect(details).toBeVisible();
    const stage = page.locator('.g-browse-hero');
    expect((await stage.boundingBox())?.y).toBe(0);
    expect((await stage.boundingBox())?.width).toBe(width);
    await expect.poll(() => stage.locator('img').last().evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1);
    expect(await stage.getByRole('heading').evaluate((heading) => getComputedStyle(heading).fontStyle)).toBe('normal');
    await page.screenshot({ path: `../output/web-native/movies-${width}.png` });
    await details.click();
    await expect(page).toHaveURL(/\/video\/title\/movie-0000$/);
    await expect.poll(() => page.locator('.t-hero img.g-art-image').first().evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1);
    await page.screenshot({ path: `../output/web-native/movie-detail-${width}.png` });
    await page.goBack();
    await expect(details).toBeFocused({ timeout: 3000 });
    expect(await page.evaluate(() => window.scrollY)).toBeLessThan(10);
    if (width < 680) {
      await page.locator('.g-poster').first().focus();
      await page.keyboard.press('ArrowDown');
      const card = await page.locator('.g-poster:focus').boundingBox();
      const navigation = await page.locator('.g-mobile-tabs').boundingBox();
      expect(card!.y + card!.height).toBeLessThanOrEqual(navigation!.y);
      await details.focus();
      await page.evaluate(() => window.scrollTo(0, 0));
    }
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    const overflow = await page.evaluate(() => [...document.querySelectorAll('body *')].map((node) => ({ tag: node.tagName, class: node.className, right: node.getBoundingClientRect().right, width: node.getBoundingClientRect().width })).filter((node) => node.right > innerWidth + 1).slice(0, 20));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), JSON.stringify(overflow)).toBe(true);
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
    await details.focus(); await expect(details).toBeFocused();
    await page.screenshot({ path: `../output/web-native/movies-zoom-contrast-${width}.png` });
  });
}

for (const width of [1440, 768, 390, 320]) for (const lens of ['movies', 'shows'] as const) {
  test(`library header stays at the top without a gap or overlapping labels: ${lens} ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    await mockApi(page, {}); await mockGalleryWall(page, { total: 120, artwork: true });
    await page.goto(`/video/${lens}`); await signIn(page);
    const name = lens === 'movies' ? 'Movies' : 'Shows';
    await expect(page.getByRole('heading', { level: 1, name })).toBeAttached();
    const header = page.locator('.g-topbar');
    await expect(header.locator('.g-topbar-context')).toHaveText(name);
    const top = await header.boundingBox();
    const brand = await header.getByRole('link', { name: 'Lumina home' }).boundingBox();
    const context = await page.locator('.g-browse-context').boundingBox();
    expect.soft(context!.y, 'library lenses clear the global logo and menu').toBeGreaterThanOrEqual(top!.y + top!.height);
    expect.soft(brand!.x + brand!.width, 'library title starts after the logo').toBeLessThanOrEqual((await header.locator('.g-topbar-context').boundingBox())!.x);
    await page.evaluate(() => window.scrollTo(0, 1000));
    await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(900);
    const scrolledHeader = await header.boundingBox();
    const toolbar = await page.locator('.g-wall > .g-toolbar').boundingBox();
    expect.soft(scrolledHeader!.y, 'the whole navigation stays at the viewport top').toBe(0);
    expect.soft(Math.abs(toolbar!.y - scrolledHeader!.y - scrolledHeader!.height), 'filters follow the pinned header with no vacant band').toBeLessThanOrEqual(16);
    await page.screenshot({ path: `../output/nav-scroll/${lens}-${width}-scrolled.png` });
    await page.goto(`/video/${lens}?q=Title`);
    await expect(page.locator('.g-browse-hero')).toHaveCount(0);
    await expect(header.locator('.g-topbar-context')).toHaveText(name);
    const heading = await page.getByRole('heading', { level: 1, name }).boundingBox();
    const logo = await header.getByRole('link', { name: 'Lumina home' }).boundingBox();
    expect(heading!.width <= 1 || heading!.y >= logo!.y + logo!.height, 'filtered library title does not overlap the logo').toBe(true);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `../output/nav-scroll/${lens}-${width}-filtered.png` });
  });
}
