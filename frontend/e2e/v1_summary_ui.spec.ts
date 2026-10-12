import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, mockTranscripts, signIn } from './lumina-mock';

/** Summary tool: grounded result with evidence seek, honest empty/generating states. */

const summary = {
  id: 'sum-1', library_item_id: item.id, transcript_id: 'tr-en', transcript_revision: 2, model_id: 'qwen3.8-27b-ninfer-nvfp4-kv', state: 'succeeded',
  overview: 'A portrait of an alpine valley where farming families treat conservation as daily habit, rebuilding a flood-prone stone bridge each summer and following the herds to the high pastures.',
  key_points: [
    { text: 'Terrace farming here has continued for eleven generations.', cue_ordinals: [1, 7], start_ms: 1500 },
    { text: 'The stone bridge floods every spring and is rebuilt by the village each summer.', cue_ordinals: [2], start_ms: 3000 },
    { text: 'Conservation is framed as a household habit rather than a policy.', cue_ordinals: [3, 9], start_ms: 4500 },
    { text: 'Snowmelt feeding the lake underpins nearly all life in the valley.', cue_ordinals: [5], start_ms: 7500 },
  ],
  chapters: [{ title: 'Morning on the ridge', cue_ordinal: 0, start_ms: 0 }, { title: 'The bridge', cue_ordinal: 2, start_ms: 3000 }, { title: 'High pastures', cue_ordinal: 4, start_ms: 6000 }],
  dropped_points: 2, error: null, created_at: '2026-01-03T00:00:00Z', completed_at: '2026-01-03T00:00:12Z',
};

async function openSummary(page: Page, withSummary: 'ready' | 'generating') {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockTranscripts(page);
  await page.route(/\/api\/(library\/[^/]+\/summar|summaries\/)/, (route) => {
    const running = { ...summary, id: 'sum-2', state: 'running', overview: null, key_points: [], chapters: [], dropped_points: 0 };
    const body = withSummary === 'ready' ? summary : route.request().method() === 'POST' || route.request().url().includes('/api/summaries/') ? running : null;
    return route.fulfill(body ? { contentType: 'application/json', status: 200, body: JSON.stringify(body) } : { contentType: 'application/json', status: 404, body: '{"detail":"No summary yet"}' });
  });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Summary' }).click();
}

test('test_summary_evidence_opens_cue', async ({ page }) => {
  await openSummary(page, 'ready');
  await page.locator('video').first().evaluate((media: HTMLVideoElement) => {
    const native = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!;
    Object.defineProperty(media, 'currentTime', { configurable: true, get: () => native.get!.call(media), set: (value: number) => { (window as unknown as { seekTo: number }).seekTo = value; native.set!.call(media, value); } });
  });
  await page.getByRole('button', { name: 'Play evidence at 0:07' }).click();
  expect(await page.evaluate(() => (window as unknown as { seekTo: number }).seekTo)).toBe(7.5);
  await expect(page.getByText('2 points omitted: no supporting transcript evidence.')).toBeVisible();
});

test('test_summary_generating', async ({ page }) => {
  await openSummary(page, 'generating');
  await page.getByRole('button', { name: 'Generate summary' }).click();
  await expect(page.getByText('Generating summary on your local model…')).toBeVisible();
});
