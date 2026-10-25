import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiBaseUrl, getOAuthProviders, startOAuth } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('OAuth session API contract', () => {
  it('discovers configured providers', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`${apiBaseUrl}/api/session/oauth/providers`);
      expect(init?.credentials).toBe('include');
      return new Response(JSON.stringify([{ id: 'google', name: 'Google' }]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(getOAuthProviders()).resolves.toEqual([{ id: 'google', name: 'Google' }]);
  });

  it('starts a provider with the remember-on-device choice', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`${apiBaseUrl}/api/session/oauth/google%2Fwork/start`);
      expect(init?.method).toBe('POST');
      expect(init?.credentials).toBe('include');
      expect(JSON.parse(String(init?.body))).toEqual({ remember_on_device: true });
      return new Response(JSON.stringify({ authorization_url: 'https://accounts.example/authorize' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(startOAuth('google/work', true)).resolves.toEqual({ authorization_url: 'https://accounts.example/authorize' });
  });
});
