import { requestJson } from '../../api';

export type ArrImportState = 'unmapped' | 'root_unavailable' | 'awaiting_first_import' | 'pending_import' | 'registered';
export type ArrDataset = 'queue' | 'history' | 'health' | 'calendar' | 'missing' | 'cutoff';

export type ArrServerObservation = {
  server_id: string;
  kind: string;
  name: string;
  enabled: boolean;
  version: string | null;
  capabilities: Record<string, boolean>;
  summary: {
    inventory_count: number;
    queue_count: number;
    history_count: number;
    health_count: number;
    calendar_count: number;
    missing_count: number;
    cutoff_count: number;
    import_counts: Record<string, number>;
  };
  last_ok_at: string | null;
  last_error: string | null;
  stale: boolean;
  attempted_at: string | null;
};

export type ArrObservedItem = {
  server_id: string;
  upstream_id: number;
  media_type: string;
  tmdb_id: number | null;
  tvdb_id: number | null;
  title: string;
  year: number | null;
  monitored: boolean;
  has_file: boolean;
  import_state: ArrImportState;
  details: Record<string, unknown>;
  observed_at: string;
};

export type ArrObservationPage = {
  servers: ArrServerObservation[];
  items: ArrObservedItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type ArrDatasetRecord = { record_id: string; item_id: number | null; data: Record<string, unknown>; observed_at: string };
export type ArrDatasetPage = { items: ArrDatasetRecord[]; page: number; page_size: number; total: number; total_pages: number; stale: boolean };
export type TitleIntegrations = { title_id: string; integrations: Array<{ state: ArrImportState; stale: boolean }> };
export type ArrControlledSeason = { season_number: number; monitored: boolean };
export type ArrProfile = { id: number; name: string };
export type ArrControlledItem = {
  server_id: string;
  upstream_id: number;
  revision: string;
  monitored: boolean;
  quality_profile_id: number;
  seasons: ArrControlledSeason[];
  profiles: ArrProfile[];
  quality: { current: string | null; target: string | null; cutoff: string | null };
};
export type ArrRelease = {
  release_id: string;
  title: string;
  quality: string | null;
  languages: string[];
  rejections: string[];
  approved: boolean;
  size: number | null;
};
export type ArrReleases = { items: ArrRelease[]; revision: string };
export type ArrOperationAction = 'monitor' | 'profile' | 'search' | 'rescan' | 'grab';
export type ArrOperationStatus = 'pending' | 'dispatching' | 'succeeded' | 'failed' | 'uncertain' | 'conflict';
export type ArrOperation = {
  id: string;
  server_id: string;
  upstream_id: number;
  action: ArrOperationAction;
  status: ArrOperationStatus;
  result: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
};
export type ArrOperationPage = { items: ArrOperation[]; page: number; page_size: number; total: number; total_pages: number };
export type ArrOperationInput = {
  operation_id: string;
  base_revision: string;
  action: ArrOperationAction;
  monitored?: boolean;
  season_number?: number;
  profile_id?: number;
  release_id?: string;
};

const query = (input: Record<string, string | number | undefined>) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) if (value !== undefined) params.set(key, String(value));
  const text = params.toString();
  return text ? `?${text}` : '';
};
const segment = (value: string) => encodeURIComponent(value);

export const getArrObservations = (input: { serverId?: string; page?: number; pageSize?: number } = {}) =>
  requestJson<ArrObservationPage>(`/api/admin/arr/observations${query({ server_id: input.serverId, page: input.page ?? 1, page_size: input.pageSize ?? 50 })}`);

export const getArrDataset = (serverId: string, dataset: ArrDataset, input: { page?: number; pageSize?: number } = {}) =>
  requestJson<ArrDatasetPage>(`/api/admin/arr/servers/${segment(serverId)}/${dataset}${query({ page: input.page ?? 1, page_size: input.pageSize ?? 50 })}`);

/** The upstream call can take over 30 seconds. A timeout reports an uncertain outcome; the UI never retries it on its own. */
export const refreshArrServer = (serverId: string) => requestJson<ArrServerObservation>(`/api/admin/arr/servers/${segment(serverId)}/refresh`, { method: 'POST' }, { timeoutMs: 35000 });
export const getTitleIntegrations = (titleId: string) => requestJson<TitleIntegrations>(`/api/arr/titles/${segment(titleId)}`);
export const getArrControlledItem = (serverId: string, upstreamId: number) => requestJson<ArrControlledItem>(`/api/admin/arr/servers/${segment(serverId)}/items/${upstreamId}`);
export const getArrReleases = (serverId: string, upstreamId: number) => requestJson<ArrReleases>(`/api/admin/arr/servers/${segment(serverId)}/items/${upstreamId}/releases`);
export const postArrOperation = (serverId: string, upstreamId: number, body: ArrOperationInput) => requestJson<ArrOperation>(`/api/admin/arr/servers/${segment(serverId)}/items/${upstreamId}/operations`, { method: 'POST', body: JSON.stringify(body) });
export const getArrOperations = (serverId: string, input: { page?: number; pageSize?: number } = {}) => requestJson<ArrOperationPage>(`/api/admin/arr/operations${query({ server_id: serverId, page: input.page ?? 1, page_size: input.pageSize ?? 50 })}`);
export const reconcileArrOperation = (operationId: string) => requestJson<ArrOperation>(`/api/admin/arr/operations/${segment(operationId)}/reconcile`, { method: 'POST' });

export type ConnectorKind = 'lidarr' | 'prowlarr' | 'qbittorrent' | 'nzbget';
export type ConnectorPathMapping = { remote: string; local: string };
export type ConnectorArrClient = { server_id: string; client_name: string };
export type ConnectorSettings = {
  root_folder?: string | null;
  quality_profile_id?: number | null;
  metadata_profile_id?: number | null;
  path_mappings: ConnectorPathMapping[];
  arr_clients: ConnectorArrClient[];
};
export type ServiceConnector = {
  id: string;
  kind: ConnectorKind;
  name: string;
  base_url: string;
  settings: ConnectorSettings;
  api_key_set: boolean;
  username_set: boolean;
  password_set: boolean;
  enabled: boolean;
  version: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  stale: boolean;
  capabilities: Record<string, boolean>;
};
export type ConnectorDataset = 'transfers' | 'health' | 'indexers' | 'indexer_status' | 'history' | 'stats' | 'root_folders' | 'quality_profiles' | 'metadata_profiles' | 'artists' | 'albums' | 'queue';
export type ConnectorDetails = ServiceConnector & { dataset: ConnectorDataset; items: Array<Record<string, unknown>>; page: number; page_size: number; total: number };
export type ConnectorInput = {
  kind: ConnectorKind;
  name: string;
  base_url: string;
  api_key?: string;
  username?: string;
  password?: string;
  settings: ConnectorSettings;
};

export const getConnectors = () => requestJson<{ items: ServiceConnector[] }>('/api/admin/connectors');
export const createConnector = (body: ConnectorInput) => requestJson<ServiceConnector>('/api/admin/connectors', { method: 'POST', body: JSON.stringify(body) });
export const updateConnector = (id: string, body: ConnectorInput) => requestJson<ServiceConnector>(`/api/admin/connectors/${segment(id)}`, { method: 'PUT', body: JSON.stringify(body) });
export const refreshConnector = (id: string) => requestJson<ServiceConnector>(`/api/admin/connectors/${segment(id)}/refresh`, { method: 'POST' }, { timeoutMs: 35000 });
export const getConnectorDetails = (id: string, dataset: ConnectorDataset, input: { page?: number; pageSize?: number } = {}) => requestJson<ConnectorDetails>(`/api/admin/connectors/${segment(id)}${query({ dataset, page: input.page ?? 1, page_size: input.pageSize ?? 50 })}`);
export const disableConnector = (id: string) => requestJson<ServiceConnector>(`/api/admin/connectors/${segment(id)}`, { method: 'DELETE' });

export type MusicRequestStatus = 'pending_approval' | 'dispatching' | 'awaiting_import' | 'succeeded' | 'failed' | 'uncertain';
export type MusicRequest = {
  id: string;
  connector_id: string;
  foreign_artist_id: string;
  foreign_album_id: string;
  artist_name: string;
  album_name: string;
  artist_id: number | null;
  album_id: number | null;
  status: MusicRequestStatus;
  result: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
  availability?: { state: 'awaiting_import' | 'playable'; library_album_id?: string } | null;
};
export type MusicRequestPage = { items: MusicRequest[]; page: number; page_size: number; total: number; total_pages: number };
export type MusicRequestTarget = { id: string; name: string };
export type LidarrInventoryArtist = { id: number; foreign_artist_id: string; name: string; monitored: boolean; albums: Array<{ id: number; foreign_album_id: string; title: string; monitored: boolean }> };
export type LidarrInventoryPage = { items: LidarrInventoryArtist[]; page: number; page_size: number; total: number; total_pages: number; queue_count: number; history_count: number };
export type MusicRequestInput = { id: string; connector_id: string; foreign_artist_id: string; foreign_album_id: string; artist_name?: string; album_name?: string };

export const getMusicRequestTargets = () => requestJson<{ items: MusicRequestTarget[] }>('/api/music/request-targets');
export const createMusicRequest = (body: MusicRequestInput) => requestJson<MusicRequest>('/api/music/requests', { method: 'POST', body: JSON.stringify(body) });
export const getAdminMusicRequests = (input: { page?: number; pageSize?: number } = {}) => requestJson<MusicRequestPage>(`/api/admin/music/requests${query({ page: input.page ?? 1, page_size: input.pageSize ?? 25 })}`);
export const approveMusicRequest = (id: string) => requestJson<MusicRequest>(`/api/admin/music/requests/${segment(id)}/approve`, { method: 'POST' }, { timeoutMs: 35000 });
export const reconcileMusicRequest = (id: string) => requestJson<MusicRequest>(`/api/admin/music/requests/${segment(id)}/reconcile`, { method: 'POST' });
export const getLidarrInventory = (id: string, input: { page?: number; pageSize?: number } = {}) => requestJson<LidarrInventoryPage>(`/api/admin/lidarr/${segment(id)}/inventory${query({ page: input.page ?? 1, page_size: input.pageSize ?? 25 })}`);
