import { afterEach, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({
  ApiRequestError: class ApiRequestError extends Error {
    constructor(message: string, public status: number) { super(message); }
  },
  apiBaseUrl: 'http://lumina.test',
  csrfHeaders: () => ({ 'X-CSRF-Token': 'csrf' }),
  getSession: vi.fn(),
}));

const { streamHouseholdTransfer } = await import('./features/onboarding/onboardingTransferApi');

afterEach(() => vi.unstubAllGlobals());

function stream(chunks: string[]) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) { for (const chunk of chunks) controller.enqueue(encoder.encode(chunk)); controller.close(); },
  });
}

it('parses fragmented UTF-8 SSE events and requires an explicit completion event', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(stream([
    'data: {"type":"connecting"}\r\n\r\ndata: {"type":"progress","source_id":"sam","source_name":"S',
    'am 👋","completed":0,"total":1}\r',
    '\n\r\ndata: {"type":"member","member":{"source_id":"sam","source_name":"Sam 👋","disabled":false,"action":"create"},"completed":1,"total":1}\n\ndata: {"type":"complete","completed":1,"total":1}\n\n',
  ]), { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
  vi.stubGlobal('fetch', fetchMock);
  const events: unknown[] = [];

  await streamHouseholdTransfer('plex', { token: 'token', member_ids: ['sam'] }, (event) => events.push(event));

  expect(events).toEqual([
    { type: 'connecting' },
    { type: 'progress', source_id: 'sam', source_name: 'Sam 👋', completed: 0, total: 1 },
    { type: 'member', member: { source_id: 'sam', source_name: 'Sam 👋', disabled: false, action: 'create' }, completed: 1, total: 1 },
    { type: 'complete', completed: 1, total: 1 },
  ]);
  expect(fetchMock).toHaveBeenCalledWith('http://lumina.test/api/admin/household-transfer/plex/import', expect.objectContaining({
    method: 'POST', credentials: 'include', body: JSON.stringify({ token: 'token', member_ids: ['sam'] }),
  }));
});

it('rejects unknown event shapes before exposing them to the UI', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream([
    'data: {"type":"complete-ish","completed":1,"total":1}\n\n',
  ]), { status: 200 })));
  const onEvent = vi.fn();

  await expect(streamHouseholdTransfer('plex', { token: 'token' }, onEvent)).rejects.toThrow('invalid progress data');
  expect(onEvent).not.toHaveBeenCalled();
});

it('rejects a stream that closes without a completion event', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream([
    'data: {"type":"progress","source_id":"sam","source_name":"Sam","completed":0,"total":1}\n\n',
  ]), { status: 200 })));

  await expect(streamHouseholdTransfer('plex', { token: 'token', member_ids: ['sam'] }, () => undefined)).rejects.toThrow('ended before it finished');
});

it('returns on a valid completion event without waiting for the connection to close', async () => {
  const encoder = new TextEncoder();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"type":"member","member":{"source_id":"sam","source_name":"Sam","disabled":false,"action":"create"},"completed":1,"total":1}\n\ndata: {"type":"complete","completed":1,"total":1}\n\n'));
    },
  }), { status: 200 })));

  await streamHouseholdTransfer('plex', { token: 'token', member_ids: ['sam'] }, () => undefined);
});

it('rejects completion when a selected member result is missing', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream([
    'data: {"type":"complete","completed":1,"total":1}\n\n',
  ]), { status: 200 })));

  const onEvent = vi.fn();
  await expect(streamHouseholdTransfer('plex', { token: 'token', member_ids: ['sam'] }, onEvent)).rejects.toThrow('missing member results');
  expect(onEvent).not.toHaveBeenCalled();
});

it('rejects malformed nested member summaries before rendering them', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream([
    'data: {"type":"member","member":{"source_id":"sam","source_name":"Sam","disabled":false,"action":"create","summary":{"watched":"many"}},"completed":1,"total":1}\n\n',
  ]), { status: 200 })));
  const onEvent = vi.fn();

  await expect(streamHouseholdTransfer('plex', { token: 'token', member_ids: ['sam'] }, onEvent)).rejects.toThrow('invalid progress data');
  expect(onEvent).not.toHaveBeenCalled();
});
