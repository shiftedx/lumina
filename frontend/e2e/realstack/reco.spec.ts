import type { APIRequestContext, Page } from '@playwright/test';
import { expect, test } from './realstack';

/** Spec Part 10, Realstack: play a recommended title to completion, then Diagnostics shows the attributed play and the export shows the completion. */

async function writer(page: Page, origin: string) {
  const session = await (await page.request.get('/api/session/me')).json();
  const headers = { Origin: origin, 'X-CSRF-Token': session.csrf_token as string };
  const request: APIRequestContext = page.request;
  return {
    get: (url: string) => request.get(url),
    post: (url: string, data?: unknown) => request.post(url, { headers, data }),
    put: (url: string, data?: unknown) => request.put(url, { headers, data }),
  };
}

type TitleRow = { kind: string; items: { id: string; name: string; play_item_id?: string | null; reco?: { list_id: string; key: string } | null }[] };
const attributedPlays = async (api: Awaited<ReturnType<typeof writer>>) => {
  const body = (await (await api.get('/api/admin/diagnostics')).json()).recommendations as { surfaces: { plays: number }[] };
  return body.surfaces.reduce((sum, surface) => sum + surface.plays, 0);
};

test('a played recommendation is attributed and counted in Diagnostics', async ({ page, baseURL }) => {
  await page.goto('/');
  const api = await writer(page, baseURL!);
  const playsBefore = await attributedPlays(api);

  // Use a dedicated subject-matched pair; earlier playback journeys may have started the general movie fixtures.
  const movies = ((await (await api.get('/api/titles?type=movie&limit=60')).json()) as { items: { id: string; name: string; play_item_id?: string | null }[] }).items;
  const anchor = movies.find((item) => item.name === 'Realstack Reco Anchor' && item.play_item_id);
  const match = movies.find((item) => item.name === 'Realstack Reco Match' && item.play_item_id);
  expect(anchor, 'a playable recommendation anchor fixture').toBeTruthy();
  expect(match, 'a playable related movie fixture').toBeTruthy();
  const seeded = await api.put(`/api/library/${anchor!.play_item_id}/playback`, { position_seconds: 4, duration_seconds: 4, completed: true });
  expect(seeded.ok(), await seeded.text()).toBe(true);

  const rows = ((await (await api.get('/api/home/title-rows')).json()) as { rows: TitleRow[] }).rows;
  const picked = rows.flatMap((row) => row.items).find((item) => item.id === match!.id && item.reco && item.play_item_id);
  expect(picked, 'the subject-matched title is recommended with a playable version').toBeTruthy();
  const { list_id: listId, key } = picked!.reco!;

  await test.step('the client reports what it showed and opened', async () => {
    const sent = await api.post('/api/reco/events', { events: [{ kind: 'impression', list_id: listId, key, age_ms: 1500 }, { kind: 'open', list_id: listId, key, age_ms: 500 }] });
    expect(sent.status()).toBe(204);
  });

  await test.step('playing it to the end writes an attributed play and completion', async () => {
    const started = await api.put(`/api/library/${picked!.play_item_id}/playback`, { position_seconds: 1, duration_seconds: 4, completed: false });
    expect(started.ok(), await started.text()).toBe(true);
    const finished = await api.put(`/api/library/${picked!.play_item_id}/playback`, { position_seconds: 4, duration_seconds: 4, completed: true });
    expect(finished.ok(), await finished.text()).toBe(true);
  });

  await test.step('Diagnostics counts one more attributed play', async () => {
    expect(await attributedPlays(api)).toBe(playsBefore + 1);
    await page.goto('/admin/diagnostics');
    await expect(page.getByRole('region', { name: 'Recommendations', exact: true }).getByRole('heading', { level: 3, name: 'Recommendations' })).toBeVisible();
  });

  await test.step('the export shows the play and the completion with the list they came from', async () => {
    const exported = (await (await api.get('/api/me/export')).json()) as { recommendation_events: { kind: string; item_key: string; surface: string | null }[] };
    const mine = exported.recommendation_events.filter((event) => event.item_key === key);
    expect(mine.filter((event) => event.kind === 'play' && event.surface)).toHaveLength(1);
    expect(mine.filter((event) => event.kind === 'complete' && event.surface)).toHaveLength(1);
  });
});
