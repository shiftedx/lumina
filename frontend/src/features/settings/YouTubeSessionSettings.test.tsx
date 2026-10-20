// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ request: vi.fn(), owner: 'alice' }));
vi.mock('../../api', () => ({ requestJson: mocks.request }));
vi.mock('./settingsHost', () => ({ useSettingsHost: () => ({ user: { id: mocks.owner } }) }));
import { YouTubeSessionSettings } from './YouTubeSessionSettings';

beforeEach(() => { mocks.request.mockReset(); mocks.owner = 'alice'; });

it('requires a file and explicit consent, clears it after saving, and disconnects', async () => {
  mocks.request.mockImplementation(async (_url, init) => ({ status: init?.method === 'PUT' ? 'connected' : 'not_connected', updated_at: null }));
  render(<YouTubeSessionSettings />);
  await screen.findByText('Not connected');
  const connect = screen.getByRole('button', { name: 'Connect YouTube' });
  expect((connect as HTMLButtonElement).disabled).toBe(true);
  const file = new File(['synthetic-cookie-export'], 'cookies.txt', { type: 'text/plain' });
  Object.defineProperty(file, 'text', { value: async () => 'synthetic-cookie-export' });
  fireEvent.change(screen.getByLabelText('YouTube cookie file'), { target: { files: [file] } });
  expect(screen.getByText('cookies.txt')).toBeTruthy();
  expect((connect as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(connect);
  await screen.findByText('Session saved');
  expect(screen.getByText('No file selected')).toBeTruthy();
  expect(JSON.parse(mocks.request.mock.calls.find((call) => call[1]?.method === 'PUT')![1].body)).toEqual({ cookies: 'synthetic-cookie-export', consent: true });
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  expect((screen.getByLabelText('YouTube cookie file') as HTMLInputElement).value).toBe('');
  expect(screen.queryByText('synthetic-cookie-export')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect YouTube' }));
  await screen.findByText('Not connected');
  expect(mocks.request).toHaveBeenLastCalledWith('/api/me/youtube-session', expect.objectContaining({ method: 'DELETE' }));
});

it('shows reconnect guidance and retains a safe error when an import fails', async () => {
  mocks.request.mockImplementation(async (_url, init) => { if (init?.method === 'PUT') throw new Error('Export a fresh YouTube cookie file.'); return { status: 'needs_reconnect' }; });
  render(<YouTubeSessionSettings />);
  await screen.findByText(/Needs reconnecting/);
  const file = new File(['invalid'], 'cookies.txt');
  Object.defineProperty(file, 'text', { value: async () => 'invalid' });
  fireEvent.change(screen.getByLabelText('YouTube cookie file'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Replace session' }));
  expect((await screen.findByRole('alert')).textContent).toBe('Export a fresh YouTube cookie file.');
});

it('does not upload a session if the member changes while the file is being read', async () => {
  mocks.request.mockResolvedValue({ status: 'not_connected' });
  const view = render(<YouTubeSessionSettings />);
  await screen.findByText('Not connected');
  let finish: (text: string) => void = () => undefined;
  const file = new File(['pending'], 'cookies.txt');
  Object.defineProperty(file, 'text', { value: () => new Promise<string>((resolve) => { finish = resolve; }) });
  fireEvent.change(screen.getByLabelText('YouTube cookie file'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Connect YouTube' }));
  mocks.owner = 'bob'; view.rerender(<YouTubeSessionSettings />);
  await screen.findByText('Not connected'); finish('synthetic-old-member-session');
  await waitFor(() => expect(mocks.request.mock.calls.filter((call) => call[1]?.method === 'PUT')).toHaveLength(0));
});
