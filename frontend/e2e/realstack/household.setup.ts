import { chooseSelect } from '../select-helper';
import { MEDIA_ROOT, OWNER, OWNER_STATE, expect, test } from './realstack';

test('first owner connects and imports media during onboarding, then signs in again', async ({ page }) => {
  await test.step('first-owner setup', async () => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Make Lumina yours' })).toBeVisible();
    await page.getByLabel('Username').fill(OWNER.username);
    await page.getByLabel('Display name').fill(OWNER.displayName);
    await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
    await page.getByRole('button', { name: 'Create household vault' }).click();
    await expect(page.getByRole('heading', { name: 'Give your vault a home.' })).toBeVisible();
    await page.getByLabel('Folder name').fill('Fixture media');
    await page.getByLabel('Mounted path').fill(MEDIA_ROOT);
    await expect(page.getByRole('radio', { name: 'Household', exact: true })).toBeChecked();
    // household.spec proves a member cannot see the owner's private import.
    await page.getByRole('radio', { name: 'Private', exact: true }).check();
    const scanStarted = page.waitForResponse((response) => response.url().endsWith('/api/admin/imports') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Add and scan' }).click();
    expect((await scanStarted).status()).toBe(202);
    await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Bring your household along.' })).toBeVisible();
    await page.getByRole('button', { name: 'Skip for now', exact: true }).click();
    await page.getByRole('button', { name: 'Explore without setup' }).click();
    await expect(page.getByRole('main', { name: 'Main content' })).toBeVisible();
  });

  await test.step('sign out and sign back in', async () => {
    await page.getByRole('button', { name: /, account menu$/ }).click();  // Sign out lives in the account menu
    await page.getByRole('menuitem', { name: 'Sign out' }).click();
    await expect(page.getByRole('heading', { name: 'Welcome home' })).toBeVisible();
    await page.getByLabel('Username').fill(OWNER.username);
    await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('main', { name: 'Main content' })).toBeVisible();
  });

  await test.step('the connected folder is available in storage settings', async () => {
    await page.goto('/admin/storage');
    const root = page.getByRole('row', { name: /^Fixture media External/ });  // roots are table rows
    await expect(root).toContainText('External · read-only');
    await expect(root).toContainText('Available');
  });

  await test.step('the onboarding scan indexes every fixture file', async () => {
    await page.getByRole('link', { name: 'Imports', exact: true }).click();
    const run = page.getByRole('region', { name: 'Fixture media Complete' });
    await expect(run).toBeVisible({ timeout: 30_000 });
    await expect(run.getByRole('definition').first()).toHaveText('10');
    await expect(run).toContainText('Nothing needs attention.');
  });

  await test.step('Library lenses show Movies and Shows by title, with sidecar posters', async () => {
    await page.goto('/library/movies');
    for (const title of ['Realstack Direct', 'Realstack Remux', 'Realstack Transcode']) {
      await expect(page.getByRole('button', { name: new RegExp(`^${title},`) })).toBeVisible();
    }
    // With a srcset, naturalWidth is divided by the chosen candidate's density; decode the served file on its own for its pixels.
    const servedWidth = async (img: HTMLImageElement) => {
      if (!img.complete || !img.currentSrc) return 0;
      const served = new Image();
      served.src = img.currentSrc;
      await served.decode();
      return served.naturalWidth;
    };
    const moviePoster = page.getByRole('button', { name: /^Realstack Direct,/ }).locator('img.g-art-image');
    await expect.poll(() => moviePoster.evaluate(servedWidth)).toBe(120);
    await page.goto('/library/shows'); // the Shows tab link needs /api/library/sections; the address works before and after
    // Title A–Z: under "Recently added" the first show is a backdrop feature tile, and this show has no backdrop.
    await chooseSelect(page, page.getByRole('combobox', { name: 'Sort' }), 'Title A–Z');
    const show = page.getByRole('button', { name: /^Realstack Show,/ });
    await expect(show).toBeVisible();
    await expect.poll(() => show.locator('img.g-art-image').evaluate(servedWidth)).toBe(120);
    for (const colorScheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme });
      await page.screenshot({ path: `../output/playwright/realstack/series-${colorScheme}.png` });
    }
    await page.emulateMedia({ colorScheme: 'light' });
  });

  await page.context().storageState({ path: OWNER_STATE });
});
