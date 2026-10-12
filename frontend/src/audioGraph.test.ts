import { describe, expect, it, vi } from 'vitest';
import { STALL_CHECK_MS, audioGraphFor, inMuteRange } from './features/watch/audioGraph';

type FakeMedia = { currentTime: number; playbackRate: number; paused: boolean; volume: number; muted: boolean; currentSrc: string; src: string; canPlayType: () => string };

function fakeMedia(overrides: Partial<FakeMedia> = {}): HTMLMediaElement {
  return { currentTime: 5, playbackRate: 1, paused: false, volume: 1, muted: false, currentSrc: '', src: '', canPlayType: () => '', ...overrides } as unknown as HTMLMediaElement;
}

function fakeContext() {
  const calls: Array<[number, number]> = [];
  const gain = { setValueAtTime: vi.fn((value: number, at: number) => calls.push([value, at])), cancelScheduledValues: vi.fn() };
  const node = { gain, connect: vi.fn((next: unknown) => next) };
  const source = { connect: vi.fn(() => node) };
  const context = {
    currentTime: 10,
    state: 'suspended',
    destination: {},
    createGain: vi.fn(() => node),
    createMediaElementSource: vi.fn(() => source),
    resume: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  };
  return { context, calls, gain, createContext: vi.fn(() => context as unknown as AudioContext) };
}

const HALF = 0.5;

describe('audioGraph', () => {
  it('finds mute ranges with an exclusive end', () => {
    expect(inMuteRange(5.5, [{ start_seconds: 5, end_seconds: 6 }])).toBe(true);
    expect(inMuteRange(6, [{ start_seconds: 5, end_seconds: 6 }])).toBe(false);
  });

  it('schedules loudness × mute one second ahead', () => {
    const fake = fakeContext();
    const graph = audioGraphFor(fakeMedia(), { createContext: fake.createContext });
    expect(graph.mode).toBe('webaudio');
    graph.setLoudnessGain(HALF);
    fake.calls.length = 0;
    graph.setMuteRanges([{ start_seconds: 5.5, end_seconds: 6 }, { start_seconds: 20, end_seconds: 21 }]);
    expect(fake.gain.cancelScheduledValues).toHaveBeenLastCalledWith(10);
    expect(fake.calls).toEqual([[HALF, 10], [0, 10.5], [HALF, 11]]);
  });

  it('honours playback rate and starts muted inside a range', () => {
    const fake = fakeContext();
    const graph = audioGraphFor(fakeMedia({ playbackRate: 2, currentTime: 5.2 }), { createContext: fake.createContext });
    graph.setMuteRanges([{ start_seconds: 5, end_seconds: 6 }, { start_seconds: 6.5, end_seconds: 7 }]);
    expect(fake.calls).toEqual([[0, 10], [1, 10.4], [0, 10.65], [1, 10.9]]);
  });

  it('only sets the current gain while paused', () => {
    const fake = fakeContext();
    const graph = audioGraphFor(fakeMedia({ paused: true }), { createContext: fake.createContext });
    graph.setMuteRanges([{ start_seconds: 5.5, end_seconds: 6 }]);
    expect(fake.calls).toEqual([[1, 10]]);
  });

  it('builds one graph per element and resumes a suspended context', async () => {
    const fake = fakeContext();
    const media = fakeMedia();
    const graph = audioGraphFor(media, { createContext: fake.createContext });
    expect(audioGraphFor(media, { createContext: fake.createContext })).toBe(graph);
    expect(fake.createContext).toHaveBeenCalledTimes(1);
    expect(fake.context.createMediaElementSource).toHaveBeenCalledTimes(1);
    await graph.resume();
    expect(fake.context.resume).toHaveBeenCalledTimes(1);
  });

  it('falls back to the element when the source node cannot be created', () => {
    const fake = fakeContext();
    fake.context.createMediaElementSource.mockImplementation(() => {
      throw new DOMException('already connected', 'InvalidStateError');
    });
    expect(audioGraphFor(fakeMedia(), { createContext: fake.createContext }).mode).toBe('element');
    expect(fake.context.close).toHaveBeenCalled();
  });

  it('cross-origin media uses the element fallback', () => {
    const fake = fakeContext();
    const graph = audioGraphFor(fakeMedia({ currentSrc: 'https://cdn.example.com/video.mp4' }), { createContext: fake.createContext });
    expect(graph.mode).toBe('element');
    expect(fake.createContext).not.toHaveBeenCalled();
    expect(audioGraphFor(fakeMedia({ currentSrc: 'blob:http://localhost:3000/x' }), { createContext: fake.createContext }).mode).toBe('webaudio');
  });

  it('element fallback pre-mutes with lookahead, restores only its own mute, and only attenuates', () => {
    const media = fakeMedia({ currentTime: 5 });
    const graph = audioGraphFor(media, { createContext: () => null });
    graph.setMuteRanges([{ start_seconds: 5.2, end_seconds: 6 }]);
    expect(media.muted).toBe(true);
    media.currentTime = 6.5;
    graph.schedule();
    expect(media.muted).toBe(false);

    const userMuted = fakeMedia({ currentTime: 5, muted: true });
    const other = audioGraphFor(userMuted, { createContext: () => null });
    other.setMuteRanges([{ start_seconds: 5, end_seconds: 6 }]);
    userMuted.currentTime = 7;
    other.schedule();
    expect(userMuted.muted).toBe(true);

    graph.setLoudnessGain(HALF);
    graph.setVolume(0.8);
    expect(media.volume).toBeCloseTo(0.8 * HALF, 5);
    graph.setLoudnessGain(2);
    expect(media.volume).toBeCloseTo(0.8, 5);
    graph.setLoudnessGain(Number.NaN);
    expect(media.volume).toBeCloseTo(0.8, 5);
  });

  it('bypasses the graph when the context clock does not advance after play', async () => {
    vi.useFakeTimers();
    const fake = fakeContext();
    fake.context.state = 'running';
    const onBypass = vi.fn();
    const media = fakeMedia();
    const graph = audioGraphFor(media, { createContext: fake.createContext, onBypass });
    graph.setLoudnessGain(HALF);
    await graph.resume();
    await vi.advanceTimersByTimeAsync(STALL_CHECK_MS);
    expect(fake.context.close).toHaveBeenCalled();
    expect(onBypass).toHaveBeenCalledTimes(1);
    expect(graph.mode).toBe('element');
    expect(media.volume).toBe(HALF);
    vi.useRealTimers();
  });

  it('keeps the graph when the context clock advances', async () => {
    vi.useFakeTimers();
    const fake = fakeContext();
    fake.context.state = 'running';
    const onBypass = vi.fn();
    const graph = audioGraphFor(fakeMedia(), { createContext: fake.createContext, onBypass });
    await graph.resume();
    fake.context.currentTime += 0.5;
    await vi.advanceTimersByTimeAsync(STALL_CHECK_MS);
    expect(onBypass).not.toHaveBeenCalled();
    expect(graph.mode).toBe('webaudio');
    vi.useRealTimers();
  });
});
