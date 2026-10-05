import { useState } from 'react';

import { activateLocalModel, cancelLocalModelDownload, downloadLocalModel, type LocalModel, type LocalModelRole, removeLocalModel } from '../../../api';
import { Button, clampServerText, ConfirmDialog, Field, fieldProps, ProgressBar, Select, StatusText, type StatusTone } from '../../../ui';
import { errorMessage } from '../../../utils';
import { InfoButton } from '../InfoButton';
import { useSettingsHost } from '../settingsHost';
import { featureName, useAiForm } from './aiForm';

/** Decimal sizes, as the catalog and the spec quote them (115 MB, 1.6 GB). */
export const sizeText = (bytes: number): string => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`);

/** "115 MB download, about 913 MB of memory while running, Apache-2.0 licence." */
export const modelFacts = (model: LocalModel): string => `${sizeText(model.size_bytes)} download, about ${sizeText(model.ram_bytes)} of memory while running, ${model.licence} licence.`;

const STATE_LABELS: Record<LocalModel['state'], string> = { absent: 'Not installed', downloading: 'Downloading', verifying: 'Checking the download', ready: 'Ready', failed: 'Failed' };
const STATE_TONES: Record<LocalModel['state'], StatusTone> = { absent: 'muted', downloading: 'attention', verifying: 'attention', ready: 'ok', failed: 'danger' };
const REQUIREMENT: Record<LocalModelRole, 'search_model' | 'speech_model'> = { search: 'search_model', speech: 'speech_model' };

function ModelItem({ model }: { model: LocalModel }) {
  const { config, set, replaceModel, reloadModels, reloadConfig } = useAiForm();
  const { onMessage = () => undefined } = useSettingsHost();
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(errorMessage(failure, 'That did not work. Try again.'));
    } finally {
      setBusy(false);
    }
  }
  const download = () => run(async () => replaceModel(await downloadLocalModel(model.id)));
  const cancel = () => run(async () => replaceModel(await cancelLocalModelDownload(model.id)));
  // Choosing a model flips the other row's `active` and may change readiness, so both reload.
  const activate = () => run(async () => { replaceModel(await activateLocalModel(model.id)); reloadModels(); reloadConfig(); });
  const needs = () => model.features.filter((key) => config.features[key]?.requires === REQUIREMENT[model.role]).map(featureName).join(', ');
  const effectText = () => (model.active && needs() ? ` Features that need it (${needs()}) turn off unless an external server covers them.` : '');
  function remove() {
    void run(async () => {
      const result = await removeLocalModel(model.id);
      replaceModel(result.model);
      // The server already turned these off; mirror that in the draft so Save is not left looking unsaved.
      if (result.disabled_features.length) set('disabled', (current) => [...new Set([...current, ...result.disabled_features])]);
      onMessage(result.disabled_features.length ? `Deleted ${model.name}. Turned off: ${result.disabled_features.map(featureName).join(', ')}.` : `Deleted ${model.name}.`);
      reloadConfig();
    });
  }

  return (
    <li aria-busy={busy} className="g-list-row setting-model">
      <div className="setting-model-head">
        <strong>{model.name}</strong>
        {model.active ? <StatusText tone="ok">In use</StatusText> : null}
        <span aria-live="polite"><StatusText tone={STATE_TONES[model.state]}>{STATE_LABELS[model.state]}{model.state === 'ready' && model.running ? ' · running' : ''}</StatusText></span>
        <InfoButton id={`model-${model.id}-info`} label={model.name} text={`${model.description} ${modelFacts(model)}`} />
      </div>
      <small>{modelFacts(model)}</small>
      {model.state === 'downloading' ? (
        <div className="setting-model-progress">
          <ProgressBar label={`${model.name} download`} value={model.bytes_total ? ((model.bytes_done ?? 0) / model.bytes_total) * 100 : null} />
          <small>{model.bytes_total ? `${sizeText(model.bytes_done ?? 0)} of ${sizeText(model.bytes_total)}` : 'Starting…'}</small>
        </div>
      ) : null}
      {model.reason ? <p className={model.state === 'failed' ? 'auth-error' : 'admin-note'}>{model.reason}</p> : null}
      <div className="admin-member-actions">
        {model.state === 'absent' ? <Button aria-label={`Download ${model.name}`} disabled={busy} onClick={() => { void download(); }}>Download</Button> : null}
        {model.state === 'failed' ? <Button aria-label={`Retry ${model.name}`} disabled={busy} onClick={() => { void download(); }}>Retry</Button> : null}
        {model.state === 'downloading' ? <Button aria-label={`Cancel ${model.name} download`} disabled={busy} onClick={() => { void cancel(); }}>Cancel</Button> : null}
        {model.state === 'ready' ? <Button aria-label={`Delete ${model.name}`} disabled={busy} onClick={() => setDeleting(true)} variant="quiet">Delete</Button> : null}
        {!model.active ? <Button aria-label={`Use ${model.name}`} disabled={busy} onClick={() => { void activate(); }}>Use this model</Button> : null}
      </div>
      {error ? <p className="auth-error" role="alert">{clampServerText(error)}</p> : null}
      <ConfirmDialog
        body={`This frees ${sizeText(model.size_bytes)}.${effectText()}`}
        busy={busy}
        confirmLabel="Delete"
        danger
        onCancel={() => setDeleting(false)}
        onConfirm={() => { setDeleting(false); remove(); }}
        open={deleting}
        title={`Delete \u201c${model.name}\u201d?`}
      />
    </li>
  );
}

function ModelRole({ role }: { role: LocalModelRole }) {
  const { models, modelsError } = useAiForm();
  if (modelsError && !models) return <p className="auth-error" role="alert">{modelsError}</p>;
  if (!models) return <p className="admin-note" role="status">Loading the on-device models…</p>;
  return (
    <ul aria-label={role === 'search' ? 'Search models' : 'Speech models'} className="setting-models">
      {models.filter((model) => model.role === role).map((model) => <ModelItem key={model.id} model={model} />)}
    </ul>
  );
}

export const SearchModels = () => <ModelRole role="search" />;
export const SpeechModels = () => <ModelRole role="speech" />;

/** Threads for on-device models: automatic, or lower (never higher) than the automatic count. */
export function ModelThreads() {
  const { config, draft, set } = useAiForm();
  const most = Math.max(config.model_threads_auto, config.model_threads ?? 0);
  return (
    <Field hideLabel label="Model threads">
      {(ids) => (
        <Select {...fieldProps(ids)} onChange={(event) => set('model_threads', event.target.value)} value={draft.model_threads}>
          <option value="0">Automatic ({config.model_threads_auto})</option>
          {Array.from({ length: most }, (_, index) => String(index + 1)).map((count) => <option key={count} value={count}>{count}</option>)}
        </Select>
      )}
    </Field>
  );
}
