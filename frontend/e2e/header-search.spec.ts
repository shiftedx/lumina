import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import { seriesSummary } from '../src/test/galleryFixtures';
import { catalogHome } from '../src/features/requests/requestsFixtures';
import type { ServerNavigation } from '../src/types';
import { mockApi, signIn } from './lumina-mock';
import { mockGalleryWall } from './gallery-mock';

const OUT = join(process.cwd(), '..', 'output', 'header-search');
for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 390, height: 844 }]) {
  test(`contextual header search and Add link: ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    mkdirSync(OUT, { recursive: true });
    const navigation: ServerNavigation = { categories: ['movies', 'tv'], features: { streaming: false, requests: true } };
    await mockApi(page, {}, navigation);
    const title = seriesSummary('lantern', { name: 'Lantern Harbor', category: 'shows' });
    const galleryRequests = await mockGalleryWall(page, { total: 4, search: { query: 'lantern', mode: 'lexical', matches: [{ kind: 'title', id: 'lantern', title: title.name, subtitle: '', score: 1, lexical_score: 1, semantic_score: 0, match_mode: 'lexical', media_title: title }], items: [], index_generation: 1 } });
    const requestSearches: string[] = [];
    await page.route(/\/api\/requests(?:\/|\?|$)/, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/requests/catalog/home') return route.fulfill({ json: catalogHome });
      if (url.pathname === '/api/requests/catalog/search') { requestSearches.push(url.searchParams.get('q') ?? ''); return route.fulfill({ json: { items: [], page: 1, total_pages: 1 } }); }
      if (url.pathname === '/api/requests') return route.fulfill({ json: { items: [], page: 1, total_pages: 1, counts: {} } });
      return route.fallback();
    });
    await page.goto('/video/shows');
    await signIn(page);
    const header = page.locator('header.g-topbar');
    await page.keyboard.press('Control+k');
    const showsSearch = page.getByRole('searchbox', { name: 'Search shows' });
    await expect(showsSearch).toBeVisible();
    await expect(showsSearch).toBeFocused();
    await expect(page.getByRole('dialog', { name: 'Search Lumina' })).toHaveCount(0);
    await expect(header.getByRole('searchbox', { name: 'Search shows' })).toHaveCount(1);
    await expect(page.locator('main').getByRole('searchbox', { name: 'Search shows' })).toHaveCount(0);
    await expect(header.getByRole('button', { name: /add (?:a )?link/i })).toHaveCount(0);
    await showsSearch.fill('lantern');
    await showsSearch.press('Enter');
    await expect(page.getByRole('region', { name: 'Results for “lantern”' })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Lantern Harbor/ })).toBeVisible();
    expect(galleryRequests.some((url) => url.pathname === '/api/search' && url.searchParams.get('q') === 'lantern')).toBe(true);
    await page.screenshot({ path: join(OUT, `shows-${viewport.name}.png`) });
    await page.goto('/requests');
    if (viewport.name === 'phone') await header.getByRole('button', { name: 'Search films, series and anime' }).click();
    const requestsSearch = page.getByRole('searchbox', { name: 'Search films, series and anime' });
    await expect(requestsSearch).toBeVisible();
    await expect(header.getByRole('searchbox', { name: 'Search films, series and anime' })).toHaveCount(1);
    await expect(page.locator('main').getByRole('searchbox', { name: 'Search films, series and anime' })).toHaveCount(0);
    await requestsSearch.fill('lantern');
    await requestsSearch.press('Enter');
    await expect(page.getByRole('heading', { name: 'Results for “lantern”' })).toBeVisible();
    await expect.poll(() => requestSearches).toContain('lantern');
    await page.screenshot({ path: join(OUT, `requests-${viewport.name}.png`) });
    if (viewport.name === 'phone') {
      await header.getByRole('button', { name: 'Close search' }).click();
      await expect(header.getByRole('button', { name: 'Search films, series and anime' })).toBeFocused();
    }
    await page.goto('/settings');
    if (viewport.name === 'phone') await header.getByRole('button', { name: 'Search settings' }).click();
    const settingsSearch = header.getByRole('searchbox', { name: 'Search settings' });
    await expect(settingsSearch).toBeVisible();
    await expect(page.locator('main').getByRole('searchbox', { name: 'Search settings' })).toHaveCount(0);
    await settingsSearch.fill('dark');
    await expect(page.getByRole('region', { name: 'Search results' }).getByRole('link', { name: /Theme/ })).toBeVisible();
    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
    await page.screenshot({ path: join(OUT, `settings-dark-${viewport.name}.png`) });
    if (viewport.name === 'phone') {
      await header.getByRole('button', { name: 'Close search' }).click();
      await expect(header.getByRole('button', { name: 'Search settings' })).toBeFocused();
      await header.getByRole('button', { name: 'Open navigation' }).click();
      await expect(page.getByRole('dialog', { name: 'Navigation' }).getByRole('button', { name: 'Add a link' })).toHaveCount(0);
      await page.getByRole('button', { name: 'Close menu' }).click();
    }
    navigation.features.streaming = true;
    await Promise.all([page.waitForResponse((response) => new URL(response.url()).pathname === '/api/navigation'), page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))]);
    if (viewport.name === 'phone') {
      await header.getByRole('button', { name: 'Open navigation' }).click();
      await expect(page.getByRole('dialog', { name: 'Navigation' }).getByRole('button', { name: 'Add a link' })).toBeVisible();
    } else await expect(header.getByRole('button', { name: /add (?:a )?link/i })).toBeVisible();
  });
}
