import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** Read-only source chat replay, auth-only live chat explained, household notes kept separate. */

const REPLAY_URL = 'https://www.youtube.com/watch?v=REPLAY1';
const TWITCH_URL = 'https://www.twitch.tv/luminafixture';

const preview = (url: string, provider: string, lifecycle: string, chat: Record<string, unknown>) => ({
  kind: 'video', title: 'Harbour festival — the full evening broadcast with every performance', webpage_url: url, extractor: provider, extractor_key: provider, media_kind: 'video', entries: [],
  capabilities: { provider, lifecycle, can_play: false, play_reason: 'segmented_transport_not_supported', can_acquire: false, acquire_reason: 'segmented_transport_not_supported', can_record: false, chat },
  raw: { id: 'REPLAY1', webpage_url: url, uploader: 'Lumina fixture', duration: 600, extractor: provider },
});

const events = [
  { id: 'e1', offset_ms: 0, kind: 'message', text: 'Evening everyone, the lights just came on over the water', moderation: 'visible', author: { name: 'Ada', badges: [] } },
  { id: 'e2', offset_ms: 0, kind: 'paid_message', amount: '$5.00', text: '<b>not bold</b> — shown as text', moderation: 'visible', author: { name: 'Grace', badges: ['moderator'] } },
  { id: 'e3', offset_ms: 0, kind: 'message', text: 'removed', moderation: 'deleted', author: { name: 'Mallory', badges: [] } },
];

async function open(page: Page, url: string, body: unknown, scheme: 'light' | 'dark', viewport: { width: number; height: number }) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await page.route('**/api/preview', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) }));
  await page.route('**/api/chat-replay/**', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
    source_identity: 'youtube:REPLAY1', status: 'ready', event_count: events.length, truncated: false, dropped_malformed: 0, events,
  }) }));
  await page.goto(`/watch?url=${encodeURIComponent(url)}`);
  await signIn(page);
}

test('test_replay_chat_is_read_only_source_chat', async ({ page }) => {
  await open(page, REPLAY_URL, preview(REPLAY_URL, 'youtube', 'completed_live', { live: 'unavailable', replay: 'available' }), 'dark', { width: 1536, height: 960 });
  const rail = page.getByRole('complementary', { name: 'Replay chat from YouTube' });
  await expect(rail.getByText('Public chat saved by YouTube. Read-only — Lumina never posts to it. Talk with your household in Notes.')).toBeVisible();
  await expect(rail.getByText('<b>not bold</b> — shown as text')).toBeVisible();
  await expect(rail.locator('b')).toHaveCount(0);
  await expect(rail.getByRole('textbox')).toHaveCount(0); // no composer: nothing can be posted to the provider
});

test('test_twitch_live_chat_explained_without_sign_in', async ({ page }) => {
  await open(page, TWITCH_URL, preview(TWITCH_URL, 'twitch', 'live', { live: 'unavailable', replay: 'unavailable', live_reason: 'authentication_required' }), 'light', { width: 1536, height: 960 });
  const note = page.getByRole('note').filter({ hasText: 'Twitch live chat' });
  await expect(note).toHaveText(/Twitch live chat needs a Twitch account, which Lumina never asks for\. The stream plays without it\./);
  await expect(page.getByRole('button', { name: /connect|sign in to twitch/i })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /connect|sign in to twitch/i })).toHaveCount(0);
});

test('test_chat_renders_without_page_errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page, REPLAY_URL, preview(REPLAY_URL, 'youtube', 'completed_live', { live: 'unavailable', replay: 'available' }), 'dark', { width: 1536, height: 960 });
  await expect(page.getByRole('complementary', { name: 'Replay chat from YouTube' }).getByText('Evening everyone', { exact: false })).toBeVisible();
  await page.goto('about:blank');
  await open(page, TWITCH_URL, preview(TWITCH_URL, 'twitch', 'live', { live: 'unavailable', replay: 'unavailable', live_reason: 'authentication_required' }), 'dark', { width: 1536, height: 960 });
  await expect(page.getByRole('note').filter({ hasText: 'Twitch live chat' })).toBeVisible();
  expect(errors).toEqual([]);
});
