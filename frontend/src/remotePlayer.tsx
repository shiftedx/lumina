import { LoaderCircle, Settings } from 'lucide-react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { startDashClimb } from './dashClimb';
import { ApiRequestError, apiBaseUrl, refreshRemoteStream, releaseRemoteStream, selectRemoteRendition } from './api';
import type { RemotePlayback } from './types';
import { LuminaPlayer, type LuminaPlayerExtensions, type LuminaPlayerSource } from './LuminaPlayer';

type RemotePlayerProps = {
  playback: RemotePlayback | null | undefined;
  poster: string | null;
  title: string;
  extensions?: LuminaPlayerExtensions;
  onMediaRef?: (media: HTMLMediaElement | null) => void;
  onReacquire?: () => Promise<boolean>;
  onEnded?: (media: HTMLMediaElement) => void;
  /** The relay reported the live broadcast ended; the page should stop claiming Live. */
  onLiveEnded?: () => void;
  onPause?: (media: HTMLMediaElement) => void;
  onSeeked?: (media: HTMLMediaElement) => void;
  onTimeUpdate?: (media: HTMLMediaElement) => void;
  resumePosition?: number | null;
};

const streamRetainers = new Map<string, number>();
const pendingReleases = new Map<string, ReturnType<typeof globalThis.setTimeout>>();
const playbackFailureMessage = 'The stream could not be played. You can still download it to the vault.';
const AUTO = 'auto';
/** A start estimate above any rung, so dash.js begins at the highest one (kb/s). */
export const DASH_TOP_RUNG_KBPS = 1_000_000;
// Streams whose Switch to Auto suggestion was already offered: at most once per video session.
const autoSuggestedStreams = new Set<string>();
// Set once hls.js has loaded; until then it drives no element, so native recovery applies.
let hlsSupported = false;

class RemoteStreamSessionMissingError extends Error {}

function streamUrl(path: string): string {
  return `${apiBaseUrl}${path}`;
}

/** An expired/unknown stream session means reacquire, not retry. */
async function streamRequest<T>(request: Promise<T>): Promise<T> {
  try {
    return await request;
  } catch (error) {
    if (error instanceof ApiRequestError && (error.status === 404 || error.status === 410)) throw new RemoteStreamSessionMissingError('Remote stream session expired.');
    throw error;
  }
}

let releasingOnPageHide = false;

/** A hard navigation or a closed tab never unmounts React: release every held stream (a keepalive DELETE, which
 *  survives the page) so it does not hold the member's relay cap until the server reaps it as idle. */
function releaseAllOnPageHide() {
  for (const streamId of streamRetainers.keys()) releaseRemoteStream(streamId).catch(() => undefined);
}

function retainRemoteStream(streamId: string): () => void {
  if (!releasingOnPageHide && typeof window !== 'undefined') {
    window.addEventListener('pagehide', releaseAllOnPageHide);
    releasingOnPageHide = true;
  }
  const pending = pendingReleases.get(streamId);
  if (pending !== undefined) {
    globalThis.clearTimeout(pending);
    pendingReleases.delete(streamId);
  }
  streamRetainers.set(streamId, (streamRetainers.get(streamId) || 0) + 1);
  return () => {
    const remaining = Math.max(0, (streamRetainers.get(streamId) || 1) - 1);
    if (remaining) {
      streamRetainers.set(streamId, remaining);
      return;
    }
    streamRetainers.delete(streamId);
    const timer = globalThis.setTimeout(() => {
      pendingReleases.delete(streamId);
      // The backend also expires abandoned sessions; unmount must stay non-blocking.
      if (!streamRetainers.has(streamId)) releaseRemoteStream(streamId).catch(() => undefined);
    }, 0);
    pendingReleases.set(streamId, timer);
  };
}

export function RemotePlayer({ extensions, onEnded, onLiveEnded, onMediaRef, onPause, onReacquire, onSeeked, onTimeUpdate, playback, poster, resumePosition, title }: RemotePlayerProps) {
  const qualityAvailabilityId = useId();
  const [descriptor, setDescriptor] = useState<RemotePlayback | null | undefined>(playback);
  const [attempt, setAttempt] = useState(0);
  const [playbackProblem, setPlaybackProblem] = useState<string | null>(null);
  const [qualityProblem, setQualityProblem] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const mountedRef = useRef(true);
  const descriptorRef = useRef(descriptor);
  const onReacquireRef = useRef(onReacquire);
  const localRecoveryUsedRef = useRef(false);
  const refreshUsedRef = useRef(false);
  const refreshPendingRef = useRef(false);
  const reacquirePendingRef = useRef(false);
  const refreshControllerRef = useRef<AbortController | null>(null);
  const refreshTokenRef = useRef(0);
  const synchronizedInitialPlaybackRef = useRef(false);
  const selectionControllerRef = useRef<AbortController | null>(null);
  const resumeAfterSelectionRef = useRef<{ currentTime: number; playing: boolean } | null>(null);
  const pendingInitialResumeRef = useRef(Number.isFinite(resumePosition) && (resumePosition || 0) > 0 ? resumePosition as number : null);
  const lastResumePositionRef = useRef(resumePosition);
  const dashRecoveryAttemptsRef = useRef<Set<number>>(new Set());
  const [activeHeight, setActiveHeight] = useState(0);
  // A relayed HLS ladder (live and relayed VOD) has no server renditions: hls.js switches levels itself.
  const hlsRef = useRef<import('hls.js').default | null>(null);
  const [hlsLadder, setHlsLadder] = useState<Array<{ index: number; height: number }>>([]);
  const [hlsChoice, setHlsChoice] = useState(AUTO);
  const hlsPickRef = useRef<number | null>(null);
  const [suggestAuto, setSuggestAuto] = useState(false);

  descriptorRef.current = descriptor;
  const autoMode = descriptor?.selected_rendition_id === AUTO;
  const hlsAuto = hlsLadder.length > 1 && hlsChoice === AUTO;
  const pinnedWithAuto = Boolean(descriptor?.auto_available && descriptor.selected_rendition_id && !autoMode);
  const streamId = descriptor?.stream_id;
  onReacquireRef.current = onReacquire;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      refreshTokenRef.current += 1;
      refreshControllerRef.current?.abort();
      refreshControllerRef.current = null;
      selectionControllerRef.current?.abort();
      selectionControllerRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!synchronizedInitialPlaybackRef.current) {
      synchronizedInitialPlaybackRef.current = true;
      return;
    }
    refreshTokenRef.current += 1;
    refreshControllerRef.current?.abort();
    refreshControllerRef.current = null;
    selectionControllerRef.current?.abort();
    selectionControllerRef.current = null;
    const media = videoRef.current;
    resumeAfterSelectionRef.current = media && media.currentTime > 0
      ? { currentTime: media.currentTime, playing: !media.paused }
      : null;
    setDescriptor(playback);
    setPlaybackProblem(null);
    setQualityProblem(null);
    setPreparing(false);
    setAttempt((value) => value + 1);
    localRecoveryUsedRef.current = false;
    refreshUsedRef.current = false;
    refreshPendingRef.current = false;
    reacquirePendingRef.current = false;
  }, [playback]);

  const applyPendingInitialResume = useCallback((media: HTMLMediaElement) => {
    const requested = pendingInitialResumeRef.current;
    if (!requested || !Number.isFinite(requested)) return;
    const target = Number.isFinite(media.duration) ? Math.min(requested, Math.max(0, media.duration - 0.25)) : requested;
    // A slow history response must never rewind playback the member has already advanced.
    if (target > media.currentTime + 0.75) media.currentTime = target;
    pendingInitialResumeRef.current = null;
  }, []);

  useEffect(() => {
    if (resumePosition === lastResumePositionRef.current) return;
    lastResumePositionRef.current = resumePosition;
    pendingInitialResumeRef.current = Number.isFinite(resumePosition) && (resumePosition || 0) > 0 ? resumePosition as number : null;
    const media = videoRef.current;
    if (media && media.readyState >= HTMLMediaElement.HAVE_METADATA) applyPendingInitialResume(media);
  }, [applyPendingInitialResume, resumePosition]);

  useEffect(() => playback?.stream_id ? retainRemoteStream(playback.stream_id) : undefined, [playback?.stream_id]);

  const snapshotResume = useCallback(() => {
    const media = videoRef.current;
    if (media && Number.isFinite(media.currentTime) && media.currentTime > 0) {
      resumeAfterSelectionRef.current = { currentTime: media.currentTime, playing: !media.paused };
    }
  }, []);

  // Automatic recovery refreshes once; a member's explicit Refresh always retries.
  const refreshOnce = useCallback(async (manual = false) => {
    const current = descriptorRef.current;
    if (refreshPendingRef.current || reacquirePendingRef.current) return;
    if (!current || (refreshUsedRef.current && !manual)) {
      setPlaybackProblem(playbackFailureMessage);
      return;
    }
    snapshotResume();
    refreshPendingRef.current = true;
    refreshUsedRef.current = true;
    const token = refreshTokenRef.current + 1;
    refreshTokenRef.current = token;
    const streamId = current.stream_id;
    const controller = new AbortController();
    refreshControllerRef.current?.abort();
    refreshControllerRef.current = controller;
    setPreparing(true);
    try {
      const refreshed = await streamRequest(refreshRemoteStream(streamId, controller.signal));
      if (!mountedRef.current || token !== refreshTokenRef.current || descriptorRef.current?.stream_id !== streamId) return;
      setDescriptor(refreshed);
      setPlaybackProblem(null);
      setAttempt((value) => value + 1);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof RemoteStreamSessionMissingError && onReacquireRef.current) {
        reacquirePendingRef.current = true;
        let reacquired = false;
        try {
          reacquired = await onReacquireRef.current();
        } catch {
          reacquired = false;
        } finally {
          reacquirePendingRef.current = false;
        }
        if (!reacquired && mountedRef.current && token === refreshTokenRef.current && descriptorRef.current?.stream_id === streamId) {
          setPlaybackProblem(playbackFailureMessage);
        }
      } else if (mountedRef.current && token === refreshTokenRef.current && descriptorRef.current?.stream_id === streamId) {
        setPlaybackProblem(playbackFailureMessage);
      }
    } finally {
      if (token === refreshTokenRef.current) {
        refreshPendingRef.current = false;
        if (refreshControllerRef.current === controller) refreshControllerRef.current = null;
        if (mountedRef.current) setPreparing(false);
      }
    }
  }, [snapshotResume]);

  const recoverNative = useCallback(() => {
    if (!localRecoveryUsedRef.current) {
      snapshotResume();
      localRecoveryUsedRef.current = true;
      setAttempt((value) => value + 1);
      return;
    }
    void refreshOnce();
  }, [refreshOnce, snapshotResume]);

  const recoverDash = useCallback(() => {
    // dash.js and the native media element may report the same failure. Only
    // one of those signals may consume a recovery step for a given attempt.
    if (dashRecoveryAttemptsRef.current.has(attempt)) return;
    dashRecoveryAttemptsRef.current.add(attempt);
    recoverNative();
  }, [attempt, recoverNative]);

  const recoverVideoElement = useCallback(() => {
    const current = descriptorRef.current;
    const video = videoRef.current;
    if (current?.transport === 'hls' && video && !video.canPlayType('application/vnd.apple.mpegurl') && hlsSupported) return;
    recoverNative();
  }, [recoverNative]);

  const chooseRendition = useCallback(async (renditionId: string) => {
    const current = descriptorRef.current;
    if (!current || renditionId === current.selected_rendition_id) return;
    const media = videoRef.current;
    resumeAfterSelectionRef.current = media ? { currentTime: media.currentTime, playing: !media.paused } : null;
    const controller = new AbortController();
    selectionControllerRef.current?.abort();
    selectionControllerRef.current = controller;
    setQualityProblem(null);
    setPreparing(true);
    try {
      const selected = await streamRequest(selectRemoteRendition(current.stream_id, renditionId, controller.signal));
      if (!mountedRef.current || controller.signal.aborted || descriptorRef.current?.stream_id !== current.stream_id) return;
      setDescriptor(selected);
      setPlaybackProblem(null);
      localRecoveryUsedRef.current = false;
      refreshUsedRef.current = false;
      setAttempt((value) => value + 1);
    } catch (error) {
      if (!controller.signal.aborted && mountedRef.current) {
        resumeAfterSelectionRef.current = null;
        if (error instanceof RemoteStreamSessionMissingError && onReacquireRef.current) {
          reacquirePendingRef.current = true;
          let reacquired = false;
          try {
            reacquired = await onReacquireRef.current();
          } catch {
            reacquired = false;
          } finally {
            reacquirePendingRef.current = false;
          }
          if (!reacquired && mountedRef.current && descriptorRef.current?.stream_id === current.stream_id) {
            setPlaybackProblem(playbackFailureMessage);
          }
        } else {
          setQualityProblem('That quality could not be selected. The previous stream is still available.');
        }
      }
    } finally {
      const isCurrent = selectionControllerRef.current === controller;
      if (isCurrent) selectionControllerRef.current = null;
      if (isCurrent && !controller.signal.aborted && mountedRef.current) setPreparing(false);
    }
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || descriptor?.status !== 'ready' || descriptor.transport !== 'hls' || !descriptor.playback_url) return;

    const url = streamUrl(descriptor.playback_url);
    setPreparing(true);
    // Native HLS only without MediaSource (iPhone), as in localPlayer: Chrome now claims native HLS, and its
    // player puts YouTube live's separate audio rendition whole segments (2-4 s) off the video.
    if (!('MediaSource' in window) && video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
      return () => {
        video.removeAttribute('src');
        video.load();
      };
    }
    let hls: import('hls.js').default | null = null;
    let destroyed = false;
    const destroy = () => {
      if (destroyed) return;
      destroyed = true;
      if (hlsRef.current === hls) hlsRef.current = null;
      setHlsLadder([]);
      hls?.destroy();
    };
    // hls.js loads only for an HLS source the browser cannot play natively.
    import('hls.js').then(({ default: Hls }) => {
      if (destroyed) return;
      hlsSupported = Hls.isSupported();
      if (!hlsSupported) {
        setPreparing(false);
        setPlaybackProblem('This browser cannot play this stream. You can still download it to the vault.');
        return;
      }
      const player = new Hls({
        enableWorker: true,
        // A home-LAN bandwidth guess (20 Mb/s) so ABR does not drop below the top rung before it has measured anything.
        abrEwmaDefaultEstimate: 20_000_000,
        // With two segments buffered, judge a level by its segments' measured bitrate, not its declared peak: YouTube
        // live declares 1080p at 5.4 Mb/s for ~0.8 Mb/s of segments, whose slow small transfers then read as a shortfall.
        abrMaxWithRealBitrate: true,
        // The first manifest waits for server-side packaging: keep showing "Preparing" instead of timing out.
        manifestLoadPolicy: { default: { maxTimeToFirstByteMs: Infinity, maxLoadTimeMs: 120_000, timeoutRetry: { maxNumRetry: 1, retryDelayMs: 0, maxRetryDelayMs: 0 }, errorRetry: { maxNumRetry: 1, retryDelayMs: 1000, maxRetryDelayMs: 8000 } } },
        xhrSetup: (xhr) => { xhr.withCredentials = true; },
      });
      hls = player;
      hlsRef.current = player;
      player.loadSource(url);
      player.attachMedia(video);
      // Owner rule: start at the highest fidelity and only step down on real bandwidth or buffering trouble (ABR).
      player.on(Hls.Events.MANIFEST_PARSED, (_event, data) => {
        globalThis.performance?.mark?.('lumina:manifest-parsed');
        if (data.levels.length > 1) player.startLevel = data.levels.length - 1;
        // Levels come ordered by bitrate: the last one of each height is its best.
        const best = new Map(data.levels.map((level, index) => [level.height, index]));
        setHlsLadder([...best].filter(([height]) => height > 0).sort((a, b) => b[0] - a[0]).map(([height, index]) => ({ height, index })));
        setHlsChoice(AUTO);
      });
      // A pinned height flushes the other height, but when the pick lands at a segment start Chrome keeps decoding the
      // old one until the next keyframe (a whole 5 s YouTube segment). Once the pick's segment under the playhead is
      // buffered, a same-time seek decodes the new height now; a first segment ahead of the playhead shows by itself.
      player.on(Hls.Events.FRAG_BUFFERED, (_event, { frag }) => {
        if (frag.type !== 'main' || frag.level !== hlsPickRef.current) return;
        hlsPickRef.current = null;
        const at = video.currentTime;
        if (frag.start <= at && at < frag.end) video.currentTime = at;
      });
      player.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        if (!localRecoveryUsedRef.current) {
          snapshotResume();
          localRecoveryUsedRef.current = true;
          if (data.type === Hls.ErrorTypes.NETWORK_ERROR) player.startLoad();
          else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) player.recoverMediaError();
          destroy();
          setAttempt((value) => value + 1);
          return;
        }
        destroy();
        void refreshOnce();
      });
    }).catch(() => { if (!destroyed) void refreshOnce(); });
    return destroy;
  }, [attempt, descriptor?.playback_url, descriptor?.status, descriptor?.transport, refreshOnce, snapshotResume]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || descriptor?.status !== 'ready' || descriptor.transport !== 'dash' || !descriptor.playback_url) return;

    let cancelled = false;
    let player: import('dashjs').MediaPlayerClass | null = null;
    let stopClimb = () => {};
    const streamId = descriptor.stream_id;
    const start = async () => {
      setPreparing(true);
      try {
        const dashjs = await import('dashjs');
        if (cancelled || descriptorRef.current?.stream_id !== streamId) return;
        player = dashjs.MediaPlayer().create();
        for (const requestType of ['MPD', 'InitializationSegment', 'IndexSegment', 'MediaSegment']) {
          player.setXHRWithCredentialsForType(requestType, true);
        }
        const onError = () => {
          if (cancelled) return;
          player?.reset();
          player = null;
          recoverDash();
        };
        player.on(dashjs.MediaPlayer.events.ERROR, onError);
        // dash.js drops video it cannot decode and would play the rest as sound only: fail honestly instead.
        player.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
          if (cancelled || !player || player.getTracksFor('video').length) return;
          player.reset();
          player = null;
          setPreparing(false);
          setPlaybackProblem('This browser cannot decode the selected quality. Choose another quality to continue.');
        });
        // Owner rule: Auto starts at the top rung, steps down only on evidence (a segment download measurably too slow,
        // the buffer running dry, dropped frames) and climbs back once the trouble passes. dash.js's own throughput
        // estimate is off through the relay (it read 3-9 Mb/s while segments arrived at 80-950 Mb/s: Chrome caps Network
        // Information at 10 Mb/s and tiny init/index ranges dominate the mean), so its throughput and BOLA rules, which
        // began YouTube 4K at 360p, are off; startDashClimb measures whole segments and climbs instead.
        if (autoMode) {
          player.updateSettings({ streaming: { abr: { initialBitrate: { video: DASH_TOP_RUNG_KBPS }, rules: { throughputRule: { active: false }, bolaRule: { active: false }, droppedFramesRule: { active: true } } } } });
          stopClimb = startDashClimb(player);
        }
        const resume = resumeAfterSelectionRef.current;
        player.initialize(
          video,
          streamUrl(descriptor.playback_url as string),
          resume?.playing ?? true,
          resume?.currentTime ?? pendingInitialResumeRef.current ?? 0,
        );
      } catch {
        if (!cancelled) void refreshOnce();
      }
    };
    void start();
    return () => {
      cancelled = true;
      stopClimb();
      player?.reset();
      player = null;
    };
  }, [attempt, autoMode, descriptor?.playback_url, descriptor?.status, descriptor?.stream_id, descriptor?.transport, recoverDash]);

  // Auto reports the resolution it is actually playing.
  useEffect(() => {
    const media = videoRef.current;
    if (!media || !(autoMode || hlsAuto)) return undefined;
    const update = () => setActiveHeight(media.videoHeight);
    update();
    media.addEventListener('resize', update);
    return () => media.removeEventListener('resize', update);
  }, [attempt, autoMode, hlsAuto]);

  // A pinned height switches at once (hls.js drops the buffered other height); Auto hands back to ABR at the next
  // segment, so the picture never pauses for it.
  const chooseHlsLevel = useCallback((value: string) => {
    const player = hlsRef.current;
    if (!player) return;
    setHlsChoice(value);
    hlsPickRef.current = value === AUTO ? null : Number(value);
    if (value === AUTO) player.nextLevel = -1;
    else player.currentLevel = Number(value);
  }, []);

  // A pinned quality that keeps buffering earns one dismissible Switch to Auto offer; it never changes by itself.
  useEffect(() => {
    const media = videoRef.current;
    if (!media || !pinnedWithAuto || !streamId || autoSuggestedStreams.has(streamId)) return undefined;
    let started = false;
    let stalls: number[] = [];
    let timer: ReturnType<typeof globalThis.setTimeout> | undefined;
    const suggest = () => {
      if (autoSuggestedStreams.has(streamId)) return;
      autoSuggestedStreams.add(streamId);
      setSuggestAuto(true);
    };
    const onWaiting = () => {
      if (!started || media.seeking) return; // startup and seeks are expected to buffer
      const now = Date.now();
      stalls = [...stalls.filter((at) => now - at < 60_000), now];
      globalThis.clearTimeout(timer);
      if (stalls.length >= 3) suggest();
      else timer = globalThis.setTimeout(suggest, 5_000);
    };
    const onPlaying = () => {
      started = true;
      globalThis.clearTimeout(timer);
    };
    media.addEventListener('waiting', onWaiting);
    media.addEventListener('playing', onPlaying);
    return () => {
      globalThis.clearTimeout(timer);
      media.removeEventListener('waiting', onWaiting);
      media.removeEventListener('playing', onPlaying);
    };
  }, [attempt, pinnedWithAuto, streamId]);

  const liveEnded = descriptor?.fallback_code === 'live_stream_ended';
  useEffect(() => { if (liveEnded) onLiveEnded?.(); }, [liveEnded, onLiveEnded]);

  const unsupported = !descriptor || descriptor.status === 'unsupported' || !descriptor.playback_url || (descriptor.media_kind !== 'audio' && !descriptor.has_audio);
  const source: LuminaPlayerSource = {
    id: descriptor ? `${descriptor.stream_id}:${descriptor.playback_url || 'unsupported'}:${attempt}` : 'remote-unavailable',
    kind: descriptor?.media_kind === 'audio' ? 'audio' : 'video',
    src: unsupported || playbackProblem || descriptor?.transport === 'hls' || descriptor?.transport === 'dash' ? null : streamUrl(descriptor.playback_url as string),
    poster,
    seekable: descriptor?.seekable !== false,
    live: descriptor?.live === true,
    autoPlay: resumeAfterSelectionRef.current?.playing ?? true,
    crossOrigin: 'use-credentials',
    nativeTitle: descriptor?.transport === 'hls' ? 'HLS remote stream' : descriptor?.transport === 'dash' ? 'DASH remote stream' : 'Progressive remote stream',
    state: playbackProblem ? 'failed' : unsupported ? 'unsupported' : 'loading',
    message: playbackProblem
      || descriptor?.fallback_message
      || (!descriptor?.has_audio && descriptor?.media_kind !== 'audio' ? 'This source has no compatible stream with audio. You can still download it to the vault.' : null)
      || 'You can still download this video to the vault.',
    // An ended broadcast has nothing to refresh; offering it would only fail.
    onRefresh: descriptor && !liveEnded ? async () => { await refreshOnce(true); } : undefined,
  };

  const hasOneRendition = descriptor?.renditions?.length === 1 && !descriptor.auto_available;
  const pinnedLabel = descriptor?.renditions?.find((rendition) => rendition.rendition_id === descriptor.selected_rendition_id)?.display_label || 'this quality';
  const activeLabel = activeHeight ? descriptor?.renditions?.find((rendition) => rendition.height === activeHeight)?.display_label || `${activeHeight}p` : null;
  const recoveryChoiceRequired = !descriptor?.selected_rendition_id;
  const quality = descriptor?.renditions && descriptor.renditions.length > 0 ? (
    <div className="quality-control"><Settings aria-hidden="true" /><label><span className="sr-only">Quality</span>
      <select aria-describedby={hasOneRendition ? qualityAvailabilityId : undefined} aria-label="Remote playback quality" disabled={preparing || (hasOneRendition && !recoveryChoiceRequired)} onChange={(event) => void chooseRendition(event.currentTarget.value)} value={descriptor.selected_rendition_id || ''}>
        {!descriptor.selected_rendition_id ? <option disabled value="">Choose quality</option> : null}
        {descriptor.auto_available || autoMode ? <option value={AUTO}>{autoMode && activeLabel ? `Auto (${activeLabel})` : 'Auto'}</option> : null}
        {descriptor.renditions.map((rendition) => <option key={rendition.rendition_id} value={rendition.rendition_id}>{rendition.display_label}</option>)}
      </select>
    </label>{hasOneRendition ? <span className="sr-only" id={qualityAvailabilityId}>{recoveryChoiceRequired ? `${descriptor.renditions[0].display_label} is available. Choose it to resume.` : `Only ${descriptor.renditions[0].display_label} is available from this source.`}</span> : null}{qualityProblem ? <span aria-live="polite" className="quality-problem">{qualityProblem}</span> : null}</div>
  ) : hlsLadder.length > 1 ? (
    <div className="quality-control"><Settings aria-hidden="true" /><label><span className="sr-only">Quality</span>
      <select aria-label="Remote playback quality" onChange={(event) => chooseHlsLevel(event.currentTarget.value)} value={hlsChoice}>
        <option value={AUTO}>{hlsAuto && activeHeight ? `Auto (${activeHeight}p)` : 'Auto'}</option>
        {hlsLadder.map((level) => <option key={level.index} value={String(level.index)}>{`${level.height}p`}</option>)}
      </select>
    </label></div>
  ) : null;
  return <><LuminaPlayer extensions={{ ...extensions, quality: quality || extensions?.quality }} mediaRef={(media) => { videoRef.current = media instanceof HTMLVideoElement ? media : null; onMediaRef?.(media); }} onCanPlay={(media) => { globalThis.performance?.mark?.('lumina:can-play'); setPreparing(false); const resume = resumeAfterSelectionRef.current; if (resume) { media.currentTime = Math.min(resume.currentTime, Number.isFinite(media.duration) ? media.duration : resume.currentTime); if (resume.playing) void media.play().catch(() => undefined); else media.pause(); resumeAfterSelectionRef.current = null; } else applyPendingInitialResume(media); }} onEnded={onEnded} onMediaReplaced={() => { snapshotResume(); setAttempt((value) => value + 1); }} onError={() => { if (descriptor?.transport === 'hls') recoverVideoElement(); else if (descriptor?.transport === 'dash') recoverDash(); else recoverNative(); return true; }} onLoadedMetadata={applyPendingInitialResume} onPause={onPause} onSeeked={onSeeked} onTimeUpdate={onTimeUpdate} source={source} title={title} />{suggestAuto && pinnedWithAuto ? <div className="quality-suggestion" role="status"><span>{pinnedLabel} keeps pausing to buffer.</span><button onClick={() => { setSuggestAuto(false); void chooseRendition(AUTO); }} type="button">Switch to Auto</button><button onClick={() => setSuggestAuto(false)} type="button">Keep {pinnedLabel}</button></div> : null}{preparing && !unsupported && !playbackProblem ? <div aria-live="polite" className="player-loading remote-player-preparing"><LoaderCircle className="spin" /><strong>Preparing the remote stream</strong><span>Finding the selected browser-compatible version…</span></div> : null}</>;
}

export function RemotePlayerLoading() {
  return <div className="player-loading"><LoaderCircle className="spin" /><strong>Preparing the stream</strong><span>Finding the best playable version…</span></div>;
}
