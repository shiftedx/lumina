import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import { animeDetail, quotas, request } from '../src/features/requests/requestsFixtures';
import type { ServerNavigation } from '../src/types';
import { mockApi, signIn } from './lumina-mock';

const OUT = join(process.cwd(), '..', 'output', 'server-navigation-language');
const NAV: ServerNavigation = { categories: ['movies', 'recordings'], features: { streaming: false, requests: true } };
const available = { ...animeDetail, title: 'The Lantern Harbor', status: { state: 'available' as const, library_title_id: 'title-1', language_requests_supported: true } };

for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 390, height: 844 }]) {
  test(`server navigation and language variants: ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    mkdirSync(OUT, { recursive: true });
    await mockApi(page, {}, NAV);
    const submissions: unknown[] = [];
    await page.route(/\/api\/requests(?:\/|\?|$)/, async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/requests/catalog/languages/title-1') return route.fulfill({ json: { audio: 'partial', subtitles: 'missing' } });
      if (path.startsWith('/api/requests/catalog/title/')) return route.fulfill({ json: available });
      if (path === '/api/requests/quota') return route.fulfill({ json: { quotas } });
      if (path === '/api/requests' && route.request().method() === 'POST') {
        submissions.push(route.request().postDataJSON());
        return route.fulfill({ status: 201, json: request({ id: 'variant-1', status: 'pending', audio_language: 'en', subtitle_language: null, fulfillment_mode: 'manual' }) });
      }
      if (path === '/api/requests') return route.fulfill({ json: { items: [], page: 1, total_pages: 1, counts: {} } });
      return route.fallback();
    });
    await page.goto('/');
    await signIn(page);
    await page.getByRole('button', { name: 'Open navigation' }).click();
    const menu = page.getByRole('dialog', { name: 'Navigation' });
    await expect(menu.getByRole('button', { name: 'Movies', exact: true })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Requests', exact: true })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Shows', exact: true })).toHaveCount(0);
    await expect(menu.getByRole('button', { name: 'Streaming', exact: true })).toHaveCount(0);
    await expect(menu.getByRole('button', { name: 'Books', exact: true })).toHaveCount(0);
    await page.screenshot({ path: join(OUT, `navigation-${viewport.name}.png`) });
    await page.goto('/requests/title/anime/16498');
    await expect(page.getByText('English dub: Some episodes · English subtitles: Missing')).toBeVisible();
    await page.getByRole('button', { name: 'Request another language' }).click();
    const dialog = page.getByRole('dialog', { name: 'Request The Lantern Harbor' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('radio', { name: /English audio/ }).check();
    await page.screenshot({ path: join(OUT, `language-${viewport.name}.png`) });
    await dialog.getByRole('button', { name: 'Request', exact: true }).click();
    await expect.poll(() => submissions.length).toBe(1);
    expect(submissions[0]).toMatchObject({ kind: 'anime', anilist_id: 16498, audio_language: 'en' });
    await expect(page.getByRole('button', { name: 'Open in Library' })).toBeVisible();
  });
}
