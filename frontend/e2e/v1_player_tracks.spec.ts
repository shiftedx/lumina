import { expect, test, type Page } from '@playwright/test';
import { fulfillRangeMedia, mockApi, mockTitles, pauseAt, signIn } from './lumina-mock';

/** Subtitle menu, subtitle jobs, skip and up next over the 1.5 s synthetic clip. */

const EP = 'item-ep-2-2';
const trackBase = { forced: false, default: false, hearing_impaired: false };
const GENERATED = 't:0f8fad5b-d9cb-469f-a165-70867728950e';

async function mockPlayerExtras(page: Page) {
  let polls = 0;
  await page.route((url) => url.pathname.startsWith(`/api/library/${EP}/subtitle-tracks`) || url.pathname === `/api/library/${EP}/segments` || url.pathname === '/api/enrichment' || url.pathname.startsWith('/api/enrichment/jobs/'), async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/enrichment') return json({ ai_summaries: true, asr: true });
    if (path.endsWith('.vtt')) return route.fulfill({ status: 200, contentType: 'text/vtt', body: 'WEBVTT\n\n00:00.000 --> 00:01.400\nThe ferry is late again.\n' });
    if (path.endsWith('/subtitle-tracks/generate')) return json({ id: 'job-1', library_item_id: EP, kind: 'asr', state: 'queued', created_at: '2026-09-25T00:00:00Z' }, 202);
    if (path === '/api/enrichment/jobs/job-1') { polls += 1; return json({ id: 'job-1', library_item_id: EP, kind: 'asr', state: polls > 1 ? 'succeeded' : 'running', created_at: '2026-09-25T00:00:00Z' }); }
    if (path.endsWith('/subtitle-tracks')) {
      return json([
        { id: 's:0', label: 'English', language: 'eng', origin: 'sidecar', format: 'text', url: `/api/library/${EP}/subtitle-tracks/s:0.vtt`, ...trackBase },
        ...(polls > 1 ? [{ id: GENERATED, label: 'English (generated)', language: 'eng', origin: 'generated', format: 'text', url: `/api/library/${EP}/subtitle-tracks/${GENERATED}.vtt`, ...trackBase }] : []),
      ]);
    }
    if (path.endsWith('/segments')) {
      return json({ item_id: EP, segments: [
        { type: 'intro', start_seconds: 0.1, end_seconds: 0.6, source: 'fingerprint', confidence: 0.9 },
        { type: 'credits', start_seconds: 1.0, end_seconds: 1.5, source: 'heuristic', confidence: 0.7 },
      ] });
    }
    return route.fallback();
  });
}

async function openEpisode(page: Page, rangeMedia = false) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockTitles(page);
  await mockPlayerExtras(page);
  if (rangeMedia) await page.route(`**/api/library/${EP}/media`, fulfillRangeMedia);
  const segments = rangeMedia ? page.waitForResponse(`**/api/library/${EP}/segments`) : null; // the player reads them on a time update, so they must be in before the seek
  await page.goto(`/watch/library/${EP}`);
  await signIn(page);
  await segments;
  const video = page.locator('video');
  await expect(video).toHaveAttribute('src', `/api/library/${EP}/media`);
  await video.evaluate((media: HTMLVideoElement) => media.pause());
  return video;
}

test('subtitle menu: choose a track, generate one, see it arrive', async ({ page }) => {
  const video = await openEpisode(page);
  const toggle = page.getByRole('button', { name: 'Playback settings' });
  await toggle.click();
  const menu = page.getByRole('group', { name: 'Playback settings' });
  await menu.getByRole('radio', { name: /^English\s*Subtitle file/ }).check();
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.textTracks[0]?.mode)).toBe('showing');
  await menu.getByRole('button', { name: 'Generate subtitles' }).click();
  await expect(menu.getByRole('status')).toHaveText('Done. The new subtitles are in the list.', { timeout: 10_000 });
  await expect(menu.getByRole('radio', { name: /English \(generated\)/ })).toBeVisible();
  await toggle.click();
  await expect(menu).toBeHidden();
});

test('Skip intro inside the intro, and the up-next card at credits', async ({ page }) => {
  // Range-capable media lets a paused seek stick, so each checkpoint is reached by seeking rather
  // than racing playback against the 0.1-0.6 s intro window on a loaded host.
  const video = await openEpisode(page, true);
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.readyState)).toBeGreaterThanOrEqual(1);
  await pauseAt(page, 0.3);
  const skip = page.getByRole('button', { name: 'Skip intro' });
  await expect(skip).toBeVisible();
  await skip.click();
  await expect(skip).toBeHidden();
  await pauseAt(page, 1.05);
  const upNext = page.getByRole('region', { name: 'Next episode' });
  await expect(upNext).toContainText('S2 · E3 · Fog Line');
  await expect(upNext.getByRole('button', { name: 'Play now' })).toBeFocused();
  await upNext.getByRole('button', { name: 'Cancel' }).click();
  await expect(upNext).toBeHidden();
});
