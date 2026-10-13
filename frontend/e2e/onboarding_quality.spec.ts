import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Route } from '@playwright/test';
import { mockApi, user } from './lumina-mock';

for (const width of [390, 1440]) {
  test(`onboarding opens the vault and keeps both setup steps usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await mockApi(page, {});
    await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending' } } }));
    await page.route('**/api/discovery/interests', (route) => route.fulfill({ json: { categories: [{ key: 'film', label: 'Film' }], selected_keys: [] } }));
    await page.route(/\/api\/discovery\/channels(\?|$)/, (route) => route.fulfill({ json: { categories: [] } }));
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Welcome to Lumina' })).toBeVisible();
    const theme = width === 1440 ? 'dark' : 'light';
    await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.g-onboarding-welcome-art img')).toHaveAttribute('src', '/onboarding/vault-arrival-v2.jpg');
    await expect(page.getByText(/new discoveries with family and friends/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Personalize my Home' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.getByRole('button', { name: 'Personalize my Home' }).click();

    await expect(page.getByRole('heading', { name: 'What draws you in?' })).toBeVisible();
    await expect(page.locator('.g-onboarding-interest-art img')).toHaveAttribute('src', '/onboarding/listening-room-v1.jpg');
    const assertProgress = async () => {
      const progress = await page.locator('.g-onboarding-progress').boundingBox();
      const masthead = await page.locator('.g-onboarding-panel .g-masthead').boundingBox();
      expect(progress!.y + progress!.height).toBeLessThan(masthead!.y);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    };
    await assertProgress();
    await page.getByLabel('Film', { exact: true }).check();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Meet your next favorites' })).toBeVisible();
    await expect(page.locator('.g-onboarding-discovery-hero img')).toHaveAttribute('src', '/onboarding/creator-studio-v1.jpg');
    await assertProgress();
    const search = page.getByRole('searchbox');
    await expect(search).toBeVisible();
    if (width === 390) {
      expect((await search.boundingBox())!.width).toBeGreaterThan(250);
      const button = await page.getByRole('button', { name: 'Find channels' }).boundingBox();
      const field = await search.boundingBox();
      expect(button!.width).toBeGreaterThanOrEqual(46);
      expect(button!.width).toBeLessThanOrEqual(50);
      expect(Math.abs(button!.y - field!.y)).toBeLessThan(2);
      expect(button!.x).toBeGreaterThanOrEqual(field!.x + field!.width);
      const back = page.getByRole('button', { name: 'Back', exact: true });
      const picks = page.getByRole('button', { name: 'Your chosen creators, 0 selected' });
      const next = page.getByRole('button', { name: 'Enter my Home', exact: true });
      const backBox = await back.boundingBox();
      const nextBox = await next.boundingBox();
      expect(Math.abs(backBox!.y - nextBox!.y)).toBeLessThan(2);
      await back.focus();
      await page.keyboard.press('Tab');
      await expect(picks).toBeFocused();
      await page.keyboard.press('Tab');
      await expect(next).toBeFocused();
    }
  });
}


const tasteCategories = [
  { key: 'music', label: 'Music' }, { key: 'film', label: 'Film' },
  { key: 'science-technology', label: 'Science & Technology' }, { key: 'nature', label: 'Nature' },
  { key: 'documentaries', label: 'Documentaries' }, { key: 'cooking', label: 'Cooking' },
  { key: 'gaming', label: 'Gaming' }, { key: 'travel', label: 'Travel' },
  { key: 'photography', label: 'Photography' },
];
const creator = (name: string, slug: string, preview = false) => ({
  channel_key: `https://www.youtube.com/@${slug}`, source_url: `https://www.youtube.com/@${slug}`,
  display_name: name, source: 'youtube', source_label: 'YouTube', artwork_url: null,
  preview_artwork_url: preview ? `/api/artwork/remote/${slug}` : null,
  preview_title: preview ? 'A look inside the creative process' : null, category_keys: ['music'], following: false,
});
const channelShelves = [
  { key: 'music', label: 'Music', state: 'ranked', channels: [creator('Studio Sessions', 'studio-sessions', true), creator('Sound & Story', 'sound-story', true), creator('The Listening Room', 'listening-room')] },
  { key: 'film', label: 'Film', state: 'curated', channels: [creator('Wide Angle', 'wide-angle'), creator('Frame by Frame', 'frame-by-frame')] },
];

for (const width of [320, 390, 768, 1440]) {
  test(`creator onboarding preserves the collection through back and a failed save at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const theme = width === 390 || width === 768 ? 'light' : 'dark';
    await mockApi(page, { theme });
    await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending' } } }));
    await page.route('**/api/discovery/interests', (route) => route.fulfill({ json: { categories: tasteCategories, selected_keys: [] } }));
    await page.route(/\/api\/discovery\/channels(\?|$)/, (route) => route.fulfill({ json: { categories: channelShelves } }));
    await page.route('**/api/artwork/remote/*', (route) => route.fulfill({ path: 'public/onboarding/listening-room-v1.jpg', contentType: 'image/jpeg' }));
    let attempts = 0;
    const writes: unknown[] = [];
    await page.route('**/api/onboarding/complete', (route) => {
      writes.push(route.request().postDataJSON());
      attempts++;
      return attempts === 1 ? route.fulfill({ status: 500, json: { detail: 'Temporarily unavailable' } }) : route.fulfill({ json: { status: 'completed', selected_keys: ['music'], followed: [] } });
    });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Welcome to Lumina' })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('welcome.png'), fullPage: true });
    await page.getByRole('button', { name: 'Personalize my Home' }).click();
    await page.getByRole('checkbox', { name: 'Music', exact: true }).check();
    await page.screenshot({ path: testInfo.outputPath('taste.png'), fullPage: true });
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    const tile = page.getByRole('button', { name: 'Follow Studio Sessions', exact: true });
    await expect(tile.locator('.g-onboarding-channel-image')).toHaveAttribute('src', /api\/artwork\/remote/);
    if (width === 390) {
      const image = await tile.locator('.g-onboarding-channel-image').boundingBox();
      const name = await tile.locator('.g-onboarding-channel-name').boundingBox();
      const actionBar = await page.locator('.g-onboarding-footer').boundingBox();
      expect(image!.y + image!.height).toBeLessThanOrEqual(actionBar!.y);
      expect(name!.y + name!.height).toBeLessThanOrEqual(actionBar!.y);
    }
    await tile.click();
    await page.getByRole('button', { name: 'Your chosen creators, 1 selected' }).click();
    await expect(page.getByRole('list', { name: 'Your chosen creators' })).toContainText('Studio Sessions');
    await page.screenshot({ path: testInfo.outputPath('creators.png'), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const violations = (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()).violations.filter((entry) => entry.impact === 'serious' || entry.impact === 'critical');
    expect(violations).toEqual([]);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Your chosen creators' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Back', exact: true }).click();
    await expect(page.getByRole('checkbox', { name: 'Music', exact: true })).toBeChecked();
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Studio Sessions — selected, deselect' })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: 'Enter my Home with 1 channel' }).click();
    await expect(page.getByRole('alert')).toContainText('We could not save your setup');
    await page.getByRole('button', { name: 'Your chosen creators, 1 selected' }).click();
    await expect(page.getByRole('list', { name: 'Your chosen creators' })).toContainText('Studio Sessions');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Enter my Home with 1 channel' }).click();
    await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
    expect(writes).toEqual(Array.from({ length: 2 }, () => ({ keys: ['music'], follows: [{ source_url: 'https://www.youtube.com/@studio-sessions', display_name: 'Studio Sessions' }] })));
  });
}


test('creator discovery offers a starting collection even without choosing interests', async ({ page }) => {
  await mockApi(page, {});
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending' } } }));
  await page.route('**/api/discovery/interests', (route) => route.fulfill({ json: { categories: tasteCategories, selected_keys: [] } }));
  let requestedKeys: string[] = [];
  await page.route(/\/api\/discovery\/channels(\?|$)/, (route) => {
    requestedKeys = new URL(route.request().url()).searchParams.getAll('keys');
    return route.fulfill({ json: { categories: channelShelves } });
  });
  let saved: unknown;
  await page.route('**/api/onboarding/complete', (route) => {
    saved = route.request().postDataJSON();
    return route.fulfill({ json: { status: 'completed', selected_keys: [], followed: [] } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Personalize my Home' }).click();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Follow Studio Sessions' })).toBeVisible();
  expect(requestedKeys).toEqual(['music', 'documentaries', 'science-technology']);
  await page.getByRole('button', { name: 'Enter my Home' }).click();
  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
  expect(saved).toEqual({ keys: [], follows: [] });
});

test('a slow earlier shelf cannot replace the member’s newer interests', async ({ page }) => {
  await mockApi(page, {});
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending' } } }));
  await page.route('**/api/discovery/interests', (route) => route.fulfill({ json: { categories: tasteCategories, selected_keys: [] } }));
  let earlier: Route | undefined;
  await page.route(/\/api\/discovery\/channels(\?|$)/, (route) => {
    const keys = new URL(route.request().url()).searchParams.getAll('keys');
    if (keys.includes('music')) { earlier = route; return; }
    return route.fulfill({ json: { categories: [channelShelves[1]] } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Personalize my Home' }).click();
  await page.getByRole('checkbox', { name: 'Music', exact: true }).check();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect.poll(() => Boolean(earlier)).toBe(true);
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await page.getByRole('checkbox', { name: 'Music', exact: true }).uncheck();
  await page.getByRole('checkbox', { name: 'Film', exact: true }).check();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Popular in Film' })).toBeVisible();
  await earlier!.fulfill({ json: { categories: [channelShelves[0]] } });
  // The completed earlier response has arrived; the active collection stays Film.
  await expect(page.getByRole('heading', { name: 'Popular in Film' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Popular in Music' })).toHaveCount(0);
});


test('existing interests can be cleared without returning on Back', async ({ page }) => {
  await mockApi(page, {});
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending' } } }));
  let interestsRequest: Route | undefined;
  await page.route('**/api/discovery/interests', (route) => { interestsRequest = route; });
  await page.route(/\/api\/discovery\/channels(\?|$)/, (route) => route.fulfill({ json: { categories: channelShelves } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Personalize my Home' }).click();
  await expect.poll(() => Boolean(interestsRequest)).toBe(true);
  await interestsRequest!.fulfill({ json: { categories: tasteCategories, selected_keys: ['music'] } });
  const music = page.getByRole('checkbox', { name: 'Music', exact: true });
  await expect(music).toBeChecked();
  await music.uncheck();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Meet your next favorites' })).toBeVisible();
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(music).not.toBeChecked();
});
