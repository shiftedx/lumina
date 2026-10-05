import type { Page } from '@playwright/test';
import { expect, test } from './realstack';

/** ADR 0010 amendment: apps add Lumina by host:port alone, and the SPA's own deep links still open Lumina. */

async function allowApps(page: Page, on: boolean) {
  await page.goto('/settings/media');
  await page.getByRole('switch', { name: 'Let apps like Infuse connect' }).setChecked(on);
  await page.getByRole('button', { name: 'Save media server settings' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
}

test('the Jellyfin API answers at the root while browser deep links still render', async ({ page }) => {
  await allowApps(page, true);
  try {
    await expect(page.getByRole('textbox', { name: 'Server address for apps' })).toHaveValue(new URL(page.url()).origin);
    const info = await page.request.get('/System/Info/Public');
    expect(info.ok(), `${info.status()}`).toBe(true);
    expect((await info.json()).ProductName).toBe('Jellyfin Server');
    for (const alias of ['/jellyfin/System/Info/Public', '/emby/System/Info/Public']) {  // root only, no aliases
      expect((await page.request.get(alias)).status(), alias).toBe(404);
    }
    expect((await page.request.get('/Items', { headers: { Authorization: 'MediaBrowser Client="e2e"' } })).status()).toBe(401);

    await page.goto('/library/shows');
    await expect(page.getByRole('button', { name: /^Realstack Show,/ })).toBeVisible();
    await page.goto('/settings');
    await expect(page.getByRole('main', { name: 'Main content' })).toBeVisible();
  } finally {
    await allowApps(page, false);
  }
});
