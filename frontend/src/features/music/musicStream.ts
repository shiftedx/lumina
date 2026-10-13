import type { MusicEngineState, MusicRepeatMode, MusicSource } from './musicEngine';

export interface MusicStreamOptions {
  onChange: (state: MusicEngineState) => void;
  onAccessDenied?: (error: Error & { status?: number }) => void;
  createAudio?: () => HTMLAudioElement;
  createContext?: () => AudioContext | null;
  probe?: (url: string, signal: AbortSignal) => Promise<void>;
}

type StreamMode = 'native' | 'fallback';

const INITIAL_STATE: MusicEngineState = {
  index: -1,
  position: 0,
  duration: 0,
  playing: false,
  loading: false,
  error: null,
};

class MusicStreamError extends Error {
  public constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = 'MusicStreamError';
  }
}

const finitePosition = (value: number): number => Number.isFinite(value) && value >= 0 ? value : 0;
const clamp = (value: number): number => Math.max(0, Math.min(1, value));
const isAbort = (error: unknown): boolean => error instanceof Error && error.name === 'AbortError';
const isUnsupported = (error: unknown): boolean => error instanceof Error && error.name === 'NotSupportedError';
const messageForStatus = (status: number): string => status === 401
  ? 'Sign in again to continue listening.'
  : status === 403 ? 'Listening access is currently unavailable.' : 'This track is unavailable.';

async function defaultProbe(url: string, signal: AbortSignal): Promise<void> {
  const response = await fetch(url, { method: 'HEAD', credentials: 'same-origin', signal });
  await response.body?.cancel().catch(() => undefined);
  if (!response.ok) throw new MusicStreamError(messageForStatus(response.status), response.status);
}

function defaultAudio(): HTMLAudioElement {
  return new Audio();
}

function defaultContext(): AudioContext | null {
  const Context = globalThis.AudioContext ?? (globalThis as typeof globalThis & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  return Context ? new Context() : null;
}

/**
 * Browser-native music streaming. The media element owns Range requests and
 * decoding, so no complete track is fetched into JavaScript memory. This path
 * is intentionally labelled streaming: element `ended` transitions are not
 * gapless, even when two adjacent responses happen to play without a pause.
 */
export class MusicStream {
  private readonly audio: HTMLAudioElement;
  private readonly onChange: MusicStreamOptions['onChange'];
  private readonly onAccessDenied?: MusicStreamOptions['onAccessDenied'];
  private readonly probe: NonNullable<MusicStreamOptions['probe']>;
  private readonly createContext: NonNullable<MusicStreamOptions['createContext']>;
  private state: MusicEngineState = { ...INITIAL_STATE };
  private queue: MusicSource[] = [];
  private repeat: MusicRepeatMode = 'off';
  private generation = 0;
  private request: AbortController | null = null;
  private fallbackAttempted = false;
  private pendingPosition = 0;
  private volume = 0.8;
  private gain = 1;
  private disposed = false;
  private context: AudioContext | null = null;
  private graphAttempted = false;
  private sourceNode: MediaElementAudioSourceNode | null = null;
  private gainNode: GainNode | null = null;
  private volumeNode: GainNode | null = null;

  public constructor(options: MusicStreamOptions) {
    this.audio = (options.createAudio ?? defaultAudio)();
    this.onChange = options.onChange;
    this.onAccessDenied = options.onAccessDenied;
    this.probe = options.probe ?? defaultProbe;
    this.createContext = options.createContext ?? defaultContext;
    this.audio.preload = 'metadata';
    this.audio.addEventListener('loadedmetadata', this.metadata);
    this.audio.addEventListener('durationchange', this.metadata);
    this.audio.addEventListener('timeupdate', this.timeupdate);
    this.audio.addEventListener('playing', this.playing);
    this.audio.addEventListener('pause', this.paused);
    this.audio.addEventListener('waiting', this.waiting);
    this.audio.addEventListener('ended', this.ended);
    this.audio.addEventListener('error', this.failed);
    this.applyLevels();
  }

  public async play(queue: readonly MusicSource[], index = 0, position = 0): Promise<void> {
    this.assertUsable();
    const sources = queue.map((source) => ({ ...source }));
    if (!sources.length) throw new Error('Cannot stream an empty music queue.');
    if (!Number.isInteger(index) || index < 0 || index >= sources.length) throw new Error(`Music queue index ${index} is out of range.`);
    this.queue = sources;
    const generation = this.invalidate();
    await this.start(index, position, 'native', generation);
  }

  public pause(): void {
    if (this.disposed) return;
    this.audio.pause();
    this.setState({ position: finitePosition(this.audio.currentTime), playing: false, loading: false });
  }

  public async resume(): Promise<void> {
    this.assertUsable();
    if (this.state.index < 0 || this.state.index >= this.queue.length || this.state.playing) return;
    const generation = this.generation;
    try {
      await this.resumeGraph();
      await this.audio.play();
      if (this.active(generation)) this.setState({ playing: true, loading: false, error: null });
    } catch (error) {
      if (!this.active(generation) || isAbort(error)) return;
      if (isUnsupported(error) && !this.fallbackAttempted && this.queue[this.state.index]?.fallbackUrl) {
        await this.start(this.state.index, this.state.position, 'fallback', generation);
        return;
      }
      this.fail(error);
      throw error;
    }
  }

  public async seek(position: number): Promise<void> {
    this.assertUsable();
    const duration = Number.isFinite(this.audio.duration) ? this.audio.duration : this.state.duration;
    const target = Math.min(finitePosition(position), duration > 0 ? duration : Number.MAX_SAFE_INTEGER);
    this.audio.currentTime = target;
    this.setState({ position: target });
  }

  public async next(): Promise<void> {
    this.assertUsable();
    if (this.state.index < 0) return;
    const target = this.state.index + 1 < this.queue.length ? this.state.index + 1 : this.repeat === 'all' ? 0 : null;
    if (target !== null) await this.start(target, 0, 'native', this.invalidate());
  }

  public async previous(): Promise<void> {
    this.assertUsable();
    if (this.state.index < 0) return;
    const target = this.state.index > 0 ? this.state.index - 1 : this.repeat === 'all' ? this.queue.length - 1 : 0;
    if (target === this.state.index) await this.seek(0);
    else await this.start(target, 0, 'native', this.invalidate());
  }

  public setVolume(volume: number): void {
    if (!Number.isFinite(volume)) return;
    this.volume = clamp(volume);
    this.applyLevels();
  }

  public setGain(gain: number): void {
    if (!Number.isFinite(gain) || gain < 0) return;
    this.gain = gain;
    this.applyLevels();
  }

  public setRepeat(mode: MusicRepeatMode): void {
    this.assertUsable();
    if (mode !== 'off' && mode !== 'all' && mode !== 'one') throw new Error(`Unknown repeat mode "${mode}".`);
    this.repeat = mode;
  }

  public replaceUpcoming(queue: readonly MusicSource[]): void {
    this.assertUsable();
    const index = this.state.index;
    const sources = queue.map((source) => ({ ...source }));
    if (index < 0 || !this.same(this.queue[index], sources[index])) {
      throw new Error('The replacement queue must contain the current source at the current index.');
    }
    this.queue = [...this.queue.slice(0, index), ...sources.slice(index)];
  }

  public getState(): MusicEngineState {
    return { ...this.state };
  }

  public dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.invalidate();
    this.audio.pause();
    this.audio.removeEventListener('loadedmetadata', this.metadata);
    this.audio.removeEventListener('durationchange', this.metadata);
    this.audio.removeEventListener('timeupdate', this.timeupdate);
    this.audio.removeEventListener('playing', this.playing);
    this.audio.removeEventListener('pause', this.paused);
    this.audio.removeEventListener('waiting', this.waiting);
    this.audio.removeEventListener('ended', this.ended);
    this.audio.removeEventListener('error', this.failed);
    this.audio.removeAttribute('src');
    this.audio.load();
    try { this.sourceNode?.disconnect(); } catch { /* best effort */ }
    try { this.gainNode?.disconnect(); } catch { /* best effort */ }
    try { this.volumeNode?.disconnect(); } catch { /* best effort */ }
    void this.context?.close().catch(() => undefined);
    this.context = null;
    this.sourceNode = null;
    this.gainNode = null;
    this.volumeNode = null;
    this.queue = [];
  }

  private async start(index: number, position: number, mode: StreamMode, generation: number): Promise<void> {
    const source = this.queue[index];
    if (!source || !this.active(generation)) return;
    const url = mode === 'fallback' ? source.fallbackUrl : source.url;
    if (!url) throw new Error('This track has no compatible streaming source.');
    this.fallbackAttempted = mode === 'fallback';
    this.pendingPosition = finitePosition(position);
    this.setState({ index, position: this.pendingPosition, duration: 0, playing: false, loading: true, error: null, playbackMode: mode });
    const request = new AbortController();
    this.request?.abort();
    this.request = request;
    try {
      await this.probe(url, request.signal);
      if (!this.active(generation) || request.signal.aborted) return;
      await this.resumeGraph();
      if (!this.active(generation) || request.signal.aborted) return;
      this.audio.src = url;
      this.audio.load();
      try { this.audio.currentTime = this.pendingPosition; } catch { /* metadata will apply it */ }
      await this.audio.play();
      if (!this.active(generation) || request.signal.aborted) return;
      this.setState({
        position: finitePosition(this.audio.currentTime || this.pendingPosition),
        duration: Number.isFinite(this.audio.duration) ? this.audio.duration : 0,
        playing: true,
        loading: false,
        error: null,
        playbackMode: mode,
      });
    } catch (error) {
      if (!this.active(generation) || request.signal.aborted || isAbort(error)) return;
      if (mode === 'native' && isUnsupported(error) && source.fallbackUrl) {
        await this.start(index, position, 'fallback', generation);
        return;
      }
      this.fail(error);
      throw error;
    } finally {
      if (this.request === request) this.request = null;
    }
  }

  private readonly metadata = (): void => {
    if (this.disposed) return;
    const duration = Number.isFinite(this.audio.duration) ? this.audio.duration : 0;
    try { this.audio.currentTime = Math.min(this.pendingPosition, duration || this.pendingPosition); } catch { /* seekability follows */ }
    this.setState({ duration, position: finitePosition(this.audio.currentTime || this.pendingPosition) });
  };

  private readonly timeupdate = (): void => {
    if (!this.disposed) this.setState({ position: finitePosition(this.audio.currentTime) });
  };

  private readonly playing = (): void => {
    if (!this.disposed) this.setState({ playing: true, loading: false, error: null });
  };

  private readonly paused = (): void => {
    if (!this.disposed && !this.audio.ended) this.setState({ position: finitePosition(this.audio.currentTime), playing: false, loading: false });
  };

  private readonly waiting = (): void => {
    if (!this.disposed) this.setState({ loading: true });
  };

  private readonly ended = (): void => {
    if (this.disposed) return;
    const duration = Number.isFinite(this.audio.duration) ? this.audio.duration : this.state.duration;
    this.setState({ position: duration, playing: false, loading: false });
    if (this.repeat === 'one') void this.start(this.state.index, 0, 'native', this.invalidate()).catch(() => undefined);
    else if (this.state.index + 1 < this.queue.length) void this.start(this.state.index + 1, 0, 'native', this.invalidate()).catch(() => undefined);
    else if (this.repeat === 'all') void this.start(0, 0, 'native', this.invalidate()).catch(() => undefined);
  };

  private readonly failed = (): void => {
    if (this.disposed) return;
    const error = this.audio.error;
    if (error?.code === 4 && !this.fallbackAttempted && this.queue[this.state.index]?.fallbackUrl) {
      void this.start(this.state.index, this.state.position, 'fallback', this.generation).catch(() => undefined);
      return;
    }
    this.fail(new MusicStreamError(error?.message || 'This track is unavailable.'));
  };

  private fail(error: unknown): void {
    const failure: Error & { status?: number } = error instanceof Error
      ? error as Error & { status?: number }
      : new Error('This track is unavailable.');
    this.audio.pause();
    this.setState({ playing: false, loading: false, error: failure.message });
    if (failure.status === 401 || failure.status === 403) this.onAccessDenied?.(failure);
  }

  private invalidate(): number {
    this.generation += 1;
    this.request?.abort();
    this.request = null;
    this.audio.pause();
    return this.generation;
  }

  private active(generation: number): boolean {
    return !this.disposed && generation === this.generation;
  }

  private same(left: MusicSource | undefined, right: MusicSource | undefined): boolean {
    return Boolean(left && right && left.id === right.id && left.url === right.url && left.fallbackUrl === right.fallbackUrl);
  }

  private setState(patch: Partial<MusicEngineState>): void {
    this.state = { ...this.state, ...patch };
    this.onChange({ ...this.state });
  }

  private assertUsable(): void {
    if (this.disposed) throw new Error('MusicStream has been disposed.');
  }

  private async resumeGraph(): Promise<void> {
    if (!this.context && !this.graphAttempted) {
      this.graphAttempted = true;
      let context: AudioContext | null = null;
      try {
        context = this.createContext();
        this.context = context;
        if (context) {
          this.sourceNode = context.createMediaElementSource(this.audio);
          this.gainNode = context.createGain();
          this.volumeNode = context.createGain();
          this.sourceNode.connect(this.gainNode).connect(this.volumeNode).connect(context.destination);
          this.applyLevels();
        }
      } catch {
        void context?.close().catch(() => undefined);
        this.context = null;
        this.sourceNode = null;
        this.gainNode = null;
        this.volumeNode = null;
        this.applyLevels();
      }
    }
    const context = this.context;
    if (context && (context.state === 'suspended' || (context.state as string) === 'interrupted')) await context.resume();
  }

  private applyLevels(): void {
    if (this.gainNode && this.volumeNode && this.context) {
      this.audio.volume = 1;
      this.gainNode.gain.setValueAtTime(this.gain, this.context.currentTime);
      this.volumeNode.gain.setValueAtTime(this.volume, this.context.currentTime);
    } else {
      this.audio.volume = clamp(this.volume * this.gain);
    }
  }
}
