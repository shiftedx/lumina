// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MusicEngineState, MusicSource } from './musicEngine';
import { MusicStream } from './musicStream';

const source = (id: string): MusicSource => ({
  id,
  url: `/api/music/tracks/${id}/audio`,
  fallbackUrl: `/api/music/tracks/${id}/audio?format=wav`,
});

function fakeAudio() {
  const audio = document.createElement('audio');
  let duration = 180;
  let currentTime = 0;
  let ended = false;
  Object.defineProperties(audio, {
    duration: { configurable: true, get: () => duration, set: (value) => { duration = value; } },
    currentTime: { configurable: true, get: () => currentTime, set: (value) => { currentTime = value; } },
    ended: { configurable: true, get: () => ended, set: (value) => { ended = value; } },
  });
  const play = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
  const pause = vi.fn();
  const load = vi.fn();
  Object.defineProperties(audio, {
    play: { configurable: true, value: play },
    pause: { configurable: true, value: pause },
    load: { configurable: true, value: load },
  });
  return { audio, play, pause, load, setDuration: (value: number) => { duration = value; }, setEnded: (value: boolean) => { ended = value; } };
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('MusicStream', () => {
  it('uses only a bounded HEAD probe and leaves media bytes to the browser element', async () => {
    const fake = fakeAudio();
    const body = { cancel: vi.fn().mockResolvedValue(undefined) };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, body });
    vi.stubGlobal('fetch', fetchMock);
    const states: MusicEngineState[] = [];
    const stream = new MusicStream({ onChange: (state) => states.push(state), createAudio: () => fake.audio, createContext: () => null });

    await stream.play([source('a')], 0, 12);

    expect(fetchMock).toHaveBeenCalledWith(source('a').url, expect.objectContaining({ method: 'HEAD', credentials: 'same-origin', signal: expect.any(AbortSignal) }));
    expect(body.cancel).toHaveBeenCalledOnce();
    expect(fake.audio.src).toMatch(/\/api\/music\/tracks\/a\/audio$/);
    expect(fake.audio.preload).toBe('metadata');
    expect(fake.play).toHaveBeenCalledOnce();
    expect(states.at(-1)).toMatchObject({ index: 0, position: 12, duration: 180, playing: true, playbackMode: 'native' });
  });

  it('tries the original once, then one server WAV fallback for unsupported decoding', async () => {
    const fake = fakeAudio();
    const unsupported = Object.assign(new Error('unsupported codec'), { name: 'NotSupportedError' });
    fake.play.mockRejectedValueOnce(unsupported).mockResolvedValueOnce(undefined);
    const probes: string[] = [];
    const stream = new MusicStream({
      onChange: () => undefined,
      createAudio: () => fake.audio,
      createContext: () => null,
      probe: async (url) => { probes.push(url); },
    });

    await stream.play([source('a')]);

    expect(probes).toEqual([source('a').url, source('a').fallbackUrl]);
    expect(fake.play).toHaveBeenCalledTimes(2);
    expect(stream.getState()).toMatchObject({ playing: true, playbackMode: 'fallback', error: null });
    expect(fake.audio.src).toContain('format=wav');
  });

  it('handles the media element unsupported-source event with one fallback attempt', async () => {
    const fake = fakeAudio();
    const probes: string[] = [];
    let mediaError: Pick<MediaError, 'code' | 'message'> | null = null;
    Object.defineProperty(fake.audio, 'error', { configurable: true, get: () => mediaError });
    const stream = new MusicStream({
      onChange: () => undefined,
      createAudio: () => fake.audio,
      createContext: () => null,
      probe: async (url) => { probes.push(url); },
    });
    await stream.play([source('a')]);
    mediaError = { code: 4, message: 'unsupported' };
    fake.audio.dispatchEvent(new Event('error'));
    await settle();
    expect(probes).toEqual([source('a').url, source('a').fallbackUrl]);
    expect(stream.getState().playbackMode).toBe('fallback');

    fake.audio.dispatchEvent(new Event('error'));
    await settle();
    expect(probes).toHaveLength(2);
    expect(stream.getState()).toMatchObject({ playing: false, error: 'unsupported' });
  });

  it('stops immediately on access denial without attempting the compatibility source', async () => {
    const fake = fakeAudio();
    const body = { cancel: vi.fn().mockResolvedValue(undefined) };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403, body }));
    const access = vi.fn();
    const stream = new MusicStream({ onChange: () => undefined, onAccessDenied: access, createAudio: () => fake.audio, createContext: () => null });

    await expect(stream.play([source('a')])).rejects.toThrow('Listening access');

    expect(fetch).toHaveBeenCalledOnce();
    expect(body.cancel).toHaveBeenCalledOnce();
    expect(fake.play).not.toHaveBeenCalled();
    expect(fake.pause).toHaveBeenCalled();
    expect(access).toHaveBeenCalledWith(expect.objectContaining({ status: 403 }));
    expect(stream.getState()).toMatchObject({ playing: false, loading: false, error: 'Listening access is currently unavailable.' });
  });

  it('keeps the current element while replacing future order and advances on ended', async () => {
    const fake = fakeAudio();
    const probes: string[] = [];
    const stream = new MusicStream({
      onChange: () => undefined,
      createAudio: () => fake.audio,
      createContext: () => null,
      probe: async (url) => { probes.push(url); },
    });
    await stream.play([source('a'), source('b'), source('c')]);
    const currentUrl = fake.audio.src;

    stream.replaceUpcoming([source('a'), source('c'), source('b')]);
    expect(fake.audio.src).toBe(currentUrl);
    fake.setEnded(true);
    fake.audio.dispatchEvent(new Event('ended'));
    await settle();

    expect(probes.at(-1)).toBe(source('c').url);
    expect(stream.getState().index).toBe(1);
    expect(() => stream.replaceUpcoming([source('c'), source('b')])).toThrow(/current source/);
  });

  it('supports repeat-one, repeat-all, seek, volume, gain and clean disposal', async () => {
    const fake = fakeAudio();
    const gain = (value = 1) => {
      const param = { value, setValueAtTime: vi.fn((next: number) => { param.value = next; }) };
      return { gain: param, connect: vi.fn(function connect(node) { return node; }), disconnect: vi.fn() };
    };
    const sourceNode = { connect: vi.fn(function connect(node) { return node; }), disconnect: vi.fn() };
    const gains = [gain(), gain()];
    const context = {
      state: 'running', currentTime: 0, destination: {}, sampleRate: 48_000,
      createMediaElementSource: vi.fn(() => sourceNode), createGain: vi.fn(() => gains.shift()),
      resume: vi.fn().mockResolvedValue(undefined), close: vi.fn().mockResolvedValue(undefined),
    } as unknown as AudioContext;
    const stream = new MusicStream({ onChange: () => undefined, createAudio: () => fake.audio, createContext: () => context, probe: async () => undefined });
    await stream.play([source('a'), source('b')]);
    stream.setVolume(0.4);
    stream.setGain(1.5);
    await stream.seek(27);
    stream.setRepeat('one');
    fake.audio.dispatchEvent(new Event('ended'));
    await settle();
    expect(stream.getState().index).toBe(0);
    stream.setRepeat('all');
    await stream.previous();
    expect(stream.getState().index).toBe(1);
    expect(fake.audio.currentTime).toBe(0);

    stream.dispose();
    expect(fake.pause).toHaveBeenCalled();
    expect(fake.audio.getAttribute('src')).toBeNull();
    expect(sourceNode.disconnect).toHaveBeenCalled();
    expect(context.close).toHaveBeenCalled();
  });

  it('aborts an old asynchronous probe so it can never autoplay', async () => {
    const fake = fakeAudio();
    let release: (() => void) | undefined;
    const stream = new MusicStream({
      onChange: () => undefined,
      createAudio: () => fake.audio,
      createContext: () => null,
      probe: (_url, signal) => new Promise<void>((resolve, reject) => {
        release = resolve;
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
      }),
    });
    const pending = stream.play([source('old')]);
    stream.dispose();
    release?.();
    await pending;

    expect(fake.play).not.toHaveBeenCalled();
  });
});
