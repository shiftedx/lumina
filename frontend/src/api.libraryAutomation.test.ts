import { afterEach, describe, expect, it, vi } from 'vitest';

import { retryAdminDownload, retryAdminTask, scanLibraries, setNightHour, updateRootAutomation } from './api';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function stubFetch() {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
const call = (m: ReturnType<typeof stubFetch>) => ({ url: m.mock.calls[0][0], method: m.mock.calls[0][1]?.method, body: m.mock.calls[0][1]?.body });

describe('library automation client', () => {
  it('scanLibraries posts an empty body or one root id', async () => {
    let m = stubFetch();
    await scanLibraries();
    expect(call(m)).toEqual({ url: '/api/admin/library/automation/scan', method: 'POST', body: '{}' });
    m = stubFetch();
    await scanLibraries('r1');
    expect(call(m).body).toBe('{"root_id":"r1"}');
  });

  it('updateRootAutomation patches the encoded root route', async () => {
    const m = stubFetch();
    await updateRootAutomation('a/b', { watch: true });
    expect(call(m)).toEqual({ url: '/api/admin/library/automation/roots/a%2Fb', method: 'PATCH', body: '{"watch":true}' });
  });

  it('setNightHour patches the base route', async () => {
    const m = stubFetch();
    await setNightHour(3);
    expect(call(m)).toEqual({ url: '/api/admin/library/automation', method: 'PATCH', body: '{"night_hour":3}' });
  });

  it('retryAdminTask covers import and download routes', async () => {
    let m = stubFetch();
    await retryAdminTask('import', 'x');
    expect(call(m).url).toBe('/api/admin/tasks/import/x/retry');
    m = stubFetch();
    await retryAdminDownload('d');
    expect(call(m).url).toBe('/api/admin/tasks/download/d/retry');
  });
});
