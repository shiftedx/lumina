/**
 * Mocked API for the music pages and persistent player: one album of three tracks by one
 * artist, the artist, a deterministic music queue, and both the current music and legacy Library media endpoints.
 * Register after mockApi and mockGalleryWall (which answers /api/art).
 */
import type { Page } from '@playwright/test';

import { albumDetail, albumSummary, albumTrack, artistSummary, FIXTURE_AT, stillItem, titleDetail } from '../src/test/galleryFixtures';
import { user } from './lumina-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';
import type { MusicSharedSnapshot, MusicSharedWrite } from '../src/features/music/musicSharedSession';

export const musicTracks = [1, 2, 3].map((number) => albumTrack(number, { duration_seconds: 90 }));
export const musicAlbum = albumDetail(albumSummary('album-1', { child_count: 3 }), musicTracks);
export const musicArtist = titleDetail(artistSummary('artist-1', { child_count: 1 }), { logo: null, children: [albumSummary('album-1', { child_count: 3 })] });

const WAV_SECONDS = 30;
const WAV_SAMPLE_RATE = 8_000;
const musicWav = (() => {
  const samples = WAV_SAMPLE_RATE * WAV_SECONDS;
  const dataBytes = samples * 2;
  const wav = Buffer.alloc(44 + dataBytes);
  wav.write('RIFF', 0); wav.writeUInt32LE(36 + dataBytes, 4); wav.write('WAVE', 8);
  wav.write('fmt ', 12); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(WAV_SAMPLE_RATE, 24); wav.writeUInt32LE(WAV_SAMPLE_RATE * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(dataBytes, 40);
  for (let sample = 0; sample < samples; sample += 1) wav.writeInt16LE(Math.round(Math.sin(2 * Math.PI * 220 * sample / WAV_SAMPLE_RATE) * 2_048), 44 + sample * 2);
  return wav;
})();

const queueTrack = (number: number) => ({
  id: `track-${number}`,
  title: `Song ${number}`,
  artist: 'Artist A',
  album_artist: 'Artist A',
  album: 'Album One',
  album_id: 'album-1',
  artist_id: 'artist-1',
  number,
  disc: 1,
  duration: WAV_SECONDS,
  artwork_url: null,
  audio_url: `/api/music/tracks/track-${number}/audio`,
  codec: 'pcm_s16le',
  sample_rate: WAV_SAMPLE_RATE,
  bit_depth: 16,
  channels: 1,
  gain_db: null,
  genres: ['Electronic'],
  edited: false,
});

function trackItem(id: string) {
  const track = musicTracks.find((entry) => entry.item_id === id);
  return track ? stillItem(id, { kind: 'track', title: `0${track.number} ${track.name}.flac`, title_id: 'album-1', uploader: 'Artist A', playlist_name: 'Album One', duration: 90, extractor: 'external_library' }) : null;
}

export async function mockMusic(page: Page): Promise<string[]> {
  const calls: string[] = [];
  let shared: MusicSharedSnapshot = { revision: 0, queue: [], current_index: null, position_seconds: 0, repeat: 'off', shuffle: false,
    active_output: { kind: 'browser' }, updated_at: null, removed_count: 0, entries: [], entry_index: null, unavailable_policy: 'skip' };
  await page.route((url) => url.pathname === '/api/me/music-session', async (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as MusicSharedWrite;
      if (body.base_revision !== shared.revision) return route.fulfill({ status: 409, contentType: 'application/json',
        body: JSON.stringify({ detail: { code: 'music_session_conflict', session: shared } }) });
      const queue = body.item_ids.flatMap((id) => {
        const number = Number(/^track-(\d+)$/.exec(id)?.[1]);
        return number >= 1 && number <= 3 ? [queueTrack(number)] : [];
      });
      shared = { ...shared, revision: shared.revision + 1, queue, current_index: queue.length ? body.current_index ?? 0 : null,
        position_seconds: body.position_seconds, repeat: body.repeat, shuffle: body.shuffle,
        entries: queue.map((track) => ({ kind: 'track', item_id: track.id, state: 'playable', tracks: [track] })),
        entry_index: queue.length ? body.current_index ?? 0 : null, unavailable_policy: body.unavailable_policy ?? 'skip' };
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(shared) });
  });
  await page.route((url) => url.pathname.startsWith('/api/music/'), async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    calls.push(`${method} ${path}`);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/music/queue') {
      const body = request.postDataJSON() as { item_ids?: string[]; title_id?: string };
      const wanted = body.item_ids?.length ? new Set(body.item_ids) : null;
      return json([1, 2, 3].map(queueTrack).filter((track) => !wanted || wanted.has(track.id)));
    }
    const track = /^\/api\/music\/tracks\/(track-\d+)(?:\/(audio|lyrics|videos))?$/.exec(path);
    if (track) {
      const number = Number(track[1].split('-')[1]);
      if (number < 1 || number > 3) return json({ detail: 'Not found' }, 404);
      if (!track[2]) return json(queueTrack(number));
      if (track[2] === 'audio') return route.fulfill({ status: 200, contentType: 'audio/wav', body: musicWav });
      if (track[2] === 'lyrics') return json({ plain: null, cues: [], source: null, instrumental: true });
      return json({ items: [], refreshing: false, error: null });
    }
    if (/^\/api\/music\/titles\/(album-1|artist-1)\/videos$/.test(path)) return json({ items: [], refreshing: false, error: null });
    if (/^\/api\/music\/titles\/(album-1|artist-1)\/enrichment$/.test(path)) return json({ refreshing: false, identified: true, error: null, candidates: [] });
    if (/^\/api\/music\/titles\/(album-1|artist-1)\/editions$/.test(path)) return json({ owned: [], items: [], next_offset: null, refreshing: false, error: null });
    if (/^\/api\/music\/titles\/(album-1|artist-1)\/discography$/.test(path)) return json({ items: [], next_offset: null, refreshing: false, error: null });
    if (/^\/api\/music\/titles\/(album-1|artist-1)\/tracks$/.test(path)) return json([]);
    if (path === '/api/music/sonos') return json([]);
    return json({ detail: 'Not found' }, 404);
  });
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
