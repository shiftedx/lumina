import { afterEach, expect, it, vi } from 'vitest';

import { ApiRequestError, loginSession, updateUser } from './api';

afterEach(() => vi.unstubAllGlobals());

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const csrfRejected = () => json(403, { detail: 'CSRF token missing or invalid.' });
const user = { id: 'u1', username: 'local' };

it('sends the session CSRF token on mutations and refreshes it once on rejection', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(json(200, { user, csrf_token: 'old' }))
    .mockResolvedValueOnce(csrfRejected())
    .mockResolvedValueOnce(json(200, { user, csrf_token: 'new' }))
    .mockResolvedValueOnce(json(200, user));
  vi.stubGlobal('fetch', fetchMock);

  await loginSession({ username: 'local', password: 'x' });
  await updateUser('u1', { display_name: 'Changed' });

  const headers = (call: number) => (fetchMock.mock.calls[call][1] as RequestInit).headers as Record<string, string>;
  expect(headers(1)['X-CSRF-Token']).toBe('old');
  expect(fetchMock.mock.calls[2][0]).toBe('/api/session/me');
  expect(headers(3)['X-CSRF-Token']).toBe('new');
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

it('test_csrf_expiry_one_refresh: an expired session asks for sign-in without repeating the mutation', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(csrfRejected())
    .mockResolvedValueOnce(json(401, { detail: 'Authentication required' }));
  vi.stubGlobal('fetch', fetchMock);

  const failure = await updateUser('u1', { display_name: 'Changed' }).catch((error: unknown) => error);

  expect(failure).toBeInstanceOf(ApiRequestError);
  expect((failure as ApiRequestError).status).toBe(401);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls.filter(([url]) => url !== '/api/session/me')).toHaveLength(1);
});

it('does not loop when a refreshed token is still rejected', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(csrfRejected())
    .mockResolvedValueOnce(json(200, { user, csrf_token: 't' }))
    .mockResolvedValueOnce(csrfRejected());
  vi.stubGlobal('fetch', fetchMock);

  await expect(updateUser('u1', {})).rejects.toMatchObject({ status: 403 });
  expect(fetchMock).toHaveBeenCalledTimes(3);
});
