import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatReplayRail, LibraryCapturedChatRail } from './chatReplayRail';
import { getChatReplay, loadChatReplay } from './api';
import type { ChatReplayAsset } from './types';

vi.mock('./api', () => ({
  getChatReplay: vi.fn(),
  loadChatReplay: vi.fn(),
}));

function asset(overrides: Partial<ChatReplayAsset> = {}): ChatReplayAsset {
  return {
    source_identity: 'youtube:vid',
    status: 'ready',
    event_count: 3,
    truncated: false,
    dropped_malformed: 0,
    events: [
      { id: 'a', offset_ms: 1000, kind: 'message', text: 'first message', moderation: 'visible', author: { name: 'Ada', badges: [] } },
      { id: 'b', offset_ms: 5000, kind: 'paid_message', amount: '$5.00', text: 'super chat', moderation: 'visible', author: { name: 'Grace', badges: ['moderator'] } },
      { id: 'c', offset_ms: 9000, kind: 'message', text: '', moderation: 'deleted', author: { name: 'Troll', badges: [] } },
    ],
    ...overrides,
  };
}

function renderRail(props: Partial<React.ComponentProps<typeof ChatReplayRail>> = {}, onSeek = vi.fn()) {
  render(
    <ChatReplayRail
      currentTimeSeconds={props.currentTimeSeconds ?? 0}
      durationSeconds={props.durationSeconds ?? 600}
      onSeek={onSeek}
      sourceIdentity={props.sourceIdentity ?? 'youtube:vid'}
      sourceUrl={props.sourceUrl ?? 'https://youtu.be/vid'}
    />,
  );
  return onSeek;
}

beforeEach(() => {
  vi.mocked(getChatReplay).mockReset().mockResolvedValue(null);
  vi.mocked(loadChatReplay).mockReset();
});

describe('ChatReplayRail deliberate load', () => {
  it('does not fetch chat from the provider until the member presses Load', async () => {
    vi.mocked(loadChatReplay).mockResolvedValue(asset());
    renderRail();
    await waitFor(() => expect(getChatReplay).toHaveBeenCalled());
    expect(loadChatReplay).not.toHaveBeenCalled();

    await userEvent.click(await screen.findByRole('button', { name: /load replay chat/i }));
    await waitFor(() => expect(loadChatReplay).toHaveBeenCalledWith(
      'youtube:vid',
      expect.objectContaining({ source_url: 'https://youtu.be/vid' }),
    ));
  });
});

describe('ChatReplayRail synchronization and seeking', () => {
  it('reveals messages up to the playback position and seeks on a timestamp click', async () => {
    vi.mocked(loadChatReplay).mockResolvedValue(asset());
    const onSeek = renderRail({ currentTimeSeconds: 6 });
    await userEvent.click(await screen.findByRole('button', { name: /load replay chat/i }));

    // At 6s the first two events are revealed; the 9s event is not yet.
    expect(await screen.findByText('first message')).toBeTruthy();
    expect(screen.getByText('super chat')).toBeTruthy();
    expect(screen.queryByText('Message removed')).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: /jump to 0:05/i }));
    expect(onSeek).toHaveBeenCalledWith(5);
  });
});

describe('ChatReplayRail search and moderation', () => {
  it('searches across the whole asset and renders moderated events as placeholders', async () => {
    vi.mocked(loadChatReplay).mockResolvedValue(asset());
    renderRail({ currentTimeSeconds: 0 });
    await userEvent.click(await screen.findByRole('button', { name: /load replay chat/i }));

    const search = await screen.findByRole('searchbox', { name: /search replay chat/i });
    await userEvent.type(search, 'super');
    expect(await screen.findByText('super chat')).toBeTruthy();
    expect(screen.queryByText('first message')).toBeNull();

    await userEvent.clear(search);
    // A deleted message shows a placeholder, never its original text.
    await userEvent.click(screen.getByRole('button', { name: /^messages$/i }));
    expect(await screen.findByText('Message removed')).toBeTruthy();
  });
});

describe('ChatReplayRail degraded states never break playback', () => {
  it('shows a failed state and offers retry when the load fails, without seeking', async () => {
    vi.mocked(loadChatReplay).mockRejectedValue(new Error('network'));
    const onSeek = renderRail();
    await userEvent.click(await screen.findByRole('button', { name: /load replay chat/i }));
    expect(await screen.findByText(/could not load/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy();
    expect(onSeek).not.toHaveBeenCalled();
  });

  it('renders unavailable and empty assets without an event list', async () => {
    vi.mocked(getChatReplay).mockResolvedValue(asset({ status: 'unavailable', event_count: 0, events: [] }));
    renderRail();
    expect(await screen.findByText(/replay chat unavailable/i)).toBeTruthy();
    expect(screen.queryByRole('searchbox')).toBeNull();
  });
});

describe('LibraryCapturedChatRail surfaces a recording’s own captured chat (#110)', () => {
  const twitchRecordingItem = {
    extractor: 'twitch',
    remote_id: '424242',
    webpage_url: 'https://www.twitch.tv/somestreamer',
  };

  function renderLibraryRail(item: import('./playbackModel').CapturedChatIdentityInput = twitchRecordingItem) {
    const onSeek = vi.fn();
    render(
      <LibraryCapturedChatRail
        currentTimeSeconds={6}
        durationSeconds={600}
        item={item}
        onSeek={onSeek}
      />,
    );
    return onSeek;
  }

  it('mounts the synchronized rail keyed twitch:<stream_id> when the member’s captured asset exists', async () => {
    vi.mocked(getChatReplay).mockResolvedValue(asset({ source_identity: 'twitch:424242' }));
    renderLibraryRail();

    // The probe and the rail restore both read the member's own captured asset
    // under the recording's provider-scoped identity — never url:<channel>.
    expect(await screen.findByRole('complementary', { name: /replay chat/i })).toBeTruthy();
    expect(await screen.findByText('first message')).toBeTruthy();
    for (const [identity] of vi.mocked(getChatReplay).mock.calls) {
      expect(identity).toBe('twitch:424242');
    }
    // Restoring captured chat is a read of the durable asset, never a provider fetch.
    expect(loadChatReplay).not.toHaveBeenCalled();
  });

  it('renders nothing when no captured asset exists for this member', async () => {
    vi.mocked(getChatReplay).mockResolvedValue(null);
    renderLibraryRail();
    await waitFor(() => expect(getChatReplay).toHaveBeenCalledWith('twitch:424242', expect.anything()));
    expect(screen.queryByRole('complementary', { name: /replay chat/i })).toBeNull();
    expect(loadChatReplay).not.toHaveBeenCalled();
  });

  it('stays silent on a degraded asset instead of adding noise to ordinary playback', async () => {
    vi.mocked(getChatReplay).mockResolvedValue(asset({ status: 'unavailable', event_count: 0, events: [] }));
    renderLibraryRail();
    await waitFor(() => expect(getChatReplay).toHaveBeenCalled());
    expect(screen.queryByRole('complementary', { name: /replay chat/i })).toBeNull();
  });

  it('renders nothing when the probe fails, so playback is never interrupted', async () => {
    vi.mocked(getChatReplay).mockRejectedValue(new Error('network'));
    renderLibraryRail();
    await waitFor(() => expect(getChatReplay).toHaveBeenCalled());
    expect(screen.queryByRole('complementary', { name: /replay chat/i })).toBeNull();
  });

  it('never probes without a derivable identity', async () => {
    renderLibraryRail({ extractor: null, remote_id: null, webpage_url: null });
    await Promise.resolve();
    expect(getChatReplay).not.toHaveBeenCalled();
    expect(screen.queryByRole('complementary', { name: /replay chat/i })).toBeNull();
  });
});
