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
    test(`profile and navigation popups share rounded corners: ${scheme} ${width}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await mockApi(page, { sidebar_collapsed: true });
      await mockHome(page);
      await page.goto('/');
      await signIn(page);
      await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
      const navigation = page.getByRole('dialog', { name: 'Navigation', exact: true });
      await expect(navigation).toBeVisible();
      const radius = await navigation.evaluate((element) => getComputedStyle(element).borderTopLeftRadius);
      expect(parseFloat(radius)).toBeGreaterThanOrEqual(12);
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: /account menu$/ }).click();
      const profile = page.getByRole('menu');
      await expect(profile).toBeVisible();
      for (const corner of ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomLeftRadius', 'borderBottomRightRadius'] as const) {
        expect(await profile.evaluate((element, property) => getComputedStyle(element)[property], corner)).toBe(radius);
      }
      expect(await profile.evaluate((element) => getComputedStyle(element).borderTopWidth)).toBe('0px');
      const activeTheme = profile.locator('[role="menuitemradio"][aria-checked="true"]');
      await expect(activeTheme).toHaveCount(1);
      const focusedRow = profile.locator('.g-menu-item:focus');
      const activeStyle = await activeTheme.evaluate((element) => {
        const style = getComputedStyle(element);
        const text = [...element.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim());
        const range = document.createRange();
        range.selectNode(text!);
        return { background: style.backgroundColor, accent: style.borderInlineStartColor, textOffset: range.getBoundingClientRect().left - element.getBoundingClientRect().left };
      });
      expect(activeStyle.background).toBe(await focusedRow.evaluate((element) => getComputedStyle(element).backgroundColor));
      expect(activeStyle.accent).toBe(await focusedRow.evaluate((element) => getComputedStyle(element).borderInlineStartColor));
      const regularOffset = await focusedRow.evaluate((element) => {
        const range = document.createRange();
        range.selectNode([...element.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())!);
        return range.getBoundingClientRect().left - element.getBoundingClientRect().left;
      });
      expect(activeStyle.textOffset).toBe(regularOffset);
      await expect(activeTheme.locator('svg')).toHaveCount(0);
      await page.screenshot({ path: `../output/playwright/borderless/profile-${scheme}-${width}.png` });
      await page.keyboard.press('Escape');
      await expect(page.getByRole('button', { name: /account menu$/ })).toBeFocused();
    });

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
          // Settings fields use a functional border to make editable values easy to find.
          if (element.matches('input, textarea, select') && element.closest('.g-settings')) return false;
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
