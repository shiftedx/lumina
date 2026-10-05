/** Mocked metadata editor API. Register after mockApi and mockTitles so these routes win. */
import type { Page, Route } from '@playwright/test';

import type { EditRequest, HistoryBatch, PersonDoc, TitleMetadataDoc } from '../src/types';

/** A 1×1 lossless WebP served for every /api/art rendition. */
const WEBP_1X1 = Buffer.from('UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==', 'base64');

export async function mockMetadataEditor(page: Page, options: { doc: TitleMetadataDoc; history?: HistoryBatch[] }) {
  let doc = structuredClone(options.doc);
  const history: HistoryBatch[] = [...(options.history ?? [])];
  const before = new Map<string, Record<string, unknown>>();
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

  await page.route((url) => url.pathname.startsWith('/api/art/'), (route) => route.fulfill({ status: 200, contentType: 'image/webp', body: WEBP_1X1 }));
  await page.route((url) => url.pathname === '/api/metadata/edits', async (route) => {
    const request = route.request().postDataJSON() as EditRequest;
    const batchId = `b${history.length + 1}`;
    const changes: HistoryBatch['changes'] = [];
    const prior: Record<string, unknown> = {};
    for (const entry of request.edits) {
      for (const [field, change] of Object.entries(entry.changes)) {
        prior[field] = doc.fields[field]?.value;
        changes.push({ field, before: prior[field], after: change.value, before_source: doc.fields[field]?.source ?? null, after_source: 'user' });
        doc.fields[field] = { value: change.value, source: 'user', locked: true, kept: null };
      }
    }
    before.set(batchId, prior);
    doc.history_count += 1;
    history.unshift({ batch_id: batchId, kind: 'edit', user: { id: 'member-1', display_name: 'Alexandria' }, created_at: new Date().toISOString(), changes, undone: false, title_count: 1 });
    return json(route, { batch_id: batchId, titles: [doc], conflicts: [] });
  });
  await page.route((url) => /^\/api\/metadata\/batches\/[^/]+\/undo$/.test(url.pathname), (route) => {
    const batchId = new URL(route.request().url()).pathname.split('/')[4]!;
    const batch = history.find((entry) => entry.batch_id === batchId);
    if (!batch || batch.undone) return json(route, { detail: 'already_undone' }, 409);
    for (const change of batch.changes) doc.fields[change.field] = { value: change.before, source: change.before_source, locked: change.before_source === 'user', kept: null };
    batch.undone = true;
    return json(route, { batch_id: `${batchId}-undo`, restored: batch.changes.length, skipped: [] });
  });
  await page.route((url) => /^\/api\/titles\/[^/]+\/metadata\/history$/.test(url.pathname), (route) => json(route, { batches: history, next_cursor: null }));
  await page.route((url) => /^\/api\/titles\/[^/]+\/metadata$/.test(url.pathname), (route) => json(route, doc));
  await page.route((url) => url.pathname === '/api/metadata/vocabulary' || url.pathname === '/api/metadata/people', (route) => json(route, []));
  // #164 household people editor: one person per id, renamed in place.
  const people = new Map<string, PersonDoc>();
  await page.route((url) => /^\/api\/metadata\/people\/[^/]+$/.test(url.pathname), (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[4]!);
    const credit = (doc.fields.people?.value as { person_id: string | null; name: string }[] | null)?.find((ref) => ref.person_id === id);
    const person = people.get(id) ?? { person_id: id, name: credit?.name ?? id, source_name: credit?.name ?? id, name_edited: false, photo_edited: false, image_url: null, title_count: 2 };
    if (route.request().method() === 'PUT') {
      const name = (route.request().postDataJSON() as { name: string | null }).name;
      Object.assign(person, { name: name ?? person.source_name, name_edited: name !== null });
    }
    people.set(id, person);
    return json(route, route.request().method() === 'PUT' ? { batch_id: 'person-1', person } : person);
  });
}
