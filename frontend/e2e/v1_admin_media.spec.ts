import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** Media server administration and connected apps (mocked API). */

const baseSettings = { jellyfin_enabled: false, jellyfin_url: 'https://vault.example', has_tmdb_key: false, metadata_language: 'en-US', introdb_enabled: false, hwaccel: 'auto', max_playback_sessions: 3, transcode_cache_gb: 10, jellyfin_import_url: null };
const TOKEN = 'lum_agent_e2e_7f3c9d';

async function mockMediaServer(page: Page) {
  let current: Record<string, unknown> = { ...baseSettings };
  const puts: Record<string, unknown>[] = [];
  type App = { id: string; user_id: string; owner_display_name: string; kind: string; scope: string; device_name: string; client: string | null; client_version: string | null; created_at: string; last_seen_at: string | null };
  let apps: App[] = [{ id: 'd1', user_id: 'member-1', owner_display_name: 'Alexandria', kind: 'jellyfin', scope: 'write', device_name: 'Living room Apple TV', client: 'Infuse-Direct', client_version: '8.1', created_at: '2026-09-20T10:00:00Z', last_seen_at: '2026-09-25T08:00:00Z' }];
  await page.route((url) => url.pathname.startsWith('/api/admin/media-server') || url.pathname === '/api/admin/metadata/unmatched' || url.pathname.startsWith('/api/connected-apps'), async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/admin/media-server' && method === 'PUT') {
      const body = request.postDataJSON() as Record<string, unknown>;
      puts.push(body);
      const { tmdb_api_key: key, ...rest } = body;
      current = { ...current, ...rest, has_tmdb_key: key === undefined ? current.has_tmdb_key : Boolean(key) };
      return json(current);
    }
    if (path === '/api/admin/media-server') return json(current);
    if (path === '/api/admin/media-server/tmdb-test') return json({ ok: true });
    if (path === '/api/admin/metadata/unmatched') return json([]);
    if (path === '/api/connected-apps' && method === 'POST') {
      const body = request.postDataJSON() as { name: string; scope: string };
      const app = { ...apps[0], id: 'd2', kind: 'agent', scope: body.scope, device_name: body.name, client: null, client_version: null, last_seen_at: null };
      apps = [app, ...apps];
      return json({ app, token: TOKEN }, 201);
    }
    if (path === '/api/connected-apps') return json(apps);
    const revoke = /^\/api\/connected-apps\/([^/]+)$/.exec(path);
    if (revoke && method === 'DELETE') { apps = apps.filter((app) => app.id !== revoke[1]); return route.fulfill({ status: 204 }); }
    return route.fallback();
  });
  return puts;
}

test('TMDB key, Jellyfin toggle and Infuse instructions', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  const puts = await mockMediaServer(page);
  await page.goto('/admin/media');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Media server' })).toBeVisible();
  await page.getByLabel('TMDB API key').fill('tmdb-e2e-key');
  await page.getByRole('switch', { name: 'Let apps like Infuse connect' }).check();
  await page.getByRole('button', { name: 'Save media server settings' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  expect(puts).toEqual([{ tmdb_api_key: 'tmdb-e2e-key', jellyfin_enabled: true }]);
  await expect(page.getByLabel('TMDB API key')).toHaveValue('');
  await expect(page.getByLabel('TMDB API key')).toHaveAttribute('placeholder', /Saved key hidden/);
  await expect(page.getByRole('textbox', { name: 'Server address for apps' })).toHaveValue('https://vault.example');
  await expect(page.getByRole('list', { name: 'Connect Infuse' }).getByRole('listitem')).toHaveCount(4);
  await page.getByRole('button', { name: 'Test key' }).click();
  await expect(page.getByText('The key works.')).toBeVisible();
});

test('an agent token is shown once, then revoked', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false });
  await mockMediaServer(page);
  await page.goto('/settings/apps');
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Connected apps' }).first()).toBeVisible();
  await page.getByLabel('Token name').fill('Home Assistant');
  await page.getByRole('button', { name: 'Create agent token' }).click();
  const token = page.getByRole('textbox', { name: 'Token for Home Assistant' });
  await expect(token).toHaveValue(TOKEN);
  await expect(token).toBeFocused();
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(token).toHaveCount(0);
  await page.getByRole('button', { name: 'Revoke Home Assistant' }).click();
  await page.getByRole('dialog', { name: 'Revoke Home Assistant?' }).getByRole('button', { name: 'Revoke', exact: true }).click();
  await expect(page.getByText('Home Assistant was revoked. It has to sign in again.')).toBeVisible();
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage }))).not.toContain(TOKEN);
});
