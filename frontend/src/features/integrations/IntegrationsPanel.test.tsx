import { chooseSelect } from '../../test/selectChoice';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from '../../api';

const api = vi.hoisted(() => ({ getArrObservations: vi.fn(), getArrDataset: vi.fn(), refreshArrServer: vi.fn(), getArrControlledItem: vi.fn(), getArrReleases: vi.fn(), getArrOperations: vi.fn(), postArrOperation: vi.fn(), reconcileArrOperation: vi.fn(), getConnectors: vi.fn(), createConnector: vi.fn(), updateConnector: vi.fn(), refreshConnector: vi.fn(), getConnectorDetails: vi.fn(), disableConnector: vi.fn(), getMusicRequestTargets: vi.fn(), createMusicRequest: vi.fn(), getAdminMusicRequests: vi.fn(), approveMusicRequest: vi.fn(), reconcileMusicRequest: vi.fn(), getLidarrInventory: vi.fn() }));
vi.mock('./integrationsApi', async (original) => ({ ...(await original<typeof import('./integrationsApi')>()), ...api }));

import { IntegrationsPanel } from './IntegrationsPanel';
import { MusicReleaseRequest, MusicRequestOperations } from './MusicRequestControls';
import type { ArrObservedItem, ArrObservationPage, ArrServerObservation, ArrOperationInput } from './integrationsApi';

// The observer can return an empty summary for a connection without a completed collection pass.
const server = { server_id: 'sonarr-1', kind: 'sonarr', name: 'TV', enabled: true, version: '4.0', capabilities: {}, summary: {}, last_ok_at: '2026-10-17T12:00:00Z', last_error: null, stale: false, attempted_at: '2026-10-17T12:00:00Z' } as ArrServerObservation;
const observations = (items: ArrObservedItem[] = []): ArrObservationPage => ({ servers: [server], items, page: 1, page_size: 50, total: items.length, total_pages: 1 });
const observedItem: ArrObservedItem = { server_id: 'sonarr-1', upstream_id: 3, media_type: 'tv', tmdb_id: null, tvdb_id: 12, title: 'Northbound', year: 2024, monitored: true, has_file: true, import_state: 'awaiting_first_import', details: { episodes: { episode_count: 4, file_count: 1 } }, observed_at: '2026-10-17T12:00:00Z' };
const controlled = { server_id: 'sonarr-1', upstream_id: 3, revision: 'fresh-revision', monitored: true, quality_profile_id: 4, seasons: [{ season_number: 1, monitored: true }], profiles: [{ id: 4, name: 'HD' }, { id: 8, name: 'Archive' }], quality: { current: 'WEBDL-1080p', target: 'HD', cutoff: 'Bluray-1080p' } };
const operation = (input: ArrOperationInput, status: 'succeeded' | 'uncertain' | 'conflict' = 'succeeded') => ({ id: input.operation_id, server_id: 'sonarr-1', upstream_id: 3, action: input.action, status, result: {}, error: null, created_at: '2026-10-17T12:00:00Z', updated_at: '2026-10-17T12:00:00Z' });
const connector = { id: 'lidarr-1', kind: 'lidarr' as const, name: 'Music', base_url: 'http://lidarr.test', settings: { path_mappings: [], arr_clients: [] }, api_key_set: true, username_set: false, password_set: false, enabled: false, version: null, last_ok_at: null, last_error: null, stale: true, capabilities: {} };

describe('IntegrationsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    api.getArrObservations.mockResolvedValue(observations([observedItem]));
    api.getArrDataset.mockResolvedValue({ items: [{ record_id: 'q1', item_id: 3, data: { status: 'Downloading' }, observed_at: '2026-10-17T12:00:00Z' }], page: 1, page_size: 50, total: 1201, total_pages: 25, stale: false });
    api.refreshArrServer.mockResolvedValue(server);
    api.getArrOperations.mockResolvedValue({ items: [], page: 1, page_size: 25, total: 0, total_pages: 1 });
    api.getArrControlledItem.mockResolvedValue(controlled);
    api.getArrReleases.mockResolvedValue({ items: [], revision: 'fresh-revision' });
    api.getConnectors.mockResolvedValue({ items: [] });
    api.getConnectorDetails.mockResolvedValue({ ...connector, dataset: 'root_folders', items: [], page: 1, page_size: 50, total: 0 });
    api.getMusicRequestTargets.mockResolvedValue({ items: [] });
    api.getAdminMusicRequests.mockResolvedValue({ items: [], page: 1, page_size: 25, total: 0, total_pages: 1 });
  });

  it('saves and explicitly tests a connector before presenting its read-only observations', async () => {
    const user = userEvent.setup();
    api.createConnector.mockResolvedValue(connector);
    api.getConnectorDetails.mockImplementation((_id: string, dataset: string) => Promise.resolve({ ...connector, dataset, items: dataset === 'root_folders' ? [{ id: 1, path: '/music' }] : dataset === 'quality_profiles' ? [{ id: 2, name: 'Lossless' }] : dataset === 'metadata_profiles' ? [{ id: 3, name: 'Standard' }] : [], page: 1, page_size: 50, total: dataset === 'root_folders' ? 1 : 0 }));
    api.refreshConnector.mockResolvedValue({ ...connector, enabled: true, last_ok_at: '2026-10-17T12:00:00Z', capabilities: { observe: true } });
    render(<IntegrationsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Add connector' }));
    await user.clear(screen.getByLabelText('Lidarr address'));
    await user.type(screen.getByLabelText('Lidarr address'), 'http://lidarr.test');
    await user.type(screen.getByLabelText('Lidarr API key'), 'write-only-key');
    await user.click(screen.getByRole('button', { name: 'Save connector' }));
    await waitFor(() => expect(api.createConnector).toHaveBeenCalledWith(expect.objectContaining({ kind: 'lidarr', base_url: 'http://lidarr.test', api_key: 'write-only-key' })));
    expect(await screen.findByText('Connector saved. Test this connection to qualify it.')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Test connection' }));
    await waitFor(() => expect(api.refreshConnector).toHaveBeenCalledWith('lidarr-1'));
    expect(await screen.findByText('Connection tested. Saved observations are available below.')).toBeTruthy();
    expect(screen.getByText('/music', { selector: '.connector-records span' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /transfer|grab/i })).toBeNull();
  });

  it('explains read-only torrent observations and retains the snapshot date after a collection failure', async () => {
    const torrent = { ...connector, kind: 'qbittorrent', name: 'Downloads', enabled: true, last_ok_at: server.last_ok_at, stale: true, last_error: 'Service unavailable', capabilities: { observe: true, control: false } };
    api.getConnectors.mockResolvedValue({ items: [torrent] });
    api.getConnectorDetails.mockResolvedValue({ ...torrent, dataset: 'transfers', items: [], page: 1, page_size: 50, total: 0 });
    render(<IntegrationsPanel />);
    const details = await screen.findByRole('region', { name: 'Collected information' });
    expect(within(details).getByText(/Transfer states, progress/)).toBeTruthy();
    expect(within(details).getByText(/Read-only connection/)).toBeTruthy();
    expect(within(details).getByRole('button', { name: 'Transfers' })).toBeTruthy();
    expect(within(details).queryByRole('button', { name: 'Health' })).toBeNull();
    expect(screen.getAllByText(/Saved snapshot from/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Last collection failed: Service unavailable').length).toBeGreaterThan(0);
  });

  it('submits an explicit release-group request to a member-safe Lidarr target', async () => {
    const user = userEvent.setup();
    api.getMusicRequestTargets.mockResolvedValue({ items: [{ id: 'lidarr-1', name: 'Music' }] });
    api.createMusicRequest.mockResolvedValue({ id: 'request-1', connector_id: 'lidarr-1', foreign_artist_id: '11111111-1111-4111-8111-111111111111', foreign_album_id: '22222222-2222-4222-8222-222222222222', artist_name: 'Northbound', album_name: 'Arrival', artist_id: null, album_id: null, status: 'pending_approval', result: {}, error: null, created_at: '2026-10-17T12:00:00Z', updated_at: '2026-10-17T12:00:00Z' });
    render(<MusicReleaseRequest artistId="11111111-1111-4111-8111-111111111111" artistName="Northbound" release={{ id: '22222222-2222-4222-8222-222222222222', name: 'Arrival' }} />);
    await user.click(await screen.findByRole('button', { name: 'Request album' }));
    await waitFor(() => expect(api.createMusicRequest).toHaveBeenCalledWith(expect.objectContaining({ connector_id: 'lidarr-1', foreign_artist_id: '11111111-1111-4111-8111-111111111111', foreign_album_id: '22222222-2222-4222-8222-222222222222', id: expect.any(String) })));
    expect(await screen.findByText('Waiting for approval')).toBeTruthy();
  });

  it.each([401, 403])('does not expose a member album action when target discovery returns %s', async (status) => {
    api.getMusicRequestTargets.mockRejectedValue(new ApiRequestError('Not available', status));
    render(<MusicReleaseRequest artistId="11111111-1111-4111-8111-111111111111" artistName="Northbound" release={{ id: '22222222-2222-4222-8222-222222222222', name: 'Arrival' }} />);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Request album' })).toBeNull());
  });

  it('limits admin actions to approval and read-only reconciliation', async () => {
    const user = userEvent.setup();
    const request = { id: 'request-1', connector_id: 'lidarr-1', foreign_artist_id: '11111111-1111-4111-8111-111111111111', foreign_album_id: '22222222-2222-4222-8222-222222222222', artist_name: 'Northbound', album_name: 'Arrival', artist_id: null, album_id: null, status: 'pending_approval' as const, result: {}, error: null, created_at: '2026-10-17T12:00:00Z', updated_at: '2026-10-17T12:00:00Z' };
    api.getAdminMusicRequests.mockResolvedValue({ items: [request], page: 1, page_size: 25, total: 1, total_pages: 1 }); api.approveMusicRequest.mockResolvedValue({ ...request, status: 'awaiting_import' });
    render(<MusicRequestOperations />);
    await user.click(await screen.findByRole('button', { name: 'Approve request' }));
    await waitFor(() => expect(api.approveMusicRequest).toHaveBeenCalledWith('request-1'));
    expect(await screen.findByText('Waiting for import')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /delete|transfer|cancel/i })).toBeNull();
  });

  it('explains collected information and retained library matching counts with dated freshness', async () => {
    const saved = { ...server, stale: true, summary: { inventory_count: 1201, queue_count: 2, history_count: 8, health_count: 1, calendar_count: 3, missing_count: 4, cutoff_count: 5, import_counts: { registered: 1000, pending_import: 100, awaiting_first_import: 50, root_unavailable: 20, unmapped: 31 } } };
    api.getArrObservations.mockResolvedValue({ ...observations([observedItem]), servers: [saved] });
    api.getArrDataset.mockResolvedValue({ items: [{ record_id: 'q1', item_id: 3, data: { status: 'Downloading', sizeleft: 1048576, timeleft: '00:02:00', protocol: 'torrent' }, observed_at: server.last_ok_at }], page: 1, page_size: 50, total: 2, total_pages: 1, stale: true });
    render(<IntegrationsPanel />);

    const information = await screen.findByRole('region', { name: 'Information from TV' });
    expect(within(information).getByText('1,201')).toBeTruthy();
    expect(within(information).getByText('1,000')).toBeTruthy();
    expect(within(information).getByText('150')).toBeTruthy();
    expect(within(information).getByText('20')).toBeTruthy();
    expect(within(information).getByText('31')).toBeTruthy();
    expect(screen.getByText(/Saved snapshot from/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Queue 2 records' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Health 1 record' })).toBeTruthy();
    expect(await screen.findByText(/1.0 MB remaining/)).toBeTruthy();
    expect(screen.queryByText(/may be out of date/)).toBeNull();
    expect(api.refreshArrServer).not.toHaveBeenCalled();
    api.refreshArrServer.mockRejectedValueOnce(new Error('Connection unavailable'));
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Connection unavailable');
    expect(within(information).getByText('1,201')).toBeTruthy();
    expect(api.refreshArrServer).toHaveBeenCalledTimes(1);
    api.getArrDataset.mockResolvedValueOnce({ items: [{ record_id: 'h1', item_id: 3, data: { eventType: 'downloadFolderImported', date: server.last_ok_at }, observed_at: server.last_ok_at }], page: 1, page_size: 50, total: 8, total_pages: 1, stale: true });
    await userEvent.click(screen.getByRole('button', { name: 'History 8 records' }));
    expect(await screen.findByText(/^Event /)).toBeTruthy();
  });

  it('shows incomplete server summaries, paged observations and a deliberate refresh without request controls', async () => {
    render(<IntegrationsPanel />);
    expect(await screen.findByText('Inventory not collected yet')).toBeTruthy();
    expect(await screen.findByText('Northbound')).toBeTruthy();
    expect(screen.getByText('Waiting for Lumina’s first import')).toBeTruthy();
    await userEvent.click(await screen.findByRole('button', { name: 'Queue' }));
    expect(await screen.findByText('Downloading')).toBeTruthy();
    expect(screen.getByText('Page 1 of 25 · 1,201 items')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(api.refreshArrServer).toHaveBeenCalledWith('sonarr-1'));
    expect(screen.queryByRole('button', { name: /grab|monitor|unmonitor/i })).toBeNull();
  });

  it('returns to the last valid inventory page after a refresh shortens it', async () => {
    let shortened = false;
    api.getArrObservations.mockImplementation(({ serverId, page = 1 }: { serverId?: string; page?: number } = {}) => Promise.resolve(
      serverId ? { servers: [server], items: shortened && page === 3 ? [] : [observedItem], page, page_size: 50, total: (shortened ? 2 : 3) * 50, total_pages: shortened ? 2 : 3 } : observations(),
    ));
    api.refreshArrServer.mockImplementation(async () => { shortened = true; return server; });
    render(<IntegrationsPanel />);
    await screen.findByText('Northbound');
    await userEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]);
    await screen.findByText('Page 2 of 3 · 150 items');
    await userEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]);
    expect(await screen.findByText('Page 3 of 3 · 150 items')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Page 2 of 2 · 100 items')).toBeTruthy();
    await waitFor(() => expect(api.getArrObservations).toHaveBeenCalledWith({ serverId: 'sonarr-1', page: 2 }));
  });

  it('keeps navigation while an out-of-range dataset page is being clamped', async () => {
    let shortened = false;
    api.getArrDataset.mockImplementation((_serverId: string, _dataset: string, { page = 1 }: { page?: number }) => {
      if (page === 3) shortened = true;
      const totalPages = shortened ? 2 : 3;
      return Promise.resolve({ items: page === 3 ? [] : [{ record_id: `q${page}`, item_id: 3, data: { status: 'Downloading' }, observed_at: '2026-10-17T12:00:00Z' }], page, page_size: 50, total: totalPages * 50, total_pages: totalPages, stale: false });
    });
    render(<IntegrationsPanel />);
    await screen.findByText('Downloading');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Page 2 of 3 · 150 items');
    await waitFor(() => expect(api.getArrDataset).toHaveBeenCalledWith('sonarr-1', 'queue', { page: 2 }));
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(api.getArrDataset).toHaveBeenCalledWith('sonarr-1', 'queue', { page: 3 }));
    expect(await screen.findByText('Page 2 of 2 · 100 items')).toBeTruthy();
    await waitFor(() => expect(api.getArrDataset).toHaveBeenCalledWith('sonarr-1', 'queue', { page: 2 }));
  });

  it('loads a fresh item before sending one revisioned monitor or profile operation', async () => {
    const user = userEvent.setup();
    api.postArrOperation.mockImplementation((_serverId: string, _upstreamId: number, input: ArrOperationInput) => Promise.resolve(operation(input)));
    render(<IntegrationsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Manage Northbound' }));
    expect(await screen.findByRole('heading', { name: 'Manage Northbound' })).toBeTruthy();
    expect(screen.getByText('WEBDL-1080p')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Unmonitor title' }));
    await waitFor(() => expect(api.postArrOperation).toHaveBeenCalledWith('sonarr-1', 3, expect.objectContaining({ action: 'monitor', base_revision: 'fresh-revision', monitored: false, operation_id: expect.any(String) })));
    chooseSelect(screen.getByLabelText('Quality profile'), 'Archive');
    await user.click(screen.getByRole('button', { name: 'Apply quality profile' }));
    await waitFor(() => expect(api.postArrOperation).toHaveBeenLastCalledWith('sonarr-1', 3, expect.objectContaining({ action: 'profile', base_revision: 'fresh-revision', profile_id: 8, operation_id: expect.any(String) })));
  });

  it('reloads current state after a stale conflict and lets the admin choose a new action', async () => {
    const user = userEvent.setup();
    api.postArrOperation.mockRejectedValue({ status: 409 });
    render(<IntegrationsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Manage Northbound' }));
    await user.click(await screen.findByRole('button', { name: 'Unmonitor title' }));
    expect(await screen.findByText(/This item changed upstream/i)).toBeTruthy();
    expect(api.getArrControlledItem.mock.calls.length).toBeGreaterThan(1);
    expect((screen.getByRole('button', { name: 'Search' }) as HTMLButtonElement).disabled).toBe(false);
    expect(api.postArrOperation).toHaveBeenCalledTimes(1);
  });

  it('keeps an uncertain operation across reload and requires explicit reconciliation', async () => {
    const user = userEvent.setup();
    api.postArrOperation.mockRejectedValue(new Error('Timed out'));
    const first = render(<IntegrationsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Manage Northbound' }));
    await user.click(await screen.findByRole('button', { name: 'Unmonitor title' }));
    expect(await screen.findByText(/could not confirm the result/i)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Search' }) as HTMLButtonElement).disabled).toBe(true);
    const sent = api.postArrOperation.mock.calls[0][2] as ArrOperationInput;
    first.unmount();
    api.getArrOperations.mockResolvedValue({ items: [operation(sent, 'uncertain')], page: 1, page_size: 25, total: 1, total_pages: 1 });
    api.reconcileArrOperation.mockResolvedValue({ ...operation(sent), status: 'succeeded' });
    render(<IntegrationsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Manage Northbound' }));
    expect(await screen.findByText(/Monitoring: uncertain/i)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Reconcile operation' }));
    await waitFor(() => expect(api.reconcileArrOperation).toHaveBeenCalledWith(sent.operation_id));
  });

  it('shows a rejected release without a grab action', async () => {
    const user = userEvent.setup();
    api.getArrReleases.mockResolvedValue({ revision: 'fresh-revision', items: [{ release_id: 'opaque-release', title: 'Northbound 1080p', quality: 'WEBDL-1080p', languages: ['English'], rejections: ['Does not meet the configured cutoff'], approved: false, size: 1_200_000_000 }] });
    render(<IntegrationsPanel />);
    await user.click(await screen.findByRole('button', { name: 'Manage Northbound' }));
    await user.click(await screen.findByRole('button', { name: 'Review releases' }));
    expect(await screen.findByText('Not available to grab')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Grab release' })).toBeNull();
  });
});
