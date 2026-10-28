import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { albumDetail, albumSummary, albumTrack, userData } from '../src/test/galleryFixtures';
import type { TitleDetail } from '../src/types';

import { mockGalleryWall } from './gallery-mock';
import { mockApi, navigateApp, signIn } from './lumina-mock';
import { mockMusic } from './music-mock';

/**
 * The album and artist pages and the persistent music player over the mocked API.
 * Legacy /title/{id} and /watch/library/{id} bookmarks resolve to canonical Audio pages.
 */

const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 320, height: 844 };
type Viewport = typeof DESKTOP;

async function open(page: Page, path: string, viewport: Viewport = DESKTOP, scheme: 'light' | 'dark' = 'light', album?: TitleDetail) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: true });
  await mockGalleryWall(page);
  const calls = await mockMusic(page);
  if (album) {
    await page.route('**/api/titles/album-1', (route) => route.fulfill({ json: album }));
    await page.route('**/api/artwork/remote/record-cover', (route) => route.fulfill({ contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600"><rect width="600" height="600" fill="${album.poster?.dominant}"/><circle cx="300" cy="300" r="200" fill="none" stroke="white" stroke-width="2"/><text x="300" y="300" text-anchor="middle" fill="white" font-size="28">THE EVENING PLAYERS</text></svg>` }));
  }
  await page.goto(path);
  await signIn(page);
  return calls;
}

async function noSeriousAxe(page: Page, where: string) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).exclude('.player-frame video').analyze();
  expect(results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => `${where}: ${violation.id}`)).toEqual([]);
}

test('album playback stays on its Audio page and Next updates the player and Media Session', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page, '/title/album-1');
  await expect(page.getByRole('heading', { level: 1, name: 'Album One' })).toBeVisible();
  await expect(page.getByText('Album · 2019 · 3 tracks · 5 min')).toBeVisible();
  const cover = await page.locator('.g-album-cover .g-art-square').boundingBox();
  expect(Math.round(cover!.width)).toBe(Math.round(cover!.height));
  await page.getByRole('button', { name: 'Play 1. Song 1, 1:30' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  const player = page.getByRole('region', { name: 'Music player', exact: true });
  await expect(player).toBeVisible();
  await expect(player.getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => navigator.mediaSession.metadata?.title)).toBe('Song 1');
  await player.getByRole('button', { name: 'Open listening room for Song 1' }).click();
  const room = page.getByRole('dialog', { name: 'Listening room' });
  await expect(room.getByRole('heading', { level: 3, name: 'Song 1' })).toBeVisible();
  await expect(room.getByRole('button', { name: 'Next track' })).toBeEnabled();
  await room.getByRole('button', { name: 'Next track' }).click();
  await expect(room.getByRole('heading', { level: 3, name: 'Song 2' })).toBeVisible();
  await expect(player.getByRole('button', { name: 'Open listening room for Song 2' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => navigator.mediaSession.metadata?.title)).toBe('Song 2');
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  expect(errors).toEqual([]);
});

for (const scheme of ['light', 'dark'] as const) {
for (const viewport of [DESKTOP, PHONE]) {
  test(`paused music collapses, expands and closes in ${scheme} at ${viewport.width}px`, async ({ page }) => {
    await open(page, '/audio/title/album-1', viewport, scheme);
    const playTrack = page.getByRole('button', { name: 'Play 1. Song 1, 1:30' });
    const player = page.getByRole('region', { name: 'Music player', exact: true });
    const mini = page.getByRole('region', { name: 'Mini music player', exact: true });
    await playTrack.click();
    await expect(player.getByRole('button', { name: 'Pause music' })).toBeVisible();
    await player.getByRole('button', { name: 'Pause music' }).click();
    await expect(player).toBeHidden();
    await expect(mini).toBeVisible();
    await expect(mini.getByRole('button', { name: 'Play music' })).toBeFocused();
    await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue('--music-dock-height'))).toBe('');
    const bounds = await mini.boundingBox();
    expect(bounds!.x).toBeGreaterThan(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width);
    expect(bounds!.y + bounds!.height).toBeLessThan(viewport.height - (viewport.width < 680 ? 56 : 0));
    await noSeriousAxe(page, 'paused mini player');
    await page.screenshot({ path: `../output/music-mini-${scheme}-${viewport.width}.png` });

    await mini.getByRole('button', { name: /Expand music player/ }).click();
    await expect(player.getByRole('button', { name: 'Play music' })).toBeFocused();
    await expect(mini).toBeHidden();
    await player.getByRole('button', { name: 'Collapse music player' }).click();
    await expect(mini.getByRole('button', { name: 'Play music' })).toBeFocused();
    await mini.getByRole('button', { name: 'Play music' }).click();
    await expect(player.getByRole('button', { name: 'Pause music' })).toBeVisible();
    await expect(player.getByRole('button', { name: 'Pause music' })).toBeFocused();

    await player.getByRole('button', { name: 'Collapse music player' }).click();
    await expect(mini.getByRole('button', { name: 'Pause music' })).toBeVisible();
    await mini.getByRole('button', { name: 'Pause music' }).click();
    await mini.getByRole('button', { name: 'Dismiss music player' }).click();
    await expect(player).toBeHidden();
    await expect(mini).toBeHidden();
    await expect(page.getByRole('main')).toBeFocused();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Album One', exact: true })).toBeVisible();
    await expect(player).toBeHidden();
    await expect(mini).toBeHidden();
    await navigateApp(page, 'Video');
    await expect(mini).toBeHidden();
    await expect(player).toBeHidden();
    await page.goBack();
    await expect(page.getByRole('heading', { name: 'Album One', exact: true })).toBeVisible();
    await expect(mini).toBeHidden();
    await playTrack.click();
    await expect(player.getByRole('button', { name: 'Pause music' })).toBeVisible();
  });
}
}

test('artist → album, and Shuffle plays from a shuffled order', async ({ page }) => {
  await open(page, '/title/artist-1');
  await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
  await expect(page.getByText('Artist · 1 album · 3 tracks')).toBeVisible();
  await page.getByRole('button', { name: 'Album One by Artist A, 2019, 3 tracks' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Album One' })).toBeVisible();
  await page.getByRole('button', { name: 'Shuffle' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  await expect(page.getByRole('region', { name: 'Music player', exact: true }).getByRole('button', { name: /Open listening room for Song [123]/ })).toBeVisible();
});

test('an album opened on its artist names the artist on Back, and goes there even after a track played', async ({ page }) => {
  await open(page, '/title/artist-1');
  await page.getByRole('button', { name: 'Album One by Artist A, 2019, 3 tracks' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  await page.getByRole('button', { name: 'Play 1. Song 1, 1:30' }).click();
  const player = page.getByRole('region', { name: 'Music player', exact: true });
  await expect(player.getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
  await page.getByRole('button', { name: 'Back to Artist A' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/artist-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
  await expect(player.getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
});

test('keyboard only: the tracklist is one column and Left reaches Play', async ({ page }) => {
  await open(page, '/title/album-1');
  const first = page.getByRole('button', { name: 'Play 1. Song 1, 1:30' });
  await first.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('button', { name: 'Play 2. Song 2, 1:30' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  await expect(page.getByRole('region', { name: 'Music player', exact: true }).getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
});

test('keyboard focus keeps a phone track clear of the persistent navigation', async ({ page }) => {
  await open(page, '/audio/title/album-1', { width: 390, height: 844 });
  const firstTrack = page.getByRole('button', { name: 'Play 1. Song 1, 1:30' });
  await firstTrack.focus();
  await expect(firstTrack).toBeFocused();
  const bounds = await page.evaluate(() => {
    const track = document.querySelector('.g-track')?.getBoundingClientRect();
    const navigation = document.querySelector('[aria-label="Mobile primary navigation"]')?.getBoundingClientRect();
    if (!track || !navigation) throw new Error('Expected track and mobile navigation');
    return { track: { bottom: track.bottom }, navigation: { top: navigation.top } };
  });
  expect(bounds.track.bottom).toBeLessThanOrEqual(bounds.navigation.top - 8);
});

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, PHONE]) {
    test(`music pages and the listening room in ${scheme} at ${viewport.width}px: no serious axe findings or sideways scroll`, async ({ page }, testInfo) => {
      await open(page, '/title/album-1', viewport, scheme);
      await expect(page.getByRole('button', { name: 'Play 1. Song 1, 1:30' })).toBeVisible();
      const sideways = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      await noSeriousAxe(page, `album ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`album-${scheme}-${viewport.width}.png`), fullPage: true });
      await page.goto('/title/artist-1');
      await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
      await noSeriousAxe(page, `artist ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`artist-${scheme}-${viewport.width}.png`), fullPage: true });
      await page.goto('/watch/library/track-1');
      await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
      const player = page.getByRole('region', { name: 'Music player', exact: true });
      await expect(player.getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
      await player.getByRole('button', { name: 'Open listening room for Song 1' }).click();
      await expect(page.getByRole('dialog', { name: 'Listening room' })).toBeVisible();
      await noSeriousAxe(page, `listening room ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`listening-room-${scheme}-${viewport.width}.png`), fullPage: true });
    });
  }
}


for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, { width: 1920, height: 1080 }, PHONE]) {
    test(`album sleeve layout with a long title and listening progress: ${scheme} ${viewport.width}px`, async ({ page }, testInfo) => {
      const name = 'The Long Way Home — Twentieth Anniversary Expanded Edition';
      const album = albumDetail(albumSummary('album-1', { name, poster: { url: '/api/artwork/remote/record-cover', widths: [], dominant: scheme === 'dark' ? '#21485a' : '#9a394a', accent: '#59879b' } }), [
        albumTrack(1, { artist: 'The Evening Players', user_data: userData({ position_seconds: 30 }) }),
        albumTrack(2),
      ]);
      await open(page, '/audio/title/album-1', viewport, scheme, album);
      const heading = page.getByRole('heading', { level: 1, name });
      await expect(heading).toBeVisible();
      const fontSize = await heading.evaluate((node) => parseFloat(getComputedStyle(node).fontSize));
      expect(fontSize).toBeLessThanOrEqual(viewport.width === 320 ? 28 : 48);
      const row = page.getByRole('button', { name: /^Play 1\. Song 1/ });
      const title = (await row.locator('.g-track-title').boundingBox())!;
      const artist = (await row.locator('.g-track-name .g-label').boundingBox())!;
      const progress = (await row.locator('.g-track-progress').boundingBox())!;
      const number = (await row.locator('.g-track-number').boundingBox())!;
      expect(Math.abs(progress.x - title.x)).toBeLessThan(1);
      expect(Math.abs(progress.x - artist.x)).toBeLessThan(1);
      expect(number.width).toBeLessThanOrEqual(24);
      await row.focus();
      await page.keyboard.press('ArrowDown');
      const second = page.getByRole('button', { name: /^Play 2\. Song 2/ });
      await expect(second).toBeFocused();
      await expect(second.locator('.g-track-glyph')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (viewport.width === 320) {
        const play = (await page.getByRole('button', { name: 'Resume · 1. Song 1' }).boundingBox())!;
        const body = (await page.locator('.g-album-body').boundingBox())!;
        expect(Math.abs(play.width - body.width)).toBeLessThan(1);
      }
      await noSeriousAxe(page, `album sleeve ${scheme} ${viewport.width}`);
      await heading.focus();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: testInfo.outputPath(`album-sleeve-${scheme}-${viewport.width}.png`), fullPage: true });
    });
  }
}


for (const viewport of [DESKTOP, PHONE]) {
  test(`listening room adapts to lyrics availability and uses branded choices at ${viewport.width}px`, async ({ page }, testInfo) => {
    await open(page, '/audio/title/album-1', viewport);
    let retry = false;
    await page.route('**/api/music/tracks/*/lyrics*', (route) => {
      retry ||= new URL(route.request().url()).searchParams.get('retry') === 'true';
      return route.fulfill({ json: retry
        ? { plain: 'Opening line\nClosing line', cues: [{ start: 0, text: 'Opening line' }, { start: 10, text: 'Closing line' }], instrumental: false, source: 'LRCLIB', refreshing: false }
        : { error: 'Music provider is temporarily unavailable', plain: null, cues: [], instrumental: false, refreshing: false } });
    });
    await page.getByRole('button', { name: 'Play 1. Song 1, 1:30' }).click();
    await page.getByRole('region', { name: 'Music player', exact: true }).getByRole('button', { name: 'Open listening room for Song 1' }).click();
    const room = page.getByRole('dialog', { name: 'Listening room' });
    await expect(room.getByText('Music provider is temporarily unavailable')).toBeVisible();
    await expect(room).toHaveClass(/is-compact/);
    const compact = (await room.boundingBox())!;
    await expect(room.locator('select')).toHaveCount(0);
    await room.getByRole('button', { name: 'Crossfade', exact: true }).click();
    await page.getByRole('menuitemradio', { name: '3 seconds' }).click();
    await expect(room.getByRole('button', { name: 'Crossfade', exact: true })).toHaveText('3 seconds');
    await room.getByRole('button', { name: 'Sleep timer', exact: true }).click();
    await page.getByRole('menuitemradio', { name: '30 minutes' }).click();
    await expect(room.getByRole('button', { name: 'Sleep timer', exact: true })).toHaveText('30 min left');
    await noSeriousAxe(page, `compact room ${viewport.width}`);
    await page.screenshot({ path: testInfo.outputPath(`room-no-lyrics-${viewport.width}.png`) });
    await room.getByRole('button', { name: 'Retry lyrics' }).click();
    await expect(room.getByRole('button', { name: 'Opening line' })).toBeVisible();
    await expect(room).toHaveClass(/has-lyrics/);
    if (viewport.width > 680) expect((await room.boundingBox())!.width).toBeGreaterThan(compact.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await noSeriousAxe(page, `room with lyrics ${viewport.width}`);
    await page.screenshot({ path: testInfo.outputPath(`room-with-lyrics-${viewport.width}.png`) });
    await room.getByRole('button', { name: 'Close listening room' }).click();
    await expect(room).not.toBeVisible();
  });
}
