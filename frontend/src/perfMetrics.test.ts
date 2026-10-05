import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ sendClientMetrics: vi.fn(), beaconClientMetrics: vi.fn() }));
vi.mock('./api', () => api);

async function fresh() {
  vi.resetModules();
  return import('./perfMetrics');
}

beforeEach(() => {
  vi.useFakeTimers();
  api.sendClientMetrics.mockResolvedValue(undefined);
  api.beaconClientMetrics.mockReturnValue(true);
});
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

describe('perfMetrics', () => {
  it('keeps only the newest 500 samples and posts them in 200-sample chunks every 30 s', async () => {
    const metrics = await fresh();
    for (let value = 0; value < 520; value += 1) metrics.recordMetric('image_load_ms', 'poster:net', value);
    await vi.advanceTimersByTimeAsync(metrics.FLUSH_INTERVAL_MS);
    expect(api.sendClientMetrics.mock.calls.map(([chunk]) => chunk.length)).toEqual([200, 200, 100]);
    expect(api.sendClientMetrics.mock.calls[0][0][0]).toEqual({ metric: 'image_load_ms', label: 'poster:net', value: 20 });
  });

  it('sends what is left through the beacon on pagehide, never twice', async () => {
    const metrics = await fresh();
    metrics.recordMetric('ttff_ms', 'direct', 1200);
    metrics.recordMetric('ttff_ms', 'remux', 2400);
    window.dispatchEvent(new Event('pagehide'));
    expect(api.beaconClientMetrics).toHaveBeenCalledTimes(1);
    expect(api.beaconClientMetrics.mock.calls[0][0]).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(metrics.FLUSH_INTERVAL_MS);
    expect(api.sendClientMetrics).not.toHaveBeenCalled();
  });

  it('drops invalid values, rounds, and clamps long ones', async () => {
    const metrics = await fresh();
    for (const value of [Number.NaN, -1, Number.POSITIVE_INFINITY]) metrics.recordMetric('ttff_ms', 'direct', value);
    metrics.recordMetric('ttff_ms', 'direct', 12.6);
    metrics.recordMetric('ttff_ms', 'direct', 4_000_000);
    await metrics.flushMetrics();
    expect(api.sendClientMetrics.mock.calls[0][0].map((sample: { value: number }) => sample.value)).toEqual([13, 3_600_000]);
  });

  it('a failed post is dropped, not retried', async () => {
    const metrics = await fresh();
    api.sendClientMetrics.mockRejectedValue(new Error('CSRF token missing or invalid.'));
    metrics.recordMetric('ttff_ms', 'direct', 900);
    await vi.advanceTimersByTimeAsync(metrics.FLUSH_INTERVAL_MS * 2);
    expect(api.sendClientMetrics).toHaveBeenCalledTimes(1);
  });

  it('keeps the first Play mark for 5 s, hands it out once, and ignores one older than 60 s', async () => {
    const metrics = await fresh();
    metrics.notePlayIntent({}, 100);
    metrics.notePlayIntent({ keepRecent: true }, 2_000);
    expect(metrics.takePlayIntent(2_500)).toBe(100);
    expect(metrics.takePlayIntent(2_600)).toBeNull();
    metrics.notePlayIntent({}, 100);
    metrics.notePlayIntent({ keepRecent: true }, 10_000);
    expect(metrics.takePlayIntent(10_001)).toBe(10_000);
    metrics.notePlayIntent({}, 0);
    expect(metrics.takePlayIntent(60_001)).toBeNull();
  });

  it('reports the first presented frame once, through requestVideoFrameCallback or else playing', async () => {
    const metrics = await fresh();
    let presented: ((now: number) => void) | null = null;
    const video = Object.assign(document.createElement('video'), { requestVideoFrameCallback: (callback: (now: number) => void) => { presented = callback; return 7; }, cancelVideoFrameCallback: vi.fn() });
    const onFrame = vi.fn();
    metrics.onFirstFrame(video, onFrame);
    presented!(1234);
    presented!(1300);
    expect(onFrame.mock.calls).toEqual([[1234]]);
    const audio = document.createElement('audio');
    const onAudio = vi.fn();
    const cancel = metrics.onFirstFrame(audio, onAudio);
    audio.dispatchEvent(new Event('playing'));
    audio.dispatchEvent(new Event('playing'));
    expect(onAudio).toHaveBeenCalledTimes(1);
    const stopped = vi.fn();
    metrics.onFirstFrame(audio, stopped)();
    audio.dispatchEvent(new Event('playing'));
    expect(stopped).not.toHaveBeenCalled();
    cancel();
  });

  it('counts long tasks over 50 ms until stopped, and 0 where unsupported', async () => {
    let report: ((list: { getEntries: () => { duration: number }[] }) => void) | null = null;
    const disconnect = vi.fn();
    class FakeObserver {
      static supportedEntryTypes = ['longtask'];
      constructor(callback: typeof report) { report = callback; }
      observe() {}
      takeRecords() { return [{ duration: 70 }]; }
      disconnect = disconnect;
    }
    vi.stubGlobal('PerformanceObserver', FakeObserver);
    const metrics = await fresh();
    const stop = metrics.startLongTaskCount();
    report!({ getEntries: () => [{ duration: 60 }, { duration: 40 }] });
    expect(stop()).toBe(2);
    expect(disconnect).toHaveBeenCalled();
    vi.stubGlobal('PerformanceObserver', class { static supportedEntryTypes: string[] = []; });
    expect((await fresh()).startLongTaskCount()()).toBe(0);
  });
});
