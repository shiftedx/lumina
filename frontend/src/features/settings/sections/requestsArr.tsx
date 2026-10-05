import { ArrowRight, Plus, PlugZap, Sparkles, Trash2 } from 'lucide-react';
import { useState } from 'react';

import { Button, ConfirmDialog, Field, fieldProps, IconButton, Input, PasswordInput, Select, StatusText } from '../../../ui';
import { errorMessage, formatDateTime } from '../../../utils';
import {
  type ArrKind, type ArrServer, type ArrServerInput, createAnimeLanguageProfiles, createArrServer, deleteArrServer, type PathMapping, testArrServer, updateArrServer,
} from '../requestsSettingsApi';
import { type FormStatus, SectionForm } from '../SectionForm';
import { SettingSwitch } from '../SettingRow';
import { useSettingsHost } from '../settingsHost';
import { useRequestsForm } from './requestsContext';

const NAMES: Record<ArrKind, string> = { sonarr: 'Sonarr', radarr: 'Radarr' };
const DEFAULT_MAPS: Record<ArrKind, PathMapping[]> = { sonarr: [{ remote: '/tv', local: '/media/tv' }], radarr: [{ remote: '/movies', local: '/media/movies' }] };

type Draft = { base_url: string; api_key: string; enabled: boolean; root_folder: string; quality_profile_id: string; anime_root_folder: string; anime_quality_profile_id: string; path_mappings: PathMapping[] };

const draftOf = (kind: ArrKind, server?: ArrServer): Draft => ({
  base_url: server?.base_url ?? '', api_key: '', enabled: server?.enabled ?? true, root_folder: server?.root_folder ?? '', quality_profile_id: server?.quality_profile_id == null ? '' : String(server.quality_profile_id),
  anime_root_folder: server?.anime_root_folder ?? '', anime_quality_profile_id: server?.anime_quality_profile_id == null ? '' : String(server.anime_quality_profile_id),
  path_mappings: server ? (server.path_mappings ?? []).map((row) => ({ ...row })) : DEFAULT_MAPS[kind].map((row) => ({ ...row })),
});

const NEW_KEY = 'Enter the API key again for the new address';
const needsKey = (error: unknown) => error instanceof Error && error.message === 'api_key_required';
const num = (value: string) => (value === '' ? null : Number(value));
/** The saved shape. The key is write-only: it joins the payload only when typed, and is never read back. */
export function serverPayload(kind: ArrKind, draft: Draft): ArrServerInput {
  const input: ArrServerInput = {
    kind, name: NAMES[kind], base_url: draft.base_url.trim(), enabled: draft.enabled, root_folder: draft.root_folder || null, quality_profile_id: num(draft.quality_profile_id),
    path_mappings: draft.path_mappings.map((row) => ({ remote: row.remote.trim(), local: row.local.trim() })).filter((row) => row.remote && row.local),
  };
  if (kind === 'sonarr') { input.anime_root_folder = draft.anime_root_folder || null; input.anime_quality_profile_id = num(draft.anime_quality_profile_id); }
  if (draft.api_key) input.api_key = draft.api_key;
  return input;
}

function withCurrent<T extends string | number>(options: { value: T; label: string }[], current: T | '' | null | undefined, fallbackLabel: (value: T) => string) {
  return current === '' || current == null || options.some((option) => option.value === current) ? options : [...options, { value: current as T, label: fallbackLabel(current as T) }];
}

export function ArrCard({ kind }: { kind: ArrKind }) {
  const form = useRequestsForm();
  const showAdvanced = useSettingsHost().showAdvanced ?? true;
  const server = form.serverOf(kind);
  const test = form.tests[kind];
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const [testing, setTesting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const name = NAMES[kind];

  if (form.servers.error && !form.servers.data) return <p className="auth-error" role="alert">{form.servers.error}</p>;
  if (!form.servers.data) return <p className="admin-note" role="status">Loading {name}…</p>;
  const current = draft ?? draftOf(kind, server);
  const edit = (patch: Partial<Draft>) => { setStatus(null); if ('api_key' in patch || 'base_url' in patch) setKeyError(null); setDraft({ ...current, ...patch }); };
  const baseline = draftOf(kind, server);
  const dirty = JSON.stringify(serverPayload(kind, current)) !== JSON.stringify(serverPayload(kind, baseline));
  const keySaved = Boolean(server?.api_key_set ?? server?.has_api_key);

  const roots = withCurrent((test?.root_folders ?? []).map((root) => ({ value: root.path, label: root.path })), current.root_folder, (value) => value);
  const animeRoots = withCurrent((test?.root_folders ?? []).map((root) => ({ value: root.path, label: root.path })), current.anime_root_folder, (value) => value);
  const profiles = (id: string) => withCurrent((test?.quality_profiles ?? []).map((profile) => ({ value: String(profile.id), label: profile.name })), id, (value) => `Profile ${value}`);

  async function runTest() {
    setTesting(true);
    setStatus(null);
    try {
      const result = await testArrServer({ kind, base_url: current.base_url.trim(), ...(current.api_key ? { api_key: current.api_key } : {}), ...(server ? { id: server.id } : {}) });
      form.setTest(kind, result);
      if (result.ok) setDraft({
        ...current,
        root_folder: current.root_folder || result.root_folders[0]?.path || '', quality_profile_id: current.quality_profile_id || String(result.quality_profiles[0]?.id ?? ''),
        ...(kind === 'sonarr' ? { anime_root_folder: current.anime_root_folder || result.root_folders[1]?.path || result.root_folders[0]?.path || '', anime_quality_profile_id: current.anime_quality_profile_id || String(result.quality_profiles[0]?.id ?? '') } : {}),
      });
    } catch (error) {
      if (needsKey(error)) setKeyError(NEW_KEY); else form.setTest(kind, { ok: false, root_folders: [], quality_profiles: [], error: errorMessage(error, 'The test could not run.') });
    } finally {
      setTesting(false);
    }
  }

  async function save() {
    if (!current.base_url.trim()) { setStatus({ tone: 'error', text: `Enter the ${name} address first.` }); return; }
    if (!server && !current.api_key) { setKeyError(`Enter the ${name} API key.`); return; }
    setSaving(true);
    setStatus(null);
    try {
      const payload = serverPayload(kind, current);
      const saved = server ? await updateArrServer(server.id, payload) : await createArrServer(payload);
      form.putServer(saved);
      setDraft(null);
      setStatus({ tone: 'ok', text: `${name} saved.` });
    } catch (error) {
      if (needsKey(error)) setKeyError(NEW_KEY); else setStatus({ tone: 'error', text: errorMessage(error, `Unable to save ${name}.`) });
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!server) return;
    setConfirmDelete(false);
    try {
      await deleteArrServer(server.id);
      form.removeServer(server.id);
      form.setTest(kind, undefined);
      setDraft(null);
      setStatus({ tone: 'ok', text: `${name} removed.` });
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, `Unable to remove ${name}.`) });
    }
  }

  const setMap = (index: number, patch: Partial<PathMapping>) => edit({ path_mappings: current.path_mappings.map((row, at) => (at === index ? { ...row, ...patch } : row)) });
  const select = (label: string, value: string, onChange: (value: string) => void, options: { value: string; label: string }[], none: string) => (
    <Field label={label}>{(ids) => (
      <Select {...fieldProps(ids)} onChange={(event) => onChange(event.currentTarget.value)} value={value}>
        <option value="">{none}</option>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </Select>
    )}</Field>
  );

  return (
    <div className="g-req">
      <SectionForm dirty={dirty} label={name} onDiscard={() => { setDraft(null); setStatus(null); }} onSave={() => { void save(); }} saveLabel={`Save ${name}`} saving={saving} status={status}>
        <div className="g-subrows">
          <SettingSwitch checked={current.enabled} label={`Send requests to ${name}`} onChange={(enabled) => edit({ enabled })} />
          <Field label={`${name} address`} hint="For example http://sonarr.lan:8989">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" inputMode="url" onChange={(event) => edit({ base_url: event.currentTarget.value })} value={current.base_url} />}</Field>
          <Field error={keyError} label={`${name} API key`} hint={keySaved ? 'A key is saved. Type a new one to replace it.' : undefined}>
            {(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="new-password" onChange={(event) => edit({ api_key: event.currentTarget.value })} placeholder={keySaved ? 'Saved key hidden' : ''} value={current.api_key} />}
          </Field>
          <div className="g-actions">
            {test ? <div role={test.ok ? 'status' : 'alert'}><StatusText tone={test.ok ? 'ok' : 'danger'}>{test.ok ? `Connected to ${name}${test.version ? ` ${test.version}` : ''}.` : test.error ?? 'Connection failed.'}</StatusText></div> : null}
            <Button busy={testing} disabled={!current.base_url.trim()} icon={<PlugZap />} onClick={() => { void runTest(); }}>{testing ? 'Testing…' : `Test ${name} connection`}</Button>
          </div>
          {select(`${name} root folder`, current.root_folder, (root_folder) => edit({ root_folder }), roots, 'Test the connection to choose')}
          {select(`${name} quality profile`, current.quality_profile_id, (quality_profile_id) => edit({ quality_profile_id }), profiles(current.quality_profile_id), 'Test the connection to choose')}
          {kind === 'sonarr' && showAdvanced ? (
            <>
              {select('Anime root folder', current.anime_root_folder, (anime_root_folder) => edit({ anime_root_folder }), animeRoots, 'Test the connection to choose')}
              {select('Anime quality profile', current.anime_quality_profile_id, (anime_quality_profile_id) => edit({ anime_quality_profile_id }), profiles(current.anime_quality_profile_id), 'Test the connection to choose')}
            </>
          ) : null}
        </div>
        {showAdvanced ? (
          <fieldset className="g-req-maps">
            <legend>Path mapping <span className="g-setting-tag">Advanced</span></legend>
            <p className="g-req-line">Where {name} saves files, and the same folder as Lumina sees it.</p>
            {current.path_mappings.map((row, index) => (
              <div className="g-req-map-row" key={index}>
                <Field hideLabel label={`${name} path ${index + 1}`}>{(ids) => <Input {...fieldProps(ids)} onChange={(event) => setMap(index, { remote: event.currentTarget.value })} placeholder={`${name} path`} value={row.remote} />}</Field>
                <ArrowRight aria-hidden="true" className="g-req-map-arrow" />
                <Field hideLabel label={`${name} path ${index + 1} in Lumina`}>{(ids) => <Input {...fieldProps(ids)} onChange={(event) => setMap(index, { local: event.currentTarget.value })} placeholder="Lumina path" value={row.local} />}</Field>
                <IconButton icon={<Trash2 />} label={`Remove ${name} path mapping ${index + 1}`} onClick={() => edit({ path_mappings: current.path_mappings.filter((_, at) => at !== index) })} />
              </div>
            ))}
            <div className="g-actions"><Button icon={<Plus />} onClick={() => edit({ path_mappings: [...current.path_mappings, { remote: '', local: '' }] })} variant="quiet">{`Add ${name} path mapping`}</Button></div>
          </fieldset>
        ) : null}
        {server ? (
          <div className="g-actions g-req-status">
            <p className="g-req-line">
              {server.last_error ? <StatusText tone="danger">Last error: {server.last_error}</StatusText> : server.last_ok_at ? <StatusText tone="ok">Last worked {formatDateTime(server.last_ok_at)}</StatusText> : 'Not used yet.'}
            </p>
            <Button icon={<Trash2 />} onClick={() => setConfirmDelete(true)} variant="quiet">{`Delete ${name}`}</Button>
          </div>
        ) : null}
      </SectionForm>
      <ConfirmDialog body="Requests already sent stay in Sonarr and Radarr. New requests will wait for a server to be set up again." confirmLabel="Delete" danger onCancel={() => setConfirmDelete(false)} onConfirm={() => { void remove(); }} open={confirmDelete} title={`Delete ${name}?`} />
    </div>
  );
}

export const SonarrCard = () => <ArrCard kind="sonarr" />;
export const RadarrCard = () => <ArrCard kind="radarr" />;

export function AnimeLanguage() {
  const form = useRequestsForm();
  const server = form.serverOf('sonarr');
  const test = form.tests.sonarr;
  const [draft, setDraft] = useState<{ dub: string; sub: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const [summary, setSummary] = useState('');
  if (!form.servers.data) return <p className="admin-note" role="status">Loading…</p>;
  if (!server) return <p className="admin-note">Save Sonarr first; the anime language profiles live there.</p>;
  const saved = { dub: server.dub_profile_id == null ? '' : String(server.dub_profile_id), sub: server.sub_profile_id == null ? '' : String(server.sub_profile_id) };
  const current = draft ?? saved;
  const dirty = current.dub !== saved.dub || current.sub !== saved.sub;
  const options = (id: string) => withCurrent((test?.quality_profiles ?? []).map((profile) => ({ value: String(profile.id), label: profile.name })), id, (value) => `Profile ${value}`);
  const nameOf = (id: number | null | undefined, list = test) => list?.quality_profiles.find((profile) => profile.id === id)?.name ?? (id == null ? 'none' : `Profile ${id}`);

  async function save() {
    setSaving(true);
    setStatus(null);
    try {
      form.putServer(await updateArrServer(server!.id, { dub_profile_id: num(current.dub), sub_profile_id: num(current.sub) }));
      setDraft(null);
      setStatus({ tone: 'ok', text: 'Anime language saved.' });
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, 'Unable to save the anime language profiles.') });
    } finally {
      setSaving(false);
    }
  }

  async function create() {
    setCreating(true);
    setStatus(null);
    setSummary('');
    try {
      const updated = await createAnimeLanguageProfiles(server!.id);
      form.putServer(updated);
      setDraft(null);
      const fresh = await testArrServer({ kind: 'sonarr', base_url: updated.base_url, id: updated.id }).catch(() => undefined);
      if (fresh) form.setTest('sonarr', fresh);
      setSummary(`Ready: English dub uses “${nameOf(updated.dub_profile_id, fresh)}” and Japanese with subtitles uses “${nameOf(updated.sub_profile_id, fresh)}”.`);
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, 'Unable to create the anime language profiles.') });
    } finally {
      setCreating(false);
    }
  }

  const pick = (label: string, key: 'dub' | 'sub') => (
    <Field label={label}>{(ids) => (
      <Select {...fieldProps(ids)} onChange={(event) => { setStatus(null); setDraft({ ...current, [key]: event.currentTarget.value }); }} value={current[key]}>
        <option value="">Use the anime quality profile</option>
        {options(current[key]).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </Select>
    )}</Field>
  );
  return (
    <div className="g-req">
      <SectionForm dirty={dirty} label="Anime language" onDiscard={() => { setDraft(null); setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save anime language" saving={saving} status={status}>
        <div className="g-subrows">
          {pick('English dub profile', 'dub')}
          {pick('Japanese with subtitles profile', 'sub')}
          <div className="g-actions">
            {summary ? <p className="g-req-line" role="status"><StatusText tone="ok">{summary}</StatusText></p> : null}
            <Button busy={creating} icon={<Sparkles />} onClick={() => { void create(); }}>{creating ? 'Creating…' : 'Create anime language profiles'}</Button>
          </div>
        </div>
      </SectionForm>
    </div>
  );
}
