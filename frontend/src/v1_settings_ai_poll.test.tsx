import { render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { AiConfig, LocalModel } from './api';

const api = vi.hoisted(() => ({ getAiConfig: vi.fn(), listAdminTasks: vi.fn(), listLocalModels: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { sectionById } = await import('./features/settings/registry');
const { SectionRows } = await import('./features/settings/SettingRow');
const { SettingsHostContext } = await import('./features/settings/settingsHost');

afterEach(() => { vi.resetAllMocks(); });

const config = {
  base_url: '', model: '', has_api_key: false, max_concurrency: 3, context_tokens: 145000, embedding_model: '', asr_base_url: '', asr_model: '',
  enabled: false, asr_available: false, ai_features_disabled: [], features: {}, model_threads: null, model_threads_auto: 3,
} as AiConfig;
const downloading = {
  id: 'granite-embedding-97m-multilingual-r2-q8', role: 'search', name: 'Granite Embedding 97M multilingual', description: '', licence: 'Apache-2.0',
  size_bytes: 115_061_088, ram_bytes: 912_680_550, default: true, active: true, state: 'downloading', bytes_done: 1, bytes_total: 115_061_088, reason: null,
  running: false, features: ['semantic_search'],
} as LocalModel;

it('keeps polling a running download after one models request fails', async () => {
  api.getAiConfig.mockResolvedValue(config);
  api.listAdminTasks.mockResolvedValue({ items: [], next_cursor: null, counts: { download: {}, asr: {}, summary: {} } });
  api.listLocalModels.mockResolvedValueOnce({ models: [downloading] }).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ models: [downloading] });
  render(<SettingsHostContext.Provider value={{ user: { id: 'u1', role: 'admin' } as never, onMessage: vi.fn() }}><SectionRows section={sectionById('ai')!} /></SettingsHostContext.Provider>);
  await waitFor(() => expect(api.listLocalModels).toHaveBeenCalledTimes(3), { timeout: 5000 });
}, 10000);
