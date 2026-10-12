import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  continueTitle, episodeCode, formatRuntime, orderSeasons, primaryAction, titleBasedLibrary,
  titleDestination, useFetched, versionLabel,
} from './features/titles/titleModel';
import type { LibraryItem, PlaybackProgress, TitleDetail, TitleSummary, TitleUserData, TitleVersion } from './types';

const user = (patch: Partial<TitleUserData> = {}): TitleUserData => ({ played: false, is_favorite: false, position_seconds: 0, ...patch });
const summary = (patch: Partial<TitleSummary>): TitleSummary => ({ id: 't', type: 'episode', name: 'Name', genres: [], added_at: '2026-09-01T00:00:00Z', user_data: user(), ...patch });
const version = (patch: Partial<TitleVersion>): TitleVersion => ({ item_id: 'v', hdr: false, ...patch });
const detail = (patch: Partial<TitleDetail>): TitleDetail => ({
  ...summary({ type: 'movie' }), studios: [], provider_ids: {}, people: [], versions: [], extras: [], children: [], has_recap: false, ...patch,
});

describe('title labels', () => {
  it('codes episodes, multi-episode files and specials', () => {
    expect(episodeCode({ season_number: 1, index_number: 3 })).toBe('S1 · E3');
    expect(episodeCode({ season_number: 1, index_number: 1, index_number_end: 2 })).toBe('S1 · E1–2');
    expect(episodeCode({ season_number: 0, index_number: 2 })).toBe('Special 2');
    expect(episodeCode({ index_number: 4 })).toBe('E4');
    expect(episodeCode({})).toBe('');
  });

  it('orders seasons with Specials last and formats runtime and versions', () => {
    expect(orderSeasons([{ index_number: 0 }, { index_number: 2 }, { index_number: 1 }]).map((season) => season.index_number)).toEqual([1, 2, 0]);
    expect(formatRuntime(6720)).toBe('1h 52m');
    expect(formatRuntime(2880)).toBe('48m');
    expect(formatRuntime(3600)).toBe('1h');
    expect(formatRuntime(20)).toBeNull();
    expect(versionLabel(version({ height: 2160, hdr: true, file_size: 58 * 1024 ** 3 }))).toBe('4K HDR · 58 GB');
    expect(versionLabel(version({ label: '4K HDR', height: 2160, hdr: true }))).toBe('4K HDR');
    expect(versionLabel(version({ height: 1080, file_size: 8 * 1024 ** 3 }))).toBe('1080p · 8.0 GB');
  });
});

describe('primary action', () => {
  it('resumes or plays a movie, honours the chosen version, and refuses an offline file with a reason', () => {
    const movie = detail({ play_item_id: 'v1', user_data: user({ position_seconds: 2530, resume_item_id: 'v1' }), versions: [version({ item_id: 'v1' }), version({ item_id: 'v2', media_state: 'offline' })] });
    expect(primaryAction(movie)).toEqual({ label: 'Resume at 42:10', itemId: 'v1', reason: null });
    expect(primaryAction(movie, 'v2')).toEqual({ label: 'Play', itemId: 'v2', reason: 'This file’s drive is offline right now.' });
    expect(primaryAction(detail({ play_item_id: null }))).toEqual({ label: 'Play', itemId: null, reason: 'No playable file is available right now.' });
  });

  it('starts, resumes or plays the next episode of a series', () => {
    const e1 = summary({ season_number: 1, index_number: 1, play_item_id: 'i1' });
    const series = (next: TitleSummary, watchedBefore: boolean) => detail({ type: 'series', play_next: next, user_data: user({ last_watched_at: watchedBefore ? '2026-09-01T00:00:00Z' : null }) });
    expect(primaryAction(series(e1, false))?.label).toBe('Start S1 · E1');
    expect(primaryAction(series({ ...e1, user_data: user({ position_seconds: 40 }) }, true))?.label).toBe('Resume S1 · E1');
    expect(primaryAction(series(summary({ season_number: 2, index_number: 4, play_item_id: 'i9' }), true))).toEqual({ label: 'Play S2 · E4', itemId: 'i9', reason: null });
    expect(primaryAction(series({ ...e1, play_item_id: null }, false))?.reason).toBe('This episode’s file is not available right now.');
    expect(primaryAction(detail({ type: 'series', play_next: null }))).toBeNull();
  });
});

describe('navigation helpers', () => {
  it('sends episodes and seasons to their series page at the right season', () => {
    expect(titleDestination(summary({ id: 'e', type: 'episode', series_id: 's', season_number: 2 }))).toEqual({ id: 's', season: 2 });
    expect(titleDestination(summary({ id: 'x', type: 'season', parent_id: 's', index_number: 0 }))).toEqual({ id: 's', season: 0 });
    expect(titleDestination(summary({ id: 'm', type: 'movie' }))).toEqual({ id: 'm', season: null });
  });

  it('makes Continue watching progress drive the card, and keeps the Library All view title-based', () => {
    const entry = { id: 'pb', item_id: 'i', position_seconds: 30, duration_seconds: 60, completed: false, title: summary({ user_data: user({ played: true }) }) } as PlaybackProgress;
    expect(continueTitle(entry)?.user_data).toMatchObject({ played: false, position_seconds: 30, duration_seconds: 60 });
    expect(continueTitle({ ...entry, title: null })).toBeNull();
    const item = (id: string, patch: Partial<LibraryItem> = {}) => ({ id, title: id, ...patch }) as LibraryItem;
    const kept = titleBasedLibrary([
      item('4k', { title_id: 'm1', kind: 'movie' }), item('1080', { title_id: 'm1', kind: 'movie' }), item('ep', { title_id: 'e1', kind: 'episode' }),
      item('trailer', { title_id: 'm1', extra_type: 'trailer' }), item('video'),
    ]);
    expect(kept.map((entry) => entry.id)).toEqual(['4k', 'video']);
  });
});

describe('useFetched', () => {
  it('drops a stale response when the key changes and keeps data while a revision reloads', async () => {
    let resolveA: (value: string) => void = () => undefined;
    const slowA = new Promise<string>((resolve) => { resolveA = resolve; });
    const { result, rerender } = renderHook(({ id, revision }) => useFetched(id, () => (id === 'a' ? slowA : Promise.resolve(`B${revision}`)), revision), { initialProps: { id: 'a', revision: 0 } });
    expect(result.current.loading).toBe(true);
    rerender({ id: 'b', revision: 0 });
    await waitFor(() => expect(result.current.data).toBe('B0'));
    resolveA('A');
    await slowA;
    expect(result.current.data).toBe('B0');
    rerender({ id: 'b', revision: 1 });
    expect(result.current.data).toBe('B0');
    await waitFor(() => expect(result.current.data).toBe('B1'));
    result.current.patch((value) => `${value}!`);
    await waitFor(() => expect(result.current.data).toBe('B1!'));
  });
});
