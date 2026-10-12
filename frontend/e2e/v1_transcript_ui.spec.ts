import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, mockTranscripts, signIn } from './lumina-mock';

/** Transcript tool: paged cues, search, follow playback, cue seek. */

async function openTranscript(page: Page, viewport: { width: number; height: number }, scheme: 'light' | 'dark' = 'dark') {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await mockTranscripts(page);
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Transcript' }).click();
  await expect(page.getByRole('list', { name: 'Transcript' }).getByRole('button').first()).toBeVisible();
}

test('test_transcript_search_seek', async ({ page }) => {
  await openTranscript(page, { width: 1536, height: 960 });
  const list = page.getByRole('list', { name: 'Transcript' });
  await expect(list.getByRole('listitem')).toHaveCount(100); // first page only, not all 240
  await page.getByRole('searchbox', { name: 'Search transcript' }).fill('stone bridge');
  await page.getByRole('searchbox', { name: 'Search transcript' }).press('Enter');
  await expect(page.getByText('1 of 40')).toBeVisible();
  await page.getByRole('button', { name: 'Previous match' }).click(); // wraps to the last match, ordinal 236 → loads later pages
  await expect(page.getByText('40 of 40')).toBeVisible();
  const match = list.locator('button.current-match');
  // Scrolled into view inside the transcript list (the page itself is not yanked).
  await expect.poll(() => match.evaluate((row) => {
    const list = row.closest('ol')!.getBoundingClientRect();
    const rect = row.getBoundingClientRect();
    return rect.top >= list.top && rect.bottom <= list.bottom;
  })).toBe(true);
  // The synthetic clip is 1.5s, so record the requested seek rather than the clamped position.
  await page.locator('video').first().evaluate((media: HTMLVideoElement) => {
    const native = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!;
    Object.defineProperty(media, 'currentTime', { configurable: true, get: () => native.get!.call(media), set: (value: number) => { (window as unknown as { seekTo: number }).seekTo = value; native.set!.call(media, value); } });
  });
  await match.locator('mark').first().click();
  expect(await page.evaluate(() => (window as unknown as { seekTo: number }).seekTo)).toBe(236 * 1.5);
  await expect(page.getByRole('button', { name: 'Follow playback' })).toHaveAttribute('aria-pressed', 'false');
});

test('test_transcript_search_state', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openTranscript(page, { width: 1536, height: 960 });
  await page.getByRole('searchbox', { name: 'Search transcript' }).fill('lake');
  await page.getByRole('searchbox', { name: 'Search transcript' }).press('Enter');
  await expect(page.getByText(/1 of \d+/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});
