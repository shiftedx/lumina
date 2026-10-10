import { type ComponentProps, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { apiBaseUrl, getLocalPlaybackOptions, libraryMediaUrl, markHevcFailed, startLocalPlaybackSession, stopLocalPlaybackSession } from './api';
import { accessStopCode, reportAccessStop } from './features/access/accessEvents';
import { LuminaPlayer, type LuminaPlayerSource, type LuminaPlayerTrack } from './LuminaPlayer';
import { releaseBackground } from './backgroundGate';
import { onFirstFrame, recordMetric, takePlayIntent, type TtffLabel } from './perfMetrics';
import { startPoint } from './playbackModel';
import { adoptSpeculativeStart, holdConversion, takePrefetchedOptions } from './playbackPrefetch';
import type { LocalPlaybackOptions, LocalPlaybackSession, PlaybackSessionRequest } from './types';

type LocalPlayerProps = Omit<ComponentProps<typeof LuminaPlayer>, 'source' | 'ref'> & {
  itemId: string;
  kind: 'video' | 'audio';
  poster: string | null;
  /** Version, audio track, burned-in subtitle and quality; a change restarts playback at the current position. */
  request?: PlaybackSessionRequest;
  /** Lets the sustained-buffering prompt choose a lower quality; without it there is no prompt. */
  onRequestChange?: (next: PlaybackSessionRequest) => void;
  /** Text subtitle tracks; carried in every playback mode. */
  tracks?: LuminaPlayerTrack[];
  /** The probe result for the playback menu and loudness; null when the probe is unreachable. */
  onOptions?: (options: LocalPlaybackOptions | null) => void;
  /** Where playback starts, in seconds: the resume point or the watch link's `t`. A later change seeks there. */
  startAt?: number;
  /** Changes when the same `startAt` is asked for again (the same moment pressed twice), so it seeks again. */
  startKey?: number;
};

export const STOPPED_BY_ADMIN_MESSAGE = 'Playback was stopped by the server owner.';
/** True when a 403 body carries the detail code `stopped_by_admin` (Activity page). */
export const isStoppedByAdmin = (status: number | undefined, body: string | undefined) => status === 403 && /"?code"?\s*:\s*"stopped_by_admin"/.test(body ?? '');

const ENCODE_MAX_HEIGHT = 1080; // mirrors playback_decision.MAX_ENCODE_HEIGHT
const codecLabel = (codec: string) => (codec === 'h264' ? 'H.264' : codec.toUpperCase());
// only MSE-less browsers (iPhone) use native HLS, which cannot seek past the converted part.
const nativeHls = () => typeof window !== 'undefined' && !('MediaSource' in window) && document.createElement('video').canPlayType('application/vnd.apple.mpegurl') !== '';
const START_BUFFER_SECONDS = 12; // A short first buffer, restored after the first frame
const NORMAL_BUFFER_SECONDS = 30; // hls.js's default maxBufferLength
// HEVC that has data but no frame by then is a browser that claimed it and cannot decode it (Firefox on Windows).
export const HEVC_FIRST_FRAME_MS = 8000;

/** The ttff_ms label for how this Play is served. */
export function ttffLabel(session: LocalPlaybackSession | null, start: number): TtffLabel {
  if (!session) return start > 0 ? 'direct_resume' : 'direct';
  return session.kind === 'video_hw' ? 'transcode_hw' : session.kind === 'video_sw' ? 'transcode_sw' : 'remux';
}

/** A stable key for the member's choices: drops null/undefined fields so equivalent requests (an
 * explicit `null` vs. an omitted field) never trigger a spurious restart (#F13). */
function requestKey(request?: PlaybackSessionRequest): string {
  if (!request) return '{}';
  const entries = Object.entries(request).filter(([, value]) => value != null);
  return JSON.stringify(Object.fromEntries(entries));
}

/** A seek outside the converted range [start, edge] restarts the conversion there (#137). */
export function needsRestart(target: number, start: number, edge: number | undefined): boolean {
  return edge !== undefined && (target < start - 1 || target > edge);
}

/** True when the file or the member's choices need a server session (a direct file plays as-is otherwise). */
export function needsSession(options: LocalPlaybackOptions | null, request?: PlaybackSessionRequest): boolean {
  if (options?.mode === 'remux' || options?.mode === 'transcode') return true;
  if (options?.mode !== 'direct' || !request) return false;
  const tracks = options.audio_tracks ?? [];
  const defaultAudio = (tracks.find((track) => track.default) ?? tracks[0])?.index;
  return Boolean(
    (request.max_height && (options.facts?.height ?? 0) > request.max_height)
    || request.subtitle?.startsWith('i:')
    || (request.audio_index != null && request.audio_index !== defaultAudio),
  );
}

/** Linear gain for the shared audio graph; 1 when off or not analysed yet. */
export function loudnessGain(options: LocalPlaybackOptions | null, enabled: boolean): number {
  const gainDb = options?.loudness_gain_db;
  return enabled && typeof gainDb === 'number' && Number.isFinite(gainDb) ? 10 ** (gainDb / 20) : 1;
}

/** The next Quality rung below what plays now, or null at the bottom. */
export function lowerQuality(options: LocalPlaybackOptions | null, current: number | null | undefined): number | null {
  const playing = current ?? Math.min(options?.facts?.height ?? 0, ENCODE_MAX_HEIGHT);
  return (options?.quality_heights ?? []).find((height) => height < playing) ?? null;
}

export function playbackFactsLabel(options: LocalPlaybackOptions | null): string | null {
  const facts = options?.facts;
  if (!facts) return null;
  const size = facts.height ? `${facts.height}p` : null;
  const codecs = [facts.video_codec, facts.audio_codec].filter((codec): codec is string => Boolean(codec)).map(codecLabel).join(' / ');
  const via = options.mode === 'remux' ? 'Remuxed' : options.mode === 'transcode' ? 'Transcoded' : 'Original';
  return [size, codecs, via].filter(Boolean).join(' · ');
}

/** Plays a Library item through the path its probed codecs and the member's choices actually need. */
export function LocalLibraryPlayer({ itemId, kind, poster, extensions, onLoadedMetadata, request, onRequestChange, tracks, onOptions, startAt = 0, startKey, ...playerProps }: LocalPlayerProps) {
  const playId = request?.version_id ?? itemId;
  const key = requestKey(request);
  const [options, setOptions] = useState<LocalPlaybackOptions | null>(null);
  const [probeFailed, setProbeFailed] = useState(false);
  const [session, setSession] = useState<LocalPlaybackSession | null>(null);
  const [conversionProblem, setConversionProblem] = useState<string | null>(null);
  const [stoppedByAdmin, setStoppedByAdmin] = useState(false);
  // What the server session plays: a start position and the member's choices, always changed together,
  // so a new choice starts exactly one conversion.
  const [target, setTarget] = useState({ start: startAt, key });
  // A direct file's start point is fixed at mount (its URL must not change); a later `startAt` seeks instead.
  const [directStart] = useState(startAt);
  const appliedStartRef = useRef({ at: startAt, key: startKey });
  // Time to first frame: from the Play the app noted to this player's first presented frame.
  const playStartRef = useRef<number | null>(null);
  const ttffDoneRef = useRef(false);
  useEffect(() => { playStartRef.current ??= takePlayIntent(); }, []); // ??=: StrictMode runs this twice
  const [suggestLower, setSuggestLower] = useState(false);
  const restartedRef = useRef(false);
  const choiceRef = useRef(`${itemId}|${key}`);
  // The position the current session was asked for; copied video can start a little before it (session.start).
  const askedRef = useRef(0);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  // Bumped when the player swaps its element (stalled audio clock): hls.js re-attaches to the new one.
  const [mediaEpoch, setMediaEpoch] = useState(0);
  // A version switch replaces the <video> during commit, so the position is read here, while the old
  // element is still mounted, and kept with its play intent for the choice effect below.
  const choiceStartRef = useRef(0);
  const resumePlayingRef = useRef(true);
  const onOptionsRef = useRef(onOptions);
  onOptionsRef.current = onOptions;
  if (choiceRef.current !== `${itemId}|${key}`) {
    choiceStartRef.current = Math.floor(mediaRef.current?.currentTime ?? 0);
    resumePlayingRef.current = mediaRef.current ? !mediaRef.current.paused : true;
  }
  const converts = needsSession(options, request);
  const duration = options?.facts?.duration;
  // Bumped by the HEVC fallback to ask for a new decision now that this browser no longer offers HEVC.
  const [decisionAttempt, setDecisionAttempt] = useState(0);
  const fellBackRef = useRef(false); // at most one automatic fallback per player
  const copiesVideo = session ? (session.kind ? session.kind === 'remux' || session.kind === 'audio' : session.mode === 'remux') : options?.mode === 'direct' && !converts;
  const hevcVideo = kind === 'video' && options?.facts?.video_codec === 'hevc' && copiesVideo;
  const arrivingRef = useRef(false);
  // HEVC the browser claimed but cannot play: remember it, drop HEVC from the profiles and ask again at the
  // same position, so the server encodes H.264 instead. False when this is not that case (show the failure).
  const fallBackRef = useRef<() => boolean>(() => false);
  fallBackRef.current = () => {
    if (!hevcVideo || fellBackRef.current) return false;
    fellBackRef.current = true;
    markHevcFailed();
    const media = mediaRef.current;
    const at = session ? Math.max(media?.currentTime ?? 0, askedRef.current) : media?.currentTime || directFrom;
    restartedRef.current = true;
    setOptions(null);
    setSession(null);
    setTarget((current) => ({ ...current, start: Math.floor(at) }));
    setDecisionAttempt((attempt) => attempt + 1);
    return true;
  };

  useEffect(() => {
    let active = true;
    setOptions(null);
    setProbeFailed(false);
    setSession(null);
    ((decisionAttempt ? null : takePrefetchedOptions(playId)) ?? getLocalPlaybackOptions(playId)).then(
      (next) => { if (active) { setOptions(next); onOptionsRef.current?.(next); } },
      // An unreachable probe should not block a file the browser may still play.
      () => { if (active) { setProbeFailed(true); onOptionsRef.current?.(null); } },
    );
    return () => { active = false; };
  }, [playId, decisionAttempt]);

  // A new choice (version, audio, subtitle, quality) restarts at the current position, like a far seek.
  // A new item starts from the beginning and resets the choice memory.
  useEffect(() => {
    const next = `${itemId}|${key}`;
    if (choiceRef.current === next) return;
    const sameItem = choiceRef.current.split('|', 1)[0] === itemId;
    choiceRef.current = next;
    setSuggestLower(false);
    restartedRef.current = sameItem;
    setTarget({ start: sameItem ? choiceStartRef.current : 0, key });
  }, [itemId, key]);

  // A new watch-link time for the open item seeks there; a converted session restarts through its seeking handler.
  useEffect(() => {
    const applied = appliedStartRef.current;
    if (startAt === applied.at && startKey === applied.key) return;
    appliedStartRef.current = { at: startAt, key: startKey };
    if (mediaRef.current) mediaRef.current.currentTime = startAt;
  }, [startAt, startKey]);

  // One server conversion per open item and choice, restarted at `start` by a far seek or a new choice;
  // leaving the item (or restarting) cancels the previous one and its ffmpeg child.
  useEffect(() => {
    // Back to direct play: drop the stopped session so hls.js is torn down and the direct file resumes.
    if (!converts) { setSession(null); return; }
    let active = true;
    let started: string | null = null;
    // Held from before the request, so no tab starts a conversion early that would replace this one.
    const release = holdConversion();
    setConversionProblem(null);
    const body = target.key === '{}' ? undefined : (JSON.parse(target.key) as PlaybackSessionRequest);
    // The first start is clamped like the early start (startPoint), so both ask for the same position; the
    // options (and so the duration) arrive in the same render that turns `converts` on.
    const from = restartedRef.current ? target.start : startPoint(target.start, duration);
    askedRef.current = from;
    startLocalPlaybackSession(itemId, from, body).then(
      (next) => {
        started = next.session_id;
        adoptSpeculativeStart(itemId, next.session_id); // the server returns the early session for the same start
        if (active) setSession(next);
        else void stopLocalPlaybackSession(next.session_id).catch(() => undefined);
      },
      (error: unknown) => {
        release();
        if (active) setConversionProblem(error instanceof Error ? error.message : 'This file could not be converted for playback.');
      },
    );
    return () => {
      active = false;
      release();
      if (started) void stopLocalPlaybackSession(started).catch(() => undefined);
    };
  }, [converts, itemId, target]);

  // A layout effect: hls.js detaches before the player hands its element on (picture-in-picture into the next item)
  // or loads the next source into it, so it never empties the element under the next item.
  useLayoutEffect(() => {
    const media = mediaRef.current;
    if (!session || !media || nativeHls()) return;
    let hls: import('hls.js').default | null = null;
    let onSeeking: (() => void) | null = null;
    let stopFirstFrame: (() => void) | null = null;
    let cancelled = false;
    // hls.js loads only when a converted session needs it; direct play never downloads it.
    import('hls.js').then(({ default: Hls }) => {
      if (cancelled) return;
      if (!Hls.isSupported()) {
        setConversionProblem('This browser cannot play converted media.');
        return;
      }
      // timelineOffset keeps media time absolute, and the duration override spans the
      // whole file, so every seek path (bar, chapters, resume) can target any position.
      const player = new Hls({
        autoStartLoad: false, timelineOffset: session.start, xhrSetup: (xhr) => { xhr.withCredentials = true; },
        // Fetch the first fragment at once and keep the first buffer short.
        startFragPrefetch: true, maxBufferLength: START_BUFFER_SECONDS,
      });
      stopFirstFrame = onFirstFrame(media, () => { if (player.config) player.config.maxBufferLength = NORMAL_BUFFER_SECONDS; });
      hls = player;
      // Registered before hls.js attaches, so this runs before it reacts to the seek.
      onSeeking = () => {
        if (!needsRestart(media.currentTime, session.start, player.latestLevelDetails?.edge)) return;
        player.stopLoad();
        resumePlayingRef.current = !media.paused;
        restartedRef.current = true;
        setTarget((current) => ({ ...current, start: Math.floor(media.currentTime) }));
      };
      media.addEventListener('seeking', onSeeking);
      // hls.js treats an event playlist (no #EXT-X-ENDLIST while ffmpeg still writes it) as
      // live and ignores timelineOffset for the initial position, landing on its own live-edge
      // or level-start guess. Pin it to the asked position, which copied video's earlier
      // keyframe start (session.start) can precede (and resume — attachMedia on an
      // already-mounted <video> doesn't retrigger `autoplay`).
      const at = Math.max(session.start, askedRef.current);
      // Its own start then compares a relative position with the offset playlist start, falls back to the live
      // edge and adds timelineOffset again: whenever the playlist already lists that far (a fast encode), the first
      // fragment is the one at twice the asked position. Started once the playlist is in, at the asked position
      // relative to the session start, it loads that fragment first.
      player.once(Hls.Events.LEVEL_LOADED, () => player.startLoad(at - session.start));
      if (at) {
        player.once(Hls.Events.MANIFEST_PARSED, () => {
          media.currentTime = at;
          if (resumePlayingRef.current) void media.play().catch(() => undefined);
        });
      }
      player.on(Hls.Events.FRAG_LOADED, () => { arrivingRef.current = true; });
      player.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        player.destroy();
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR && fallBackRef.current()) return;
        const body = typeof data.response?.data === 'string' ? data.response.data : data.response?.text;
        const stop = accessStopCode(data.response?.code, body);
        if (stop) reportAccessStop(stop); // the app stops the player and shows the viewing-hours or time-up state
        else if (isStoppedByAdmin(data.response?.code, body)) setStoppedByAdmin(true);
        else setConversionProblem('Playback of the converted file failed.');
      });
      player.loadSource(`${apiBaseUrl}${session.playback_url}`);
      player.attachMedia(duration ? { media, overrides: { duration } } : media);
    }).catch(() => { if (!cancelled) setConversionProblem('Playback of the converted file failed.'); });
    return () => {
      cancelled = true;
      if (onSeeking) media.removeEventListener('seeking', onSeeking);
      stopFirstFrame?.();
      hls?.destroy();
    };
  }, [duration, session, mediaEpoch]);

  // A transcode that keeps pausing to buffer earns one lower-quality offer per session; it never changes by itself.
  const nextLower = session?.mode === 'transcode' ? lowerQuality(options, request?.max_height) : null;
  // Parents re-render on every timeupdate with fresh callbacks, so the stall count and the
  // offered-once memory live in refs that only a new session resets.
  const canOfferRef = useRef(false);
  canOfferRef.current = nextLower !== null && Boolean(onRequestChange);
  const stallsRef = useRef({ sessionId: null as string | null, at: [] as number[], offered: false });
  useEffect(() => {
    const media = mediaRef.current;
    if (!media || !session) return undefined;
    const stalls = stallsRef.current;
    if (stalls.sessionId !== session.session_id) Object.assign(stalls, { sessionId: session.session_id, at: [], offered: false });
    const onWaiting = () => {
      if (media.seeking || stalls.offered || !canOfferRef.current) return; // seeks are expected to buffer
      const now = Date.now();
      stalls.at = [...stalls.at.filter((at) => now - at < 60_000), now];
      if (stalls.at.length < 3) return;
      stalls.offered = true; // one offer per session, taken or declined
      setSuggestLower(true);
    };
    media.addEventListener('waiting', onWaiting);
    return () => media.removeEventListener('waiting', onWaiting);
  }, [session, mediaEpoch]);

  const base = { id: playId, kind, poster, autoPlay: !restartedRef.current || resumePlayingRef.current, tracks };
  let source: LuminaPlayerSource;
  // Direct resume: the first range request targets the start point. A start at or past the end
  // (a stale position, a moment in the credits) plays from the beginning instead of ending at once.
  const directFrom = probeFailed ? 0 : startPoint(directStart, duration);
  const fragment = directFrom ? `#t=${directFrom}` : '';
  if (stoppedByAdmin) source = { ...base, id: `${playId}:stopped`, src: null, state: 'failed', message: STOPPED_BY_ADMIN_MESSAGE };
  else if (probeFailed || (options?.mode === 'direct' && !converts)) source = { ...base, src: `${libraryMediaUrl(playId)}${fragment}`, state: 'loading' };
  else if (options?.mode === 'unavailable') source = { ...base, src: null, state: 'unsupported', message: 'This file has no playable audio or video.' };
  else if (conversionProblem) source = { ...base, id: `${playId}:failed`, src: null, state: 'failed', message: conversionProblem };
  else if (session) source = { ...base, id: `${playId}:converted`, src: nativeHls() ? `${apiBaseUrl}${session.playback_url}` : null, state: 'loading' };
  else source = { ...base, id: `${playId}:preparing`, src: null, state: 'loading' };

  const sessionRef = useRef(session);
  sessionRef.current = session;
  useEffect(() => {
    const media = mediaRef.current;
    const playStart = playStartRef.current;
    if (!media) return undefined;
    return onFirstFrame(media, (frameAt) => {
      releaseBackground(); // the page's side requests were held for this frame (backgroundGate.ts)
      if (playStart === null || ttffDoneRef.current) return;
      ttffDoneRef.current = true;
      recordMetric('ttff_ms', ttffLabel(sessionRef.current, directFrom), frameAt - playStart);
    });
  }, [source.id, directFrom]);

  // Data arriving but no frame within HEVC_FIRST_FRAME_MS: the browser cannot decode this HEVC after all.
  useEffect(() => {
    const media = mediaRef.current;
    if (!media || !hevcVideo) return undefined;
    arrivingRef.current = false;
    const onData = () => { arrivingRef.current = true; }; // readyState 1 = metadata, 2 = a frame decoded
    media.addEventListener('progress', onData);
    const timer = setTimeout(() => {
      if ((arrivingRef.current || media.readyState >= 1) && media.readyState < 2) fallBackRef.current();
    }, HEVC_FIRST_FRAME_MS);
    const stopFirstFrame = onFirstFrame(media, () => clearTimeout(timer));
    return () => { clearTimeout(timer); stopFirstFrame(); media.removeEventListener('progress', onData); };
  }, [hevcVideo, source.id, session]);

  const label = playbackFactsLabel(options);
  const quality = label ? <span className="quality-control" data-playback-mode={options?.mode}>{label}</span> : extensions?.quality;
  // A restart reloads metadata; the caller's resume must not re-apply over the member's seek or choice.
  // A direct file reloaded by a new version resumes where the member was.
  const loaded = (media: HTMLMediaElement) => {
    if (!restartedRef.current) {
      onLoadedMetadata?.(media);
      // A browser that ignored the #t= fragment, or a failed probe, still starts at the start point.
      const from = startPoint(directStart, media.duration);
      if (!session && from && media.currentTime < from - 1) media.currentTime = from;
    } else if (!session && target.start) media.currentTime = target.start;
  };
  return (
    <>
      <LuminaPlayer {...playerProps} onError={(media) => {
        const code = media.error?.code;
        if ((code === 3 || code === 4) && fallBackRef.current()) return true; // MEDIA_ERR_DECODE, MEDIA_ERR_SRC_NOT_SUPPORTED
        // A <video> error carries no status, so a failed direct file asks the server for one byte to learn why.
        if (!session) void fetch(libraryMediaUrl(playId), { credentials: 'include', headers: { Range: 'bytes=0-0' } }).then(async (response) => { const body = await response.text(); const stop = accessStopCode(response.status, body); if (stop) reportAccessStop(stop); else if (isStoppedByAdmin(response.status, body)) setStoppedByAdmin(true); }).catch(() => undefined);
        return playerProps.onError?.(media);
      }} extensions={{ ...extensions, quality }} onLoadedMetadata={loaded} onMediaReplaced={(at) => { askedRef.current = at; setMediaEpoch((epoch) => epoch + 1); }} onPlayIntentChange={(playing) => { resumePlayingRef.current = playing; playerProps.onPlayIntentChange?.(playing); }} ref={mediaRef} source={source} />
      {suggestLower && nextLower !== null ? (
        <div className="quality-suggestion" role="status">
          <span>This video keeps pausing to buffer.</span>
          <button onClick={() => { setSuggestLower(false); onRequestChange?.({ ...request, max_height: nextLower }); }} type="button">Switch to {nextLower}p</button>
          <button onClick={() => setSuggestLower(false)} type="button">Keep quality</button>
        </div>
      ) : null}
    </>
  );
}
