import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from '@playwright/test';

const [fixturePath] = process.argv.slice(2);
assert(fixturePath, 'Usage: node frontend/scripts/verify-music-home.mjs Fixture.json');
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'));
const origin = new URL(fixture.serverURL);
assert(origin.protocol === 'http:' && origin.hostname === '127.0.0.1' && fixture.musicHomeWaitRequestID,
  'Use the disposable #343 loopback proof server');

const expect = baseExpect.configure({ timeout: 15_000 });
const route = '/audio?pane=home';
const layoutPath = '/api/me/music-home/layout';
const titleId = (id) => id.replace(/^(\w{8})(\w{4})(\w{4})(\w{4})(\w{12})$/, '$1-$2-$3-$4-$5');

async function login(context, username, password) {
  const response = await context.request.post('/api/session/login', { data: { username, password } });
  assert(response.ok(), `Fixture sign-in returned HTTP ${response.status()}`);
  return (await response.json()).csrf_token;
}

async function home(page) {
  await page.goto(route);
  await expect(page.getByRole('heading', { name: 'Music Home', exact: true })).toBeVisible();
}

async function changeLayout(page, action) {
  const saved = page.waitForResponse((response) =>
    new URL(response.url()).pathname === layoutPath && response.request().method() === 'PUT');
  await action();
  const response = await saved;
  assert(response.ok(), `Layout save returned HTTP ${response.status()}`);
  return response.json();
}

function widthOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

const browser = await chromium.launch({ channel: 'chrome', args: ['--disable-audio-output'] });
try {
  const desktop = await browser.newContext({ baseURL: origin.origin, viewport: { width: 1440, height: 900 } });
  const phone = await browser.newContext({ baseURL: origin.origin, viewport: { width: 390, height: 844 } });
  const csrf = await login(desktop, fixture.username, fixture.password);
  const setup = await desktop.request.post('/api/admin/onboarding/media/complete', {
    headers: { 'X-CSRF-Token': csrf, Origin: origin.origin },
  });
  assert(setup.ok(), `Fixture media setup returned HTTP ${setup.status()}`);
  await login(phone, fixture.musicHomeMemberUsername, fixture.musicHomeMemberPassword);
  const owner = await desktop.newPage();
  const member = await phone.newPage();

  await home(owner);
  await home(member);
  assert((await widthOverflow(owner)) <= 1 && (await widthOverflow(member)) <= 1);
  assert.equal(new URL(owner.url()).pathname, '/audio');

  const edit = owner.getByRole('button', { name: 'Edit Music Home', exact: true });
  await edit.press('Enter');
  const earlier = owner.getByRole('button', { name: 'Move Downloads earlier', exact: true });
  const edited = await changeLayout(owner, () => earlier.press('Enter'));
  assert.deepEqual(edited.order, ['resume', 'recent', 'downloads', 'playlists', 'mixes']);
  await expect(earlier).toBeFocused();
  const hidden = await changeLayout(owner, () => owner.getByRole('button', { name: 'Hide Mixes', exact: true }).click());
  assert.deepEqual(hidden.hidden, ['mixes']);
  await owner.getByRole('button', { name: 'Done', exact: true }).press('Enter');
  await expect(edit).toBeFocused();
  await owner.reload();
  await expect(owner.getByRole('region', { name: 'Downloads', exact: true })).toBeVisible();
  await expect(owner.getByRole('region', { name: 'Mixes', exact: true })).toHaveCount(0);
  await member.reload();
  await expect(member.getByRole('region', { name: 'Mixes', exact: true })).toBeVisible();
  await edit.click();
  const reset = await changeLayout(owner, () => owner.getByRole('button', { name: 'Reset Music Home', exact: true }).click());
  assert.deepEqual(reset, { order: ['resume', 'recent', 'playlists', 'downloads', 'mixes'], hidden: [] });

  await home(owner);
  const resume = owner.getByRole('button', { name: `Resume ${fixture.audioTrackTitle}`, exact: true });
  await expect(resume).toBeEnabled();
  await expect(owner.getByRole('region', { name: 'Resume', exact: true })).toContainText('0:23');
  const pause = owner.getByRole('button', { name: 'Pause music', exact: true });
  await expect(pause).toHaveCount(0);
  await resume.press('Enter');
  await expect(pause).toBeVisible();

  const savedSession = () => desktop.request.get('/api/me/music-session').then((response) => response.json());
  const saved = await savedSession();
  assert(saved.position_seconds >= 23 && saved.unavailable_policy === 'wait');
  assert.equal(saved.entries[0].item_id, saved.entries[1].item_id);
  assert.equal(saved.entries[2].request_id, fixture.musicHomeWaitRequestID);

  const browse = owner.getByRole('button', { name: 'Browse music', exact: true });
  await browse.click();
  await expect(pause).toBeVisible();

  await owner.goto(`/audio/title/${titleId(fixture.audioAlbumID)}`);
  await expect(owner.getByRole('heading', { name: fixture.audioAlbumTitle, exact: true })).toBeVisible();
  await owner.goto(`/audio/title/${titleId(fixture.audioArtistID)}`);
  await expect(owner.getByRole('heading', { name: 'The Synthetic Lantern Ensemble', exact: true })).toBeVisible();
  await owner.goto('/audio?view=artists');
  await expect(owner.getByRole('button', { name: 'Artists', exact: true })).toHaveAttribute('aria-pressed', 'true');

  const preference = owner.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/settings/me' && response.request().method() === 'PUT');
  await owner.getByRole('button', { name: 'Albums', exact: true }).click();
  const albums = owner.getByRole('heading', { name: 'All albums', exact: true });
  await expect(albums).toBeVisible();
  const settings = await preference;
  assert(settings.ok());
  assert.equal((await settings.json()).ui_prefs.music_browse.view, 'albums');
  await home(owner);
  await browse.click();
  await expect(albums).toBeVisible();

  // Save through the real player, then read device-local storage via Home.
  const expand = owner.getByRole('button', { name: `Expand music player for ${fixture.audioTrackTitle}`, exact: true });
  const openRoom = owner.getByRole('button', { name: `Open listening room for ${fixture.audioTrackTitle}`, exact: true });
  await expect(expand.or(openRoom)).toBeVisible();
  if (await expand.isVisible()) await expand.click();
  await openRoom.click();
  const room = owner.getByRole('dialog', { name: 'Listening room', exact: true });
  await room.getByRole('button', { name: 'Save track', exact: true }).click();
  await expect(room.getByRole('button', { name: 'Track available offline', exact: true })).toBeVisible();
  await room.getByRole('button', { name: 'Close listening room', exact: true }).click();
  await home(owner);
  const downloads = owner.getByRole('region', { name: 'Downloads', exact: true });
  await expect(downloads).toContainText('1 track is saved on this device.');
  await expect(downloads.getByRole('link', { name: 'Open downloads' })).toHaveAttribute('href', '/music-offline.html');
  await home(member);
  await expect(member.getByRole('region', { name: 'Downloads', exact: true })).not.toContainText('1 track is saved');
  console.log('#347 PASS desktop/phone Home, keyboard layout, member isolation/reset, explicit saved Resume, persistent player, legacy routes, device downloads');
} finally {
  await browser.close();
}
