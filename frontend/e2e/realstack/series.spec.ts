import type { Page, Response } from '@playwright/test';
import { expect, test, videoTime } from './realstack';

/** A show starts, resumes from its page after Home lists it, and moves on to the next episode. */

const playbackSave = (completed: boolean) => (response: Response) => response.request().method() === 'PUT'
  && /\/api\/library\/[^/]+\/playback$/.test(new URL(response.url()).pathname)
  && (response.request().postDataJSON() as { completed?: boolean }).completed === completed;

async function openShow(page: Page) {
  await page.goto('/library/shows');
  await page.getByRole('button', { name: /^Realstack Show,/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Realstack Show' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Season 1' })).toHaveAttribute('aria-selected', 'true');
}

test('a show starts, resumes from Home and moves on to the next episode', async ({ page }) => {
  await test.step('start the first episode and leave it part-way', async () => {
    await openShow(page);
    await page.getByRole('button', { name: 'Start S1 · E1', exact: true }).click();
    await expect(page).toHaveURL(/\/watch\/library\//);
    await expect(page.locator('.watch-episode-byline')).toContainText('S1 · E1');
    await expect.poll(() => videoTime(page)).toBeGreaterThan(0.5);
    const saved = page.waitForResponse(playbackSave(false));
    await page.locator('video').evaluate((video: HTMLVideoElement) => { video.currentTime = 30; });
    await expect.poll(() => videoTime(page)).toBeGreaterThanOrEqual(30);
    await page.locator('video').evaluate((video: HTMLVideoElement) => video.pause());
    await saved;
  });

  await test.step('Home lists it under Continue watching', async () => {
    await page.goto('/');
    await expect(page.getByRole('region', { name: 'Continue watching' })).toContainText('S1 · E1');
  });

  await test.step('the show page resumes past the saved position', async () => {
    await openShow(page);
    await page.getByRole('button', { name: 'Resume S1 · E1', exact: true }).click();
    await expect.poll(() => videoTime(page)).toBeGreaterThanOrEqual(29);
  });

  await test.step('playing to the end offers the next episode and records the finish', async () => {
    const completed = page.waitForResponse(playbackSave(true));
    await page.locator('video').evaluate((video: HTMLVideoElement) => { video.currentTime = video.duration - 5; return video.play(); });
    const upNext = page.getByRole('region', { name: 'Next episode' });
    await expect(upNext).toContainText('S1 · E2');
    await upNext.getByRole('button', { name: 'Cancel' }).click();
    await completed;
    await expect(page.locator('.watch-episode-byline')).toContainText('S1 · E1');
  });

  await test.step('the show page now plays the second episode', async () => {
    await openShow(page);
    await expect(page.getByRole('button', { name: 'Play S1 · E2', exact: true })).toBeVisible();
    for (const colorScheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' }); // no shot mid-transition
      await page.screenshot({ path: `../output/playwright/realstack/series-title-${colorScheme}.png` });
    }
    await page.emulateMedia({ colorScheme: 'light', reducedMotion: null });
  });
});
