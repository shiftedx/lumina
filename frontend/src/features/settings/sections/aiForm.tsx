import { createContext, type ReactNode, useContext, useEffect, useRef, useState } from 'react';

import { type AiConfig, type AiConfigUpdate, getAiConfig, listAdminTasks, listLocalModels, type LocalModel, type LocalModelRole, updateAiConfig } from '../../../api';
import { ConfirmDialog } from '../../../ui';
import { errorMessage } from '../../../utils';
import { useAdminResource } from '../../admin/useAdminResource';
import { type FormStatus, SectionForm } from '../SectionForm';

/** The feature switches: keys of AiConfig.ai_features_disabled and AiConfig.features. */
export const AI_FEATURE_KEYS = [
  'semantic_search', 'subtitles_from_speech', 'sync', 'translate', 'recap', 'episode_summaries', 'key_scenes', 'smart_collection_builder', 'match_tie_breaker', 'mute_strong_language', 'personal_recommendations',
] as const;
export type AiFeatureKey = (typeof AI_FEATURE_KEYS)[number];

/** Row labels and switch names; unique across the Settings registry. */
export const FEATURE_NAMES: Record<AiFeatureKey, string> = {
  semantic_search: 'Semantic search', subtitles_from_speech: 'Subtitles from speech', sync: 'Sync subtitles to audio', translate: 'Translate subtitles',
  recap: '“The story so far” recaps', episode_summaries: 'Summaries of watched episodes', key_scenes: 'Key scenes',
  smart_collection_builder: 'Smart-collection builder', match_tie_breaker: 'Match tie-breaker', mute_strong_language: 'Strong-language muting', personal_recommendations: 'Personalised recommendations',
};
export const featureName = (key: string): string => FEATURE_NAMES[key as AiFeatureKey] ?? key;

export type AiDraft = {
  base_url: string; model: string; api_key: string; clear_key: boolean; max_concurrency: string; context_tokens: string;
  asr_base_url: string; asr_model: string; embedding_model: string;
  /** Feature keys switched off (ai_features_disabled). */
  disabled: string[];
  /** '0' means automatic. */
  model_threads: string;
};

export const draftOf = (config: AiConfig): AiDraft => ({
  base_url: config.base_url, model: config.model, api_key: '', clear_key: false, max_concurrency: String(config.max_concurrency),
  context_tokens: String(config.context_tokens), asr_base_url: config.asr_base_url, asr_model: config.asr_model, embedding_model: config.embedding_model ?? '',
  disabled: [...config.ai_features_disabled], model_threads: String(config.model_threads ?? 0),
});

const sortedKeys = (keys: readonly string[]) => [...keys].sort();

/** Only changed fields are sent. The key is write-only (typed to replace, ticked to remove); the off-list is sorted. */
export function changes(config: AiConfig, draft: AiDraft): AiConfigUpdate {
  const update: AiConfigUpdate = {};
  for (const key of ['base_url', 'model', 'asr_base_url', 'asr_model'] as const) if (draft[key].trim() !== config[key]) update[key] = draft[key].trim();
  if (draft.embedding_model.trim() !== (config.embedding_model ?? '')) update.embedding_model = draft.embedding_model.trim();
  for (const key of ['max_concurrency', 'context_tokens'] as const) if (Number(draft[key]) !== config[key]) update[key] = Number(draft[key]);
  if (draft.clear_key) update.api_key = '';
  else if (draft.api_key) update.api_key = draft.api_key;
  const disabled = sortedKeys(draft.disabled);
  if (disabled.join() !== sortedKeys(config.ai_features_disabled).join()) update.ai_features_disabled = disabled;
  if (Number(draft.model_threads) !== (config.model_threads ?? 0)) update.model_threads = Number(draft.model_threads);
  return update;
}

export type AiForm = {
  config: AiConfig;
  draft: AiDraft;
  /** An updater function reads the latest draft, so an edit made while a request was in flight is kept. */
  set: <K extends keyof AiDraft>(key: K, value: AiDraft[K] | ((current: AiDraft[K]) => AiDraft[K])) => void;
  /** Catalog rows with live state; null while loading. */
  models: LocalModel[] | null;
  modelsError: string | null;
  /** Puts one row from a model API response in place. */
  replaceModel: (model: LocalModel) => void;
  reloadModels: () => void;
  reloadConfig: () => void;
};

const AiFormContext = createContext<AiForm | null>(null);

export function useAiForm(): AiForm {
  const form = useContext(AiFormContext);
  if (!form) throw new Error('AI & models rows render inside their section form.');
  return form;
}

/** The role's model that downloads serve: the admin's choice, else the catalog default. */
export const activeModel = (models: LocalModel[] | null, role: LocalModelRole): LocalModel | null =>
  models?.find((model) => model.role === role && model.active) ?? models?.find((model) => model.role === role && model.default) ?? null;

const POLL_MS = 1500;

/** AI & models' Save form: one draft over GET/PUT /api/admin/ai/config, plus live model rows. */
export function AiSettingsForm({ children }: { children: ReactNode }) {
  const configResource = useAdminResource(getAiConfig, 'Unable to load AI settings.');
  const modelsResource = useAdminResource(listLocalModels, 'Unable to load the on-device models.');
  const [draft, setDraft] = useState<AiDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const config = configResource.data;
  const models = modelsResource.data?.models ?? null;
  const { reload: reloadModels, setData: setModels } = modelsResource;
  const reloadConfig = configResource.reload;

  // The draft starts from the first load only; the refreshes below must never wipe unsaved edits.
  useEffect(() => { if (config && !draft) setDraft(draftOf(config)); }, [config, draft]);
  // Except features the server newly turned off (another admin's Remove): the draft follows, so Save is not left looking unsaved.
  const previousConfig = useRef(config);
  useEffect(() => {
    const previous = previousConfig.current;
    previousConfig.current = config;
    const added = previous && config ? config.ai_features_disabled.filter((key) => !previous.ai_features_disabled.includes(key)) : [];
    if (added.length) setDraft((current) => current && { ...current, disabled: [...new Set([...current.disabled, ...added])] });
  }, [config]);

  // polls every 1.5 s while a model downloads or verifies. The admin-only `model_state`
  // event can replace this once app events reach Settings sections (today only the workspace reducer sees them).
  const busy = models?.some((model) => model.state === 'downloading' || model.state === 'verifying') ?? false;
  useEffect(() => {
    if (!busy) return undefined;
    const timer = window.setInterval(reloadModels, POLL_MS);
    return () => window.clearInterval(timer);
  }, [busy, reloadModels]);

  // Readiness has no event of its own: refetch the config whenever a model's state changes.
  const states = models?.map((model) => `${model.id}:${model.state}`).join(',') ?? '';
  const seen = useRef('');
  useEffect(() => {
    if (!states) return;
    if (seen.current && seen.current !== states) reloadConfig();
    seen.current = states;
  }, [states, reloadConfig]);

  // Unmounting while asking resolves false, so a save never hangs.
  const [askTurnOff, setAskTurnOff] = useState<{ running: number; resolve: (ok: boolean) => void } | null>(null);
  useEffect(() => () => askTurnOff?.resolve(false), [askTurnOff]);
  const confirmTurnOff = (running: number) => new Promise<boolean>((resolve) => setAskTurnOff({ running, resolve }));

  if (configResource.error && !config) return <p className="auth-error" role="alert">{configResource.error}</p>;
  if (!config || !draft) return <p className="admin-note" role="status">Loading AI settings…</p>;
  const update = changes(config, draft);

  async function save() {
    if (update.base_url === '' || update.asr_base_url === '') {
      const counts = (await listAdminTasks('summary', 'active').catch(() => null))?.counts;
      const running = (counts?.summary.active ?? 0) + (counts?.asr.active ?? 0);
      if (running > 0 && !(await confirmTurnOff(running))) return;
    }
    setSaving(true);
    setStatus(null);
    try {
      const next = await updateAiConfig(update);
      configResource.setData(next);
      setDraft(draftOf(next)); // explicit: the response may equal the loaded object
      setStatus({ tone: 'ok', text: 'Saved. New summaries and transcriptions use these settings; running jobs keep the model they started with.' });
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, 'Unable to save AI settings.') });
    } finally {
      setSaving(false);
    }
  }
  const set: AiForm['set'] = (key, value) => { setStatus(null); setDraft((current) => current && { ...current, [key]: typeof value === 'function' ? value(current[key]) : value }); };
  const replaceModel = (model: LocalModel) => setModels((current) => current && { models: current.models.map((entry) => (entry.id === model.id ? model : entry)) });

  return (
    <AiFormContext.Provider value={{ config, draft, set, models, modelsError: modelsResource.error, replaceModel, reloadModels, reloadConfig }}>
      <SectionForm dirty={Object.keys(update).length > 0} label="AI & models" onDiscard={() => { setStatus(null); setDraft(draftOf(config)); }} onSave={() => { void save(); }} saveLabel="Save AI settings" saving={saving} status={status}>{children}</SectionForm>
      <ConfirmDialog
        body="They finish with the settings they started with. Turn this off anyway?"
        confirmLabel="Turn off"
        danger
        onCancel={() => { askTurnOff?.resolve(false); setAskTurnOff(null); }}
        onConfirm={() => { askTurnOff?.resolve(true); setAskTurnOff(null); }}
        open={askTurnOff !== null}
        title={`${askTurnOff?.running ?? 0} enrichment job${askTurnOff?.running === 1 ? ' is' : 's are'} running`}
      />
    </AiFormContext.Provider>
  );
}
