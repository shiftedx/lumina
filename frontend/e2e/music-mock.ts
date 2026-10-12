/**
 * Mocked API for the music pages and the audio stage: one album of three tracks by one
 * artist, the artist, and the tracks' media and playback endpoints (a 1.5 s synthetic file). Register
 * after mockApi and mockGalleryWall (which answers /api/art).
 */
import type { Page } from '@playwright/test';

import { albumDetail, albumSummary, albumTrack, artistSummary, FIXTURE_AT, stillItem, titleDetail } from '../src/test/galleryFixtures';
import { user } from './lumina-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

export const musicTracks = [1, 2, 3].map((number) => albumTrack(number, { duration_seconds: 90 }));
export const musicAlbum = albumDetail(albumSummary('album-1', { child_count: 3 }), musicTracks);
export const musicArtist = titleDetail(artistSummary('artist-1', { child_count: 1 }), { logo: null, children: [albumSummary('album-1', { child_count: 3 })] });

function trackItem(id: string) {
  const track = musicTracks.find((entry) => entry.item_id === id);
  return track ? stillItem(id, { kind: 'track', title: `0${track.number} ${track.name}.flac`, title_id: 'album-1', uploader: 'Artist A', playlist_name: 'Album One', duration: 90, extractor: 'external_library' }) : null;
}

export async function mockMusic(page: Page): Promise<string[]> {
  const calls: string[] = [];
  await page.route((url) => /^\/api\/titles\/(album-1|artist-1)(\/.*)?$/.test(url.pathname) || /^\/api\/library\/track-\d+(\/.*)?$/.test(url.pathname) || url.pathname.startsWith('/api/me/favorites/'), async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    calls.push(`${method} ${path}`);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.startsWith('/api/me/favorites/')) return route.fulfill({ status: 204 });
    if (path === '/api/titles/album-1') return json(musicAlbum);
    if (path === '/api/titles/artist-1') return json(musicArtist);
    if (path.startsWith('/api/titles/')) return json({ detail: 'Not found' }, 404);
    const [, id, part] = /^\/api\/library\/(track-\d+)(?:\/(.+))?$/.exec(path) ?? [];
    const item = trackItem(id);
    if (!item) return json({ detail: 'Not found' }, 404);
    if (!part) return json(item);
    if (part === 'media') return route.fulfill({ status: 200, contentType: SYNTHETIC_MEDIA_TYPE, body: Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64') });
    if (part === 'playback') {
      return method === 'PUT'
        ? json({ id: `pb-${id}`, user_id: user.id, item_id: id, ...(request.postDataJSON() as object), last_watched_at: FIXTURE_AT, created_at: FIXTURE_AT, updated_at: FIXTURE_AT, item })
        : json(null);
    }
    if (part === 'playback-options') return json({ mode: 'direct', reason: null, facts: { container: 'webm', video_codec: 'vp9', audio_codec: 'opus', width: 160, height: 90, duration: 1.5 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null });
    if (part === 'segments') return json({ item_id: id, segments: [] });
    if (part === 'artwork') return route.fulfill({ status: 404, body: '' });
    if (['subtitle-tracks', 'mute-ranges', 'tags', 'notes', 'transcripts'].includes(part)) return json([]);
    return json({ detail: 'Not found' }, 404); // provenance, summary: the panels' own empty or error states
  });
  return calls;
}
