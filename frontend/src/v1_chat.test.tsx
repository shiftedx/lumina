// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getChatReplay, loadChatReplay } from './api';
import { ChatReplayRail, LibraryCapturedChatRail, SourceChatUnavailableNote } from './chatReplayRail';
import type { ChatReplayAsset, TimedChatEvent } from './types';

vi.mock('./api', async (importActual) => ({
  ...(await importActual<typeof import('./api')>()),
  getChatReplay: vi.fn(),
  loadChatReplay: vi.fn(),
}));

const event = (id: string, offset_ms: number, text: string): TimedChatEvent => ({ id, offset_ms, kind: 'message', text, moderation: 'visible', author: { name: 'Ada', badges: [] } });
const asset = (events: TimedChatEvent[]): ChatReplayAsset => ({ source_identity: 'youtube:REC1', status: 'ready', event_count: events.length, truncated: false, dropped_malformed: 0, events });

afterEach(() => { vi.mocked(getChatReplay).mockReset(); vi.mocked(loadChatReplay).mockReset(); });

describe('source chat vs household conversation', () => {
  it('test_auth_chat_unavailable_no_prompt', () => {
    const { container } = render(<SourceChatUnavailableNote provider="twitch" />);
    expect(screen.getByRole('note').textContent).toContain('Twitch live chat needs a Twitch account, which Lumina never asks for.');
    expect(container.querySelector('button, a, input')).toBeNull();
  });

  it('test_captured_chat_seeks_correct_offset', async () => {
    vi.mocked(getChatReplay).mockResolvedValue(asset([event('a', 1000, 'first'), event('b', 4000, 'second'), event('c', 9000, 'third')]));
    const onSeek = vi.fn();
    const props = { durationSeconds: 60, item: { extractor: 'youtube', remote_id: 'REC1', webpage_url: 'https://www.youtube.com/watch?v=REC1' }, onSeek };
    const { rerender } = render(<LibraryCapturedChatRail currentTimeSeconds={5} {...props} />);
    const rail = await screen.findByRole('complementary', { name: 'Replay chat from YouTube' });
    expect(rail.textContent).toContain('Captured by Lumina while recording from YouTube. Read-only');
    await screen.findByText('second');
    expect(screen.queryByText('third')).toBeNull();
    // Seeking back hides later messages; one row per message, never duplicated.
    rerender(<LibraryCapturedChatRail currentTimeSeconds={2} {...props} />);
    await waitFor(() => expect(screen.queryByText('second')).toBeNull());
    expect(screen.getAllByText('first')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Jump to 0:01' }));
    expect(onSeek).toHaveBeenCalledWith(1);
  });

  it('test_chat_payload_xss_safe', async () => {
    vi.mocked(getChatReplay).mockResolvedValue(asset([event('x', 0, '<img src=x onerror="window.pwned=1">')]));
    const { container } = render(<ChatReplayRail currentTimeSeconds={1} durationSeconds={10} onSeek={() => undefined} sourceIdentity="youtube:REC1" sourceUrl="https://www.youtube.com/watch?v=REC1" />);
    await screen.findByText('<img src=x onerror="window.pwned=1">');
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('complementary').textContent).toContain('Public chat saved by YouTube. Read-only — Lumina never posts to it.');
  });

  it('test_chat_failure_playback_continues', async () => {
    vi.mocked(getChatReplay).mockRejectedValue(new Error('offline'));
    const onSeek = vi.fn();
    const { container } = render(<LibraryCapturedChatRail currentTimeSeconds={1} durationSeconds={10} item={{ extractor: 'youtube', remote_id: 'REC1', webpage_url: null }} onSeek={onSeek} />);
    await waitFor(() => expect(getChatReplay).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');

    vi.mocked(getChatReplay).mockResolvedValue(null);
    vi.mocked(loadChatReplay).mockRejectedValue(new Error('provider down'));
    render(<ChatReplayRail currentTimeSeconds={1} durationSeconds={10} onSeek={onSeek} sourceIdentity="youtube:REC1" sourceUrl="https://www.youtube.com/watch?v=REC1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Load replay chat' }));
    expect(await screen.findByText('You can keep watching; try loading chat again later.')).toBeTruthy();
    expect(onSeek).not.toHaveBeenCalled();
  });
});
