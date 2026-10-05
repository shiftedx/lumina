import { MEDIA_ROOT, OWNER, OWNER_STATE, expect, test } from './realstack';

test('first owner sets up the vault, signs in again, adds an external root and imports it', async ({ page }) => {
  await test.step('first-owner setup', async () => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Make Lumina yours' })).toBeVisible();
    await page.getByLabel('Username').fill(OWNER.username);
    await page.getByLabel('Display name').fill(OWNER.displayName);
    await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
    await page.getByRole('button', { name: 'Create household vault' }).click();
    await page.getByRole('button', { name: 'Skip for now' }).click();
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

  await test.step('admin adds the fixture folder as an external storage root', async () => {
    await page.goto('/admin/storage');
    await page.getByLabel('Container path').fill(MEDIA_ROOT);
    await page.getByLabel('Label').fill('Fixture media');
    await page.getByLabel('Mode').selectOption({ label: 'External — read-only, for imports' });
    await page.getByRole('button', { name: 'Add root' }).click();
    const root = page.getByRole('row', { name: /^Fixture media External/ });  // roots are table rows
    await expect(root).toContainText('External · read-only');
    await expect(root).toContainText('Available');
  });

  await test.step('Import now indexes every fixture file (shared by default, chosen private here)', async () => {
    const root = page.getByRole('group', { name: /Import Fixture media now/ });  // the import step opens under the new root's row
    await expect(root.getByRole('radio', { name: /Household/ })).toBeChecked();
    // household.spec proves a member cannot see the owner's private import.
    await root.getByRole('radio', { name: /Private/ }).check();
    await root.getByRole('button', { name: 'Import now' }).click();
    await expect(page.getByText('Import started. Its progress shows under Imports.')).toBeVisible();
    const run = page.getByRole('region', { name: 'Fixture media Complete' });
    await expect(run).toBeVisible({ timeout: 30_000 });
    await expect(run.getByRole('definition').first()).toHaveText('8');
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
    await page.getByRole('combobox', { name: 'Sort' }).selectOption('name');
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
