import { useCallback, useEffect, useRef, useState } from 'react';

import type { LibraryItem, PlaybackProgress, TitleDetail, TitleSummary, TitleVersion } from '../../types';
import { formatBytes, formatDuration } from '../../utils';

/** TMDB's required notice (Settings › About and Settings › Media server). */
export const TMDB_ATTRIBUTION = 'This product uses the TMDB API but is not endorsed or certified by TMDB.';

type Numbered = { season_number?: number | null; index_number?: number | null; index_number_end?: number | null };

/** "S1 · E3", "S1 · E1–2" (one file, two episodes), "Special 2", "E4"; '' when unnumbered. */
export function episodeCode({ season_number: season, index_number: index, index_number_end: end }: Numbered): string {
  if (index == null) return season != null && season > 0 ? `S${season}` : '';
  const number = end != null && end > index ? `${index}–${end}` : `${index}`;
  if (season === 0) return `Special ${number}`;
  return season != null ? `S${season} · E${number}` : `E${number}`;
}

export const seasonLabel = (season: number): string => (season === 0 ? 'Specials' : `Season ${season}`);

/** Regular seasons ascending; Specials (0) and unnumbered last. */
export function orderSeasons<T extends { index_number?: number | null }>(seasons: T[]): T[] {
  const rank = (season: T) => season.index_number || Number.MAX_SAFE_INTEGER;
  return [...seasons].sort((a, b) => rank(a) - rank(b));
}

export function formatRuntime(seconds?: number | null): string | null {
  if (!seconds || seconds < 60) return null;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return minutes % 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes / 60}h`;
}

export function titleFacts(title: TitleSummary): string[] {
  return [title.year ? String(title.year) : null, title.official_rating ?? null, formatRuntime(title.runtime_seconds), title.genres.slice(0, 3).join(', ') || null]
    .filter((fact): fact is string => Boolean(fact));
}

/** In-progress share of a title (0 when unstarted or finished; finished shows a check instead). */
export function progressPercent(title: TitleSummary): number {
  const { played, position_seconds: position } = title.user_data;
  const duration = title.user_data.duration_seconds || title.runtime_seconds || 0;
  return !played && position > 0 && duration > 0 ? Math.min(100, Math.round((position / duration) * 100)) : 0;
}

/** "4K HDR", "1080p" or the version's own label, without the size. */
export function versionQuality(version: Pick<TitleVersion, 'hdr'> & Partial<Pick<TitleVersion, 'label' | 'height' | 'container'>>): string {
  const height = version.height ?? 0;
  const resolution = version.label || (height >= 2000 ? '4K' : height >= 1400 ? '1440p' : height >= 1000 ? '1080p' : height ? `${height}p` : version.container?.toUpperCase() || 'Version');
  return version.hdr && !/HDR/i.test(resolution) ? `${resolution} HDR` : resolution;
}

export function versionLabel(version: TitleVersion): string {
  return [versionQuality(version), version.file_size ? formatBytes(version.file_size) : null].filter(Boolean).join(' · ');
}

export type TitleAction = { label: string; itemId: string | null; reason: string | null };
const UNPLAYABLE = new Set(['offline', 'missing', 'quarantined']);
const isFirstEpisode = (episode: TitleSummary) => (episode.season_number ?? 1) <= 1 && (episode.index_number ?? 1) <= 1;

/** The title page's one primary button: what it says, which Library item it opens, and why it cannot. */
export function primaryAction(title: TitleDetail, versionId: string | null = null): TitleAction | null {
  if (title.type === 'series' || title.type === 'season') {
    const next = title.play_next;
    if (!next) return null;
    const code = episodeCode(next);
    const started = !next.user_data.played && next.user_data.position_seconds > 0;
    const label = started ? `Resume ${code}` : !title.user_data.last_watched_at && isFirstEpisode(next) ? `Start ${code}` : `Play ${code}`;
    return { label, itemId: next.play_item_id ?? null, reason: next.play_item_id ? null : 'This episode’s file is not available right now.' };
  }
  const itemId = versionId ?? title.user_data.resume_item_id ?? title.play_item_id ?? null;
  const resumable = !title.user_data.played && title.user_data.position_seconds > 0 && (!versionId || versionId === title.user_data.resume_item_id);
  const state = title.versions.find((version) => version.item_id === itemId)?.media_state;
  const reason = !itemId ? 'No playable file is available right now.' : state && UNPLAYABLE.has(state) ? 'This file’s drive is offline right now.' : null;
  return { label: resumable ? `Resume at ${formatDuration(title.user_data.position_seconds)}` : 'Play', itemId, reason };
}

/** Episodes and seasons open their series page at their season; everything else opens its own page. */
export function titleDestination(title: TitleSummary): { id: string; season: number | null } {
  if (title.type === 'episode' && title.series_id) return { id: title.series_id, season: title.season_number ?? null };
  if (title.type === 'season' && title.parent_id) return { id: title.parent_id, season: title.index_number ?? null };
  return { id: title.id, season: null };
}

/** A Continue watching entry's title card, driven by that entry's own position. */
export function continueTitle(entry: PlaybackProgress): TitleSummary | null {
  if (!entry.title) return null;
  return { ...entry.title, user_data: { ...entry.title.user_data, played: false, position_seconds: entry.position_seconds, duration_seconds: entry.duration_seconds ?? entry.title.user_data.duration_seconds } };
}

/** Library "All" is title-based: no episodes or extras, one card per movie title. */
export function titleBasedLibrary(items: LibraryItem[]): LibraryItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (item.kind === 'episode' || item.extra_type) return false;
    if (!item.title_id) return true;
    if (seen.has(item.title_id)) return false;
    seen.add(item.title_id);
    return true;
  });
}

/**
 * One fetch per `key` in the usePagedLibrary style: a response for an older key never lands.
 * A `revision` bump reloads the same key but keeps showing the current data until the new data arrives.
 */
export function useFetched<T>(key: string | null, load: () => Promise<T>, revision = 0) {
  const [state, setState] = useState<{ key: string | null; data: T | null; error: unknown }>({ key: null, data: null, error: null });
  const [pending, setPending] = useState<string | null>(null);
  const latest = useRef(load);
  latest.current = load;
  useEffect(() => {
    if (key === null) return undefined;
    let current = true;
    setPending(`${key}#${revision}`);
    latest.current().then(
      (data) => { if (current) { setState({ key, data, error: null }); setPending(null); } },
      (error: unknown) => { if (current) { setState({ key, data: null, error: error ?? new Error('Request failed') }); setPending(null); } },
    );
    return () => { current = false; };
  }, [key, revision]);
  const patch = useCallback((update: (current: T) => T) => setState((current) => (current.data === null ? current : { ...current, data: update(current.data) })), []);
  const settled = state.key === key;
  return { data: settled ? state.data : null, error: settled ? state.error : null, loading: key !== null && (!settled || pending !== null), patch };
}
