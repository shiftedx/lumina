import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';

const fixture = JSON.parse(await readFile(process.argv[2], 'utf8'));
const origin = new URL(fixture.serverURL);
assert(origin.protocol === 'http:' && origin.hostname === '127.0.0.1' && fixture.musicHomeWaitRequestID, 'Use a disposable #343 server');
const titleId = (id) => id.replace(/^(\w{8})(\w{4})(\w{4})(\w{4})(\w{12})$/, '$1-$2-$3-$4-$5');
const browser = await chromium.launch({ channel: 'chrome', args: ['--disable-audio-output'] });
try {
  for (const viewport of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
    const context = await browser.newContext({ baseURL: origin.origin, viewport });
    const login = await context.request.post('/api/session/login', { data: { username: fixture.username, password: fixture.password } });
    assert(login.ok());
    const csrf = (await login.json()).csrf_token;
    await context.request.post('/api/admin/onboarding/media/complete', { headers: { 'X-CSRF-Token': csrf, Origin: origin.origin } });
    const session = async () => (await context.request.get('/api/me/music-session')).json();
    const page = await context.newPage();
    await page.goto(`/audio/title/${titleId(fixture.musicHomeMultidiscAlbumID)}`);
    const instant = page.getByRole('button', { name: /^Instant Mix for / });
    await expect(instant).toBeVisible();
    const before = await session();
    await instant.press('Enter');
    const dialog = page.getByRole('dialog', { name: /^Instant Mix · / });
    await expect(dialog.getByText('From this album').first()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    const after = await session();
    assert.equal(after.revision, before.revision);
    assert.deepEqual(after.entries, before.entries);
    assert((await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)) <= 1);
    const radio = page.getByRole('button', { name: /^Track Radio for / }).first();
    await radio.click();
    await expect(page.getByRole('dialog', { name: /^Track Radio · / })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: /^Track Radio · / })).toHaveCount(0);
    if (viewport.width > 500) {
      await instant.click();
      await expect(dialog.getByText('From this album').first()).toBeVisible();
      await dialog.getByRole('button', { name: 'Save as Playlist' }).click();
      await expect(dialog.getByText('Playlist saved.')).toBeVisible();
      assert.equal((await session()).revision, before.revision);
      await dialog.getByRole('button', { name: 'Add to Queue' }).click();
      await expect(dialog).toHaveCount(0);
      const appended = await session();
      assert(appended.revision > before.revision);
      assert.equal(appended.entry_index, before.entry_index);
      assert.equal(appended.position_seconds, before.position_seconds);
      assert.equal(appended.entries[2].request_id, fixture.musicHomeWaitRequestID);
      assert(appended.entries.slice(3).some((entry) => entry.provenance?.source === 'contextual_mix'));
      await instant.click();
      await dialog.getByRole('button', { name: 'Play Mix' }).click();
      const confirm = page.getByRole('dialog', { name: 'Replace queue?' });
      await expect(confirm).toContainText('waiting request');
      await confirm.getByRole('button', { name: 'Play Mix' }).click();
      await expect(dialog).toHaveCount(0);
      await expect.poll(async () => (await session()).entries.some((entry) => entry.request_id === fixture.musicHomeWaitRequestID)).toBe(false);
      const replaced = await session();
      assert(replaced.entries.every((entry) => entry.provenance?.source === 'contextual_mix'));
      await expect(page.getByRole('button', { name: 'Pause music' }).first()).toBeVisible();
      await page.getByRole('button', { name: /^Open listening room for / }).click();
      const room = page.getByRole('dialog', { name: 'Listening room' });
      await room.getByRole('button', { name: /^Track Radio for / }).click();
      await expect(page.getByRole('dialog', { name: /^Track Radio · / })).toBeVisible();
      await page.keyboard.press('Escape');
    }
    await page.goto(`/audio/title/${titleId(fixture.audioArtistID)}`);
    await expect(page.getByRole('button', { name: /^Instant Mix for / })).toBeVisible();
    await page.getByRole('button', { name: /^Instant Mix for / }).click();
    await expect(page.getByRole('dialog', { name: /^Instant Mix · / }).getByText('Same artist').first()).toBeVisible();
    await page.keyboard.press('Escape');
    await context.close();
  }
  console.log('PASS desktop and phone preview, keyboard dismissal, queue preservation and append behind Wait');
} finally { await browser.close(); }
