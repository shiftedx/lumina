import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ApiRequestError, refreshFollows } from './api';
import { followOutcome, SUBSCRIPTION_FEED_LIMIT, subscriptionItems, useSubscriptionAcquisitionFeed } from './subscriptionFeed';
import type { SourceAutomation } from './types';

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  refreshFollows: vi.fn(),
}));

function channel(id: string, overrides: Partial<SourceAutomation> = {}): SourceAutomation {
  return {
    id,
    user_id: 'user-1',
    label: `Channel ${id}`,
    source_url: `https://example.test/${id}`,
    source_type: 'channel',
    cron_expression: '0 */6 * * *',
    active: true,
    auto_download: false,
    format_selection: {},
    output_profile: {},
    rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
    duplicate_policy: 'skip_same_source',
    last_run_summary: {},
    last_checked_at: '2026-01-01T00:00:00',
    feed_entries: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

const entry = (id: string) => ({ id, title: `Film ${id}`, webpage_url: `https://example.test/watch/${id}` });

function options(channels: SourceAutomation[], refreshGeneration = 0) {
  return {
    channels,
    enabled: true,
    refreshGeneration,
    sessionIdentity: 'user-1',
    captureSessionToken: () => 1,
    isSessionTokenCurrent: () => true,
    onFailure: vi.fn(),
    onSessionExpired: vi.fn(),
    onSettled: vi.fn(),
  };
}

describe('follow feed state', () => {
  it('maps each follow to an honest last-known state', () => {
    expect(followOutcome(channel('new', { last_checked_at: null })).status).toBe('loading');
    expect(followOutcome(channel('paused', { last_checked_at: null, active: false })).status).toBe('empty');
    expect(followOutcome(channel('quiet')).status).toBe('empty');
    expect(followOutcome(channel('members', { last_error: 'This channel publishes members-only content.' })).status).toBe('authentication-required');
    // A failed check keeps its last-known videos visible next to the problem.
    const failed = followOutcome(channel('down', { last_error: 'HTTP Error 503', feed_entries: [entry('a')] }));
    expect(failed).toMatchObject({ status: 'failed', message: 'HTTP Error 503' });
    expect(failed.items.map((item) => item.id)).toEqual(['a']);
  });

  it('labels mixed-source entries from their inspected provider', () => {
    const kick = channel('kick', { feed_entries: [{ ...entry('live'), capabilities: { provider: 'kick', lifecycle: 'live', can_play: false, can_acquire: false, chat: { live: 'unavailable', replay: 'unavailable' } } }] });
    expect(followOutcome(kick).items[0]).toMatchObject({ source: 'kick', source_label: 'Kick' });
    expect(followOutcome(channel('yt', { feed_entries: [entry('v')] })).items[0]).toMatchObject({ source: 'youtube' });
  });

  it('deduplicates and balances the combined feed within its bound', () => {
    const shared = [channel('one', { feed_entries: [entry('same')] }), channel('two', { feed_entries: [entry('same')] })].map(followOutcome);
    expect(subscriptionItems(shared)).toHaveLength(1);
    const many = Array.from({ length: 17 }, (_, index) => followOutcome(channel(String(index), {
      feed_entries: Array.from({ length: 12 }, (_, item) => entry(`${index}-${item}`)),
    })));
    const feed = subscriptionItems(many);
    expect(feed).toHaveLength(SUBSCRIPTION_FEED_LIMIT);
    expect(new Set(feed.map((item) => item.id?.split('-')[0]))).toHaveLength(17);
  });
});

describe('follow refresh hook', () => {
  it('reads the feed without provider work on mount', () => {
    vi.mocked(refreshFollows).mockReset();
    const { result } = renderHook(() => useSubscriptionAcquisitionFeed(options([channel('one', { feed_entries: [entry('a')] })])));
    expect(result.current.videos.map((item) => item.id)).toEqual(['a']);
    expect(refreshFollows).not.toHaveBeenCalled();
  });

  it('asks the server to refresh and settles once every follow has been checked', async () => {
    const before = channel('one');
    vi.mocked(refreshFollows).mockReset().mockResolvedValue([before]);
    const feedOptions = options([before], 1);
    const { result, rerender } = renderHook((props) => useSubscriptionAcquisitionFeed(props), { initialProps: feedOptions });

    await waitFor(() => expect(result.current.refreshing).toBe(true));
    expect(refreshFollows).toHaveBeenCalledTimes(1);
    rerender({ ...feedOptions, channels: [{ ...before, last_checked_at: '2026-01-01T00:05:00', feed_entries: [entry('fresh')] }] });

    await waitFor(() => expect(result.current.refreshing).toBe(false));
    expect(feedOptions.onSettled).toHaveBeenCalledTimes(1);
    expect(result.current.videos.map((item) => item.id)).toEqual(['fresh']);
  });

  it('hands an expired session to the caller', async () => {
    vi.mocked(refreshFollows).mockReset().mockRejectedValue(new ApiRequestError('Expired', 401, null, null));
    const feedOptions = options([channel('one')], 1);
    renderHook(() => useSubscriptionAcquisitionFeed(feedOptions));
    await waitFor(() => expect(feedOptions.onSessionExpired).toHaveBeenCalled());
  });
});

it('carries a feed entry\'s channel, views, date and the member\'s markers onto the card', () => {
  const follow = { id: 'f', label: 'Harbor', source_url: 'https://www.youtube.com/@harbor', artwork_url: null, feed_entries: [{
    id: 'abcdefghijk', title: 't', webpage_url: 'https://www.youtube.com/watch?v=abcdefghijk', view_count: 42, published_at: '2026-09-26T12:00:00',
    channel_id: 'UCabcdefghijklmnopqrstuv', channel_url: 'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv', saved_item_id: 'i1',
    progress: { position_seconds: 10, duration_seconds: 100, completed: false },
  }] } as unknown as SourceAutomation;
  const [item] = followOutcome(follow).items;
  expect(item).toMatchObject({ view_count: 42, published_at: '2026-09-26T12:00:00', uploader_id: 'UCabcdefghijklmnopqrstuv', uploader_url: 'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv', saved_item_id: 'i1', progress: { position_seconds: 10 } });
});
