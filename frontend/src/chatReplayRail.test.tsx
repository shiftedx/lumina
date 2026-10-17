import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatEventRow, ChatReplayRail, LibraryCapturedChatRail } from './chatReplayRail';
import { getChatReplay, loadChatReplay } from './api';
import type { ChatReplayAsset, TimedChatEvent } from './types';

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

function renderEvent(emotes: unknown[], overrides: Partial<TimedChatEvent> = {}) {
  const event = {
    id: 'emotes', offset_ms: 1000, kind: 'message', text: '😀hi :wave: + :wave:!', moderation: 'visible',
    author: { name: 'Ada', badges: [] }, emotes, ...overrides,
  } as unknown as TimedChatEvent;
  const rendered = render(<ChatEventRow active={false} durationSeconds={60} event={event} onSeek={vi.fn()} />);
  return rendered.container.querySelector('.chat-event-text') as HTMLSpanElement;
}

describe('ChatEventRow custom emotes', () => {
  it('renders sorted repeated emotes among Unicode code points and restores each original span when its image fails', () => {
    const artworkUrl = '/api/artwork/emotes/emote_opaque-1';
    const text = renderEvent([
      { start: 13, end: 19, label: ':wave:', artwork_url: artworkUrl },
      { start: 4, end: 10, label: ':wave:', artwork_url: artworkUrl },
    ]);

    const images = within(text).getAllByRole('img', { name: ':wave:' });
    expect(images).toHaveLength(2);
    expect(images.map((image) => image.getAttribute('src'))).toEqual([artworkUrl, artworkUrl]);
    expect(images.every((image) => image.getAttribute('loading') === 'lazy')).toBe(true);
    expect(text.textContent).toBe('😀hi  + !');

    fireEvent.error(images[0]);
    expect(text.textContent).toBe('😀hi :wave: + !');
    fireEvent.error(images[1]);
    expect(text.textContent).toBe('😀hi :wave: + :wave:!');
  });

  it.each([
    ['external', [{ start: 4, end: 10, label: 'Wave', artwork_url: 'https://provider.example/emote.png' }]],
    ['protocol-relative', [{ start: 4, end: 10, label: 'Wave', artwork_url: '//provider.example/emote.png' }]],
    ['data', [{ start: 4, end: 10, label: 'Wave', artwork_url: 'data:image/png;base64,abc' }]],
    ['overlapping', [
      { start: 4, end: 10, label: 'Wave', artwork_url: '/api/artwork/remote/a' },
      { start: 9, end: 12, label: 'Overlap', artwork_url: '/api/artwork/remote/b' },
    ]],
    ['fractional', [{ start: 4.5, end: 10, label: 'Wave', artwork_url: '/api/artwork/remote/a' }]],
    ['out-of-range', [{ start: 4, end: 100, label: 'Wave', artwork_url: '/api/artwork/remote/a' }]],
    ['null among spans', [{ start: 13, end: 19, label: 'Wave', artwork_url: '/api/artwork/remote/a' }, null]],
    ['non-string URL', [{ start: 4, end: 10, label: 'Wave', artwork_url: 42 }]],
  ])('rejects %s emote metadata and preserves the full fallback text', (_case, emotes) => {
    const text = renderEvent(emotes);
    expect(within(text).queryByRole('img')).toBeNull();
    expect(text.textContent).toBe('😀hi :wave: + :wave:!');
  });

  it('rejects more than 32 emote spans', () => {
    const fallback = ':x:'.repeat(33);
    const emotes = Array.from({ length: 33 }, (_, index) => ({
      start: index * 3, end: index * 3 + 3, label: 'x', artwork_url: `/api/artwork/remote/e${index}`,
    }));
    const text = renderEvent(emotes, { text: fallback });
    expect(within(text).queryByRole('img')).toBeNull();
    expect(text.textContent).toBe(fallback);
  });

  it('accepts the existing remote-artwork route for compatible stored events', () => {
    const text = renderEvent([{ start: 4, end: 10, label: ':wave:', artwork_url: '/api/artwork/remote/legacy' }]);
    expect(within(text).getByRole('img', { name: ':wave:' })).toBeTruthy();
  });

  it('retries a failed emote when the server remaps its span to a new opaque artwork URL', () => {
    const base: TimedChatEvent = {
      id: 'retry', offset_ms: 1000, kind: 'message', text: ':wave:', moderation: 'visible', author: { name: 'Ada', badges: [] },
      emotes: [{ start: 0, end: 6, label: ':wave:', artwork_url: '/api/artwork/emotes/old' }],
    };
    const view = render(<ChatEventRow active={false} durationSeconds={60} event={base} onSeek={vi.fn()} />);
    fireEvent.error(screen.getByRole('img', { name: ':wave:' }));
    expect(screen.queryByRole('img', { name: ':wave:' })).toBeNull();

    view.rerender(<ChatEventRow
      active={false}
      durationSeconds={60}
      event={{ ...base, emotes: [{ start: 0, end: 6, label: ':wave:', artwork_url: '/api/artwork/emotes/new' }] }}
      onSeek={vi.fn()}
    />);
    expect(screen.getByRole('img', { name: ':wave:' }).getAttribute('src')).toBe('/api/artwork/emotes/new');
  });

  it.each([
    ['empty', ''],
    ['oversized', '😀'.repeat(129)],
    ['different from the fallback span', 'Wave'],
  ])('rejects a %s emote label', (_case, label) => {
    const text = renderEvent([{ start: 4, end: 10, label, artwork_url: '/api/artwork/emotes/a' }]);
    expect(within(text).queryByRole('img')).toBeNull();
    expect(text.textContent).toBe('😀hi :wave: + :wave:!');
  });

  it('never renders emotes or original text for a moderated message', () => {
    const text = renderEvent(
      [{ start: 4, end: 10, label: 'Wave', artwork_url: '/api/artwork/remote/a' }],
      { moderation: 'deleted', text: 'secret :wave:' },
    );
    expect(within(text).queryByRole('img')).toBeNull();
    expect(text.textContent).toBe('Message removed');
    expect(text.textContent).not.toContain('secret');
  });
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

  it('searches the full fallback text even when a matching emote is rendered as an image', async () => {
    vi.mocked(loadChatReplay).mockResolvedValue(asset({
      event_count: 1,
      events: [{
        id: 'emote', offset_ms: 0, kind: 'message', text: 'hello :wave:', moderation: 'visible', author: { name: 'Ada', badges: [] },
        emotes: [{ start: 6, end: 12, label: ':wave:', artwork_url: '/api/artwork/emotes/wave' }],
      }],
    }));
    renderRail();
    await userEvent.click(await screen.findByRole('button', { name: /load replay chat/i }));
    expect(await screen.findByRole('img', { name: ':wave:' })).toBeTruthy();

    await userEvent.type(screen.getByRole('searchbox', { name: /search replay chat/i }), ':wave:');
    expect(screen.getByRole('img', { name: ':wave:' })).toBeTruthy();
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
