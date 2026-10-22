import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { mockAdminFixtures, mockApi, signIn } from './lumina-mock';

const server = { server_id: 'sonarr-1', kind: 'sonarr', name: 'Living room TV', enabled: true, version: '4.0', capabilities: {}, summary: { inventory_count: 1201, queue_count: 2, history_count: 0, health_count: 0, calendar_count: 0, missing_count: 0, cutoff_count: 0, import_counts: { registered: 1200, awaiting_first_import: 1, pending_import: 0, root_unavailable: 0, unmapped: 0 } }, last_ok_at: '2026-10-17T12:00:00Z', last_error: null, stale: false, attempted_at: '2026-10-17T12:00:00Z' };
const item = { server_id: 'sonarr-1', upstream_id: 7, media_type: 'tv', tmdb_id: null, tvdb_id: 7, title: 'Harbor Lights', year: 2026, monitored: true, has_file: true, import_state: 'awaiting_first_import', details: { episodes: { episode_count: 1, file_count: 1 } }, observed_at: '2026-10-17T12:00:00Z' };
const controlled = { server_id: 'sonarr-1', upstream_id: 7, revision: 'fresh-revision', monitored: true, quality_profile_id: 4, seasons: [{ season_number: 1, monitored: true }], profiles: [{ id: 4, name: 'HD' }, { id: 8, name: 'Archive' }], quality: { current: 'WEBDL-1080p', target: 'HD', cutoff: 'Bluray-1080p' } };
type Mode = 'success' | 'conflict' | 'uncertain';

async function mockIntegrationOperations(page: Page, state: { mode: Mode; stale?: boolean }) {
  const posted: Array<Record<string, unknown>> = [];
  const operations: Array<Record<string, unknown>> = [];
  const operation = (body: Record<string, unknown>, status: string, error: string | null = null) => ({ id: body.operation_id, server_id: 'sonarr-1', upstream_id: 7, action: body.action, status, result: {}, error, created_at: '2026-10-17T12:00:00Z', updated_at: '2026-10-17T12:00:00Z' });
  await page.route('**/api/admin/arr/**', async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/admin/arr/observations') { const url = new URL(request.url()); return json({ servers: [{ ...server, stale: state.stale ?? false }], items: url.searchParams.get('server_id') ? [item] : [], page: 1, page_size: 50, total: url.searchParams.get('server_id') ? 1201 : 0, total_pages: url.searchParams.get('server_id') ? 25 : 1 }); }
    if (path === '/api/admin/arr/servers/sonarr-1/refresh') return json(server);
    if (path === '/api/admin/arr/servers/sonarr-1/items/7') return json(controlled);
    if (path === '/api/admin/arr/servers/sonarr-1/items/7/releases') return json({ revision: 'fresh-revision', items: [{ release_id: 'release-1', title: 'Harbor Lights 1080p', quality: 'WEBDL-1080p', languages: ['English'], rejections: ['Does not meet the configured cutoff'], approved: false, size: 1_200_000_000 }] });
    if (path === '/api/admin/arr/servers/sonarr-1/items/7/operations' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>; posted.push(body);
      if (state.mode === 'conflict') { const result = operation(body, 'conflict', 'This item changed upstream.'); operations.unshift(result); return json({ detail: { detail: 'stale_resource', operation: result } }, 409); }
      if (state.mode === 'uncertain') { operations.unshift(operation(body, 'uncertain', 'Dispatch outcome unknown.')); return json({ detail: 'The integration did not confirm the operation.' }, 503); }
      const result = operation(body, 'succeeded'); operations.unshift(result); return json(result);
    }
    if (path === '/api/admin/arr/operations') return json({ items: operations, page: 1, page_size: 25, total: operations.length, total_pages: 1 });
    if (/^\/api\/admin\/arr\/operations\/[^/]+\/reconcile$/.test(path)) { const id = path.split('/')[5]; const result = operations.find((entry) => entry.id === id); if (result) { result.status = 'succeeded'; result.error = null; } return json(result); }
    const queue = path.endsWith('/queue') ? [1, 2].map((index) => ({ record_id: `queue-${index}`, item_id: 6 + index, data: { status: 'Downloading', sizeleft: 1048576 * index, timeleft: '00:02:00', protocol: 'torrent' }, observed_at: server.last_ok_at })) : [];
    return json({ items: queue, page: 1, page_size: 50, total: queue.length, total_pages: 1, stale: state.stale ?? false });
  });
  return { operations, posted };
}

async function mockConnectors(page: Page) {
  const connectors: Array<Record<string, unknown>> = [];
  const observations: Record<string, Record<string, unknown[]>> = {};
  await page.route(/\/api\/admin\/connectors(?:\/|\?|$)/, async (route) => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const connector = (id: string) => connectors.find((entry) => entry.id === id);
    if (path === '/api/admin/connectors' && request.method() === 'GET') return json({ items: connectors });
    if (path === '/api/admin/connectors' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>; const id = `connector-${connectors.length + 1}`;
      const next = { id, kind: body.kind, name: body.name, base_url: body.base_url, settings: body.settings, api_key_set: Boolean(body.api_key), username_set: Boolean(body.username), password_set: Boolean(body.password), enabled: false, version: null, last_ok_at: null, last_error: null, stale: true, capabilities: {} };
      connectors.push(next); observations[id] = { root_folders: [{ id: 1, path: '/music' }], quality_profiles: [{ id: 2, name: 'Lossless' }], metadata_profiles: [{ id: 3, name: 'Standard' }], transfers: [{ id: 'job-1', state: 'downloading', correlation: 'matched', server_id: 'sonarr-1', upstream_id: 7 }] }; return json(next);
    }
    const refreshMatch = path.match(/^\/api\/admin\/connectors\/([^/]+)\/refresh$/);
    if (refreshMatch && request.method() === 'POST') { const row = connector(refreshMatch[1]); if (!row) return json({ detail: 'missing connector' }, 404); Object.assign(row, { enabled: true, version: '2.3', last_ok_at: '2026-10-17T12:00:00Z', last_error: null, stale: false, capabilities: { observe: true, control: false } }); return json(row); }
    const match = path.match(/^\/api\/admin\/connectors\/([^/]+)$/); if (!match) return json({ detail: 'missing connector' }, 404);
    const row = connector(match[1]); if (!row) return json({ detail: 'missing connector' }, 404);
    if (request.method() === 'GET') { const dataset = url.searchParams.get('dataset') || 'transfers'; const items = observations[match[1]]?.[dataset] ?? []; return json({ ...row, dataset, items, total: items.length, page: 1, page_size: 50 }); }
    if (request.method() === 'PUT') { const body = request.postDataJSON() as Record<string, unknown>; Object.assign(row, { kind: body.kind, name: body.name, base_url: body.base_url, settings: body.settings, enabled: false, version: null, last_ok_at: null, last_error: null, stale: true, capabilities: {} }); return json(row); }
    if (request.method() === 'DELETE') { Object.assign(row, { enabled: false, api_key_set: false, username_set: false, password_set: false, stale: true }); return json(row); }
    return json({ detail: 'unsupported' }, 405);
  });
  return { connectors };
}

async function mockMusicRequests(page: Page) {
  const requests: Array<Record<string, unknown>> = [];
  const json = (route: import('@playwright/test').Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/music/request-targets', (route) => json(route, { items: [{ id: 'lidarr-1', name: 'Music' }] }));
  await page.route('**/api/music/requests', (route) => { if (route.request().method() !== 'POST') return json(route, { items: [] }); const body = route.request().postDataJSON() as Record<string, unknown>; const next = { ...body, artist_id: null, album_id: null, status: 'pending_approval', result: {}, error: null, created_at: '2026-10-17T12:00:00Z', updated_at: '2026-10-17T12:00:00Z' }; requests.unshift(next); return json(route, next); });
  await page.route(/\/api\/admin\/music\/requests(?:\/|\?|$)/, (route) => { const path = new URL(route.request().url()).pathname; const match = path.match(/^\/api\/admin\/music\/requests\/([^/]+)\/(approve|reconcile)$/); if (!match) return json(route, { items: requests, page: 1, page_size: 25, total: requests.length, total_pages: 1 }); const row = requests.find((entry) => entry.id === match[1]); if (!row) return json(route, { detail: 'missing' }, 404); row.status = match[2] === 'approve' ? 'awaiting_import' : 'uncertain'; return json(route, row); });
  return { requests };
}

async function openIntegrations(page: Page) { await mockConnectors(page); const music = await mockMusicRequests(page); await page.goto('/'); await signIn(page); await page.goto('/settings/requests?page=integrations'); await expect(page.getByRole('button', { name: 'Manage Harbor Lights' })).toBeVisible(); return music; }

test('Integration information fits both themes on desktop and a narrow phone', async ({ page }) => {
  await mockApi(page, { settings_advanced: true });
  await mockAdminFixtures(page);
  await mockIntegrationOperations(page, { mode: 'success', stale: true });
  await openIntegrations(page);
  for (const scheme of ['light', 'dark'] as const) {
    for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 320, height: 740 }]) {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await page.setViewportSize(viewport);
      await page.goto('/settings/requests?page=integrations');
      await expect(page.getByRole('button', { name: 'Manage Harbor Lights' })).toBeVisible();
      const information = page.getByRole('region', { name: 'Information from Living room TV' });
      await expect(information).toContainText('1,201');
      await expect(information).toContainText('1,200');
      await expect(page.getByText(/Saved snapshot from/).first()).toBeVisible();
      await expect(page.getByRole('button', { name: 'Queue 2 records' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Health 0 records' })).toBeVisible();
      expect((await new AxeBuilder({ page }).include('.arr-integrations').withRules(['color-contrast']).analyze()).violations).toEqual([]);
      await page.getByRole('button', { name: 'Queue 2 records' }).focus();
      await expect(page.getByRole('button', { name: 'Queue 2 records' })).toBeFocused();
      await page.locator('main[aria-label="Main content"]').evaluate((node) => { node.setAttribute('tabindex', '-1'); (node as HTMLElement).focus(); });
      await expect(page.getByRole('link', { name: 'Skip to main content' })).not.toBeFocused();
      await expect.poll(() => page.locator('.g-settings-pane').evaluate((node) => node.scrollWidth > node.clientWidth + 1 || document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
      await information.evaluate((node) => node.scrollIntoView({ block: 'center', behavior: 'instant' }));
      await information.screenshot({ path: `../output/playwright/integrations-visuals/information-${scheme}-${viewport.name}.png` });
      await page.screenshot({ fullPage: true, path: `../output/playwright/integrations-visuals/integrations-${scheme}-${viewport.name}.png` });
    }
  }
});

test('admin saves then explicitly tests a Lidarr connector, with read-only saved observations', async ({ page }) => {
  await mockApi(page, { settings_advanced: true }); await mockAdminFixtures(page); await mockIntegrationOperations(page, { mode: 'success' }); await openIntegrations(page);
  await page.getByRole('button', { name: 'Add connector' }).click(); await page.getByLabel('Lidarr address').fill('http://lidarr.test'); await page.getByLabel('Lidarr API key').fill('fixture-key'); await page.getByRole('button', { name: 'Save connector' }).click();
  await expect(page.getByText('Connector saved. Test this connection to qualify it.')).toBeVisible(); await expect(page.getByText('Saved. Test this connection to qualify it.').first()).toBeVisible();
  await page.getByRole('button', { name: 'Test connection' }).click(); await expect(page.getByText('Connection tested. Saved observations are available below.')).toBeVisible(); await expect(page.locator('.connector-records').getByText('/music')).toBeVisible();
  await expect(page.getByRole('button', { name: /grab|transfer|start download/i })).toHaveCount(0);
  await page.setViewportSize({ width: 320, height: 740 }); await expect.poll(() => page.locator('.g-settings-pane').evaluate((node) => node.scrollWidth > node.clientWidth + 1 || document.documentElement.scrollWidth > innerWidth + 1)).toBe(false); await page.screenshot({ fullPage: true, path: '../output/playwright/integrations-visuals/connector-phone.png' });
});


test('admin approves a member album request without any transfer or deletion control', async ({ page }) => {
  await mockApi(page, { settings_advanced: true }); await mockAdminFixtures(page); await mockIntegrationOperations(page, { mode: 'success' }); const music = await openIntegrations(page);
  music.requests.unshift({ id: '11111111-1111-4111-8111-111111111111', connector_id: 'lidarr-1', foreign_artist_id: '22222222-2222-4222-8222-222222222222', foreign_album_id: '33333333-3333-4333-8333-333333333333', artist_name: 'Northbound', album_name: 'Arrival', artist_id: null, album_id: null, status: 'pending_approval', result: {}, error: null, created_at: '2026-10-17T12:00:00Z', updated_at: '2026-10-17T12:00:00Z' });
  await page.getByRole('button', { name: 'Refresh requests' }).click(); await expect(page.getByText('Arrival')).toBeVisible(); await page.getByRole('button', { name: 'Approve request' }).click(); await expect(page.getByRole('region', { name: 'Music requests' }).getByRole('listitem').filter({ hasText: 'Arrival' }).getByText('Waiting for import')).toBeVisible(); await expect(page.getByRole('button', { name: /delete|transfer|cancel/i })).toHaveCount(0);
  expect((await new AxeBuilder({ page }).include('.music-request-operations').withRules(['color-contrast']).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 320, height: 740 }); await expect.poll(() => page.locator('.g-settings-pane').evaluate((node) => node.scrollWidth > node.clientWidth + 1 || document.documentElement.scrollWidth > innerWidth + 1)).toBe(false); await page.screenshot({ fullPage: true, path: '../output/playwright/integrations-visuals/music-request-phone.png' });
});
