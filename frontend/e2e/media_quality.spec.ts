import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

import { docFixture, fieldState } from '../src/features/titles/editor/editorFixtures';
import { mockGalleryWall, mockLibrarySections } from './gallery-mock';
import { CHANNEL_ID, mockChannelPage, mockLibraryChannels, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { mockLibraryItems } from './library-mock';
import { item, mockApi, mockTitles, signIn } from './lumina-mock';
import { mockMetadataEditor } from './metadata-editor-mock';

const CAPTURE_OUT = process.env.MEDIA_QUALITY_OUT;
const fullFacts = '90p · VP9 / OPUS · Original';

async function openWatch(page: Page, width: number) {
  await page.setViewportSize({ width, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, {});
  await page.route(new RegExp(`/api/library/${item.id}/playback-options(?:\\?|$)`), (route) => route.fulfill({ json: {
    mode: 'direct', reason: null,
    facts: { container: 'matroska,webm', video_codec: 'vp9', audio_codec: 'opus', width: 160, height: 90, duration: 1 },
    audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 1,
  } }));
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('.quality-control-facts')).toBeAttached();
}

test('phone playback settings remain inside the visible player and every section is reachable', async ({ page }) => {
  await openWatch(page, 390);
  await page.getByRole('button', { name: 'Playback settings' }).click();
  const frame = page.locator('.player-frame');
  const panel = page.getByRole('group', { name: 'Playback settings' });
  await expect(panel).toBeVisible();
  const [frameBox, panelBox] = await Promise.all([frame.boundingBox(), panel.boundingBox()]);
  expect(frameBox).not.toBeNull();
  expect(panelBox).not.toBeNull();
  expect(panelBox!.x).toBeGreaterThanOrEqual(8);
  expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(382);
  expect(panelBox!.y).toBeGreaterThanOrEqual(frameBox!.y);
  expect(panelBox!.y + panelBox!.height).toBeLessThanOrEqual(frameBox!.y + frameBox!.height);
  await page.getByRole('checkbox', { name: 'Skip intros automatically' }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('checkbox', { name: 'Skip intros automatically' })).toBeVisible();
});

test('phone playback facts keep full technical text without colliding with time or speed', async ({ page }) => {
  await openWatch(page, 390);
  const facts = page.locator('.quality-control-facts');
  const visualFacts = facts.locator('.quality-control-copy');
  await expect(facts.locator('.sr-only')).toHaveText(`Playback details: ${fullFacts}`);
  await expect(visualFacts).toHaveText(fullFacts);
  await expect(visualFacts).toHaveAttribute('aria-hidden', 'true');
  await expect(visualFacts).toHaveAttribute('data-compact-label', '90p');
  expect(await visualFacts.evaluate((element) => ({
    content: getComputedStyle(element, '::after').content,
    fontSize: getComputedStyle(element, '::after').fontSize,
  }))).toEqual({ content: '"90p"', fontSize: '11px' });
  expect(await facts.ariaSnapshot()).toBe(`- text: "Playback details: ${fullFacts}"`);
  const [factsBox, timeBox, rateBox] = await Promise.all([
    facts.boundingBox(), page.locator('.player-timecode').boundingBox(), page.getByLabel('Playback speed').boundingBox(),
  ]);
  expect(factsBox).not.toBeNull();
  expect(timeBox).not.toBeNull();
  expect(rateBox).not.toBeNull();
  expect(factsBox!.y + factsBox!.height).toBeLessThanOrEqual(timeBox!.y);
  expect(factsBox!.y + factsBox!.height).toBeLessThanOrEqual(rateBox!.y);
  expect(factsBox!.height).toBeLessThanOrEqual(44);
  await page.getByRole('button', { name: 'Playback settings' }).click();
  await expect(page.getByRole('group', { name: 'Playback settings' }).getByText(`Playing: ${fullFacts}`)).toBeVisible();
});

test('desktop Caption style is a single-line control', async ({ page }) => {
  await openWatch(page, 1440);
  await page.getByRole('button', { name: 'Playback settings' }).click();
  const button = page.getByRole('button', { name: 'Caption style…' });
  await expect(button).toBeVisible();
  expect(await button.evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return new Set([...range.getClientRects()].map((rect) => Math.round(rect.y))).size;
  })).toBe(1);
});

test('desktop volume expands for pointer and keyboard access without animating layout', async ({ page }) => {
  await openWatch(page, 1440);
  const control = page.locator('.player-volume-control');
  const label = control.locator('label');
  const slider = page.getByRole('slider', { name: 'Volume' });
  await control.hover();
  await expect(label).toHaveCSS('width', '72px');
  await expect(label).toHaveCSS('transition-duration', '0s');
  await page.mouse.move(0, 0);
  await slider.focus();
  await expect(label).toHaveCSS('width', '72px');
});

if (CAPTURE_OUT) {
  for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 390, height: 844 }] as const) {
    for (const scheme of ['light', 'dark'] as const) {
      test(`capture all metadata editor tabs at ${viewport.name} ${scheme}`, async ({ page }) => {
        mkdirSync(CAPTURE_OUT, { recursive: true });
        await page.setViewportSize(viewport);
        await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
        await mockApi(page, { sidebar_collapsed: false });
        await mockTitles(page);
        const doc = docFixture('series', {
          title_id: 'series-1', name: 'Harbor Lights: The Long Household Archive Edition',
          fields: {
            ...docFixture('series').fields,
            people: fieldState([
              { person_id: 'person-1', name: 'Alexandria Whitcombe-Harrington', role: 'Keeper of the North Lighthouse', type: 'Actor' },
              { person_id: 'person-2', name: 'Sam Rivers', role: 'Archivist', type: 'Director' },
            ]),
          },
          images: [{ type: 'Primary', index: 0, url: '/api/art/editor-primary', tag: 'abc', origin: 'tmdb', source: 'tmdb', locked: false, width: 1000, height: 1500 }],
        });
        await mockMetadataEditor(page, { doc });
        await page.route('**/api/titles/series-1/metadata/episodes?*', (route) => route.fulfill({ json: {
          seasons: [{ id: 'season-1', name: 'Season 1', index_number: 1 }],
          episodes: [{ title_id: 'episode-1', name: 'A Deliberately Long Episode Name for the Phone Layout', index_number: 1, season_number: 1, overview: 'A long overview used to exercise the compact editor.', air_date: '2026-10-11', runtime: 52, locked: false, locked_fields: [] }],
        } }));
        await page.goto('/title/series-1/edit');
        await signIn(page);
        await expect(page.getByRole('heading', { level: 1, name: doc.name })).toBeVisible();
        for (const tab of ['Details', 'People', 'Artwork', 'IDs', 'Episodes', 'History']) {
          await page.getByRole('radio', { name: tab }).check({ force: true });
          await expect(page.getByRole('radio', { name: tab })).toBeChecked();
          await page.evaluate(() => window.scrollTo(0, 0));
          await page.screenshot({ fullPage: true, path: join(CAPTURE_OUT, `metadata-${tab.toLowerCase()}-${viewport.name}-${scheme}.png`) });
        }
      });
    }
  }

  for (const profile of [{ name: 'desktop-light', width: 1440, height: 900, scheme: 'light' }, { name: 'phone-dark', width: 390, height: 844, scheme: 'dark' }] as const) {
    test(`capture the uncatalogued Library lenses at ${profile.name}`, async ({ page }) => {
      mkdirSync(CAPTURE_OUT, { recursive: true });
      await page.setViewportSize(profile);
      await page.emulateMedia({ colorScheme: profile.scheme, reducedMotion: 'reduce' });
      await mockApi(page, { sidebar_collapsed: false });
      await mockGalleryWall(page);
      await mockLibrarySections(page);
      await mockLibraryItems(page);
      for (const [index, [name, path]] of ([['shows', '/library/shows'], ['anime', '/library/anime'], ['music-albums', '/library/music'], ['music-artists', '/library/music?view=artists'], ['saved-audio', '/library/music?view=saved'], ['youtube', '/library/youtube'], ['recordings', '/library/recordings']] as const).entries()) {
        await page.goto(path);
        if (index === 0) await signIn(page);
        await expect(page.locator('.surface.gallery h1').first()).toBeVisible();
        await page.screenshot({ path: join(CAPTURE_OUT, `library-${name}-${profile.name}.png`) });
      }
    });

    test(`capture every uncatalogued channel tab at ${profile.name}`, async ({ page }) => {
      mkdirSync(CAPTURE_OUT, { recursive: true });
      await page.setViewportSize(profile);
      await page.emulateMedia({ colorScheme: profile.scheme, reducedMotion: 'reduce' });
      await mockApi(page, { sidebar_collapsed: false });
      await mockRemoteArtwork(page);
      await mockLive(page);
      await mockChannelPage(page);
      await mockLibraryChannels(page, 12);
      await mockLibraryItems(page);
      for (const [index, tab] of (['live', 'shorts', 'playlists', 'library'] as const).entries()) {
        await page.goto(`/channel/youtube/${CHANNEL_ID}?tab=${tab}`);
        if (index === 0) await signIn(page);
        await expect(page.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeVisible();
        await expect(page.getByRole('tab', { selected: true })).toBeVisible();
        await expect(page.locator('.g-channel-wall')).toBeVisible();
        await page.screenshot({ path: join(CAPTURE_OUT, `channel-${tab}-${profile.name}.png`) });
      }
    });
  }
}
