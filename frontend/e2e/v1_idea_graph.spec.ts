import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, mockTranscripts, signIn } from './lumina-mock';

/** Idea graph: evidence-backed ideas and connections, list first, optional radial map. */

const summary = {
  id: 'sum-1', library_item_id: item.id, transcript_id: 'tr-en', transcript_revision: 2, model_id: 'qwen3.8-27b-ninfer-nvfp4-kv', state: 'succeeded', overview: 'An alpine valley.',
  key_points: [{ text: 'Terraces', cue_ordinals: [1], start_ms: 1500 }], chapters: [], dropped_points: 0, error: null, created_at: '2026-01-03T00:00:00Z', completed_at: '2026-01-03T00:00:12Z',
};
const labels = ['Terrace farming', 'Eleven generations', 'Stone bridge', 'Spring floods', 'Summer rebuilding', 'Conservation as habit', 'High pastures', 'Snowmelt', 'The lake', 'Shepherds and herds with a deliberately long concept label'];
const graph = {
  id: 'g-1', library_item_id: item.id, summary_id: 'sum-1', transcript_id: 'tr-en', transcript_revision: 2, model_id: summary.model_id, state: 'succeeded', dropped: 2, error: null,
  created_at: '2026-01-03T00:01:00Z', completed_at: '2026-01-03T00:01:09Z',
  nodes: labels.map((label, index) => ({ id: `n${index}`, label, cue_ordinals: [index], start_ms: index * 1500 })),
  edges: [[0, 1, 'has lasted'], [2, 3, 'is washed out by'], [4, 2, 'restores'], [5, 4, 'motivates'], [7, 8, 'feeds'], [8, 6, 'waters'], [9, 6, 'graze'], [5, 0, 'sustains']]
    .map(([source, target, label]) => ({ source: `n${source}`, target: `n${target}`, label, cue_ordinals: [source], start_ms: Number(source) * 1500 })),
};

async function openIdeas(page: Page, viewport: { width: number; height: number }, scheme: 'light' | 'dark', withGraph: boolean) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await mockTranscripts(page);
  await page.route(/\/api\/library\/[^/]+\/summary$/, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(summary) }));
  await page.route(/\/api\/library\/[^/]+\/idea-graph$/, (route) => route.fulfill(withGraph
    ? { contentType: 'application/json', body: JSON.stringify(graph) }
    : { contentType: 'application/json', status: 404, body: '{"detail":"No idea graph yet"}' }));
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Idea graph' }).click();
}

test('test_idea_graph_list_and_map', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openIdeas(page, { width: 1536, height: 960 }, 'dark', true);
  await expect(page.getByText('Stone bridge — is washed out by → Spring floods')).toBeVisible();
  await page.getByRole('button', { name: 'Map', exact: true }).click();
  await page.locator('.idea-map').getByRole('button', { name: 'Stone bridge' }).click();
  expect(errors).toEqual([]);
});

test('test_idea_graph_empty', async ({ page }) => {
  await openIdeas(page, { width: 1536, height: 960 }, 'dark', false);
  await expect(page.getByRole('button', { name: 'Map ideas' })).toBeVisible();
});
