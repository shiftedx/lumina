/**
 * Recommendation builders: served-list annotations on the remote entries and the
 * title summaries, suppressions and Diagnostics.
 */
import type { MemberRecommendationSnapshot, RecoAnnotation, RecoDiagnostics, RecoSurfaceStats, RemoteEntry, Suppression, SuppressionList, SuppressionScope, TitleSummary } from '../types';
import { movieSummary } from './galleryFixtures';
import { remoteEntry } from './remoteFixtures';

export const LIST_ID = '0123456789abcdef';
export const OTHER_LIST_ID = 'fedcba9876543210';
/** What the server accepts as an event key. */
export const RECO_KEY = /^(?:[0-9a-f]{64}|[0-9a-f-]{36})$/;

/** A stable 64-hex key for remote entry `id`, as the server would give it. Clients echo keys and never parse them. */
export const remoteKey = (id: string): string => Array.from(id, (c) => c.charCodeAt(0).toString(16).padStart(2, '0')).join('').padEnd(64, '0').slice(0, 64);
/** A title id shaped like the server's uuid4 ids, so events built from it validate. */
export const titleKey = (n: number): string => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;

export const recoAnnotation = (patch: Partial<RecoAnnotation> = {}): RecoAnnotation => ({
  list_id: LIST_ID, key: remoteKey('v1'), position: 0, slot: 'exploit', reason_code: 'finished', reason: 'Because you finished Harbor walk at dawn', ...patch,
});

/** A recommended remote video: the VOD entry with its annotation at `position` (0-based). */
export function recoEntry(id = 'v1', position = 0, patch: Partial<RemoteEntry> = {}, reco: Partial<RecoAnnotation> = {}): RemoteEntry {
  return remoteEntry(id, { uploader: `Channel ${id}`, ...patch, reco: recoAnnotation({ key: remoteKey(id), position, ...reco }) });
}

/** A recommended title: the movie summary with a uuid-shaped id and its annotation at `position`. */
export function recoTitle(n = 1, position = 0, patch: Partial<TitleSummary> = {}, reco: Partial<RecoAnnotation> = {}): TitleSummary {
  const id = titleKey(n);
  return movieSummary(id, { name: `Recommended Film ${n}`, ...patch, reco: recoAnnotation({ key: id, position, reason_code: 'like_anchor', reason: 'Like Arrival', ...reco }) });
}

/** Picked for you as /api/discovery/home serves it: `n` items, exploration in the last 2 of every 12. */
export function pickedSnapshot(n = 20, patch: Partial<MemberRecommendationSnapshot> = {}): MemberRecommendationSnapshot {
  const items = Array.from({ length: n }, (_, index) => recoEntry(`v${index + 1}`, index, {}, index % 12 >= 10
    ? { slot: 'explore', reason_code: 'explore', reason: 'Something different · like Harbor walk at dawn' }
    : {}));
  return { items, categories: [], state: 'ready', refreshing: false, stale: false, ...patch };
}

export function suppression(scope: SuppressionScope, id = `${scope}-1`, patch: Partial<Suppression> = {}): Suppression {
  return {
    id, scope,
    target_key: scope === 'title' ? titleKey(1) : scope === 'item' ? 'id:youtube:v1' : 'channel one',
    title: scope === 'item' ? 'Harbor walk at dawn' : scope === 'title' ? 'Recommended Film 1' : null,
    channel_name: scope === 'title' ? null : 'Channel One',
    source: scope === 'title' ? null : 'youtube',
    created_at: '2026-09-20T10:00:00Z',
    recovers_at: scope === 'fewer' ? '2027-02-07T10:00:00Z' : null,
    ...patch,
  };
}

export const suppressionList = (patch: Partial<SuppressionList> = {}): SuppressionList => ({ items: [], channels: [], fewer: [], titles: [], ...patch });

export const recoSurfaceStats = (patch: Partial<RecoSurfaceStats> = {}): RecoSurfaceStats => ({
  surface: 'home_picked', impressions: 1240, opens: 81, ctr: 0.065, plays: 64, play_through_median: 0.71, completion_rate: 0.42,
  negative_rate: 0.012, explore_play_rate: 0.031, exploit_play_rate: 0.055, ...patch,
});

export const recoDiagnostics = (patch: Partial<RecoDiagnostics> = {}): RecoDiagnostics => ({
  enabled: true, window_days: 28, surfaces: [recoSurfaceStats()], reco_share_of_remote_plays: 0.38,
  pool: { members_with_pool: 3, median_pool_size: 352, oldest_refresh_age_minutes: 214, provider_calls_24h: 61, budget_hits_24h: 0, remote_vector_coverage: 0.93, dropped_events_24h: 0 },
  ...patch,
});
