import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { mockApi, signIn, user } from './lumina-mock';
import { chooseSelect } from './select-helper';

for (const [width, colorScheme] of [[320, 'light'], [1440, 'dark']] as const) {
  test(`mobile connect pairs and replaces the browser origin at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
    await mockApi(page, {});
    const created: Array<{ server_url: string; replace_id?: string }> = [];
    let used = false;
    await page.route(/\/api\/mobile-connect(?:\/.*)?$/, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path === '/api/mobile-connect' && request.method() === 'GET') {
        return route.fulfill({ json: { enabled: true, email_configured: false } });
      }
      if (path === '/api/mobile-connect' && request.method() === 'POST') {
        const body = request.postDataJSON();
        created.push(body);
        return route.fulfill({ json: {
          id: `pair-${created.length}`, server_url: body.server_url,
          expires_at: new Date(Date.now() + 300_000).toISOString(),
          qr_payload: 'synthetic pairing payload', qr_size: 29, qr_path: 'M4 4h7v7h-7zM18 4h7v7h-7zM4 18h7v7h-7z',
          email_sent: null,
        } });
      }
      if (request.method() === 'GET') return route.fulfill({ json: { status: used ? 'used' : 'pending' } });
      return route.fulfill({ status: 204 });
    });
    await page.goto('/');
    await signIn(page);
    await page.getByRole('button', { name: /account menu$/ }).click();
    await page.getByRole('menuitem', { name: 'Mobile connect' }).click();
    const dialog = page.getByRole('dialog', { name: 'Mobile connect' });
    await expect(dialog.getByRole('img', { name: /QR/i })).toBeVisible();
    expect(created[0].server_url).toBe(new URL(page.url()).origin);
    await expect(dialog.getByLabel('Server URL', { exact: true })).not.toBeVisible();
    const help = dialog.getByText('Join your home Wi-Fi.', { exact: false });
    await expect(help).not.toBeVisible();
    await dialog.getByText('Trouble connecting?', { exact: true }).click();
    await expect(help).toBeVisible();
    await dialog.getByText('Trouble connecting?', { exact: true }).click();
    const audit = await new AxeBuilder({ page }).include('dialog').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
    expect(audit.violations.filter((violation) => ['moderate', 'serious', 'critical'].includes(violation.impact ?? ''))).toEqual([]);
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    await dialog.getByText('Advanced', { exact: true }).click();
    await dialog.getByLabel('Server URL', { exact: true }).fill('https://media.example.com');
    await dialog.getByRole('button', { name: 'Regenerate QR', exact: true }).click();
    await expect.poll(() => created.length).toBe(2);
    expect(created[1]).toMatchObject({ server_url: 'https://media.example.com', replace_id: 'pair-1' });
    used = true;
    await expect(dialog.getByText(/connected/i).first()).toBeVisible();
    await expect(dialog.getByRole('img', { name: /QR/i })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('button', { name: /account menu$/ })).toBeFocused();
  });
}

test('an owner can email and download a member code from a narrow phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await mockApi(page, {});
  await page.route('**/api/admin/users', (route) => route.fulfill({ json: [user, { ...user, id: 'member-2', display_name: 'Mira', username: 'mira', role: 'viewer' }] }));
  const issued: Array<Record<string, unknown>> = [];
  const revoked: string[] = [];
  await page.route(/\/api\/mobile-connect(?:\/.*)?$/, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/mobile-connect' && request.method() === 'GET') return route.fulfill({ json: { enabled: true, email_configured: true } });
    if (path === '/api/mobile-connect' && request.method() === 'POST') {
      const body = request.postDataJSON();
      issued.push(body);
      return route.fulfill({ json: {
        id: `pair-${issued.length}`, server_url: body.server_url, expires_at: new Date(Date.now() + (body.share ? 900_000 : 300_000)).toISOString(),
        qr_size: 29, qr_path: 'M4 4h7v7h-7z', qr_payload: 'synthetic pairing payload', email_sent: Boolean(body.email),
      } });
    }
    if (request.method() === 'DELETE') { revoked.push(path); return route.fulfill({ status: 204 }); }
    return route.fulfill({ json: { status: 'pending' } });
  });
  await page.goto('/');
  await signIn(page);
  await page.getByRole('button', { name: /account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'Mobile connect' }).click();
  const dialog = page.getByRole('dialog', { name: 'Mobile connect' });
  await expect(dialog.getByRole('img', { name: /QR/i })).toBeVisible();
  await dialog.getByText('Advanced', { exact: true }).click();
  await dialog.getByText('Share with a household member', { exact: true }).click();
  await chooseSelect(page, dialog.getByRole('combobox', { name: 'Member' }), 'Mira');
  await dialog.getByLabel('Email', { exact: true }).fill('mira@example.com');
  await dialog.getByRole('button', { name: 'Send email', exact: true }).click();
  await expect(dialog.getByText('Email sent.', { exact: true })).toBeVisible();
  expect(issued.at(-1)).toMatchObject({ user_id: 'member-2', share: true, email: 'mira@example.com', replace_id: 'pair-1' });
  const download = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Download QR', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('lumina-mobile-connect.svg');
  const audit = await new AxeBuilder({ page }).include('dialog').withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(audit.violations.filter((violation) => ['moderate', 'serious', 'critical'].includes(violation.impact ?? ''))).toEqual([]);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(revoked).not.toContain('/api/mobile-connect/pair-2');
});
