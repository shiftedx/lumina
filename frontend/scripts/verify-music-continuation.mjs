import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';

const fixture = JSON.parse(await readFile(process.argv[2], 'utf8'));
const origin = new URL(fixture.serverURL);
assert(origin.protocol === 'http:' && origin.hostname === '127.0.0.1' && fixture.musicHomeWaitRequestID, 'Use a disposable #343 server');
const uuid = (id) => id.replace(/^(\w{8})(\w{4})(\w{4})(\w{4})(\w{12})$/, '$1-$2-$3-$4-$5');
const browser = await chromium.launch({ channel: 'chrome', args: ['--disable-audio-output'] });
try {
  const first = await browser.newContext({ baseURL: origin.origin });
  const second = await browser.newContext({ baseURL: origin.origin });
  async function login(context) {
    const response = await context.request.post('/api/session/login', { data: { username: fixture.username, password: fixture.password } });
    assert(response.ok()); return (await response.json()).csrf_token;
  }
  const csrf = await login(first), otherCsrf = await login(second);
  const headers = { 'X-CSRF-Token': csrf, Origin: origin.origin };
  const otherHeaders = { 'X-CSRF-Token': otherCsrf, Origin: origin.origin };
  await first.request.post('/api/admin/onboarding/media/complete', { headers });
  const read = async (context) => (await context.request.get('/api/me/music-session')).json();
  const saved = await read(first), item = uuid(fixture.audioTrackID);
  const initial = await first.request.put('/api/me/music-session', { headers, data: { base_revision: saved.revision, item_ids: [item], current_index: 0, position_seconds: 23, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, entries: [{ kind: 'track', item_id: item }], entry_index: 0, unavailable_policy: 'skip' } });
  assert(initial.ok());
  const page = await first.newPage();
  await page.goto('/audio?pane=home');
  await page.getByRole('button', { name: `Resume ${fixture.audioTrackTitle}` }).click();
  await page.getByRole('button', { name: /^Open listening room for / }).click();
  await page.getByRole('checkbox', { name: 'Continue after queue ends' }).check();
  await page.getByRole('button', { name: 'Close listening room' }).click();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  let started;
  const requested = new Promise((resolve) => { started = resolve; });
  await page.route('**/api/music/contextual-mix?*', async (route) => { started(); await held; await route.continue(); });
  const seek = page.getByRole('slider', { name: 'Track position' });
  await seek.fill('89');
  await Promise.race([requested, new Promise((_, reject) => setTimeout(() => reject(new Error('No continuation request')), 15_000))]);
  const winner = await read(second);
  const conflictWrite = await second.request.put('/api/me/music-session', { headers: otherHeaders, data: { base_revision: winner.revision, item_ids: winner.queue.map((track) => track.id), current_index: winner.current_index, position_seconds: winner.position_seconds, repeat: winner.repeat, shuffle: winner.shuffle, active_output: { kind: 'browser' }, entries: winner.entries.map((entry) => entry.kind === 'track' ? { kind: 'track', item_id: entry.item_id } : { kind: 'acquisition', request_id: entry.request_id }), entry_index: winner.entry_index, unavailable_policy: winner.unavailable_policy } });
  assert(conflictWrite.ok());
  release();
  await expect(page.getByText(/Queue conflict/)).toBeVisible({ timeout: 15_000 });
  const final = await read(second);
  assert.equal(final.revision, winner.revision + 1);
  assert.equal(final.queue.length, 1);
  console.log('PASS real Chrome natural end, bounded preview and second-browser revision conflict');
} finally { await browser.close(); }
