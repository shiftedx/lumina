import type { Page } from '@playwright/test';

const title = (id: string, name: string, category: string, type = 'movie') => ({ kind: 'title', id, title: name, subtitle: `${type === 'movie' ? 'Film' : 'Series'} · 2024`, score: 1, lexical_score: 1, semantic_score: 0, match_mode: 'lexical', media_title: { id, name, type, category, year: 2024, images: {}, user_data: { played: false, is_favorite: false, position_seconds: 0 } } });

/** Answers /api/search and /api/youtube-search with every palette group filled (synthetic names only). */
export async function mockPaletteSearch(page: Page, { delayMs = 60, youtubeFails = false }: { delayMs?: number; youtubeFails?: boolean } = {}) {
  await page.route('**/api/search?*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    const q = new URL(route.request().url()).searchParams.get('q') ?? '';
    const matches = q.startsWith('zz') ? [] : [
      title('movie-1', 'Alpine Crossing', 'movies'), title('series-1', 'Alpine Rescue', 'shows', 'series'), title('anime-1', 'Alpine Spirits', 'anime', 'series'),
      { ...title('album-1', 'Alpine Songs', '', 'album'), media_title: { id: 'album-1', name: 'Alpine Songs', type: 'album', category: null, images: {}, user_data: { played: false, is_favorite: false, position_seconds: 0 } } },
      { kind: 'moment', id: 'moment-1', title: 'Summit at dawn', subtitle: 'Alpine Crossing · 12:04', item: { id: 'library-1', title: 'Alpine Crossing' }, start_ms: 724000, score: 1 },
      { kind: 'library', id: 'library-2', title: 'Alpine vlog', subtitle: 'YouTube', item: { id: 'library-2', title: 'Alpine vlog' }, score: 1 },
    ];
    await route.fulfill({ json: { query: q, mode: 'hybrid', matches, items: [], index_generation: 1 } });
  });
  await page.route('**/api/youtube-search', async (route) => {
    if (youtubeFails) return route.fulfill({ status: 503, json: { detail: 'quota' } });
    await route.fulfill({ json: { items: [{ id: 'yt-1', title: 'Alpine drive in winter', webpage_url: 'https://www.youtube.com/watch?v=yt-1', uploader: 'Synthetic Roads' }] } });
  });
}
