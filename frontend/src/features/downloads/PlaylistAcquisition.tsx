import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, LoaderCircle, RefreshCw, RotateCcw, X } from 'lucide-react';

import { createAcquisitionBatch, listAcquisitionBatches, retryAcquisitionEntry } from '../../api';
import type { AcquisitionBatch, AcquisitionBatchCreateRequest, FormatSelection, OutputProfile, PreviewEntry, PreviewResponse, YouTubeSearchResult } from '../../types';
import { capabilityActionDisabled, capabilityActionMessage, capabilityLifecycleLabel } from '../../sourceCapabilities';
import { formatBytes, formatDuration } from '../../utils';
import { Button, Checkbox, EmptyState, ErrorState, IconButton, Masthead, ProgressBar, Skeleton, StatusText, type StatusTone } from '../../ui';
import { GalleryArt } from '../gallery/GalleryArt';
import { LiveBadge, liveBadgeState } from '../gallery/LiveBadge';
import { remoteArt, remoteColour } from '../gallery/remoteModel';
import './downloads.css';

type PlaylistApi = {
  createBatch: typeof createAcquisitionBatch;
  listBatches: typeof listAcquisitionBatches;
  retryEntry: typeof retryAcquisitionEntry;
};

const playlistApi: PlaylistApi = {
  createBatch: createAcquisitionBatch,
  listBatches: listAcquisitionBatches,
  retryEntry: retryAcquisitionEntry,
};


function sourceForEntry(entry: PreviewEntry, preview: PreviewResponse): string | null {
  let webpageUrl: string | null = null;
  if (entry.webpage_url) {
    try {
      const parsed = new URL(entry.webpage_url);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') webpageUrl = parsed.toString();
    } catch {
      // A relative or malformed provider value is not a playable source.
    }
  }
  const youtube = [preview.extractor, preview.extractor_key].some((value) => value?.toLowerCase().includes('youtube'));
  return webpageUrl || (youtube && entry.id ? `https://www.youtube.com/watch?v=${encodeURIComponent(entry.id)}` : null);
}

function rawString(raw: Record<string, unknown>, key: string): string | undefined {
  const value = raw[key];
  return typeof value === 'string' && value.trim() ? value : undefined;
}

export function playlistBatchPayload(
  preview: PreviewResponse,
  selectedIndexes: number[],
  formatSelection: FormatSelection,
  outputProfile: OutputProfile,
): AcquisitionBatchCreateRequest | null {
  const entries = selectedIndexes.flatMap((index) => {
    const entry = preview.entries[index];
    const source = entry && sourceForEntry(entry, preview);
    return entry && source ? [{
      source_url: source,
      remote_id: entry.id,
      title: entry.title,
      thumbnail: entry.thumbnail,
      uploader: entry.uploader,
      duration: entry.duration,
      availability: entry.availability,
      extractor: preview.extractor,
    }] : [];
  });
  const sourceUrl = preview.webpage_url || rawString(preview.raw, 'original_url') || rawString(preview.raw, 'webpage_url');
  if (!sourceUrl || !entries.length) return null;
  return {
    source_url: sourceUrl,
    source_title: preview.title,
    source_provenance: {
      extractor: preview.extractor,
      extractor_key: preview.extractor_key,
      playlist_id: rawString(preview.raw, 'id'),
      playlist_title: preview.title,
      uploader: rawString(preview.raw, 'uploader'),
      channel: rawString(preview.raw, 'channel'),
      webpage_url: preview.webpage_url,
      thumbnail: rawString(preview.raw, 'thumbnail'),
    },
    format_selection: formatSelection,
    output_profile: outputProfile,
    entries,
  };
}

export function PlaylistAcquisitionPanel({
  preview,
  formatSelection,
  outputProfile,
  api = playlistApi,
  onQueued,
  onOpenEntry,
}: {
  preview: PreviewResponse;
  formatSelection: FormatSelection;
  outputProfile: OutputProfile;
  api?: PlaylistApi;
  onQueued?: (batch: AcquisitionBatch) => void;
  onOpenEntry?: (item: YouTubeSearchResult) => void;
}) {
  const mounted = useRef(true);
  const selectable = useMemo(() => preview.entries.map((entry, index) => (
    sourceForEntry(entry, preview) && !capabilityActionDisabled(entry.capabilities, 'acquire') ? index : -1
  )).filter((index) => index >= 0), [preview]);
  const [selected, setSelected] = useState(() => new Set(selectable));
  const [queueing, setQueueing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const allSelected = selectable.length > 0 && selected.size === selectable.length;

  useEffect(() => setSelected(new Set(selectable)), [selectable]);
  useEffect(() => () => { mounted.current = false; }, []);

  async function queueSelected() {
    const payload = playlistBatchPayload(preview, [...selected].sort((a, b) => a - b), formatSelection, outputProfile);
    if (!payload) {
      setError('Choose at least one available video.');
      return;
    }
    setQueueing(true);
    setError(null);
    try {
      const batch = await api.createBatch(payload);
      if (!mounted.current) return;
      onQueued?.(batch);
    } catch (failure) {
      if (mounted.current) setError('The selected videos could not be queued. Check access and try again.');
    } finally {
      if (mounted.current) setQueueing(false);
    }
  }

  const selectedBytes = [...selected].reduce((sum, index) => sum + (preview.entries[index]?.size_bytes ?? 0), 0);
  const toggle = (index: number) => setSelected((current) => { const next = new Set(current); if (next.has(index)) next.delete(index); else next.add(index); return next; });

  return <section aria-labelledby="playlist-acquisition-title" className="playlist-acquisition g-playlist">
    <Masthead headingId="playlist-acquisition-title" kicker={preview.title} level={2} lede="Choose one, a few, or the whole playlist. Each video keeps its own outcome." title="Keep videos from this playlist" />
    <Checkbox checked={allSelected} disabled={queueing || !selectable.length} indeterminate={selected.size > 0 && !allSelected} label={`Select all ${selectable.length}`} onChange={() => setSelected(allSelected ? new Set() : new Set(selectable))} />
    <ul className="g-playlist-list">{preview.entries.map((entry, index) => {
      const available = selectable.includes(index);
      const lifecycleLabel = capabilityLifecycleLabel(entry.capabilities?.lifecycle);
      const playUnavailable = capabilityActionDisabled(entry.capabilities, 'play');
      const unavailableCopy = capabilityActionMessage(entry.capabilities?.acquire_reason, 'acquire') || capabilityActionMessage(entry.capabilities?.play_reason, 'play');
      const title = entry.title || `Video ${index + 1}`;
      return <li aria-disabled={available ? undefined : 'true'} className="g-playlist-row" key={entry.id || entry.webpage_url || index}>
        <Checkbox checked={selected.has(index)} disabled={!available || queueing} label={<span className="sr-only">{`Video ${index + 1}: ${title}`}</span>} onChange={() => toggle(index)} />
        <span className="g-playlist-thumb"><GalleryArt alt="" art={remoteArt(entry.artwork_url)} card={{ name: title }} colour={remoteColour({ id: entry.id ?? String(index), webpage_url: entry.webpage_url ?? '' })} kind="still" priority={2} sizes="96px" /></span>
        <span className="g-playlist-copy">
          <span className="g-list-row-title g-clamp-2">{title}{liveBadgeState(entry.capabilities?.lifecycle) ? <><LiveBadge className="g-live-inline" startsAt={entry.capabilities?.scheduled_start} state={liveBadgeState(entry.capabilities?.lifecycle)!} surface="paper" /><span className="sr-only">{lifecycleLabel}</span></> : null}</span>
          <span className="g-list-row-meta">{[entry.uploader, entry.duration ? formatDuration(entry.duration) : null, unavailableCopy].filter(Boolean).join(' · ')}</span>
          {available ? null : <StatusText tone="muted">Unavailable</StatusText>}
        </span>
        {onOpenEntry ? <Button aria-label={`Play ${title}`} disabled={playUnavailable} onClick={() => { const source = sourceForEntry(entry, preview); if (source) onOpenEntry({ id: entry.id, title: entry.title, uploader: entry.uploader, duration: entry.duration, thumbnail: entry.thumbnail, artwork_url: entry.artwork_url, webpage_url: source, availability: entry.availability, capabilities: entry.capabilities }); }} variant="quiet">Play</Button> : null}
      </li>;
    })}</ul>
    <div className="g-playlist-footer">
      <span aria-live="polite" className="g-tabular">{selected.size} of {selectable.length} selected{selectedBytes ? ` · about ${formatBytes(selectedBytes)}` : ''}</span>
      <Button aria-disabled={selected.size === 0 || undefined} busy={queueing} disabled={selected.size === 0} onClick={() => void queueSelected()} variant="primary">{`Keep ${selected.size} videos`}</Button>
    </div>
    {error ? <ErrorState onRetry={() => setError(null)} retryLabel="Dismiss" title={error} /> : null}
  </section>;
}

export function AcquisitionBatchActivity({ api = playlistApi, refreshKey = 0 }: { api?: PlaylistApi; refreshKey?: number }) {
  const mounted = useRef(true);
  const requestGeneration = useRef(0);
  const [batches, setBatches] = useState<AcquisitionBatch[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState<ReadonlySet<string>>(new Set());

  const refresh = useCallback(async () => {
    const generation = ++requestGeneration.current;
    try {
      const details = await api.listBatches();
      if (!mounted.current || generation !== requestGeneration.current) return;
      setBatches(details);
      setError(null);
    } catch (failure) {
      if (!mounted.current || generation !== requestGeneration.current) return;
      setError('Playlist activity is unavailable. Try again without changing members.');
    } finally {
      if (mounted.current && generation === requestGeneration.current) setLoading(false);
    }
  }, [api]);

  useEffect(() => { mounted.current = true; void refresh(); return () => { mounted.current = false; requestGeneration.current += 1; }; }, [refresh, refreshKey]);
  useEffect(() => {
    if (!batches.some(acquisitionBatchNeedsPolling)) return undefined;
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 3000);
    return () => window.clearInterval(timer);
  }, [batches, refresh]);

  async function retry(batchId: string, entryId: string) {
    setRetrying((current) => new Set(current).add(entryId));
    try {
      const next = await api.retryEntry(batchId, entryId);
      if (!mounted.current) return;
      setBatches((current) => [next, ...current]);
      setError(null);
    } catch (failure) {
      if (mounted.current) {
        setError('That video could not be retried. Refresh activity and try again.');
        void refresh();
      }
    } finally {
      if (mounted.current) setRetrying((current) => { const next = new Set(current); next.delete(entryId); return next; });
    }
  }

  return <section aria-labelledby="playlist-activity-title" className="batch-activity g-downloads-section">
    <h2 id="playlist-activity-title">Batch saves<IconButton icon={<RefreshCw />} label="Refresh batch saves" onClick={() => void refresh()} /></h2>
    <p className="g-list-row-meta">Playlists and collections saved to your vault together. Every selected video advances and retries independently.</p>
    {loading ? <Skeleton count={2} label="Loading batch saves" shape="row" /> : null}
    {!loading && !batches.length && !error ? <EmptyState body="Playlists and collections you save to your vault appear here with per-video progress." size="inline" title="No batch saves yet." /> : null}
    <ul>{batches.map((batch) => <li className="g-list-row g-batch" key={batch.id}>
      <div className="g-job-copy">
        <span className="g-list-row-title g-clamp-2">{batch.source_title || 'Playlist download'}</span>
        <span className="g-list-row-meta">{batch.selected_count} selected · {batch.duplicate_count ? `${batch.duplicate_count} already queued · ` : ''}{batch.failed_count ? `${batch.failed_count} need attention` : `${batch.progress}% complete`}</span>
        <ProgressBar label={`${batch.progress} percent complete`} value={batch.progress} />
        <StatusText tone={BATCH_TONE[batch.status] ?? 'muted'}>{batch.status}</StatusText>
      </div>
      {batch.entries.length ? <ul className="g-batch-entries">{batch.entries.map((entry) => <li key={entry.id}>
        <span aria-hidden="true">{entry.status === 'completed' ? <Check /> : entry.status === 'duplicate' ? <RotateCcw /> : ['queued', 'running', 'dispatching'].includes(entry.status) ? <LoaderCircle className={entry.status === 'running' ? 'spin' : ''} /> : <X />}</span>
        <div className="g-job-copy"><span className="g-list-row-title">{entry.title || 'Untitled video'}</span><span className="g-list-row-meta">{entry.status === 'duplicate' ? 'Already downloading or saved' : entry.error || `${entry.progress}% · ${entry.status}`}</span></div>
        {['failed', 'cancelled'].includes(entry.status) ? <IconButton busy={retrying.has(entry.id)} icon={<RotateCcw />} label={`Retry ${entry.title || 'video'}`} onClick={() => void retry(batch.id, entry.id)} /> : null}
      </li>)}</ul> : null}
    </li>)}</ul>
    {error ? <ErrorState onRetry={() => void refresh()} title={error} /> : null}
  </section>;
}

const BATCH_TONE: Record<string, StatusTone> = { partial: 'attention', failed: 'danger', completed: 'ok' };

export function acquisitionBatchNeedsPolling(batch: AcquisitionBatch): boolean {
  return !batch.finished_at && ['dispatching', 'queued', 'partial'].includes(batch.status);
}

