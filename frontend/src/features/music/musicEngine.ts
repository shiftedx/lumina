export type MusicSource = {
  id: string;
  url: string;
  fallbackUrl?: string;
  /** Buffered playback needs a size estimate; native streaming also accepts unknown facts. */
  expectedDecodedBytes?: number;
  duration?: number;
  sampleRate?: number;
  channels?: number;
};

export type MusicPlaybackMode = 'native' | 'fallback';

export type MusicEngineState = {
  index: number;
  position: number;
  duration: number;
  playing: boolean;
  loading: boolean;
  error: string | null;
  /** The decoder source used by the active buffer, when playback has started. */
  playbackMode?: MusicPlaybackMode;
};

export type MusicRepeatMode = 'off' | 'all' | 'one';

export type MusicEngineOptions = {
  onChange: (state: MusicEngineState) => void;
  getAudio?: (source: MusicSource, signal: AbortSignal) => Promise<ArrayBuffer>;
  createContext?: () => AudioContext;
};

/** The fetch/decode boundary is deliberately finite so a queue cannot turn into a library decoder. */
export const MAX_AUDIO_BYTES = 128 * 1024 * 1024;
/** One decoded track remains bounded even when compressed input expands sharply. */
export const MAX_DECODED_AUDIO_BYTES = 128 * 1024 * 1024;
/** Gapless playback retains exactly the current and next buffers inside this combined budget. */
export const MAX_GAPLESS_AUDIO_BYTES = 256 * 1024 * 1024;

const POSITION_UPDATE_MS = 250;

type Playback = {
  node: AudioBufferSourceNode;
  fade: GainNode;
  buffer: AudioBuffer;
  mode: MusicPlaybackMode;
  index: number;
  startTime: number;
  offset: number;
  generation: number;
};

type DecodedAudio = {
  buffer: AudioBuffer;
  mode: MusicPlaybackMode;
  decodedBytes: number | null;
};

type Prefetched = {
  index: number;
  source: MusicSource;
  audio: DecodedAudio;
};

type PendingLoad = {
  generation: number;
  controller: AbortController;
  promise: Promise<DecodedAudio>;
  source: MusicSource;
};

const INITIAL_STATE: MusicEngineState = {
  index: -1,
  position: 0,
  duration: 0,
  playing: false,
  loading: false,
  error: null,
};

function defaultContext(): AudioContext {
  const Context = globalThis.AudioContext ?? (globalThis as typeof globalThis & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Context) throw new Error('Web Audio is unavailable in this browser.');
  return new Context();
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error) return error;
  return 'Unknown audio error.';
}

class AudioSourceLoadError extends Error {
  public readonly status: number;

  public constructor(message: string, status: number) {
    super(message);
    this.name = 'AudioSourceLoadError';
    this.status = status;
  }
}

class DecodedMemoryError extends Error {
  public constructor(source: MusicSource, available: number) {
    const maxMiB = Math.floor(Math.max(0, available) / 1024 / 1024);
    super(`Audio source "${source.id}" exceeds the ${maxMiB} MiB decoded-memory budget. Choose streaming playback.`);
    this.name = 'DecodedMemoryError';
  }
}

function errorStatus(error: unknown): number | undefined {
  if (error instanceof AudioSourceLoadError) return error.status;
  if (!error || typeof error !== 'object') return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === 'number' ? status : undefined;
}

function isPermissionError(error: unknown): boolean {
  const status = errorStatus(error);
  if (status === 401 || status === 403) return true;
  return error instanceof Error && (error.name === 'NotAllowedError' || error.name === 'SecurityError');
}

function isAbortError(error: unknown, signal?: AbortSignal): boolean {
  return signal?.aborted === true || (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError') || (error instanceof Error && error.name === 'AbortError');
}

function abortError(): Error {
  if (typeof DOMException !== 'undefined') return new DOMException('The audio request was aborted.', 'AbortError');
  const error = new Error('The audio request was aborted.');
  error.name = 'AbortError';
  return error;
}

function finiteNonNegative(value: number, fallback = 0): number {
  return Number.isFinite(value) && value >= 0 ? value : fallback;
}

function clampPosition(position: number, duration: number): number {
  const wanted = finiteNonNegative(position);
  return duration > 0 && Number.isFinite(duration) ? Math.min(wanted, duration) : wanted;
}

function isArrayBuffer(value: unknown): value is ArrayBuffer {
  return value instanceof ArrayBuffer || Object.prototype.toString.call(value) === '[object ArrayBuffer]';
}

function validDuration(buffer: AudioBuffer, source: MusicSource): number {
  const duration = buffer.duration;
  if (!Number.isFinite(duration) || duration <= 0) {
    throw new Error(`Unable to decode audio source "${source.id}": the decoded audio has no finite duration.`);
  }
  return duration;
}

function expectedDecodedBytes(source: MusicSource): number | null {
  if (typeof source.expectedDecodedBytes === 'number' && Number.isFinite(source.expectedDecodedBytes) && source.expectedDecodedBytes > 0) {
    return source.expectedDecodedBytes;
  }
  const { duration, sampleRate, channels } = source;
  if (![duration, sampleRate, channels].every((value) => typeof value === 'number' && Number.isFinite(value) && value > 0)) return null;
  const bytes = duration! * sampleRate! * channels! * Float32Array.BYTES_PER_ELEMENT;
  return Number.isFinite(bytes) && bytes > 0 ? bytes : null;
}

function measuredDecodedBytes(buffer: AudioBuffer): number | null {
  const frames = buffer.length;
  const channels = buffer.numberOfChannels;
  if (!Number.isFinite(frames) || frames < 0 || !Number.isFinite(channels) || channels <= 0) return null;
  const bytes = frames * channels * Float32Array.BYTES_PER_ELEMENT;
  return Number.isSafeInteger(bytes) && bytes >= 0 ? bytes : null;
}

function checkDecodedBudget(buffer: AudioBuffer, source: MusicSource, available: number): number | null {
  const bytes = measuredDecodedBytes(buffer);
  if (bytes !== null && bytes > available) throw new DecodedMemoryError(source, available);
  return bytes;
}

function setParamValue(param: AudioParam, value: number, at: number): void {
  // Assigning value keeps lightweight test contexts and older implementations working;
  // setValueAtTime also makes the update explicit to the audio thread when available.
  param.value = value;
  if (typeof param.setValueAtTime === 'function' && Number.isFinite(at)) {
    try {
      param.setValueAtTime(value, at);
    } catch {
      // Some minimal AudioContext test doubles expose value but not a usable scheduler.
    }
  }
}

async function readResponse(response: Response, source: MusicSource, signal: AbortSignal): Promise<ArrayBuffer> {
  if (!response.ok) {
    throw new AudioSourceLoadError(`Unable to load audio source "${source.id}" (HTTP ${response.status}).`, response.status);
  }

  const contentLength = Number(response.headers?.get('content-length'));
  if (Number.isFinite(contentLength) && contentLength > MAX_AUDIO_BYTES) {
    try {
      await response.body?.cancel();
    } catch {
      // The size error is the useful failure even if the body is already closing.
    }
    throw new Error(`Audio source "${source.id}" exceeds the ${MAX_AUDIO_BYTES} byte input limit.`);
  }

  if (!response.body) {
    const bytes = await response.arrayBuffer();
    if (signal.aborted) throw abortError();
    if (bytes.byteLength > MAX_AUDIO_BYTES) {
      throw new Error(`Audio source "${source.id}" exceeds the ${MAX_AUDIO_BYTES} byte input limit.`);
    }
    return bytes;
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let completed = false;
  let cancellation: Promise<void> | null = null;
  const cancel = (): Promise<void> => {
    if (!cancellation) cancellation = reader.cancel().catch(() => undefined);
    return cancellation;
  };
  const cancelReader = () => {
    void cancel();
  };
  signal.addEventListener('abort', cancelReader, { once: true });
  try {
    while (true) {
      if (signal.aborted) throw abortError();
      const result = await reader.read();
      if (signal.aborted) throw abortError();
      if (result.done) break;
      const chunk = result.value;
      if (!chunk) continue;
      total += chunk.byteLength;
      if (total > MAX_AUDIO_BYTES) {
        throw new Error(`Audio source "${source.id}" exceeds the ${MAX_AUDIO_BYTES} byte input limit.`);
      }
      chunks.push(chunk.slice());
    }
    completed = true;
  } finally {
    signal.removeEventListener('abort', cancelReader);
    if (!completed) {
      await cancel();
    }
    reader.releaseLock();
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes.buffer as ArrayBuffer;
}

async function defaultGetAudio(source: MusicSource, signal: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(source.url, { credentials: 'same-origin', signal });
  return readResponse(response, source, signal);
}

export class MusicEngine {
  private readonly onChange: (state: MusicEngineState) => void;
  private readonly getAudio: (source: MusicSource, signal: AbortSignal) => Promise<ArrayBuffer>;
  private readonly createContext: () => AudioContext;

  private context: AudioContext | null = null;
  private gainNode: GainNode | null = null;
  private volumeNode: GainNode | null = null;
  private volume = 1;
  private gain = 1;
  private crossfadeSeconds = 0;

  private queue: readonly MusicSource[] = [];
  private state: MusicEngineState = { ...INITIAL_STATE };
  private repeat: MusicRepeatMode = 'off';
  private generation = 0;
  private successorRevision = 0;
  private disposed = false;
  private currentBuffer: AudioBuffer | null = null;
  private prefetched: Prefetched | null = null;
  private currentPlayback: Playback | null = null;
  private scheduledNext: Playback | null = null;
  private readonly requests = new Set<AbortController>();
  private readonly pendingLoads = new Map<number, PendingLoad>();
  private positionTimer: ReturnType<typeof globalThis.setInterval> | null = null;

  public constructor(options: MusicEngineOptions) {
    this.onChange = options.onChange;
    this.getAudio = options.getAudio ?? defaultGetAudio;
    this.createContext = options.createContext ?? defaultContext;
  }

  public async play(queue: readonly MusicSource[], index = 0, position = 0): Promise<void> {
    this.assertUsable();
    const sources = queue.map((source) => this.copySource(source));
    if (sources.length === 0) {
      const error = new Error('Cannot play an empty music queue.');
      this.replaceQueue([], -1);
      this.setState({ error: error.message });
      throw error;
    }
    if (!Number.isInteger(index) || index < 0 || index >= sources.length) {
      throw new Error(`Music queue index ${index} is out of range.`);
    }

    const generation = this.replaceQueue(sources, index);
    const requestedPosition = finiteNonNegative(position);
    this.setState({ position: requestedPosition, duration: 0, playing: false, loading: true, error: null });
    if (!this.isActive(generation)) return;

    try {
      // Keep context creation and resume before the first network await: this is the play-gesture boundary.
      const context = this.ensureContext();
      await this.resumeContext(context);
      if (!this.isActive(generation)) return;
      const audio = await this.loadBuffer(index, generation);
      if (!this.isActive(generation)) return;
      this.startCurrent(index, audio, requestedPosition, generation);
    } catch (error) {
      if (!this.isActive(generation)) return;
      const message = errorMessage(error);
      this.setState({ playing: false, loading: false, error: message });
      throw error instanceof Error ? error : new Error(message);
    }
  }

  public pause(): void {
    if (this.disposed) return;
    const position = this.currentPlayback ? this.positionFor(this.currentPlayback) : this.state.position;
    this.invalidatePlayback(false);
    this.setState({ position, playing: false, loading: false });
  }

  public async resume(): Promise<void> {
    this.assertUsable();
    if (this.state.playing) return;
    if (this.indexUnavailable()) return;

    const generation = this.invalidatePlayback(false);
    const position = this.state.position;
    this.setState({ loading: true, error: null });
    if (!this.isActive(generation)) return;
    try {
      const context = this.ensureContext();
      await this.resumeContext(context);
      if (!this.isActive(generation)) return;
      const audio = this.currentBuffer
        ? { buffer: this.currentBuffer, mode: this.state.playbackMode ?? 'native', decodedBytes: measuredDecodedBytes(this.currentBuffer) }
        : await this.loadBuffer(this.state.index, generation);
      if (!this.isActive(generation)) return;
      this.startCurrent(this.state.index, audio, position, generation);
    } catch (error) {
      if (!this.isActive(generation)) return;
      const message = errorMessage(error);
      this.setState({ playing: false, loading: false, error: message });
      throw error instanceof Error ? error : new Error(message);
    }
  }

  public async seek(position: number): Promise<void> {
    this.assertUsable();
    if (this.indexUnavailable()) return;

    const duration = this.currentBuffer?.duration ?? this.state.duration;
    const wanted = clampPosition(position, duration);
    const wasPlaying = this.state.playing;
    const generation = this.invalidatePlayback(false);
    this.setState({ position: wanted, playing: false, loading: wasPlaying, error: wasPlaying ? null : this.state.error });
    if (!this.isActive(generation)) return;
    if (!wasPlaying) return;

    try {
      const context = this.ensureContext();
      await this.resumeContext(context);
      if (!this.isActive(generation)) return;
      const audio = this.currentBuffer
        ? { buffer: this.currentBuffer, mode: this.state.playbackMode ?? 'native', decodedBytes: measuredDecodedBytes(this.currentBuffer) }
        : await this.loadBuffer(this.state.index, generation);
      if (!this.isActive(generation)) return;
      this.startCurrent(this.state.index, audio, wanted, generation);
    } catch (error) {
      if (!this.isActive(generation)) return;
      const message = errorMessage(error);
      this.setState({ playing: false, loading: false, error: message });
      throw error instanceof Error ? error : new Error(message);
    }
  }

  public async next(): Promise<void> {
    this.assertUsable();
    if (this.indexUnavailable()) return;
    const target = this.state.index + 1 < this.queue.length
      ? this.state.index + 1
      : this.repeat === 'all' && this.queue.length > 0 ? 0 : null;
    if (target === null) return;
    await this.startManualTrack(target, 0);
  }

  public async previous(): Promise<void> {
    this.assertUsable();
    if (this.indexUnavailable()) return;
    const target = this.state.index > 0
      ? this.state.index - 1
      : this.repeat === 'all' && this.queue.length > 0 ? this.queue.length - 1 : 0;
    if (target === this.state.index) {
      await this.seek(0);
      return;
    }
    await this.startManualTrack(target, 0);
  }

  public setVolume(volume: number): void {
    if (!Number.isFinite(volume)) return;
    this.volume = Math.min(1, Math.max(0, volume));
    if (this.context && this.volumeNode) setParamValue(this.volumeNode.gain, this.volume, this.context.currentTime);
  }

  public setGain(gain: number): void {
    if (!Number.isFinite(gain) || gain < 0) return;
    this.gain = gain;
    if (this.context && this.gainNode) setParamValue(this.gainNode.gain, this.gain, this.context.currentTime);
  }

  /** Crossfade is opt-in and only affects the two already-bounded decoded buffers. */
  public setCrossfade(seconds: number): void {
    const next = Number.isFinite(seconds) ? Math.max(0, Math.min(12, seconds)) : 0;
    if (next === this.crossfadeSeconds) return;
    this.crossfadeSeconds = next;
    this.successorRevision += 1;
    this.cancelScheduledSuccessor();
    if (this.currentPlayback && this.context) {
      try { this.currentPlayback.fade.gain.cancelScheduledValues(this.context.currentTime); } catch { /* minimal contexts */ }
      setParamValue(this.currentPlayback.fade.gain, 1, this.context.currentTime);
    }
    if (this.currentPlayback) this.prefetchNext(this.currentPlayback.generation, this.successorRevision);
  }

  public setRepeat(mode: MusicRepeatMode): void {
    this.assertUsable();
    if (mode !== 'off' && mode !== 'all' && mode !== 'one') throw new Error(`Unknown repeat mode "${mode}".`);
    if (this.repeat === mode) return;
    this.repeat = mode;
    this.successorRevision += 1;
    this.cancelScheduledSuccessor();
    this.prefetched = null;
    this.abortFutureLoads();
    if (this.currentPlayback) this.prefetchNext(this.currentPlayback.generation, this.successorRevision);
  }

  /**
   * Replace only content at and after the active track. The active source must
   * remain at the same index so its already-playing AudioBufferSourceNode can
   * continue uninterrupted.
   */
  public replaceUpcoming(queue: readonly MusicSource[]): void {
    this.assertUsable();
    const index = this.state.index;
    const current = this.queue[index];
    const sources = queue.map((source) => this.copySource(source));
    if (index < 0 || !current || !sources[index] || !this.sameSource(current, sources[index])) {
      throw new Error('The replacement queue must contain the current source at the current index.');
    }

    this.successorRevision += 1;
    this.cancelScheduledSuccessor();
    this.prefetched = null;
    this.abortFutureLoads();
    this.queue = [...this.queue.slice(0, index), ...sources.slice(index)];
    this.setState({ error: null });
    if (this.currentPlayback) this.prefetchNext(this.currentPlayback.generation, this.successorRevision);
  }

  public getState(): MusicEngineState {
    return { ...this.state };
  }

  public dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.generation += 1;
    this.abortRequests();
    this.stopPositionTimer();
    this.releasePlaybackNodes();
    this.currentBuffer = null;
    this.prefetched = null;
    this.queue = [];
    this.repeat = 'off';
    this.pendingLoads.clear();
    this.state = { ...this.state, playing: false, loading: false };

    const context = this.context;
    const gainNode = this.gainNode;
    const volumeNode = this.volumeNode;
    this.context = null;
    this.gainNode = null;
    this.volumeNode = null;
    try {
      gainNode?.disconnect();
    } catch {
      // Disposal is best effort for browser-native nodes.
    }
    try {
      volumeNode?.disconnect();
    } catch {
      // Disposal is best effort for browser-native nodes.
    }
    if (context && typeof context.close === 'function') void Promise.resolve(context.close()).catch(() => undefined);
  }

  private get index(): number {
    return this.state.index;
  }

  private assertUsable(): void {
    if (this.disposed) throw new Error('MusicEngine has been disposed.');
  }

  private indexUnavailable(): boolean {
    return this.state.index < 0 || this.state.index >= this.queue.length;
  }

  private copySource(source: MusicSource): MusicSource {
    return { ...source };
  }

  private sameSource(left: MusicSource | undefined, right: MusicSource | undefined): boolean {
    if (!left || !right) return false;
    return left.id === right.id && left.url === right.url && left.fallbackUrl === right.fallbackUrl;
  }

  private replaceQueue(queue: readonly MusicSource[], index: number): number {
    const generation = this.invalidatePlayback(true);
    this.queue = queue.map((source) => this.copySource(source));
    this.state = {
      ...this.state,
      index,
      position: 0,
      duration: 0,
      playing: false,
      loading: false,
      error: null,
      playbackMode: undefined,
    };
    return generation;
  }

  private ensureContext(): AudioContext {
    if (this.context) return this.context;
    const context = this.createContext();
    try {
      const gainNode = context.createGain();
      const volumeNode = context.createGain();
      gainNode.connect(volumeNode);
      volumeNode.connect(context.destination);
      setParamValue(gainNode.gain, this.gain, context.currentTime);
      setParamValue(volumeNode.gain, this.volume, context.currentTime);
      this.context = context;
      this.gainNode = gainNode;
      this.volumeNode = volumeNode;
      return context;
    } catch (error) {
      if (typeof context.close === 'function') void Promise.resolve(context.close()).catch(() => undefined);
      throw new Error(`Unable to initialize Web Audio: ${errorMessage(error)}`);
    }
  }

  private async resumeContext(context: AudioContext): Promise<void> {
    if (context.state === 'closed') throw new Error('The Web Audio context is closed.');
    if (context.state === 'suspended' || (context.state as string) === 'interrupted') await context.resume();
    if ((context as AudioContext & { state: string }).state === 'closed') throw new Error('The Web Audio context closed before playback could start.');
  }

  private async loadBuffer(index: number, generation: number): Promise<DecodedAudio> {
    const source = this.queue[index];
    if (!source) throw new Error(`Music queue index ${index} is unavailable.`);
    const existing = this.pendingLoads.get(index);
    if (existing?.generation === generation && this.sameSource(existing.source, source)) return existing.promise;
    if (existing) {
      existing.controller.abort();
      if (this.pendingLoads.get(index) === existing) this.pendingLoads.delete(index);
    }
    const controller = new AbortController();
    this.requests.add(controller);
    const promise = this.decodeSource(source, controller.signal, generation, this.decodedBudget(index));
    const pending: PendingLoad = { generation, controller, promise, source: this.copySource(source) };
    this.pendingLoads.set(index, pending);
    void promise.then(
      () => {
        this.requests.delete(controller);
        if (this.pendingLoads.get(index) === pending) this.pendingLoads.delete(index);
      },
      () => {
        this.requests.delete(controller);
        if (this.pendingLoads.get(index) === pending) this.pendingLoads.delete(index);
      },
    );
    return promise;
  }

  private async decodeSource(source: MusicSource, signal: AbortSignal, generation: number, availableDecodedBytes: number): Promise<DecodedAudio> {
    const expected = expectedDecodedBytes(source);
    const sourceBudget = Math.min(MAX_DECODED_AUDIO_BYTES, availableDecodedBytes);
    if (expected === null) throw new Error('Audio size is not known yet. Choose streaming playback.');
    if (expected > sourceBudget) throw new DecodedMemoryError(source, sourceBudget);
    let bytes: ArrayBuffer;
    try {
      const loaded = await this.getAudio(source, signal);
      if (signal.aborted || !this.isActive(generation)) throw abortError();
      if (!isArrayBuffer(loaded)) throw new Error('The audio loader did not return an ArrayBuffer.');
      bytes = loaded;
    } catch (error) {
      if (isAbortError(error, signal)) throw error;
      throw new Error(`Unable to load audio source "${source.id}": ${errorMessage(error)}`);
    }

    if (bytes.byteLength > MAX_AUDIO_BYTES) {
      throw new Error(`Audio source "${source.id}" exceeds the ${MAX_AUDIO_BYTES} byte input limit.`);
    }
    const context = this.context;
    if (!context) throw new Error('Web Audio is unavailable.');
    let buffer: AudioBuffer;
    let nativeError: unknown;
    try {
      buffer = await context.decodeAudioData(bytes);
      if (signal.aborted || !this.isActive(generation)) throw abortError();
      validDuration(buffer, source);
      return { buffer, mode: 'native', decodedBytes: checkDecodedBudget(buffer, source, sourceBudget) };
    } catch (error) {
      if (isAbortError(error, signal)) throw error;
      if (error instanceof DecodedMemoryError) throw error;
      nativeError = error;
    }

    if (signal.aborted || !this.isActive(generation)) throw abortError();
    const fallbackUrl = source.fallbackUrl;
    if (!fallbackUrl || isPermissionError(nativeError)) {
      throw new Error(`Unable to decode audio source "${source.id}": ${errorMessage(nativeError)}`);
    }

    try {
      const fallbackSource = { ...source, url: fallbackUrl };
      const loadedFallback = await this.getAudio(fallbackSource, signal);
      if (signal.aborted || !this.isActive(generation)) throw abortError();
      if (!isArrayBuffer(loadedFallback)) throw new Error('The audio loader did not return an ArrayBuffer.');
      if (loadedFallback.byteLength > MAX_AUDIO_BYTES) {
        throw new Error(`Audio source "${source.id}" exceeds the ${MAX_AUDIO_BYTES} byte input limit.`);
      }
      const fallbackBytes = loadedFallback;
      const fallbackBuffer = await context.decodeAudioData(fallbackBytes);
      if (signal.aborted || !this.isActive(generation)) throw abortError();
      validDuration(fallbackBuffer, fallbackSource);
      return { buffer: fallbackBuffer, mode: 'fallback', decodedBytes: checkDecodedBudget(fallbackBuffer, fallbackSource, sourceBudget) };
    } catch (error) {
      if (isAbortError(error, signal)) throw error;
      if (error instanceof DecodedMemoryError) throw error;
      throw new Error(`Unable to decode audio source "${source.id}": ${errorMessage(nativeError)}; compatible fallback failed: ${errorMessage(error)}`);
    }
  }

  private startCurrent(index: number, audio: DecodedAudio, position: number, generation: number): void {
    if (!this.isActive(generation)) return;
    const context = this.context;
    const gainNode = this.gainNode;
    if (!context || !gainNode) throw new Error('Web Audio is unavailable.');
    const buffer = audio.buffer;
    const duration = validDuration(buffer, this.queue[index]);
    const offset = clampPosition(position, duration);
    const startTime = this.now();
    let node: AudioBufferSourceNode | null = null;
    let playback: Playback | null = null;
    this.scheduledNext = null;
    this.currentBuffer = buffer;
    try {
      const created = this.createSource(buffer, generation);
      node = created.node;
      playback = { ...created, buffer, mode: audio.mode, index, startTime, offset, generation };
      this.currentPlayback = playback;
      node.start(startTime, offset);
    } catch (error) {
      if (this.currentPlayback === playback) this.currentPlayback = null;
      if (node) this.releaseNode(node, false);
      throw new Error(`Unable to start audio source "${this.queue[index].id}": ${errorMessage(error)}`);
    }
    if (this.prefetched?.index === index) this.prefetched = null;
    this.setState({ index, position: offset, duration, playing: true, loading: false, error: null, playbackMode: audio.mode });
    if (!playback || !this.isActive(generation) || this.currentPlayback !== playback) return;
    this.startPositionTimer();
    this.prefetchNext(generation);
  }

  private createSource(buffer: AudioBuffer, generation: number): Pick<Playback, 'node' | 'fade'> {
    const context = this.context;
    const gainNode = this.gainNode;
    if (!context || !gainNode) throw new Error('Web Audio is unavailable.');
    const node = context.createBufferSource();
    const fade = context.createGain();
    try {
      node.buffer = buffer;
      node.connect(fade);
      fade.connect(gainNode);
      setParamValue(fade.gain, 1, context.currentTime);
      node.onended = () => this.handleSourceEnded(node, generation);
      return { node, fade };
    } catch (error) {
      this.releaseNode({ node, fade } as Playback, false);
      throw error;
    }
  }

  private prefetchNext(generation: number, successorRevision = this.successorRevision): void {
    if (!this.isActive(generation) || !this.currentPlayback) return;
    const current = this.currentPlayback;
    const nextIndex = this.successorIndex(current.index);
    if (nextIndex === null) return;
    const source = this.queue[nextIndex];
    if (!source) return;

    if (nextIndex === current.index) {
      this.prefetched = { index: nextIndex, source: this.copySource(source), audio: { buffer: current.buffer, mode: current.mode, decodedBytes: measuredDecodedBytes(current.buffer) } };
      this.scheduleNextIfReady(generation, successorRevision);
      return;
    }
    if (this.prefetched?.index === nextIndex && this.sameSource(this.prefetched.source, source)) {
      this.scheduleNextIfReady(generation, successorRevision);
      return;
    }
    this.prefetched = null;

    const promise = this.loadBuffer(nextIndex, generation);
    void promise.then(
      (audio) => {
        if (!this.isActive(generation) || successorRevision !== this.successorRevision || this.currentPlayback !== current) return;
        if (this.successorIndex(current.index) !== nextIndex || !this.sameSource(this.queue[nextIndex], source)) return;
        this.prefetched = { index: nextIndex, source: this.copySource(source), audio };
        this.scheduleNextIfReady(generation, successorRevision);
      },
      (error) => {
        if (!this.isActive(generation) || successorRevision !== this.successorRevision || this.currentPlayback !== current) return;
        // Keep playing the current buffer, but expose a successor that cannot be
        // prepared so the UI no longer claims this transition will be gapless.
        this.setState({ error: errorMessage(error) });
      },
    );
  }

  private scheduleNextIfReady(generation: number, successorRevision = this.successorRevision): void {
    const current = this.currentPlayback;
    const next = this.prefetched;
    const context = this.context;
    const expectedIndex = current ? this.successorIndex(current.index) : null;
    if (
      !this.isActive(generation) ||
      successorRevision !== this.successorRevision ||
      !current ||
      !next ||
      !context ||
      expectedIndex === null ||
      next.index !== expectedIndex ||
      !this.sameSource(next.source, this.queue[expectedIndex]) ||
      this.scheduledNext
    ) return;

    const boundary = current.startTime + current.buffer.duration - current.offset;
    if (this.now() >= boundary) {
      this.finishCurrent(current, generation);
      return;
    }

    let node: AudioBufferSourceNode | null = null;
    try {
      const overlap = Math.min(this.crossfadeSeconds, Math.max(0, current.buffer.duration - current.offset) / 2, next.audio.buffer.duration / 2);
      const startTime = boundary - overlap;
      const created = this.createSource(next.audio.buffer, generation);
      node = created.node;
      const playback: Playback = { ...created, buffer: next.audio.buffer, mode: next.audio.mode, index: next.index, startTime, offset: 0, generation };
      if (overlap > 0) {
        this.ramp(current.fade.gain, 1, 0, startTime, boundary);
        this.ramp(playback.fade.gain, 0, 1, startTime, boundary);
      }
      this.scheduledNext = playback;
      node.start(startTime, 0);
    } catch (error) {
      this.scheduledNext = null;
      if (node) this.releaseNode(node, false);
      this.setState({ error: `Unable to schedule next audio source "${this.queue[next.index].id}": ${errorMessage(error)}` });
    }
  }

  private handleSourceEnded(node: AudioBufferSourceNode, generation: number): void {
    if (!this.isActive(generation)) return;
    const current = this.currentPlayback;
    if (current?.node === node) {
      this.finishCurrent(current, generation);
      return;
    }
    const next = this.scheduledNext;
    if (next?.node === node && this.now() >= next.startTime) this.promoteScheduled(next, generation);
  }

  private successorIndex(index: number): number | null {
    if (index < 0 || index >= this.queue.length) return null;
    if (this.repeat === 'one') return index;
    if (index + 1 < this.queue.length) return index + 1;
    return this.repeat === 'all' && this.queue.length > 0 ? 0 : null;
  }

  private finishCurrent(current: Playback, generation: number): void {
    if (!this.isActive(generation) || this.currentPlayback !== current) return;
    const scheduled = this.scheduledNext;
    if (scheduled) {
      this.promoteScheduled(scheduled, generation);
      return;
    }

    this.currentPlayback = null;
    this.scheduledNext = null;
    this.releaseNode(current, false);
    this.currentBuffer = null;
    this.stopPositionTimer();
    const nextIndex = this.successorIndex(current.index);
    const hasNext = nextIndex !== null;
    this.setState({ position: current.buffer.duration, playing: false, loading: hasNext });
    if (!this.isActive(generation)) return;
    if (!hasNext) return;

    const ready = this.prefetched?.index === nextIndex && this.sameSource(this.prefetched.source, this.queue[nextIndex]) ? this.prefetched : null;
    if (ready) {
      this.prefetched = null;
      this.startCurrent(nextIndex, ready.audio, 0, generation);
      return;
    }

    const successorRevision = this.successorRevision;
    const promise = this.loadBuffer(nextIndex, generation);
    void promise.then(
      (audio) => {
        if (!this.isActive(generation) || successorRevision !== this.successorRevision || this.currentPlayback || this.index !== current.index) return;
        this.startCurrent(nextIndex, audio, 0, generation);
      },
      (error) => {
        if (!this.isActive(generation) || successorRevision !== this.successorRevision) return;
        this.setState({ loading: false, error: errorMessage(error) });
      },
    );
  }

  private promoteScheduled(next: Playback, generation: number): void {
    if (!this.isActive(generation) || this.scheduledNext !== next) return;
    const old = this.currentPlayback;
    this.currentPlayback = null;
    this.scheduledNext = null;
    if (old) this.releaseNode(old, false);
    this.currentBuffer = next.buffer;
    this.prefetched = null;
    this.currentPlayback = next;
    this.setState({ index: next.index, position: Math.max(0, this.now() - next.startTime), duration: next.buffer.duration, playing: true, loading: false, error: null, playbackMode: next.mode });
    if (!this.isActive(generation) || this.currentPlayback !== next) return;
    this.startPositionTimer();
    this.prefetchNext(generation);
  }

  private async startManualTrack(index: number, position: number): Promise<void> {
    const reusable = this.prefetched?.index === index && this.sameSource(this.prefetched.source, this.queue[index]) ? this.prefetched.audio : null;
    const generation = this.invalidatePlayback(true);
    this.setState({ index, position, duration: 0, playing: false, loading: true, error: null, playbackMode: undefined });
    if (!this.isActive(generation)) return;
    try {
      const context = this.ensureContext();
      await this.resumeContext(context);
      if (!this.isActive(generation)) return;
      const audio = reusable ?? await this.loadBuffer(index, generation);
      if (!this.isActive(generation)) return;
      this.startCurrent(index, audio, position, generation);
    } catch (error) {
      if (!this.isActive(generation)) return;
      const message = errorMessage(error);
      this.setState({ playing: false, loading: false, error: message });
      throw error instanceof Error ? error : new Error(message);
    }
  }

  private positionFor(playback: Playback): number {
    const elapsed = Math.max(0, this.now() - playback.startTime);
    return Math.min(playback.buffer.duration, Math.max(0, playback.offset + elapsed));
  }

  private decodedBudget(index: number): number {
    const currentIndex = this.currentPlayback?.index ?? this.state.index;
    if (!this.currentBuffer || currentIndex === index) return MAX_DECODED_AUDIO_BYTES;
    const retained = measuredDecodedBytes(this.currentBuffer);
    return retained === null ? MAX_DECODED_AUDIO_BYTES : Math.min(MAX_DECODED_AUDIO_BYTES, Math.max(0, MAX_GAPLESS_AUDIO_BYTES - retained));
  }

  private now(): number {
    const currentTime = this.context?.currentTime ?? 0;
    return Number.isFinite(currentTime) ? currentTime : 0;
  }

  private startPositionTimer(): void {
    if (this.positionTimer !== null) return;
    this.positionTimer = globalThis.setInterval(() => {
      if (!this.state.playing || !this.currentPlayback || this.disposed) return;
      this.setState({ position: this.positionFor(this.currentPlayback) });
    }, POSITION_UPDATE_MS);
  }

  private stopPositionTimer(): void {
    if (this.positionTimer === null) return;
    globalThis.clearInterval(this.positionTimer);
    this.positionTimer = null;
  }

  private cancelScheduledSuccessor(): void {
    const scheduled = this.scheduledNext;
    this.scheduledNext = null;
    if (scheduled) this.releaseNode(scheduled, true);
  }

  private abortFutureLoads(): void {
    const currentIndex = this.currentPlayback?.index ?? this.state.index;
    for (const [index, pending] of this.pendingLoads) {
      if (index === currentIndex) continue;
      pending.controller.abort();
      if (this.pendingLoads.get(index) === pending) this.pendingLoads.delete(index);
    }
  }

  private invalidatePlayback(clearBuffers: boolean): number {
    this.generation += 1;
    this.successorRevision += 1;
    this.abortRequests();
    this.stopPositionTimer();
    this.releasePlaybackNodes();
    if (clearBuffers) {
      this.currentBuffer = null;
      this.prefetched = null;
    }
    return this.generation;
  }

  private abortRequests(): void {
    for (const controller of this.requests) controller.abort();
    this.requests.clear();
    this.pendingLoads.clear();
  }

  private releasePlaybackNodes(): void {
    const current = this.currentPlayback;
    const next = this.scheduledNext;
    this.currentPlayback = null;
    this.scheduledNext = null;
    if (current) this.releaseNode(current, true);
    if (next && next.node !== current?.node) this.releaseNode(next, true);
  }

  private releaseNode(playback: Pick<Playback, 'node' | 'fade'> | AudioBufferSourceNode, stop: boolean): void {
    const node = 'node' in playback ? playback.node : playback;
    const fade = 'node' in playback ? playback.fade : null;
    node.onended = null;
    if (stop) {
      try {
        node.stop();
      } catch {
        // An already-ended source is already released for our purposes.
      }
    }
    try {
      node.disconnect();
    } catch {
      // Browser-native disconnect is idempotent; minimal test nodes may omit it.
    }
    try { fade?.disconnect(); } catch { /* best effort */ }
  }

  private ramp(param: AudioParam, from: number, to: number, start: number, end: number): void {
    try { param.cancelScheduledValues(start); } catch { /* minimal contexts may not implement it */ }
    setParamValue(param, from, start);
    if (typeof param.linearRampToValueAtTime === 'function') {
      try { param.linearRampToValueAtTime(to, end); return; } catch { /* fall through */ }
    }
    setParamValue(param, to, end);
  }

  private isActive(generation: number): boolean {
    return !this.disposed && this.generation === generation;
  }

  private setState(patch: Partial<MusicEngineState>): void {
    const next = { ...this.state, ...patch };
    if (
      next.index === this.state.index &&
      next.position === this.state.position &&
      next.duration === this.state.duration &&
      next.playing === this.state.playing &&
      next.loading === this.state.loading &&
      next.error === this.state.error &&
      next.playbackMode === this.state.playbackMode
    ) return;
    this.state = next;
    this.onChange({ ...next });
  }
}
