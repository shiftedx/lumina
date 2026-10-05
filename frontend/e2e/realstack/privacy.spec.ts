import type { APIRequestContext, Page } from '@playwright/test';
import { expect, inviteMember, libraryItemId, test } from './realstack';

/** API writer that passes CSRF and Origin, so a 404 below proves authorization, not a CSRF refusal. */
async function writer(page: Page, origin: string) {
  const session = await (await page.request.get('/api/session/me')).json();
  expect(session.csrf_token).toBeTruthy();
  const headers = { Origin: origin, 'X-CSRF-Token': session.csrf_token as string };
  const request: APIRequestContext = page.request;
  return {
    get: (url: string) => request.get(url),
    post: (url: string, data?: unknown) => request.post(url, { headers, data }),
    put: (url: string, data?: unknown) => request.put(url, { headers, data }),
    patch: (url: string, data?: unknown) => request.patch(url, { headers, data }),
    delete: (url: string) => request.delete(url, { headers }),
  };
}

test('cross-user matrix: a member cannot read or change another member’s private data', async ({ page, browser, baseURL }) => {
  await page.goto('/library');
  const owner = await writer(page, baseURL!);
  const privateId = await libraryItemId(page, 'Realstack Remux');
  const sharedId = await libraryItemId(page, 'Realstack Transcode');
  const SECRET = 'owner-only lantern note';

  // Owner data: a private note on a private item and on a shared item, a private collection,
  // a queue entry and a running playback session.
  expect((await owner.put(`/api/library/${sharedId}/visibility`, { visibility: 'shared' })).ok()).toBe(true);
  const notes = await Promise.all([privateId, sharedId].map(async (id) => {
    const response = await owner.post(`/api/library/${id}/notes`, { body: SECRET, visibility: 'private' });
    expect(response.ok(), await response.text()).toBe(true);
    return (await response.json()).id as string;
  }));
  const collection = await (await owner.post('/api/collections', { name: 'Owner private shelf', visibility: 'private' })).json();
  expect((await owner.post(`/api/collections/${collection.id}/items/${privateId}`)).ok()).toBe(true);
  const queue = await (await owner.post('/api/me/watch-queue/entries', { ref: { kind: 'library', library_item_id: privateId } })).json();
  const queueEntry = queue.entries.at(-1).id as string;
  const started = await owner.post(`/api/library/${privateId}/playback-sessions`);
  expect(started.ok(), await started.text()).toBe(true);
  const playbackSession = (await started.json()).session_id as string;

  let context: Awaited<ReturnType<typeof inviteMember>>['context'] | undefined;
  try {
    const invited = await inviteMember(page, browser, 'tern', 'Quiet-harbor-2026');
    context = invited.context;
    const { member, problems } = invited;
    await member.goto('/library');
    const intruder = await writer(member, baseURL!);
    const ownQueueRevision = (await (await intruder.get('/api/me/watch-queue')).json()).revision as number;
    const leaks = (body: string) => [SECRET, 'Realstack Remux', 'Owner private shelf', privateId, collection.id, playbackSession].filter((secret) => body.includes(secret));

    const denied: [string, () => ReturnType<typeof intruder.get>][] = [
      ['item', () => intruder.get(`/api/library/${privateId}`)],
      ['media', () => intruder.get(`/api/library/${privateId}/media`)],
      ['notes', () => intruder.get(`/api/library/${privateId}/notes`)],
      ['add note', () => intruder.post(`/api/library/${privateId}/notes`, { body: 'x' })],
      ['edit note (private item)', () => intruder.put(`/api/library/notes/${notes[0]}`, { body: 'x', visibility: 'household' })],
      ['edit note (shared item)', () => intruder.put(`/api/library/notes/${notes[1]}`, { body: 'x', visibility: 'household' })],
      ['delete note', () => intruder.delete(`/api/library/notes/${notes[1]}`)],
      ['transcripts', () => intruder.get(`/api/library/${privateId}/transcripts`)],
      ['asr job', () => intruder.get(`/api/library/${privateId}/transcripts/asr`)],
      ['start asr', () => intruder.post(`/api/library/${privateId}/transcripts/asr`, {})],
      ['summary', () => intruder.get(`/api/library/${privateId}/summary`)],
      ['start summary', () => intruder.post(`/api/library/${privateId}/summaries`, {})],
      ['idea graph', () => intruder.get(`/api/library/${privateId}/idea-graph`)],
      ['start idea graph', () => intruder.post(`/api/library/${privateId}/idea-graphs`)],
      ['provenance', () => intruder.get(`/api/library/${privateId}/provenance`)],
      ['playback options', () => intruder.get(`/api/library/${privateId}/playback-options`)],
      ['progress', () => intruder.get(`/api/library/${privateId}/playback`)],
      ['visibility', () => intruder.put(`/api/library/${privateId}/visibility`, { visibility: 'shared' })],
      ['queue add', () => intruder.post('/api/me/watch-queue/entries', { ref: { kind: 'library', library_item_id: privateId } })],
      ['queue move', () => intruder.patch(`/api/me/watch-queue/entries/${queueEntry}`, { position: 0, expected_revision: ownQueueRevision })],
      ['collection', () => intruder.get(`/api/collections/${collection.id}`)],
      ['collection rename', () => intruder.put(`/api/collections/${collection.id}/name`, { name: 'x' })],
      ['collection add', () => intruder.post(`/api/collections/${collection.id}/remote-items`, { url: 'https://example.com/v' })],
      ['collection delete', () => intruder.delete(`/api/collections/${collection.id}`)],
      ['session manifest', () => intruder.get(`/api/playback-sessions/${playbackSession}/index.m3u8`)],
      ['recording', () => intruder.get('/api/live-recordings/not-yours')],
      ['recording keep', () => intruder.put('/api/live-recordings/not-yours/keep', { kept: true })],
      ['admin tasks', () => intruder.get('/api/admin/tasks')],
    ];
    for (const [name, call] of denied) {
      const response = await call();
      expect([403, 404], `${name} → ${response.status()}`).toContain(response.status());
      expect(leaks(await response.text()), name).toEqual([]);
    }

    // Queue removal and session stop are idempotent and scoped to the caller: they succeed on nothing.
    for (const url of [`/api/me/watch-queue/entries/${queueEntry}`, `/api/playback-sessions/${playbackSession}`]) {
      const removal = await intruder.delete(url);
      expect(removal.ok(), url).toBe(true);
      expect(leaks(await removal.text()), url).toEqual([]);
    }

    // Lists and the member's own export carry none of the owner's private data.
    for (const url of ['/api/library?limit=60', '/api/library?search=lantern', '/api/collections', '/api/me/watch-queue', `/api/library/${sharedId}/notes`, '/api/live-recordings', '/api/me/export']) {
      const response = await intruder.get(url);
      expect(response.ok(), `${url} → ${response.status()}`).toBe(true);
      expect(leaks(await response.text()), url).toEqual([]);
    }

    // Nothing the member tried changed the owner's data.
    for (const id of [privateId, sharedId]) {
      expect((await (await owner.get(`/api/library/${id}/notes`)).json()).map((note: { body: string }) => note.body)).toContain(SECRET);
    }
    expect((await (await owner.get(`/api/library/${privateId}`)).json()).visibility).toBe('private');
    expect((await (await owner.get('/api/me/watch-queue')).json()).entries.map((entry: { id: string }) => entry.id)).toContain(queueEntry);
    expect((await (await owner.get(`/api/collections/${collection.id}`)).json()).name).toBe('Owner private shelf');
    expect((await owner.get(`/api/playback-sessions/${playbackSession}/index.m3u8`)).ok()).toBe(true);
    expect(problems, 'member page errors or server 5xx').toEqual([]);
  } finally {
    await context?.close();
    // Leave the shared stack as the later journeys expect it.
    await owner.delete(`/api/playback-sessions/${playbackSession}`);
    await owner.delete(`/api/me/watch-queue/entries/${queueEntry}`);
    await owner.delete(`/api/collections/${collection.id}`);
    for (const note of notes) await owner.delete(`/api/library/notes/${note}`);
    await owner.put(`/api/library/${sharedId}/visibility`, { visibility: 'private' });
  }
});
