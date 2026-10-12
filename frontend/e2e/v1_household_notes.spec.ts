import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn, user } from './lumina-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/** Timestamped private/household notes in the Watch Notes tool. */

async function openNotes(page: Page, viewport: { width: number; height: number }, scheme: 'light' | 'dark', posted: Array<{ timestamp_ms: number | null }> = []) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  const notes = [
    { id: 'n-general', item_id: item.id, user_id: user.id, visibility: 'private', timestamp_ms: null, body: 'Rewatch with the kids this weekend.', author_display_name: user.display_name, is_owner: true, can_delete: true, created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00' },
    { id: 'n-intro', item_id: item.id, user_id: user.id, visibility: 'private', timestamp_ms: 1_200, body: 'Title card fades in.', author_display_name: user.display_name, is_owner: true, can_delete: true, created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00' },
    { id: 'n-sam', item_id: item.id, user_id: 'member-2', visibility: 'household', timestamp_ms: 61_000, body: 'The harbour shot here is the one Grandma talked about — worth pausing on.', author_display_name: 'Sam', is_owner: false, can_delete: true, created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00' },
  ];
  // Registered after mockApi, so this handler wins for the notes endpoints.
  await page.route('**/api/library/**/notes', async (route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      const created = { id: `n-${notes.length}`, item_id: item.id, user_id: user.id, author_display_name: user.display_name, is_owner: true, can_delete: true, created_at: '2026-01-02T00:00:00', updated_at: '2026-01-02T00:00:00', ...request.postDataJSON() };
      notes.push(created);
      posted.push(created);
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(created) });
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(notes) });
  });
  // Byte-range media so the browser treats the clip as seekable.
  const media = Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64');
  await page.route(`**/api/library/${item.id}/media`, async (route) => {
    const [, start = '0', end] = /bytes=(\d*)-(\d*)/.exec(route.request().headers().range || '') || [];
    const from = Number(start) || 0;
    const to = end ? Number(end) : media.length - 1;
    await route.fulfill({ status: 206, headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${from}-${to}/${media.length}`, 'Content-Type': SYNTHETIC_MEDIA_TYPE }, body: media.subarray(from, to + 1) });
  });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Notes', exact: true }).click();
  await expect(page.getByRole('article', { name: 'Household note from Sam' })).toBeVisible();
}

test('test_notes_timestamp_and_seek', async ({ page }) => {
  const posted: Array<{ timestamp_ms: number | null }> = [];
  await openNotes(page, { width: 1536, height: 960 }, 'dark', posted);
  // The synthetic clip is 1.5s: let it play briefly, then hold its position.
  const video = page.locator('video').first();
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => { if (media.paused) void media.play().catch(() => undefined); return media.currentTime; })).toBeGreaterThan(0.3);
  const position = await video.evaluate((media: HTMLVideoElement) => { media.pause(); return media.currentTime; });

  // Keyboard-only: capture the time, type, share, save.
  await page.getByRole('button', { name: 'Add current time' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('textbox', { name: 'New note' })).toBeFocused();
  await page.keyboard.type('Opening title card');
  await page.getByRole('switch', { name: 'Share with household' }).focus();
  await page.keyboard.press('Space');
  await page.getByRole('button', { name: 'Save note' }).focus();
  await page.keyboard.press('Enter');
  const list = page.getByRole('list', { name: 'Notes on this item' });
  await expect(list.getByRole('article')).toHaveCount(4);
  await expect(list.getByRole('article', { name: 'Household note from You' })).toBeVisible();
  expect(Math.abs((posted[0].timestamp_ms ?? -1) - position * 1000)).toBeLessThanOrEqual(1);

  await list.getByRole('article').filter({ hasText: 'Title card fades in.' }).getByRole('button', { name: 'Seek to 0:01' }).click();
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.currentTime)).toBeCloseTo(1.2, 1);
});

test('test_notes_visual', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openNotes(page, { width: 1536, height: 960 }, 'dark');
  await page.locator('.notes-panel').scrollIntoViewIfNeeded();
  expect(errors).toEqual([]);
});
