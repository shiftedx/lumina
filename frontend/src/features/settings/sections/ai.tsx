import { useEffect, useState } from 'react';
import { PlugZap } from 'lucide-react';

import { listAdminTasks, testAiConnection } from '../../../api';
import { Button, Checkbox, clampServerText, Field, fieldProps, Input, PasswordInput, StatusText } from '../../../ui';
import { errorMessage } from '../../../utils';
import '../settings.css';
import type { SettingEntry, SettingsSectionDef } from '../settingsTypes';
import { type AiFeatureKey, activeModel, AiSettingsForm, FEATURE_NAMES, useAiForm } from './aiForm';
import { featureDetail, FeatureSwitch } from './aiFeatures';
import { ModelThreads, SearchModels, SpeechModels } from './aiModels';

type Counts = Record<'asr' | 'summary', Record<string, number>>;
type TestResult = Awaited<ReturnType<typeof testAiConnection>>;

function AiStatus() {
  const { config, models } = useAiForm();
  const [counts, setCounts] = useState<Counts | null>(null);
  useEffect(() => {
    let active = true;
    void listAdminTasks('summary', 'active').then((page) => { if (active) setCounts(page.counts); }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  const local = activeModel(models, 'speech');
  const speech = config.features.subtitles_from_speech?.ready ?? config.asr_available;
  const summary = counts?.summary ?? {};
  const asr = counts?.asr ?? {};
  return (
    <dl className="g-stat-strip">
      <div><dt>Summaries</dt><dd>{config.enabled ? 'On' : 'Off'}</dd><dd className="admin-metric-note">{config.enabled ? config.model : 'No assistant server set'}</dd></div>
      <div><dt>Speech recognition</dt><dd>{speech ? 'On' : 'Off'}</dd><dd className="admin-metric-note">{!speech ? 'Transcripts come only from source captions' : local?.state === 'ready' ? local.name : config.asr_model}</dd></div>
      <div><dt>Summary jobs</dt><dd>{summary.active ?? 0}</dd><dd className="admin-metric-note">running · {summary.succeeded ?? 0} done · {(summary.failed ?? 0) + (summary.interrupted ?? 0)} failed</dd></div>
      <div><dt>Transcription jobs</dt><dd>{asr.active ?? 0}</dd><dd className="admin-metric-note">running · {asr.succeeded ?? 0} done · {(asr.failed ?? 0) + (asr.interrupted ?? 0)} failed</dd></div>
    </dl>
  );
}

type TextKey = 'base_url' | 'model' | 'embedding_model' | 'asr_base_url' | 'asr_model';

function textField(key: TextKey, name: string, hint: string, url = false) {
  return function TextField() {
    const { draft, set } = useAiForm();
    return (
      <Field hideLabel hint={hint} label={name}>
        {(ids) => <Input {...fieldProps(ids)} autoComplete="off" maxLength={url ? 2048 : 200} onChange={(event) => set(key, event.target.value)} placeholder={url ? 'http://192.168.1.20:8080/v1' : undefined} spellCheck={false} type={url ? 'url' : 'text'} value={draft[key]} />}
      </Field>
    );
  };
}

function numberField(key: 'max_concurrency' | 'context_tokens', name: string, hint: string, min: number, max: number) {
  return function NumberField() {
    const { draft, set } = useAiForm();
    return (
      <Field hideLabel hint={hint} label={name}>
        {(ids) => <Input {...fieldProps(ids)} inputMode="numeric" max={max} min={min} onChange={(event) => set(key, event.target.value)} required type="number" value={draft[key]} />}
      </Field>
    );
  };
}

function ApiKey() {
  const { config, draft, set } = useAiForm();
  return (
    <div className="g-control-stack">
      <Field hideLabel hint={config.has_api_key ? 'A key is saved. It is never shown again.' : 'Only if your server requires one.'} label="API key">
        {(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="new-password" disabled={draft.clear_key} maxLength={4096} onChange={(event) => set('api_key', event.target.value)} placeholder={config.has_api_key ? 'Saved key hidden' : 'None'} value={draft.api_key} />}
      </Field>
      {config.has_api_key ? <Checkbox checked={draft.clear_key} label="Remove the saved API key" onChange={(event) => set('clear_key', event.target.checked)} /> : null}
    </div>
  );
}

function AssistantTest() {
  const { config } = useAiForm();
  const [test, setTest] = useState<TestResult | 'testing' | null>(null);
  async function run() {
    setTest('testing');
    try {
      setTest(await testAiConnection());
    } catch (failure) {
      setTest({ ok: false, models: [], model_available: false, error: errorMessage(failure, 'The test could not run.') });
    }
  }
  return (
    <div className="g-subrows">
      <div className="g-actions">
        {!config.enabled ? <p className="admin-note">Save a server address and model to test the connection.</p> : null}
        <Button disabled={test === 'testing' || !config.enabled} icon={<PlugZap />} onClick={() => { void run(); }}>{test === 'testing' ? 'Testing…' : 'Test connection'}</Button>
      </div>
      {test && test !== 'testing' ? <TestOutcome model={config.model} result={test} /> : null}
    </div>
  );
}

function TestOutcome({ result, model }: { result: TestResult; model: string }) {
  return (
    <div className="admin-ai-test" role={result.ok ? 'status' : 'alert'}>
      <p><StatusText tone={result.ok ? 'ok' : 'danger'}><strong>{result.ok ? 'Connected.' : result.models.length ? 'Connected, but the model is missing.' : 'Connection failed.'}</strong> {result.ok ? `${model} is served.` : clampServerText(result.error ?? '')}</StatusText></p>
      {result.models.length ? (
        <>
          <small>Tested with the saved settings. The server offers {result.models.length} model{result.models.length === 1 ? '' : 's'}:</small>
          <ul>{result.models.map((name) => <li key={name}>{name === model ? <strong>{name}</strong> : name}</li>)}</ul>
        </>
      ) : <small>Tested with the saved settings.</small>}
    </div>
  );
}

function feature(key: AiFeatureKey, info: string, keywords: string[]): SettingEntry {
  const Control = () => <FeatureSwitch feature={key} />;
  return { id: `ai.${key.replace(/_/g, '-')}`, label: FEATURE_NAMES[key], layout: 'switch', info, keywords, Control, Detail: featureDetail(key) };
}

const STATUS_ENTRY: SettingEntry = {
  id: 'ai.status', label: 'AI activity', layout: 'block', keywords: ['summaries', 'speech recognition', 'jobs', 'summary jobs', 'transcription jobs'],
  info: 'Shows whether summaries and speech recognition are available, and how many summary and transcription jobs are running, done or failed. It is read when you open this section. Covers everyone on this server.',
  Control: AiStatus,
};

const FEATURE_ENTRIES: SettingEntry[] = [
  feature('semantic_search', 'Lets search find titles and moments by what they mean, not only by the words typed. It uses the on-device search model (a 115 MB download using about 913 MB of memory) or an external search model, and plain keyword search works without either. Affects search for everyone on this server.', ['meaning', 'embeddings', 'search by meaning', 'vector']),
  feature('subtitles_from_speech', 'Members can generate a subtitle track for a file that has none by listening to its speech. It uses the on-device speech model (a 486 MB download using about 859 MB of memory; a 2-hour film takes about 17 minutes) or your own speech server. Affects everyone who watches on this server.', ['asr', 'transcription', 'whisper', 'generate subtitles', 'speech to text']),
  feature('sync', 'Shifts a subtitle track so its lines appear when people speak. It uses speech timings when the speech model or a transcript is available and simple speech detection otherwise, so it needs nothing extra. Affects everyone on this server.', ['subtitle timing', 'offset', 'align']),
  feature('translate', "Members can translate a subtitle track into another language. It needs your assistant server, which receives the track's text. Affects everyone on this server.", ['translation', 'language']),
  feature('recap', "Shows “The story so far” on shows you are partway through: a short recap of earlier episodes that cites them and never spoils later ones. It needs your assistant server; without it Lumina shows the previous episodes' overviews instead. Affects everyone on this server.", ['story so far', 'previously on', 'recap', 'episodes']),
  feature('episode_summaries', "Episodes you have finished show a short summary written by your assistant from the dialogue. Episodes you have not finished only ever show the episode guide's teaser. It needs your assistant server and affects everyone on this server.", ['summaries', 'episode summary', 'watched episodes', 'spoilers']),
  feature('key_scenes', 'Title pages of films and shows you have watched quote a few key lines with a button to jump to each moment, written from the dialogue by your assistant. It needs your assistant server. Affects everyone on this server.', ['key scenes', 'quotes', 'moments', 'jump to']),
  feature('smart_collection_builder', 'Members can describe a collection in plain words and get rules they can edit. It needs your assistant server; without it they build the rules by hand. Affects everyone on this server.', ['smart collection', 'collections', 'rules']),
  feature('match_tie_breaker', 'Chooses between close TMDB matches for titles Lumina could not match on its own. It needs your assistant server; without it close matches stay unmatched for you to fix. Affects the whole Library.', ['tmdb', 'unmatched', 'identify']),
  feature('mute_strong_language', "Lets members mute strong language in their own playback in Lumina's web player. It needs nothing extra and works best when subtitles or a transcript exist. Turning it off here hides it for everyone on this server.", ['profanity', 'swearing', 'mute']),
  feature('personal_recommendations', "Picks what Home, Explore and Up next recommend for each member from what they have watched, followed and chosen, using this server's own processor and no outside service. It needs nothing extra. Turning it off returns every recommendation to the simpler ranking Lumina used before, and deletes nothing.", ['recommendations', 'picked for you', 'up next', 'personalized', 'personalised', 'for you']),
];

const MODEL_ENTRIES: SettingEntry[] = [
  {
    id: 'ai.search-model', label: 'Search model', layout: 'block', keywords: ['granite', 'nomic', 'embedding', 'download', 'on-device', 'local model'],
    info: "Lets semantic search understand meaning on this server's own processor, with no other service involved. The default model is a 115 MB download that uses about 913 MB of memory and reads about 62 short texts a second on a 4-core processor; the alternative is English-only and about 3.5 times slower. Affects search for everyone on this server.",
    Control: SearchModels,
  },
  {
    id: 'ai.speech-model', label: 'Speech model', layout: 'block', keywords: ['whisper', 'faster-whisper', 'large-v3-turbo', 'download', 'on-device', 'local model'],
    info: "Turns speech into subtitle text on this server's own processor, with word timings for subtitle sync. The default model is a 486 MB download that uses about 859 MB of memory and transcribes a 2-hour film in about 17 minutes on a 4-core processor; the accurate model is 1.6 GB, uses about 1.7 GB of memory and is about 4 times slower. Affects subtitles for everyone on this server.",
    Control: SpeechModels,
  },
  {
    id: 'ai.model-threads', advanced: true, label: 'Model threads', keywords: ['cpu', 'cores', 'threads', 'performance'],
    info: 'How many processor cores the on-device models may use at once. Automatic uses all but one of the cores this server gives Lumina, which was fastest in testing; lower it if other apps on the server slow down. Affects search indexing and subtitle generation for everyone.',
    Control: ModelThreads,
  },
];

const ASSISTANT_ENTRIES: SettingEntry[] = [
  {
    id: 'ai.assistant-address', advanced: true, label: 'Assistant server', keywords: ['openai', 'server address', 'endpoint', 'base url', 'llm'],
    info: 'The address of an OpenAI-compatible server on your network that writes summaries, recaps and translations, including its /v1 path. Lumina sends text only to this address, and leaving it empty turns those features off. Affects everyone on this server.',
    Control: textField('base_url', 'Server address', 'Include the /v1 path. Leave empty to turn assistant features off.', true),
  },
  {
    id: 'ai.assistant-model', advanced: true, label: 'Assistant model', keywords: ['model', 'model name'],
    info: 'The model name exactly as your assistant server lists it. Test connection shows the names it offers. Affects every summary, recap and translation.',
    Control: textField('model', 'Model', 'Exactly as the server lists it.'),
  },
  {
    id: 'ai.api-key', advanced: true, label: 'Assistant key', keywords: ['api key', 'token', 'secret', 'remove key'],
    info: 'The key your assistant server asks for, if it asks for one. It is stored on this server and never shown again, so type a new one to replace it. Affects every request Lumina sends to that server.',
    Control: ApiKey,
  },
  {
    id: 'ai.max-concurrency', advanced: true, label: 'Requests at once', keywords: ['simultaneous model requests', 'parallel', 'concurrency'],
    info: 'How many requests Lumina sends to the assistant server at the same time, from 1 to 16. Match what the server can serve at once, because extra requests only wait there. Affects summaries, recaps and translations for everyone.',
    Control: numberField('max_concurrency', 'Simultaneous model requests', '1 to 16.', 1, 16),
  },
  {
    id: 'ai.context-window', advanced: true, label: 'Context window', keywords: ['tokens', 'context window', 'long transcripts'],
    info: "How much text the assistant model can read at once, in tokens (a token is roughly three quarters of a word). Long transcripts are split to fit, and a change applies to new summaries. Affects everyone's summaries.",
    Control: numberField('context_tokens', 'Context window (tokens)', '4,096 to 2,000,000.', 4096, 2_000_000),
  },
  {
    id: 'ai.embedding-model', advanced: true, label: 'External search model', keywords: ['embedding model', 'semantic search', 're-index'],
    info: 'An embedding model on your assistant server that semantic search can use when no on-device search model is installed. An installed on-device search model always wins, and changing this re-indexes the Library in the background. Affects search for everyone.',
    Control: textField('embedding_model', 'Embedding model', 'Optional. An installed on-device search model wins.'),
  },
  {
    id: 'ai.assistant-test', advanced: true, label: 'Assistant connection', layout: 'block', keywords: ['test connection', 'check', 'models offered'],
    info: 'Checks that Lumina can reach the saved assistant server and that it serves the chosen model. It uses the saved settings, so save your changes first. Running it changes nothing.',
    Control: AssistantTest,
  },
];

const SPEECH_SERVER_ENTRIES: SettingEntry[] = [
  {
    id: 'ai.speech-address', advanced: true, label: 'Speech server', keywords: ['transcription server address', 'asr', 'whisper'],
    info: 'The address of your own OpenAI-compatible speech server, used for subtitles from speech when no on-device speech model is installed. An installed speech model always wins, and leaving this empty is fine. Affects everyone on this server.',
    Control: textField('asr_base_url', 'Transcription server address', 'Optional. An installed on-device speech model wins.', true),
  },
  {
    id: 'ai.speech-server-model', advanced: true, label: 'Speech server model', keywords: ['transcription model', 'whisper'],
    info: 'The model name your speech server expects, for example a Whisper model. It is used only while that speech server is in use. Affects subtitles generated for everyone.',
    Control: textField('asr_model', 'Transcription model', 'For example a Whisper model name.'),
  },
];

/** AI & models: one Save form; model actions act at once. */
export const AI_SECTION: SettingsSectionDef = {
  id: 'ai', group: 'server', label: 'AI & models', summary: 'The AI features, the on-device search and speech models, and the assistant and speech servers they can use.',
  Provider: AiSettingsForm,
  entries: [STATUS_ENTRY, ...FEATURE_ENTRIES, ...MODEL_ENTRIES, ...ASSISTANT_ENTRIES, ...SPEECH_SERVER_ENTRIES],
};
