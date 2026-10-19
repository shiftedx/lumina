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

async function openWatch(page: Page, width: number, denseMenu = false) {
  const watchItemId = denseMenu ? 'item-movie-4k' : item.id;
  await page.setViewportSize({ width, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, {});
  if (denseMenu) await mockTitles(page);
  await page.route(new RegExp(`/api/library/${watchItemId}/playback-options(?:\\?|$)`), (route) => route.fulfill({ json: {
    mode: 'direct', reason: null,
    facts: { container: 'matroska,webm', video_codec: 'vp9', audio_codec: 'opus', width: 160, height: 90, duration: 1 },
    audio_tracks: denseMenu ? [
      { index: 0, language: 'eng', label: 'English 5.1 — Original theatrical mix', codec: 'opus', channels: 6, default: true },
      { index: 1, language: 'spa', label: 'Español latinoamericano — Audiodescripción', codec: 'aac', channels: 2, default: false },
    ] : [],
    quality_heights: denseMenu ? [2160, 1080, 720] : [], loudness_gain_db: null, free_video_slots: 1,
  } }));
  if (denseMenu) await page.route((url) => url.pathname === `/api/library/${watchItemId}/subtitle-tracks` || url.pathname === '/api/enrichment', (route) => {
    if (new URL(route.request().url()).pathname === '/api/enrichment') return route.fulfill({ json: { ai_summaries: false, asr: false, disabled_features: ['sync'] } });
    return route.fulfill({ json: [
      { id: 'embedded:0', label: 'English CC — Original theatrical dialogue', language: 'eng', origin: 'embedded', format: 'text', forced: false, default: true, hearing_impaired: true },
      { id: 'embedded:1', label: 'Español (Latinoamérica) — Comentarios descriptivos', language: 'spa', origin: 'embedded', format: 'text', forced: false, default: false, hearing_impaired: false },
      { id: 'embedded:2', label: 'Français (Canada) — Sourds et malentendants', language: 'fra', origin: 'embedded', format: 'text', forced: false, default: false, hearing_impaired: true },
    ] });
  });
  await page.goto(`/watch/library/${watchItemId}`);
  await signIn(page);
  await expect(page.locator('.quality-control-facts')).toBeAttached();
}

for (const width of [1280, 390]) {
  for (const scheme of ['light', 'dark'] as const) {
    test(`dense playback settings keep complete actions, aligned help, and scroll access at ${width}px ${scheme}`, async ({ page }) => {
      await openWatch(page, width, true);
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await page.getByRole('button', { name: 'Playback settings' }).click();
      const panel = page.getByRole('group', { name: 'Playback settings' });
      await expect(panel.getByRole('radio', { name: /Français \(Canada\)/ })).toBeAttached();
      if (CAPTURE_OUT) {
        mkdirSync(CAPTURE_OUT, { recursive: true });
        await panel.evaluate((element) => { element.scrollTop = 0; });
        await page.screenshot({ path: join(CAPTURE_OUT, `player-menu-${width}-${scheme}-tracks.png`) });
        await panel.getByRole('button', { name: 'Generate subtitles' }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: join(CAPTURE_OUT, `player-menu-${width}-${scheme}-actions.png`) });
      }
      expect(await panel.evaluate(el => el.scrollWidth)).toBeLessThanOrEqual(await panel.evaluate(el => el.clientWidth));
      expect(await panel.evaluate(el => el.scrollHeight)).toBeGreaterThan(await panel.evaluate(el => el.clientHeight));

      const panelBox = await panel.boundingBox();
      for (const name of ['Quality', 'Version', 'Audio', 'Subtitles', 'Sound and skipping']) {
        const box = await panel.locator('legend', { hasText: name }).boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(panelBox!.x);
        expect(box!.x + box!.width).toBeLessThanOrEqual(panelBox!.x + panelBox!.width);
      }
      for (const name of [/^Original$/, /4K HDR/, /English 5\.1/, /Français \(Canada\)/]) {
        const label = panel.getByRole('radio', { name }).locator('..');
        const box = await label.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.height).toBeGreaterThanOrEqual(44);
        expect(box!.x).toBeGreaterThanOrEqual(panelBox!.x);
        expect(box!.x + box!.width).toBeLessThanOrEqual(panelBox!.x + panelBox!.width);
      }
      for (const name of ['Generate subtitles', 'Sync to audio', 'Translate']) {
        const button = panel.getByRole('button', { name, exact: true });
        await button.scrollIntoViewIfNeeded();
        expect(await button.evaluate((element) => {
          const buttonBox = element.getBoundingClientRect();
          const range = document.createRange();
          range.selectNodeContents(element);
          const textBox = range.getBoundingClientRect();
          return textBox.left >= buttonBox.left && textBox.right <= buttonBox.right;
        })).toBe(true);
        const buttonBox = await button.boundingBox();
        expect(buttonBox!.height).toBeGreaterThanOrEqual(44);
        const reasonId = await button.getAttribute('aria-describedby');
        expect(reasonId).toBeTruthy();
        const reasonBox = await panel.locator(`#${reasonId}`).boundingBox();
        expect(reasonBox).not.toBeNull();
        expect(reasonBox!.y).toBeGreaterThanOrEqual(buttonBox!.y + buttonBox!.height);
        expect(reasonBox!.x).toBeGreaterThanOrEqual(panelBox!.x);
        expect(reasonBox!.x + reasonBox!.width).toBeLessThanOrEqual(panelBox!.x + panelBox!.width);
      }
      const translate = panel.locator('.playback-menu-translate');
      const select = translate.getByRole('combobox');
      expect((await select.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      expect(await translate.evaluate((element) => {
        const label = element.querySelector(':scope > span')!;
        return label.getBoundingClientRect().right <= element.querySelector('.g-select-trigger')!.getBoundingClientRect().left;
      })).toBe(true);
      const origin = panel.getByRole('radio', { name: /Français \(Canada\)/ }).locator('..');
      expect(await origin.evaluate((element) => {
        const text = [...element.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.includes('Français'));
        const range = document.createRange();
        range.selectNode(text!);
        return element.querySelector('small')!.getBoundingClientRect().top >= range.getBoundingClientRect().bottom;
      })).toBe(true);
      const mute = panel.getByRole('checkbox', { name: /Mute strong language/ }).locator('..');
      await mute.scrollIntoViewIfNeeded();
      if (CAPTURE_OUT) await page.screenshot({ path: join(CAPTURE_OUT, `player-menu-${width}-${scheme}-sound.png`) });
      expect(await mute.evaluate((element) => {
        const text = [...element.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.includes('Mute strong language'));
        const range = document.createRange();
        range.selectNode(text!);
        return element.querySelector('small')!.getBoundingClientRect().top >= range.getBoundingClientRect().bottom;
      })).toBe(true);
      await expect(panel.getByRole('checkbox', { name: 'Skip intros automatically' })).toBeVisible();
      const captionStyle = panel.getByRole('button', { name: 'Caption style…' });
      await captionStyle.scrollIntoViewIfNeeded();
      await captionStyle.click();
      if (CAPTURE_OUT) {
        await panel.evaluate((element) => { element.scrollTop = 0; });
        await page.screenshot({ path: join(CAPTURE_OUT, `player-menu-${width}-${scheme}-captions.png`) });
      }
      expect(await panel.evaluate(el => el.scrollWidth)).toBeLessThanOrEqual(await panel.evaluate(el => el.clientWidth));
      for (const name of ['Size', 'Background']) await expect(panel.getByRole('group', { name })).toBeVisible();
      for (const name of ['Small', 'Medium', 'Large', 'Extra large', 'None', 'Box', 'Solid', 'Shadow']) {
        const option = panel.getByRole('radio', { name, exact: true }).locator('..');
        const box = await option.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.height).toBeGreaterThanOrEqual(44);
        expect(box!.x + box!.width).toBeLessThanOrEqual(panelBox!.x + panelBox!.width);
        expect(await option.evaluate((element) => {
          const channels = (value: string) => [...value.matchAll(/[\d.]+/g)].map((part) => Number(part[0]));
          const [red, green, blue] = channels(getComputedStyle(element).color).map((channel) => {
            const value = channel / 255;
            return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
          });
          const foreground = .2126 * red + .7152 * green + .0722 * blue;
          const [bgRed, bgGreen, bgBlue, alpha = 1] = channels(getComputedStyle(element.closest('.player-panel')!).backgroundColor);
          const background = [bgRed, bgGreen, bgBlue].map((channel) => {
            const value = (channel * alpha) / 255;
            return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
          });
          const backdrop = .2126 * background[0] + .7152 * background[1] + .0722 * background[2];
          return (Math.max(foreground, backdrop) + .05) / (Math.min(foreground, backdrop) + .05);
        })).toBeGreaterThanOrEqual(4.5);
      }
      const backgroundOptions = panel.getByRole('group', { name: 'Background' });
      await backgroundOptions.getByRole('radio', { name: 'Box', exact: true }).focus();
      await page.keyboard.press('ArrowRight');
      const focusedOption = backgroundOptions.locator('.g-segment:has(input:focus-visible)');
      await expect(focusedOption).toHaveCount(1);
      const focusedStyle = await focusedOption.evaluate((element) => {
        const luminance = (color: string) => {
          const [red, green, blue] = [...color.matchAll(/[\d.]+/g)].map((part) => Number(part[0])).map((channel) => {
            const value = channel / 255;
            return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
          });
          return .2126 * red + .7152 * green + .0722 * blue;
        };
        const foreground = luminance(getComputedStyle(element).outlineColor);
        const background = luminance(getComputedStyle(element.closest('.player-panel')!).backgroundColor);
        return {
          contrast: (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05),
          style: getComputedStyle(element).outlineStyle,
          width: Number.parseFloat(getComputedStyle(element).outlineWidth),
        };
      });
      expect(focusedStyle.style).toBe('solid');
      expect(focusedStyle.width).toBeGreaterThanOrEqual(2);
      expect(focusedStyle.contrast).toBeGreaterThanOrEqual(3);
      if (CAPTURE_OUT) {
        await panel.getByRole('group', { name: 'Background' }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: join(CAPTURE_OUT, `player-menu-${width}-${scheme}-caption-options.png`) });
      }
      const back = panel.getByRole('button', { name: 'Back', exact: true });
      expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    });
  }
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
