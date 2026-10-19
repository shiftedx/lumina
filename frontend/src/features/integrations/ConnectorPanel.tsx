import { ExternalLink, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { errorMessage, formatDateTime } from '../../utils';
import { Button, ConfirmDialog, EmptyState, ErrorState, Field, fieldProps, IconButton, Input, PasswordInput, Select, StatusText } from '../../ui';
import {
  type ConnectorArrClient, type ConnectorDataset, type ConnectorInput, type ConnectorKind, type ConnectorPathMapping, type ConnectorSettings, type ServiceConnector,
  createConnector, disableConnector, getConnectorDetails, getConnectors, refreshConnector, updateConnector,
} from './integrationsApi';

const KINDS: Array<{ value: ConnectorKind; label: string }> = [
  { value: 'lidarr', label: 'Lidarr' }, { value: 'prowlarr', label: 'Prowlarr' }, { value: 'qbittorrent', label: 'qBittorrent' }, { value: 'nzbget', label: 'NZBGet' },
];
const DATASETS: Record<ConnectorKind, Array<{ value: ConnectorDataset; label: string }>> = {
  lidarr: [{ value: 'artists', label: 'Artists' }, { value: 'albums', label: 'Albums' }, { value: 'queue', label: 'Queue' }, { value: 'history', label: 'History' }, { value: 'root_folders', label: 'Root folders' }, { value: 'quality_profiles', label: 'Quality profiles' }, { value: 'metadata_profiles', label: 'Metadata profiles' }],
  prowlarr: [{ value: 'health', label: 'Health' }, { value: 'indexers', label: 'Indexers' }, { value: 'indexer_status', label: 'Indexer status' }, { value: 'history', label: 'History' }, { value: 'stats', label: 'Statistics' }],
  qbittorrent: [{ value: 'transfers', label: 'Transfers' }],
  nzbget: [{ value: 'transfers', label: 'Transfers' }, { value: 'health', label: 'Health' }],
};

const COLLECTED_INFORMATION: Record<ConnectorKind, string> = {
  lidarr: 'Artist and album inventory, download activity, history, music folders, quality and metadata profiles.',
  prowlarr: 'Configured indexers, indexer availability, health notices, history and statistics.',
  qbittorrent: 'Transfer states, progress, sizes, remaining data and download speeds.',
  nzbget: 'Transfer states, progress, sizes, remaining data and service health.',
};

type Draft = { kind: ConnectorKind; name: string; base_url: string; api_key: string; username: string; password: string; settings: ConnectorSettings };
type Profiles = { roots: Array<{ path: string }>; quality: Array<{ id: number; name: string }>; metadata: Array<{ id: number; name: string }> };

const emptySettings = (): ConnectorSettings => ({ path_mappings: [], arr_clients: [] });
const blank = (): Draft => ({ kind: 'lidarr', name: 'Lidarr', base_url: '', api_key: '', username: '', password: '', settings: emptySettings() });
const copySettings = (settings: ConnectorSettings): ConnectorSettings => ({
  root_folder: settings.root_folder ?? null, quality_profile_id: settings.quality_profile_id ?? null, metadata_profile_id: settings.metadata_profile_id ?? null,
  path_mappings: (settings.path_mappings ?? []).map((row) => ({ ...row })), arr_clients: (settings.arr_clients ?? []).map((row) => ({ ...row })),
});
const draftOf = (connector: ServiceConnector): Draft => ({ kind: connector.kind, name: connector.name, base_url: connector.base_url, api_key: '', username: '', password: '', settings: copySettings(connector.settings) });
const titleOf = (kind: ConnectorKind) => KINDS.find((entry) => entry.value === kind)?.label ?? kind;
const credentialKind = (kind: ConnectorKind) => kind === 'lidarr' || kind === 'prowlarr' ? 'key' : 'password';
const datasetFor = (kind: ConnectorKind) => DATASETS[kind][0].value;
const numberOrNull = (value: string) => value ? Number(value) : null;

function connectorPayload(draft: Draft): ConnectorInput {
  const body: ConnectorInput = { kind: draft.kind, name: draft.name.trim(), base_url: draft.base_url.trim(), settings: {
    root_folder: draft.settings.root_folder || null, quality_profile_id: draft.settings.quality_profile_id ?? null, metadata_profile_id: draft.settings.metadata_profile_id ?? null,
    path_mappings: draft.settings.path_mappings.map((row) => ({ remote: row.remote.trim(), local: row.local.trim() })).filter((row) => row.remote && row.local),
    arr_clients: draft.settings.arr_clients.map((row) => ({ server_id: row.server_id.trim(), client_name: row.client_name.trim() })).filter((row) => row.server_id && row.client_name),
  } };
  if (draft.api_key) body.api_key = draft.api_key;
  if (draft.username) body.username = draft.username;
  if (draft.password) body.password = draft.password;
  return body;
}

function State({ connector }: { connector: ServiceConnector }) {
  return <div className="arr-freshness">
    <p>{connector.last_ok_at ? <>Saved snapshot from <time dateTime={connector.last_ok_at}>{formatDateTime(connector.last_ok_at)}</time>.{connector.stale ? ' Test this connection to check for updates.' : ''}</> : 'No completed collection yet. Test this connection to collect its information.'}</p>
    {connector.last_error ? <StatusText tone="danger">Last collection failed: {connector.last_error}</StatusText> : null}
  </div>;
}

function recordText(value: unknown) { return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : null; }
const detailKeys = ['id', 'name', 'path', 'state', 'source', 'type', 'protocol', 'enabled', 'initial_failure', 'disabled', 'event_type', 'indexer_id', 'foreign_artist_id', 'foreign_album_id', 'artist_id', 'album_id', 'title', 'monitored', 'progress', 'size', 'amount_left', 'dlspeed', 'upspeed', 'eta', 'correlation', 'server_id', 'upstream_id'];
function DetailRows({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows.length) return <EmptyState body="Run Test to collect a current snapshot." title="No saved details on this page" />;
  return <ul className="connector-records">{rows.map((row, index) => {
    const values = detailKeys.flatMap((key) => { const value = recordText(row[key]); return value == null ? [] : [[key, value] as const]; });
    return <li key={`${String(row.id ?? index)}:${index}`}>{values.length ? values.map(([key, value]) => <span key={key}><b>{key.replaceAll('_', ' ')}</b> {value}</span>) : <span>Saved observation</span>}</li>;
  })}</ul>;
}

function Pager({ page, total, pageSize, onPage }: { page: number; total: number; pageSize: number; onPage: (value: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return <p className="arr-page-count">{total.toLocaleString()} item{total === 1 ? '' : 's'}</p>;
  return <nav aria-label="Connector page" className="arr-pager"><span>Page {page.toLocaleString()} of {pages.toLocaleString()} · {total.toLocaleString()} items</span><Button disabled={page <= 1} onClick={() => onPage(page - 1)} variant="quiet">Previous</Button><Button disabled={page >= pages} onClick={() => onPage(page + 1)} variant="quiet">Next</Button></nav>;
}

export function ConnectorPanel() {
  const [connectors, setConnectors] = useState<ServiceConnector[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmDisable, setConfirmDisable] = useState(false);
  const [dataset, setDataset] = useState<ConnectorDataset>('root_folders');
  const [detailPage, setDetailPage] = useState(1);
  const [details, setDetails] = useState<Awaited<ReturnType<typeof getConnectorDetails>> | null>(null);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<Profiles>({ roots: [], quality: [], metadata: [] });

  const load = useCallback(async () => {
    setError(null);
    try {
      const listed = await getConnectors();
      const next = Array.isArray(listed.items) ? listed.items : [];
      setConnectors(next);
      setSelectedId((current) => current && next.some((item) => item.id === current) ? current : next[0]?.id ?? null);
    } catch (cause) { setError(errorMessage(cause, 'Unable to load infrastructure connectors.')); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const selected = connectors?.find((item) => item.id === selectedId) ?? null;
  const current = draft ?? (selected ? draftOf(selected) : blank());
  const update = (patch: Partial<Draft>) => { setNotice(null); setDraft({ ...current, ...patch }); };
  const connectorChanged = selected ? JSON.stringify(connectorPayload(current)) !== JSON.stringify(connectorPayload(draftOf(selected))) : Boolean(draft);

  useEffect(() => {
    if (!selected) { setDetails(null); setProfiles({ roots: [], quality: [], metadata: [] }); return; }
    const datasets = DATASETS[selected.kind];
    if (!datasets.some((entry) => entry.value === dataset)) { setDataset(datasetFor(selected.kind)); setDetailPage(1); }
  }, [dataset, selected]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    setDetails(null); setDetailsError(null);
    getConnectorDetails(selected.id, dataset, { page: detailPage }).then((value) => { if (active) setDetails(value); }).catch((cause) => { if (active) setDetailsError(errorMessage(cause, 'Unable to load saved connector details.')); });
    return () => { active = false; };
  }, [selected?.id, dataset, detailPage, selected?.enabled]);
  useEffect(() => {
    if (!selected || selected.kind !== 'lidarr' || !selected.enabled) { setProfiles({ roots: [], quality: [], metadata: [] }); return; }
    let active = true;
    Promise.all([
      getConnectorDetails(selected.id, 'root_folders'), getConnectorDetails(selected.id, 'quality_profiles'), getConnectorDetails(selected.id, 'metadata_profiles'),
    ]).then(([roots, quality, metadata]) => {
      if (!active) return;
      setProfiles({
        roots: roots.items.flatMap((row) => typeof row.path === 'string' ? [{ path: row.path }] : []),
        quality: quality.items.flatMap((row) => typeof row.id === 'number' && typeof row.name === 'string' ? [{ id: row.id, name: row.name }] : []),
        metadata: metadata.items.flatMap((row) => typeof row.id === 'number' && typeof row.name === 'string' ? [{ id: row.id, name: row.name }] : []),
      });
    }).catch(() => { if (active) setProfiles({ roots: [], quality: [], metadata: [] }); });
    return () => { active = false; };
  }, [selected?.id, selected?.kind, selected?.enabled]);
  useEffect(() => {
    if (details && detailPage > Math.max(1, Math.ceil(details.total / details.page_size))) setDetailPage(Math.max(1, Math.ceil(details.total / details.page_size)));
  }, [details, detailPage]);

  function choose(id: string | null) { setSelectedId(id); setDraft(id ? null : blank()); setNotice(null); setDetailPage(1); }
  async function save() {
    if (!current.name.trim() || !current.base_url.trim()) { setError('Enter a name and service address.'); return; }
    if (!selected && credentialKind(current.kind) === 'key' && !current.api_key) { setError(`Enter the ${titleOf(current.kind)} API key.`); return; }
    if (!selected && credentialKind(current.kind) === 'password' && (!current.username || !current.password)) { setError(`Enter the ${titleOf(current.kind)} username and password.`); return; }
    setSaving(true); setError(null); setNotice(null);
    try {
      const saved = selected ? await updateConnector(selected.id, connectorPayload(current)) : await createConnector(connectorPayload(current));
      setConnectors((rows) => selected ? (rows ?? []).map((row) => row.id === saved.id ? saved : row) : [...(rows ?? []), saved].sort((a, b) => a.name.localeCompare(b.name)));
      setSelectedId(saved.id); setDraft(null); setNotice('Connector saved. Test this connection to qualify it.');
    } catch (cause) { setError(errorMessage(cause, `Unable to save ${titleOf(current.kind)}.`)); }
    finally { setSaving(false); }
  }
  async function test() {
    if (!selected) return;
    setRefreshing(true); setError(null); setNotice(null);
    try {
      const refreshed = await refreshConnector(selected.id);
      setConnectors((rows) => (rows ?? []).map((row) => row.id === refreshed.id ? refreshed : row));
      setNotice(refreshed.last_error ? 'The test did not qualify this connection. Review the error before changing it.' : 'Connection tested. Saved observations are available below.');
    } catch (cause) { setError(errorMessage(cause, 'The test did not finish. Its outcome is unknown; review the saved connection before trying again.')); }
    finally { setRefreshing(false); }
  }
  async function removeCredentials() {
    if (!selected) return;
    setConfirmDisable(false); setSaving(true); setError(null);
    try {
      const disabled = await disableConnector(selected.id);
      setConnectors((rows) => (rows ?? []).map((row) => row.id === disabled.id ? disabled : row));
      setDraft(null); setNotice('Connector disabled and credentials removed. Its audit identity remains available.');
    } catch (cause) { setError(errorMessage(cause, 'Unable to remove connector credentials.')); }
    finally { setSaving(false); }
  }
  const settings = current.settings;
  const editSettings = (patch: Partial<ConnectorSettings>) => update({ settings: { ...settings, ...patch } });
  const profileOptions = useMemo(() => ({ roots: profiles.roots, quality: profiles.quality, metadata: profiles.metadata }), [profiles]);
  const savedCredential = current.kind === 'lidarr' || current.kind === 'prowlarr' ? selected?.api_key_set : Boolean(selected?.username_set && selected?.password_set);

  return <section aria-labelledby="connector-heading" className="connector-panel">
    <header><div><h3 id="connector-heading">Infrastructure connectors</h3><p>These admin-only connections observe supporting services. Saving changes disables observation until you explicitly test the connection.</p></div><Button onClick={() => choose(null)} variant="quiet">Add connector</Button></header>
    {error ? <ErrorState body={error} onRetry={() => { void load(); }} title="Lumina could not complete this connector action" /> : null}
    {!connectors ? <p className="admin-note" role="status">Loading infrastructure connectors…</p> : <>
      {connectors.length ? <ul aria-label="Infrastructure connectors" className="connector-list">{connectors.map((connector) => <li className={connector.id === selected?.id ? 'is-selected' : ''} key={connector.id}><button aria-pressed={connector.id === selected?.id} onClick={() => choose(connector.id)} type="button"><strong>{connector.name}</strong><span>{titleOf(connector.kind)}{connector.version ? ` ${connector.version}` : ''}</span></button><State connector={connector} /></li>)}</ul> : !draft ? <EmptyState body="Add a connector to save an endpoint and test it from this page." title="No infrastructure connectors" /> : null}
      {(selected || draft) ? <div className="connector-editor">
        <h4>{selected ? `Manage ${selected.name}` : 'Add connector'}</h4>
        <div className="connector-fields">
          <Field label="Service">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => update({ kind: event.currentTarget.value as ConnectorKind, name: selected ? current.name : titleOf(event.currentTarget.value as ConnectorKind), settings: emptySettings() })} value={current.kind}>{KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}</Select>}</Field>
          <Field label="Name">{(ids) => <Input {...fieldProps(ids)} onChange={(event) => update({ name: event.currentTarget.value })} value={current.name} />}</Field>
          <Field label={`${titleOf(current.kind)} address`} hint="Use the service address available to the Lumina server.">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" inputMode="url" onChange={(event) => update({ base_url: event.currentTarget.value })} placeholder="http://service.local" value={current.base_url} />}</Field>
          {credentialKind(current.kind) === 'key' ? <Field label={`${titleOf(current.kind)} API key`} hint={savedCredential ? 'A key is saved. Leave this blank to keep it.' : 'Copied credentials are stored write-only.'}>{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="new-password" onChange={(event) => update({ api_key: event.currentTarget.value })} placeholder={savedCredential ? 'Saved key hidden' : ''} value={current.api_key} />}</Field> : <><Field label={`${titleOf(current.kind)} username`} hint={savedCredential ? 'A username is saved. Leave this blank to keep it.' : undefined}>{(ids) => <Input {...fieldProps(ids)} autoComplete="off" onChange={(event) => update({ username: event.currentTarget.value })} value={current.username} />}</Field><Field label={`${titleOf(current.kind)} password`} hint={savedCredential ? 'A password is saved. Leave this blank to keep it.' : undefined}>{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="new-password" onChange={(event) => update({ password: event.currentTarget.value })} placeholder={savedCredential ? 'Saved password hidden' : ''} value={current.password} />}</Field></>}
        </div>
        {current.kind === 'lidarr' ? <LidarrSettings draft={current} profiles={profileOptions} onSettings={editSettings} /> : null}
        {current.kind === 'qbittorrent' || current.kind === 'nzbget' ? <ArrBindings rows={settings.arr_clients} onChange={(arr_clients) => editSettings({ arr_clients })} /> : null}
        {current.kind === 'prowlarr' ? <p className="connector-note">Prowlarr is observed read-only. Lumina does not send indexer or transfer commands.</p> : null}
        <div className="g-actions connector-actions"><Button busy={saving} disabled={selected ? !connectorChanged : false} onClick={() => { void save(); }}>{saving ? 'Saving…' : selected ? 'Save connection' : 'Save connector'}</Button>{selected ? <><Button busy={refreshing} icon={<RefreshCw />} onClick={() => { void test(); }} variant="quiet">Test connection</Button><a className="g-button is-quiet" href={selected.base_url} rel="noreferrer" target="_blank"><ExternalLink aria-hidden="true" /> Open {titleOf(selected.kind)}</a></> : null}</div>
        {notice ? <p className="connector-note" role="status">{notice}</p> : null}
        {selected ? <><div className="connector-status"><State connector={selected} /><Button icon={<Trash2 />} onClick={() => setConfirmDisable(true)} variant="quiet">Remove credentials</Button></div><ConnectorDetails connector={selected} dataset={dataset} details={details} error={detailsError} onDataset={(next) => { setDataset(next); setDetailPage(1); }} onPage={setDetailPage} /></> : null}
      </div> : null}
    </>}
    <ConfirmDialog body="This disables the connector and removes Lumina’s stored credentials. Audit identities remain, and it does not change the upstream service." confirmLabel="Remove credentials" danger busy={saving} onCancel={() => setConfirmDisable(false)} onConfirm={() => { void removeCredentials(); }} open={confirmDisable} title="Remove connector credentials?" />
  </section>;
}

function LidarrSettings({ draft, profiles, onSettings }: { draft: Draft; profiles: Profiles; onSettings: (patch: Partial<ConnectorSettings>) => void }) {
  const settings = draft.settings;
  const map = (at: number, patch: Partial<ConnectorPathMapping>) => onSettings({ path_mappings: settings.path_mappings.map((row, index) => index === at ? { ...row, ...patch } : row) });
  return <fieldset className="connector-settings"><legend>Lidarr defaults</legend><p className="connector-note">Test the connection to load its current roots and profiles. These values are used only for approved music requests.</p>
    <div className="connector-fields"><Field label="Root folder">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => onSettings({ root_folder: event.currentTarget.value || null })} value={settings.root_folder ?? ''}><option value="">Choose after testing</option>{profiles.roots.map((entry) => <option key={entry.path} value={entry.path}>{entry.path}</option>)}</Select>}</Field><Field label="Quality profile">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => onSettings({ quality_profile_id: numberOrNull(event.currentTarget.value) })} value={settings.quality_profile_id?.toString() ?? ''}><option value="">Choose after testing</option>{profiles.quality.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}</Select>}</Field><Field label="Metadata profile">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => onSettings({ metadata_profile_id: numberOrNull(event.currentTarget.value) })} value={settings.metadata_profile_id?.toString() ?? ''}><option value="">Choose after testing</option>{profiles.metadata.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}</Select>}</Field></div>
    <div className="connector-mappings"><h5>Path mappings</h5>{settings.path_mappings.map((row, index) => <div className="connector-map-row" key={index}><Field hideLabel label={`Lidarr path mapping ${index + 1} remote path`}>{(ids) => <Input {...fieldProps(ids)} onChange={(event) => map(index, { remote: event.currentTarget.value })} placeholder="Lidarr path" value={row.remote} />}</Field><Field hideLabel label={`Lidarr path mapping ${index + 1} Lumina path`}>{(ids) => <Input {...fieldProps(ids)} onChange={(event) => map(index, { local: event.currentTarget.value })} placeholder="Lumina path" value={row.local} />}</Field><IconButton icon={<Trash2 />} label={`Remove Lidarr path mapping ${index + 1}`} onClick={() => onSettings({ path_mappings: settings.path_mappings.filter((_, at) => at !== index) })} /></div>)}<Button icon={<Plus />} onClick={() => onSettings({ path_mappings: [...settings.path_mappings, { remote: '', local: '' }] })} variant="quiet">Add path mapping</Button></div>
  </fieldset>;
}

function ArrBindings({ rows, onChange }: { rows: ConnectorArrClient[]; onChange: (rows: ConnectorArrClient[]) => void }) {
  const edit = (index: number, patch: Partial<ConnectorArrClient>) => onChange(rows.map((row, at) => at === index ? { ...row, ...patch } : row));
  return <fieldset className="connector-settings"><legend>ARR download-client bindings</legend><p className="connector-note">Enter each manager server ID and its exact download-client name. Lumina only correlates transfers when both values match.</p>{rows.map((row, index) => <div className="connector-map-row" key={index}><Field hideLabel label={`ARR binding ${index + 1} server ID`}>{(ids) => <Input {...fieldProps(ids)} onChange={(event) => edit(index, { server_id: event.currentTarget.value })} placeholder="Server ID" value={row.server_id} />}</Field><Field hideLabel label={`ARR binding ${index + 1} client name`}>{(ids) => <Input {...fieldProps(ids)} onChange={(event) => edit(index, { client_name: event.currentTarget.value })} placeholder="Exact client name" value={row.client_name} />}</Field><IconButton icon={<Trash2 />} label={`Remove ARR binding ${index + 1}`} onClick={() => onChange(rows.filter((_, at) => at !== index))} /></div>)}<Button icon={<Plus />} onClick={() => onChange([...rows, { server_id: '', client_name: '' }])} variant="quiet">Add ARR binding</Button></fieldset>;
}

function ConnectorDetails({ connector, dataset, details, error, onDataset, onPage }: { connector: ServiceConnector; dataset: ConnectorDataset; details: Awaited<ReturnType<typeof getConnectorDetails>> | null; error: string | null; onDataset: (value: ConnectorDataset) => void; onPage: (value: number) => void }) {
  const entries = DATASETS[connector.kind];
  return <section aria-labelledby="connector-details-heading" className="connector-details"><h5 id="connector-details-heading">Collected information</h5><p className="connector-note">{COLLECTED_INFORMATION[connector.kind]}</p>{connector.capabilities.control === false ? <p className="connector-note">Read-only connection. Lumina observes activity without controlling this service.</p> : connector.capabilities.approved_music_requests ? <p className="connector-note">Album requests are sent only after approval.</p> : null}<div aria-label="Connector dataset" className="arr-tabs">{entries.map((entry) => <button aria-pressed={dataset === entry.value} key={entry.value} onClick={() => onDataset(entry.value)} type="button">{entry.label}</button>)}</div>{error ? <p className="connector-note" role="alert">{error}</p> : !details ? <p className="admin-note" role="status">Loading saved observations…</p> : <><DetailRows rows={details.items} /><Pager page={details.page} pageSize={details.page_size} total={details.total} onPage={onPage} /></>}</section>;
}
