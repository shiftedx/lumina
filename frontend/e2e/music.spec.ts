import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { albumDetail, albumSummary, albumTrack, artistSummary, titleDetail, userData } from '../src/test/galleryFixtures';
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
  await expect(page.getByText('Album · 3 tracks · 5 min')).toBeVisible();
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

for (const [scheme, viewport] of [['dark', { width: 1920, height: 1080 }], ['light', DESKTOP], ['dark', { width: 390, height: 844 }]] as const) {
  test(`album cover palette and wide tracks keep large edition catalogs closed: ${scheme} ${viewport.width}px`, async ({ page }) => {
    const album = albumDetail(albumSummary('album-1', { name: 'Signal Garden', poster: { url: '/api/artwork/remote/record-cover', widths: [], dominant: '#367c91', accent: '#59879b' } }), Array.from({ length: 13 }, (_, index) => albumTrack(index + 1)));
    album.music = { release_type: 'album', edition: null, label: 'Signal Records', catalogue_number: null, country: 'US', original_year: 2019, origin: null, active_since: null, active_until: null, styles: [], mood: [], discography: [], candidates: [], identified: true, release_group_id: 'group-1' };
    let editionRequests = 0;
    page.on('request', (request) => { if (new URL(request.url()).pathname.endsWith('/album-1/editions')) editionRequests += 1; });
    await open(page, '/audio/title/album-1', viewport, scheme, album);
    const tracks = page.getByRole('region', { name: 'Signal Garden track list' });
    await expect(tracks.getByRole('button')).toHaveCount(13);
    const root = (await page.locator('.g-album-page').boundingBox())!;
    expect(Math.abs(root.width - viewport.width)).toBeLessThanOrEqual(1);
    if (viewport.width > 1000) expect((await tracks.boundingBox())!.width).toBeGreaterThan(viewport.width / 2);
    const editions = page.locator('details.music-editions');
    await expect(editions).not.toHaveAttribute('open', '');
    expect(editionRequests).toBe(0);
    await expect(page.getByRole('heading', { name: 'On film', exact: true })).toHaveCount(0);
    await page.route('**/api/music/titles/album-1/editions?*', (route) => route.fulfill({ json: {
      owned: [], items: Array.from({ length: 100 }, (_, index) => ({ id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`, name: `Pressing ${index + 1}`, date: '2025-01-01', country: 'US', edition: `Variant ${index + 1}`, label: 'Signal Records' })),
      next_offset: null, refreshing: false, error: null,
    } }));
    const closedHeight = await page.locator('.g-album-page').evaluate((element) => element.getBoundingClientRect().height);
    await page.screenshot({ path: `../output/music-layout/album-palette-${scheme}-${viewport.width}.png`, fullPage: true });
    await editions.locator('summary').click();
    await expect(editions.getByText('Pressing 100', { exact: true })).toBeVisible();
    expect(editionRequests).toBe(1);
    const expandedHeight = await page.locator('.g-album-page').evaluate((element) => element.getBoundingClientRect().height);
    expect(expandedHeight).toBeGreaterThan(closedHeight + 1000);
    await editions.locator('summary').click();
    await expect(editions.getByText('Pressing 1', { exact: true })).toBeHidden();
    expect(await page.locator('.g-album-page').evaluate((element) => element.getBoundingClientRect().height)).toBeLessThanOrEqual(closedHeight + 1);
    await editions.locator('summary').click();
    await expect(editions.getByText('Pressing 1', { exact: true })).toBeVisible();
    expect(editionRequests).toBe(1);
    await editions.locator('summary').click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await noSeriousAxe(page, `album palette ${scheme} ${viewport.width}`);
  });
}

test('source file quality stays visible from artist to album on desktop and phones', async ({ page }) => {
  const quality = { track_count: 3, codecs: ['aac', 'flac'], bit_depths: [24], sample_rates: [44100, 96000], bitrate_kbps: 1200, formats_complete: true };
  const album = albumDetail(albumSummary('album-1', { child_count: 3, audio_quality: quality }), [
    albumTrack(1, { audio_quality: { codec: 'flac', bitrate_kbps: 1800, bit_depth: 24, sample_rate: 96000 } }),
    albumTrack(2, { audio_quality: { codec: 'flac', bitrate_kbps: 1600, bit_depth: 24, sample_rate: 96000 } }),
    albumTrack(3, { audio_quality: { codec: 'aac', bitrate_kbps: 256, bit_depth: null, sample_rate: 44100 } }),
  ]);
  await mockApi(page, { sidebar_collapsed: true });
  await mockGalleryWall(page);
  await mockMusic(page);
  await page.route('**/api/titles/album-1', (route) => route.fulfill({ json: album }));
  await page.route('**/api/titles/artist-1', (route) => route.fulfill({ json: titleDetail(artistSummary(), { overview: 'A long artist biography that remains available without pushing the albums below the portrait. '.repeat(8), children: [album,
    albumSummary('album-2', { name: 'An Album With a Particularly Long Name That Still Needs Room Beneath Its Cover', audio_quality: { ...quality, codecs: ['flac'], bit_depths: [], sample_rates: [44100], bitrate_kbps: null, formats_complete: false } }),
    albumSummary('album-3', { name: 'Third release', audio_quality: { ...quality, codecs: ['alac'], bit_depths: [16], sample_rates: [44100], bitrate_kbps: 720 } }),
    albumSummary('album-4', { name: 'Unreported file facts', audio_quality: { ...quality, codecs: [], bit_depths: [], sample_rates: [], bitrate_kbps: null, formats_complete: false } }),
  ] }) }));
  await page.goto('/audio');
  await signIn(page);
  await expect(page.getByRole('button', { name: 'Artists', exact: true })).toHaveAttribute('aria-pressed', 'true');
  for (const [width, scheme] of [[1440, 'dark'], [1024, 'light'], [768, 'dark'], [390, 'light'], [320, 'dark']] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await page.goto('/audio/title/artist-1');
    const card = page.getByRole('button', { name: /Album One by Artist A.*Mixed formats.*1,200 kbps average/ });
    await expect(card).toBeVisible();
    const about = page.getByText('About the artist', { exact: true });
    await about.click();
    await expect(about.locator('..')).toHaveAttribute('open', '');
    await about.click();
    const partial = page.getByLabel(/File details for An Album With a Particularly Long Name/);
    await partial.click();
    await expect(partial.locator('..').getByText('Bitrate unavailable', { exact: true })).toBeVisible();
    await partial.click();
    const artistFacts = card.locator('..').locator('.g-album-file-quality');
    await expect(artistFacts.locator('summary').getByText('1,200 kbps', { exact: true })).toBeVisible();
    await artistFacts.locator('summary').click();
    await expect(artistFacts.getByText('Average bitrate')).toBeVisible();
    await expect(page).toHaveURL(/artist-1/);
    await artistFacts.locator('summary').click();
    await expect(card.getByText('Album One', { exact: true })).toBeVisible();
    const portraitArt = page.locator('.soundstage-portrait > .g-art');
    const artistHeading = page.getByRole('heading', { level: 1, name: 'Artist A' });
    const portraitBox = (await portraitArt.boundingBox())!;
    const headingBox = (await artistHeading.boundingBox())!;
    expect(headingBox.y).toBeGreaterThanOrEqual(portraitBox.y + portraitBox.height);
    expect(Math.abs(headingBox.x - portraitBox.x)).toBeLessThanOrEqual(1);
    if (width >= 1024) {
      expect((await card.locator('.g-art-square').boundingBox())!.width).toBeGreaterThan(240);
      const year = (await card.locator('.g-caption-year').boundingBox())!;
      const title = (await card.locator('.g-caption-title').boundingBox())!;
      expect(Math.abs(year.y - title.y)).toBeLessThan(8);
      const format = (await artistFacts.locator('.g-quality-format').boundingBox())!;
      const rate = (await artistFacts.locator('.g-quality-rate').boundingBox())!;
      expect(rate.y >= format.y + format.height - 1 || rate.x >= format.x + format.width - 1).toBe(true);
      expect(Math.abs(rate.x + rate.width - ((await card.boundingBox())!.x + (await card.boundingBox())!.width))).toBeLessThanOrEqual(1);
    }

    expect(await card.locator('.g-caption').evaluate((caption) => {
      const box = caption.getBoundingClientRect();
      return [...caption.children].every((child) => child.getBoundingClientRect().bottom <= box.bottom + 1);
    })).toBe(true);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: `../output/music-quality/artist-${width}-${scheme}.png`, fullPage: true });
    await card.click();
    await expect(page.getByRole('heading', { name: 'Album One', exact: true })).toBeVisible();
    const albumCover = (await page.locator('.g-album-cover > .g-art').boundingBox())!;
    const albumHeading = (await page.getByRole('heading', { level: 1, name: 'Album One' }).boundingBox())!;
    expect(albumHeading.y).toBeGreaterThanOrEqual(albumCover.y + albumCover.height);
    expect(Math.abs(albumHeading.x - albumCover.x)).toBeLessThanOrEqual(1);
    const source = page.locator('.g-album-file-quality');
    await expect(source.getByText(/Mixed formats · AAC \+ FLAC/).first()).toBeVisible();
    await source.locator('summary').click();
    await expect(source.getByText('24-bit (where reported)', { exact: true })).toBeVisible();
    await expect(source.getByText('44.1–96 kHz', { exact: true })).toBeVisible();
    const row = page.getByRole('button', { name: /Play 1\. Song 1.*FLAC.*24-bit.*96 kHz.*1,800 kbps/ });
    await expect(row.getByText('1,800 kbps')).toBeVisible();
    await expect(page.getByRole('button', { name: /Play 3\. Song 3.*AAC.*44.1 kHz.*256 kbps/ })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: `../output/music-quality/album-${width}-${scheme}.png`, fullPage: true });
    await noSeriousAxe(page, `file quality ${width} ${scheme}`);
  }
});
