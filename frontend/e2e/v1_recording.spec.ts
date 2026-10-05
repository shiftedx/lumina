import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** Recording lifecycle states: limits while live, honest end reasons, restart gap, keep. */

const LIVE_URL = 'https://www.youtube.com/watch?v=LIVEREC1';
const TITLE = 'A very long live broadcast title that keeps going to check wrapping on narrow screens';

const recording = (phase: string, extra: Record<string, unknown> = {}) => ({
  id: 'rec-s35', source_url: LIVE_URL, title: TITLE, extractor: 'youtube', status: phase, stop_requested: false, cancel_requested: false,
  media: phase === 'live'
    ? { status: 'recording', library_item_id: null, end_reason: null }
    : { status: 'partial', library_item_id: 'item-s35', end_reason: 'size_limit' },
  chat: phase === 'live' ? { status: 'capturing', chat_asset_id: null } : { status: 'completed', chat_asset_id: 'chat-s35' },
  capture_origin: 'live_edge', history: phase === 'live' ? 'pending' : 'from_edge', awaiting_fallback_choice: false, waiting_reason: null,
  resumed_after_restart: phase !== 'live', kept: false, max_runtime_seconds: 21600, max_bytes: 8 * 1024 ** 3,
  created_at: '2026-07-20T00:00:00', ...extra,
});

async function open(page: Page, phase: 'live' | 'partial', scheme: 'light' | 'dark', viewport: { width: number; height: number }, kept: boolean[] = []) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await page.route('**/api/preview', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
    kind: 'video', title: TITLE, webpage_url: LIVE_URL, extractor: 'youtube', extractor_key: 'Youtube', media_kind: 'video', entries: [],
    capabilities: { provider: 'youtube', lifecycle: 'live', can_play: true, can_acquire: false, acquire_reason: 'live_acquisition_not_supported', can_record: true, record_reason: null, chat: { live: 'unavailable', replay: 'unavailable' } },
    playback: { status: 'unsupported', stream_id: 'none', has_video: true, has_audio: true, seekable: false, live: true, fallback_code: 'unsupported', fallback_message: 'Preview only in this test.' },
    raw: { id: 'LIVEREC1', webpage_url: LIVE_URL, uploader: 'Lumina fixture', is_live: true, extractor: 'youtube' },
  }) }));
  await page.route(/\/api\/live-recordings(\/|$|\?)/, (route) => {
    const request = route.request();
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (request.url().includes('/keep')) {
      const next = (request.postDataJSON() as { kept: boolean }).kept;
      kept.push(next);
      return json(recording(phase, { kept: next }));
    }
    if (new URL(request.url()).pathname === '/api/live-recordings') return json({ items: [recording(phase)], next_cursor: null });
    return json(recording(phase));
  });
  await page.goto(`/watch?url=${encodeURIComponent(LIVE_URL)}`);
  await signIn(page);
  const region = page.getByRole('region', { name: 'Live recording' });
  await expect(region).toBeVisible();
  return region;
}

test('test_recording_states_live_and_partial', async ({ page }) => {
  const live = await open(page, 'live', 'dark', { width: 1536, height: 960 });
  await expect(live.getByText('Stops automatically after 6 h or 8 GB. Saved privately to your Library as a recording.')).toBeVisible();
  await expect(live.getByRole('button', { name: 'Stop & save' })).toBeVisible();
});

test('test_recording_keep_after_partial', async ({ page }) => {
  const kept: boolean[] = [];
  const region = await open(page, 'partial', 'light', { width: 1536, height: 960 }, kept);
  await expect(region.getByText('Stopped at the recording size limit.')).toBeVisible();
  await expect(region.getByText(/Lumina restarted during this recording/)).toBeVisible();
  const keep = region.getByRole('checkbox', { name: /Keep this recording/ });
  await keep.focus();
  await page.keyboard.press('Space');
  await expect(keep).toBeChecked();
  expect(kept).toEqual([true]);
});

// The long title must wrap inside the recording region on a phone; the route-level
// sweep never reaches these recording states.
for (const phase of ['live', 'partial'] as const) {
  test(`test_recording_no_overflow_phone ${phase}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const region = await open(page, phase, 'dark', { width: 390, height: 844 });
    await region.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    expect(errors).toEqual([]);
  });
}

test('test_recording_retention_admin', async ({ page }) => {
  const saved: unknown[] = [];
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false, settings_advanced: true }); // live recording cleanup is an advanced row
  const json = (body: unknown) => ({ contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/admin/storage/roots', (route) => route.fulfill(json([])));
  await page.route('**/api/admin/storage/rules', (route) => route.fulfill(json({ revision: 1, default_root_id: null, rules: [] })));
  await page.route('**/api/admin/recording-retention', (route) => {
    const put = route.request().method() === 'PUT';
    if (put) saved.push(route.request().postDataJSON());
    return route.fulfill(json(put ? route.request().postDataJSON() : { keep_days: 30, max_gb: 0 }));
  });
  await page.goto('/admin/storage');
  await signIn(page);
  const form = page.getByRole('form', { name: 'Live recording cleanup' });
  await expect(form.getByLabel('Keep recordings for (days)')).toHaveValue('30');
  await form.getByLabel('Total size for recordings (GB)').fill('500');
  await form.getByRole('button', { name: 'Save cleanup policy' }).click();
  await expect(form.getByRole('status')).toHaveText('Saved.');
  expect(saved).toEqual([{ keep_days: 30, max_gb: 500 }]);
});
