import { expect, test } from '@playwright/test';

import { SYNTHETIC_MEDIA_BASE64 } from './synthetic-media';

const OWNER_A = 'offline-owner-a';
const OWNER_B = 'offline-owner-b';
const PRODUCTION_URL = process.env.MUSIC_OFFLINE_PRODUCTION_URL?.replace(/\/$/, '');
async function waitForShell(page: import('@playwright/test').Page) {
  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    const worker = registration.active;
    if (!worker) throw new Error('offline worker did not activate');
    await new Promise<void>((resolve, reject) => {
      const channel = new MessageChannel();
      const timeout = window.setTimeout(() => reject(new Error('offline shell did not become ready')), 20_000);
      channel.port1.onmessage = (event) => {
        window.clearTimeout(timeout);
        channel.port1.close();
        if (event.data?.type === 'OFFLINE_SHELL_READY') resolve();
        else reject(new Error(event.data?.message || 'offline shell failed'));
      };
      worker.postMessage({ type: 'CACHE_OFFLINE_SHELL' }, [channel.port2]);
    });
  });
}

async function seedOfflineTracks(page: import('@playwright/test').Page, longFirst = false) {
  await page.evaluate(async ({ audioBase64, longFirst }) => {
    const bytes = Uint8Array.from(atob(audioBase64), (value) => value.charCodeAt(0));
    const open = indexedDB.open('lumina-music-offline', 1);
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      open.onupgradeneeded = () => {
        const store = open.result.createObjectStore('tracks', { keyPath: 'key' });
        store.createIndex('owner', 'owner');
      };
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    });
    const save = (owner: string, id: string, title: string, duration = 1.5) => new Promise<void>((resolve, reject) => {
      const transaction = database.transaction('tracks', 'readwrite');
      transaction.objectStore('tracks').put({
        key: `${owner}:${id}`,
        owner,
        track: {
          id, title, artist: 'Offline Artist', album_artist: 'Offline Artist', album: 'Offline Album', album_id: null,
          artist_id: null, number: 1, disc: 1, duration, artwork_url: null, audio_url: `/api/music/tracks/${id}/audio`,
          codec: 'opus', sample_rate: 48_000, bit_depth: null, channels: 1, gain_db: null, genres: [], edited: false,
        },
        audio: bytes.buffer.slice(0),
        artwork: null,
        lyrics: { plain: `${title} cached lyrics`, cues: [{ start: 0, text: `${title} cached lyrics` }], source: 'Saved with track', instrumental: false },
        savedAt: Date.now(),
      });
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
    await save('offline-owner-a', 'track-a', 'Offline One', longFirst ? 60 * 60 : 1.5);
    await save('offline-owner-b', 'track-b', 'Offline Two');
    localStorage.setItem('lumina:music:offline-owner', 'offline-owner-a');
    database.close();
  }, { audioBase64: SYNTHETIC_MEDIA_BASE64, longFirst });
}

test('reloads the standalone offline entry, plays saved audio, and never shows another owner', async ({ page, context }) => {
  test.skip(Boolean(PRODUCTION_URL), 'the production run exercises the built entry below');
  await page.goto('/music-offline.html');
  await expect(page.getByRole('heading', { name: 'Offline listening' })).toBeVisible();
  await waitForShell(page);
  await seedOfflineTracks(page, true);

  await page.reload();
  await expect(page.getByRole('button', { name: 'Play Offline One' })).toBeVisible();
  await expect(page.getByText('Offline Two', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Cached lyrics' })).toBeVisible();
  await expect(page.getByText('Offline One cached lyrics', { exact: true })).toBeVisible();

  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
  await context.setOffline(true);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Offline listening' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play Offline One' })).toBeVisible();
  await expect(page.getByText('Offline Two', { exact: true })).toHaveCount(0);

  await page.getByRole('button', { name: 'Play Offline One' }).click();
  await expect(page.getByRole('button', { name: 'Pause music' })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/Streaming saved source/)).toBeVisible();
  const position = page.getByRole('slider', { name: 'Track position' });
  await expect(position).toBeEnabled();
  await position.focus();
  await page.keyboard.press('ArrowRight');

  await page.getByRole('button', { name: 'Remove Offline One' }).click();
  await expect(page.getByRole('button', { name: 'Play Offline One' })).toHaveCount(0);
  await expect(page.getByText('Offline Two', { exact: true })).toHaveCount(0);
});

test('built shell recursively caches hashed dependencies and remains usable with the server disconnected', async ({ page, context }) => {
  test.skip(!PRODUCTION_URL, 'set MUSIC_OFFLINE_PRODUCTION_URL to a built preview server');
  await page.goto(`${PRODUCTION_URL}/music-offline.html`);
  await expect(page.getByRole('heading', { name: 'Offline listening' })).toBeVisible();
  await waitForShell(page);
  await seedOfflineTracks(page);

  const resources = await page.evaluate(async () => {
    const meta = await caches.open('lumina-offline-shell-v1-meta');
    const ready = await meta.match('/__lumina_offline_shell_ready__');
    if (!ready) throw new Error('offline shell metadata is missing');
    const value = await ready.json() as { cacheName?: string };
    if (!value.cacheName) throw new Error('offline shell cache name is missing');
    const requests = await (await caches.open(value.cacheName)).keys();
    return requests.map((request) => new URL(request.url).pathname + new URL(request.url).search).sort();
  });
  expect(resources).toContain('/music-offline.html');
  expect(resources.some((url) => /^\/assets\/[^/]+-[A-Za-z0-9_-]+\.js$/.test(url))).toBe(true);
  expect(resources.some((url) => /^\/assets\/[^/]+-[A-Za-z0-9_-]+\.css$/.test(url))).toBe(true);
  expect(resources).toContain('/fonts/Newsreader-variable.woff2');
  expect(resources).toContain('/fonts/Newsreader-Italic-variable.woff2');
  expect(resources.some((url) => url.startsWith('/api/') || url.startsWith('/src/') || /(?:token|sig|cookie)=/i.test(url))).toBe(false);

  await page.reload();
  await expect(page.getByRole('button', { name: 'Play Offline One' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
  await context.setOffline(true);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Offline listening' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play Offline One' })).toBeVisible();
  await expect(page.getByText(/could not (?:finish preparing|be prepared)/i)).toHaveCount(0);

  await page.getByRole('button', { name: 'Play Offline One' }).click();
  await expect(page.getByRole('button', { name: 'Pause music' })).toBeVisible({ timeout: 15_000 });
  await page.getByRole('button', { name: 'Remove Offline One' }).click();
  await expect(page.getByRole('button', { name: 'Play Offline One' })).toHaveCount(0);
  await expect(page.getByText('Offline Two', { exact: true })).toHaveCount(0);
});
