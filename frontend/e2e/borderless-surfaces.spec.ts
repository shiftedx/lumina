import { expect, test, type Locator } from '@playwright/test';

import { mockGalleryWall, mockLibrarySections } from './gallery-mock';
import { mockHome } from './home-mock';
import { openHomeEditor } from './home-edit-helper';
import { mockReco } from './reco-mock';
import { mockLibraryItems, mockTitleDetails } from './library-mock';
import { item, mockAdminFixtures, mockApi, mockTitles, signIn } from './lumina-mock';

async function switchAppearance(control: Locator) {
  return control.evaluate((element) => {
    const home = element.matches('button');
    const track = home ? element : element.parentElement!.querySelector('.g-switch-track')!;
    const style = getComputedStyle(track, home ? '::before' : null);
    const knob = getComputedStyle(home ? element.querySelector('.h-switch-knob')! : track, home ? null : '::after');
    return {
      radius: style.borderTopLeftRadius, knobRadius: knob.borderTopLeftRadius,
      height: style.height, knobSize: [knob.width, knob.height],
      fill: style.backgroundColor, border: style.borderTopColor, knob: knob.backgroundColor,
    };
  });
}

const ROUTES = [
  ['home', '/'], ['library', '/library/shows'], ['downloads', '/downloads'],
  ['settings', '/settings/appearance'], ['storage', '/settings/library'], ['title', '/title/movie-1'],
] as const;

for (const scheme of ['light', 'dark'] as const) {
  for (const width of [1440, 390]) {
    test(`switches share square gold states across Settings, playback and Home: ${scheme} ${width}`, async ({ page }) => {
      test.setTimeout(90_000);
      await page.setViewportSize({ width, height: 900 });
      await mockApi(page, {}); await mockTitles(page); await mockHome(page); await mockReco(page);
      await page.goto('/settings'); await signIn(page);
      for (const forcedColors of ['none', 'active'] as const) {
        await page.emulateMedia({ colorScheme: scheme, forcedColors, reducedMotion: 'reduce' });
        const inspect = async (control: Locator) => {
          await control.scrollIntoViewIfNeeded();
          await page.keyboard.press('Tab');
          const states = [];
          for (const checked of [false, true]) {
            const isChecked = () => control.evaluate((element) => element.matches('input') ? (element as HTMLInputElement).checked : element.getAttribute('aria-checked') === 'true');
            if (await isChecked() !== checked) {
              await control.focus();
              await page.keyboard.press('Space');
            }
            await expect.poll(isChecked).toBe(checked);
            await control.focus();
            await expect(control).toBeFocused();
            expect(await control.evaluate((element) => [element, element.parentElement!.querySelector('.g-switch-track')].some((node) => node && getComputedStyle(node).outlineStyle === 'solid'))).toBe(true);
            states.push(await switchAppearance(control));
          }
          return states;
        };
        await page.goto('/settings');
        const settings = await inspect(page.getByRole('switch', { name: 'Autoplay next video' }));
        const tokens = await page.locator('html').evaluate((element) => {
          const style = getComputedStyle(element);
          const probe = document.createElement('span');
          probe.style.backgroundColor = 'var(--g-gold)'; element.append(probe);
          const gold = getComputedStyle(probe).backgroundColor; probe.remove();
          return { radius: style.getPropertyValue('--g-radius').trim(), gold };
        });
        expect(settings[0].radius).toBe(tokens.radius);
        expect(settings[0].knobRadius).toBe(tokens.radius);
        expect(settings[0].fill).not.toBe(settings[1].fill);
        expect(settings[1].fill).not.toBe(settings[1].knob);
        if (forcedColors === 'none') expect(settings[1].fill).toBe(tokens.gold);
        for (const [id, label] of [['item-ep-1-2', 'Autoplay'], [item.id, 'Autoplay next video']]) {
          await page.goto(`/watch/library/${id}`);
          await page.locator('video').first().evaluate((media: HTMLVideoElement) => media.pause());
          expect(await inspect(page.getByRole('switch', { name: label, exact: true }))).toEqual(settings);
        }
        await page.goto('/'); await openHomeEditor(page);
        expect(await inspect(page.getByRole('switch', { name: 'Show Next up' }))).toEqual(settings);
        await page.getByRole('button', { name: 'Done', exact: true }).click();
        await openHomeEditor(page);
        await expect(page.getByRole('switch', { name: 'Show Next up' })).toHaveAttribute('aria-checked', 'true');
      }
    });

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
