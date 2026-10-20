import { expect, test } from '@playwright/test';
import { mockApi, mockAdminFixtures, signIn } from './lumina-mock';
import { auditA11y } from './release-evidence';

for (const width of [1440, 390]) test(`personal YouTube session connects and disconnects at ${width}px`, async ({ page }) => {
  await page.emulateMedia({ colorScheme: width === 390 ? 'dark' : 'light', reducedMotion: 'reduce' });
  await page.setViewportSize({ width, height: 960 });
  await mockApi(page, { sidebar_collapsed: false, theme: width === 390 ? 'dark' : 'light' }); await mockAdminFixtures(page);
  let connected = false;
  await page.route('**/api/me/youtube-session', async (route) => {
    if (route.request().method() === 'PUT') {
      expect(route.request().postDataJSON()).toMatchObject({ consent: true }); connected = true;
    } else if (route.request().method() === 'DELETE') connected = false;
    await route.fulfill({ json: { status: connected ? 'connected' : 'not_connected', updated_at: null } });
  });
  await page.goto('/'); await signIn(page);
  await page.goto('/settings/apps?page=youtube');
  await expect(page.getByText('Not connected', { exact: true })).toBeVisible();
  const connect = page.getByRole('button', { name: 'Connect YouTube', exact: true });
  await page.getByLabel('YouTube cookie file').setInputFiles({ name: 'cookies.txt', mimeType: 'text/plain', buffer: Buffer.from('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t4102444800\tSAPISID\tsynthetic-session\n') });
  await expect(connect).toBeDisabled();
  await page.getByRole('checkbox', { name: 'Allow Lumina to use this session for my YouTube playback.' }).check();
  await connect.click();
  await expect(page.getByText('Session saved', { exact: true })).toBeVisible();
  await expect(page.getByRole('checkbox')).not.toBeChecked();
  await auditA11y(page);
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: `../output/youtube-session-${width}.png`, fullPage: true });
  await page.getByRole('button', { name: 'Disconnect YouTube' }).click();
  await expect(page.getByText('Not connected', { exact: true })).toBeVisible();
  await expect(page.locator('html')).toHaveJSProperty('scrollWidth', await page.locator('html').evaluate((el) => el.clientWidth));
});
