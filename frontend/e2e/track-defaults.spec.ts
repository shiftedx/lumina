import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { CaptionPrefs } from '../src/features/watch/captionPrefs';
import { mockApi, signIn } from './lumina-mock';

test('member language defaults save with caption style and fit both themes on a narrow phone', async ({ page }, testInfo) => {
  const saved = await mockApi(page, { captions: { size: 'medium', background: 'shadow' } });
  await page.goto('/settings/playback?page=captions'); await signIn(page);
  await page.getByRole('textbox', { name: 'Audio languages' }).fill('jpn, en-US');
  await page.getByRole('textbox', { name: 'Audio languages' }).press('Tab');
  await page.getByRole('switch', { name: 'Enable subtitles by default' }).click();
  await page.getByRole('textbox', { name: 'Subtitle languages' }).fill('fre, en');
  await page.getByRole('textbox', { name: 'Subtitle languages' }).press('Tab');
  await page.getByRole('switch', { name: 'Use only forced subtitles' }).click();
  await expect.poll(() => ((saved.at(-1)?.ui_prefs as { captions?: CaptionPrefs })?.captions)?.tracks).toEqual({ audioLanguages: ['ja', 'en'], subtitleLanguages: ['fr', 'en'], subtitlesEnabled: true, forcedOnly: true, preferSdh: false });
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 320, height: 740 }]) {
      await page.setViewportSize(viewport);
      await page.goto('/settings/playback?page=captions');
      await expect(page.getByRole('textbox', { name: 'Audio languages' })).toHaveValue('ja, en');
      expect(await page.locator('.g-settings-pane').evaluate((node) => node.scrollWidth > node.clientWidth + 1 || document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
      expect((await new AxeBuilder({ page }).include('[data-setting-id="playback.captions"]').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()).violations).toEqual([]);
      await page.screenshot({ fullPage: true, path: testInfo.outputPath(`track-defaults-${viewport.name}-${scheme}.png`) });
    }
  }
});
