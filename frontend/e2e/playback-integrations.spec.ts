import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { item, mockApi, signIn } from './lumina-mock';

const visuals = '../output/playwright/playback-integrations-visuals';

test('Chromecast acknowledges load/control and keeps a recoverable browser source on failure', async ({ page }) => {
  await mockApi(page, {});
  const requests: string[] = [];
  let minted = 0;
  await page.route('**/api/chromecast/**', async (route) => {
    const request = route.request();
    requests.push(`${request.method()} ${new URL(request.url()).pathname}`);
    const body = request.postDataJSON();
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/chromecast/tickets') minted += 1;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(request.method() === 'POST' ? {
      ticket_id: `ticket-${minted}`, media_url: `https://lumina.test/api/chromecast/ticket-${minted}/media?token=scoped`, content_type: 'video/mp4', title: 'Video', duration: 90,
      position_seconds: body?.position_seconds ?? 0, expires_at: '2026-10-17T20:00:00Z', qualification: 'unverified_hardware',
    } : {}) });
  });
  await page.route(`**/api/library/${item.id}/playback-options*`, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    mode: 'direct', reason: null, facts: { container: 'mp4', video_codec: 'h264', audio_codec: 'aac', width: 160, height: 90, duration: 90 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 2,
  }) }));
  await page.addInitScript(() => {
    const actions: string[] = [];
    const receiver = { media: { contentId: '' }, currentTime: 0, playerState: 'IDLE',
      getStatus: (_request: unknown, done: () => void) => done(),
      play: (_request: unknown, done: () => void) => { actions.push('play'); receiver.playerState = 'PLAYING'; done(); },
      pause: (_request: unknown, done: () => void) => { actions.push('pause'); receiver.playerState = 'PAUSED'; done(); },
      stop: (_request: unknown, done: () => void) => { actions.push('stop'); receiver.playerState = 'IDLE'; done(); },
      seek: (request: { currentTime: number }, done: () => void) => { actions.push('seek'); receiver.currentTime = request.currentTime; done(); },
    };
    Object.defineProperty(window, '__castActions', { value: actions });
    Object.defineProperty(window, '__failCastLoad', { value: false, writable: true });
    const session = { getMediaSession: () => receiver, async loadMedia(request: { media: { contentId: string }; currentTime: number }) {
      actions.push('load');
      if (Reflect.get(window, '__failCastLoad')) throw new Error('fixture load failure');
      receiver.media = request.media; receiver.currentTime = request.currentTime; receiver.playerState = 'PLAYING';
    } };
    Object.defineProperty(window, 'cast', { configurable: true, value: { framework: { CastContext: { getInstance: () => ({ setOptions() {}, requestSession: () => Promise.resolve(), getCurrentSession: () => session }) } } } });
    Object.defineProperty(window, 'chrome', { configurable: true, value: { cast: { media: {
      MediaInfo: class { constructor(public contentId: string, public contentType: string) {} }, LoadRequest: class { constructor(public media: object) {} },
      Track: class { constructor(public trackId: number, public type: string) {} }, TrackType: { TEXT: 'TEXT' }, TextTrackType: { SUBTITLES: 'SUBTITLES' },
    } } } });
  });
  await page.goto(`/watch/library/${item.id}`); await signIn(page);
  const panel = page.getByRole('group', { name: 'Chromecast output' });
  await panel.locator('summary').click();
  await panel.getByRole('button', { name: 'Cast to Chromecast' }).click();
  await expect(panel.getByText(/Playing on Chromecast/)).toBeVisible();
  await panel.getByRole('button', { name: 'Pause Chromecast' }).click();
  await expect(panel.getByRole('button', { name: 'Resume Chromecast' })).toBeVisible();
  await panel.getByRole('button', { name: 'Resume Chromecast' }).click();
  await panel.getByRole('spinbutton', { name: 'Chromecast position in seconds' }).fill('12');
  await panel.getByRole('spinbutton', { name: 'Chromecast position in seconds' }).press('Tab');
  await expect(panel.getByText(/0:12/)).toBeVisible();
  mkdirSync(visuals, { recursive: true });
  for (const scheme of ['light', 'dark'] as const) for (const width of [1440, 320]) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' }); await page.setViewportSize({ width, height: 900 });
    await panel.scrollIntoViewIfNeeded();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    const disclosure = await panel.locator('summary').boundingBox();
    expect(disclosure?.height).toBeGreaterThanOrEqual(width < 600 ? 44 : 36);
    expect((await new AxeBuilder({ page }).include('details[aria-label="Chromecast output"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
    await page.screenshot({ path: `${visuals}/chromecast-${width}-${scheme}.png`, fullPage: true });
  }
  await panel.getByRole('button', { name: 'Return playback here' }).click();
  await expect(panel.getByRole('button', { name: 'Cast to Chromecast' })).toBeVisible();
  expect(requests).toContain('DELETE /api/chromecast/tickets/ticket-1');
  await page.evaluate(() => Reflect.set(window, '__failCastLoad', true));
  await panel.getByRole('button', { name: 'Cast to Chromecast' }).click();
  await expect(panel.getByRole('alert')).toContainText('Browser playback is still available');
  expect(await page.evaluate(() => Reflect.get(window, '__castActions'))).toEqual(['load', 'pause', 'play', 'seek', 'stop', 'load']);
});

test('member connects listening export, reconciles an uncertain outcome and disconnects', async ({ page }) => {
  await mockApi(page, {});
  let connected = false, sent = false;
  await page.route((url) => url.pathname.startsWith('/api/me/listening-export'), async (route) => {
    if (route.request().method() === 'PUT') connected = true;
    if (route.request().method() === 'DELETE') connected = false;
    const account = { provider: 'listenbrainz', generation: 'fixture-account', enabled: connected, username: connected ? 'listener' : null, credentials_set: connected, authorization_pending: false };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(route.request().method() === 'GET' ? { items: [account] } : account) });
  });
  await page.route((url) => url.pathname.startsWith('/api/me/listening-events'), async (route) => {
    if (route.request().method() === 'POST') sent = true;
    const event = { id: 'play', started_at: 900, deliveries: { listenbrainz: { status: sent ? 'sent' : 'uncertain' } } };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(route.request().method() === 'GET' ? { items: [event], page: 1 } : event) });
  });
  await page.goto('/settings/playback?page=listening-export'); await signIn(page);
  await page.getByLabel('ListenBrainz user token').fill('fixture-token');
  await page.getByRole('button', { name: 'Connect ListenBrainz' }).click();
  await expect(page.getByText('Connected as listener')).toBeVisible();
  await expect(page.getByLabel('ListenBrainz user token')).toHaveValue('');
  await page.getByRole('button', { name: 'Check provider history' }).click();
  await expect(page.getByText('ListenBrainz: sent')).toBeVisible();
  mkdirSync(visuals, { recursive: true });
  for (const scheme of ['light', 'dark'] as const) for (const width of [1440, 320]) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' }); await page.setViewportSize({ width, height: 900 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect((await new AxeBuilder({ page }).include('[data-setting-id="playback.listening-export"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
    await page.screenshot({ path: `${visuals}/listening-export-${width}-${scheme}.png`, fullPage: true });
  }
  await page.getByRole('button', { name: 'Disconnect ListenBrainz' }).click();
  await expect(page.getByText('Connected as listener')).toHaveCount(0);
});
