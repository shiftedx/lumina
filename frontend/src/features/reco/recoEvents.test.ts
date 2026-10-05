import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ sendRecoEvents: vi.fn(), beaconRecoEvents: vi.fn() }));
vi.mock('../../api', () => api);

import { LIST_ID, OTHER_LIST_ID, recoAnnotation, remoteKey } from '../../test/recoFixtures';
import { flushRecoEvents, RECO_FLUSH_CHUNK, RECO_FLUSH_INTERVAL_MS, RECO_MAX_EVENTS, recordRecoImpression, recordRecoOpen, resetRecoEvents } from './recoEvents';

const annotation = (n: number, patch = {}) => recoAnnotation({ key: remoteKey(`v${n}`), position: n % 24, ...patch });
const sent = (call = 0) => api.sendRecoEvents.mock.calls[call][0] as Array<{ kind: string; list_id: string; key: string; age_ms: number }>;
/** Moves the clock without firing timers (the flush interval must not run while a test ages events). */
const later = (ms: number) => vi.setSystemTime(Date.now() + ms);

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-30T12:00:00Z'));
  api.sendRecoEvents.mockReset().mockResolvedValue(undefined);
  api.beaconRecoEvents.mockReset().mockReturnValue(true);
  resetRecoEvents();
});
afterEach(() => {
  resetRecoEvents();
  vi.useRealTimers();
});

describe('recoEvents', () => {
  it('counts one impression per list and key in a page view, and every open', async () => {
    recordRecoImpression(annotation(1));
    recordRecoImpression(annotation(1));
    recordRecoImpression(annotation(1, { list_id: OTHER_LIST_ID }));
    recordRecoOpen(annotation(1));
    recordRecoOpen(annotation(1));
    await flushRecoEvents();
    expect(api.sendRecoEvents).toHaveBeenCalledTimes(1);
    expect(sent().map((event) => [event.kind, event.list_id])).toEqual([['impression', LIST_ID], ['impression', OTHER_LIST_ID], ['open', LIST_ID], ['open', LIST_ID]]);
  });

  it('reports how long ago each event happened, measured at the flush', async () => {
    recordRecoImpression(annotation(1));
    later(5_000);
    recordRecoOpen(annotation(1));
    later(1_000);
    await flushRecoEvents();
    expect(sent().map((event) => event.age_ms)).toEqual([6_000, 1_000]);
  });

  it('drops events older than the server\'s 15-minute window rather than send a batch it would refuse', async () => {
    recordRecoImpression(annotation(1));
    later(900_001);
    recordRecoImpression(annotation(2));
    await flushRecoEvents();
    expect(sent()).toHaveLength(1);
    expect(sent()[0].key).toBe(remoteKey('v2'));
    expect(sent()[0].age_ms).toBe(0);

    recordRecoOpen(annotation(3));
    later(900_001);
    api.sendRecoEvents.mockClear();
    await flushRecoEvents();
    expect(api.sendRecoEvents).not.toHaveBeenCalled();
  });

  it('keeps the newest 500 and sends at most 200 per request', async () => {
    for (let n = 0; n < 520; n += 1) recordRecoImpression(annotation(n));
    await flushRecoEvents();
    expect(api.sendRecoEvents.mock.calls.map((call) => call[0].length)).toEqual([RECO_FLUSH_CHUNK, RECO_FLUSH_CHUNK, RECO_MAX_EVENTS - 2 * RECO_FLUSH_CHUNK]);
    expect(sent(0)[0].key).toBe(remoteKey('v20'));
    expect(sent(2).at(-1)?.key).toBe(remoteKey('v519'));
  });

  it('drops a failed post\'s events and never throws', async () => {
    api.sendRecoEvents.mockRejectedValueOnce(new Error('offline'));
    recordRecoImpression(annotation(1));
    await expect(flushRecoEvents()).resolves.toBeUndefined();
    api.sendRecoEvents.mockClear();
    await flushRecoEvents();
    expect(api.sendRecoEvents).not.toHaveBeenCalled();
  });

  it('posts what is buffered every 30 seconds', async () => {
    recordRecoImpression(annotation(1));
    await vi.advanceTimersByTimeAsync(RECO_FLUSH_INTERVAL_MS - 1);
    expect(api.sendRecoEvents).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(api.sendRecoEvents).toHaveBeenCalledTimes(1);
  });

  it('beacons what is left on pagehide, and nothing is posted twice', async () => {
    recordRecoImpression(annotation(1));
    recordRecoOpen(annotation(1));
    window.dispatchEvent(new Event('pagehide'));
    expect(api.beaconRecoEvents).toHaveBeenCalledTimes(1);
    expect(api.beaconRecoEvents.mock.calls[0][0]).toHaveLength(2);
    await flushRecoEvents();
    expect(api.sendRecoEvents).not.toHaveBeenCalled();
  });

  it('forgets everything on reset, so the next member starts clean', async () => {
    recordRecoImpression(annotation(1));
    resetRecoEvents();
    await flushRecoEvents();
    expect(api.sendRecoEvents).not.toHaveBeenCalled();
    recordRecoImpression(annotation(1));
    await flushRecoEvents();
    expect(sent()).toHaveLength(1);
  });
});
