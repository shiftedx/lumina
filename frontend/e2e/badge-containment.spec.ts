import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import { seriesSummary, userData } from '../src/test/galleryFixtures';
import { mockApi, signIn } from './lumina-mock';

const OUT = join(process.cwd(), '..', 'output', 'badge-containment');
for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 390, height: 844 }]) {
  test(`spotlight unwatched count stays with its artwork: ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    mkdirSync(OUT, { recursive: true });
    await mockApi(page, {});
    const show = seriesSummary('lantern', { name: 'Lantern Harbor', category: 'shows', user_data: userData({ unplayed_count: 47 }) });
    await page.route('**/api/titles?**', (route) => route.fulfill({ json: { items: [show], total: 1, next_cursor: null } }));
    await page.goto('/video');
    await signIn(page);
    const feature = page.locator('.g-all.is-stage-led .g-spotlight .g-feature.is-lead');
    const badge = feature.locator('.g-marker-count');
    await expect(feature).toHaveAccessibleName('Lantern Harbor, 47 unwatched episodes');
    await expect(badge).toHaveText('47');
    const check = async () => {
      const tile = await feature.boundingBox();
      const mark = await badge.boundingBox();
      const header = await page.locator('header.g-topbar').boundingBox();
      expect(tile && mark && header).toBeTruthy();
      expect(mark!.x).toBeGreaterThanOrEqual(tile!.x);
      expect(mark!.x + mark!.width).toBeLessThanOrEqual(tile!.x + tile!.width);
      expect(mark!.y).toBeGreaterThanOrEqual(tile!.y);
      expect(mark!.y + mark!.height).toBeLessThanOrEqual(tile!.y + tile!.height);
      expect(mark!.y).toBeGreaterThan(header!.y + header!.height);
    };
    await check();
    await page.screenshot({ path: join(OUT, `spotlight-${viewport.name}.png`) });
    await page.evaluate(() => window.scrollBy(0, 150));
    await check();
  });
}
