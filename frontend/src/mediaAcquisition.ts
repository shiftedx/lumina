import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiRequestError, createJob, getUpNext, previewUrl } from './api';
import { youtubeChannelId } from './features/channels/channelMention';
import { readString, remoteItemIsSaved, resultFromPreview } from './luminaModel';
import { acquisitionPlanForSource } from './sourcePreferences';
import type {
  AcquisitionErrorCategory,
  DownloadJob,
  FormatSelection,
  PreviewResponse,
  YouTubeSearchResult,
} from './types';
import type { WorkspaceAction, WorkspaceState } from './workspace';

export type AcquisitionFormat = 'best' | 'best_1080p' | 'best_editable' | 'audio_only';

export const acquisitionFormatOptions = [
  ['best', 'Best available', 'Highest quality video and audio'],
  ['best_1080p', 'Video · 1080p', 'A smaller, playback-friendly copy'],
  ['best_editable', 'Editable (H.264)', 'Opens in QuickTime, Preview, and editing apps'],
  ['audio_only', 'Audio only', 'Save this to your music collection'],
] as const;

export interface AcquisitionFailure {
  category: AcquisitionErrorCategory | null;
  message: string;
  phase: 'preview' | 'queue';
  retryItem?: YouTubeSearchResult;
}

export interface AcquisitionSelection {
  item: YouTubeSearchResult;
  preview: PreviewResponse | null;
}

export interface MediaAcquisitionState {
  selection: AcquisitionSelection | null;
  related: YouTubeSearchResult[];
  previewing: boolean;
  queueingSourceUrls: string[];
  format: AcquisitionFormat;
  failure: AcquisitionFailure | null;
}

interface MediaAcquisitionOptions {
  workspace: {
    state: Pick<WorkspaceState, 'currentUser' | 'jobs' | 'library' | 'preferences'>;
    dispatch: (action: WorkspaceAction) => void;
    captureSessionToken: () => number;
    isSessionTokenCurrent: (token: number) => boolean;
  };
  onMessage: (message: string) => void;
}

export interface MediaAcquisition {
  state: MediaAcquisitionState;
  open: (item: YouTubeSearchResult) => Promise<PreviewResponse | null> | null;
  queue: (item: YouTubeSearchResult, preview?: PreviewResponse | null, format?: AcquisitionFormat) => Promise<DownloadJob | null>;
  queueSelection: () => Promise<DownloadJob | null>;
  chooseFormat: (format: AcquisitionFormat) => void;
  isQueueing: (item: YouTubeSearchResult) => boolean;
  dropRelated: (predicate: (item: YouTubeSearchResult) => boolean) => void;
  reset: () => void;
  resetSession: () => void;
  clearFailure: () => void;
}

/**
 * Deep feature seam for Media acquisition. The shell supplies household state and
 * consumes outcomes; source validation, inspection identity, related discovery,
 * format choices, duplicate/concurrency guards, queue transitions,
 * session safety, and typed failures remain local. A request from an invalidated
 * session never mutates a later session, and one source has at most one active queue
 * submission even while distinct sources are queued concurrently.
 */
export function useMediaAcquisition(options: MediaAcquisitionOptions): MediaAcquisition {
  const { workspace } = options;
  const preferences = workspace.state.preferences;
  const preferredFormat: AcquisitionFormat = preferences.formatPreset;
  const [selection, setSelection] = useState<AcquisitionSelection | null>(null);
  const [related, setRelated] = useState<YouTubeSearchResult[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [queueingSourceUrls, setQueueingSourceUrls] = useState<string[]>([]);
  const [format, setFormat] = useState<AcquisitionFormat>(preferredFormat);
  const [failure, setFailure] = useState<AcquisitionFailure | null>(null);
  const previewGeneration = useRef(0);
  const previewChoiceKey = useRef<string | null>(null);
  const activeQueueRequests = useRef(new Map<string, symbol>());

  useEffect(() => setFormat(preferredFormat), [preferredFormat]);

  const sourcePlan = useCallback((choice: AcquisitionFormat) => acquisitionPlanForSource({
    formatPreset: choice,
    outputContainer: preferences.outputContainer,
    downloadSubtitles: preferences.downloadSubtitles,
    outputFolder: preferences.outputFolder,
  }), [preferences.downloadSubtitles, preferences.outputContainer, preferences.outputFolder]);

  const formatSelection = useCallback(
    (choice: AcquisitionFormat) => sourcePlan(choice).formatSelection,
    [sourcePlan],
  );

  const recordFailure = useCallback((phase: AcquisitionFailure['phase'], error: unknown, fallback: string, sourceUrl: string, retryItem?: YouTubeSearchResult) => {
    const category = error instanceof ApiRequestError ? error.category : null;
    setFailure({
      category,
      message: error instanceof Error ? error.message : fallback,
      phase,
      ...(retryItem ? { retryItem } : {}),
    });
  }, []);

  const open = useCallback((item: YouTubeSearchResult): Promise<PreviewResponse | null> | null => {
    const sourceUrl = sourceUrlFor(item);
    const generation = ++previewGeneration.current;
    setRelated([]);
    previewChoiceKey.current = null;
    setFailure(null);
    if (!sourceUrl) {
      setSelection(null);
      setPreviewing(false);
      setFailure({ category: null, message: 'This result does not include a playable address.', phase: 'preview' });
      return null;
    }
    const sessionToken = workspace.captureSessionToken();
    setSelection({ item, preview: null });
    setPreviewing(true);
    return (async () => {
      try {
        globalThis.performance?.mark?.('lumina:preview-start'); // time to first frame, step by step
        const inspected = await previewUrl({
          source_url: sourceUrl,
          lazy_playlist: true,
          format_selection: formatSelection(format),
        });
        globalThis.performance?.mark?.('lumina:preview-ready');
        if (generation !== previewGeneration.current || !workspace.isSessionTokenCurrent(sessionToken)) return null;
        const resolvedItem = resultFromPreview(inspected, item);
        setSelection({ item: resolvedItem, preview: inspected });
        previewChoiceKey.current = acquisitionChoiceKey(format, preferences);
        // Up Next / related media comes ranked from the one shared recommendation
        // policy (ADR 0007); the frontend ranks nothing. Failure is swallowed so a
        // recommendation error never interrupts playback of the current source.
        void getUpNext({
          source_url: inspected.webpage_url || sourceUrl,
          source_id: resolvedItem.id ?? null,
          source: resolvedItem.source,
          title: resolvedItem.title ?? null,
          uploader: resolvedItem.uploader ?? null,
          category_keys: relatedSubjectKeys(item),
          ...upNextChannel(inspected, resolvedItem),
        }).then((snapshot) => {
          if (generation === previewGeneration.current && workspace.isSessionTokenCurrent(sessionToken)) {
            setRelated(snapshot.items);
          }
        }).catch(() => undefined);
        return inspected;
      } catch (error) {
        if (generation === previewGeneration.current && workspace.isSessionTokenCurrent(sessionToken)) {
          recordFailure('preview', error, 'Unable to prepare this stream', sourceUrl, item);
        }
        return null;
      } finally {
        if (generation === previewGeneration.current && workspace.isSessionTokenCurrent(sessionToken)) setPreviewing(false);
      }
    })();
  }, [format, formatSelection, preferences, recordFailure, workspace]);

  const queue = useCallback(async (
    item: YouTubeSearchResult,
    existingPreview?: PreviewResponse | null,
    requestedFormat: AcquisitionFormat = format,
  ): Promise<DownloadJob | null> => {
    const sourceUrl = sourceUrlFor(item);
    const sessionToken = workspace.captureSessionToken();
    setFailure(null);
    if (!sourceUrl) {
      setFailure({ category: null, message: 'This video does not include a download address.', phase: 'queue' });
      return null;
    }
    if (remoteItemIsSaved(item, workspace.state.library)) {
      options.onMessage('This video is already in your vault.');
      return null;
    }
    if (workspace.state.jobs.some((job) => job.source_url === sourceUrl && ['queued', 'running', 'postprocessing'].includes(job.status))) {
      options.onMessage('This video is already in the download queue.');
      return null;
    }
    if (activeQueueRequests.current.has(sourceUrl)) {
      options.onMessage('This video is already being added to the download queue.');
      return null;
    }
    const requestIdentity = Symbol(sourceUrl);
    activeQueueRequests.current.set(sourceUrl, requestIdentity);
    setQueueingSourceUrls((current) => [...current, sourceUrl]);
    try {
      const plan = sourcePlan(requestedFormat);
      const choiceKey = acquisitionChoiceKey(requestedFormat, preferences);
      const canReusePreview = Boolean(existingPreview && previewChoiceKey.current === choiceKey);
      const inspected = canReusePreview ? existingPreview as PreviewResponse : await previewUrl({
          source_url: sourceUrl,
          lazy_playlist: true,
          format_selection: plan.formatSelection,
        });
      if (!workspace.isSessionTokenCurrent(sessionToken)) return null;
      if (!canReusePreview) {
        previewChoiceKey.current = choiceKey;
        setSelection((current) => current && sourceUrlFor(current.item) === sourceUrl
          ? { item: resultFromPreview(inspected, item), preview: inspected }
          : current);
      }
      const queued = await createJob({
        source_url: inspected.webpage_url || sourceUrl,
        preview_snapshot: {
          ...inspected.raw,
          title: inspected.title,
          thumbnail: readString(inspected.raw, 'thumbnail') || item.thumbnail,
        },
        format_selection: plan.formatSelection,
        output_profile: plan.outputProfile,
      });
      if (!workspace.isSessionTokenCurrent(sessionToken)) return null;
      workspace.dispatch({ type: 'jobs/upsert', job: queued, select: true });
      options.onMessage(requestedFormat === 'audio_only' ? 'Audio added to the download queue.' : 'Added to the download queue.');
      return queued;
    } catch (error) {
      if (workspace.isSessionTokenCurrent(sessionToken)) recordFailure('queue', error, 'Unable to add this video to the queue', sourceUrl, item);
      return null;
    } finally {
      if (activeQueueRequests.current.get(sourceUrl) === requestIdentity) {
        activeQueueRequests.current.delete(sourceUrl);
        setQueueingSourceUrls((current) => current.filter((candidate) => candidate !== sourceUrl));
      }
    }
  }, [format, options, preferences, recordFailure, sourcePlan, workspace]);

  const queueSelection = useCallback(() => {
    if (!selection) return Promise.resolve(null);
    return queue(selection.item, selection.preview, format);
  }, [format, queue, selection]);

  const chooseFormat = useCallback((choice: AcquisitionFormat) => {
    setFormat(choice);
    workspace.dispatch({ type: 'preferences/patch', patch: { formatPreset: choice } });
  }, [workspace]);

  const isQueueing = useCallback((item: YouTubeSearchResult) => {
    const sourceUrl = sourceUrlFor(item);
    return Boolean(sourceUrl && queueingSourceUrls.includes(sourceUrl));
  }, [queueingSourceUrls]);

  const dropRelated = useCallback((predicate: (item: YouTubeSearchResult) => boolean) => {
    // Drop suppressed candidates from the currently visible Up Next / related
    // rail at once. The shared policy keeps them out of every later fetch; this
    // only makes the removal immediate and reveals the next ranked candidate.
    setRelated((current) => current.filter((item) => !predicate(item)));
  }, []);

  const reset = useCallback(() => {
    previewGeneration.current += 1;
    previewChoiceKey.current = null;
    setSelection(null);
    setRelated([]);
    setPreviewing(false);
    setFailure(null);
  }, []);

  const resetSession = useCallback(() => {
    reset();
    // Hide the prior household member's activity, but retain live request
    // identities until their own finally blocks release the duplicate guards.
    setQueueingSourceUrls([]);
  }, [reset]);

  return {
    state: {
      selection,
      related,
      previewing,
      queueingSourceUrls,
      format,
      failure,
    },
    open,
    queue,
    queueSelection,
    chooseFormat,
    isQueueing,
    dropRelated,
    reset,
    resetSession,
    clearFailure: () => setFailure(null),
  };
}

function sourceUrlFor(item: YouTubeSearchResult): string | null {
  return item.webpage_url || (item.id ? `https://www.youtube.com/watch?v=${item.id}` : null);
}

/** The current video's channel for Up Next: the extractor's own record first, then the entry. */
export function upNextChannel(preview: PreviewResponse, item: YouTubeSearchResult): { channel_id?: string; channel_url?: string } {
  const channelId = youtubeChannelId({
    channel_id: readString(preview.raw, 'channel_id'),
    uploader_id: readString(preview.raw, 'uploader_id') || item.uploader_id,
    channel_url: readString(preview.raw, 'channel_url'),
    uploader_url: readString(preview.raw, 'uploader_url') || item.uploader_url,
  });
  const url = readString(preview.raw, 'channel_url') || readString(preview.raw, 'uploader_url') || item.uploader_url || '';
  return { ...(channelId ? { channel_id: channelId } : {}), ...(url && url.length <= 2_048 ? { channel_url: url } : {}) };
}

/** The current source's canonical subjects when opened from a recommendation surface. */
function relatedSubjectKeys(item: YouTubeSearchResult): string[] | undefined {
  const keys = (item as { category_keys?: unknown }).category_keys;
  return Array.isArray(keys) ? keys.filter((key): key is string => typeof key === 'string') : undefined;
}

function acquisitionChoiceKey(
  format: AcquisitionFormat,
  preferences: WorkspaceState['preferences'],
): string {
  return [format, preferences.downloadSubtitles ? 'subtitles' : '', preferences.outputContainer].join(':');
}

export function buildAcquisitionFormatSelection(
  choice: AcquisitionFormat,
  preferences: Pick<WorkspaceState['preferences'], 'downloadSubtitles' | 'outputContainer'>,
): FormatSelection {
  return acquisitionPlanForSource({
    formatPreset: choice,
    outputContainer: preferences.outputContainer,
    downloadSubtitles: preferences.downloadSubtitles,
    outputFolder: '',
  }).formatSelection;
}
