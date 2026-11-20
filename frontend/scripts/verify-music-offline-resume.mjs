import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium, expect as baseExpect, request } from '@playwright/test';

const fixture = JSON.parse(await readFile(process.argv[2], 'utf8'));
const origin = new URL(fixture.serverURL);
assert(origin.protocol === 'http:' && origin.hostname === '127.0.0.1' && fixture.musicHomeWaitRequestID,
  'Use a fresh disposable #343 server');
const profile = await mkdtemp(join(tmpdir(), 'lumina-offline-resume-'));
const expect = baseExpect.configure({ timeout: 20_000 });
const app = '/audio?pane=home';
const options = { baseURL: origin.origin, channel: 'chrome', viewport: { width: 1440, height: 900 }, args: ['--disable-audio-output'] };
const api = await request.newContext({ baseURL: origin.origin });
const identity = { username: fixture.username, password: fixture.password };

async function login(context) {
  const response = await context.post('/api/session/login', { data: identity });
  assert(response.ok());
  return { 'X-CSRF-Token': (await response.json()).csrf_token, Origin: origin.origin };
}

let context;
try {
  const headers = await login(api);
  context = await chromium.launchPersistentContext(profile, options);
  const ownerHeaders = await login(context.request);
  assert((await context.request.post('/api/admin/onboarding/media/complete', { headers: ownerHeaders })).ok());
  let page = context.pages()[0];
  await page.goto(app);
  const resume = page.getByRole('button', { name: `Resume ${fixture.audioTrackTitle}`, exact: true });
  await expect(resume).toBeVisible();
  await resume.click();
  const roomButton = page.getByRole('button', { name: `Open listening room for ${fixture.audioTrackTitle}`, exact: true });
  await page.getByRole('region', { name: 'Music player', exact: true })
    .getByRole('button', { name: 'Pause music', exact: true }).waitFor();
  await roomButton.click();
  const room = page.getByRole('dialog', { name: 'Listening room', exact: true });
  await room.getByRole('button', { name: 'Save album', exact: true }).click();
  await expect(room.getByText('2 tracks are ready offline.', { exact: true })).toBeVisible();
  await expect(room.getByRole('button', { name: 'Track available offline', exact: true })).toBeVisible();
  await room.getByRole('button', { name: 'Pause music', exact: true }).click();
  await expect(room.getByRole('button', { name: 'Play music', exact: true })).toBeVisible();
  await room.getByRole('button', { name: 'Close listening room', exact: true }).click();
  await page.reload();
  const downloads = page.getByRole('region', { name: 'Downloads', exact: true });
  await expect(downloads).toContainText('2 tracks are saved on this device.');
  await page.getByRole('link', { name: 'Open downloads' }).click();
  await expect(page.getByRole('heading', { name: 'Offline listening' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  await context.close();

  context = await chromium.launchPersistentContext(profile, options);
  await context.setOffline(true);
  page = context.pages()[0];
  await page.goto('/music-offline.html');
  const saved = page.getByRole('region', { name: 'Resume saved queue' });
  await expect(saved).toContainText('Queue item 1');
  await expect(saved).toContainText(fixture.audioTrackTitle);
  await expect(page.getByRole('button', { name: 'Pause music' })).toHaveCount(0);
  await saved.getByRole('button', { name: 'Resume saved queue' }).click();
  const pause = page.getByRole('button', { name: 'Pause music' });
  await expect(pause).toBeVisible();
  const slider = page.getByRole('slider', { name: 'Track position' });
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(23);
  await slider.fill('10');
  await pause.click();
  await expect(page.getByRole('button', { name: 'Play music' })).toBeVisible();

  const before = await (await api.get('/api/me/music-session')).json();
  const edited = await api.put('/api/me/music-session', {
    headers,
    data: {
      base_revision: before.revision, item_ids: before.queue.map((track) => track.id),
      entries: before.entries.map((entry) => entry.kind === 'track' ? { kind: 'track', item_id: entry.item_id }
        : { kind: 'acquisition', request_id: entry.request_id }),
      entry_index: before.entry_index, current_index: before.current_index, position_seconds: 41,
      repeat: before.repeat, shuffle: before.shuffle, unavailable_policy: before.unavailable_policy,
      active_output: { kind: 'browser' },
    },
  });
  assert(edited.ok());
  await context.setOffline(false);
  await page.getByRole('link', { name: 'Back to Music' }).click();
  await expect.poll(() => page.url()).toMatch(/\/audio$/);
  const reconnectDock = page.getByRole('region', { name: 'Music player', exact: true });
  const reconnectExpand = page.getByRole('button', { name: `Expand music player for ${fixture.audioTrackTitle}`, exact: true });
  await expect(reconnectDock.or(reconnectExpand)).toBeVisible();
  if (await reconnectExpand.isVisible()) await reconnectExpand.click();
  await expect(reconnectDock.getByText('Queue conflict.', { exact: false })).toBeVisible();
  await reconnectDock.getByRole('button', { name: 'Keep this device' }).click();
  await expect.poll(async () => (await (await api.get('/api/me/music-session')).json()).position_seconds).toBeLessThan(20);
  const resolved = await (await api.get('/api/me/music-session')).json();
  assert.equal(resolved.entries[2].request_id, fixture.musicHomeWaitRequestID);
  assert.equal(resolved.entries[0].item_id, resolved.entries[1].item_id);
  console.log('#353 PASS Chrome process restart offline, explicit resume and seek, real revision conflict, Keep this device, duplicates and Wait');
} finally {
  await context?.close();
  await api.dispose();
  await rm(profile, { recursive: true, force: true });
}
