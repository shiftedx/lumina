import { RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { errorMessage, formatDateTime } from '../../utils';
import { Button, EmptyState, ErrorState, StatusText } from '../../ui';
import { type ArrDataset, type ArrDatasetPage, type ArrImportState, type ArrObservedItem, type ArrServerObservation, getArrDataset, getArrObservations, refreshArrServer } from './integrationsApi';
import { ManageIntegrationItem, OperationAudit } from './ManageIntegrationItem';
import { ConnectorPanel } from './ConnectorPanel';
import { MusicRequestOperations } from './MusicRequestControls';
import './integrations.css';

const DATASETS: Array<{ id: ArrDataset; label: string }> = [
  { id: 'queue', label: 'Queue' }, { id: 'history', label: 'History' }, { id: 'health', label: 'Health' }, { id: 'calendar', label: 'Calendar' }, { id: 'missing', label: 'Missing' }, { id: 'cutoff', label: 'Cutoff' },
];
const importCopy: Record<ArrImportState, string> = {
  unmapped: 'Needs a matching Lumina title',
  root_unavailable: 'The mapped library root is unavailable',
  awaiting_first_import: 'Waiting for Lumina’s first import',
  pending_import: 'Waiting for import',
  registered: 'Registered in Lumina',
};

function Pager({ page, totalPages, total, onPage }: { page: number; totalPages: number; total: number; onPage: (next: number) => void }) {
  if (totalPages <= 1) return <p className="arr-page-count">{total.toLocaleString()} item{total === 1 ? '' : 's'}</p>;
  return <nav aria-label="Integration page" className="arr-pager"><span>Page {page.toLocaleString()} of {totalPages.toLocaleString()} · {total.toLocaleString()} items</span><Button disabled={page <= 1} onClick={() => onPage(page - 1)} variant="quiet">Previous</Button><Button disabled={page >= totalPages} onClick={() => onPage(page + 1)} variant="quiet">Next</Button></nav>;
}

function ServerState({ server }: { server: ArrServerObservation }) {
  const status = server.last_error ? 'danger' : server.stale ? 'attention' : server.last_ok_at ? 'ok' : 'muted';
  const message = server.last_error ? `Last refresh failed: ${server.last_error}` : server.stale ? `Last successful observation is out of date${server.last_ok_at ? ` (${formatDateTime(server.last_ok_at)})` : ''}.` : server.last_ok_at ? `Observed ${formatDateTime(server.last_ok_at)}.` : 'No completed observation yet.';
  return <StatusText tone={status}>{message}</StatusText>;
}

function Inventory({ items, page, onManage, onPage }: { items: ArrObservedItem[]; page: { page: number; total: number; total_pages: number }; onManage: (item: ArrObservedItem) => void; onPage: (next: number) => void }) {
  return <>
    {items.length ? <ul className="arr-inventory" aria-label="Observed inventory">
      {items.map((item) => <li key={`${item.server_id}:${item.upstream_id}`}>
        <div><strong>{item.title}</strong>{item.year ? <span> · {item.year}</span> : null}<small>{item.media_type} · {item.monitored ? 'Monitored' : 'Not monitored'}{item.has_file ? ' · Upstream reports a file' : ''}</small></div>
        <div className="arr-inventory-actions"><span className={`arr-import is-${item.import_state}`}>{importCopy[item.import_state]}</span><Button onClick={() => onManage(item)} variant="quiet">Manage {item.title}</Button></div>
      </li>)}
    </ul> : <EmptyState body="When this service observes movies or shows, they will appear here. This does not create requests or make media playable." title="No inventory on this page" />}
    <Pager page={page.page} total={page.total} totalPages={page.total_pages} onPage={onPage} />
  </>;
}

function safeRecordLabel(record: ArrDatasetPage['items'][number]) {
  const data = record.data;
  for (const key of ['status', 'trackedDownloadStatus', 'trackedDownloadState', 'eventType', 'type', 'airDateUtc', 'airDate']) if (typeof data[key] === 'string' && data[key]) return data[key] as string;
  return record.item_id == null ? 'Observed record' : `Item ${record.item_id}`;
}

function Dataset({ serverId }: { serverId: string }) {
  const [dataset, setDataset] = useState<ArrDataset>('queue');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ArrDatasetPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try { setData(await getArrDataset(serverId, dataset, { page })); } catch (cause) { setError(errorMessage(cause, 'Unable to load this observation.')); }
  }, [dataset, page, serverId]);
  useEffect(() => {
    let active = true;
    setData(null); setError(null);
    getArrDataset(serverId, dataset, { page }).then((next) => { if (active) setData(next); }).catch((cause) => { if (active) setError(errorMessage(cause, 'Unable to load this observation.')); });
    return () => { active = false; };
  }, [dataset, page, serverId]);
  useEffect(() => {
    if (data && page > data.total_pages) setPage(Math.max(1, data.total_pages));
  }, [data, page]);
  return <section aria-labelledby="arr-dataset-heading" className="arr-dataset">
    <h3 id="arr-dataset-heading">Service observations</h3>
    <div aria-label="Observation dataset" className="arr-tabs">
      {DATASETS.map((entry) => <button aria-pressed={dataset === entry.id} key={entry.id} onClick={() => { setDataset(entry.id); setPage(1); }} type="button">{entry.label}</button>)}
    </div>
    {error ? <ErrorState body={error} onRetry={() => { void load(); }} title="Lumina could not load these observations" /> : !data ? <p className="admin-note" role="status">Loading observations…</p> : <>
      {data.items.length ? <ul className="arr-records">{data.items.map((record) => <li key={record.record_id}><span>{safeRecordLabel(record)}</span><small>Observed {formatDateTime(record.observed_at)}</small></li>)}</ul> : <EmptyState body="There are no saved observations in this category yet." title={`No ${dataset} observations`} />}
      {data.stale ? <p className="arr-note"><StatusText tone="attention">These records are from the last successful refresh and may be out of date.</StatusText></p> : null}
      <Pager page={data.page} total={data.total} totalPages={data.total_pages} onPage={setPage} />
    </>}
  </section>;
}

/** Admin-only operations; upstream rows remain observations until Lumina registers a visible library item. */
export function IntegrationsPanel() {
  const [servers, setServers] = useState<ArrServerObservation[] | null>(null);
  const [inventory, setInventory] = useState<Awaited<ReturnType<typeof getArrObservations>> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshRevision, setRefreshRevision] = useState(0);
  const [managed, setManaged] = useState<ArrObservedItem | null>(null);
  const loadServers = useCallback(async () => {
    setError(null);
    try {
      const next = await getArrObservations({ page: 1 });
      setServers(next.servers);
      setSelected((current) => current && next.servers.some((server) => server.server_id === current) ? current : next.servers[0]?.server_id ?? null);
    } catch (cause) { setError(errorMessage(cause, 'Unable to load integrations.')); }
  }, []);
  useEffect(() => { void loadServers(); }, [loadServers]);
  useEffect(() => {
    if (!selected) { setInventory(null); return; }
    let active = true;
    setInventory(null);
    getArrObservations({ serverId: selected, page }).then((next) => { if (active) setInventory(next); }).catch((cause) => { if (active) setError(errorMessage(cause, 'Unable to load this inventory.')); });
    return () => { active = false; };
  }, [selected, page, refreshRevision]);
  useEffect(() => {
    if (inventory && page > inventory.total_pages) setPage(Math.max(1, inventory.total_pages));
  }, [inventory, page]);
  const server = servers?.find((entry) => entry.server_id === selected) ?? null;
  async function refresh(serverId: string) {
    setRefreshing(serverId); setRefreshError(null);
    try { await refreshArrServer(serverId); await loadServers(); setRefreshRevision((value) => value + 1); }
    catch (cause) { setRefreshError(errorMessage(cause, 'The refresh did not finish. Its outcome is unknown; review the next observation before trying again.')); }
    finally { setRefreshing(null); }
  }
  return <div className="arr-integrations">
    <p className="arr-intro">Integration observations continue while Requests is off. They describe Sonarr and Radarr activity; only a visible registered library item can be played.</p>
    {error ? <ErrorState body={error} onRetry={() => { void loadServers(); }} title="Lumina could not load integrations" /> : !servers ? <p className="admin-note" role="status">Loading integrations…</p> : !servers.length ? <EmptyState body="Set up Sonarr or Radarr below. Lumina will retain their credentials and existing request configuration." title="No connected integrations" /> : <>
      <ul className="arr-servers" aria-label="Connected integrations">{servers.map((entry) => <li className={entry.server_id === server?.server_id ? 'is-selected' : ''} key={entry.server_id}>
        <button aria-pressed={entry.server_id === server?.server_id} className="arr-server-select" onClick={() => { setInventory(null); setManaged(null); setSelected(entry.server_id); setPage(1); }} type="button"><strong>{entry.name || entry.kind}</strong><span>{entry.kind}{entry.version ? ` ${entry.version}` : ''}{entry.enabled ? '' : ' · Disabled'}</span></button>
        <div className="arr-server-meta"><span>{(entry.summary?.inventory_count ?? 0).toLocaleString()} observed · {(entry.summary?.queue_count ?? 0).toLocaleString()} queued</span><ServerState server={entry} /><Button busy={refreshing === entry.server_id} icon={<RefreshCw />} onClick={() => { void refresh(entry.server_id); }} variant="quiet">Refresh</Button></div>
      </li>)}</ul>
      {refreshError ? <p className="arr-note" role="alert">{refreshError}</p> : null}
      {server ? <section aria-labelledby="arr-inventory-heading" className="arr-inventory-panel"><header><div><h3 id="arr-inventory-heading">{server.name || server.kind} inventory</h3><p>Imported titles become available only after Lumina can match and register them.</p></div></header><div className="arr-inventory-content">{!inventory ? <p className="admin-note" role="status">Loading inventory…</p> : <Inventory items={inventory.items} page={inventory} onManage={setManaged} onPage={setPage} />}{managed?.server_id === server.server_id ? <ManageIntegrationItem item={managed} key={`${managed.server_id}:${managed.upstream_id}`} onClose={() => setManaged(null)} onOperation={() => setRefreshRevision((value) => value + 1)} /> : null}</div><Dataset key={`dataset:${server.server_id}:${refreshRevision}`} serverId={server.server_id} /><OperationAudit key={`audit:${server.server_id}:${refreshRevision}`} revision={refreshRevision} serverId={server.server_id} /></section> : null}
    </>}
    <ConnectorPanel />
    <MusicRequestOperations />
  </div>;
}
