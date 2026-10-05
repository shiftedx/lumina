import { type ComponentProps, type KeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { useAccess, useCanDownload, watchGate } from '../access/access';
import { ArrowDownToLine, Check, ChevronDown, ChevronLeft, Download, ExternalLink, LoaderCircle, Plus, RotateCcw, Share2 } from 'lucide-react';
import { clearRemotePlaybackProgress, getLibraryUpNext, getMediaSegments, getMuteRanges, getRecap, getRemotePlaybackProgress, getTitle, type Summary, updateRemotePlaybackProgress } from '../../api';
import { type AlbumTrack, type DownloadJob, type LibraryItem, type LibraryNote, type LocalPlaybackOptions, type MediaSegment, type MuteRange, type PlaybackProgress, type PlaybackSessionRequest, type SubtitleTrack, type TitleSummary, type PreviewResponse, type RemotePlaybackProgress, type SourceAutomation, type WatchQueueEntry, type YouTubeSearchResult } from '../../types';
import { EXTERNAL_LIBRARY, isAudioItem, jobProgress, libraryThumbnail, readNumber, readString, remoteItemIsSaved } from '../../luminaModel';
import { Avatar } from '../../ui';
import { RemotePlayer, RemotePlayerLoading } from '../../remotePlayer';
import { ChatReplayRail, LibraryCapturedChatRail, SourceChatUnavailableNote } from '../../chatReplayRail';
import { LiveRecordingControls } from '../../liveRecordingControls';
import { Artwork } from '../../Artwork';
import { LuminaPlayer, PlayerPreferencesContext, type PlayerAudio } from '../../LuminaPlayer';
import { LocalLibraryPlayer, loudnessGain } from '../../localPlayer';
import { ChapterSeekMarkers, DescriptionWithTimestamps, type TimelineChapter } from '../../chapters';
import { TheaterModeControl } from '../../theaterMode';
import { findChannelBySource } from '../../channelSubscriptions';
import { distinctRemoteSources, PLAYBACK_CHECKPOINT_INTERVAL_SECONDS, remoteSourceIdentity, shouldSavePlaybackCheckpoint } from '../../playbackModel';
import { type AcquisitionFormat, acquisitionFormatOptions } from '../../mediaAcquisition';
import { capabilityActionDisabled, capabilityActionMessage } from '../../sourceCapabilities';
import { formatCompactNumber, formatDuration } from '../../utils';
import { formatPublished } from '../media/MediaCards';
import { youtubeChannelId } from '../channels/channelMention';
import { useAutoplayEligible } from '../reco/recoFeedback';
import { WatchRelated } from './WatchRelated';
import { LibraryUpNextRail, nextInUpNext, previousInUpNext } from './LibraryUpNextRail';
import { WatchTools } from './WatchTools';
import { HouseholdNotesPanel } from './HouseholdNotesPanel';
import { firstPlayableQueued, removeQueued, useWatchQueue, WatchQueuePanel } from './WatchQueue';
import { MiniPlayer } from './MiniPlayer';
import { AudioStage, setAction } from './AudioStage';
import { TranscriptPanel, useEnrichment, useTranscripts } from './TranscriptPanel';
import { SummaryPanel } from './SummaryPanel';
import { buildMoments, MomentsPanel } from './MomentsPanel';
import { IdeaGraphPanel } from './IdeaGraphPanel';
import { ProvenancePanel } from './ProvenancePanel';
import { DEFAULT_AUTO_SKIP, DEFAULT_PROFANITY, type PlaybackPrefs } from '../../workspace';
import { moveFocus } from '../media/focusNav';
import { episodeCode, useFetched } from '../titles/titleModel';
import type { CaptionPrefs } from './captionPrefs';
import { PlaybackMenu, subtitleTrackSources, useSubtitleTracks } from './PlaybackMenu';
import { type UpNext, PlayerOverlays } from './PlayerOverlays';
import { showTextTrack } from './playerSegments';
import { stepFrom } from '../gallery/albumQueue';
import { useMediaQuery } from '../gallery/WallGrid';
import { WatchInfoColumn } from './WatchInfoColumn';
import { WatchChatSeam } from './WatchChatSeam';
import { useLiveViewers } from './useLiveViewers';
import { bylineChannelId, isWebVideo, watchBadge, watchMeta, watchProvider } from './watchInfoModel';

const NO_CHAPTERS: TimelineChapter[] = [];
const NO_SEGMENTS: MediaSegment[] = [];
const DEFAULT_PLAYBACK_PREFS: PlaybackPrefs = { normalizeLoudness: true, autoSkip: DEFAULT_AUTO_SKIP, profanity: DEFAULT_PROFANITY };
const NO_REQUEST: PlaybackSessionRequest = {};
const NO_MUTE: MuteRange[] = [];
const NO_TRACKS: AlbumTrack[] = [];

export type WatchSelection =
  | { kind: 'library'; item: LibraryItem }
  | { kind: 'remote'; item: YouTubeSearchResult; preview: PreviewResponse | null };

/** A generic web source with no channel metadata is attributed to its site, never guessed. */
function siteName(url: string | null | undefined): string | null {
  try { return url ? new URL(url).hostname.replace(/^www\./, '') : null; } catch { return null; }
}

export function createPlaybackCheckpointClientId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  return `player-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

/** The chat seam with the phone query read here, so movie and episode pages never subscribe to it. */
function WatchChatSeamForViewport(props: Omit<ComponentProps<typeof WatchChatSeam>, 'phone'>) {
  return <WatchChatSeam {...props} phone={useMediaQuery('(max-width: 599px)')} />;
}

export function WatchSurface({
  selection,
  previewLoading,
  related,
  jobs,
  channels,
  library,
  downloadQuality,
  downloadMenuOpen,
  busy,
  playback,
  curation,
  onBack,
  onDownload,
  onDownloadQuality,
  onToggleDownloadMenu,
  onCloseDownloadMenu,
  onOpenRelated,
  onOpenQueued = () => undefined,
  onOpenLibraryItem,
  onFollow,
  onOpenDownloads,
  onPlaybackCheckpoint,
  onReacquireRemote,
  onRestart,
  onClearPlayback,
  playlistAcquisition,
  theaterMode = false,
  onTheaterModeChange = () => undefined,
  autoplayUpNext = true,
  onAutoplayUpNextChange = () => undefined,
  mini = false,
  onExpand = () => undefined,
  onClose = () => undefined,
  sourceProblem = null,
  playerVolume,
  onPlayerVolumeChange,
  captions,
  onCaptionsChange,
  onOpenTitle,
  playbackPrefs,
  onPlaybackPrefsChange,
  startSeconds = null,
  startNonce,
}: {
  selection: WatchSelection;
  previewLoading: boolean;
  related: YouTubeSearchResult[];
  jobs: DownloadJob[];
  channels: SourceAutomation[];
  library: LibraryItem[];
  downloadQuality: AcquisitionFormat;
  downloadMenuOpen: boolean;
  busy: boolean;
  playback: PlaybackProgress | null;
  curation?: ReactNode;
  onBack: () => void;
  onDownload: () => void;
  onDownloadQuality: (quality: AcquisitionFormat) => void;
  onToggleDownloadMenu: () => void;
  onCloseDownloadMenu: () => void;
  onOpenRelated: (item: YouTubeSearchResult) => void;
  onOpenQueued?: (entry: WatchQueueEntry) => void;
  onOpenLibraryItem?: (id: string) => void;
  onFollow: () => void;
  onOpenDownloads: () => void;
  onPlaybackCheckpoint: (item: LibraryItem, position: number, duration: number | null, completed: boolean) => void;
  onReacquireRemote?: (item: YouTubeSearchResult) => Promise<boolean>;
  onRestart: (item: LibraryItem) => void;
  onClearPlayback: (item: LibraryItem) => void;
  playlistAcquisition?: ReactNode;
  theaterMode?: boolean;
  onTheaterModeChange?: (theaterMode: boolean) => void;
  autoplayUpNext?: boolean;
  onAutoplayUpNextChange?: (autoplayUpNext: boolean) => void;
  /** Docked mini-player: the same surface (and media element) stays mounted, only its placement changes. */
  mini?: boolean;
  onExpand?: () => void;
  onClose?: () => void;
  /** Why the remote source could not be inspected (restricted, removed, …); never a sign-in prompt. */
  sourceProblem?: string | null;
  playerVolume?: number;
  onPlayerVolumeChange?: (volume: number) => void;
  captions?: CaptionPrefs;
  onCaptionsChange?: (next: CaptionPrefs) => void;
  /** Episode byline → its series page. */
  onOpenTitle?: (title: TitleSummary) => void;
  playbackPrefs?: PlaybackPrefs;
  onPlaybackPrefsChange?: (patch: Partial<PlaybackPrefs>) => void;
  /** The watch link's `t`: the local player starts there instead of the resume point. */
  startSeconds?: number | null;
  /** New for every Play with a `t`, so the same moment pressed again seeks again. */
  startNonce?: number;
}) {
  const canDownload = useCanDownload();
  const local = selection.kind === 'library' ? selection.item : null;
  // Where the local player starts: the link's `t`, else the resume point, read when the player
  // first shows for this item and moved only by a new `t`, so progress checkpoints and a reopen without `t`
  // never move it.
  const startRef = useRef<{ id: string; at: number; key?: number } | null>(null);
  if (local && !previewLoading && (startRef.current?.id !== local.id || (startSeconds !== null && startRef.current.key !== startNonce))) {
    startRef.current = { id: local.id, at: startSeconds ?? (playback && !playback.completed && playback.position_seconds > 0 ? playback.position_seconds : 0), key: startNonce };
  }
  const startAt = local && startRef.current?.id === local.id ? startRef.current.at : 0;
  const remote = selection.kind === 'remote' ? selection.item : null;
  const preview = selection.kind === 'remote' ? selection.preview : null;
  const capabilities = preview?.capabilities;
  // The player learns a live broadcast ended (relay refresh); the page stops claiming Live.
  const [liveEnded, setLiveEnded] = useState(false);
  // A scanned library file carries no metadata duration (probing fills it in later, asynchronously);
  // the playing element's own duration is the one source that's always right once it loads.
  const [mediaDuration, setMediaDuration] = useState<number | null>(null);
  // A remote source that failed inspection is honestly unavailable, not "still downloadable".
  const inspectionProblem = remote && !preview ? sourceProblem : null;
  const playUnavailable = Boolean(inspectionProblem) || capabilityActionDisabled(capabilities, 'play');
  const acquireUnavailable = Boolean(inspectionProblem) || capabilityActionDisabled(capabilities, 'acquire');
  const playUnavailableCopy = inspectionProblem || capabilityActionMessage(capabilities?.play_reason, 'play');
  const acquireUnavailableCopy = inspectionProblem ? null : capabilityActionMessage(capabilities?.acquire_reason, 'acquire');
  const raw = preview?.raw || local?.metadata_json || {};
  // A pasted link that never resolved keeps no "Loading video…" placeholder title.
  const title = preview?.title || (inspectionProblem ? remote?.title?.replace(/^Loading video…$/, 'Unavailable video') : remote?.title) || local?.title || 'Untitled video';
  // Imported files have no provider channel to follow: credit the series (or the import) instead.
  const imported = local?.extractor === EXTERNAL_LIBRARY;
  const uploader = imported ? local.uploader || local.playlist_name || 'Imported media' : readString(raw, 'uploader') || readString(raw, 'channel') || remote?.uploader || local?.uploader || siteName(preview?.webpage_url || remote?.webpage_url) || 'Unknown channel';
  const thumbnail = preview?.artwork_url || remote?.artwork_url || (local ? libraryThumbnail(local) : null);
  const duration = readNumber(raw, 'duration') || remote?.duration || local?.duration || mediaDuration;
  const views = readNumber(raw, 'view_count') || remote?.view_count || null;
  const description = readString(raw, 'description');
  const published = readString(raw, 'upload_date') || remote?.published_at;
  const sourceHeight = readNumber(raw, 'height');
  const knownResolution = readString(raw, 'resolution') || (sourceHeight ? `${sourceHeight}p` : null);
  const sourceUrl = preview?.webpage_url || remote?.webpage_url || local?.webpage_url || null;
  const remoteIdentity = useMemo(
    () => remote && sourceUrl ? remoteSourceIdentity({ ...remote, webpage_url: sourceUrl }, raw) : null,
    [remote, sourceUrl, raw],
  );
  const currentRelatedIdentity = useMemo(
    () => sourceUrl ? remoteSourceIdentity({ id: remote?.id, source: remote?.source, webpage_url: sourceUrl }, raw) : null,
    [raw, remote?.id, remote?.source, sourceUrl],
  );
  const visibleRelated = useMemo(
    () => distinctRemoteSources(related, currentRelatedIdentity, 8),
    [currentRelatedIdentity, related],
  );
  // Autoplay advances only to the first eligible PLAYABLE recommendation: a
  // known not-playable candidate (live/upcoming) never auto-advances, even if
  // the shared policy surfaced it for manual download.
  // The member's deliberate queue always plays before recommendations.
  const queuedNext = firstPlayableQueued(useWatchQueue().queue);
  // Autoplay never takes an exploration slot, nor a card the member just hid.
  const autoplayEligible = useAutoplayEligible();
  const autoplayIndex = useMemo(
    () => queuedNext ? -1 : visibleRelated.findIndex((item) => !capabilityActionDisabled(item.capabilities, 'play') && autoplayEligible(item)),
    [queuedNext, visibleRelated, autoplayEligible],
  );
  const activeJob = sourceUrl ? jobs.find((job) => job.source_url === sourceUrl && ['queued', 'running', 'postprocessing'].includes(job.status)) : null;
  const saved = Boolean(local) || (remote ? remoteItemIsSaved(remote, library) : false) || jobs.some((job) => job.source_url === sourceUrl && job.status === 'completed');
  const channelUrl = readString(raw, 'channel_url') || readString(raw, 'uploader_url');
  const following = Boolean(channelUrl && findChannelBySource(channels, channelUrl));
  // A web video (remote, or saved with no Media title) reads like a title page under its player.
  const webVideo = isWebVideo(selection);
  const provider = watchProvider(raw, remote, local);
  const badge = webVideo ? watchBadge(capabilities?.lifecycle, liveEnded) : null;
  const liveNow = badge === 'live';
  const viewers = useLiveViewers(liveNow ? sourceUrl : null, liveNow, remote?.view_count ?? readNumber(raw, 'concurrent_view_count') ?? null);
  const meta = !webVideo ? '' : watchMeta({ state: badge, published, views, duration, viewers: viewers.count, viewersAsOf: viewers.asOf, startedAt: readNumber(raw, 'release_timestamp'), startsAt: capabilities?.scheduled_start });
  const playerRef = useRef<HTMLMediaElement | null>(null);
  const downloadToggleRef = useRef<HTMLButtonElement>(null);
  const downloadMenuRef = useRef<HTMLDivElement>(null);
  const shareFallbackRef = useRef<HTMLInputElement>(null);
  const lastSavedPositionRef = useRef(playback?.position_seconds || 0);
  const remoteMutationRef = useRef<Promise<void>>(Promise.resolve());
  // A queued write must not be sent once the surface is gone (a member switch), under the next member's cookie.
  const mountedRef = useRef(true);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);
  const remoteRequestGenerationRef = useRef(0);
  const remoteInteractionGenerationRef = useRef(0);
  const activeRemoteIdentityRef = useRef(remoteIdentity);
  const checkpointClientIdRef = useRef<string | null>(null);
  if (!checkpointClientIdRef.current) checkpointClientIdRef.current = createPlaybackCheckpointClientId();
  const checkpointSequenceRef = useRef(0);
  const autoplayHandledRef = useRef<string | null>(null);
  const checkpointRevisionRef = useRef(0);
  const lastKeepaliveSignatureRef = useRef('');
  const latestPlaybackSampleRef = useRef<{ position: number; duration: number | null; completed: boolean } | null>(null);
  const remoteCheckpointSuppressionRef = useRef<{ identity: string; lastPosition: number; advancedSeconds: number } | null>(null);
  const [shareFeedback, setShareFeedback] = useState<{ kind: 'success' | 'error'; message: string; fallback: boolean } | null>(null);
  const [currentTime, setCurrentTime] = useState(playback?.position_seconds || 0);
  const [remoteProgress, setRemoteProgress] = useState<RemotePlaybackProgress | null>(null);
  const shareUrl = sourceUrl || window.location.href;
  const chapters = preview?.chapters || local?.chapters || NO_CHAPTERS;
  const [transcriptRevision, setTranscriptRevision] = useState(0);
  const transcripts = useTranscripts(local?.id, transcriptRevision);
  const enrichment = useEnrichment(Boolean(local));
  // Media vault: the item's title, its next episode, subtitle tracks, segments and session choices.
  // Every fetch fails soft: without these endpoints the file still plays as before.
  const prefs = playbackPrefs ?? DEFAULT_PLAYBACK_PREFS;
  const watchTitle = useFetched(local?.title_id ?? null, () => getTitle(local?.title_id as string));
  const episode = watchTitle.data?.type === 'episode' ? watchTitle.data : null;
  // Up next for a titled item (an episode's following episodes, a movie's collection); untitled items keep related videos.
  const libraryUpNext = useFetched(local?.title_id && !isAudioItem(local) ? local.id : null, () => getLibraryUpNext(local?.id as string)).data;
  const nextTitle = useMemo(() => nextInUpNext(libraryUpNext), [libraryUpNext]);
  const previousTitle = useMemo(() => previousInUpNext(libraryUpNext), [libraryUpNext]);
  // Never advance on its own once the member's watching time or hours are over (the stop state takes over).
  const gated = watchGate(useAccess()) !== null;
  // Music: a track or saved audio plays on the audio stage. An album track continues the
  // member's stored order (Shuffle, Play all) while that holds this track, else the album's; read once per track,
  // because the order is stored before the track opens.
  const audio = local && isAudioItem(local) ? local : null;
  const album = audio && watchTitle.data?.type === 'album' ? watchTitle.data : null;
  const nextTrack = useMemo(() => (album && audio ? stepFrom(audio.id, album.tracks ?? NO_TRACKS, 1) : null), [album, audio]);
  const previousTrack = useMemo(() => (album && audio ? stepFrom(audio.id, album.tracks ?? NO_TRACKS, -1) : null), [album, audio]);
  const [playbackOptions, setPlaybackOptions] = useState<LocalPlaybackOptions | null>(null);
  const [sessionRequest, setSessionRequest] = useState<PlaybackSessionRequest>(NO_REQUEST);
  const [subtitle, setSubtitle] = useState<string | null>(null);
  // The file that actually plays: tracks, segments and mute ranges belong to the chosen version.
  const playId = sessionRequest.version_id ?? local?.id ?? null;
  const { tracks: subtitleTracks, reload: reloadSubtitleTracks } = useSubtitleTracks(playId);
  const selectedTextTrack = subtitleTracks.some((track) => track.id === subtitle && track.format === 'text') ? subtitle : null;
  // The chosen text track renders as <track default>, so a session restart's new media element keeps it.
  const playerTracks = useMemo(() => subtitleTrackSources(subtitleTracks).map((track) => ({ ...track, default: track.id === selectedTextTrack })), [subtitleTracks, selectedTextTrack]);
  const segments = useFetched(playId, () => getMediaSegments(playId as string)).data?.segments ?? NO_SEGMENTS;
  const recap = useFetched(episode?.id ?? null, () => getRecap(episode?.id as string)).data ?? null;
  const upNextCancelledRef = useRef(false);
  const upNext = useMemo<UpNext | null>(() => {
    if (!onOpenLibraryItem) return null;
    if (nextTrack) return { label: nextTrack.label, action: 'Next track', autoplay: true, onPlay: () => onOpenLibraryItem(nextTrack.itemId) };
    if (!nextTitle?.play_item_id) return null;
    const episodeNext = nextTitle.type === 'episode';
    return {
      label: [episodeCode(nextTitle), nextTitle.name].filter(Boolean).join(' · '),
      action: episodeNext ? 'Next episode' : 'Next film',
      still: nextTitle.poster?.url ?? nextTitle.poster_url,
      // A series or anime plays on by itself; a collection's next film waits for the member.
      autoplay: episodeNext,
      onPlay: () => onOpenLibraryItem(nextTitle.play_item_id as string),
    };
  }, [nextTitle, nextTrack, onOpenLibraryItem]);
  const autoAdvance = autoplayUpNext && !gated;
  useEffect(() => { showTextTrack(playerRef.current?.textTracks, selectedTextTrack); }, [selectedTextTrack, playerTracks, sessionRequest]);

  function chooseSubtitle(track: SubtitleTrack | null) {
    setSubtitle(track?.id ?? null);
    // Image subtitles are burned in by a session restart; text tracks switch in place.
    const burnedIn = track?.format === 'image' ? track.id : null;
    if ((sessionRequest.subtitle ?? null) !== burnedIn) setSessionRequest((current) => ({ ...current, subtitle: burnedIn }));
  }

  /** A version change drops the old file's subtitle choice (its stream ids belong to that file). */
  function changeSessionRequest(next: PlaybackSessionRequest) {
    if ((next.version_id ?? null) === (sessionRequest.version_id ?? null)) { setSessionRequest(next); return; }
    setSubtitle(null);
    setSessionRequest({ ...next, subtitle: null });
  }

  // Loudness × mute go through the one per-element audio graph, never a re-encode.
  const muteKey = playId && prefs.profanity.enabled ? `${playId}:${prefs.profanity.words.join('\n')}` : null;
  // refetched when the word list changes; the server reads the saved list, so an edit applies after the 650 ms autosave.
  const muteRanges = useFetched(muteKey, () => getMuteRanges(playId as string)).data ?? NO_MUTE;
  const playerAudio = useMemo<PlayerAudio>(
    () => ({ gain: loudnessGain(playbackOptions, prefs.normalizeLoudness), muteRanges }),
    [playbackOptions, prefs.normalizeLoudness, muteRanges],
  );
  // The Summary and Notes tools own their fetches and report here; rows are filtered by item id so a
  // previous item's summary/notes never leak into this item's moments during a switch.
  const [latestSummary, setLatestSummary] = useState<Summary | null>(null);
  const [notes, setNotes] = useState<LibraryNote[]>([]);
  const [notesRevision, setNotesRevision] = useState(0);
  const [showSuggested, setShowSuggested] = useState(true);
  const itemSummary = local && latestSummary?.library_item_id === local.id ? latestSummary : null;
  const moments = useMemo(
    () => buildMoments(chapters, itemSummary, local ? notes.filter((note) => note.item_id === local.id) : []),
    [chapters, itemSummary, local, notes],
  );
  const markers = useMemo(
    () => moments.filter((moment) => showSuggested || moment.origin !== 'suggested').map((moment) => ({ start_time: moment.seconds, title: moment.title, origin: moment.origin })),
    [moments, showSuggested],
  );
  const descriptionTimestamps = preview?.description_timestamps || local?.description_timestamps || [];
  const playerExtensions = {
    chapters: duration && markers.length ? <ChapterSeekMarkers chapters={markers} currentTime={currentTime} duration={duration} interactive={false} onSeek={seekPlayback} /> : undefined,
    theater: <TheaterModeControl onTheaterModeChange={onTheaterModeChange} shortcutEnabled={!mini} theaterMode={theaterMode} />,
    ...(local && !mini ? {
      menu: <PlaybackMenu captions={captions} capabilities={enrichment} onCaptionsChange={onCaptionsChange} currentVersionId={local.id} itemId={playId as string} onPrefs={onPlaybackPrefsChange ?? (() => undefined)} onRequestChange={changeSessionRequest} onSubtitle={chooseSubtitle} onTracksChanged={reloadSubtitleTracks} options={playbackOptions} prefs={prefs} request={sessionRequest} subtitle={subtitle} tracks={subtitleTracks} versions={watchTitle.data?.versions ?? []} />,
      // Music gets no up-next card: its countdown would cut the last 20 s off every song. The stage names the next track.
      overlay: <PlayerOverlays autoSkip={prefs.autoSkip} autoplay={autoAdvance} currentTime={currentTime} duration={duration} onCancelUpNext={() => { upNextCancelledRef.current = true; }} onSeek={seekPlayback} recap={recap} segments={segments} upNext={audio ? null : upNext} />,
    } : {}),
  };

  useEffect(() => {
    if (downloadMenuOpen) requestAnimationFrame(() => downloadMenuRef.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus());
  }, [downloadMenuOpen]);

  function closeDownloadMenuAndRestoreFocus() {
    onCloseDownloadMenu();
    requestAnimationFrame(() => downloadToggleRef.current?.focus());
  }

  function onDownloadOptionKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const direction = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
    if (!direction) return;
    event.preventDefault();
    const nextIndex = (index + direction + acquisitionFormatOptions.length) % acquisitionFormatOptions.length;
    const quality = acquisitionFormatOptions[nextIndex][0];
    onDownloadQuality(quality);
    requestAnimationFrame(() => document.getElementById(`watch-preset-${quality}`)?.focus());
  }

  useEffect(() => {
    lastSavedPositionRef.current = playback?.position_seconds || 0;
  }, [local?.id, playback?.position_seconds]);

  useEffect(() => {
    activeRemoteIdentityRef.current = remoteIdentity;
    const requestGeneration = ++remoteRequestGenerationRef.current;
    const interactionGeneration = remoteInteractionGenerationRef.current;
    setRemoteProgress(null);
    if (!remoteIdentity || playUnavailable) return undefined;
    lastSavedPositionRef.current = 0;
    checkpointRevisionRef.current = 0;
    const controller = new AbortController();
    void getRemotePlaybackProgress(remoteIdentity, controller.signal).then((progress) => {
      if (controller.signal.aborted || requestGeneration !== remoteRequestGenerationRef.current || remoteIdentity !== activeRemoteIdentityRef.current) return;
      // A seek or checkpoint made while history was loading is newer than this response.
      if (interactionGeneration !== remoteInteractionGenerationRef.current) return;
      checkpointRevisionRef.current = progress?.checkpoint_revision || 0;
      const visibleProgress = progress?.cleared ? null : progress;
      setRemoteProgress(visibleProgress);
      lastSavedPositionRef.current = visibleProgress?.position_seconds || 0;
      if (visibleProgress && !visibleProgress.completed) setCurrentTime(visibleProgress.position_seconds);
    }).catch(() => {
      // Remote playback remains available if personal history cannot be loaded.
    });
    return () => controller.abort();
  }, [playUnavailable, remoteIdentity]);

  useEffect(() => setShareFeedback(null), [shareUrl]);
  useEffect(() => setCurrentTime(playback?.position_seconds || 0), [local?.id, playback?.position_seconds, remote?.id]);
  useEffect(() => {
    const media = playerRef.current;
    if (!local || !playback || playback.completed || playback.position_seconds <= 0 || !media || media.readyState < HTMLMediaElement.HAVE_METADATA) return;
    const target = Number.isFinite(media.duration)
      ? Math.min(playback.position_seconds, Math.max(0, media.duration - 0.25))
      : playback.position_seconds;
    if (target > media.currentTime + 0.75) media.currentTime = target;
  }, [local?.id, playback]);

  useEffect(() => {
    if (!shareFeedback?.fallback) return;
    shareFallbackRef.current?.focus();
    shareFallbackRef.current?.select();
  }, [shareFeedback]);

  async function shareSource() {
    setShareFeedback(null);
    if (typeof navigator.clipboard?.writeText !== 'function') {
      setShareFeedback({ kind: 'error', message: 'Copy is unavailable in this browser. Use the share address below.', fallback: true });
      return;
    }
    try {
      await navigator.clipboard.writeText(shareUrl);
      setShareFeedback({ kind: 'success', message: 'Source address copied.', fallback: false });
    } catch {
      setShareFeedback({ kind: 'error', message: 'Lumina could not copy the source address. Use the share address below.', fallback: true });
    }
  }

  function nextCheckpointToken() {
    checkpointSequenceRef.current += 1;
    return {
      checkpoint_client_id: checkpointClientIdRef.current as string,
      checkpoint_sequence: checkpointSequenceRef.current,
    };
  }

  function rememberPlaybackSample(media: HTMLMediaElement) {
    latestPlaybackSampleRef.current = {
      position: Math.max(0, Math.floor(media.ended && Number.isFinite(media.duration) ? media.duration : media.currentTime)),
      duration: Number.isFinite(media.duration) ? Math.floor(media.duration) : duration,
      completed: media.ended,
    };
  }

  function saveRemoteCheckpoint(position: number, playerDuration: number | null, completed: boolean, keepalive = false) {
    if (!remote || !remoteIdentity || !sourceUrl) return;
    const interactionGeneration = ++remoteInteractionGenerationRef.current;
    // The channel travels with the checkpoint so watch depth keys to it. The extractor's own
    // record first, then the entry; only a UC id is a channel_id, and an over-long address is left out.
    const channelId = youtubeChannelId({
      channel_id: readString(raw, 'channel_id'),
      uploader_id: readString(raw, 'uploader_id') || remote?.uploader_id,
      channel_url: readString(raw, 'channel_url'),
      uploader_url: readString(raw, 'uploader_url') || remote?.uploader_url,
    });
    const channelUrl = readString(raw, 'channel_url') || readString(raw, 'uploader_url') || remote?.uploader_url || null;
    const payload = {
      source_identity: remoteIdentity,
      source_url: sourceUrl,
      title,
      uploader,
      artwork_url: thumbnail,
      position_seconds: position,
      duration_seconds: playerDuration,
      completed,
      ...nextCheckpointToken(),
      expected_revision: checkpointRevisionRef.current,
      ...(channelId ? { channel_id: channelId } : {}),
      ...(channelUrl && channelUrl.length <= 2_048 ? { channel_url: channelUrl } : {}),
    };
    const persist = async () => {
      if (!keepalive && !mountedRef.current) return;
      try {
        const savedProgress = await updateRemotePlaybackProgress(remoteIdentity, payload, { keepalive });
        if (activeRemoteIdentityRef.current !== remoteIdentity) return;
        checkpointRevisionRef.current = Math.max(checkpointRevisionRef.current, savedProgress.checkpoint_revision);
        const accepted = savedProgress.checkpoint_client_id === payload.checkpoint_client_id
          && savedProgress.checkpoint_sequence === payload.checkpoint_sequence;
        if (accepted && interactionGeneration === remoteInteractionGenerationRef.current) {
          setRemoteProgress(savedProgress.cleared ? null : savedProgress);
        }
      } catch {
        // Checkpoint failures must never interrupt playback.
      }
    };
    if (keepalive) {
      void persist();
      return;
    }
    remoteMutationRef.current = remoteMutationRef.current.catch(() => undefined).then(persist);
  }

  function checkpointPlayer(force = false, completed = false, keepalive = false) {
    const player = playerRef.current;
    if (!local && !remoteIdentity) return;
    if (player) rememberPlaybackSample(player);
    const sample = latestPlaybackSampleRef.current;
    if (!sample) return;
    const watchedToEnd = completed || sample.completed;
    const position = watchedToEnd && sample.duration !== null ? sample.duration : sample.position;
    const playerDuration = sample.duration;
    if (position <= 0 && !watchedToEnd) return;
    const suppression = remoteCheckpointSuppressionRef.current;
    if (suppression?.identity === remoteIdentity && !watchedToEnd) {
      const forwardDelta = position - suppression.lastPosition;
      if (forwardDelta > 0 && forwardDelta <= 5) suppression.advancedSeconds += forwardDelta;
      suppression.lastPosition = position;
      lastSavedPositionRef.current = position;
      if (suppression.advancedSeconds < PLAYBACK_CHECKPOINT_INTERVAL_SECONDS) return;
      remoteCheckpointSuppressionRef.current = null;
    }
    const keepaliveSignature = `${remoteIdentity || local?.id || ''}:${position}:${watchedToEnd}`;
    if (keepalive && keepaliveSignature === lastKeepaliveSignatureRef.current) return;
    if (!shouldSavePlaybackCheckpoint({ lastSavedPosition: lastSavedPositionRef.current, position, force })) return;
    lastSavedPositionRef.current = position;
    if (keepalive) lastKeepaliveSignatureRef.current = keepaliveSignature;
    if (local) onPlaybackCheckpoint(local, position, playerDuration, watchedToEnd);
    else saveRemoteCheckpoint(position, playerDuration, watchedToEnd, keepalive);
  }

  useEffect(() => {
    // Capture this media identity so cleanup cannot write an outgoing player's
    // timestamp into the next selection during a route change.
    const flush = () => checkpointPlayer(true, false, true);
    const onVisibilityChange = () => { if (document.visibilityState === 'hidden') flush(); };
    window.addEventListener('pagehide', flush);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('pagehide', flush);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      flush();
    };
  }, [local?.id, remoteIdentity]);

  function restartPlayback() {
    const player = playerRef.current;
    autoplayHandledRef.current = null;
    if (player) {
      player.currentTime = 0;
      void player.play();
    }
    latestPlaybackSampleRef.current = { position: 0, duration: Number.isFinite(player?.duration) ? Math.floor(player?.duration || 0) : duration, completed: false };
    remoteCheckpointSuppressionRef.current = null;
    lastSavedPositionRef.current = 0;
    if (local) onRestart(local);
    else saveRemoteCheckpoint(0, Number.isFinite(player?.duration) ? Math.floor(player?.duration || 0) : duration, false);
  }

  async function clearRemoteProgress() {
    if (!remoteIdentity) return;
    const player = playerRef.current;
    if (player) rememberPlaybackSample(player);
    const baseline = latestPlaybackSampleRef.current?.position || 0;
    remoteCheckpointSuppressionRef.current = { identity: remoteIdentity, lastPosition: baseline, advancedSeconds: 0 };
    lastSavedPositionRef.current = baseline;
    const interactionGeneration = ++remoteInteractionGenerationRef.current;
    try {
      let token = nextCheckpointToken();
      let cleared = await clearRemotePlaybackProgress(
        remoteIdentity,
        token.checkpoint_client_id,
        token.checkpoint_sequence,
        checkpointRevisionRef.current,
      );
      if (activeRemoteIdentityRef.current !== remoteIdentity) return;
      checkpointRevisionRef.current = Math.max(checkpointRevisionRef.current, cleared.checkpoint_revision);
      if (interactionGeneration !== remoteInteractionGenerationRef.current) return;
      if (cleared.checkpoint_client_id !== token.checkpoint_client_id || cleared.checkpoint_sequence !== token.checkpoint_sequence) {
        if (!mountedRef.current) return; // the retry would go out under the next member
        token = nextCheckpointToken();
        cleared = await clearRemotePlaybackProgress(
          remoteIdentity,
          token.checkpoint_client_id,
          token.checkpoint_sequence,
          checkpointRevisionRef.current,
        );
        if (activeRemoteIdentityRef.current !== remoteIdentity) return;
        checkpointRevisionRef.current = Math.max(checkpointRevisionRef.current, cleared.checkpoint_revision);
      }
      if (cleared.cleared && interactionGeneration === remoteInteractionGenerationRef.current && activeRemoteIdentityRef.current === remoteIdentity) {
        setRemoteProgress(null);
      }
    } catch {
      if (interactionGeneration === remoteInteractionGenerationRef.current) {
        remoteCheckpointSuppressionRef.current = null;
        lastSavedPositionRef.current = remoteProgress?.position_seconds || baseline;
      }
      // Clearing history is optional and must not stop the active stream.
    }
  }

  function seekPlayback(seconds: number) {
    const player = playerRef.current;
    if (!player) return;
    player.currentTime = seconds;
    setCurrentTime(seconds);
    checkpointPlayer(true);
  }

  function handlePlaybackEnded() {
    const playbackIdentity = currentRelatedIdentity
      || (local ? `library:${local.id}` : `remote:${remote?.id || sourceUrl || title}`);
    if (autoplayHandledRef.current === playbackIdentity) return;
    autoplayHandledRef.current = playbackIdentity;
    checkpointPlayer(true, true);
    // An episode continues its show unless the member cancelled the up-next card; a film never starts itself.
    if (upNext) { if (upNext.autoplay && autoAdvance && !upNextCancelledRef.current) upNext.onPlay(); return; }
    if (album) return; // the album's last track ends its queue
    if (autoAdvance) playNext();
  }

  function playNext() {
    if (upNext) upNext.onPlay();
    else if (queuedNext) playQueued(queuedNext);
    else if (autoplayIndex >= 0) onOpenRelated(visibleRelated[autoplayIndex]);
  }

  // The picture-in-picture window and the system media controls name the video and skip to the next (or previous)
  // one; music's audio stage keeps its own.
  const playNextRef = useRef(playNext);
  playNextRef.current = playNext;
  const canNext = Boolean(upNext || queuedNext || autoplayIndex >= 0);
  const previousId = previousTitle?.play_item_id ?? null;
  const artist = episode?.series_name || uploader;
  useEffect(() => {
    if (audio || !('mediaSession' in navigator) || typeof MediaMetadata === 'undefined') return undefined;
    const session = navigator.mediaSession;
    session.metadata = new MediaMetadata({ title, artist, artwork: thumbnail ? [{ src: thumbnail }] : [] });
    setAction(session, 'nexttrack', canNext ? () => playNextRef.current() : null);
    setAction(session, 'previoustrack', previousId && onOpenLibraryItem ? () => onOpenLibraryItem(previousId) : null);
    return () => {
      session.metadata = null;
      setAction(session, 'nexttrack', null);
      setAction(session, 'previoustrack', null);
    };
  }, [audio, title, artist, thumbnail, canNext, previousId, onOpenLibraryItem]);

  function playQueued(entry: WatchQueueEntry) {
    void removeQueued(entry.id);
    onOpenQueued(entry);
  }

  function resetAutoplayCycle(media: HTMLMediaElement) {
    if (!media.ended && (!Number.isFinite(media.duration) || media.currentTime < media.duration)) {
      autoplayHandledRef.current = null;
    }
  }

  const saveLabel = webVideo ? (activeJob ? 'Saving' : saved ? 'Saved' : 'Save to library') : (activeJob ? 'Downloading' : saved ? 'In your vault' : 'Download to vault');
  const actionButtons = (
    <>
      {canDownload ? <div className="download-control">
        <button aria-describedby={acquireUnavailableCopy ? 'watch-acquisition-unavailable' : undefined} className={`g-button is-primary ${activeJob ? 'downloading' : ''}`} data-focus-item disabled={busy || saved || Boolean(activeJob) || acquireUnavailable} onClick={onDownload} type="button">{busy ? <LoaderCircle className="spin" /> : activeJob ? <LoaderCircle className="spin" /> : saved ? <Check /> : <ArrowDownToLine />}{saveLabel}</button>
        {!saved && !activeJob && !acquireUnavailable ? <button aria-controls="download-quality-menu" aria-expanded={downloadMenuOpen} aria-label="Download options" className="g-button is-primary split-button" data-focus-item onClick={onToggleDownloadMenu} ref={downloadToggleRef} type="button"><ChevronDown /></button> : null}
        {downloadMenuOpen ? <div aria-label="Download quality" className="download-menu" id="download-quality-menu" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) onCloseDownloadMenu(); }} onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeDownloadMenuAndRestoreFocus(); } }} ref={downloadMenuRef} role="radiogroup"><p>Download quality</p>{acquisitionFormatOptions.map(([value, label, detail], index) => <button aria-checked={downloadQuality === value} className={downloadQuality === value ? 'selected' : ''} id={`watch-preset-${value}`} key={value} onClick={() => { onDownloadQuality(value); closeDownloadMenuAndRestoreFocus(); }} onKeyDown={(event) => onDownloadOptionKeyDown(event, index)} role="radio" tabIndex={downloadQuality === value ? 0 : -1} type="button"><span><strong>{label}</strong><small>{detail}</small></span>{downloadQuality === value ? <Check /> : null}</button>)}</div> : null}
      </div> : null}
      {canDownload && acquireUnavailableCopy ? <p className="capability-note" id="watch-acquisition-unavailable" role="status">{acquireUnavailableCopy}</p> : null}
      {remote && sourceUrl && playUnavailable ? <a className="g-button" data-focus-item href={sourceUrl} rel="noreferrer noopener" target="_blank"><ExternalLink /> Open original</a> : null}
      <button className="g-button" data-focus-item onClick={() => void shareSource()} type="button"><Share2 /> Share</button>
      {(local && playback && playback.position_seconds > 0) || (remoteProgress && remoteProgress.position_seconds > 0) ? <button className="g-button" data-focus-item onClick={restartPlayback} type="button"><RotateCcw /> Start over</button> : null}
      {local && playback ? <button className="g-text-button clear-playback" data-focus-item onClick={() => onClearPlayback(local)} type="button">Clear progress</button> : remoteProgress ? <button className="g-text-button clear-playback" data-focus-item onClick={() => void clearRemoteProgress()} type="button">Clear progress</button> : null}
    </>
  );
  const shareNote = shareFeedback ? <div className={`share-feedback ${shareFeedback.kind}`} role={shareFeedback.kind === 'error' ? 'alert' : 'status'}><span>{shareFeedback.message}</span>{shareFeedback.fallback ? <input aria-label="Share address" className="g-input" onFocus={(event) => event.currentTarget.select()} readOnly ref={shareFallbackRef} value={shareUrl} /> : null}</div> : null;
  const recordingControls = canDownload && remote && sourceUrl && !liveEnded && (capabilities?.lifecycle === 'live' || capabilities?.lifecycle === 'upcoming') ? (
      // "Record from now" (live) and "Schedule this broadcast" (upcoming,
      // issue #98) are deliberate durable actions distinct from live viewing:
      // they show the recording's two sibling outputs (media + chat), the
      // from-start intent choice, and honest partial / partial-history outcomes.
      <LiveRecordingControls capabilities={capabilities} sourceUrl={sourceUrl} />
  ) : null;
  const toolList = [
    webVideo ? null : {
      id: 'overview',
      label: 'Overview',
      panel: (
        <>
        <div className="description-card">
          <div className="description-meta">{views ? <strong>{formatCompactNumber(views)} views</strong> : null}{published ? <strong>{formatPublished(published)}</strong> : null}{duration ? <span>{formatDuration(duration)}</span> : null}</div>
          {description ? <DescriptionWithTimestamps description={`${description.slice(0, 900)}${description.length > 900 ? '…' : ''}`} onSeek={seekPlayback} timestamps={descriptionTimestamps.filter((token) => token.end <= 900)} /> : <p>Stream now, or keep this video in your household vault for later.</p>}
          {local ? null : <details><summary>Details &amp; provenance <ChevronDown /></summary><dl><div><dt>Original source</dt><dd>{readString(raw, 'extractor_key') || readString(raw, 'extractor') || 'Web video'}</dd></div>{knownResolution ? <div><dt>Known resolution</dt><dd>{knownResolution}</dd></div> : null}{sourceUrl ? <div><dt>Source address</dt><dd>{sourceUrl}</dd></div> : null}<div><dt>Availability</dt><dd>Streaming from source</dd></div></dl></details>}
        </div>
        {local ? <ProvenancePanel itemId={local.id} key={local.id} onOpenItem={onOpenLibraryItem} /> : null}
        </>
      ),
    },
    local ? { id: 'summary', label: 'Summary', panel: <SummaryPanel aiEnabled={enrichment?.ai_summaries} itemId={local.id} key={local.id} onLatest={setLatestSummary} onSeek={seekPlayback} transcripts={transcripts} /> } : null,
    local && itemSummary ? { id: 'ideas', label: 'Idea graph', panel: <IdeaGraphPanel itemId={local.id} key={local.id} onSeek={seekPlayback} summary={itemSummary} /> } : null,
    transcripts?.length || (transcripts && enrichment?.asr) ? { id: 'transcript', label: 'Transcript', panel: <TranscriptPanel asr={Boolean(enrichment?.asr)} currentTime={currentTime} itemId={local?.id} key={local?.id} onGenerated={() => setTranscriptRevision((value) => value + 1)} onSeek={seekPlayback} transcripts={transcripts} /> } : null,
    local || moments.length ? { id: 'moments', label: 'Moments', panel: <MomentsPanel currentTime={currentTime} getTime={() => playerRef.current?.currentTime || 0} itemId={local?.id} key={local?.id} moments={moments} onBookmarksChanged={() => setNotesRevision((revision) => revision + 1)} onSeek={seekPlayback} onShowSuggested={setShowSuggested} showSuggested={showSuggested} /> } : null,
    local ? { id: 'notes', label: 'Notes', panel: <HouseholdNotesPanel getTime={() => playerRef.current?.currentTime || 0} itemId={local.id} key={local.id} onNotes={setNotes} onSeek={seekPlayback} refreshKey={notesRevision} /> } : null,
    curation ? { id: 'library', label: 'Sharing & tags', panel: curation } : null,
    playlistAcquisition && canDownload ? { id: 'playlist', label: 'Playlist', panel: playlistAcquisition } : null,
  ];

  const playerPreferences = { volume: playerVolume, onVolumeChange: onPlayerVolumeChange, onNext: playNext, globalShortcuts: !mini, subtitle: episode ? episodeCode(episode) || undefined : uploader, theater: theaterMode, captions };

  return (
    <div
      className={mini ? 'watch-surface watch-mini' : 'surface watch-surface'}
      id={mini ? 'mini-player' : undefined}
      onClick={mini ? (event) => { if (!(event.target as Element).closest('.mini-player-bar, .lumina-player')) { const element = event.currentTarget.querySelector<HTMLMediaElement>('video, audio'); if (element?.paused) void element.play().catch(() => undefined); else element?.pause(); } } : undefined}
      onDoubleClick={mini ? (event) => { if (!(event.target as Element).closest('.mini-player-bar')) onExpand(); } : undefined}
      tabIndex={mini ? -1 : undefined}
    >
      <button className="watch-back g-text-button" onClick={onBack} type="button"><ChevronLeft /> Back</button>
      <div className={`watch-layout ${theaterMode ? 'theater' : 'standard'}${webVideo ? ' is-editorial' : ''}`} data-theater-mode={theaterMode ? 'theater' : 'standard'}>
        <div className="watch-main">
          <div className={`player-frame${audio ? ' has-audio-stage' : ''}`}>
            {audio ? <AudioStage album={album} item={audio} next={nextTrack} onPlayItem={(itemId) => onOpenLibraryItem?.(itemId)} onSeek={seekPlayback} previous={previousTrack} /> : null}
            <PlayerPreferencesContext.Provider value={playerPreferences}>
              {previewLoading ? <RemotePlayerLoading /> : local ? <LocalLibraryPlayer audio={playerAudio} extensions={playerExtensions} mediaRef={playerRef} onEnded={handlePlaybackEnded} onLoadedMetadata={(media) => { playerRef.current = media; if (Number.isFinite(media.duration)) setMediaDuration(media.duration); showTextTrack(media.textTracks, selectedTextTrack); }} onOptions={setPlaybackOptions} onRequestChange={changeSessionRequest} request={sessionRequest} startAt={startAt} startKey={startRef.current?.key} tracks={playerTracks} onPause={() => checkpointPlayer(true)} onSeeked={(media) => { resetAutoplayCycle(media); checkpointPlayer(true); }} onTimeUpdate={(media) => { resetAutoplayCycle(media); setCurrentTime(media.currentTime); checkpointPlayer(); }} itemId={local.id} kind={isAudioItem(local) ? 'audio' : 'video'} poster={thumbnail} title={title} /> : remote && !playUnavailable ? <RemotePlayer extensions={playerExtensions} onEnded={handlePlaybackEnded} onLiveEnded={() => setLiveEnded(true)} onMediaRef={(media) => { playerRef.current = media; }} onPause={() => checkpointPlayer(true)} onReacquire={onReacquireRemote ? () => onReacquireRemote(remote) : undefined} onSeeked={(media) => { resetAutoplayCycle(media); checkpointPlayer(true); }} onTimeUpdate={(media) => { resetAutoplayCycle(media); setCurrentTime(media.currentTime); checkpointPlayer(); }} playback={preview?.playback} poster={thumbnail} resumePosition={preview?.playback?.live ? null : remoteProgress && !remoteProgress.completed ? remoteProgress.position_seconds : null} title={title} /> : <LuminaPlayer extensions={playerExtensions} source={{ id: 'unavailable', kind: 'video', src: null, state: 'unsupported', message: playUnavailableCopy || 'You can still download this video to the vault.' }} title={title} />}
            </PlayerPreferencesContext.Provider>
          </div>
          {webVideo ? (
            <div className="gallery g-watch-body">
              <WatchInfoColumn
                actions={actionButtons}
                badge={badge}
                channel={{ name: uploader, id: bylineChannelId(raw, remote, provider), avatarUrl: remote?.channel_artwork_url ?? null, followers: readNumber(raw, 'channel_follower_count'), imported, following, onFollow }}
                chapters={chapters}
                currentTime={currentTime}
                description={description}
                feedback={shareNote}
                meta={meta}
                onSeek={seekPlayback}
                provider={provider}
                recording={recordingControls}
                saved={Boolean(local)}
                startsAt={capabilities?.scheduled_start}
                timestamps={descriptionTimestamps}
                title={title}
                tools={<WatchTools tools={toolList} />}
              />
              <aside aria-label="Watch context" className="g-watch-side">
                {local ? <LibraryCapturedChatRail currentTimeSeconds={currentTime} durationSeconds={duration} item={local} onSeek={seekPlayback} /> : null}
                {remote ? <WatchChatSeamForViewport capabilities={capabilities} currentTime={currentTime} duration={duration} ended={liveEnded} onSeek={seekPlayback} provider={provider.toLowerCase()} sourceIdentity={remoteIdentity} sourceUrl={sourceUrl} /> : null}
                <WatchQueuePanel autoplay={autoplayUpNext} onPlay={playQueued} />
                <WatchRelated autoplay={autoplayUpNext} autoplayIndex={autoplayIndex} items={visibleRelated} loading={previewLoading} onAutoplayChange={onAutoplayUpNextChange} onOpen={onOpenRelated} remote={Boolean(remote)} showAutoplay={Boolean(visibleRelated.length || queuedNext)} />
                <details className="g-watch-details">
                  <summary>Details &amp; provenance</summary>
                  {local ? <ProvenancePanel itemId={local.id} key={local.id} onOpenItem={onOpenLibraryItem} /> : (
                    <dl><div><dt>Original source</dt><dd>{readString(raw, 'extractor_key') || readString(raw, 'extractor') || 'Web video'}</dd></div>{knownResolution ? <div><dt>Known resolution</dt><dd>{knownResolution}</dd></div> : null}{sourceUrl ? <div><dt>Source address</dt><dd>{sourceUrl}</dd></div> : null}<div><dt>Availability</dt><dd>Streaming from source</dd></div></dl>
                  )}
                </details>
              </aside>
            </div>
          ) : (
            <>
              {/* Byline, Follow and the actions row are one arrow-key grid. */}
              <section className="watch-info" onKeyDown={moveFocus}>
                {episode?.series_id ? <p className="watch-episode-byline"><button className="g-text-button" data-focus-item onClick={() => onOpenTitle?.(episode)} type="button">{episode.series_name || 'Show'}</button> · {[episodeCode(episode), episode.name].filter(Boolean).join(' · ')}</p> : null}
                <h1>{title}</h1>
                <div className="watch-byline">
                  <Avatar decorative name={uploader} size={34} />
                  <div><strong>{uploader}</strong><small>{imported ? 'Imported · read-only' : readNumber(raw, 'channel_follower_count') ? `${formatCompactNumber(readNumber(raw, 'channel_follower_count') || 0)} followers` : 'Channel'}</small></div>
                  {imported ? null : <button className={`g-button${following ? ' is-quiet' : ''}`} data-focus-item onClick={onFollow} type="button">{following ? <Check /> : <Plus />}{following ? 'Following' : 'Follow channel'}</button>}
                </div>
                <div className="watch-actions" data-focus-row>{actionButtons}</div>
                {shareNote}
                {recordingControls}
              </section>
              <WatchTools tools={toolList} />
            </>
          )}
        </div>
        {webVideo ? null : (
          <aside aria-label="Watch context" className="watch-rail">
            {local ? (
              // Completed live-recording playback (#100 AC6, #110): the recording's
              // Library item shares its provider-scoped identity (youtube:<id> /
              // twitch:<stream_id>) with the member's captured timed chat asset, so
              // the rail mounts only when that member-owned capture exists. It reads
              // the durable asset — never a provider backfill — and any failure
              // renders nothing, so playback stays untouched.
              <LibraryCapturedChatRail
                currentTimeSeconds={currentTime}
                durationSeconds={duration}
                item={local}
                onSeek={seekPlayback}
              />
            ) : null}
            {remote && remoteIdentity && sourceUrl && capabilities?.chat?.replay === 'available' ? (
              <ChatReplayRail
                currentTimeSeconds={currentTime}
                durationSeconds={duration}
                onSeek={seekPlayback}
                sourceIdentity={remoteIdentity}
                sourceUrl={sourceUrl}
              />
            ) : null}
            {remote && !liveEnded && capabilities?.chat?.live_reason === 'authentication_required' ? <SourceChatUnavailableNote provider={capabilities.provider} /> : null}
            <WatchQueuePanel autoplay={autoplayUpNext} onPlay={playQueued} />
            {libraryUpNext ? <LibraryUpNextRail autoplay={autoplayUpNext} onAutoplayChange={onAutoplayUpNextChange} onOpenAll={episode ? () => onOpenTitle?.(episode) : undefined} onPlay={(itemId) => onOpenLibraryItem?.(itemId)} upNext={libraryUpNext} /> : null}
          </aside>
        )}
      </div>
      {mini ? <MiniPlayer onClose={onClose} onExpand={onExpand} title={title} /> : null}
      {activeJob ? <div className="g-notice queue-notice"><strong role="status">{activeJob.status === 'queued' ? 'Added to download queue' : 'Downloading to your vault'}</strong><span>{title}</span><b>{activeJob.status === 'queued' ? 'Waiting' : `${Math.round(jobProgress(activeJob))}%`}</b><button className="g-text-button" onClick={onOpenDownloads} type="button">View downloads</button></div> : null}
    </div>
  );
}
