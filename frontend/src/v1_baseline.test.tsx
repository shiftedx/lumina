import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiBaseUrl, getSession, loginSession } from './api';

// Baseline: the auth + playback URL surface the real-browser
// baseline (e2e/v1_baseline.spec.ts) exercises. Desired behavior, no mocks
// beyond the fetch stub — these are the endpoints the web product cannot
// work without.

const user = {
  id: 'member-1',
  username: 'alexandria',
  display_name: 'Alexandria',
  role: 'admin',
  is_active: true,
  bio: '',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  onboarding_status: 'completed',
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('baseline API contract', () => {
  it('loginSession posts credentials to /api/session/login and returns the session state', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`${apiBaseUrl}/api/session/login`);
      expect(init?.method).toBe('POST');
      expect(init?.credentials).toBe('include');
      expect(JSON.parse(String(init?.body))).toEqual({ username: 'alexandria', password: 'secret' });
      return new Response(JSON.stringify({ user }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    const session = await loginSession({ username: 'alexandria', password: 'secret' });
    expect((session as { user: { username: string } }).user.username).toBe('alexandria');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('getSession reads /api/session/me with cookie credentials', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`${apiBaseUrl}/api/session/me`);
      expect(init?.credentials).toBe('include');
      return new Response(JSON.stringify({ user }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    const session = await getSession();
    expect(session.user.id).toBe('member-1');
  });
});
