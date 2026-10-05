import { expect, test } from '@playwright/test';
import { mockAdminFixtures, mockApi, signIn } from './lumina-mock';

/** Success criterion 6: every control Settings renders sits in a registry row whose ⓘ has text. */

const SECTIONS = ['account', 'playback', 'appearance', 'discovery', 'downloads', 'apps', 'privacy', 'about', 'overview', 'library', 'media', 'requests', 'transcoding', 'ai', 'members', 'tasks', 'backups', 'diagnostics'];
/** The registry's row count (v1_settings_registry.test.ts pins the rows themselves). The Jellyfin history import added two, the household migration one, gallery artwork preparation one. The gallery AI extras added two. The library gallery's anime folders added one. Recommendations added two (history, switch). */
const ROWS = 88; // + members.local-address; + account.two-factor, members.owner-two-factor, overview.source-ports (2.9.0); + members.invitations and members.public-address (2.8 member access); + playback.captions, library.automation and library.editing (2.1.0), playback.mute-words (its own row so the mute switch never repeats its label)

test('every rendered Settings control belongs to a registry row with ⓘ text', async ({ page }) => {
  test.setTimeout(90_000); // 18 sections, each waited on; the default 30 s flakes when other specs share the dev server
  await page.setViewportSize({ width: 1536, height: 960 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  // Advanced on: the walk covers every row, including the technical ones.
  await mockApi(page, { sidebar_collapsed: false, settings_advanced: true });
  await mockAdminFixtures(page);
  await page.goto('/');
  await signIn(page);
  const problems: string[] = [];
  const seen = new Set<string>();
  for (const section of SECTIONS) {
    await page.goto(`/settings/${section}`);
    await expect(page.locator('.g-settings-section-head h2')).toBeVisible();
    await page.waitForTimeout(250);
    const report = await page.locator('.g-settings-pane').evaluate((pane) => ({
      orphans: [...pane.querySelectorAll<HTMLElement>('input, select, textarea, button, a[href], summary, [role="switch"], [role="radio"]')]
        .filter((element) => element.getClientRects().length > 0 && !element.closest('[data-setting-id], .g-save-bar') && !element.matches('.g-settings-back'))
        .map((element) => element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName),
      rows: [...pane.querySelectorAll<HTMLElement>('[data-setting-id]')].map((row) => {
        const info = row.querySelector<HTMLElement>(':scope > .g-setting-head .g-info-button');
        return { id: row.dataset.settingId ?? '', text: (info && document.getElementById(info.getAttribute('aria-controls') ?? '')?.textContent?.trim()) || '' };
      }),
    }));
    for (const orphan of report.orphans) problems.push(`${section}: control outside every registry row: ${orphan}`);
    for (const row of report.rows) {
      seen.add(row.id);
      if (!row.id.startsWith(`${section}.`)) problems.push(`${section}: row ${row.id} belongs to another section`);
      if (row.text.length < 40) problems.push(`${section}: ${row.id} has no ⓘ text`);
    }
  }
  expect(problems).toEqual([]);
  expect(seen.size).toBe(ROWS);
});

test('every member-page control belongs to a members.* row with ⓘ text (member access)', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  await mockAdminFixtures(page);
  await page.goto('/');
  await signIn(page);
  await page.goto('/settings/members/m2');
  const tabs = page.getByRole('tab');
  await expect(tabs).toHaveCount(7);
  const problems: string[] = [];
  const seen = new Set<string>();
  for (const name of await tabs.allTextContents()) {
    await page.getByRole('tab', { name }).click();
    await page.waitForTimeout(200);
    const report = await page.locator('.g-settings-pane').evaluate((pane) => ({
      orphans: [...pane.querySelectorAll<HTMLElement>('input, select, textarea, button, a[href], summary, [role="switch"], [role="radio"]')]
        .filter((element) => element.getClientRects().length > 0 && !element.closest('[data-setting-id], .g-save-bar, .g-member-nav'))
        .map((element) => element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName),
      rows: [...pane.querySelectorAll<HTMLElement>('[data-setting-id]')].map((row) => {
        const info = row.querySelector<HTMLElement>(':scope > .g-setting-head .g-info-button');
        return { id: row.dataset.settingId ?? '', text: (info && document.getElementById(info.getAttribute('aria-controls') ?? '')?.textContent?.trim()) || '' };
      }),
    }));
    for (const orphan of report.orphans) problems.push(`${name}: control outside every row: ${orphan}`);
    for (const row of report.rows) {
      seen.add(row.id);
      if (!row.id.startsWith('members.')) problems.push(`${name}: row ${row.id} is not a members row`);
      if (row.text.length < 40) problems.push(`${name}: ${row.id} has no ⓘ text`);
    }
  }
  expect(problems).toEqual([]);
  expect(seen.size).toBe(26); // + the member page two-step reset row (2.9.0), + the followed channels row (2.10.0)
});
