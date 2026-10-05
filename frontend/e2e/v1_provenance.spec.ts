import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** Overview source provenance and related library context (stored facts only). */

const provenance = {
  origin: 'saved', provider: 'Youtube', original_url: 'https://www.youtube.com/watch?v=synthetic-1', channel: 'Lumina fixture', channel_url: 'https://www.youtube.com/@lumina-fixture',
  uploaded_on: '2025-06-14', saved_at: '2026-01-01T00:00:00Z', storage_label: 'Library', storage_mode: 'managed', media_state: 'available', file_size: 734_003_200,
  format: { container: 'mov,mp4,m4a', video_codec: 'h264', audio_codec: 'aac', width: 1920, height: 1080 }, notes_count: 3,
  related: [
    { reason: 'same_channel', name: 'Lumina fixture', count: 7, items: [{ id: 'r1', title: 'Life Below Zero Degrees' }, { id: 'r2', title: 'Cities That Breathe: a deliberately long related title that must wrap without overflowing' }] },
    { reason: 'same_series', name: 'A Different Tomorrow', count: 2, items: [{ id: 'r3', title: 'The Regenerative Coast' }] },
  ],
};

async function openOverview(page: Page, body: object | null) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.route(/\/api\/library\/[^/]+\/provenance$/, (route) => route.fulfill(body
    ? { contentType: 'application/json', body: JSON.stringify(body) }
    : { contentType: 'application/json', status: 500, body: '{"detail":"boom"}' }));
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  // A saved web video keeps its provenance in the side column's details.
  await page.locator('.g-watch-details summary').click();
}

test('test_provenance_panel', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openOverview(page, provenance);
  await expect(page.getByText('Saved from Youtube')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Life Below Zero Degrees' })).toHaveAttribute('href', '/watch/library/r1');
  expect(errors).toEqual([]);
});

test('test_provenance_error_state', async ({ page }) => {
  await openOverview(page, null);
  await expect(page.getByText('Source details are unavailable right now.')).toBeVisible();
});
