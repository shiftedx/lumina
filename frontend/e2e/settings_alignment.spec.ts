import { expect, test, type Locator } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { SETTINGS_CATEGORIES } from '../src/features/settings/navigation';
import { mockAdminFixtures, mockApi, openSettingsArea, signIn } from './lumina-mock';

// Check rendered relationships, including hints, expanded forms and long member names.
// A stylesheet can be valid while a label, password wrapper or dropdown drifts off its grid.
async function alignment(scope: Locator) {
  return scope.evaluate((pane) => {
    const visible = (element: Element) => element.getClientRects().length > 0;
    const controls = [...pane.querySelectorAll<HTMLElement>('.g-input, .g-select-trigger')].filter(visible);
    const problems: string[] = [];
    const rect = (element: Element) => element.getBoundingClientRect();
    for (const control of controls) {
      if (control.tagName !== 'TEXTAREA' && Math.abs(rect(control).height - 44) > 1) problems.push(`${control.getAttribute('aria-label') ?? control.id}: inconsistent control height`);
      const field = control.closest('.g-field');
      const label = field?.querySelector<HTMLElement>(':scope > .g-field-label:not(.sr-only)');
      if (!field || !label || !visible(label)) continue;
      const l = rect(label); const c = rect(control); const f = rect(field);
      const name = label.textContent;
      if ((name?.length ?? 0) > 12 && l.width < 80) problems.push(`${name}: label wraps into a narrow text column`);
      if (c.left < f.left - 1 || c.right > f.right + 1) problems.push(`${name}: control escapes field`);
      if (l.right <= c.left && c.left - l.left > 12) {
        if (control.tagName === 'TEXTAREA') {
          if (Math.abs(l.top + parseFloat(getComputedStyle(label).paddingTop) - c.top - 12) > 1) problems.push(`${name}: multiline label is not aligned to input text`);
        } else if (Math.abs(l.top + l.height / 2 - c.top - c.height / 2) > 1) problems.push(`${name}: label and control are not centered (${(l.top + l.height / 2 - c.top - c.height / 2).toFixed(1)}px)`);
      } else if (Math.abs(l.left - c.left) > 1 || c.top - l.bottom < 3 || c.top - l.bottom > 12) problems.push(`${name}: stacked label is not aligned above control`);
    }
    for (const rows of pane.querySelectorAll('.g-subrows')) {
      const fields = [...rows.querySelectorAll<HTMLElement>(':scope > .g-field .g-input, :scope > .g-field .g-select-trigger')].filter(visible);
      for (const field of fields) {
        const peers = fields.filter((other) => Math.abs(rect(other).left - rect(field).left) < 12);
        if (peers.some((other) => Math.abs(rect(other).right - rect(field).right) > 1 || Math.abs(rect(other).left - rect(field).left) > 1)) problems.push('Nested fields do not share column edges');
      }
    }
    const meters = [...pane.querySelectorAll('.activity-stats > .activity-stat')].filter((meter) => meter.querySelector('.activity-value'));
    for (const meter of meters) {
      const value = meter.querySelector('.activity-value')!;
      for (const other of meters.filter((other) => other !== meter && Math.abs(rect(other).top - rect(meter).top) <= 1)) {
        if (Math.abs(rect(value).top - rect(other.querySelector('.activity-value')!).top) > 1) problems.push('Server metric values do not share a baseline');
      }
    }
    for (const group of pane.querySelectorAll('.admin-test-form, .admin-rule-fields, .g-req-map-row, .g-day-range')) {
      const fields = [...group.querySelectorAll<HTMLElement>('.g-input, .g-select-trigger')].filter(visible);
      for (const field of fields) {
        const peers = fields.filter((other) => other !== field && Math.abs(rect(other).left - rect(field).left) > 1 && Math.abs(rect(other).top - rect(field).top) < 44);
        if (peers.some((other) => Math.abs(rect(other).top - rect(field).top) > 1)) problems.push('Adjacent inputs and dropdowns do not share a baseline');
      }
      if (group.matches('.g-day-range') && fields.length === 2 && Math.abs(rect(fields[0]).left - rect(fields[1]).left) > 1 && Math.abs(rect(fields[0]).top - rect(fields[1]).top) > 1) problems.push('Viewing-hour inputs wrap into staggered rows');
    }
    return { controls: controls.length, height: Math.round(rect(pane).height), problems, overflow: document.documentElement.scrollWidth > innerWidth + 1 };
  });
}

for (const scheme of ['light', 'dark'] as const) for (const width of [320, 390, 975, 1440]) {
  test(`settings labels and controls align in ${scheme} at ${width}px`, async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width, height: 960 });
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await mockApi(page, {}); await mockAdminFixtures(page);
    await page.route('**/api/me/two-factor', (route) => route.fulfill({ json: { enabled: false, required: false, recovery_codes_left: 0 } }));
    await page.route('**/api/requests/notifications', (route) => route.fulfill({ json: { enabled: false, email: null } }));
    await page.route('**/api/admin/updates', (route) => route.fulfill({ json: { current_version: '0.3.4', latest_release: null, status: 'not_checked', checked_at: null, error: null, checking: false, automatic_checks: true, email_notifications: false, email_configured: true } }));
    await page.goto('/settings'); await signIn(page);
    const reports: { area: string; controls: number; problems: string[]; overflow: boolean }[] = [];
    const inspect = async (area: string, scope = page.locator('.g-settings-pane')) => {
      await page.evaluate(() => document.fonts.ready);
      const report = await alignment(scope);
      reports.push({ area, ...report });
      if (process.env.SETTINGS_ALIGNMENT_OUT) {
        mkdirSync(process.env.SETTINGS_ALIGNMENT_OUT, { recursive: true });
        writeFileSync(join(process.env.SETTINGS_ALIGNMENT_OUT, `report-${width}-${scheme}.json`), JSON.stringify(reports, null, 2));
      }
      expect(report.problems, area).toEqual([]);
      expect(report.overflow, area).toBe(false);
      if (process.env.SETTINGS_ALIGNMENT_OUT) {
        mkdirSync(process.env.SETTINGS_ALIGNMENT_OUT, { recursive: true });
        await page.screenshot({ fullPage: true, path: join(process.env.SETTINGS_ALIGNMENT_OUT, `${area}-${width}-${scheme}.png`) });
      }
    };
    for (const category of SETTINGS_CATEGORIES) {
      await openSettingsArea(page, category.label);
      await expect(page.locator('[data-setting-id]').first()).toBeVisible();
      await expect(page.locator('.g-settings-pane [aria-busy="true"]')).toHaveCount(0);
      for (const summary of await page.locator('.g-settings-pane details > summary').all()) if (!(await summary.evaluate((element) => (element.parentElement as HTMLDetailsElement).open))) await summary.click();
      if (category.id === 'library') await page.getByRole('button', { name: 'Add rule', exact: true }).click();
      await inspect(category.id);
      if (category.id === 'library') await page.getByRole('button', { name: 'Discard', exact: true }).click();
      if (category.id === 'household') {
        await page.getByRole('button', { name: 'Invite someone', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: 'Invite someone' });
        await expect(dialog.getByRole('combobox', { name: 'Limits', exact: true })).toBeVisible();
        await inspect('invitation', dialog);
        await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      }
    }
    await page.goto('/settings/members/m2');
    await expect(page.locator('[data-setting-id="members.resources"] input').first()).toBeVisible();
    const hours = page.getByRole('switch', { name: 'Viewing hours', exact: true });
    if (!(await hours.isChecked())) await hours.click();
    await expect(page.getByLabel('Monday from', { exact: true })).toBeVisible();
    await inspect('member');
    expect(reports.reduce((total, report) => total + report.controls, 0)).toBeGreaterThan(100);
    if (process.env.SETTINGS_ALIGNMENT_OUT) writeFileSync(join(process.env.SETTINGS_ALIGNMENT_OUT, `report-${width}-${scheme}.json`), JSON.stringify(reports, null, 2));
  });
}
