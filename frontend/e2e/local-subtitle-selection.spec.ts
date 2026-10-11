import { expect, test, type Locator, type Page } from '@playwright/test';

import { fulfillRangeMedia, mockApi, mockTitles, signIn } from './lumina-mock';

const EPISODE = 'item-ep-2-2';
const TRACKS = [
  { id: 'embedded:2', label: 'English', language: 'eng', origin: 'embedded', format: 'text', forced: false, default: false, hearing_impaired: false, url: `/api/library/${EPISODE}/subtitle-tracks/embedded:2.vtt` },
  { id: 'embedded:3', label: 'Portuguese', language: 'por', origin: 'embedded', format: 'text', forced: false, default: false, hearing_impaired: false, url: `/api/library/${EPISODE}/subtitle-tracks/embedded:3.vtt` },
];

async function openEpisode(page: Page) {
  await mockApi(page, { sidebar_collapsed: false });
  await mockTitles(page);
  await page.route((url) => url.pathname.startsWith(`/api/library/${EPISODE}/subtitle-tracks`), (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('.vtt')) return route.fulfill({ status: 200, contentType: 'text/vtt', body: 'WEBVTT\n\n00:00.000 --> 00:01.400\nSynthetic caption.\n' });
    return route.fulfill({ json: TRACKS });
  });
  await page.route(`**/api/library/${EPISODE}/media`, fulfillRangeMedia);
  await page.goto(`/watch/library/${EPISODE}`);
  await signIn(page);
  const video = page.locator('video');
  await expect(video).toHaveAttribute('src', `/api/library/${EPISODE}/media`);
  await video.evaluate((media: HTMLVideoElement) => media.pause());
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.querySelectorAll('track').length)).toBe(2);
  return video;
}

async function modes(video: Locator) {
  return video.evaluate((media: HTMLVideoElement) => Array.from(media.textTracks, (track) => [track.id, track.mode]));
}

test('local text subtitles settle on exactly the chosen track and Off disables every track', async ({ page }) => {
  const video = await openEpisode(page);
  await expect.poll(() => modes(video)).toEqual([['embedded:2', 'disabled'], ['embedded:3', 'disabled']]);

  await page.getByRole('button', { name: 'Playback settings' }).click();
  const menu = page.getByRole('group', { name: 'Playback settings' });
  await menu.getByRole('radio', { name: /Portuguese/ }).check();
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.querySelector<HTMLTrackElement>('#embedded\\:3')?.readyState)).toBe(2);
  await expect.poll(() => modes(video)).toEqual([['embedded:2', 'disabled'], ['embedded:3', 'showing']]);
  await video.evaluate((media: HTMLVideoElement) => {
    media.textTracks[0].mode = 'showing';
    media.textTracks[1].mode = 'showing';
    media.querySelector('track')?.dispatchEvent(new Event('load'));
  });
  await expect.poll(() => modes(video)).toEqual([['embedded:2', 'disabled'], ['embedded:3', 'showing']]);

  await menu.getByRole('radio', { name: /^Off$/ }).check();
  await video.evaluate((media: HTMLVideoElement) => {
    media.textTracks[0].mode = 'showing';
    media.querySelector('track')?.dispatchEvent(new Event('load'));
  });
  await expect.poll(() => modes(video)).toEqual([['embedded:2', 'disabled'], ['embedded:3', 'disabled']]);
});
