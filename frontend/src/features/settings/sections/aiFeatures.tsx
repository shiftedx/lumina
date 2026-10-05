import { type ComponentType, type KeyboardEvent, useEffect, useRef, useState } from 'react';

import { type AiRequirement, downloadLocalModel, type LocalModel } from '../../../api';
import { routePath } from '../../../app/routes';
import { Button, Dialog } from '../../../ui';
import { SettingSwitch } from '../SettingRow';
import { errorMessage } from '../../../utils';
import { settingDomId } from '../SettingRow';
import { type SettingsHost, useSettingsHost } from '../settingsHost';
import { type AiFeatureKey, activeModel, FEATURE_NAMES, useAiForm } from './aiForm';
import { modelFacts } from './aiModels';

/** What each feature row says it needs. */
export const NEEDS: Record<AiRequirement, string> = { search_model: 'Needs the search model', speech_model: 'Needs the speech model', assistant: 'Needs your assistant server' };
const ROLE = { search_model: 'search', speech_model: 'speech' } as const;

/** What the dialog says a feature does; only features that need something appear. */
const WHAT: Partial<Record<AiFeatureKey, string>> = {
  semantic_search: 'Search finds titles and moments by what they mean, not only by the words typed.',
  subtitles_from_speech: 'Members can generate a subtitle track for files that have none, from their speech.',
  translate: 'Members can translate a subtitle track into another language.',
  recap: 'Shows you are partway through open with “The story so far”: a short recap of the episodes before, without spoilers.',
  episode_summaries: 'Episodes a member has finished show a short summary written from the dialogue.',
  key_scenes: 'Title pages of what a member has watched quote a few key lines, each with a button to jump to that moment.',
  smart_collection_builder: 'Members can describe a collection in words and get editable rules.',
  match_tie_breaker: 'Close TMDB matches are decided for you instead of waiting in Titles without a match.',
};

type Ask = { kind: 'model'; model: LocalModel } | { kind: 'assistant' };

const addressField = () => document.getElementById(settingDomId('ai.assistant-address'))?.querySelector('input') ?? null;

/**
 * Focuses the Server address field. In a search result, where that row is not on screen, Settings opens
 * AI & models (clearing the search) and the field is focused once the section has loaded.
 */
function goToServerAddress(openSection: SettingsHost['onOpenSection'], reveal: SettingsHost['onAdvancedChange']) {
  // The address is an advanced row: show advanced settings so it is there to focus.
  reveal?.(true);
  const field = addressField();
  if (field) { field.focus(); return; }
  if (!openSection) {
    // Outside the Settings shell: navigate, and the app opens AI & models.
    window.history.pushState(null, '', routePath({ surface: 'settings', section: 'ai' }));
    window.dispatchEvent(new PopStateEvent('popstate'));
    return;
  }
  if (!openSection('ai')) return;
  // The section loads its config before the field renders.
  const observer = new MutationObserver(() => {
    // The admin moved on while the section loaded: leave their focus alone.
    if (document.activeElement && document.activeElement !== document.body) { observer.disconnect(); return; }
    const shown = addressField();
    if (shown) { observer.disconnect(); shown.focus(); }
  });
  observer.observe(document.body, { childList: true, subtree: true });
  window.setTimeout(() => observer.disconnect(), 10_000);
}

function EnableDialog({ feature, ask, onCancel, onDownloaded, onGoToAddress }: {
  feature: AiFeatureKey; ask: Ask; onCancel: () => void; onDownloaded: (model: LocalModel) => void; onGoToAddress: () => void;
}) {
  const primary = useRef<HTMLButtonElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function download(model: LocalModel) {
    setBusy(true);
    setError(null);
    try {
      onDownloaded(await downloadLocalModel(model.id));
    } catch (failure) {
      setError(errorMessage(failure, 'The download could not start.'));
      setBusy(false);
    }
  }
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'GoBack' || event.key === 'BrowserBack') { event.preventDefault(); if (busy) return; onCancel(); }
  };
  return (
    // The wrapper only carries the TV Back key (events bubble from the top-layer dialog through the React tree).
    <div onKeyDown={onKeyDown} style={{ display: 'contents' }}>
      <Dialog
        busy={busy}
        footer={(
          <>
            <Button disabled={busy} onClick={onCancel} variant="quiet">Cancel</Button>
            {ask.kind === 'model'
              ? <Button busy={busy} onClick={() => { void download(ask.model); }} ref={primary} variant="primary">{busy ? 'Starting…' : 'Download and turn on'}</Button>
              : <Button onClick={onGoToAddress} ref={primary} variant="primary">Go to Server address</Button>}
          </>
        )}
        initialFocus={primary}
        onClose={onCancel}
        open
        size="md"
        title={`Turn on ${FEATURE_NAMES[feature]}?`}
      >
        {WHAT[feature] ? <p>{WHAT[feature]}</p> : null}
        {ask.kind === 'model'
          ? <p>It needs <strong>{ask.model.name}</strong>, which is not installed: {modelFacts(ask.model)} It downloads in the background, and the feature starts working once it is ready and you save.</p>
          : <p>It needs your assistant server, and none is set. Enter its address under Assistant server, then save.</p>}
        {error ? <p className="auth-error" role="alert">{error}</p> : null}
      </Dialog>
    </div>
  );
}

/** A feature's on/off switch. It edits the section draft; Save AI settings sends it. */
export function FeatureSwitch({ feature }: { feature: AiFeatureKey }) {
  const { config, draft, set, models, replaceModel } = useAiForm();
  const { onOpenSection, onAdvancedChange } = useSettingsHost();
  const [ask, setAsk] = useState<Ask | null>(null);
  const switchRef = useRef<HTMLInputElement>(null);
  const on = !draft.disabled.includes(feature);
  const readiness = config.features[feature];
  const requires = readiness?.requires ?? null;
  const turn = (value: boolean) => set('disabled', (current) => [...current.filter((key) => key !== feature), ...(value ? [] : [feature])]);

  function toggle() {
    if (on) { turn(false); return; }
    if (requires === 'assistant' && !draft.base_url.trim()) { setAsk({ kind: 'assistant' }); return; }
    const model = requires === 'search_model' || requires === 'speech_model' ? activeModel(models, ROLE[requires]) : null;
    if (model && !readiness?.ready && model.state === 'absent') { setAsk({ kind: 'model', model }); return; }
    turn(true);
  }
  // After the dialog unmounts; a modal dialog blocks focus outside it while open.
  const close = (then: () => void) => { setAsk(null); window.setTimeout(then, 0); };

  return (
    <div className="g-feature-switch">
      <SettingSwitch checked={on} onChange={(next) => (next ? toggle() : turn(false))} ref={switchRef} />
      <small id={`ai-${feature}-needs`}>{requires ? NEEDS[requires] : 'Needs nothing extra'}</small>
      {ask ? (
        <EnableDialog
          ask={ask}
          feature={feature}
          onCancel={() => close(() => switchRef.current?.focus())}
          onDownloaded={(model) => { replaceModel(model); turn(true); close(() => switchRef.current?.focus()); }}
          onGoToAddress={() => close(() => goToServerAddress(onOpenSection, onAdvancedChange))}
        />
      ) : null}
    </div>
  );
}

/** A feature row's live detail: what an on switch is waiting for, with Download or Retry. */
export function featureDetail(feature: AiFeatureKey): ComponentType {
  return function FeatureDetail() {
    const { config, draft, models, replaceModel } = useAiForm();
    const [error, setError] = useState<string | null>(null);
    const readiness = config.features[feature];
    if (draft.disabled.includes(feature) || !readiness || readiness.ready) return null;
    const requires = readiness.requires;
    const model = requires === 'search_model' || requires === 'speech_model' ? activeModel(models, ROLE[requires]) : null;
    const name = FEATURE_NAMES[feature];
    async function download(target: LocalModel) {
      setError(null);
      try {
        replaceModel(await downloadLocalModel(target.id));
      } catch (failure) {
        setError(errorMessage(failure, 'The download could not start.'));
      }
    }
    return (
      <p className="setting-feature-detail">
        <span>{model?.state === 'failed' && model.reason ? model.reason : readiness.reason}</span>
        {model?.state === 'absent' ? <Button aria-label={`Download for ${name}`} onClick={() => { void download(model); }}>Download</Button> : null}
        {model?.state === 'failed' ? <Button aria-label={`Retry for ${name}`} onClick={() => { void download(model); }}>Retry</Button> : null}
        {error ? <span className="auth-error" role="alert">{error}</span> : null}
      </p>
    );
  };
}
