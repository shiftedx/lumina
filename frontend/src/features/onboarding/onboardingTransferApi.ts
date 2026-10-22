import { ApiRequestError, apiBaseUrl, csrfHeaders, getSession } from '../../api';
import type { HouseholdTransferEvent, HouseholdTransferMember, HouseholdTransferProvider, HouseholdTransferRequest } from '../../types';

const MAX_STREAM_BYTES = 1024 * 1024;
const MAX_EVENT_BYTES = 256 * 1024;
const IMPORT_TIMEOUT_MS = 600000;

const validCount = (value: unknown) => Number.isInteger(value) && (value as number) >= 0;

function parseEvent(data: string): HouseholdTransferEvent {
  let parsed: unknown;
  try { parsed = JSON.parse(data); }
  catch { throw new ApiRequestError('The import sent invalid progress data.', 0); }
  if (!parsed || typeof parsed !== 'object') throw new ApiRequestError('The import sent invalid progress data.', 0);
  const event = parsed as Record<string, unknown>;
  if (event.type === 'connecting') return { type: 'connecting' };
  if (event.type === 'error' && typeof event.message === 'string' && event.message) return { type: 'error', message: event.message };
  if ((event.type === 'progress' || event.type === 'member' || event.type === 'complete') && validCount(event.completed) && validCount(event.total) && (event.completed as number) <= (event.total as number)) {
    const counts = { completed: event.completed as number, total: event.total as number };
    if (event.type === 'complete') return { type: 'complete', ...counts };
    if (event.type === 'progress' && typeof event.source_id === 'string' && event.source_id && typeof event.source_name === 'string') {
      return { type: 'progress', source_id: event.source_id, source_name: event.source_name, ...counts };
    }
    if (event.type === 'member' && event.member && typeof event.member === 'object') {
      const member = event.member as Record<string, unknown>;
      const nullableStringsValid = ['lumina_username', 'reason', 'error', 'reset_url', 'reset_expires_at'].every((key) => member[key] === undefined || member[key] === null || typeof member[key] === 'string');
      const summary = member.summary;
      const summaryValid = summary === undefined || summary === null || (typeof summary === 'object' && ['watched', 'in_progress', 'favorites', 'up_to_date', 'unmatched'].every((key) => validCount((summary as Record<string, unknown>)[key])) && ((summary as Record<string, unknown>).unmatched_names === undefined || (Array.isArray((summary as Record<string, unknown>).unmatched_names) && ((summary as Record<string, unknown>).unmatched_names as unknown[]).every((name) => typeof name === 'string'))));
      if (nullableStringsValid && summaryValid && typeof member.source_id === 'string' && member.source_id && typeof member.source_name === 'string' && typeof member.disabled === 'boolean' && (member.action === 'import' || member.action === 'create' || member.action === 'skip')) {
        return { type: 'member', member: member as unknown as HouseholdTransferMember, ...counts };
      }
    }
  }
  throw new ApiRequestError('The import sent invalid progress data.', 0);
}

async function responseError(response: Response): Promise<ApiRequestError> {
  let message = `Request failed with ${response.status}`;
  let body: unknown = null;
  try {
    body = await response.json();
    const detail = (body as { detail?: unknown; message?: unknown })?.detail;
    if (typeof detail === 'string') message = detail;
    else if (typeof (body as { message?: unknown })?.message === 'string') message = (body as { message: string }).message;
  } catch { /* Keep the status message for a non-JSON error. */ }
  return new ApiRequestError(message, response.status, null, null, body);
}

async function openStream(provider: HouseholdTransferProvider, payload: HouseholdTransferRequest, signal: AbortSignal, csrfRefreshed = false): Promise<Response> {
  const response = await fetch(`${apiBaseUrl}/api/admin/household-transfer/${provider}/import`, {
    method: 'POST', credentials: 'include', signal,
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...csrfHeaders() },
    body: JSON.stringify(payload),
  });
  if (response.ok) return response;
  const failure = await responseError(response);
  if (response.status === 403 && !csrfRefreshed && failure.message.includes('CSRF')) {
    await getSession({ signal });
    return openStream(provider, payload, signal, true);
  }
  throw failure;
}

/** Consume the finite import stream. A complete event is required; EOF alone is a failure. */
export async function streamHouseholdTransfer(
  provider: HouseholdTransferProvider,
  payload: HouseholdTransferRequest,
  onEvent: (event: HouseholdTransferEvent) => void,
  callerSignal?: AbortSignal,
): Promise<void> {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  if (callerSignal?.aborted) controller.abort();
  else callerSignal?.addEventListener('abort', cancel, { once: true });
  const timeout = globalThis.setTimeout(() => { timedOut = true; controller.abort(); }, IMPORT_TIMEOUT_MS);
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  try {
    const response = await openStream(provider, payload, controller.signal);
    if (!response.body) throw new ApiRequestError('The import did not return a progress stream.', 0);
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let received = 0;
    const expected = new Set(payload.member_ids ?? []);
    const seen = new Set<string>();
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      received += chunk.value.byteLength;
      if (received > MAX_STREAM_BYTES) throw new ApiRequestError('The import progress response was too large.', 0);
      buffer += decoder.decode(chunk.value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n');
      if (buffer.length > MAX_EVENT_BYTES && !buffer.includes('\n\n')) throw new ApiRequestError('The import sent an invalid progress event.', 0);
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = block.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
        if (data) {
          if (new TextEncoder().encode(data).byteLength > MAX_EVENT_BYTES) throw new ApiRequestError('The import sent an invalid progress event.', 0);
          const event = parseEvent(data);
          if (event.type === 'progress' && (!expected.has(event.source_id) || seen.has(event.source_id) || event.completed !== seen.size || event.total !== expected.size)) {
            throw new ApiRequestError('The import sent inconsistent progress data.', 0);
          }
          if (event.type === 'member' && (!expected.has(event.member.source_id) || seen.has(event.member.source_id) || event.completed !== seen.size + 1 || event.total !== expected.size)) {
            throw new ApiRequestError('The import sent inconsistent progress data.', 0);
          }
          if (event.type === 'error') throw new ApiRequestError(event.message, 0);
          if (event.type === 'complete') {
            if (event.completed !== event.total || event.total !== expected.size || seen.size !== expected.size) throw new ApiRequestError('The import ended with missing member results.', 0);
            onEvent(event);
            return;
          }
          if (event.type === 'member') seen.add(event.member.source_id);
          onEvent(event);
        }
        boundary = buffer.indexOf('\n\n');
      }
    }
    throw new ApiRequestError('The import connection ended before it finished.', 0);
  } catch (failure) {
    if (failure instanceof DOMException && failure.name === 'AbortError') {
      if (callerSignal?.aborted) throw new DOMException('Request cancelled.', 'AbortError');
      if (timedOut) throw new ApiRequestError('The import timed out. Please try again.', 0);
    }
    throw failure;
  } finally {
    await reader?.cancel().catch(() => undefined);
    globalThis.clearTimeout(timeout);
    callerSignal?.removeEventListener('abort', cancel);
  }
}
