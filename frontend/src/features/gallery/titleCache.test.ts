import { afterEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { movieSummary, titleDetail } from '../../test/galleryFixtures';
import { cachedTitle, forgetTitle, forgetTitles, loadTitle, MAX_DETAILS, rememberSummary, summaryFor, TITLE_CACHE_TTL_MS } from './titleCache';

afterEach(() => { vi.restoreAllMocks(); forgetTitles(); });

describe('title cache', () => {
  it('serves a fresh detail from memory and refetches after 30 s', async () => {
    const detail = titleDetail(movieSummary());
    const spy = vi.spyOn(api, 'getTitle').mockResolvedValue(detail);
    await loadTitle('movie-1', 1_000);
    await loadTitle('movie-1', 1_000 + TITLE_CACHE_TTL_MS - 1);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(cachedTitle('movie-1', 1_000 + TITLE_CACHE_TTL_MS - 1)).toBe(detail);
    expect(cachedTitle('movie-1', 1_000 + TITLE_CACHE_TTL_MS)).toBeNull();
    await loadTitle('movie-1', 1_000 + TITLE_CACHE_TTL_MS);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('does not cache a failure', async () => {
    const spy = vi.spyOn(api, 'getTitle').mockRejectedValueOnce(new Error('boom')).mockResolvedValue(titleDetail(movieSummary()));
    await expect(loadTitle('movie-1', 5)).rejects.toThrow('boom');
    await loadTitle('movie-1', 6);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('remembers the clicked summary', () => {
    rememberSummary(movieSummary('movie-9'));
    expect(summaryFor('movie-9')?.name).toBe('Northern Lantern');
    expect(summaryFor('nope')).toBeNull();
  });

  it('forgets every detail and summary on a member change, so the next member sees none of their marks', async () => {
    const spy = vi.spyOn(api, 'getTitle').mockResolvedValue(titleDetail(movieSummary()));
    rememberSummary(movieSummary('movie-9'));
    await loadTitle('movie-1', 1_000);
    forgetTitles();
    expect(cachedTitle('movie-1', 1_001)).toBeNull();
    expect(summaryFor('movie-9')).toBeNull();
    await loadTitle('movie-1', 1_002);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('drops expired details on insert and keeps at most MAX_DETAILS', async () => {
    const spy = vi.spyOn(api, 'getTitle').mockImplementation(async (id) => titleDetail(movieSummary(id)));
    await loadTitle('old', 0);
    await loadTitle('new', TITLE_CACHE_TTL_MS);
    expect(cachedTitle('old', 0)).toBeNull(); // swept, not merely stale
    for (let index = 0; index < MAX_DETAILS; index += 1) await loadTitle(`t${index}`, TITLE_CACHE_TTL_MS);
    expect(cachedTitle('new', TITLE_CACHE_TTL_MS)).toBeNull();
    expect(cachedTitle('t0', TITLE_CACHE_TTL_MS)).not.toBeNull();
    expect(spy).toHaveBeenCalledTimes(MAX_DETAILS + 2);
  });
});
