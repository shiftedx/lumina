import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import type { AiConfig, AiRequirement, LocalModel } from './api';

const api = vi.hoisted(() => ({
  getAiConfig: vi.fn(), updateAiConfig: vi.fn(), testAiConnection: vi.fn(), listAdminTasks: vi.fn(),
  listLocalModels: vi.fn(), downloadLocalModel: vi.fn(), cancelLocalModelDownload: vi.fn(), activateLocalModel: vi.fn(), removeLocalModel: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { sectionById } = await import('./features/settings/registry');
const { SectionRows } = await import('./features/settings/SettingRow');
const { SettingsHostContext } = await import('./features/settings/settingsHost');
const { SettingsSurface } = await import('./features/settings/SettingsSurface');
const { changes, draftOf } = await import('./features/settings/sections/aiForm');
const { confirmLeaveSettings } = await import('./features/settings/unsavedChanges');

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
afterEach(() => { vi.resetAllMocks(); vi.restoreAllMocks(); });

const ready = (requires: AiRequirement | null) => ({ requires, ready: true, reason: null });
const config: AiConfig = {
  base_url: 'http://10.0.0.5:8080/v1', model: 'qwen', has_api_key: true, max_concurrency: 3, context_tokens: 145000, embedding_model: '',
  asr_base_url: '', asr_model: '', enabled: true, asr_available: false, ai_features_disabled: [],
  features: {
    semantic_search: { requires: 'search_model', ready: false, reason: 'The search model is not installed' },
    subtitles_from_speech: { requires: 'speech_model', ready: false, reason: 'The speech model is not installed' },
    sync: ready(null), translate: ready('assistant'), recap: ready('assistant'), episode_summaries: ready('assistant'), key_scenes: ready('assistant'), smart_collection_builder: ready('assistant'), match_tie_breaker: ready('assistant'), mute_strong_language: ready(null),
  },
  model_threads: null, model_threads_auto: 3,
};
const model = (patch: Partial<LocalModel>): LocalModel => ({
  id: 'granite-embedding-97m-multilingual-r2-q8', role: 'search', name: 'Granite Embedding 97M multilingual', description: 'Fast search model that understands many languages.',
  licence: 'Apache-2.0', size_bytes: 115_061_088, ram_bytes: 912_680_550, default: true, active: true, state: 'absent', bytes_done: null, bytes_total: null, reason: null,
  running: false, features: ['semantic_search'], ...patch,
});
const granite = model({});
const nomic = model({ id: 'nomic-embed-text-v1.5-q8', name: 'Nomic Embed Text v1.5', description: 'English-only search model.', size_bytes: 146_146_432, ram_bytes: 590_558_003, default: false, active: false });
const whisper = model({ id: 'faster-whisper-small-int8', role: 'speech', name: 'Whisper small', description: 'Speech-to-text in 99 languages with word timings.', licence: 'MIT', size_bytes: 486_212_372, ram_bytes: 858_993_459, features: ['subtitles_from_speech', 'sync'] });
const admin = { id: 'u1', username: 'dana', role: 'admin', is_active: true } as never;

function setup({ config: patch = {}, models = [granite, nomic, whisper] }: { config?: Partial<AiConfig>; models?: LocalModel[] } = {}) {
  api.getAiConfig.mockResolvedValue({ ...config, ...patch });
  api.listLocalModels.mockResolvedValue({ models });
  api.listAdminTasks.mockResolvedValue({ items: [], next_cursor: null, counts: { download: {}, asr: { active: 1 }, summary: { succeeded: 4 } } });
}
function renderAi(host: Record<string, unknown> = {}) {
  return render(<SettingsHostContext.Provider value={{ user: admin, onMessage: vi.fn(), ...host }}><SectionRows section={sectionById('ai')!} /></SettingsHostContext.Provider>);
}
const rowOf = (id: string) => document.querySelector(`[data-setting-id="${id}"]`) as HTMLElement;
const saveButton = () => screen.getByRole('button', { name: 'Save AI settings' }) as HTMLButtonElement;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe('AI & models form', () => {
  it('sends only changed fields: the key is write-only, switches become a sorted off-list, threads 0 means automatic', () => {
    const draft = draftOf(config);
    expect(changes(config, draft)).toEqual({});
    expect(changes(config, { ...draft, api_key: 'new-key', max_concurrency: '2' })).toEqual({ api_key: 'new-key', max_concurrency: 2 });
    expect(changes(config, { ...draft, api_key: 'ignored', clear_key: true })).toEqual({ api_key: '' });
    expect(changes(config, { ...draft, embedding_model: ' nomic-embed ' })).toEqual({ embedding_model: 'nomic-embed' });
    expect(changes(config, { ...draft, disabled: ['sync', 'recap'] })).toEqual({ ai_features_disabled: ['recap', 'sync'] });
    expect(changes({ ...config, ai_features_disabled: ['sync', 'recap'] }, { ...draft, disabled: ['recap', 'sync'] })).toEqual({});
    expect(changes(config, { ...draft, model_threads: '2' })).toEqual({ model_threads: 2 });
    expect(changes({ ...config, model_threads: 2 }, { ...draftOf({ ...config, model_threads: 2 }), model_threads: '0' })).toEqual({ model_threads: 0 });
  });

  it('renders one row per setting, each with an ⓘ, and says what each feature needs', async () => {
    expect(sectionById('ai')!.entries.map((entry) => entry.id)).toEqual([
      'ai.status', 'ai.semantic-search', 'ai.subtitles-from-speech', 'ai.sync', 'ai.translate', 'ai.recap', 'ai.episode-summaries', 'ai.key-scenes', 'ai.smart-collection-builder', 'ai.match-tie-breaker', 'ai.mute-strong-language', 'ai.personal-recommendations',
      'ai.search-model', 'ai.speech-model', 'ai.model-threads',
      'ai.assistant-address', 'ai.assistant-model', 'ai.api-key', 'ai.max-concurrency', 'ai.context-window', 'ai.embedding-model', 'ai.assistant-test', 'ai.speech-address', 'ai.speech-server-model',
    ]);
    setup();
    renderAi();
    expect(await screen.findByRole('switch', { name: 'Semantic search' })).toBeTruthy();
    for (const entry of sectionById('ai')!.entries) expect(screen.getByRole('button', { name: `About ${entry.label}` })).toBeTruthy();
    expect(within(rowOf('ai.semantic-search')).getByText('Needs the search model')).toBeTruthy();
    expect(within(rowOf('ai.translate')).getByText('Needs your assistant server')).toBeTruthy();
    expect(within(rowOf('ai.sync')).getByText('Needs nothing extra')).toBeTruthy();
    expect(within(rowOf('ai.status')).getByText('Transcripts come only from source captions')).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
  });

  it('switches and fields are sent together by Save AI settings, never on click', async () => {
    setup();
    api.updateAiConfig.mockImplementation(async (update) => ({ ...config, ...update }));
    renderAi();
    const sync = await screen.findByRole('switch', { name: 'Sync subtitles to audio' });
    fireEvent.click(sync);
    expect((sync as HTMLInputElement).checked).toBe(false);
    expect(api.updateAiConfig).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('textbox', { name: /^Model/ }), { target: { value: 'qwen3' } });
    expect(saveButton().disabled).toBe(false);
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateAiConfig).toHaveBeenCalledWith({ model: 'qwen3', ai_features_disabled: ['sync'] }));
    expect(await screen.findByText(/^Saved\./)).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
  });

  it('lists Personalised recommendations as a switch that needs nothing extra, and saves it in the off-list', async () => {
    setup();
    api.updateAiConfig.mockImplementation(async (update) => ({ ...config, ...update }));
    renderAi();
    const toggle = await screen.findByRole('switch', { name: 'Personalised recommendations' });
    expect((toggle as HTMLInputElement).checked).toBe(true);
    expect(within(rowOf('ai.personal-recommendations')).getByText('Needs nothing extra')).toBeTruthy();
    fireEvent.click(toggle);
    expect(api.updateAiConfig).not.toHaveBeenCalled();
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateAiConfig).toHaveBeenCalledWith({ ai_features_disabled: ['personal_recommendations'] }));
  });

  it('keeps the assistant key write-only and tests the saved connection', async () => {
    setup();
    api.updateAiConfig.mockResolvedValue({ ...config, has_api_key: true });
    api.testAiConnection.mockResolvedValue({ ok: false, models: ['llama', 'mistral'], model_available: false, error: 'Configured model is not served by this endpoint' });
    renderAi();
    const key = await screen.findByLabelText(/^API key/) as HTMLInputElement;
    expect(key.value).toBe('');
    expect(key.placeholder).toMatch(/Saved key hidden/);
    fireEvent.change(key, { target: { value: 'rotated' } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(api.updateAiConfig).toHaveBeenCalledWith({ api_key: 'rotated' }));
    await waitFor(() => expect((screen.getByLabelText(/^API key/) as HTMLInputElement).value).toBe(''));
    fireEvent.click(screen.getByRole('button', { name: /Test connection/ }));
    expect(await screen.findByText('Connected, but the model is missing.')).toBeTruthy();
    expect(screen.getByText('mistral')).toBeTruthy();
  });

  it('asks before leaving unsaved AI settings', async () => {
    setup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderAi();
    fireEvent.change(await screen.findByRole('textbox', { name: /^Model/ }), { target: { value: 'other-model' } });
    expect(confirmLeaveSettings()).toBe(false);
    expect(confirm).toHaveBeenCalledWith('You have unsaved changes in AI & models. Leave without saving?');
  });

  it('polls a running download and refreshes readiness without losing unsaved edits', async () => {
    setup();
    const downloading = { ...granite, state: 'downloading' as const, bytes_done: 1, bytes_total: granite.size_bytes };
    api.listLocalModels.mockResolvedValueOnce({ models: [downloading, nomic, whisper] }).mockResolvedValue({ models: [{ ...granite, state: 'ready' }, nomic, whisper] });
    renderAi();
    const address = await screen.findByRole('textbox', { name: /^Server address/ });
    fireEvent.change(address, { target: { value: 'http://10.0.0.9:8080/v1' } });
    await waitFor(() => expect(api.listLocalModels).toHaveBeenCalledTimes(2), { timeout: 4000 });
    await waitFor(() => expect(api.getAiConfig).toHaveBeenCalledTimes(2));
    expect((screen.getByRole('textbox', { name: /^Server address/ }) as HTMLInputElement).value).toBe('http://10.0.0.9:8080/v1');
    expect(saveButton().disabled).toBe(false);
  });
});

describe('on-device model rows', () => {
  it('lists each role\'s models with size, memory, licence and state, and downloads one at once, not on Save', async () => {
    setup();
    api.downloadLocalModel.mockResolvedValue({ ...granite, state: 'downloading', bytes_done: 46_000_000, bytes_total: granite.size_bytes });
    renderAi();
    const list = await screen.findByRole('list', { name: 'Search models' });
    expect(within(list).getByText('115 MB download, about 913 MB of memory while running, Apache-2.0 licence.')).toBeTruthy();
    expect(within(list).getByText('In use')).toBeTruthy();
    expect(within(list).getAllByText('Not installed')).toHaveLength(2);
    expect(within(list).getByRole('button', { name: 'About Granite Embedding 97M multilingual' })).toBeTruthy();
    await userEvent.click(within(list).getByRole('button', { name: 'Download Granite Embedding 97M multilingual' }));
    expect(api.downloadLocalModel).toHaveBeenCalledWith(granite.id);
    expect(await within(list).findByRole('progressbar', { name: 'Granite Embedding 97M multilingual download' })).toBeTruthy();
    expect(within(list).getByText('46 MB of 115 MB')).toBeTruthy();
    expect(api.updateAiConfig).not.toHaveBeenCalled();
    expect(saveButton().disabled).toBe(true);
  });

  it('cancels a download, and choosing the active model reloads the list and readiness', async () => {
    const downloading = { ...granite, state: 'downloading' as const, bytes_done: 1, bytes_total: granite.size_bytes };
    setup({ models: [downloading, nomic, whisper] });
    api.cancelLocalModelDownload.mockResolvedValue(granite);
    api.activateLocalModel.mockResolvedValue({ ...nomic, active: true });
    renderAi();
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel Granite Embedding 97M multilingual download' }));
    expect(api.cancelLocalModelDownload).toHaveBeenCalledWith(granite.id);
    const configLoads = api.getAiConfig.mock.calls.length;
    const modelLoads = api.listLocalModels.mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: 'Use Nomic Embed Text v1.5' }));
    expect(api.activateLocalModel).toHaveBeenCalledWith(nomic.id);
    await waitFor(() => expect(api.listLocalModels.mock.calls.length).toBeGreaterThan(modelLoads));
    await waitFor(() => expect(api.getAiConfig.mock.calls.length).toBeGreaterThan(configLoads));
  });

  it('deleting a model asks first, names it, and a delete that turns features off leaves no phantom unsaved change', async () => {
    const installed = { ...granite, state: 'ready' as const };
    setup({ models: [installed, nomic, whisper] });
    api.getAiConfig.mockResolvedValueOnce({ ...config, features: { ...config.features, semantic_search: ready('search_model') } })
      .mockResolvedValue({ ...config, ai_features_disabled: ['semantic_search'] });
    api.removeLocalModel.mockResolvedValue({ model: granite, disabled_features: ['semantic_search'] });
    const onMessage = vi.fn();
    renderAi({ onMessage });
    const open = await screen.findByRole('button', { name: 'Delete Granite Embedding 97M multilingual' });
    await userEvent.click(open);
    const dialog = screen.getByRole('dialog', { name: 'Delete \u201cGranite Embedding 97M multilingual\u201d?' });
    expect(within(dialog).getByText('This frees 115 MB. Features that need it (Semantic search) turn off unless an external server covers them.')).toBeTruthy();
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(api.removeLocalModel).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(open);
    await userEvent.click(open);
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(onMessage).toHaveBeenCalledWith('Deleted Granite Embedding 97M multilingual. Turned off: Semantic search.'));
    await waitFor(() => expect((screen.getByRole('switch', { name: 'Semantic search' }) as HTMLInputElement).checked).toBe(false));
    await waitFor(() => expect(saveButton().disabled).toBe(true));
  });

  it("a delete that fails keeps the model and shows the server's reason", async () => {
    setup({ models: [{ ...granite, state: 'ready' as const }, nomic, whisper] });
    api.removeLocalModel.mockRejectedValue(new Error('The model file is in use.'));
    renderAi();
    await userEvent.click(await screen.findByRole('button', { name: 'Delete Granite Embedding 97M multilingual' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    expect((await screen.findByRole('alert')).textContent).toContain('The model file is in use.');
    expect(screen.getByRole('button', { name: 'Delete Granite Embedding 97M multilingual' })).toBeTruthy();
  });

  it('turning AI off with running jobs asks first', async () => {
    setup();
    api.listAdminTasks.mockResolvedValue({ items: [], next_cursor: null, counts: { summary: { active: 2 }, asr: { active: 0 } } });
    api.updateAiConfig.mockImplementation(async (update) => ({ ...config, ...update }));
    renderAi();
    const field = await screen.findByRole('textbox', { name: /^Server address/ });
    fireEvent.change(field, { target: { value: '' } });
    await userEvent.click(saveButton());
    const dialog = await screen.findByRole('dialog', { name: '2 enrichment jobs are running' });
    expect(within(dialog).getByText('They finish with the settings they started with. Turn this off anyway?')).toBeTruthy();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(api.updateAiConfig).not.toHaveBeenCalled();
    await userEvent.click(saveButton());
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Turn off' }));
    await waitFor(() => expect(api.updateAiConfig).toHaveBeenCalledOnce());
  });

  it('a switch flipped while a removal is in flight survives the removal turning features off', async () => {
    setup({ models: [{ ...granite, state: 'ready' as const }, nomic, whisper] });
    const removal = deferred<{ model: LocalModel; disabled_features: string[] }>();
    api.removeLocalModel.mockReturnValue(removal.promise);
    renderAi();
    await userEvent.click(await screen.findByRole('button', { name: 'Delete Granite Embedding 97M multilingual' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await userEvent.click(screen.getByRole('switch', { name: 'Sync subtitles to audio' }));
    await act(async () => { removal.resolve({ model: granite, disabled_features: ['semantic_search'] }); });
    expect((screen.getByRole('switch', { name: 'Sync subtitles to audio' }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole('switch', { name: 'Semantic search' }) as HTMLInputElement).checked).toBe(false);
  });

  it('a feature the server newly turns off (another admin\'s Remove) is no phantom unsaved change', async () => {
    setup();
    const downloading = { ...granite, state: 'downloading' as const, bytes_done: 1, bytes_total: granite.size_bytes };
    api.listLocalModels.mockResolvedValueOnce({ models: [downloading, nomic, whisper] }).mockResolvedValue({ models: [{ ...granite, state: 'failed' }, nomic, whisper] });
    api.getAiConfig.mockResolvedValueOnce(config).mockResolvedValue({ ...config, ai_features_disabled: ['semantic_search'] });
    renderAi();
    await screen.findByRole('switch', { name: 'Semantic search' });
    await waitFor(() => expect(api.getAiConfig).toHaveBeenCalledTimes(2), { timeout: 4000 });
    await waitFor(() => expect((screen.getByRole('switch', { name: 'Semantic search' }) as HTMLInputElement).checked).toBe(false));
    expect(saveButton().disabled).toBe(true);
  });

  it('shows why a download failed and offers Retry (success criterion 5)', async () => {
    const failed = { ...whisper, state: 'failed' as const, reason: 'The download was corrupted; Retry' };
    setup({ models: [granite, nomic, failed] });
    api.downloadLocalModel.mockResolvedValue({ ...whisper, state: 'downloading', bytes_done: 0, bytes_total: whisper.size_bytes });
    renderAi();
    const list = await screen.findByRole('list', { name: 'Speech models' });
    expect(within(list).getByText('The download was corrupted; Retry')).toBeTruthy();
    expect(within(list).getByText('Failed')).toBeTruthy();
    await userEvent.click(within(list).getByRole('button', { name: 'Retry Whisper small' }));
    expect(api.downloadLocalModel).toHaveBeenCalledWith(whisper.id);
  });

  it('lets an admin lower the model threads, saved with the form', async () => {
    setup();
    api.updateAiConfig.mockImplementation(async (update) => ({ ...config, model_threads: update.model_threads || null }));
    renderAi();
    const threads = await screen.findByRole('combobox', { name: 'Model threads' });
    expect([...(threads as HTMLSelectElement).options].map((option) => option.textContent)).toEqual(['Automatic (3)', '1', '2', '3']);
    await userEvent.selectOptions(threads, '2');
    await userEvent.click(saveButton());
    await waitFor(() => expect(api.updateAiConfig).toHaveBeenCalledWith({ model_threads: 2 }));
  });
});

describe('enabling a feature that needs something missing', () => {
  const off = (key: string): Partial<AiConfig> => ({ ai_features_disabled: [key] });

  it('offers Download and turn on; Cancel returns the switch to off and focus to it', async () => {
    setup({ config: off('semantic_search') });
    renderAi();
    const toggle = await screen.findByRole('switch', { name: 'Semantic search' });
    await userEvent.click(toggle);
    const dialog = screen.getByRole('dialog', { name: 'Turn on Semantic search?' });
    expect(dialog.textContent).toContain('Granite Embedding 97M multilingual');
    expect(dialog.textContent).toContain('115 MB download, about 913 MB of memory while running, Apache-2.0 licence.');
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Download and turn on' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((toggle as HTMLInputElement).checked).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(toggle));
    expect(api.downloadLocalModel).not.toHaveBeenCalled();
  });

  it('Download and turn on starts the download now, keeps the switch on, and Save turns the feature on (success criterion 1)', async () => {
    setup({ config: off('semantic_search') });
    api.getAiConfig.mockResolvedValueOnce({ ...config, ai_features_disabled: ['semantic_search'] })
      .mockResolvedValue({ ...config, ai_features_disabled: ['semantic_search'], features: { ...config.features, semantic_search: { requires: 'search_model', ready: false, reason: 'Waiting for the search model' } } });
    api.downloadLocalModel.mockResolvedValue({ ...granite, state: 'downloading', bytes_done: 0, bytes_total: granite.size_bytes });
    api.updateAiConfig.mockImplementation(async (update) => ({ ...config, ...update }));
    renderAi();
    await userEvent.click(await screen.findByRole('switch', { name: 'Semantic search' }));
    await userEvent.click(screen.getByRole('button', { name: 'Download and turn on' }));
    expect(api.downloadLocalModel).toHaveBeenCalledWith(granite.id);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((screen.getByRole('switch', { name: 'Semantic search' }) as HTMLInputElement).checked).toBe(true);
    expect(await within(rowOf('ai.semantic-search')).findByText('Waiting for the search model')).toBeTruthy();
    expect(api.updateAiConfig).not.toHaveBeenCalled();
    await userEvent.click(saveButton());
    await waitFor(() => expect(api.updateAiConfig).toHaveBeenCalledWith({ ai_features_disabled: [] }));
  });

  it('works the same for Subtitles from speech with the speech model (success criterion 2)', async () => {
    setup({ config: off('subtitles_from_speech') });
    api.downloadLocalModel.mockResolvedValue({ ...whisper, state: 'downloading', bytes_done: 0, bytes_total: whisper.size_bytes });
    renderAi();
    await userEvent.click(await screen.findByRole('switch', { name: 'Subtitles from speech' }));
    const dialog = screen.getByRole('dialog', { name: 'Turn on Subtitles from speech?' });
    expect(dialog.textContent).toContain('486 MB download, about 859 MB of memory while running, MIT licence.');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Download and turn on' }));
    expect(api.downloadLocalModel).toHaveBeenCalledWith(whisper.id);
    expect((screen.getByRole('switch', { name: 'Subtitles from speech' }) as HTMLInputElement).checked).toBe(true);
  });

  it('a download that cannot start keeps the dialog open with the reason and the switch off', async () => {
    setup({ config: off('semantic_search') });
    api.downloadLocalModel.mockRejectedValue(new Error('Not enough free space: needs 127 MB, 40 MB free'));
    renderAi();
    await userEvent.click(await screen.findByRole('switch', { name: 'Semantic search' }));
    const dialog = screen.getByRole('dialog', { name: 'Turn on Semantic search?' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Download and turn on' }));
    expect((await within(dialog).findByRole('alert')).textContent).toBe('Not enough free space: needs 127 MB, 40 MB free');
    expect((screen.getByRole('switch', { name: 'Semantic search' }) as HTMLInputElement).checked).toBe(false);
    expect(saveButton().disabled).toBe(true);
  });

  it('Esc and the TV Back key act as Cancel', async () => {
    setup({ config: off('semantic_search') });
    renderAi();
    const toggle = await screen.findByRole('switch', { name: 'Semantic search' });
    await userEvent.click(toggle);
    act(() => { screen.getByRole('dialog').dispatchEvent(new Event('cancel', { cancelable: true })); });
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(toggle);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'GoBack' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((toggle as HTMLInputElement).checked).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(toggle));
  });

  it('Esc and the TV Back key do nothing while the download is starting', async () => {
    setup({ config: off('semantic_search') });
    const start = deferred<LocalModel>();
    api.downloadLocalModel.mockReturnValue(start.promise);
    renderAi();
    await userEvent.click(await screen.findByRole('switch', { name: 'Semantic search' }));
    await userEvent.click(screen.getByRole('button', { name: 'Download and turn on' }));
    expect(screen.getByRole('button', { name: 'Starting…' })).toBeTruthy();
    act(() => { screen.getByRole('dialog').dispatchEvent(new Event('cancel', { cancelable: true })); });
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'GoBack' });
    expect(screen.getByRole('dialog')).toBeTruthy();
    await act(async () => { start.resolve({ ...granite, state: 'downloading', bytes_done: 0, bytes_total: granite.size_bytes }); });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((screen.getByRole('switch', { name: 'Semantic search' }) as HTMLInputElement).checked).toBe(true);
  });

  it('without an assistant server it offers Go to Server address, which leaves the switch off and focuses the address', async () => {
    setup({ config: { base_url: '', enabled: false, ai_features_disabled: ['translate'], features: { ...config.features, translate: { requires: 'assistant', ready: false, reason: 'Needs your assistant server' } } } });
    renderAi();
    await userEvent.click(await screen.findByRole('switch', { name: 'Translate subtitles' }));
    const dialog = screen.getByRole('dialog', { name: 'Turn on Translate subtitles?' });
    expect(dialog.textContent).toContain('It needs your assistant server, and none is set.');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Go to Server address' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((screen.getByRole('switch', { name: 'Translate subtitles' }) as HTMLInputElement).checked).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox', { name: /^Server address/ })));
  });

  it('from a search result without the address row, Go to Server address opens AI & models', async () => {
    setup({ config: { base_url: '', enabled: false, ai_features_disabled: ['translate'] } });
    const onPop = vi.fn();
    window.addEventListener('popstate', onPop);
    const ai = sectionById('ai')!;
    render(<SettingsHostContext.Provider value={{ user: admin }}><SectionRows entries={ai.entries.filter((entry) => entry.id === 'ai.translate')} section={ai} /></SettingsHostContext.Provider>);
    await userEvent.click(await screen.findByRole('switch', { name: 'Translate subtitles' }));
    await userEvent.click(screen.getByRole('button', { name: 'Go to Server address' }));
    await waitFor(() => expect(window.location.pathname).toBe('/settings/ai'));
    expect(onPop).toHaveBeenCalledTimes(1);
    window.removeEventListener('popstate', onPop);
    window.history.replaceState(null, '', '/');
  });

  it('turns straight on when the feature is already served, for example by an external search model', async () => {
    setup({ config: { ai_features_disabled: ['semantic_search'], embedding_model: 'nomic-embed', features: { ...config.features, semantic_search: ready('search_model') } } });
    renderAi();
    const toggle = await screen.findByRole('switch', { name: 'Semantic search' });
    await userEvent.click(toggle);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((toggle as HTMLInputElement).checked).toBe(true);
  });

  it('a feature that is on shows what it waits for: Download when absent, the failure and Retry when failed', async () => {
    const failed = { ...whisper, state: 'failed' as const, reason: 'The download was corrupted; Retry' };
    setup({ models: [granite, nomic, failed], config: { features: { ...config.features, subtitles_from_speech: { requires: 'speech_model', ready: false, reason: 'The download was corrupted; Retry' } } } });
    api.downloadLocalModel.mockImplementation(async (id: string) => ({ ...(id === whisper.id ? whisper : granite), state: 'downloading', bytes_done: 0, bytes_total: 1 }));
    renderAi();
    await screen.findByRole('switch', { name: 'Semantic search' });
    const search = rowOf('ai.semantic-search');
    expect(within(search).getByText('The search model is not installed')).toBeTruthy();
    await userEvent.click(within(search).getByRole('button', { name: 'Download for Semantic search' }));
    expect(api.downloadLocalModel).toHaveBeenCalledWith(granite.id);
    const speech = rowOf('ai.subtitles-from-speech');
    expect(within(speech).getByText('The download was corrupted; Retry')).toBeTruthy();
    await userEvent.click(within(speech).getByRole('button', { name: 'Retry for Subtitles from speech' }));
    expect(api.downloadLocalModel).toHaveBeenLastCalledWith(whisper.id);
  });
});

describe('Go to Server address inside Settings', () => {
  const noServer = { base_url: '', enabled: false, ai_features_disabled: ['translate'] };
  function Shell({ start, onSection }: { start: 'account' | 'ai'; onSection: (section: string | null) => void }) {
    const [section, setSection] = useState<'account' | 'ai' | null>(start);
    return <SettingsSurface formatPreset="best" onFormatChange={vi.fn()} onLogout={vi.fn()} onSection={(next) => { onSection(next); setSection(next as 'ai'); }} section={section} user={admin} />;
  }
  async function goFromSearch() {
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search settings' }), 'Translate');
    await userEvent.click(await screen.findByRole('switch', { name: 'Translate subtitles' }));
    await userEvent.click(screen.getByRole('button', { name: 'Go to Server address' }));
  }

  it('from a search result, clears the search, opens AI & models and focuses the address', async () => {
    setup({ config: noServer });
    const onSection = vi.fn();
    render(<Shell onSection={onSection} start="account" />);
    await goFromSearch();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox', { name: /^Server address/ })));
    expect(onSection).toHaveBeenCalledWith('ai');
    expect((screen.getByRole('searchbox', { name: 'Search settings' }) as HTMLInputElement).value).toBe('');
    expect(screen.getByRole('heading', { name: 'AI & models' })).toBeTruthy();
  });

  it('searching while already on AI & models goes back to the section and focuses the address', async () => {
    setup({ config: noServer });
    const onSection = vi.fn();
    render(<Shell onSection={onSection} start="ai" />);
    await screen.findByRole('switch', { name: 'Semantic search' });
    // The address is an advanced row, hidden until Go to Server address shows advanced settings.
    expect(screen.queryByRole('textbox', { name: /^Server address/ })).toBeNull();
    await goFromSearch();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox', { name: /^Server address/ })));
    expect(onSection).not.toHaveBeenCalled();
    expect((screen.getByRole('searchbox', { name: 'Search settings' }) as HTMLInputElement).value).toBe('');
  });

  it('never steals focus from where the admin moved while AI & models was loading', async () => {
    setup({ config: noServer });
    const reload = deferred<AiConfig>();
    api.getAiConfig.mockResolvedValueOnce({ ...config, ...noServer }).mockReturnValueOnce(reload.promise);
    render(<Shell onSection={vi.fn()} start="account" />);
    await goFromSearch();
    await screen.findByRole('heading', { name: 'AI & models' });
    const searchbox = screen.getByRole('searchbox', { name: 'Search settings' });
    searchbox.focus();
    await act(async () => { reload.resolve({ ...config, ...noServer }); });
    await screen.findByRole('textbox', { name: /^Server address/ });
    expect(document.activeElement).toBe(searchbox);
  });
});
