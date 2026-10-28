import { expect, test } from '@playwright/test';
import { mockApi } from './lumina-mock';

for (const path of ['/', '/invite']) {
  test(`an invite link at ${path} renders the join form and removes its fragment`, async ({ page }) => {
    await mockApi(page, {});
    await page.goto(`${path}#invite=synthetic-token`);
    await expect(page.getByRole('heading', { name: 'Step inside the vault.' })).toBeVisible();
    await expect(page.getByLabel('Username')).toBeVisible();
    await expect(page.getByLabel('Display name')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create my account' })).toBeVisible();
    expect(new URL(page.url()).hash).toBe('');
  });
}

test('a reset link renders the new-password form', async ({ page }) => {
  await mockApi(page, {});
  await page.goto('/#reset=synthetic-token');
  await expect(page.getByRole('heading', { name: 'Choose a new password' })).toBeVisible();
  await expect(page.getByLabel('New password', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Set new password' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Back to sign in' })).toBeVisible();
});

test('public brand lockups align the mark with the L across narrow layouts', async ({ page }, testInfo) => {
  await mockApi(page, {});
  for (const path of ['/', '/music-offline.html']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: path === '/' ? 'Welcome home' : 'Offline listening' })).toBeVisible();
    for (const width of [390, 720]) {
      await page.setViewportSize({ width, height: 844 });
      if (path === '/') {
        await expect(page.locator('.g-auth-side')).toBeHidden();
        await expect(page.locator('.g-auth-bar')).toBeVisible();
      }
      const brand = page.locator(path === '/' ? '.g-auth-bar .g-brand' : '.offline-wordmark');
      const edges = await brand.evaluate(async (element) => {
        await document.fonts.ready;
        const image = element.querySelector('img')!;
        await image.decode();
        const word = element.querySelector<HTMLElement>('.g-brand-word')!;
        const canvas = document.createElement('canvas');
        const scale = 16;
        canvas.width = image.naturalWidth * scale;
        canvas.height = image.naturalHeight * scale;
        const context = canvas.getContext('2d')!;
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let lastPaintedRow = -1;
        for (let y = 0; y < canvas.height; y++) {
          for (let x = 0; x < canvas.width; x++) {
            if (pixels[(y * canvas.width + x) * 4 + 3] >= 128) lastPaintedRow = y;
          }
        }
        const imageBounds = image.getBoundingClientRect();
        const markBottom = imageBounds.top + (lastPaintedRow + 1) / canvas.height * imageBounds.height;
        const style = getComputedStyle(word);
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const baseline = document.createElement('span');
        baseline.style.cssText = 'display:inline-block;width:0;height:0;padding:0;border:0;vertical-align:baseline';
        word.append(baseline);
        const letterBottom = baseline.getBoundingClientRect().top + context.measureText('L').actualBoundingBoxDescent;
        baseline.remove();
        return { markBottom, letterBottom };
      });
      expect(Math.abs(edges.markBottom - edges.letterBottom), JSON.stringify(edges)).toBeLessThanOrEqual(0.75);
      await testInfo.attach(`${path === '/' ? 'sign-in' : 'offline'}-brand-${width}`, { body: await brand.screenshot(), contentType: 'image/png' });
    }
  }
});

// A 1x1 PNG: the showcase art route only has to answer with a decodable image.
const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkqOcHAAGmAQs+B90oAAAAAElFTkSuQmCC', 'base64');
async function mockShowcase(page: import('@playwright/test').Page) {
  await page.route('**/api/public/showcase', (route) => route.fulfill({ json: { slides: [
    { backdrop_url: '/api/public/showcase/art/a', title: 'Dune: Part Three', caption: 'In cinemas Friday', kind: 'movie' },
    { backdrop_url: '/api/public/showcase/art/b', title: 'Frieren', caption: 'New this season', kind: 'anime' },
  ] } }));
  await page.route('**/api/public/showcase/art/*', (route) => route.fulfill({ contentType: 'image/png', body: PIXEL }));
}

test('release art fills the page behind the sign-in panel and never blocks signing in', async ({ page }) => {
  await mockApi(page, {});
  await mockShowcase(page);
  await page.goto('/');
  await expect(page.getByText('In cinemas Friday')).toBeVisible();
  await expect(page.locator('.g-auth.has-art .g-show-art img')).toHaveCount(1);
  const pips = page.getByRole('group', { name: 'Featured releases' }).getByRole('button');
  await expect(pips).toHaveCount(2);
  await pips.nth(1).click();
  await expect(page.getByText('New this season')).toBeVisible();
  await page.getByRole('button', { name: 'Pause the showcase' }).click();
  await expect(page.getByRole('button', { name: 'Play the showcase' })).toBeVisible();
  await page.getByLabel('Username').fill('sam');
  await page.getByLabel('Password', { exact: true }).fill('synthetic-passphrase');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.locator('.g-auth')).toHaveCount(0);
});

test('at phone width the release art sits behind a full-width form with no sideways scroll', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {});
  await mockShowcase(page);
  await page.goto('/');
  await expect(page.getByText('In cinemas Friday')).toBeVisible();
  await expect(page.locator('.g-auth-bar')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const form = await page.locator('.g-auth-main').boundingBox();
  expect(form?.width).toBe(390);
});

test('optional provider login carries the remember preference and navigates to the identity provider', async ({ page }) => {
  await mockApi(page, {});
  await page.route('**/api/session/oauth/providers', (route) => route.fulfill({ json: [{ id: 'google', name: 'Google' }] }));
  let started: unknown;
  await page.route('**/api/session/oauth/google/start', (route) => {
    started = route.request().postDataJSON();
    return route.fulfill({ json: { authorization_url: 'https://identity.example.com/authorize?state=synthetic-state' } });
  });
  await page.route('https://identity.example.com/**', (route) => route.fulfill({
    contentType: 'text/html', body: '<h1>Synthetic identity provider</h1>',
  }));
  await page.goto('/');
  await expect(page.getByLabel('Password', { exact: true })).toBeVisible();
  await page.getByRole('checkbox', { name: "Show me in Who's watching on this device" }).check();
  await page.getByRole('button', { name: 'Continue with Google' }).click();
  await expect(page.getByRole('heading', { name: 'Synthetic identity provider' })).toBeVisible();
  expect(started).toEqual({ remember_on_device: true });
  expect(new URL(page.url()).origin).toBe('https://identity.example.com');
});
