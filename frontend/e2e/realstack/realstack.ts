import path from 'node:path';
import { test as base, expect, type Browser, type Page } from '@playwright/test';

export { expect };
export const ROOT = process.env.REALSTACK_ROOT as string;
export const OWNER_STATE = path.join(ROOT, 'owner.json');
export const MEDIA_ROOT = path.join(ROOT, 'media');
export const OWNER = { username: 'hearth', displayName: 'Hearth Owner', password: 'Lantern-harbor-2026' };

/** Uncaught page errors and server 5xx fail the journey; expected 404s (absent artwork/summary) do not. */
export function watchPage(page: Page): string[] {
  const problems: string[] = [];
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
  page.on('response', (response) => {
    if (response.status() >= 500) problems.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
  });
  return problems;
}

export const test = base.extend<{ guard: void }>({
  guard: [async ({ page }, use) => {
    const problems = watchPage(page);
    await use();
    expect(problems, 'uncaught page errors or server 5xx').toEqual([]);
  }, { auto: true }],
});

/** Owner creates an invite link; a fresh browser context redeems it and lands signed in. */
export async function inviteMember(owner: Page, browser: Browser, username: string, password: string) {
  await owner.goto('/admin/members');
  await owner.getByRole('button', { name: 'Invite someone' }).click();
  const form = owner.getByRole('dialog', { name: 'Invite someone' });
  await form.getByLabel('Email').fill(`${username}@example.com`);
  await form.getByRole('checkbox', { name: 'All libraries' }).check();
  await form.getByLabel('Limits').selectOption('none');
  await form.getByRole('button', { name: 'Copy link instead' }).click();
  const link = await owner.getByRole('textbox', { name: 'Invite link' }).inputValue();
  expect(link).toMatch(/#invite=[\w-]+$/);
  await owner.getByRole('dialog', { name: 'Invitation ready' }).getByRole('button', { name: 'Done' }).click();
  // Test-runner contexts inherit the project's owner storageState; the member must start signed out.
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const member = await context.newPage();
  const problems = watchPage(member);
  await member.goto(link);
  await expect(member.getByRole('heading', { name: 'Join this household' })).toBeVisible();
  await member.getByLabel('Username').fill(username);
  await member.getByLabel('Display name').fill(username[0].toUpperCase() + username.slice(1));
  await member.getByLabel('Password', { exact: true }).fill(password);
  await member.getByRole('button', { name: 'Join household' }).click();
  await member.getByRole('button', { name: 'Skip for now' }).click();
  await expect(member.getByRole('main', { name: 'Main content' })).toBeVisible();
  return { context, member, problems };
}

export async function libraryItemId(page: Page, title: string): Promise<string> {
  const listing = await page.request.get('/api/library?limit=60');
  expect(listing.ok(), `${listing.status()} ${await listing.text()}`).toBe(true);
  const items = (await listing.json()).items as { id: string; title: string }[];
  const item = items.find((entry) => entry.title === title);
  expect(item, `library item ${title}`).toBeTruthy();
  return item!.id;
}

export const videoTime = (page: Page) => page.locator('video').evaluate((video: HTMLVideoElement) => video.currentTime);
