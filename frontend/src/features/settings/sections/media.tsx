import { createContext, type ReactNode, useContext, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Copy, PlugZap, RefreshCw, Stethoscope } from 'lucide-react';

import { getMediaServerSettings, listUnmatchedTitles, refreshAllMetadata, runTranscodeDiagnostics, testTmdbKey, updateMediaServerSettings } from '../../../api';
import type { HwaccelMode, MediaServerSettings, MediaServerSettingsUpdate, TitleSummary, TranscodeDiagnostics } from '../../../types';
import { Button, Checkbox, Field, fieldProps, Input, PasswordInput, Select, StatusText } from '../../../ui';
import { errorMessage, formatBytes } from '../../../utils';
import { useAdminResource } from '../../admin/useAdminResource';
import { IdentifyDialog } from '../../titles/IdentifyDialog';
import { TMDB_ATTRIBUTION } from '../../titles/titleModel';
import { type FormStatus, SectionForm } from '../SectionForm';
import { SettingSwitch } from '../SettingRow';
import { useSettingsHost } from '../settingsHost';
import type { SettingsSectionDef } from '../settingsTypes';

export type MediaServerDraft = {
  tmdb_api_key: string; clear_tmdb_key: boolean; metadata_language: string; introdb_enabled: boolean; jellyfin_enabled: boolean;
  hwaccel: HwaccelMode; max_playback_sessions: string; transcode_cache_gb: string; jellyfin_import_url: string;
};
const HWACCEL: Array<[HwaccelMode, string]> = [['auto', 'Automatic'], ['qsv', 'Intel Quick Sync (QSV)'], ['vaapi', 'VA-API'], ['off', 'Off (software only)']];
const ACTIVE_LABELS: Record<TranscodeDiagnostics['active'], string> = { qsv: 'Intel Quick Sync', vaapi: 'VA-API', none: 'Software only' };

export const draftOf = (settings: MediaServerSettings): MediaServerDraft => ({
  tmdb_api_key: '', clear_tmdb_key: false, metadata_language: settings.metadata_language, introdb_enabled: settings.introdb_enabled, jellyfin_enabled: settings.jellyfin_enabled,
  hwaccel: settings.hwaccel, max_playback_sessions: String(settings.max_playback_sessions), transcode_cache_gb: String(settings.transcode_cache_gb),
  jellyfin_import_url: settings.jellyfin_import_url ?? '',
});

/** Only changed fields are sent; the TMDB key is write-only (typed to replace, ticked to remove). */
export function mediaServerChanges(settings: MediaServerSettings, draft: MediaServerDraft): MediaServerSettingsUpdate {
  const update: MediaServerSettingsUpdate = {};
  if (draft.clear_tmdb_key) update.tmdb_api_key = '';
  else if (draft.tmdb_api_key.trim()) update.tmdb_api_key = draft.tmdb_api_key.trim();
  if (draft.metadata_language.trim() !== settings.metadata_language) update.metadata_language = draft.metadata_language.trim();
  for (const key of ['introdb_enabled', 'jellyfin_enabled'] as const) if (draft[key] !== settings[key]) update[key] = draft[key];
  if (draft.hwaccel !== settings.hwaccel) update.hwaccel = draft.hwaccel;
  for (const key of ['max_playback_sessions', 'transcode_cache_gb'] as const) if (Number(draft[key]) !== settings[key]) update[key] = Number(draft[key]);
  if (draft.jellyfin_import_url.trim() !== (settings.jellyfin_import_url ?? '')) update.jellyfin_import_url = draft.jellyfin_import_url.trim();
  return update;
}

type MediaForm = { settings: MediaServerSettings; draft: MediaServerDraft; set: <K extends keyof MediaServerDraft>(key: K, value: MediaServerDraft[K]) => void };
const MediaFormContext = createContext<MediaForm | null>(null);

function useMediaForm(): MediaForm {
  const form = useContext(MediaFormContext);
  if (!form) throw new Error('Media server rows render inside their section form.');
  return form;
}

/**
 * One Save form over GET/PUT /api/admin/media-server. Media server and Playback & transcoding each
 * get their own; a form only edits its own fields, so mediaServerChanges sends only those.
 */
function mediaServerForm(label: string, saveLabel: string) {
  return function MediaServerForm({ children }: { children: ReactNode }) {
    const resource = useAdminResource(getMediaServerSettings, 'Unable to load media server settings.');
    const [draft, setDraft] = useState<MediaServerDraft | null>(null);
    const [saving, setSaving] = useState(false);
    const [status, setStatus] = useState<FormStatus>(null);
    useEffect(() => { if (resource.data) setDraft(draftOf(resource.data)); }, [resource.data]);
    const settings = resource.data;
    if (resource.error && !settings) return <p className="auth-error" role="alert">{resource.error}</p>;
    if (!settings || !draft) return <p className="admin-note" role="status">Loading media server settings…</p>;
    const update = mediaServerChanges(settings, draft);

    async function save() {
      setSaving(true);
      setStatus(null);
      try {
        const next = await updateMediaServerSettings(update);
        resource.setData(next);
        setDraft(draftOf(next)); // explicit: the response may equal the loaded object, which would not re-run the effect
        setStatus({ tone: 'ok', text: 'Saved.' });
      } catch (error) {
        setStatus({ tone: 'error', text: errorMessage(error, 'Unable to save media server settings.') });
      } finally {
        setSaving(false);
      }
    }
    const set: MediaForm['set'] = (key, value) => { setStatus(null); setDraft((current) => current && { ...current, [key]: value }); };

    return (
      <MediaFormContext.Provider value={{ settings, draft, set }}>
        <SectionForm dirty={Object.keys(update).length > 0} label={label} onDiscard={() => { setStatus(null); setDraft(draftOf(settings)); }} onSave={() => { void save(); }} saveLabel={saveLabel} saving={saving} status={status}>{children}</SectionForm>
      </MediaFormContext.Provider>
    );
  };
}

function TmdbKey() {
  const { settings, draft, set } = useMediaForm();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [testTone, setTestTone] = useState<'ok' | 'danger'>('ok');
  async function test() {
    setTesting(true);
    try {
      const outcome = await testTmdbKey();
      setTestTone(outcome.ok ? 'ok' : 'danger');
      setResult(outcome.ok ? 'The key works.' : outcome.error || 'TMDB did not accept the key.');
    } catch (error) {
      setTestTone('danger');
      setResult(errorMessage(error, 'The test could not run.'));
    } finally {
      setTesting(false);
    }
  }
  return (
    <div className="g-subrows">
      <Field hint={settings.has_tmdb_key ? 'A key is saved. Type a new one to replace it.' : undefined} label="TMDB API key">
        {(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="new-password" disabled={draft.clear_tmdb_key} maxLength={512} onChange={(event) => set('tmdb_api_key', event.target.value)} placeholder={settings.has_tmdb_key ? 'Saved key hidden' : 'None'} value={draft.tmdb_api_key} />}
      </Field>
      {settings.has_tmdb_key ? <Checkbox checked={draft.clear_tmdb_key} label="Remove the saved TMDB key" onChange={(event) => set('clear_tmdb_key', event.target.checked)} /> : null}
      <div className="g-actions">
        {result ? <div role="status"><StatusText tone={testTone}>{result}</StatusText></div> : null}
        <Button disabled={testing || !settings.has_tmdb_key} icon={<PlugZap />} onClick={() => { void test(); }}>{testing ? 'Testing…' : 'Test key'}</Button>
      </div>
      <p className="admin-note">{TMDB_ATTRIBUTION}</p>
    </div>
  );
}

function MetadataLanguage() {
  const { draft, set } = useMediaForm();
  return <Field hideLabel label="Details language">{(ids) => <Input {...fieldProps(ids)} maxLength={16} onChange={(event) => set('metadata_language', event.target.value)} pattern="[a-z]{2}(-[A-Z]{2})?" required spellCheck={false} value={draft.metadata_language} />}</Field>;
}

function IntroDb() {
  const { draft, set } = useMediaForm();
  return <SettingSwitch checked={draft.introdb_enabled} onChange={(next) => set('introdb_enabled', next)} />;
}

function RefreshAll() {
  const { settings } = useMediaForm();
  const { onMessage = () => undefined } = useSettingsHost();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      const result = await refreshAllMetadata();
      onMessage(`Refreshing details for ${result.queued} title${result.queued === 1 ? '' : 's'} in the background.`);
    } catch (failure) {
      setError(errorMessage(failure, 'Unable to start the refresh.'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Button disabled={busy || !settings.has_tmdb_key} icon={<RefreshCw />} onClick={() => { void refresh(); }}>Refresh all metadata</Button>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
    </>
  );
}

function Jellyfin() {
  const { draft, set } = useMediaForm();
  return <SettingSwitch checked={draft.jellyfin_enabled} onChange={(next) => set('jellyfin_enabled', next)} />;
}

function JellyfinAddress() {
  const { settings } = useMediaForm();
  const [copied, setCopied] = useState<string | null>(null);
  if (!settings.jellyfin_enabled) return <p className="admin-note">Turn on apps like Infuse and save to see the address.</p>;
  const address = settings.jellyfin_url || window.location.origin;
  async function copy() {
    try { await navigator.clipboard.writeText(address); setCopied('Address copied.'); } catch { setCopied('Copy is unavailable here. Select the address and copy it.'); }
  }
  return (
    <div className="g-subrows">
      <Field label="Server address for apps">{(ids) => <Input {...fieldProps(ids)} onFocus={(event) => event.currentTarget.select()} readOnly value={address} />}</Field>
      {settings.jellyfin_public_url && settings.jellyfin_public_url !== address
        ? <Field label="Away from home">{(ids) => <Input {...fieldProps(ids)} onFocus={(event) => event.currentTarget.select()} readOnly value={settings.jellyfin_public_url ?? ''} />}</Field>
        : null}
      <div className="g-actions">
        {copied ? <p className="admin-note" role="status">{copied}</p> : null}
        <Button icon={<Copy />} onClick={() => { void copy(); }}>Copy address</Button>
      </div>
      <ol aria-label="Connect Infuse" className="admin-steps">
        <li>In Infuse, choose Add Files, then Jellyfin.</li>
        <li>Enter the address above.</li>
        <li>Sign in with your Lumina username and password.</li>
        <li>Turn on Library Mode to browse Movies and Shows with artwork.</li>
      </ol>
    </div>
  );
}

function ImportServer() {
  const { draft, set } = useMediaForm();
  return <Field hideLabel label="Jellyfin server address">{(ids) => <Input {...fieldProps(ids)} inputMode="url" maxLength={2048} onChange={(event) => set('jellyfin_import_url', event.target.value)} placeholder="http://192.168.1.20:8096" spellCheck={false} type="url" value={draft.jellyfin_import_url} />}</Field>;
}

function Unmatched() {
  const unmatched = useAdminResource(listUnmatchedTitles, 'Unable to load titles without a match.');
  const { onMessage = () => undefined } = useSettingsHost();
  const [identifying, setIdentifying] = useState<TitleSummary | null>(null);
  return (
    <>
      {unmatched.error ? <p className="admin-note">{unmatched.error}</p> : !unmatched.data ? <p className="admin-note" role="status">Loading…</p> : unmatched.data.length ? (
        <ul className="admin-counts">
          {unmatched.data.map((title) => {
            const label = `${title.name}${title.year ? ` (${title.year})` : ''}`;
            return <li key={title.id}><span>{label}</span><Button aria-label={`Identify ${label}`} onClick={() => setIdentifying(title)}>Identify</Button></li>;
          })}
        </ul>
      ) : <p className="admin-note">Every movie and show has a match.</p>}
      {/* Portal: the dialog holds its own search form, which must not nest inside the section form. */}
      {identifying ? createPortal(<IdentifyDialog onChanged={(message) => { onMessage(message); unmatched.reload(); }} onClose={() => setIdentifying(null)} title={identifying} />, document.body) : null}
    </>
  );
}

function Hwaccel() {
  const { draft, set } = useMediaForm();
  return <Field hideLabel label="Hardware acceleration">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => set('hwaccel', event.target.value as HwaccelMode)} value={draft.hwaccel}>{HWACCEL.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select>}</Field>;
}

function MaxSessions() {
  const { draft, set } = useMediaForm();
  return <Field hideLabel label="Simultaneous video conversions">{(ids) => <Input {...fieldProps(ids)} inputMode="numeric" max={16} min={1} onChange={(event) => set('max_playback_sessions', event.target.value)} required type="number" value={draft.max_playback_sessions} />}</Field>;
}

function Cache() {
  const { draft, set } = useMediaForm();
  return <Field hideLabel label="Conversion cache (GB)">{(ids) => <Input {...fieldProps(ids)} inputMode="numeric" max={1000} min={1} onChange={(event) => set('transcode_cache_gb', event.target.value)} required type="number" value={draft.transcode_cache_gb} />}</Field>;
}

function HardwareStatus() {
  const [busy, setBusy] = useState(false);
  const [diagnostics, setDiagnostics] = useState<TranscodeDiagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setBusy(true);
    setError(null);
    try { setDiagnostics(await runTranscodeDiagnostics()); } catch (failure) { setError(errorMessage(failure, 'Diagnostics could not run.')); } finally { setBusy(false); }
  }
  return (
    <div className="g-subrows">
      <div className="g-actions">
        {error ? <p className="auth-error" role="alert">{error}</p> : null}
        <Button disabled={busy} icon={<Stethoscope />} onClick={() => { void run(); }}>{busy ? 'Checking…' : 'Run diagnostics'}</Button>
      </div>
      {diagnostics ? (
        <ul aria-label="Diagnostics" className="admin-counts">
          <li><span>Hardware in use</span><strong><StatusText tone={diagnostics.hardware_disabled ? 'attention' : diagnostics.active === 'none' ? 'muted' : 'ok'}>{ACTIVE_LABELS[diagnostics.active]}{diagnostics.hardware_disabled ? ' (paused after repeated failures)' : ''}</StatusText></strong></li>
          <li><span>Hardware check</span><strong><StatusText tone={diagnostics.probe_ok ? 'ok' : 'danger'}>{diagnostics.probe_ok ? 'Passed' : diagnostics.probe_error || 'Failed'}</StatusText></strong></li>
          <li><span>HDR to SDR</span><strong><StatusText tone={diagnostics.tonemap === 'none' ? 'attention' : 'ok'}>{diagnostics.tonemap === 'none' ? 'Unavailable' : diagnostics.tonemap}</StatusText></strong></li>
          <li><span>Conversions now</span><strong>{Object.values(diagnostics.sessions).reduce((sum, count) => sum + count, 0)}</strong></li>
          <li><span>Software fallbacks</span><strong>{diagnostics.software_fallbacks}</strong></li>
          <li><span>Cache</span><strong>{formatBytes(diagnostics.cache_bytes)} of {formatBytes(diagnostics.cache_cap_bytes)}</strong></li>
          {diagnostics.recent_errors.slice(0, 5).map((entry, index) => <li key={index}><span>Recent error</span><strong>{entry}</strong></li>)}
        </ul>
      ) : null}
    </div>
  );
}

/** Media server and Playback & transcoding: two Save forms over one settings endpoint. */
export const MEDIA_SECTIONS: readonly SettingsSectionDef[] = [
  {
    id: 'media', group: 'server', label: 'Media server', aliases: ['metadata', 'infuse'], summary: 'Movie and show details, and apps like Infuse.',
    Provider: mediaServerForm('Media server', 'Save media server settings'),
    entries: [
      { id: 'media.tmdb-key', label: 'TMDB key', layout: 'block', keywords: ['tmdb api key', 'the movie database', 'metadata', 'artwork', 'test key', 'remove the saved tmdb key'], info: 'Lets Lumina fill in missing movie and show details and artwork from TMDB; a key is free from themoviedb.org. Once saved, the key is never shown again, and NFO files or edits made here are never overwritten. Affects everyone on this server.', Control: TmdbKey },
      { id: 'media.language', label: 'Metadata language', advanced: true, keywords: ['details language', 'language', 'locale', 'en-us', 'de-de'], info: 'The language Lumina asks TMDB to use for titles, overviews and artwork, as a tag such as en-US or de-DE. It applies to details fetched after you save. Affects everyone on this server.', Control: MetadataLanguage },
      { id: 'media.introdb', label: 'Intro and credits times', layout: 'switch', advanced: true, description: 'Looked up on TheIntroDB.', keywords: ['theintrodb', 'skip intro', 'segments', 'privacy', 'lookup'], info: 'Looks up where intros and credits start on TheIntroDB, so Skip buttons appear on more episodes. It is off by default because each lookup tells TheIntroDB which episodes this household owns. Affects everyone on this server.', Control: IntroDb },
      { id: 'media.refresh-all', label: 'Refresh all details', keywords: ['refresh all metadata', 'metadata', 'update artwork', 'rescan details'], info: 'Fetches details and artwork again for every movie and show, in the background. It needs a saved TMDB key and never overwrites NFO files or your edits. Affects everyone on this server.', Control: RefreshAll },
      { id: 'media.jellyfin', label: 'Let apps like Infuse connect', layout: 'switch', keywords: ['jellyfin', 'infuse', 'swiftfin', 'apple tv', 'clients', 'let apps connect'], info: 'Lets Infuse, Swiftfin and other Jellyfin apps browse and play this vault. Each member signs in with their own Lumina account and sees only what they can see here. Affects everyone on this server.', Control: Jellyfin },
      { id: 'media.jellyfin-address', label: 'Address for apps', layout: 'block', keywords: ['server address for apps', 'url', 'host', 'port', 'connect infuse', 'copy address', 'steps'], info: 'The address to type into Infuse or another Jellyfin app: the same address you open Lumina at, nothing added. It appears once apps like Infuse are allowed and saved. There is nothing to change here, and it is the same for everyone.', Control: JellyfinAddress },
      { id: 'media.jellyfin-import', label: 'Jellyfin server to import from', advanced: true, keywords: ['jellyfin server address', 'import watch history', 'move from jellyfin', 'migrate', 'watched', 'favorites'], info: "The address of the household's old Jellyfin server, such as http://192.168.1.20:8096, so members can bring their watch history and favorites over. Each member signs in to it with their own Jellyfin account under Privacy & data, and Lumina never stores that password. Leave it empty to turn importing off; it affects everyone on this server.", Control: ImportServer },
      { id: 'media.unmatched', label: 'Titles without a match', layout: 'block', keywords: ['identify', 'fix match', 'unmatched', 'wrong movie', 'tmdb match'], info: 'Movies and shows Lumina could not match to TMDB with confidence. Identify lets you pick the right one, and later scans never overwrite your choice. Affects everyone on this server.', Control: Unmatched },
    ],
  },
  {
    id: 'transcoding', group: 'server', label: 'Transcoding', aliases: ['playback & transcoding'], summary: 'How Lumina converts video that a device cannot play as it is. The originals never change.',
    Provider: mediaServerForm('Transcoding', 'Save playback and transcoding settings'),
    entries: [
      { id: 'transcoding.hwaccel', label: 'Hardware acceleration', advanced: true, keywords: ['gpu', 'quick sync', 'qsv', 'va-api', 'vaapi', 'intel', 'software', 'encode', 'transcode'], info: "Uses the server's graphics chip to convert video that a device cannot play as it is. Automatic uses an Intel GPU when one works and falls back to software, which is slower and uses more CPU. Affects playback for everyone on this server.", Control: Hwaccel },
      { id: 'transcoding.max-sessions', label: 'Simultaneous conversions', advanced: true, keywords: ['simultaneous video conversions', 'transcodes', 'sessions', 'limit', 'concurrent'], info: 'How many videos Lumina may convert for playback at once, from 1 to 16. Each one uses a lot of CPU or GPU; remuxes and audio-only conversions have their own separate limit of 16. Affects everyone on this server.', Control: MaxSessions },
      { id: 'transcoding.cache', label: 'Conversion cache', advanced: true, keywords: ['conversion cache (gb)', 'cache size', 'gb', 'disk', 'transcode cache'], info: 'Disk space, in GB, for video Lumina has already converted, so playing it again starts at once. The oldest converted files are removed first when it fills. Affects everyone on this server.', Control: Cache },
      { id: 'transcoding.diagnostics', label: 'Hardware status', layout: 'block', advanced: true, keywords: ['run diagnostics', 'diagnostics', 'gpu check', 'hdr', 'tone mapping', 'software fallback', 'errors'], info: 'Checks whether hardware conversion works on this server and shows what is in use, HDR support, the cache and recent errors. Running it changes no setting. Only vault owners see this.', Control: HardwareStatus },
    ],
  },
];
