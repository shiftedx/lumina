import { expect, test, type Page } from '@playwright/test';
import { mockAdminFixtures, mockApi, signIn } from './lumina-mock';

/** Settings on a TV at the 1600 px step, by D-pad only. */

const SECTIONS = ['account', 'appearance', 'discovery', 'playback', 'streaming', 'downloads', 'apps', 'privacy', 'about', 'overview', 'activity', 'members', 'library', 'media', 'requests', 'transcoding', 'ai', 'tasks', 'backups', 'diagnostics'];

async function tv(page: Page) {
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false, settings_advanced: true });
  await mockAdminFixtures(page);
  await page.goto('/');
  await signIn(page);
}

test('the D-pad reaches every section and row without Tab or hover', async ({ page }) => {
  await tv(page);
  await page.goto('/settings/playback');
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await nav.getByRole('link', { name: 'Playback', exact: true }).focus();
  await page.keyboard.press('ArrowDown');
  await expect(nav.getByRole('link', { name: 'Downloads' })).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(nav.getByRole('link', { name: 'Playback', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowRight');
  const info = page.getByRole('button', { name: 'About Autoplay next video' });
  await expect(info).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(info).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(info).toHaveAttribute('aria-expanded', 'false');
  await expect(info).toBeFocused();
  await page.keyboard.press('ArrowRight');
  const autoplay = page.getByRole('switch', { name: 'Autoplay next video' });
  await expect(autoplay).toBeFocused();
  const before = await autoplay.isChecked();
  await page.keyboard.press('Enter');
  await expect(autoplay).toBeChecked({ checked: !before });
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('combobox', { name: 'Maximum quality' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('switch', { name: 'Even out loudness' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('button', { name: 'About Even out loudness' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(nav.getByRole('link', { name: 'Playback', exact: true })).toBeFocused();
  await page.goto('/settings');
  await nav.getByRole('link', { name: 'Profile' }).focus();
  await page.keyboard.press('ArrowRight');
  const card = page.getByRole('region', { name: 'All settings' }).getByRole('link', { name: 'Profile' });
  await expect(card).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/settings\/account$/);
  await expect(page.locator('#settings-section-title')).toBeFocused();
  await page.goto('/settings/playback');
  await nav.getByRole('link', { name: 'Playback', exact: true }).focus();
  for (let step = 0; step < SECTIONS.length - 1 - SECTIONS.indexOf('playback'); step += 1) await page.keyboard.press('ArrowDown');
  await expect(nav.getByRole('link', { name: 'Diagnostics' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/settings\/diagnostics$/);
});

// The 10-foot step is for touch screens and TV remotes; a mouse gets the 36px desktop control (tokens.css).
test.describe('on a touch screen or TV', () => {
  test.use({ hasTouch: true });
  test('every Settings target is at least 48 px tall at the 1600 px step, and each ⓘ is 48 px wide', async ({ page }) => {
    await tv(page);
    const small: string[] = [];
    for (const section of SECTIONS) {
      await page.goto(`/settings/${section}`);
      await expect(page.locator('.g-settings-section-head h2')).toBeVisible();
      await page.waitForTimeout(250);
      small.push(...await page.locator('.g-settings').evaluate((shell, where) => [...shell.querySelectorAll<HTMLElement>('button, a[href], select, input, summary')]
        .filter((element) => element.getClientRects().length > 0 && !(element.tagName === 'A' && element.closest('p, li > span, dd, td, th')))
        .flatMap((element) => {
          const box = (element.matches('input[type="checkbox"], input[type="radio"]') ? element.closest('label') ?? element : element).getBoundingClientRect();
          const wideEnough = !element.classList.contains('g-info-button') || box.width >= 47.5;
          return box.height >= 47.5 && wideEnough ? [] : [`${where}: ${element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName} (${Math.round(box.width)}×${Math.round(box.height)})`];
        }), section));
    }
    expect(small).toEqual([]);
  });
});
