import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { climbTarget, startDashClimb } from './dashClimb';

const ladder = [
  { id: 'v240', bandwidth: 270_000 },
  { id: 'v720', bandwidth: 3_164_000 },
  { id: 'v1080', bandwidth: 5_102_000 },
  { id: 'v2160', bandwidth: 24_632_000 },
];

describe('climbTarget (owner rule: back to the top once the trouble passes)', () => {
  it('climbs to the highest rung the last three segments sustain, with headroom', () => {
    expect(climbTarget(ladder, 'v240', [40e6, 45e6, 50e6], 12)).toBe('v2160');
    expect(climbTarget(ladder, 'v240', [9e6, 50e6, 50e6], 12)).toBe('v1080'); // the slowest recent segment decides
  });

  it('never climbs on a thin buffer, too few samples, or throughput the next rung would outrun', () => {
    expect(climbTarget(ladder, 'v240', [50e6, 50e6, 50e6], 4)).toBeNull();
    expect(climbTarget(ladder, 'v240', [50e6, 50e6, 50e6], Number.NaN)).toBeNull(); // dash.js reports NaN while rebuffering
    expect(climbTarget(ladder, 'v240', [50e6, 50e6], 12)).toBeNull();
    expect(climbTarget(ladder, 'v1080', [20e6, 20e6, 20e6], 12)).toBeNull();
  });

  it('never steps down: that stays with dash.js abandon, buffer and dropped-frame rules', () => {
    expect(climbTarget(ladder, 'v2160', [1e6, 1e6, 1e6], 12)).toBeNull();
  });
});

describe('startDashClimb', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(10_000); });
  afterEach(() => vi.useRealTimers());

  function fakePlayer(requests: unknown[], buffer = 12, current = 'v240') {
    return {
      chosen: [] as string[],
      getRepresentationsByType: () => [...ladder].reverse(),
      getCurrentRepresentationForType: () => ({ id: current }),
      getBufferLength: () => buffer,
      getDashMetrics: () => ({ getHttpRequests: () => requests }),
      setRepresentationForTypeById(_type: string, id: string) { this.chosen.push(id); },
    };
  }
  // Requested after the climb watch starts (t = 10 s), as dash.js would while playing.
  const segment = (bytes: number, ms: number, type = 'MediaSegment') => ({ type, trace: [{ b: [bytes] }], trequest: new Date(10_500), _tfinish: new Date(10_500 + ms) });

  it('measures whole segments, request to last byte, and climbs once they sustain a higher rung', () => {
    // 1 MB in 100 ms = 80 Mb/s; tiny index ranges and init segments are not throughput samples.
    const player = fakePlayer([segment(776, 15), segment(1_000_000, 100, 'InitializationSegment'), segment(1_000_000, 100), segment(1_000_000, 100), segment(1_000_000, 100)]);
    const stop = startDashClimb(player as never);
    vi.advanceTimersByTime(2000);
    expect(player.chosen).toEqual(['v2160']);
    stop();
    vi.advanceTimersByTime(10_000);
    expect(player.chosen).toHaveLength(1);
  });

  it('stays put while the latest segments are slow, whatever order dash.js lists them in', () => {
    const at = (bytes: number, start: number, end: number) => ({ type: 'MediaSegment', trace: [{ b: [bytes] }], trequest: new Date(start), _tfinish: new Date(end) });
    // Newest first: three slow ones (2 Mb/s) just now, fast ones a little earlier.
    const player = fakePlayer([at(1_000_000, 19_000, 23_000), at(1_000_000, 15_000, 19_000), at(1_000_000, 11_000, 15_000), at(1_000_000, 10_100, 10_200), at(1_000_000, 10_200, 10_300), at(1_000_000, 10_300, 10_400)]);
    const stop = startDashClimb(player as never);
    vi.advanceTimersByTime(14_000);
    expect(player.chosen).toEqual([]); // 2 Mb/s sustained cannot carry 720p's 3.2 Mb/s
    stop();
  });

  it('ignores segments from before the rung last changed and ones older than 30 s', () => {
    const at = (start: number, end: number) => ({ type: 'MediaSegment', trace: [{ b: [1_000_000] }], trequest: new Date(start), _tfinish: new Date(end) });
    const stale = fakePlayer([at(5_000, 5_100), at(6_000, 6_100), at(7_000, 7_100)]); // fast, but before the watch began
    const stopStale = startDashClimb(stale as never);
    vi.advanceTimersByTime(4000);
    expect(stale.chosen).toEqual([]);
    stopStale();
    const old = fakePlayer([at(14_500, 14_600), at(15_000, 15_100), at(15_500, 15_600)]); // the clock is at 14 s now
    const stopOld = startDashClimb(old as never);
    vi.advanceTimersByTime(40_000); // they age out before the buffer ever matters
    expect(old.chosen).toEqual(['v2160']); // climbed on the first tick, while they were fresh
    stopOld();
  });
});
