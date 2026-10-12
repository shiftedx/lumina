import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, mockTranscripts, signIn } from './lumina-mock';

/** Moments: source chapters, member bookmarks and labelled AI suggestions in one seekable timeline. */

const withChapters = { ...item, chapters: [{ start_time: 0, title: 'Cold open' }, { start_time: 40, title: 'The ridge at dawn, a deliberately long source chapter title that must wrap' }] };
const summary = {
  id: 'sum-1', library_item_id: item.id, transcript_id: 'tr-en', transcript_revision: 2, model_id: 'qwen3.8-27b-ninfer-nvfp4-kv', state: 'succeeded', overview: 'An alpine valley.',
  key_points: [{ text: 'The stone bridge floods every spring and is rebuilt by the village each summer.', cue_ordinals: [2, 8], start_ms: 21_000 }],
  chapters: [{ title: 'High pastures', cue_ordinal: 40, start_ms: 60_000 }], dropped_points: 0, error: null, created_at: '2026-01-03T00:00:00Z', completed_at: '2026-01-03T00:00:12Z',
};

async function openMoments(page: Page, viewport: { width: number; height: number }, scheme: 'light' | 'dark', populated: boolean) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  const notes = populated ? [{ id: 'n1', item_id: item.id, visibility: 'private', timestamp_ms: 12_300, body: 'Great line about terraces', is_owner: true, can_delete: true, created_at: '2026-01-04T00:00:00Z' }] : [];
  const created: unknown[] = [];
  await page.route(/\/api\/library(\/library-1(\/notes)?)?(\?.*)?$/, (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ contentType: 'application/json', status, body: JSON.stringify(body) });
    const fixture = populated ? withChapters : item;
    if (path === '/api/library') return json({ items: [fixture], next_cursor: null });
    if (path === `/api/library/${item.id}`) return json(fixture);
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      const note = { id: `new-${created.length}`, item_id: item.id, is_owner: true, can_delete: true, created_at: '2026-01-05T00:00:00Z', ...body };
      created.push(note);
      notes.push(note);
      return json(note, 201);
    }
    return json(notes);
  });
  if (populated) {
    await mockTranscripts(page);
    await page.route(/\/api\/library\/[^/]+\/summary$/, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(summary) }));
  }
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Moments' }).click();
  return created;
}

test('test_moment_create_seek_roundtrip', async ({ page }) => {
  const created = await openMoments(page, { width: 1536, height: 960 }, 'dark', true);
  const list = page.getByRole('list', { name: 'Moments' });
  await expect(list.getByRole('button', { name: /^Seek to/ })).toHaveCount(5);
  await expect(list.getByText('AI-suggested key point · grounded in 2 transcript lines')).toBeVisible();
  await expect(page.locator('[data-chapter-marker][data-moment-origin="you"]')).toHaveCount(1);
  await page.getByRole('textbox', { name: 'Bookmark name (optional)' }).fill('Bridge');
  await page.getByRole('button', { name: /^Bookmark / }).click();
  await expect(list.getByRole('button', { name: /^Seek to .*: Bridge\./ })).toBeVisible();
  expect(created).toHaveLength(1);
  await page.getByRole('switch', { name: 'Show AI suggestions' }).click();
  await expect(list.getByRole('button', { name: /^Seek to/ })).toHaveCount(4);
});

test('test_moments_themes', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openMoments(page, { width: 1536, height: 960 }, 'dark', true);
  await expect(page.getByRole('list', { name: 'Moments' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('test_moments_empty', async ({ page }) => {
  await openMoments(page, { width: 1536, height: 960 }, 'dark', false);
  await expect(page.getByText('No moments yet.')).toBeVisible();
});
