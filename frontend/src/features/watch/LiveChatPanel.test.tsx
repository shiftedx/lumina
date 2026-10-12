import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveChatPanel } from './LiveChatPanel';

const api = vi.hoisted(() => ({ getLiveChat: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const message = (n: number) => ({ id: `m${n}`, offset_ms: null, kind: 'message', text: `hello ${n}`, author: { name: `viewer${n}`, channel_id: null, badges: [] }, moderation: 'visible', amount: null, source_channel: null });
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });
const url = 'https://www.youtube.com/watch?v=IIMr8iqlF4M';

describe('LiveChatPanel', () => {
  beforeEach(() => { vi.useFakeTimers(); api.getLiveChat.mockReset(); });
  afterEach(() => { vi.useRealTimers(); });

  it('reads from its cursor on the server cadence and stops when chat ends', async () => {
    api.getLiveChat
      .mockResolvedValueOnce({ status: 'active', events: [message(1), message(2)], cursor: 2, poll_after_ms: 2000 })
      .mockResolvedValueOnce({ status: 'ended', events: [message(3)], cursor: 3, poll_after_ms: 2000 });
    render(<LiveChatPanel sourceUrl={url} />);
    await flush();
    expect(screen.getByText('hello 2')).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(2000); });
    await flush();
    expect(api.getLiveChat).toHaveBeenLastCalledWith(url, 2, expect.anything());
    expect(screen.getByText('hello 3')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toBe('Chat has ended.');
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(api.getLiveChat).toHaveBeenCalledTimes(2);
  });

  it('says so when a stream has no chat, and keeps trying after a network error', async () => {
    api.getLiveChat.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ status: 'unavailable', events: [], cursor: 0, poll_after_ms: 2000 });
    render(<LiveChatPanel sourceUrl={url} />);
    await flush();
    expect(screen.getByRole('status').textContent).toBe('Connecting to chat…');
    await act(async () => { vi.advanceTimersByTime(5000); });
    await flush();
    expect(screen.getByRole('status').textContent).toBe("Chat isn't available for this stream.");
  });
});
