import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ request: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn(), refresh: vi.fn() }));
vi.mock('../../api', () => ({ requestJson: mocks.request, put: mocks.put, post: mocks.post, del: mocks.del }));
vi.mock('../music/musicListeningExport', () => ({ refreshListeningExport: mocks.refresh }));
vi.mock('./settingsHost', () => ({ useSettingsHost: () => ({ user: { id: 'alice' } }) }));
import { ListeningExportRows } from './ListeningExportRows';

beforeEach(() => { vi.clearAllMocks(); });

it('connects a member token without echoing it and reconciles an uncertain play before disconnecting', async () => {
  let connected = false;
  mocks.request.mockImplementation(async (path: string) => path.includes('listening-events') ? { items: [{ id: 'play', started_at: 900, deliveries: { listenbrainz: { status: 'uncertain' } } }] } : { items: [{ provider: 'listenbrainz', enabled: connected, username: connected ? 'listener' : null, credentials_set: connected, authorization_pending: false }] });
  mocks.put.mockImplementation(async () => { connected = true; return {}; });
  mocks.del.mockImplementation(async () => { connected = false; return {}; });
  mocks.post.mockResolvedValue({ id: 'play', started_at: 900, deliveries: { listenbrainz: { status: 'sent' } } });
  render(<ListeningExportRows />);
  await screen.findByText('ListenBrainz: uncertain');
  fireEvent.change(screen.getByLabelText('ListenBrainz user token'), { target: { value: 'private-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect ListenBrainz' }));
  await screen.findByText('Connected as listener');
  expect(screen.getByLabelText('ListenBrainz user token')).toMatchObject({ value: '' });
  expect(mocks.put).toHaveBeenCalledWith('/api/me/listening-export/listenbrainz', { token: 'private-token' });
  fireEvent.click(screen.getByRole('button', { name: 'Check provider history' }));
  await screen.findByText('ListenBrainz: sent');
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect ListenBrainz' }));
  await waitFor(() => expect(screen.queryByText('Connected as listener')).toBeNull());
  expect(mocks.refresh).toHaveBeenCalledWith('alice');
});

it('authorizes Last.fm externally and finishes with the server-held request token', async () => {
  let pending = false, connected = false;
  mocks.request.mockImplementation(async (path: string) => path.includes('listening-events') ? { items: [] } : { items: [{ provider: 'lastfm', enabled: connected, username: connected ? 'listener' : null, credentials_set: pending, authorization_pending: pending && !connected }] });
  mocks.post.mockImplementation(async (path: string) => { if (path.endsWith('authorize')) { pending = true; return { authorization_url: 'https://www.last.fm/api/auth/?token=request' }; } connected = true; return {}; });
  render(<ListeningExportRows />);
  await screen.findByText('No listening exports yet.');
  fireEvent.change(screen.getByLabelText('Last.fm API key'), { target: { value: 'a'.repeat(32) } });
  fireEvent.change(screen.getByLabelText('Last.fm API secret'), { target: { value: 'b'.repeat(32) } });
  fireEvent.click(screen.getByRole('button', { name: 'Start Last.fm authorization' }));
  expect((await screen.findByRole('link', { name: 'Approve in Last.fm' })).getAttribute('href')).toBe('https://www.last.fm/api/auth/?token=request');
  expect(screen.getByLabelText('Last.fm API secret')).toMatchObject({ value: '' });
  fireEvent.click(screen.getByRole('button', { name: 'Finish Last.fm connection' }));
  await screen.findByText('Connected as listener');
  expect(mocks.post).toHaveBeenLastCalledWith('/api/me/listening-export/lastfm/finish');
});
