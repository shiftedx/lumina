import { expect, test } from '@playwright/test';

import { mockGalleryWall, mockLibrarySections } from './gallery-mock';
import { mockHome } from './home-mock';
import { mockLibraryItems, mockTitleDetails } from './library-mock';
import { mockAdminFixtures, mockApi, mockTitles, signIn } from './lumina-mock';

const ROUTES = [
  ['home', '/'], ['library', '/library/shows'], ['downloads', '/downloads'],
  ['settings', '/settings/appearance'], ['storage', '/settings/library'], ['title', '/title/movie-1'],
] as const;

for (const scheme of ['light', 'dark'] as const) {
  for (const width of [1440, 390]) {
    test(`actions and panels have no decorative outline boxes: ${scheme} ${width}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await mockApi(page, { sidebar_collapsed: true });
      await mockAdminFixtures(page);
      await mockTitles(page);
      await mockHome(page);
      await mockGalleryWall(page);
      await mockLibrarySections(page);
      await mockLibraryItems(page);
      await mockTitleDetails(page);
      await page.goto('/');
      await signIn(page);
      for (const [name, path] of ROUTES) {
        await page.goto(path);
        await expect(page.locator('main h1').first()).toBeVisible();
        if (name === 'title') await expect(page.getByRole('heading', { name: 'Northern Lantern', exact: true })).toBeVisible();
        const framed = await page.locator('main .g-button:visible, main .g-panel:visible, main .summary-card:visible, main .collection-row:visible, main .g-input:visible, main .g-select select:visible').evaluateAll((elements) => elements.filter((element) => {
          const style = getComputedStyle(element);
          return ['Top', 'Right', 'Bottom', 'Left'].every((side) => {
            const width = style.getPropertyValue(`border-${side.toLowerCase()}-width`);
            const kind = style.getPropertyValue(`border-${side.toLowerCase()}-style`);
            const colour = style.getPropertyValue(`border-${side.toLowerCase()}-color`);
            return parseFloat(width) > 0 && kind !== 'none' && colour !== 'transparent' && !colour.endsWith(', 0)');
          });
        }).map((element) => element.textContent?.trim().slice(0, 60)));
        expect(framed, `${name}: decorative full border`).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), `${name}: overflow`).toBeLessThanOrEqual(0);
        await page.screenshot({ path: `../output/playwright/borderless/${name}-${scheme}-${width}.png` });
      }
      await page.goto('/library/shows');
      const refresh = page.getByRole('button', { name: 'Refresh', exact: true });
      await refresh.focus();
      expect(await refresh.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe('solid');
      await page.route('**/api/titles/missing-title', (route) => route.fulfill({ status: 404, json: { detail: 'Title not found' } }));
      await page.goto('/title/missing-title');
      const heading = page.getByRole('heading', { name: 'This title is not in your library', exact: true });
      await expect(heading).toBeFocused();
      expect(await heading.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe('none');
      await page.screenshot({ path: `../output/playwright/borderless/missing-title-${scheme}-${width}.png` });
      await page.keyboard.press('Tab');
      const back = page.getByRole('button', { name: 'Back to your library', exact: true });
      await expect(back).toBeFocused();
      expect(await back.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe('solid');
    });
  }
}
