import { expect, test } from './realstack';

test('a web QR signs in one mobile device and its app password revokes that device', async ({ page, playwright }, testInfo) => {
  await page.goto('/');
  const origin = new URL(page.url()).origin;
  const session = await (await page.request.get('/api/session/me')).json();
  const headers = { Origin: origin, 'X-CSRF-Token': session.csrf_token as string };
  const initial = await (await page.request.get('/api/mobile-connect')).json();
  const mobile = await playwright.request.newContext({ baseURL: origin, storageState: { cookies: [], origins: [] } });
  let passwordID: string | undefined;
  try {
    await page.getByRole('button', { name: /account menu$/ }).click();
    const generated = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/mobile-connect' && response.request().method() === 'POST');
    await page.getByRole('menuitem', { name: 'Mobile connect' }).click();
    const dialog = page.getByRole('dialog', { name: 'Mobile connect' });
    if (!initial.enabled) await dialog.getByRole('button', { name: 'Enable mobile access' }).click();
    const code = await (await generated).json();
    const payload = JSON.parse(code.qr_payload);
    expect(payload).toMatchObject({ type: 'lumina-pair', version: 1, server_url: origin });
    await expect(dialog.getByRole('img', { name: /QR/i })).toBeVisible();
    await dialog.screenshot({ path: testInfo.outputPath('mobile-connect.png') });

    const claim = await mobile.post('/api/mobile-connect/claim', { data: { token: payload.token, name: 'Pairing workflow' } });
    expect(claim.status()).toBe(200);
    expect(claim.headers()['cache-control']).toContain('no-store');
    const credentials = await claim.json();
    const apps = await (await page.request.get('/api/connected-apps')).json() as Array<{ id: string; device_name: string; kind: string }>;
    passwordID = apps.find((app) => app.kind === 'app_password' && app.device_name === 'Pairing workflow')?.id;
    expect(passwordID).toBeTruthy();
    const device = 'MediaBrowser Client="Lumina Apple", Device="iPhone", DeviceId="pairing-workflow", Version="1.0"';
    const login = await mobile.post('/Users/AuthenticateByName', { headers: { Authorization: device }, data: { Username: credentials.username, Pw: credentials.password } });
    expect(login.status()).toBe(200);
    const token = (await login.json()).AccessToken;
    const authorization = { Authorization: `${device}, Token="${token}"` };
    expect((await mobile.get('/Users/Me', { headers: authorization })).status()).toBe(200);
    expect((await mobile.post('/api/mobile-connect/claim', { data: { token: payload.token, name: 'Replay' } })).ok()).toBe(false);
    await expect(dialog.getByText('Connected.', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('img', { name: /QR/i })).toHaveCount(0);
    expect((await page.request.delete(`/api/connected-apps/${passwordID}`, { headers })).status()).toBe(204);
    passwordID = undefined;
    expect((await mobile.get('/Users/Me', { headers: authorization })).status()).toBe(401);
  } finally {
    if (passwordID) await page.request.delete(`/api/connected-apps/${passwordID}`, { headers });
    if (!initial.enabled) await page.request.put('/api/admin/media-server', { headers, data: { jellyfin_enabled: false } });
    await mobile.dispose();
  }
});
