import { expect, test, type Page } from '@playwright/test';
import { aiFeatureReadiness, item, localModels, mockApi, signIn } from './lumina-mock';

/** Admin Tasks and AI sections, member Generate transcript. */

const counts = { download: { running: 1, failed: 2, completed: 40 }, asr: { active: 1, failed: 1 }, summary: { succeeded: 12, interrupted: 1 } };
const base = { attempts: 0, library_item_id: null, finished_at: null, can_cancel: false, can_retry: false, created_at: '2026-09-24T09:10:00Z' };
const tasks = {
  download: [
    { ...base, kind: 'download', id: 'd1', status: 'running', title: 'A very long conference recording title that keeps going to prove the task row wraps without overflowing on phones', detail: 'youtube', owner: 'Alexandria', error: null, can_cancel: true },
    { ...base, kind: 'download', id: 'd2', status: 'failed', title: null, detail: 'twitch', owner: 'Jonah', error: 'ERROR: [twitch:vod] 2244889911: This video is only available to subscribers of the channel', attempts: 2, can_retry: true },
    { ...base, kind: 'download', id: 'd3', status: 'interrupted', title: 'Evening news', detail: 'generic', owner: 'Alexandria', error: 'Interrupted by a server restart; retry to download it again.', can_retry: true },
  ],
  asr: [
    { ...base, kind: 'asr', id: 'a1', status: 'running', title: 'Lecture 4: thermodynamics', detail: 'whisper-large-v3', owner: 'Alexandria', error: null, can_cancel: true, library_item_id: 'x' },
    { ...base, kind: 'asr', id: 'a2', status: 'failed', title: null, detail: 'whisper-large-v3', owner: 'Jonah', error: 'ASR endpoint is unreachable' },
  ],
  summary: [
    { ...base, kind: 'summary', id: 's1', status: 'interrupted', title: 'Lecture 3', detail: 'qwen3.8-27b-ninfer-nvfp4-kv', owner: 'Alexandria', error: null },
  ],
};
const aiConfig = {
  base_url: 'http://192.168.1.121:8080/v1', model: 'qwen3.8-27b-ninfer-nvfp4-kv', has_api_key: true, max_concurrency: 3, context_tokens: 145000,
  asr_base_url: '', asr_model: '', enabled: true, asr_available: false, ai_features_disabled: [], features: aiFeatureReadiness, model_threads: null, model_threads_auto: 3,
};

async function mockAdmin(page: Page, cancels: string[]) {
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route(/\/api\/admin\/tasks/, (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'POST') { cancels.push(url.pathname); return route.fulfill(json({ status: 'canceling' })); }
    const kind = (url.searchParams.get('kind') || 'download') as keyof typeof tasks;
    return route.fulfill(json({ items: tasks[kind], next_cursor: kind === 'download' ? 'next' : null, counts }));
  });
  await page.route('**/api/admin/ai/config', (route) => route.fulfill(json(route.request().method() === 'PUT' ? { ...aiConfig, has_api_key: false } : aiConfig)));
  await page.route('**/api/admin/models', (route) => route.fulfill(json({ models: localModels })));
  await page.route('**/api/admin/ai/test', (route) => route.fulfill(json({ ok: true, models: ['qwen3.8-27b-ninfer-nvfp4-kv', 'bge-m3-embedding'], model_available: true, error: null })));
}

async function open(page: Page) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false, settings_advanced: true }); // Tasks and the assistant key are advanced
}

test('test_admin_tasks_and_ai', async ({ page }) => {
  const errors: string[] = [];
  const cancels: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page);
  await mockAdmin(page, cancels);
  await page.goto('/admin/tasks');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Tasks' })).toBeVisible();
  await expect(page.getByText('Private download')).toBeVisible();
  await expect(page.getByText(/only available to subscribers/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Load more' })).toBeVisible();

  await page.getByRole('tab', { name: /Transcriptions/ }).click();
  await expect(page.getByText('Lecture 4: thermodynamics')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect.poll(() => cancels).toEqual(['/api/admin/tasks/asr/a1/cancel']);

  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'AI & models' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'AI & models' })).toBeVisible();
  await expect(page.getByRole('switch', { name: 'Semantic search' })).toBeVisible();
  await expect(page.getByLabel(/^API key/)).toHaveValue('');
  await expect(page.getByText(/Transcripts come only from source captions/)).toBeVisible();
  await page.getByRole('button', { name: /Test connection/ }).click();
  await expect(page.getByText('Connected.')).toBeVisible();
  expect(errors).toEqual([]);
});

test('test_generate_transcript', async ({ page }) => {
  await open(page);
  let started = false;
  const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/enrichment', (route) => route.fulfill(json({ ai_summaries: true, asr: true })));
  await page.route(`**/api/library/${item.id}/transcripts`, (route) => route.fulfill(json([])));
  await page.route(`**/api/library/${item.id}/transcripts/asr`, (route) => {
    if (route.request().method() === 'POST') started = true;
    return route.fulfill(started
      ? json({ id: 'asr-1', library_item_id: item.id, model_id: 'whisper', state: 'running', transcript_id: null, error: null, created_at: '2026-09-24T09:00:00Z', completed_at: null })
      : json({ detail: 'No transcription job yet' }, 404));
  });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Transcript' }).click();
  await expect(page.getByText('No captions for this video.')).toBeVisible();
  await page.getByRole('button', { name: 'Generate transcript' }).click();
  await expect(page.getByText(/Transcribing the audio/)).toBeVisible();
});

test('test_ai_failure_no_playback_impact', async ({ page }) => {
  await open(page);
  await page.route('**/api/enrichment', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"down"}' }));
  await page.route(`**/api/library/${item.id}/transcripts`, (route) => route.fulfill({ status: 500, body: 'boom' }));
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  const video = page.locator('video').first();
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.readyState)).toBeGreaterThan(0);
  await page.getByRole('tab', { name: 'Summary' }).click();
  await expect(page.getByText('No transcript for this video.')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Transcript' })).toHaveCount(0);
});
