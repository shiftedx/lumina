/**
 * Title detail for the gallery: a 30 s in-memory cache fed by intent prefetch and the clicked
 * summary for an instant hero.
 */
import { getTitle } from '../../api';
import type { TitleDetail, TitleSummary } from '../../types';

export const TITLE_CACHE_TTL_MS = 30_000;
const MAX_SUMMARIES = 200;
export const MAX_DETAILS = 50;
const details = new Map<string, { at: number; promise: Promise<TitleDetail>; data: TitleDetail | null }>();
const summaries = new Map<string, TitleSummary>();

/** A fresh (< 30 s) cached detail, else a new request; a failed request is not cached. */
export function loadTitle(id: string, now = Date.now()): Promise<TitleDetail> {
  const hit = details.get(id);
  if (hit && now - hit.at < TITLE_CACHE_TTL_MS) return hit.promise;
  const entry: { at: number; promise: Promise<TitleDetail>; data: TitleDetail | null } = { at: now, promise: getTitle(id), data: null };
  entry.promise.then((data) => { entry.data = data; }, () => { if (details.get(id) === entry) details.delete(id); });
  for (const [key, value] of details) if (now - value.at >= TITLE_CACHE_TTL_MS) details.delete(key);
  details.delete(id);
  details.set(id, entry);
  if (details.size > MAX_DETAILS) details.delete(details.keys().next().value as string);
  return entry.promise;
}

export function prefetchTitle(id: string): void {
  loadTitle(id).catch(() => undefined);
}

/** The cached detail when it has arrived and is still fresh. */
export function cachedTitle(id: string, now = Date.now()): TitleDetail | null {
  const hit = details.get(id);
  return hit && now - hit.at < TITLE_CACHE_TTL_MS ? hit.data : null;
}

/** Drop a title after a change the member made (watched, favorite) so the next open refetches. */
export function forgetTitle(id: string): void {
  details.delete(id);
}

/** Sign-out or member switch: the next member must never see this member's marks (user_data). */
export function forgetTitles(): void {
  details.clear();
  summaries.clear();
}

export function rememberSummary(summary: TitleSummary): void {
  summaries.delete(summary.id);
  summaries.set(summary.id, summary);
  if (summaries.size > MAX_SUMMARIES) summaries.delete(summaries.keys().next().value as string);
}

export function summaryFor(id: string): TitleSummary | null {
  return summaries.get(id) ?? null;
}
