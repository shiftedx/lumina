import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { mockApi, user } from './lumina-mock';

for (const viewport of [{ name: 'phone', width: 390, height: 900 }, { name: 'desktop', width: 1440, height: 900 }]) {
  test(`${viewport.name} first-owner transfer shows responsive streamed progress without accessibility or overflow regressions`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await mockApi(page, {});
    await page.addInitScript(() => {
      const nativeFetch = window.fetch.bind(window);
      const encoder = new TextEncoder();
      window.fetch = (input, init) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.includes('/api/admin/household-transfer/plex/import')) return nativeFetch(input, init);
        return Promise.resolve(new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode('data: {"type":"connecting"}\n\ndata: {"type":"progress","source_id":"owner","source_name":"Owner","completed":0,"total":2}\n\n'));
            window.setTimeout(() => controller.enqueue(encoder.encode('data: {"type":"member","member":{"source_id":"owner","source_name":"Owner","disabled":false,"action":"import","lumina_username":"alexandria","reason":null,"error":null,"summary":{"watched":18,"in_progress":2,"favorites":0,"up_to_date":4,"unmatched":1},"reset_url":null,"reset_expires_at":null},"completed":1,"total":2}\n\n')), 1200);
            window.setTimeout(() => {
              controller.enqueue(encoder.encode('data: {"type":"member","member":{"source_id":"sam","source_name":"Sam","disabled":false,"action":"create","lumina_username":"sam","reason":null,"error":null,"summary":{"watched":7,"in_progress":1,"favorites":0,"up_to_date":0,"unmatched":0},"reset_url":"http://lumina.test/reset/sam","reset_expires_at":"2026-10-23T00:00:00Z"},"completed":2,"total":2}\n\ndata: {"type":"complete","completed":2,"total":2}\n\n'));
              controller.close();
            }, 1800);
          },
        }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
      };
    });
    await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending', vault_setup_pending: true } } }));
    await page.route('**/api/admin/onboarding/media', (route) => route.fulfill({ json: { mount_parents: ['/media'] } }));
    await page.route('**/api/admin/storage/roots', (route) => route.fulfill({ json: [{ id: 'films', label: 'Films', path: '/media/films', mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 24, observation: { state: 'available' } }] }));
    await page.route('**/api/admin/imports', (route) => route.fulfill({ json: [] }));
    await page.route('**/api/admin/media-server', (route) => route.fulfill({ json: { jellyfin_import_url: null, plex_import_url: 'http://plex.local:32400' } }));
    await page.route('**/api/admin/household-transfer/plex/discover', (route) => route.fulfill({ json: { members: [
      { source_id: 'owner', source_name: 'Owner', disabled: false, action: 'import', lumina_username: 'alexandria', reason: null, error: null, summary: null, reset_url: null, reset_expires_at: null },
      { source_id: 'sam', source_name: 'Sam', disabled: false, action: 'create', lumina_username: 'sam', reason: null, error: null, summary: null, reset_url: null, reset_expires_at: null },
    ] } }));

    await page.goto('/');
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.getByRole('radio', { name: /Plex/ }).check();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.getByLabel('Plex server owner token').fill('fixture-owner-token');
    await page.getByRole('button', { name: 'Find people' }).click();
    await expect(page.getByText('2 found · 1 new · 1 already in Lumina · 2 selected')).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`household-transfer-${viewport.name}-selection.png`), fullPage: true });
    await page.getByRole('button', { name: 'Bring over 2 people' }).click();
    await expect(page.getByText(/Working on Owner/)).toBeVisible();

    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 'no horizontal page scroll').toBeLessThanOrEqual(0);
    const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
    expect(axe.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => violation.id)).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`household-transfer-${viewport.name}-progress.png`), fullPage: true });
    await expect(page.getByRole('heading', { name: 'Household transfer complete' })).toBeVisible();
    await expect(page.getByLabel('Password link for sam')).toHaveValue('http://lumina.test/reset/sam');
    await page.screenshot({ path: testInfo.outputPath(`household-transfer-${viewport.name}-results.png`), fullPage: true });
  });
}
