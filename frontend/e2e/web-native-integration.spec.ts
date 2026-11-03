import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';
import { mockGalleryWall, mockLibrarySections } from './gallery-mock';
import { homeMovie, mockHome } from './home-mock';
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
    const dialog = page.getByRole('dialog', { name: 'Navigation', exact: true });
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Search Lumina/ })).toHaveCount(0);
    await page.keyboard.press('Control+k');
    await expect(page.locator('.g-header-search input[type="search"]')).toBeFocused();
    await expect(page.getByRole('dialog', { name: 'Search Lumina' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    await expect(dialog).toBeVisible();
    if (width < 680) await expect(dialog.getByRole('button', { name: 'Add a link', exact: true })).toBeVisible();
    for (const name of ['Movies', 'Shows', 'Anime', 'Recordings', 'Music', 'Books', 'Photos', 'Streaming', 'Requests', 'Downloads', 'My Settings']) await expect(dialog.getByRole('button', { name, exact: true })).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'All video', exact: true })).toHaveCount(0);
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
    await page.keyboard.press('Control+k');
    await expect(page.getByRole('searchbox', { name: 'Search music', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Music tools' }).click();
    await expect(page.getByRole('menuitem', { name: 'Playlists & mixes', exact: true })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Offline listening', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: /^Search Lumina/ })).toHaveCount(0);
    await page.getByRole('button', { name: 'Albums', exact: true }).click();
    await expect(page).toHaveURL(/\/audio\?view=albums$/);
    await expect.poll(() => preferences.music_browse.view).toBe('albums');
    await expect(page.getByRole('heading', { name: 'Recently added', exact: true })).toBeVisible();
    await page.screenshot({ path: `../output/web-native/music-browse-${width}.png`, fullPage: true });
    await expect(page.getByRole('heading', { name: 'All albums', exact: true })).toBeVisible();
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
    await page.keyboard.press('Control+k');
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

for (const scheme of ['dark', 'light'] as const) {
  for (const width of [1920, 1440, 390, 320]) {
    test(`legacy video opens the first configured movie experience with detail restoration in ${scheme} at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width < 680 ? 844 : 1080 });
      await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: scheme });
      await mockApi(page, {});
      await mockGalleryTitle(page, { detail: titleDetail(movieSummary('movie-0000')) });
      await mockGalleryWall(page, { total: 60, artwork: true });
      await mockLibrarySections(page, { movies: 60, shows: 60, anime: 12, youtube: 7, recordings: 0, albums: 0, artists: 0, saved_audio: 0 });
      await page.goto('/video'); await signIn(page);
      await expect(page).toHaveURL(/\/video\/movies$/);
      const stage = page.locator('.g-browse-hero');
      await expect(stage).toBeVisible();
      await expect.poll(() => stage.locator('img').last().evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1);
      await page.evaluate(() => window.scrollTo(0, 0));
      const bounds = await stage.boundingBox();
      const details = page.locator('[data-browse-details]');
      const header = await page.locator('.g-topbar').boundingBox();
      expect.soft(bounds!.y).toBe(0);
      expect.soft(bounds!.height, 'the dedicated category still begins with its artwork-forward hero').toBeGreaterThan(header!.height);
      await expect(page.locator('.g-topbar-context')).toHaveText('Movies');
      await expect(page.getByRole('navigation', { name: 'Library tools', exact: true }).getByRole('link', { name: 'Collections', exact: true })).toBeVisible();
      await expect(page.getByRole('navigation', { name: 'Library tools', exact: true }).getByRole('link', { name: 'All', exact: true })).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `../output/category-hero/movies-${scheme}-${width}.png` });
      await details.click();
      await expect(page).toHaveURL(/\/video\/title\/movie-0000$/);
      await expect(page.locator('.t-hero')).toBeVisible();
      await page.goBack();
      await expect(details).toBeFocused();
      expect(await page.evaluate(() => window.scrollY)).toBeLessThan(10);
    });
  }
}

for (const scheme of ['dark', 'light'] as const) {
  for (const surface of ['home', 'movies', 'anime'] as const) {
    for (const width of [1440, 390]) {
      test(`hero navigation reveals artwork until scrolling on ${surface} in ${scheme} at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
        await mockApi(page, {});
        await mockGalleryWall(page, { total: 60, artwork: true });
        if (surface === 'home') {
          await mockHome(page, { newest: Array.from({ length: 12 }, (_, index) => homeMovie(index)) });
          const artwork = readFileSync(new URL('./fixtures/open-films/sintel-backdrop.jpg', import.meta.url));
          await page.route('**/api/art/**', (route) => route.fulfill({ contentType: 'image/jpeg', body: artwork }));
        }
        await page.goto(surface === 'home' ? '/' : `/video/${surface}`); await signIn(page);
        await expect(page.locator(surface === 'home' ? '.h-hero' : '.g-browse-hero')).toBeVisible();
        const header = page.locator('.g-topbar');
        const opacity = () => header.evaluate((node) => {
          const color = getComputedStyle(node).backgroundColor;
          if (color.includes('/')) return Number(color.split('/').at(-1)?.replace(')', '').trim());
          return color.startsWith('rgba') ? Number(color.split(',').at(-1)?.replace(')', '')) : 1;
        });
        await page.evaluate(() => window.scrollTo(0, 0));
        await expect.poll(opacity, { message: 'opening hero remains visible through the header' }).toBeLessThanOrEqual(0.2);
        await page.screenshot({ path: `../output/translucent-nav/${surface}-${scheme}-${width}-top.png` });
        await page.evaluate(() => window.scrollTo(0, 300));
        await expect.poll(opacity).toBe(1);
        expect((await header.boundingBox())!.y).toBe(0);
        await page.screenshot({ path: `../output/translucent-nav/${surface}-${scheme}-${width}-scrolled.png` });
        await page.evaluate(() => window.scrollTo(0, 0));
        await expect.poll(opacity).toBeLessThanOrEqual(0.2);
        await page.goto('/settings');
        await expect(page.locator('.g-settings')).toBeVisible();
        await expect.poll(opacity, { message: 'ordinary forms keep a readable opaque header' }).toBe(1);
      });
    }
  }
}
