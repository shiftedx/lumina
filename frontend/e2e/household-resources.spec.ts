import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { HouseholdResources, MemberResources, ResourceAllowances, ResourceOverrides } from '../src/types';
import { mockAdminFixtures, mockApi, signIn } from './lumina-mock';

const screenshots = join(dirname(fileURLToPath(import.meta.url)), '../../output/playwright/household-resources');

const defaults: ResourceAllowances = {
  remote_stream_bps: 7_250_000,
  remote_member_bps: 10_000_000,
  playback_sessions: 3,
  video_encodes: 2,
  download_running: 2,
  download_queued: 4,
  ai_running: 1,
  ai_queued: 3,
};

async function mockResources(page: Page) {
  let household: HouseholdResources = { upload_bps: null, lan_networks: [], defaults: { ...defaults } };
  let member: MemberResources = { overrides: {}, effective: { ...defaults } };
  const householdWrites: HouseholdResources[] = [];
  const memberWrites: ResourceOverrides[] = [];

  await mockApi(page, {});
  await mockAdminFixtures(page);
  await page.route('**/api/admin/resources', async (route) => {
    if (route.request().method() === 'PUT') {
      household = route.request().postDataJSON() as HouseholdResources;
      householdWrites.push(household);
      member = { ...member, effective: { ...household.defaults, ...member.overrides } };
    }
    await route.fulfill({ contentType: 'application/json', json: household });
  });
  await page.route('**/api/admin/members/m2/resources', async (route) => {
    if (route.request().method() === 'PUT') {
      const overrides = route.request().postDataJSON() as ResourceOverrides;
      member = { overrides, effective: { ...household.defaults, ...overrides } };
      memberWrites.push(overrides);
    }
    await route.fulfill({ contentType: 'application/json', json: member });
  });
  return { householdWrites, memberWrites };
}

async function signInAndOpenOwnerResources(page: Page) {
  await page.goto('/');
  await signIn(page);
  await page.goto('/settings/members?page=resources');
  await expect(page.getByRole('heading', { name: 'Household resources' })).toBeVisible();
}

async function openMemberResources(page: Page, phone = false) {
  await page.goto('/settings/members/m2');
  if (phone) await page.getByRole('combobox', { name: 'Member settings page' }).selectOption('resources');
  else await page.getByRole('tab', { name: 'Resources', exact: true }).click();
  await expect(page.getByRole('form', { name: 'Member resources' })).toBeVisible();
}

test('owner configures household resources and a member can override then resume inheritance', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const writes = await mockResources(page);
  await signInAndOpenOwnerResources(page);

  await page.getByLabel('Remote upload budget', { exact: true }).fill('37.125');
  await page.getByLabel('Member remote bandwidth', { exact: true }).fill('10');
  await page.getByLabel('Running downloads', { exact: true }).fill('2');
  await page.getByRole('button', { name: 'Save resources', exact: true }).click();
  await expect(page.getByText('Household resources saved.', { exact: true })).toBeVisible();
  expect(writes.householdWrites).toEqual([{
    upload_bps: 37_125_000,
    lan_networks: [],
    defaults: { ...defaults, remote_member_bps: 10_000_000, download_running: 2 },
  }]);

  await openMemberResources(page);
  const memberBandwidth = page.getByLabel('Member remote bandwidth', { exact: true });
  await expect(memberBandwidth).toHaveAttribute('placeholder', 'Inherit');
  await expect(page.getByText('Inherits 10 Mbps', { exact: false })).toBeVisible();
  await memberBandwidth.fill('12.5');
  await page.getByRole('button', { name: 'Save resources', exact: true }).click();
  await expect(page.getByText('Member resources saved.', { exact: true })).toBeVisible();
  expect(writes.memberWrites).toEqual([{ remote_member_bps: 12_500_000 }]);

  await memberBandwidth.fill('');
  await page.getByRole('button', { name: 'Save resources', exact: true }).click();
  await expect(page.getByText('Member resources saved.', { exact: true })).toBeVisible();
  expect(writes.memberWrites).toEqual([{ remote_member_bps: 12_500_000 }, {}]);
  await expect(page.getByText('Inherits 10 Mbps', { exact: false })).toBeVisible();
});

test('resource controls remain reachable at desktop and 320px phone widths', async ({ page }) => {
  mkdirSync(screenshots, { recursive: true });
  const writes = await mockResources(page);

  await page.setViewportSize({ width: 1440, height: 1000 });
  await signInAndOpenOwnerResources(page);
  await expect(page.getByLabel('Remote upload budget', { exact: true })).toBeVisible();
  await page.screenshot({ fullPage: true, path: join(screenshots, 'owner-desktop.png') });

  await page.setViewportSize({ width: 320, height: 900 });
  await openMemberResources(page, true);
  const report = await page.locator('main').evaluate((main) => ({
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
    controls: [...main.querySelectorAll<HTMLElement>('input, button')]
      .filter((control) => control.getBoundingClientRect().height > 0)
      .map((control) => {
        const box = control.getBoundingClientRect();
        return { right: box.right, left: box.left };
      }),
  }));
  expect(report.horizontalOverflow).toBe(false);
  expect(report.controls).not.toEqual([]);
  expect(report.controls.every(({ left, right }) => left >= 0 && right <= 320)).toBe(true);
  await page.getByLabel('Running downloads', { exact: true }).fill('1');
  await page.getByRole('button', { name: 'Save resources', exact: true }).click();
  expect(writes.memberWrites).toEqual([{ download_running: 1 }]);
  await page.screenshot({ fullPage: true, path: join(screenshots, 'member-phone-320.png') });
});
