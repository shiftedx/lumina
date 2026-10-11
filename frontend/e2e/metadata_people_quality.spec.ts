import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

import { docFixture, fieldState } from '../src/features/titles/editor/editorFixtures';
import { mockApi, mockTitles, signIn } from './lumina-mock';
import { mockMetadataEditor } from './metadata-editor-mock';

const OUT = join(process.cwd(), '..', 'output', 'app-quality', 'people-after');
const LONG_NAME = 'Alexandria Whitcombe-Harrington';
const LONG_ROLE = 'Keeper of the North Lighthouse';

async function openPeople(page: Page, viewport: { width: number; height: number }, scheme: 'light' | 'dark') {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  await mockTitles(page);
  const base = docFixture('series');
  const doc = docFixture('series', {
    title_id: 'series-1',
    name: 'Harbor Lights: The Long Household Archive Edition',
    fields: {
      ...base.fields,
      people: fieldState([
        { person_id: 'person-1', name: LONG_NAME, role: LONG_ROLE, type: 'Actor' },
        { person_id: 'person-2', name: 'Sam Rivers', role: 'Archivist', type: 'Director' },
      ]),
    },
  });
  await mockMetadataEditor(page, { doc });
  await page.goto('/title/series-1/edit?tab=people');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Cast and crew' })).toBeVisible();
}

test('phone rows give name and role a full line while every credit action remains available', async ({ page }) => {
  await openPeople(page, { width: 390, height: 844 }, 'light');

  const name = page.getByLabel('Name 1');
  const role = page.getByLabel('Role 1');
  const type = page.getByRole('combobox', { name: 'Type 1' });
  const actions = [
    page.getByRole('button', { name: `Move up ${LONG_NAME}` }),
    page.getByRole('button', { name: `Move down ${LONG_NAME}` }),
    page.getByRole('button', { name: `Edit ${LONG_NAME} everywhere` }),
    page.getByRole('button', { name: `Remove ${LONG_NAME}` }),
  ];
  const [nameBox, roleBox, typeBox, ...actionBoxes] = await Promise.all([
    name.boundingBox(), role.boundingBox(), type.boundingBox(), ...actions.map((action) => action.boundingBox()),
  ]);

  expect(nameBox).not.toBeNull();
  expect(roleBox).not.toBeNull();
  expect(typeBox).not.toBeNull();
  expect(nameBox!.width).toBeGreaterThan(300);
  expect(roleBox!.width).toBeGreaterThan(300);
  expect(roleBox!.y).toBeGreaterThanOrEqual(nameBox!.y + nameBox!.height);
  for (const box of actionBoxes) {
    expect(box).not.toBeNull();
    expect(Math.abs(box!.y - typeBox!.y)).toBeLessThanOrEqual(2);
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  }
  await expect(actions[0]).toBeDisabled();
  await expect(actions[1]).toBeEnabled();
  await expect(actions[2]).toBeEnabled();
  await expect(actions[3]).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await actions[1].focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('Name 2')).toHaveValue(LONG_NAME);
  await expect(page.getByRole('button', { name: `Move up ${LONG_NAME}` })).toBeFocused();
});

for (const variant of [
  { name: 'desktop-light', viewport: { width: 1440, height: 900 }, scheme: 'light' },
  { name: 'desktop-dark', viewport: { width: 1440, height: 900 }, scheme: 'dark' },
  { name: 'phone-light', viewport: { width: 390, height: 844 }, scheme: 'light' },
  { name: 'phone-dark', viewport: { width: 390, height: 844 }, scheme: 'dark' },
] as const) {
  test(`capture People editor ${variant.name}`, async ({ page }) => {
    mkdirSync(OUT, { recursive: true });
    await openPeople(page, variant.viewport, variant.scheme);
    await page.screenshot({ fullPage: true, path: join(OUT, `metadata-people-${variant.name}.png`) });
  });
}
