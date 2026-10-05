import {
  createContext,
  forwardRef,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { LiveBadge } from './features/gallery/LiveBadge';
import { exitFullscreen, FullscreenHostContext, fullscreenElement, inPictureInPicture, onFullscreenChange, placeMedia, releaseMediaElement, requestFullscreenOn, takeMediaElement } from './fullscreenHost';
import { Captions, Maximize, Minimize, Pause, PictureInPicture2, Play, RotateCcw, Volume2, VolumeX } from 'lucide-react';
import { type AudioGraph, audioGraphFor } from './features/watch/audioGraph';
import type { MuteRange } from './types';
import { captionStyle, DEFAULT_CAPTIONS, type CaptionPrefs } from './features/watch/captionPrefs';
import './features/watch/player.css';

export type LuminaPlayerState = 'loading' | 'ready' | 'unsupported' | 'failed';

/** A WebVTT subtitle track; `id` is the server's subtitle track id and becomes the TextTrack id. */
export type LuminaPlayerTrack = {
  id: string;
  label: string;
  language?: string | null;
  src: string;
  /**
   * Renders `<track default>`. Seam: a session restart remounts the media element and skips
   * onLoadedMetadata, so a caller that wants a chosen text track to survive a restart marks it
   * default rather than relying on showTextTrack, which only re-applies in place.
   */
  default?: boolean;
};

/** Loudness × mute for Lumina's own Library media, applied by the per-element audio graph. */
export type PlayerAudio = { gain: number; muteRanges: readonly MuteRange[] };

/**
 * The caller owns how a source is acquired. In particular, remote callers should
 * supply only a Lumina backend URL here, never an upstream provider URL.
 */
export type LuminaPlayerSource = {
  id?: string;
  kind: 'video' | 'audio';
  src: string | null;
  poster?: string | null;
  seekable?: boolean;
  /** A live edge: show a Live state, and omit VOD duration and seeking. */
  live?: boolean;
  autoPlay?: boolean;
  crossOrigin?: 'anonymous' | 'use-credentials';
  nativeTitle?: string;
  state?: LuminaPlayerState;
  message?: string;
  /** Subtitle tracks; cue times are absolute media time (hls.js timelineOffset keeps them aligned in sessions). */
  tracks?: LuminaPlayerTrack[];
  /** May return a refreshed descriptor without coupling the shell to a backend API. */
  onRefresh?: () => Promise<LuminaPlayerSource | void> | LuminaPlayerSource | void;
  /** Called after this source is replaced or the shell unmounts. */
  onRelease?: () => Promise<void> | void;
};

/** Caller-owned content for future playback features; the shell owns their placement. */
export type LuminaPlayerExtensions = {
  quality?: ReactNode;
  chapters?: ReactNode;
  theater?: ReactNode;
  /** Above the controls; stays visible when the controls hide (skip, up next). */
  overlay?: ReactNode;
  /** End of the control row; when supplied it owns subtitles, so the Captions toggle and `c` step aside. */
  menu?: ReactNode;
};

type LuminaPlayerProps = {
  source: LuminaPlayerSource;
  title: string;
  className?: string;
  /** Library media only: linear loudness gain (loudnessGain) and the member's mute ranges. */
  audio?: PlayerAudio;
  extensions?: LuminaPlayerExtensions;
  mediaRef?: Ref<HTMLMediaElement | null>;
  /** After a stalled audio clock swapped in a fresh element (`at` = where to resume): re-attach hls.js/dash.js to `mediaRef`'s new element. */
  onMediaReplaced?: (at: number) => void;
  onCanPlay?: (media: HTMLMediaElement) => void;
  onEnded?: (media: HTMLMediaElement) => void;
  /** Return true when the caller owns recovery and the media element should stay mounted. */
  onError?: (media: HTMLMediaElement) => boolean | void;
  onLoadedMetadata?: (media: HTMLMediaElement) => void;
  onPause?: (media: HTMLMediaElement) => void;
  onSeeked?: (media: HTMLMediaElement) => void;
  onTimeUpdate?: (media: HTMLMediaElement) => void;
};

/**
 * Member-level player preferences and page actions, provided by the Watch
 * surface so the local/remote wrappers need not thread them through.
 */
export type PlayerPreferences = {
  /** Persisted per member in `ui_prefs.player_volume`. */
  volume?: number;
  onVolumeChange?: (volume: number) => void;
  /** Shift+N: play the next queued/up-next item. */
  onNext?: () => void;
  /** Shortcuts also apply while nothing is focused (off for the docked mini-player). */
  globalShortcuts?: boolean;
  /** Sub-line under the title over the video ("S2 · E4" or the channel). */
  subtitle?: string;
  /** Theater layout: the title scrim shows without fullscreen. */
  theater?: boolean;
  /** Caption size and background (`ui_prefs.captions`, app polish 10.2). */
  captions?: CaptionPrefs;
};

export const PlayerPreferencesContext = createContext<PlayerPreferences>({});

export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

/** Text-entry targets keep every key; sliders and buttons keep only the keys they natively use. */
function shortcutOwnedByTarget(target: HTMLElement, key: string): boolean {
  if (target.isContentEditable || target.closest('textarea, select, [contenteditable="true"]')) return true;
  const input = target.closest('input');
  if (input) return input.type !== 'range' || key.startsWith('arrow') || key === 'home' || key === 'end' || key.startsWith('page');
  return key === ' ' && Boolean(target.closest('button, a, summary'));
}

function sourceKey(source: LuminaPlayerSource): string {
  return source.id || `${source.kind}:${source.src || 'unavailable'}`;
}

function playerMessage(source: LuminaPlayerSource, state: LuminaPlayerState): string {
  if (source.message) return source.message;
  if (state === 'unsupported') return 'This media cannot be played in this browser.';
  if (state === 'failed') return 'Playback could not continue. Try refreshing the stream.';
  return 'Loading media…';
}

export function formatPlayerTime(seconds: number | null | undefined): string {
  const safe = Number.isFinite(seconds) && (seconds || 0) > 0 ? Math.floor(seconds as number) : 0;
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remainder = safe % 60;
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, '0')}:${remainder.toString().padStart(2, '0')}`
    : `${minutes}:${remainder.toString().padStart(2, '0')}`;
}

/** End of the range containing `time` (buffered), or of the last range (seekable). */
function rangeEnd(ranges: TimeRanges | undefined, time?: number): number | null {
  const length = ranges?.length ?? 0;
  for (let index = length - 1; index >= 0; index -= 1) {
    if (time === undefined || (ranges!.start(index) <= time && time <= ranges!.end(index))) return ranges!.end(index);
  }
  return null;
}

// fixed threshold; hls.js already trails the edge by ~3 segments, so 20s means the member fell behind.
const LIVE_BEHIND_SECONDS = 20;
const MEDIA_EVENTS = ['canplay', 'ended', 'error', 'loadedmetadata', 'pause', 'seeked', 'play', 'playing', 'progress', 'ratechange', 'timeupdate', 'waiting', 'click', 'pointerdown'] as const;
type MediaEvent = Event & { currentTarget: HTMLMediaElement };
type PresentationVideo = HTMLVideoElement & { webkitSupportsPresentationMode?: (mode: string) => boolean; webkitSetPresentationMode?: (mode: string) => void };

function setAttr(element: Element, name: string, value: string | null) {
  if (element.getAttribute(name) === value) return;
  if (value === null) element.removeAttribute(name);
  else element.setAttribute(name, value);
}

const SEEK_KEY_DELTAS: Record<string, number> = { arrowleft: -5, arrowdown: -5, arrowright: 5, arrowup: 5, pagedown: -10, pageup: 10 };

export const LuminaPlayer = forwardRef<HTMLMediaElement, LuminaPlayerProps>(function LuminaPlayer(
  { audio, className, extensions, mediaRef, onCanPlay, onMediaReplaced, onEnded, onError, onLoadedMetadata, onPause, onSeeked, onTimeUpdate, source, title },
  forwardedRef,
) {
  const prefs = useContext(PlayerPreferencesContext);
  const nativeRef = useRef<HTMLMediaElement | null>(null);
  const shellRef = useRef<HTMLElement | null>(null);
  const controlsRef = useRef<HTMLDivElement | null>(null);
  const controlsTimerRef = useRef<ReturnType<typeof globalThis.setTimeout> | null>(null);
  const volumeCommitRef = useRef<ReturnType<typeof globalThis.setTimeout> | null>(null);
  const suppressMediaClickRef = useRef(false);
  const [replacement, setReplacement] = useState<LuminaPlayerSource | null>(null);
  const [state, setState] = useState<LuminaPlayerState>(source.state || (source.src ? 'loading' : 'unsupported'));
  const [isPlaying, setIsPlaying] = useState(false);
  const [ended, setEnded] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [volume, setVolume] = useState(prefs.volume ?? 1);
  const [rate, setRate] = useState(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);
  const [buffered, setBuffered] = useState(0);
  const [liveBehind, setLiveBehind] = useState(false);
  const [hasCaptions, setHasCaptions] = useState(false);
  const [captionsOn, setCaptionsOn] = useState(false);
  // Captions: size follows the player's height, so they scale with the mini card, theater and fullscreen.
  const captionPrefs = prefs.captions ?? DEFAULT_CAPTIONS;
  const captionVars = captionStyle(captionPrefs);
  const captionSupportsVariables = typeof CSS !== 'undefined' && typeof CSS.supports === 'function' && CSS.supports('selector(::cue)') && CSS.supports('color', 'var(--x)');
  useEffect(() => {
    const root = shellRef.current;
    if (!root || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => {
      root.style.setProperty('--caption-base', `${Math.round(entry.contentRect.height * 0.045)}px`);
    });
    observer.observe(root);
    return () => observer.disconnect();
  }, []);
  const fullscreenHost = useContext(FullscreenHostContext);
  // A player mounted for the next episode starts inside the fullscreen its predecessor entered.
  const [isFullscreen, setIsFullscreen] = useState(() => Boolean(fullscreenElement()));
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  // One graph per element (they are cached), built on that element's first play; a new element starts without one.
  const graphRef = useRef<{ media: HTMLMediaElement; graph: AudioGraph } | null>(null);
  const audioRef = useRef(audio);
  audioRef.current = audio;
  const graph = () => (graphRef.current && graphRef.current.media === nativeRef.current ? graphRef.current.graph : null);
  /** Volume goes through the graph once it exists, so its element fallback can attenuate for loudness. */
  function writeVolume(media: HTMLMediaElement, next: number) {
    if (graphRef.current?.media === media) graphRef.current.graph.setVolume(next);
    else media.volume = next;
  }
  const graphBypassedRef = useRef(false);
  const resumeAtRef = useRef<number | null>(null);
  const replacedRef = useRef(false);
  const [mediaEpoch, setMediaEpoch] = useState(0);
  function replaceStalledMedia(media: HTMLMediaElement) {
    graphBypassedRef.current = true;
    resumeAtRef.current = media.currentTime;
    replacedRef.current = true;
    setMediaEpoch((epoch) => epoch + 1);
    setNotice('Loudness levelling is off: this device\'s audio output is not responding.');
  }
  function startGraph(media: HTMLMediaElement) {
    const settings = audioRef.current;
    if (!settings) return;
    if (graphRef.current?.media !== media) {
      // After a stalled audio clock the element is captured by a dead context for good; the replacement plays without Web Audio.
      const built = audioGraphFor(media, graphBypassedRef.current ? { createContext: () => null } : { onBypass: () => replaceStalledMedia(media) });
      graphRef.current = { media, graph: built };
      built.setVolume(media.volume);
      built.setLoudnessGain(settings.gain);
      built.setMuteRanges(settings.muteRanges);
    }
    void graphRef.current.graph.resume();
    graphRef.current.graph.schedule();
  }
  useEffect(() => {
    const current = graph();
    if (!current || !audio) return;
    current.setLoudnessGain(audio.gain);
    current.setMuteRanges(audio.muteRanges);
  }, [audio?.gain, audio?.muteRanges]); // eslint-disable-line react-hooks/exhaustive-deps
  const outerSourceKey = sourceKey(source);
  const activeSource = replacement || source;
  const activeSourceKey = sourceKey(activeSource);

  useEffect(() => {
    setReplacement(null);
    setState(source.state || (source.src ? 'loading' : 'unsupported'));
    setIsPlaying(false);
    setEnded(false);
    setCurrentTime(0);
    setDuration(null);
    setBuffered(0);
    setLiveBehind(false);
    setHasCaptions(false);
    setNotice(null);
  }, [outerSourceKey, source.src, source.state]);

  useEffect(() => () => {
    if (controlsTimerRef.current !== null) globalThis.clearTimeout(controlsTimerRef.current);
    if (volumeCommitRef.current !== null) globalThis.clearTimeout(volumeCommitRef.current);
  }, []);

  // The member's saved volume can arrive after mount (settings hydrate) or change on another device.
  useEffect(() => {
    if (prefs.volume === undefined || volumeCommitRef.current !== null) return;
    setVolume(prefs.volume);
    if (nativeRef.current) writeVolume(nativeRef.current, prefs.volume);
  }, [prefs.volume]);

  useEffect(() => () => { void source.onRelease?.(); }, [outerSourceKey]);

  useEffect(() => {
    return onFullscreenChange(() => setIsFullscreen(Boolean(fullscreenElement())));
  }, []);

  function assignMedia(node: HTMLMediaElement | null) {
    nativeRef.current = node;
    if (node) {
      writeVolume(node, volume);
      node.muted = isMuted;
      // A new load resets playbackRate to the default, so set both.
      node.defaultPlaybackRate = rate;
      node.playbackRate = rate;
    }
    if (typeof forwardedRef === 'function') forwardedRef(node);
    else if (forwardedRef) forwardedRef.current = node;
    if (typeof mediaRef === 'function') mediaRef(node);
    else if (mediaRef) mediaRef.current = node;
  }

  function updateTimeline(media: HTMLMediaElement) {
    const time = Number.isFinite(media.currentTime) ? media.currentTime : 0;
    const total = Number.isFinite(media.duration) ? media.duration : null;
    setCurrentTime(time);
    setDuration(total);
    setBuffered(total ? Math.min(1, (rangeEnd(media.buffered, time) ?? 0) / total) : 0);
    if (activeSource.live) {
      const edge = rangeEnd(media.seekable);
      setLiveBehind(edge !== null && edge - time > LIVE_BEHIND_SECONDS);
    }
  }

  async function play() {
    const media = nativeRef.current;
    if (!media) return;
    setNotice(null);
    try {
      await media.play();
      setIsPlaying(true);
    } catch (error) {
      const name = (error as { name?: string } | null)?.name;
      // A blocked autoplay is a policy, not a broken stream: keep the media and ask for a press.
      if (name === 'NotAllowedError') setNotice('The browser blocked playback. Press Play to start.');
      else if (name !== 'AbortError') setState('failed');
    }
  }

  function pause() {
    nativeRef.current?.pause();
    setIsPlaying(false);
  }

  function togglePlay() {
    void (isPlaying ? pause() : play());
  }

  function seek(next: number) {
    const media = nativeRef.current;
    if (!media) return;
    const target = Math.max(0, Math.min(duration ?? Number.POSITIVE_INFINITY, next));
    media.currentTime = target;
    setCurrentTime(target);
    if (duration === null || target < duration) setEnded(false);
  }

  function goLive() {
    const media = nativeRef.current;
    const edge = rangeEnd(media?.seekable);
    if (media && edge !== null) media.currentTime = Math.max(0, edge - 3);
    setLiveBehind(false);
    void play();
  }

  function changeVolume(next: number) {
    const media = nativeRef.current;
    if (!media) return;
    const clamped = Math.round(Math.max(0, Math.min(1, next)) * 100) / 100;
    writeVolume(media, clamped);
    media.muted = clamped === 0;
    setVolume(clamped);
    setIsMuted(media.muted);
    // Persist once the member settles, not on every slider tick.
    if (volumeCommitRef.current !== null) globalThis.clearTimeout(volumeCommitRef.current);
    const commit = prefs.onVolumeChange;
    volumeCommitRef.current = globalThis.setTimeout(() => {
      volumeCommitRef.current = null;
      commit?.(clamped);
    }, 500);
  }

  function toggleMute() {
    const media = nativeRef.current;
    if (!media) return;
    media.muted = !media.muted;
    setIsMuted(media.muted);
  }

  function changeRate(next: number) {
    const media = nativeRef.current;
    setRate(next);
    if (!media) return;
    media.defaultPlaybackRate = next;
    media.playbackRate = next;
  }

  function toggleCaptions() {
    const tracks = nativeRef.current?.textTracks;
    if (!tracks?.length) return;
    const next = !captionsOn;
    for (let index = 0; index < tracks.length; index += 1) tracks[index].mode = next && index === 0 ? 'showing' : 'hidden';
    setCaptionsOn(next);
  }

  async function toggleFullscreen() {
    try {
      if (fullscreenElement()) {
        await exitFullscreen();
        return;
      }
      const media = nativeRef.current as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
      // The Watch host (when there is one) outlives this player, so the next episode stays fullscreen.
      const target = fullscreenHost?.current ?? shellRef.current;
      if (target && await requestFullscreenOn(target)) return;
      // iPhone Safari only offers fullscreen on the video element itself; the next episode then plays inline.
      if (media?.webkitEnterFullscreen) media.webkitEnterFullscreen();
      else setNotice('Fullscreen is not available in this browser.');
    } catch {
      setNotice('The browser did not allow fullscreen.');
    }
  }

  async function togglePictureInPicture() {
    const media = nativeRef.current;
    try {
      const video = media as PresentationVideo | null;
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else if (video?.requestPictureInPicture) await video.requestPictureInPicture();
      // Safari without the standard API (older iPad): its own presentation mode.
      else if (video?.webkitSetPresentationMode) video.webkitSetPresentationMode(inPictureInPicture(video) ? 'inline' : 'picture-in-picture');
    } catch {
      setNotice('The browser did not allow picture-in-picture.');
    }
  }

  function clearControlsTimer() {
    if (controlsTimerRef.current === null) return;
    globalThis.clearTimeout(controlsTimerRef.current);
    controlsTimerRef.current = null;
  }

  function scheduleControlsHide() {
    clearControlsTimer();
    if (!isPlaying || state !== 'ready') return;
    controlsTimerRef.current = globalThis.setTimeout(() => {
      // Never hide mid-interaction: a focused control keeps the bar up.
      if (!controlsRef.current?.contains(document.activeElement)) setControlsVisible(false);
      controlsTimerRef.current = null;
    }, 2200);
  }

  function revealControls() {
    setControlsVisible(true);
    scheduleControlsHide();
  }

  useEffect(() => {
    if (!isPlaying || state !== 'ready') {
      clearControlsTimer();
      setControlsVisible(true);
      return;
    }
    scheduleControlsHide();
  }, [isPlaying, state]);

  const unavailable = state === 'unsupported' || state === 'failed';
  const failure = state === 'failed';
  const isLive = activeSource.live === true;
  // A live edge is never seekable and has no VOD duration; seeking stays off
  // regardless of any duration the element happens to report.
  const canSeek = !isLive && activeSource.seekable !== false && duration !== null && duration > 0;

  /** Returns true when the key was a player shortcut. */
  function runShortcut(key: string, shiftKey: boolean, inPlayer: boolean): boolean {
    if (shiftKey) {
      if (key !== 'n' || !prefs.onNext) return false;
      prefs.onNext();
      return true;
    }
    if (key === ' ' || key === 'k') togglePlay();
    else if (canSeek && (key === 'j' || key === 'l')) seek(currentTime + (key === 'j' ? -10 : 10));
    else if (canSeek && (key === 'arrowleft' || key === 'arrowright')) seek(currentTime + (key === 'arrowleft' ? -5 : 5));
    // Up/Down keep scrolling the page unless the player itself has focus.
    else if (inPlayer && (key === 'arrowup' || key === 'arrowdown')) changeVolume((isMuted ? 0 : volume) + (key === 'arrowup' ? 0.05 : -0.05));
    else if (key === 'm') toggleMute();
    else if (key === 'f' && activeSource.kind === 'video') void toggleFullscreen();
    else if (key === 'c' && hasCaptions && !extensions?.menu) toggleCaptions();
    else if (canSeek && /^[0-9]$/.test(key)) seek((duration as number) * Number(key) / 10);
    else return false;
    return true;
  }

  const shortcutRef = useRef<(event: globalThis.KeyboardEvent) => void>(() => undefined);
  shortcutRef.current = (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || unavailable || !nativeRef.current) return;
    const target = event.target instanceof HTMLElement ? event.target : null;
    if (!target || !shellRef.current) return;
    const inPlayer = shellRef.current.contains(target);
    const idle = target === document.body || target === document.documentElement;
    if (!inPlayer && !prefs.globalShortcuts) return;
    const key = event.key.toLowerCase();
    if (shortcutOwnedByTarget(target, key)) return;
    // Arrows elsewhere belong to that widget (tabs, menus, scrolling).
    if (!inPlayer && !idle && key.startsWith('arrow')) return;
    if (!runShortcut(key, event.shiftKey, inPlayer)) return;
    event.preventDefault();
    revealControls();
  };

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => shortcutRef.current(event);
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  function onSeekKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    const key = event.key.toLowerCase();
    const next = key === 'home' ? 0 : key === 'end' ? duration : key in SEEK_KEY_DELTAS ? currentTime + SEEK_KEY_DELTAS[key] : null;
    if (next === null || !canSeek) return;
    event.preventDefault();
    seek(next);
  }

  async function refresh() {
    if (!activeSource.onRefresh || refreshing) return;
    setRefreshing(true);
    try {
      const refreshed = await activeSource.onRefresh();
      if (refreshed) {
        setReplacement(refreshed);
        setState(refreshed.state || 'loading');
        setIsPlaying(false);
      }
    } catch {
      setState('failed');
    } finally {
      setRefreshing(false);
    }
  }

  const mediaLabel = `${title} ${activeSource.kind}`;
  const progress = canSeek ? Math.max(0, Math.min(100, (currentTime / (duration || 1)) * 100)) : 0;
  const timelineLabel = `${formatPlayerTime(currentTime)} of ${formatPlayerTime(duration)}`;
  const showGoLive = isLive && state === 'ready' && (liveBehind || !isPlaying);
  const pictureInPicture = activeSource.kind === 'video' && typeof document !== 'undefined' && document.pictureInPictureEnabled === true;
  const mediaHandlers: Partial<Record<(typeof MEDIA_EVENTS)[number], (event: MediaEvent) => void>> = {
    canplay: ({ currentTarget }) => { setState('ready'); onCanPlay?.(currentTarget); },
    ended: ({ currentTarget }) => { setIsPlaying(false); setEnded(true); onEnded?.(currentTarget); },
    error: ({ currentTarget }) => { if (onError?.(currentTarget) !== true) setState('failed'); },
    loadedmetadata: ({ currentTarget }) => {
      if (resumeAtRef.current !== null) {
        currentTarget.currentTime = resumeAtRef.current;
        resumeAtRef.current = null;
      }
      updateTimeline(currentTarget);
      setHasCaptions((currentTarget.textTracks?.length ?? 0) > 0);
      onLoadedMetadata?.(currentTarget);
    },
    pause: ({ currentTarget }) => { setIsPlaying(false); graph()?.schedule(); onPause?.(currentTarget); },
    seeked: ({ currentTarget }) => { updateTimeline(currentTarget); graph()?.schedule(); onSeeked?.(currentTarget); },
    play: ({ currentTarget }) => { setIsPlaying(true); setEnded(false); startGraph(currentTarget); },
    playing: () => { setIsPlaying(true); setState('ready'); },
    progress: ({ currentTarget }) => updateTimeline(currentTarget),
    ratechange: () => graph()?.schedule(),
    timeupdate: ({ currentTarget }) => { updateTimeline(currentTarget); graph()?.schedule(); onTimeUpdate?.(currentTarget); },
    waiting: () => setState('loading'),
    ...(activeSource.kind === 'video' ? {
      click: () => {
        if (!controlsVisible) {
          revealControls();
          suppressMediaClickRef.current = false;
          return;
        }
        if (suppressMediaClickRef.current) {
          suppressMediaClickRef.current = false;
          return;
        }
        togglePlay();
      },
      pointerdown: (event: MediaEvent) => {
        if (controlsVisible) return;
        event.preventDefault();
        suppressMediaClickRef.current = true;
        revealControls();
      },
    } : {}),
  };
  const handlersRef = useRef(mediaHandlers);
  handlersRef.current = mediaHandlers;
  const kind = activeSource.kind;

  // The media element lives outside React's tree: a new one per source, as a keyed <video> would be, except that an
  // element in picture-in-picture is handed on (the next source, the next item) so the floating window stays open.
  useLayoutEffect(() => {
    const media = takeMediaElement(kind);
    const listen = (event: Event) => handlersRef.current[event.type as (typeof MEDIA_EVENTS)[number]]?.(event as MediaEvent);
    MEDIA_EVENTS.forEach((type) => media.addEventListener(type, listen));
    nativeRef.current = media;
    return () => {
      MEDIA_EVENTS.forEach((type) => media.removeEventListener(type, listen));
      assignMedia(null);
      if (!releaseMediaElement(media)) media.remove();
    };
  }, [kind, activeSourceKey, mediaEpoch]); // eslint-disable-line react-hooks/exhaustive-deps

  // What React would render as <video>/<audio> props, synced every render; a failed or unsupported source shows no media.
  useLayoutEffect(() => {
    const media = nativeRef.current;
    const shell = shellRef.current;
    if (!media || !shell) return;
    if (unavailable) media.remove();
    else if (media.parentNode !== shell) placeMedia(shell, media, shell.firstChild);
    setAttr(media, 'aria-busy', String(state === 'loading'));
    setAttr(media, 'aria-label', mediaLabel);
    setAttr(media, 'autoplay', activeSource.autoPlay !== false ? '' : null);
    setAttr(media, 'crossorigin', activeSource.crossOrigin ?? null);
    setAttr(media, 'title', activeSource.nativeTitle ?? null);
    if (kind === 'video') {
      setAttr(media, 'playsinline', '');
      setAttr(media, 'poster', activeSource.poster || null);
    }
    assignMedia(media);
    if (replacedRef.current) {
      replacedRef.current = false;
      onMediaReplaced?.(resumeAtRef.current ?? 0);
    }
  });

  const trackKey = JSON.stringify(activeSource.tracks ?? []);
  useLayoutEffect(() => {
    const media = nativeRef.current;
    if (!media) return;
    media.querySelectorAll('track').forEach((track) => track.remove());
    for (const { id, label, language, src, default: isDefault } of activeSource.tracks ?? []) {
      const track = Object.assign(document.createElement('track'), { id, kind: 'subtitles', label, src, default: Boolean(isDefault) });
      if (language) track.srclang = language;
      media.append(track);
    }
  }, [trackKey, activeSourceKey, mediaEpoch]); // eslint-disable-line react-hooks/exhaustive-deps

  // A handed-on element loads the next source. A null source empties it, except a blob: (hls.js / dash.js
  // MediaSource), which its library detaches itself when its own session ends.
  useLayoutEffect(() => {
    const media = nativeRef.current;
    if (!media) return;
    if (activeSource.src) media.src = activeSource.src;
    else if (media.getAttribute('src') && !media.getAttribute('src')?.startsWith('blob:')) {
      media.removeAttribute('src');
      media.load();
    }
  }, [activeSourceKey, activeSource.src, mediaEpoch]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section
      aria-label={`${title} player`}
      className={`lumina-player ${className || ''}`}
      data-controls-visible={controlsVisible ? 'true' : 'false'}
      data-lumina-player="true"
      data-media-kind={activeSource.kind}
      data-motion="none"
      data-caption-fallback={captionSupportsVariables ? undefined : `${captionPrefs.size} ${captionPrefs.background}`}
      data-player-state={state}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) scheduleControlsHide(); }}
      onFocus={(event) => {
        if (controlsRef.current?.contains(event.target)) { clearControlsTimer(); setControlsVisible(true); }
        else revealControls();
      }}
      onPointerEnter={(event) => {
        if (event.pointerType !== 'touch') revealControls();
      }}
      onPointerLeave={scheduleControlsHide}
      onPointerMove={revealControls}
      ref={shellRef}
      style={captionVars as CSSProperties}
      tabIndex={0}
    >
      {controlsVisible && (isFullscreen || prefs.theater) ? (
        <div aria-hidden="true" className="player-top-scrim">
          <p className="player-top-title">{title}</p>
          {prefs.subtitle ? <p className="g-label player-top-sub">{prefs.subtitle}</p> : null}
        </div>
      ) : null}
      {unavailable ? (
        <div aria-live={failure ? undefined : 'polite'} role={failure ? 'alert' : 'status'}>
          <strong>Stream unavailable</strong>
          <span>{playerMessage(activeSource, failure ? 'failed' : 'unsupported')}</span>
          {activeSource.onRefresh ? <button disabled={refreshing} onClick={() => void refresh()} type="button">{refreshing ? 'Refreshing stream' : 'Refresh stream'}</button> : null}
          {extensions?.quality ? <div className="player-recovery-quality" data-player-extension="quality">{extensions.quality}</div> : null}
          {extensions?.theater ? <div className="player-recovery-theater" data-player-extension="theater">{extensions.theater}</div> : null}
          {extensions?.chapters ? <div className="player-progress-stack player-recovery-chapters">{extensions.chapters}</div> : null}
        </div>
      ) : (
        <>
          {state === 'loading' ? <p aria-live="polite" role="status">Loading media…</p> : null}
          {failure ? <p role="alert">{playerMessage(activeSource, 'failed')}</p> : null}
          {extensions?.overlay ? <div className="player-overlay" data-player-extension="overlay">{extensions.overlay}</div> : null}
          <div aria-label="Playback controls" className="player-controls" ref={controlsRef} role="group">
            {notice ? <p className="player-notice" role="status">{notice}</p> : null}
            <div className="player-progress-stack">
              {extensions?.chapters && !isLive ? <div data-player-extension="chapters">{extensions.chapters}</div> : null}
              {isLive ? (
                <div className="player-live-line" data-player-live="true">
                  {showGoLive ? (
                    <button className="player-go-live" onClick={goLive} type="button"><span className="g-live-dot" aria-hidden="true" />Go live</button>
                  ) : (
                    <span aria-label="Watching live" className="player-live-status" role="status"><LiveBadge state="live" surface="bar" /></span>
                  )}
                  <span className="player-live-note">{showGoLive ? 'Behind the live edge' : 'Watching at the current edge'}</span>
                </div>
              ) : (
                <label className="player-seek-control">
                  <span className="sr-only">Seek</span>
                  <input
                    aria-label="Seek"
                    aria-valuemax={Math.round(duration || 0)}
                    aria-valuemin={0}
                    aria-valuenow={Math.round(Math.min(currentTime, duration || 0))}
                    aria-valuetext={timelineLabel}
                    disabled={!canSeek}
                    max={duration || 0}
                    min="0"
                    onChange={(event) => seek(Number(event.currentTarget.value))}
                    onKeyDown={onSeekKeyDown}
                    step="0.1"
                    style={{ '--player-progress': `${progress}%`, '--player-buffered': `${Math.max(progress, buffered * 100)}%` } as CSSProperties}
                    type="range"
                    value={Math.min(currentTime, duration || 0)}
                  />
                </label>
              )}
            </div>
            <div className="player-control-row">
              <div className="player-control-cluster">
                <button aria-label={isPlaying ? 'Pause' : ended ? 'Replay' : 'Play'} onClick={togglePlay} type="button">{isPlaying ? <Pause aria-hidden="true" fill="currentColor" /> : ended ? <RotateCcw aria-hidden="true" /> : <Play aria-hidden="true" fill="currentColor" />}</button>
                <div className="player-volume-control">
                  <button aria-label={isMuted ? 'Unmute' : 'Mute'} onClick={toggleMute} type="button">{isMuted || volume === 0 ? <VolumeX aria-hidden="true" /> : <Volume2 aria-hidden="true" />}</button>
                  <label>
                    <span className="sr-only">Volume</span>
                    <input aria-label="Volume" aria-valuetext={isMuted ? 'Muted' : `${Math.round(volume * 100)}%`} max="1" min="0" onChange={(event) => changeVolume(Number(event.currentTarget.value))} step="0.05" type="range" value={isMuted ? 0 : volume} />
                  </label>
                </div>
                {!isLive ? <span aria-label="Playback time" className="player-timecode">{formatPlayerTime(currentTime)} / {formatPlayerTime(duration)}</span> : null}
              </div>
              <div className="player-control-cluster player-control-cluster--end">
                {extensions?.quality ? <div data-player-extension="quality">{extensions.quality}</div> : null}
                {!isLive ? (
                  <label className="player-rate-control">
                    <span className="sr-only">Playback speed</span>
                    <select aria-label="Playback speed" onChange={(event) => changeRate(Number(event.currentTarget.value))} value={rate}>
                      {PLAYBACK_RATES.map((option) => <option key={option} value={option}>{option}×</option>)}
                    </select>
                  </label>
                ) : null}
                {extensions?.menu ? <div data-player-extension="menu">{extensions.menu}</div> : hasCaptions ? <button aria-label="Captions" aria-pressed={captionsOn} onClick={toggleCaptions} type="button"><Captions aria-hidden="true" /></button> : null}
                {pictureInPicture ? <button aria-label="Picture in picture" onClick={() => void togglePictureInPicture()} type="button"><PictureInPicture2 aria-hidden="true" /></button> : null}
                {extensions?.theater ? <div data-player-extension="theater">{extensions.theater}</div> : null}
                {activeSource.kind === 'video' ? <button aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'} onClick={() => void toggleFullscreen()} type="button">{isFullscreen ? <Minimize aria-hidden="true" /> : <Maximize aria-hidden="true" />}</button> : null}
                {failure && activeSource.onRefresh ? <button aria-label={refreshing ? 'Refreshing stream' : 'Refresh stream'} disabled={refreshing} onClick={() => void refresh()} type="button"><RotateCcw aria-hidden="true" /></button> : null}
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  );
});
