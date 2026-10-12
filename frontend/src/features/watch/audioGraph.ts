import type { MuteRange } from '../../types';

/** How far ahead the Web Audio mute envelope is planned; `schedule()` runs on every timeupdate (~250 ms). */
export const SCHEDULE_AHEAD_SECONDS = 1;
/** The element fallback only acts on timeupdate, so it mutes this early. */
export const ELEMENT_LOOKAHEAD_SECONDS = 0.3;

export type AudioGraphMode = 'webaudio' | 'element';

/**
 * The one audio path per media element: loudness normalization × profanity mute, never re-encoded.
 * `webaudio` routes the element through one GainNode. `element` (no AudioContext, iOS native HLS,
 * cross-origin media) mutes with `media.muted` and only attenuates, through `media.volume`.
 */
export interface AudioGraph {
  readonly mode: AudioGraphMode;
  /** Linear gain from `loudnessGain(options, uiPref(uiPrefs, 'normalize_loudness', true))` (localPlayer.tsx); 1 = off. */
  setLoudnessGain(gain: number): void;
  /** From `getMuteRanges`; `[]` turns muting off. */
  setMuteRanges(ranges: readonly MuteRange[]): void;
  /** The member's volume (0..1). Call this instead of writing `media.volume` so the fallback can attenuate. */
  setVolume(volume: number): void;
  /** Call from the play gesture: a suspended context plays silence. */
  resume(): Promise<void>;
  /** Re-plan the next second of gain; call on play, pause, seeked, ratechange and timeupdate. */
  schedule(): void;
}

export type AudioGraphOptions = {
  /** Test seam; the default builds an AudioContext unless Web Audio is unreliable for this element. */
  createContext?: (media: HTMLMediaElement) => AudioContext | null;
  /** Called once if the context's clock stalls after play and the graph was bypassed (see `STALL_CHECK_MS`). */
  onBypass?: () => void;
};

/** A running context advances ~this much audio time per real second; less than STALL_MIN_ADVANCE over it means a dead sink. */
export const STALL_CHECK_MS = 500;
const STALL_MIN_ADVANCE = 0.1;

const graphs = new WeakMap<HTMLMediaElement, AudioGraph>();

function linear(gain: number): number {
  return Number.isFinite(gain) && gain >= 0 ? gain : 1;
}

export function inMuteRange(time: number, ranges: readonly MuteRange[]): boolean {
  return ranges.some((range) => time >= range.start_seconds && time < range.end_seconds);
}

/** Web Audio outputs silence for cross-origin media without CORS; blob: (MSE) and data: sources are fine. */
function crossOrigin(media: HTMLMediaElement): boolean {
  const src = media.currentSrc || media.src;
  if (!src || src.startsWith('blob:') || src.startsWith('data:')) return false;
  try {
    return new URL(src, globalThis.location?.href).origin !== globalThis.location?.origin;
  } catch {
    return true;
  }
}

function defaultContext(media: HTMLMediaElement): AudioContext | null {
  const Context = globalThis.AudioContext ?? (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Context) return null;
  // iOS plays HLS natively (no MediaSource); Web Audio from such an element is unreliable there.
  const nativeHlsOnly = !('MediaSource' in globalThis) && !('ManagedMediaSource' in globalThis) && media.canPlayType('application/vnd.apple.mpegurl') !== '';
  return nativeHlsOnly ? null : new Context();
}

function webAudioGraph(media: HTMLMediaElement, context: AudioContext, onBypass?: () => void): AudioGraph | null {
  let node: GainNode;
  try {
    node = context.createGain();
    // Throws when the element already feeds another context; the WeakMap prevents that for ours.
    context.createMediaElementSource(media).connect(node).connect(context.destination);
  } catch {
    void context.close();
    return null;
  }
  const gain = node.gain;
  let base = 1;
  let ranges: readonly MuteRange[] = [];
  const schedule = () => {
    const now = context.currentTime;
    const time = media.currentTime;
    const rate = media.playbackRate || 1;
    gain.cancelScheduledValues(now);
    gain.setValueAtTime(inMuteRange(time, ranges) ? 0 : base, now);
    if (media.paused) return;
    for (const range of ranges) {
      if (range.end_seconds <= time || range.start_seconds > time + SCHEDULE_AHEAD_SECONDS * rate) continue;
      if (range.start_seconds > time) gain.setValueAtTime(0, now + (range.start_seconds - time) / rate);
      gain.setValueAtTime(base, now + (range.end_seconds - time) / rate);
    }
  };
  // A dead audio sink (some HDMI/TV setups) leaves the context 'running' with a frozen clock, and a captured element
  // then starves on its first frame. Closing the context lets Chromium detach its source node and play the element directly.
  let bypass: AudioGraph | null = null;
  let checking = false;
  const checkClock = async () => {
    if (checking || bypass || context.state !== 'running') return;
    checking = true;
    const started = context.currentTime;
    await new Promise((resolve) => setTimeout(resolve, STALL_CHECK_MS));
    checking = false;
    if (bypass || media.paused || media.ended || context.currentTime - started >= STALL_MIN_ADVANCE) return;
    void context.close();
    bypass = elementGraph(media);
    bypass.setLoudnessGain(base);
    bypass.setMuteRanges(ranges);
    onBypass?.();
  };
  return {
    get mode() {
      return bypass ? 'element' : 'webaudio';
    },
    setLoudnessGain: (next) => {
      base = linear(next);
      if (bypass) bypass.setLoudnessGain(next);
      else schedule();
    },
    setMuteRanges: (next) => {
      ranges = next;
      if (bypass) bypass.setMuteRanges(next);
      else schedule();
    },
    setVolume: (volume) => {
      if (bypass) bypass.setVolume(volume);
      else media.volume = volume;
    },
    resume: async () => {
      if (bypass) return;
      if (context.state === 'suspended') await context.resume();
      void checkClock();
    },
    schedule: () => (bypass ? bypass.schedule() : schedule()),
  };
}

function elementGraph(media: HTMLMediaElement): AudioGraph {
  let attenuation = 1;
  let volume = media.volume;
  let ranges: readonly MuteRange[] = [];
  let mutedByGraph = false;
  const schedule = () => {
    const time = media.currentTime;
    const want = inMuteRange(time, ranges) || inMuteRange(time + ELEMENT_LOOKAHEAD_SECONDS, ranges);
    if (want && !media.muted) {
      media.muted = true;
      mutedByGraph = true;
    } else if (!want && mutedByGraph) {
      // a member who unmutes inside a range is re-muted on the next tick; track intent if that confuses.
      media.muted = false;
      mutedByGraph = false;
    }
  };
  return {
    mode: 'element',
    setLoudnessGain: (next) => {
      attenuation = Math.min(1, linear(next)); // media.volume cannot exceed 1: attenuation only
      media.volume = volume * attenuation;
    },
    setMuteRanges: (next) => {
      ranges = next;
      schedule();
    },
    setVolume: (next) => {
      volume = next;
      media.volume = next * attenuation;
    },
    resume: () => Promise.resolve(),
    schedule,
  };
}

/** The element's one graph, built on first use (call after `src` is set, inside or after a play gesture). */
export function audioGraphFor(media: HTMLMediaElement, options: AudioGraphOptions = {}): AudioGraph {
  const existing = graphs.get(media);
  if (existing) return existing;
  const context = crossOrigin(media) ? null : (options.createContext ?? defaultContext)(media);
  const graph = (context && webAudioGraph(media, context, options.onBypass)) || elementGraph(media);
  graphs.set(media, graph);
  return graph;
}
