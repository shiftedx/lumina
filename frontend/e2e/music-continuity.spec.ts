import { expect, test, type Page } from '@playwright/test';

import { mockGalleryWall } from './gallery-mock';
import { mockApi, signIn } from './lumina-mock';
import { mockMusic } from './music-mock';

const requestId = '11111111-1111-4111-8111-111111111111';
const track = {
  id: 'track-1', title: 'Song 1', artist: 'Artist A', album_artist: 'Artist A', album: 'Requested album',
  album_id: 'album-1', artist_id: 'artist-1', number: 1, disc: 1, duration: 30, artwork_url: null,
  audio_url: '/api/music/tracks/track-1/audio', codec: 'pcm_s16le', sample_rate: 8000, bit_depth: 16,
  channels: 1, gain_db: null, genres: ['Electronic'], edited: false,
};

async function mockRequestQueue(page: Page) {
  let imported = false;
  const snapshot = () => imported
    ? { revision: 2, queue: [track], current_index: 0, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0,
      entries: [{ kind: 'acquisition', request_id: requestId, state: 'playable', name: 'Requested album', tracks: [track] }], entry_index: 0, unavailable_policy: 'wait' }
    : { revision: 1, queue: [], current_index: null, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0,
      entries: [{ kind: 'acquisition', request_id: requestId, state: 'awaiting_import', name: 'Requested album', message: 'Waiting for library import' }], entry_index: 0, unavailable_policy: 'wait' };
  await page.route('**/api/me/music-session', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(snapshot()) }));
  return { importAlbum: () => { imported = true; } };
}

test('a requested album remains paused until the member explicitly plays it after import', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false });
  await mockGalleryWall(page);
  const musicCalls = await mockMusic(page);
  const request = await mockRequestQueue(page);
  await page.goto('/audio/title/album-1');
  await signIn(page);

  const player = page.getByRole('region', { name: 'Music player', exact: true });
  await expect(player.getByText('Waiting for requested album')).toBeVisible();
  await expect(player.getByRole('button', { name: 'Play music' })).toBeDisabled();
  await player.getByRole('button', { name: 'Open queue' }).click();
  const room = page.getByRole('dialog', { name: 'Your queue' });
  await expect(room.getByRole('region', { name: 'Unavailable queue entries' })).toContainText('Requested album · Waiting for library import');
  await expect(room.getByRole('combobox', { name: 'Unavailable queue policy' })).toHaveValue('wait');

  request.importAlbum();
  await room.getByRole('button', { name: 'Refresh requested albums' }).click();
  await expect(player).toBeHidden();
  await expect(room.getByText('Requested albums refreshed. Choose Play when you are ready.')).toBeVisible();
  await room.getByRole('button', { name: /Song 1/ }).click();
  await expect.poll(() => musicCalls.includes('GET /api/music/tracks/track-1/audio')).toBe(true);
});
