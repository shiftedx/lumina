import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** The member's server-side watch queue in the Watch rail. */

type Entry = { id: string; availability: 'available' | 'unavailable'; ref: Record<string, string | null>; title: string | null; uploader: string | null; artwork_url: string | null; duration: number | null };

const LONG = 'An unreasonably long queued title that keeps going to prove the rail clamps it neatly to two lines';

const remote = (n: number, title: string): Entry => ({
  id: `q${n}`, availability: 'available', ref: { kind: 'remote', library_item_id: null, provider: 'youtube', remote_id: `r${n}`, url: `https://example.test/queued/${n}` },
  title, uploader: 'Queue fixture channel', artwork_url: null, duration: 240 + n,
});

/** A tiny stateful stand-in for /api/me/watch-queue that survives reloads within one test. */
async function mockQueue(page: Page) {
  const server = {
    revision: 4,
    entries: [
      remote(1, 'Evening walk through the old harbour'),
      remote(2, LONG),
      { id: 'q3', availability: 'unavailable', ref: { kind: 'library', library_item_id: null, provider: null, remote_id: null, url: null }, title: null, uploader: null, artwork_url: null, duration: null } as Entry,
    ],
    deleted: [] as string[],
  };
  const body = () => ({ revision: server.revision, limit: 500, entries: server.entries.map((entry, position) => ({ ...entry, position })) });
  await page.route((url) => url.pathname.startsWith('/api/me/watch-queue'), async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const id = path.split('/').pop() as string;
    if (request.method() === 'PATCH') {
      const { position, expected_revision } = request.postDataJSON() as { position: number; expected_revision: number };
      if (expected_revision !== server.revision) return route.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"conflict"}' });
      const entry = server.entries.find((candidate) => candidate.id === id) as Entry;
      server.entries = server.entries.filter((candidate) => candidate !== entry);
      server.entries.splice(position, 0, entry);
      server.revision += 1;
    } else if (request.method() === 'DELETE') {
      server.deleted.push(id);
      server.entries = server.entries.filter((candidate) => candidate.id !== id);
      server.revision += 1;
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body()) });
  });
  return server;
}

async function openWatch(page: Page, viewport: { width: number; height: number }) {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  const server = await mockQueue(page);
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await expect(page.getByRole('heading', { name: /Your queue/ })).toBeVisible();
  return server;
}

const rowTitles = (page: Page) => page.locator('.watch-queue-item strong').allTextContents();

test('test_queue_keyboard_reorder_survives_reload', async ({ page }) => {
  await openWatch(page, { width: 1536, height: 960 });
  const down = page.getByRole('button', { name: 'Move Evening walk through the old harbour down' });
  await down.focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => rowTitles(page)).toEqual([LONG, 'Evening walk through the old harbour', 'Unavailable video']);
  await expect(down).toBeFocused();
  await page.reload();
  await expect(page.getByRole('heading', { name: /Your queue/ })).toBeVisible();
  await expect.poll(() => rowTitles(page)).toEqual([LONG, 'Evening walk through the old harbour', 'Unavailable video']);
  await expect(page.getByRole('button', { name: 'Unavailable video, no longer shared with you' })).toBeDisabled();
});

test('test_queue_autoplays_next_when_current_ends', async ({ page }) => {
  const server = await openWatch(page, { width: 1536, height: 960 });
  await expect(page.getByText('Plays next', { exact: true })).toBeVisible();
  await page.locator('video').first().evaluate((media: HTMLVideoElement) => media.dispatchEvent(new Event('ended')));
  await expect.poll(() => server.deleted).toEqual(['q1']);
  await expect(page).toHaveURL(/\/watch\?url=https%3A%2F%2Fexample\.test%2Fqueued%2F1/);
});
