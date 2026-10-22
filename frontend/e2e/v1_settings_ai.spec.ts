import { expect, test, type Page } from '@playwright/test';
import { aiFeatureReadiness, localModel, localModels, mockAdminFixtures, mockApi, signIn } from './lumina-mock';

/** AI & models and the enable-feature dialog; success criteria 1, 2 and 5, UI side. */

type Model = ReturnType<typeof localModel>;
const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });

/** GET /api/admin/ai/config as the server computes readiness from the active models. */
function aiConfig(models: Model[], disabled: string[]) {
  const readiness = (role: 'search' | 'speech') => {
    const model = models.find((entry) => entry.role === role && entry.active)!;
    const reason = model.state === 'ready' ? null : model.state === 'failed' ? model.reason : model.state === 'absent' ? `The ${role} model is not installed` : `Waiting for the ${role} model`;
    return { requires: `${role}_model`, ready: model.state === 'ready', reason };
  };
  return {
    base_url: 'http://127.0.0.1:8080/v1', model: 'local-model', has_api_key: false, max_concurrency: 3, context_tokens: 145000, embedding_model: '',
    asr_base_url: '', asr_model: '', enabled: true, asr_available: false, ai_features_disabled: disabled, model_threads: null, model_threads_auto: 3,
    features: { ...aiFeatureReadiness, semantic_search: readiness('search'), subtitles_from_speech: readiness('speech') },
  };
}

/** A stateful fake: a started download is half done on the next polls and ready on the third. */
async function mockModelApi(page: Page, start: Model[], startDisabled: string[]) {
  const fake = { downloads: [] as string[], saved: [] as unknown[] };
  let models = start;
  let disabled = startDisabled;
  let polls = 0;
  await page.route('**/api/admin/models', (route) => {
    polls += 1;
    models = models.map((model) => (model.state !== 'downloading' ? model
      : polls >= 3 ? { ...model, state: 'ready', bytes_done: null, bytes_total: null }
        : { ...model, bytes_done: Math.round(model.size_bytes / 2) }));
    return route.fulfill(json({ models }));
  });
  await page.route('**/api/admin/models/*/download', (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[4]);
    fake.downloads.push(id);
    polls = 0;
    models = models.map((model) => (model.id === id ? { ...model, state: 'downloading', bytes_done: 0, bytes_total: model.size_bytes, reason: null } : model));
    return route.fulfill(json(models.find((model) => model.id === id)));
  });
  await page.route('**/api/admin/ai/config', (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as { ai_features_disabled?: string[] };
      fake.saved.push(body);
      if (body.ai_features_disabled) disabled = body.ai_features_disabled;
    }
    return route.fulfill(json(aiConfig(models, disabled)));
  });
  return fake;
}

async function openAi(page: Page, viewport = { width: 1536, height: 960 }) {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  await mockAdminFixtures(page);
}

// The 10-foot step is for touch screens and TV remotes; a mouse gets the 36px desktop control (tokens.css).
test.describe('on a touch screen or TV', () => {
  test.use({ hasTouch: true });
  test('the dialog works from a TV remote: OK opens it, Back cancels, focus returns, 48 px buttons', async ({ page }) => {
    await openAi(page, { width: 1600, height: 900 });
    await mockModelApi(page, localModels.map((model) => ({ ...model })), ['subtitles_from_speech']);
    await page.goto('/');
    await signIn(page);
    await page.goto('/settings/ai');
    const toggle = page.getByRole('switch', { name: 'Subtitles from speech' });
    await toggle.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Turn on Subtitles from speech?' });
    const download = dialog.getByRole('button', { name: 'Download and turn on' });
    await expect(download).toBeFocused();
    expect((await download.boundingBox())!.height).toBeGreaterThanOrEqual(48);
    expect((await dialog.getByRole('button', { name: 'Cancel' }).boundingBox())!.height).toBeGreaterThanOrEqual(48);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(toggle).toBeFocused();
    await expect(toggle).not.toBeChecked();
  });
});
