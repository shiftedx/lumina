import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MusicEngineState } from './musicEngine';
import type { MusicTrack } from './musicTypes';
const mocks = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('../../api', () => ({ ApiRequestError: class extends Error { status = 500; }, requestJson: mocks.request }));
import { ListeningTracker, recordMusicListening, refreshListeningExport, setListeningExportOwner, flushListeningExport } from './musicListeningExport';

const track = { id: 'track', duration: 120 } as MusicTrack;
const playback = (position: number, playing = true, index = 0): MusicEngineState => ({ index, position, playing, duration: 120, loading: false, error: null });

beforeEach(() => { setListeningExportOwner(null); localStorage.clear(); mocks.request.mockReset(); });

describe('eligible playback accounting', () => {
  it('counts listening time while excluding seeks, pause and intentional rewind jumps', () => {
    const tracker = new ListeningTracker('alice', () => 'play');
    tracker.observe(track, playback(0), 0);
    tracker.observe(track, playback(10), 10_000);
    tracker.observe(track, playback(90), 11_000);
    tracker.observe(track, playback(91, false), 12_000);
    tracker.observe(track, playback(91, false), 50_000);
    tracker.observe(track, playback(20), 51_000);
    tracker.observe(track, playback(25), 56_000);
    expect(tracker.current).toMatchObject({ id: 'play', listened_seconds: 16 });
  });
  it('preserves the logical play across restoration and gives a completed repeat a new identity', () => {
    let uuid = 0;
    const first = new ListeningTracker('alice', () => String(++uuid));
    first.observe(track, playback(50), 100_000);
    first.observe(track, playback(110), 160_000);
    const saved = first.snapshot();
    const restored = new ListeningTracker('alice', () => String(++uuid));
    restored.restore(saved.event!, saved.sample!);
    restored.observe(track, playback(110), 500_000);
    restored.observe(track, playback(120), 510_000);
    expect(restored.current).toMatchObject({ id: '1', listened_seconds: 70 });
    expect(restored.observe(track, playback(0), 511_000)[0]).toMatchObject({ id: '1', listened_seconds: 70 });
    expect(restored.current?.id).toBe('2');
  });
});

it('never records while off and retries one stored owner-bound event after a lost response', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(100_000);
  mocks.request.mockResolvedValueOnce({ items: [{ enabled: false }] });
  setListeningExportOwner('alice');
  await Promise.resolve(); await Promise.resolve();
  recordMusicListening('alice', track, playback(0));
  expect(JSON.parse(localStorage.getItem('lumina:music:listening-events:alice')!)).toEqual({ pending: [], current: null });
  expect(mocks.request).toHaveBeenCalledTimes(1);
  mocks.request.mockResolvedValueOnce({ items: [{ enabled: true, provider: 'listenbrainz', generation: 'generation' }] });
  await refreshListeningExport('alice');
  recordMusicListening('alice', track, playback(0));
  vi.mocked(Date.now).mockReturnValue(160_000);
  mocks.request.mockRejectedValueOnce(new Error('response lost'));
  recordMusicListening('alice', track, playback(60));
  await Promise.resolve(); await Promise.resolve();
  const mutation = mocks.request.mock.calls.find((call) => call[0] === '/api/me/listening-events')!;
  const sent = JSON.parse(mutation[1].body);
  expect(sent).toMatchObject({ owner_id: 'alice', item_id: 'track', listened_seconds: 60 });
  mocks.request.mockResolvedValueOnce({});
  await flushListeningExport();
  expect(JSON.parse(mocks.request.mock.calls.at(-1)![1].body).id).toBe(sent.id);
  setListeningExportOwner(null);
  expect(mocks.request.mock.calls.filter((call) => call[0] === '/api/me/listening-events')).toHaveLength(2);
  vi.restoreAllMocks();
});

it('starts a new logical play after an export account changes instead of restoring the old binding', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(100_000);
  const oldId = '11111111-1111-4111-8111-111111111111';
  localStorage.setItem('lumina:music:listening-events:alice', JSON.stringify({ pending: [], current: {
    event: { id: oldId, owner_id: 'alice', item_id: 'track', started_at: 90, listened_seconds: 50, account_generations: { listenbrainz: 'old-account' } },
    sample: { track: 'track', index: 0, position: 50, duration: 120, playing: true, at: 100_000 },
  } }));
  mocks.request.mockResolvedValue({ items: [{ enabled: true, provider: 'listenbrainz', generation: 'new-account' }] });
  setListeningExportOwner('alice');
  await Promise.resolve(); await Promise.resolve();
  recordMusicListening('alice', track, playback(50));
  vi.mocked(Date.now).mockReturnValue(105_000);
  recordMusicListening('alice', track, playback(55, false));
  const saved = JSON.parse(localStorage.getItem('lumina:music:listening-events:alice')!);
  expect(saved.current.event).toMatchObject({ listened_seconds: 5, account_generations: { listenbrainz: 'new-account' } });
  expect(saved.current.event.id).not.toBe(oldId);
  vi.restoreAllMocks();
});

it('keeps the newest account refresh when an older response arrives first', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(100_000);
  let finishOld!: (value: unknown) => void;
  let finishNew!: (value: unknown) => void;
  mocks.request.mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve; }))
    .mockReturnValueOnce(new Promise((resolve) => { finishNew = resolve; }));
  setListeningExportOwner('alice');
  const refreshed = refreshListeningExport('alice');
  finishOld({ items: [{ provider: 'listenbrainz', enabled: true, generation: 'old-account' }] });
  await Promise.resolve();
  finishNew({ items: [{ provider: 'listenbrainz', enabled: true, generation: 'new-account' }] });
  await refreshed;
  recordMusicListening('alice', track, playback(0));
  vi.mocked(Date.now).mockReturnValue(105_000);
  mocks.request.mockResolvedValue({});
  recordMusicListening('alice', track, playback(5, false));
  const mutation = mocks.request.mock.calls.find((call) => call[0] === '/api/me/listening-events')!;
  expect(JSON.parse(mutation[1].body).account_generations).toEqual({ listenbrainz: 'new-account' });
  vi.restoreAllMocks();
});
