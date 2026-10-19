import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { mockGalleryWall, wallTitles } from './gallery-mock';
import { mockApi, navigateApp, signIn } from './lumina-mock';
import { musicTracks, mockMusic } from './music-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';
import { albumSummary, titleDetail } from '../src/test/galleryFixtures';

/** The media-first shell keeps audio playback independent of the page under it. */

async function mockFirstClassMusic(page: Page) {
  const artists = wallTitles('artist', 6);
  await page.route((url) => /^\/api\/titles\/artist-\d{4}$/.test(url.pathname), async (route) => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1) as string;
    const artist = artists.find((entry) => entry.id === id) ?? artists[0];
    const ordinal = Number(id.split('-').at(-1));
    const releases = [0, 1, 2, 3].map((index) => albumSummary(`${id}-release-${index}`, { name: `${['Golden Rooms', 'Night Geometry', 'Second Weather', 'Live Current'][index]} ${ordinal + 1}`, parent_id: id, artist_name: artist.name, year: 2024 - index }));
    return route.fulfill({ json: titleDetail({ ...artist, overview: `${artist.name} builds patient songs from field recordings, warm synthesizers, and close harmony.`, genres: ['Electronic', 'Ambient'] }, { children: releases, music: { release_type: null, edition: null, label: null, catalogue_number: null, country: null, original_year: null, origin: ordinal % 2 ? 'Reykjavík' : 'Detroit, Michigan', active_since: String(2008 + ordinal), active_until: null, styles: [], mood: [], discography: [], candidates: [], identified: true } }) });
  });
  await page.route('**/api/music/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/music/queue') {
      return json(musicTracks.map((entry) => ({
        id: entry.item_id,
        title: entry.name,
        artist: 'Artist A',
        album_artist: 'Artist A',
        album: 'Album One',
        album_id: 'album-1',
        artist_id: 'artist-1',
        number: entry.number,
        disc: entry.disc,
        duration: 90,
        artwork_url: null,
        audio_url: `/api/music/tracks/${entry.item_id}/audio`,
        codec: 'opus',
        sample_rate: 48_000,
        bit_depth: null,
        channels: 2,
        gain_db: null,
        genres: ['Electronic'],
        edited: false,
      })));
    }
    if (/^\/api\/music\/tracks\/track-\d+\/audio$/.test(path)) return route.fulfill({ status: 200, contentType: SYNTHETIC_MEDIA_TYPE, body: Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64') });
    if (/^\/api\/music\/tracks\/track-\d+\/lyrics$/.test(path)) return json({ plain: null, cues: [], source: null, instrumental: true });
    return route.fallback();
  });
}

test('Video, Audio, and Books are direct destinations while the music queue keeps playing', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  await mockGalleryWall(page);
  await mockMusic(page);
  await mockFirstClassMusic(page);
  await page.goto('/audio/title/album-1');
  await signIn(page);

  await expect(page.getByRole('heading', { level: 1, name: 'Album One' })).toBeVisible();
  if (process.env.MUSIC_VISUAL_DIR) await page.screenshot({ path: `${process.env.MUSIC_VISUAL_DIR}/album-page-desktop.png`, fullPage: true });
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  const player = page.getByRole('region', { name: 'Music player', exact: true });
  await expect(player).toBeVisible();
  await expect(player.getByRole('button', { name: /Open listening room for Song [123]/ })).toBeVisible();
  if (process.env.MUSIC_VISUAL_DIR) {
    await player.getByRole('button', { name: /Open listening room for Song [123]/ }).click();
    await expect(page.getByRole('dialog', { name: 'Listening room' })).toBeVisible();
    await page.screenshot({ path: `${process.env.MUSIC_VISUAL_DIR}/listening-room-desktop.png`, fullPage: true });
    await page.getByRole('button', { name: 'Close listening room' }).click();
  }

  await navigateApp(page, 'Video');
  await expect(page).toHaveURL(/\/video$/);
  await expect(player).toBeVisible();

  await navigateApp(page, 'Books');
  await expect(page).toHaveURL(/\/books$/);
  await expect(page.getByText('A home for every story')).toBeVisible();
  await expect(player).toBeVisible();

  await navigateApp(page, 'Audio');
  await expect(page).toHaveURL(/\/audio$/);
  await expect(player).toBeVisible();
  await expect(player.getByRole('button', { name: /Open listening room for Song [123]/ })).toBeVisible();

  const soundstage = page.getByRole('region', { name: 'A room full of records' });
  await expect(soundstage.getByRole('button', { name: /A Title 0 by Artist 0/ })).toBeVisible();
  await soundstage.getByRole('button', { name: /A Title 0 by Artist 0/ }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-0000$/);
  await page.goto('/audio/title/artist-0000');
  await expect(page.getByRole('heading', { level: 1, name: 'A Title 0' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: /^Albums/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play all', exact: true })).toBeEnabled();
  // A full navigation restores the queue paused; only SPA navigation keeps audio playing.
  await expect(player).toBeHidden();
  await expect(page.getByRole('region', { name: 'Mini music player' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 'artist page overflow').toBeLessThanOrEqual(0);
  const artistAccessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  expect(artistAccessibility.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => violation.id)).toEqual([]);
  if (process.env.MUSIC_VISUAL_DIR) await page.screenshot({ path: `${process.env.MUSIC_VISUAL_DIR}/artist-page-desktop.png`, fullPage: true });
});

for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 390, height: 844 }, { name: 'tv', width: 1920, height: 1080 }]) {
  test(`the Audio soundstage is accessible and contained at ${viewport.name} size`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    await mockApi(page, { sidebar_collapsed: false });
    await mockGalleryWall(page, { artists: 12, albums: 12 });
    await mockMusic(page);
    await mockFirstClassMusic(page);
    await page.goto('/audio');
    await signIn(page);
    const soundstage = page.getByRole('region', { name: 'A room full of records' });
    await expect(soundstage).toBeVisible();
    await expect(soundstage.getByRole('button', { name: /A Title 0 by Artist 0/ })).toBeVisible();
    await expect(soundstage.getByRole('button', { name: /C Title 1 by Artist 1/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 'page overflow').toBeLessThanOrEqual(0);
    if (viewport.width === 390) {
      const nav = page.getByRole('navigation', { name: 'Audio' });
      expect(await nav.evaluate((element) => element.scrollWidth >= element.clientWidth)).toBe(true);
    }
    const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
    expect(axe.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => violation.id)).toEqual([]);
    expect(errors).toEqual([]);
    if (process.env.MUSIC_VISUAL_DIR) await page.screenshot({ path: `${process.env.MUSIC_VISUAL_DIR}/audio-soundstage-${viewport.name}.png`, fullPage: true });
  });
}
