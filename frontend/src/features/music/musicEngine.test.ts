import { describe, expect, it, vi } from 'vitest';
import type { MusicEngineState, MusicSource } from './musicEngine';
import { MAX_AUDIO_BYTES, MAX_DECODED_AUDIO_BYTES, MusicEngine } from './musicEngine';

type FakeSource = {
  buffer: AudioBuffer | null;
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  onended: (() => void) | null;
};

type MutableAudioContext = Omit<AudioContext, 'currentTime' | 'state'> & {
  currentTime: number;
  state: AudioContextState;
};

type FakeAudio = {
  context: MutableAudioContext;
  sources: FakeSource[];
  gains: Array<{ gain: { value: number; setValueAtTime: ReturnType<typeof vi.fn> }; disconnect: ReturnType<typeof vi.fn> }>;
};

const track = (id: string, fallbackUrl?: string): MusicSource => ({ id, url: `/audio/${id}.bin`, fallbackUrl, duration: 5, sampleRate: 48_000, channels: 2 });

function fakeAudio(durations: Record<string, number> = {}): FakeAudio {
  const sources: FakeSource[] = [];
  const gains: FakeAudio['gains'] = [];
  const context = {
    currentTime: 10,
    state: 'suspended' as AudioContextState,
    destination: {},
    createGain: vi.fn(() => {
      const gain = { value: 1, setValueAtTime: vi.fn() };
      const node = { gain, connect: vi.fn((next: unknown) => next), disconnect: vi.fn() };
      gains.push(node);
      return node;
    }),
    createBufferSource: vi.fn(() => {
      const source: FakeSource = {
        buffer: null,
        start: vi.fn(),
        stop: vi.fn(),
        disconnect: vi.fn(),
        onended: null,
      };
      (source as FakeSource & { connect: ReturnType<typeof vi.fn> }).connect = vi.fn((next: unknown) => next);
      sources.push(source);
      return source;
    }),
    decodeAudioData: vi.fn(async (bytes: ArrayBuffer) => {
      const id = new TextDecoder().decode(bytes);
      return { duration: durations[id] ?? 5 } as AudioBuffer;
    }),
    resume: vi.fn(async () => {
      context.state = 'running';
    }),
    close: vi.fn(async () => {
      context.state = 'closed';
    }),
  } as unknown as MutableAudioContext;
  return { context, sources, gains };
}

function loader(): (source: MusicSource, signal: AbortSignal) => Promise<ArrayBuffer> {
  return async (source) => new TextEncoder().encode(source.id).buffer as ArrayBuffer;
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe('MusicEngine', () => {
  it('resumes the context before loading and schedules one lookahead at the exact buffer boundary', async () => {
    const fake = fakeAudio({ a: 5, b: 4 });
    const events: string[] = [];
    (fake.context.resume as unknown as { mockImplementation: (implementation: () => Promise<void>) => void }).mockImplementation(async () => {
      events.push('resume');
      fake.context.state = 'running';
    });
    const engine = new MusicEngine({
      onChange: () => undefined,
      createContext: () => {
        events.push('context');
        return fake.context;
      },
      getAudio: async (source) => {
        events.push(`audio:${source.id}:${fake.context.state}`);
        return new TextEncoder().encode(source.id).buffer as ArrayBuffer;
      },
    });

    await engine.play([track('a'), track('b')], 0, 2);
    await settle();

    expect(events.slice(0, 4)).toEqual(['context', 'resume', 'audio:a:running', 'audio:b:running']);
    expect(fake.context.resume).toHaveBeenCalledTimes(1);
    expect(fake.sources[0].start).toHaveBeenCalledWith(10, 2);
    // 10 + (5 seconds - the 2-second starting offset), not a wall-clock estimate.
    expect(fake.sources[1].start).toHaveBeenCalledWith(13, 0);
    expect(engine.getState().index).toBe(0);

    fake.sources[0].onended?.();
    expect(engine.getState()).toMatchObject({ index: 1, position: 0, duration: 4, playing: true, loading: false });
  });

  it('keeps default gapless timing and starts decoded-buffer crossfade before the boundary', async () => {
    const fake = fakeAudio({ a: 10, b: 8 });
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio: loader() });
    await engine.play([track('a'), track('b')]);
    await settle();
    expect(fake.sources[1].start).toHaveBeenCalledWith(20, 0);

    engine.setCrossfade(3);
    await settle();
    const fadingSource = fake.sources.find((source) => source.start.mock.calls.some(([at]) => at === 17));
    expect(fadingSource?.start).toHaveBeenCalledWith(17, 0);
    engine.setCrossfade(0);
    expect(fadingSource?.stop).toHaveBeenCalled();
    engine.dispose();
  });

  it('decodes only the current and one next track, then advances lookahead after transition', async () => {
    const fake = fakeAudio();
    const loaded: string[] = [];
    const engine = new MusicEngine({
      onChange: () => undefined,
      createContext: () => fake.context,
      getAudio: async (source) => {
        loaded.push(source.id);
        return new TextEncoder().encode(source.id).buffer as ArrayBuffer;
      },
    });

    await engine.play([track('a'), track('b'), track('c')]);
    await settle();
    expect(loaded).toEqual(['a', 'b']);
    expect(engine.getState().index).toBe(0);

    fake.sources[0].onended?.();
    await settle();
    expect(loaded).toEqual(['a', 'b', 'c']);
    expect(engine.getState().index).toBe(1);
  });

  it('pauses scheduled nodes and resumes or seeks from the context-clock position', async () => {
    vi.useFakeTimers();
    try {
      const fake = fakeAudio({ a: 10, b: 5 });
      const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio: loader() });
      await engine.play([track('a'), track('b')]);
      await settle();

      fake.context.currentTime = 13.25;
      engine.pause();
      expect(engine.getState()).toMatchObject({ position: 3.25, playing: false, loading: false });
      expect(fake.sources[0].stop).toHaveBeenCalledTimes(1);
      expect(fake.sources[1].stop).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);

      fake.context.currentTime = 20;
      await engine.resume();
      expect(fake.sources[2].start).toHaveBeenCalledWith(20, 3.25);
      expect(engine.getState().playing).toBe(true);

      fake.context.currentTime = 25;
      await engine.seek(1.5);
      expect(fake.sources[4].start).toHaveBeenCalledWith(25, 1.5);
      expect(engine.getState()).toMatchObject({ position: 1.5, playing: true });
    } finally {
      vi.useRealTimers();
    }
  });

  it('invalidates slow old loads so they cannot create or start a source', async () => {
    const fake = fakeAudio();
    const pending = new Map<string, { resolve: (bytes: ArrayBuffer) => void; signal: AbortSignal }>();
    const getAudio = vi.fn((source: MusicSource, signal: AbortSignal) => new Promise<ArrayBuffer>((resolve) => {
      pending.set(source.id, { resolve, signal });
    }));
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio });

    const oldPlay = engine.play([track('old')]);
    await settle();
    expect(pending.get('old')?.signal.aborted).toBe(false);

    const newPlay = engine.play([track('new')]);
    expect(pending.get('old')?.signal.aborted).toBe(true);
    pending.get('old')?.resolve(new ArrayBuffer(1));
    await settle();
    expect(fake.sources).toHaveLength(0);

    pending.get('new')?.resolve(new ArrayBuffer(1));
    await Promise.all([oldPlay, newPlay]);
    expect(fake.sources).toHaveLength(1);
    expect(fake.sources[0].buffer).not.toBeNull();
  });

  it('surfaces a failed lookahead and retries it when the current track reaches the boundary', async () => {
    const fake = fakeAudio({ a: 5, b: 4 });
    let bAttempts = 0;
    const engine = new MusicEngine({
      onChange: () => undefined,
      createContext: () => fake.context,
      getAudio: async (source) => {
        if (source.id === 'b' && bAttempts++ === 0) throw new Error('temporary read failure');
        return new TextEncoder().encode(source.id).buffer as ArrayBuffer;
      },
    });

    await engine.play([track('a'), track('b')]);
    await settle();
    expect(engine.getState().error).toContain('temporary read failure');
    expect(engine.getState().index).toBe(0);

    fake.context.currentTime = 15;
    fake.sources[0].onended?.();
    await settle();
    expect(bAttempts).toBe(2);
    expect(engine.getState()).toMatchObject({ index: 1, playing: true, error: null });
  });

  it('reports unsupported decoding, enforces the default fetch size bound, and releases timers/nodes on dispose', async () => {
    vi.useFakeTimers();
    const fake = fakeAudio();
    fake.context.decodeAudioData = vi.fn(async () => {
      throw new Error('codec not supported');
    }) as unknown as AudioContext['decodeAudioData'];
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio: loader() });
    await expect(engine.play([track('bad')])).rejects.toThrow(/Unable to decode audio source/);
    expect(engine.getState()).toMatchObject({ loading: false, playing: false });
    expect(engine.getState().error).toContain('codec not supported');
    expect(vi.getTimerCount()).toBe(0);

    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-length': String(MAX_AUDIO_BYTES + 1) }),
      body: null,
      arrayBuffer: async () => new ArrayBuffer(1),
    }));
    vi.stubGlobal('fetch', fetchMock);
    const bounded = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context });
    await expect(bounded.play([track('too-large')])).rejects.toThrow(/input limit/);
    expect(fetchMock).toHaveBeenCalledWith('/audio/too-large.bin', expect.objectContaining({ credentials: 'same-origin', signal: expect.any(AbortSignal) }));

    const playable = fakeAudio();
    const running = new MusicEngine({ onChange: () => undefined, createContext: () => playable.context, getAudio: loader() });
    await running.play([track('a')]);
    expect(vi.getTimerCount()).toBe(1);
    running.dispose();
    expect(playable.sources[0].stop).toHaveBeenCalledTimes(1);
    expect(playable.sources[0].disconnect).toHaveBeenCalledTimes(1);
    expect(playable.context.close).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('applies independent gain and volume nodes before the first play', async () => {
    const fake = fakeAudio();
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio: loader() });
    engine.setVolume(0.35);
    engine.setGain(1.75);
    await engine.play([track('a')]);
    expect(fake.gains[0].gain.value).toBe(1.75);
    expect(fake.gains[1].gain.value).toBe(0.35);
  });

  it('emits bounded position updates only while playing', async () => {
    vi.useFakeTimers();
    try {
      const fake = fakeAudio({ a: 20 });
      const states: MusicEngineState[] = [];
      const engine = new MusicEngine({ onChange: (state) => states.push(state), createContext: () => fake.context, getAudio: loader() });
      await engine.play([track('a')]);
      const before = states.length;
      fake.context.currentTime = 10.5;
      await vi.advanceTimersByTimeAsync(250);
      expect(states.length).toBe(before + 1);
      expect(engine.getState().position).toBeCloseTo(0.5);
      engine.pause();
      const paused = states.length;
      await vi.advanceTimersByTimeAsync(1000);
      expect(states.length).toBe(paused);
    } finally {
      vi.useRealTimers();
    }
  });

  it('schedules repeat one with the same buffer at the exact boundary', async () => {
    const fake = fakeAudio({ a: 5 });
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio: loader() });
    engine.setRepeat('one');

    await engine.play([track('a')]);
    await settle();

    expect(fake.sources).toHaveLength(2);
    expect(fake.sources[1].buffer).toBe(fake.sources[0].buffer);
    expect(fake.sources[1].start).toHaveBeenCalledWith(15, 0);

    fake.context.currentTime = 15;
    fake.sources[0].onended?.();
    expect(engine.getState()).toMatchObject({ index: 0, position: 0, playing: true, playbackMode: 'native' });
    expect(fake.sources[2].buffer).toBe(fake.sources[0].buffer);
    expect(fake.sources[2].start).toHaveBeenCalledWith(20, 0);
    engine.dispose();
  });

  it('wraps repeat all at the last track with one-track lookahead', async () => {
    const fake = fakeAudio({ a: 5, b: 4, c: 3 });
    const loaded: string[] = [];
    const engine = new MusicEngine({
      onChange: () => undefined,
      createContext: () => fake.context,
      getAudio: async (source) => {
        loaded.push(source.id);
        return new TextEncoder().encode(source.id).buffer as ArrayBuffer;
      },
    });
    engine.setRepeat('all');

    await engine.play([track('a'), track('b'), track('c')]);
    await settle();
    expect(loaded).toEqual(['a', 'b']);
    expect(fake.sources[1].start).toHaveBeenCalledWith(15, 0);

    fake.context.currentTime = 15;
    fake.sources[0].onended?.();
    await settle();
    expect(engine.getState().index).toBe(1);
    expect(loaded).toEqual(['a', 'b', 'c']);

    fake.context.currentTime = 19;
    fake.sources[1].onended?.();
    await settle();
    expect(engine.getState().index).toBe(2);
    expect(loaded).toEqual(['a', 'b', 'c', 'a']);
    expect(fake.sources[3].start).toHaveBeenCalledWith(22, 0);
    expect(fake.sources).toHaveLength(4);
    engine.dispose();
  });

  it('changes repeat by replacing only the scheduled successor while leaving current audio untouched', async () => {
    const fake = fakeAudio({ a: 5, b: 4 });
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio: loader() });

    await engine.play([track('a'), track('b')]);
    await settle();
    expect(fake.sources[1].start).toHaveBeenCalledWith(15, 0);

    engine.setRepeat('one');
    await settle();
    expect(fake.sources[0].stop).not.toHaveBeenCalled();
    expect(fake.sources[1].stop).toHaveBeenCalledTimes(1);
    expect(fake.sources[2].buffer).toBe(fake.sources[0].buffer);
    expect(fake.sources[2].start).toHaveBeenCalledWith(15, 0);

    engine.setRepeat('off');
    await settle();
    expect(fake.sources[0].stop).not.toHaveBeenCalled();
    expect(fake.sources[2].stop).toHaveBeenCalledTimes(1);
    expect(fake.sources[3].start).toHaveBeenCalledWith(15, 0);
    engine.dispose();
  });

  it('reorders future queue content in place without restarting the current node', async () => {
    const fake = fakeAudio({ a: 5, b: 4, c: 3 });
    const loaded: string[] = [];
    const engine = new MusicEngine({
      onChange: () => undefined,
      createContext: () => fake.context,
      getAudio: async (source) => {
        loaded.push(source.id);
        return new TextEncoder().encode(source.id).buffer as ArrayBuffer;
      },
    });

    await engine.play([track('a'), track('b'), track('c')]);
    await settle();
    const currentNode = fake.sources[0];
    const oldSuccessor = fake.sources[1];
    expect(loaded).toEqual(['a', 'b']);

    engine.replaceUpcoming([track('a'), track('c'), track('b')]);
    await settle();

    expect(engine.getState().index).toBe(0);
    expect(currentNode.stop).not.toHaveBeenCalled();
    expect(currentNode.start).toHaveBeenCalledTimes(1);
    expect(oldSuccessor.stop).toHaveBeenCalledTimes(1);
    expect(loaded).toEqual(['a', 'b', 'c']);
    expect(fake.sources[2].start).toHaveBeenCalledWith(15, 0);
    engine.dispose();
  });

  it('rejects a future replacement that moves or changes the current source', async () => {
    const fake = fakeAudio();
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio: loader() });
    await engine.play([track('a'), track('b')]);
    await settle();

    expect(() => engine.replaceUpcoming([track('b'), track('a')])).toThrow(/current source at the current index/);
    expect(engine.getState().index).toBe(0);
    engine.dispose();
  });

  it('uses a bounded fallback fetch only after native decode fails and reports fallback mode', async () => {
    const fake = fakeAudio({ fallback: 6 });
    fake.context.decodeAudioData = vi.fn(async (bytes: ArrayBuffer) => {
      const id = new TextDecoder().decode(bytes);
      if (id === 'native') throw new Error('native codec not supported');
      return { duration: 6 } as AudioBuffer;
    }) as unknown as AudioContext['decodeAudioData'];
    const fetchMock = vi.fn(async (url: string) => {
      return {
        ok: true,
        status: 200,
        headers: new Headers({ 'content-length': '8' }),
        body: null,
        arrayBuffer: async () => new TextEncoder().encode(url.endsWith('compatible.wav') ? 'fallback' : 'native').buffer as ArrayBuffer,
      };
    });
    vi.stubGlobal('fetch', fetchMock);
    try {
      const engine = new MusicEngine({
        onChange: () => undefined,
        createContext: () => fake.context,
      });

      await engine.play([track('a', '/audio/a-compatible.wav')]);
      expect(fetchMock).toHaveBeenNthCalledWith(1, '/audio/a.bin', expect.objectContaining({ credentials: 'same-origin', signal: expect.any(AbortSignal) }));
      expect(fetchMock).toHaveBeenCalledWith('/audio/a-compatible.wav', expect.objectContaining({ credentials: 'same-origin', signal: expect.any(AbortSignal) }));
      expect(engine.getState()).toMatchObject({ playing: true, playbackMode: 'fallback' });
      engine.dispose();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it.each([401, 403])('does not try a fallback after an HTTP %s permission failure', async (status) => {
    const fake = fakeAudio();
    const fetchMock = vi.fn(async () => ({ ok: false, status, headers: new Headers(), body: null, arrayBuffer: async () => new ArrayBuffer(0) }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context });
      await expect(engine.play([track('a', '/audio/a-compatible.wav')])).rejects.toThrow(`HTTP ${status}`);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fake.context.decodeAudioData).not.toHaveBeenCalled();
      engine.dispose();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('cancels a bounded response reader on oversize input and on generation changes', async () => {
    const fake = fakeAudio();
    const cancel = vi.fn(async () => undefined);
    const releaseLock = vi.fn();
    const oversizedChunk = { byteLength: MAX_AUDIO_BYTES + 1 } as unknown as Uint8Array;
    const oversizedReader = {
      read: vi.fn(async () => ({ done: false, value: oversizedChunk })),
      cancel,
      releaseLock,
    };
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: new Headers(),
      body: { getReader: () => oversizedReader },
      arrayBuffer: async () => new ArrayBuffer(0),
    }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context });
      await expect(engine.play([track('too-large')])).rejects.toThrow(/input limit/);
      expect(cancel).toHaveBeenCalledTimes(1);
      expect(releaseLock).toHaveBeenCalledTimes(1);
      engine.dispose();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('cancels an in-flight bounded reader when a newer play generation replaces it', async () => {
    const fake = fakeAudio();
    let resolveRead: ((result: { done: boolean; value?: Uint8Array }) => void) | undefined;
    const reader = {
      read: vi.fn(() => new Promise<{ done: boolean; value?: Uint8Array }>((resolve) => { resolveRead = resolve; })),
      cancel: vi.fn(async () => { resolveRead?.({ done: true }); }),
      releaseLock: vi.fn(),
    };
    let fetchCount = 0;
    const fetchMock = vi.fn(async () => {
      fetchCount += 1;
      if (fetchCount === 1) return { ok: true, status: 200, headers: new Headers(), body: { getReader: () => reader }, arrayBuffer: async () => new ArrayBuffer(0) };
      return { ok: true, status: 200, headers: new Headers(), body: null, arrayBuffer: async () => new TextEncoder().encode('new').buffer as ArrayBuffer };
    });
    vi.stubGlobal('fetch', fetchMock);
    try {
      const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context });
      const oldPlay = engine.play([track('old')]);
      await settle();
      const newPlay = engine.play([track('new')]);
      await Promise.all([oldPlay, newPlay]);

      expect(reader.cancel).toHaveBeenCalledTimes(1);
      expect(reader.releaseLock).toHaveBeenCalledTimes(1);
      expect(fake.sources).toHaveLength(1);
      expect(engine.getState().index).toBe(0);
      engine.dispose();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('refuses a predicted oversized PCM buffer before loading or decoding it', async () => {
    const fake = fakeAudio();
    const getAudio = vi.fn(loader());
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio });

    await expect(engine.play([{
      ...track('long-opus'),
      duration: 60 * 60,
      sampleRate: 48_000,
      channels: 2,
    }])).rejects.toThrow(/decoded-memory budget.*streaming playback/i);

    expect(getAudio).not.toHaveBeenCalled();
    expect(fake.context.decodeAudioData).not.toHaveBeenCalled();
    expect(fake.sources).toHaveLength(0);
    expect(engine.getState()).toMatchObject({ playing: false, loading: false });
    engine.dispose();
  });

  it('refuses unprobed audio before loading it, including tracks introduced by queue refresh', async () => {
    const fake = fakeAudio();
    const getAudio = vi.fn(loader());
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio });
    await expect(engine.play([{ id: 'unprobed', url: '/audio/unprobed.bin' }])).rejects.toThrow(/streaming playback/i);
    expect(getAudio).not.toHaveBeenCalled();
    expect(fake.context.decodeAudioData).not.toHaveBeenCalled();
    expect(fake.sources).toHaveLength(0);
    engine.dispose();
  });

  it('drops an unexpectedly oversized decoded buffer without scheduling it', async () => {
    const fake = fakeAudio();
    const oversized = {
      duration: 30,
      length: Math.floor(MAX_DECODED_AUDIO_BYTES / 4) + 1,
      numberOfChannels: 1,
    } as AudioBuffer;
    fake.context.decodeAudioData = vi.fn(async () => oversized) as unknown as AudioContext['decodeAudioData'];
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio: loader() });

    await expect(engine.play([track('compressed-surprise')])).rejects.toThrow(/decoded-memory budget/);

    expect(fake.context.createBufferSource).not.toHaveBeenCalled();
    expect(fake.sources).toHaveLength(0);
    expect(engine.getState()).toMatchObject({ playing: false, loading: false });
    engine.dispose();
  });

  it('keeps two ordinary four-minute 48 kHz stereo tracks ready for a seamless boundary', async () => {
    const fake = fakeAudio();
    const decodedBytes = 4 * 60 * 48_000 * 2 * 4;
    fake.context.decodeAudioData = vi.fn(async (bytes: ArrayBuffer) => ({
      duration: 4 * 60,
      length: decodedBytes / 2 / 4,
      numberOfChannels: 2,
      marker: new TextDecoder().decode(bytes),
    } as unknown as AudioBuffer)) as unknown as AudioContext['decodeAudioData'];
    const getAudio = vi.fn(loader());
    const hinted = (id: string): MusicSource => ({ ...track(id), expectedDecodedBytes: decodedBytes });
    const engine = new MusicEngine({ onChange: () => undefined, createContext: () => fake.context, getAudio });

    await engine.play([hinted('a'), hinted('b')]);
    await settle();
    expect(getAudio.mock.calls.map(([source]) => source.id)).toEqual(['a', 'b']);
    expect(fake.sources).toHaveLength(2);
    expect(fake.sources[1].start).toHaveBeenCalledWith(250, 0);

    fake.sources[0].onended?.();
    await settle();
    expect(engine.getState()).toMatchObject({ index: 1, playing: true, error: null });
    engine.dispose();
  });
});
