import { mkdirSync } from 'node:fs';
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { continueEntry, mockHome } from './home-mock';
import { mockApi, signIn } from './lumina-mock';
import { episodeSummary } from '../src/test/galleryFixtures';

const days = Array.from({ length: 14 }, (_, index) => ({ date: `2026-10-${String(index + 1).padStart(2, '0')}`, watched_seconds: index % 3 ? (index + 1) * 420 : 0, sessions: index % 3 ? 2 : 0 }));
const summary = { generated_at: '2026-10-14T22:00:00Z', period_days: 14, timezone: 'UTC', watched_seconds: days.reduce((sum, day) => sum + day.watched_seconds, 0), sessions: days.reduce((sum, day) => sum + day.sessions, 0), active_days: days.filter(day => day.sessions).length, latest_activity_at: '2026-10-14T20:00:00Z', days };
for (const [width, scheme] of [[1440, 'light'], [1440, 'dark'], [390, 'dark']] as const) {
  test(`personal activity modal and account actions at ${width} ${scheme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 960 });
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: scheme });
    await mockApi(page, {});
    await mockHome(page, { continueWatching: [continueEntry(episodeSummary(1, 1))] });
    let failed = false;
    await page.route('**/api/me/activity-summary', route => route.fulfill(failed ? { status: 503, json: { detail: 'Unavailable' } } : { json: summary }));
    await page.goto('/'); await signIn(page);
    await expect(page.locator('.h-body:not([inert])')).toBeVisible();
    await expect(page.locator('.h-date, .h-masthead')).toHaveCount(0);
    const trigger = page.locator('.g-profile-trigger');
    await trigger.click();
    const dialog = page.getByRole('dialog', { name: 'Your activity' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/Good (morning|afternoon|evening)/)).toBeVisible();
    await expect(dialog.getByText('UTC', { exact: false }).first()).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'My Settings' })).toBeVisible();
    expect((await dialog.boundingBox())!.width).toBeLessThanOrEqual(width);
    expect((await new AxeBuilder({ page }).include('.member-activity-dialog').analyze()).violations).toEqual([]);
    mkdirSync('../output/member-activity', { recursive: true });
    await page.screenshot({ path: `../output/member-activity/modal-${width}-${scheme}.png` });
    await dialog.getByRole('button', { name: 'Sign out' }).scrollIntoViewIfNeeded();
    await expect(dialog.getByRole('button', { name: 'Sign out' })).toBeVisible();
    await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0); await expect(trigger).toBeFocused();
    failed = true; await trigger.click();
    await expect(dialog.getByRole('button', { name: /Retry|Try again/ })).toBeVisible();
    failed = false; await dialog.getByRole('button', { name: /Retry|Try again/ }).click();
    await expect(dialog.getByRole('button', { name: /Retry|Try again/ })).toHaveCount(0);
    await dialog.getByRole('button', { name: 'My Settings' }).click();
    await expect(dialog).toHaveCount(0); await expect(page).toHaveURL(/\/settings/);
  });
}
