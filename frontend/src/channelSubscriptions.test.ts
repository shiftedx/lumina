import { describe, expect, it, vi } from 'vitest';

import { channelSubscriptionStatus, createChannelSubscriptionCommands, createSessionGuardedChannelSubscriptionCommands, findChannelBySource } from './channelSubscriptions';
import type { SourceAutomation } from './types';

function channel(overrides: Partial<SourceAutomation> = {}): SourceAutomation {
  return {
    id: 'channel-1',
    user_id: 'member-1',
    label: 'Mars Astronomy',
    source_url: 'https://www.youtube.com/@mars/videos',
    source_type: 'channel',
    cron_expression: '0 */6 * * *',
    active: true,
    auto_download: false,
    format_selection: { preset: 'best_1080p', output_container: 'mkv', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: true },
    output_profile: { base_path: null, subdir: 'astronomy', template: '%(title)s.%(ext)s', organize_by: 'uploader' },
    rules: { include_title: ['mission'], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
    duplicate_policy: 'skip_same_source',
    max_items_per_run: 12,
    max_items_per_day: 24,
    backfill_limit: 8,
    last_error: null,
    last_run_summary: {},
    ...overrides,
  } as SourceAutomation;
}

describe('channel subscription commands', () => {
  it('toggles automatic acquisition without losing hidden automation settings', async () => {
    const current = channel();
    const updated = channel({ auto_download: true });
    const adapter = {
      pause: vi.fn(),
      resume: vi.fn(),
      setAutomaticAcquisition: vi.fn().mockResolvedValue(updated),
      remove: vi.fn(),
    };

    await expect(createChannelSubscriptionCommands(adapter).setAutomaticAcquisition(current, true)).resolves.toBe(updated);

    expect(adapter.setAutomaticAcquisition).toHaveBeenCalledWith(current.id, true);
  });

  it('uses normalized source identity and narrow pause/resume commands instead of labels', async () => {
    const paused = channel({ id: 'channel-paused', active: false });
    const resumed = channel({ id: 'channel-active', active: true });
    const adapter = {
      pause: vi.fn().mockResolvedValue(paused),
      resume: vi.fn().mockResolvedValue(resumed),
      setAutomaticAcquisition: vi.fn(),
      remove: vi.fn(),
    };
    const commands = createChannelSubscriptionCommands(adapter);
    const sameLabel = channel({ id: 'channel-2', source_url: 'https://www.youtube.com/@other/videos' });

    expect(findChannelBySource([sameLabel, channel()], ' HTTPS://WWW.YOUTUBE.COM/@mars/videos/ ')).toMatchObject({ id: 'channel-1' });
    await expect(commands.pause(channel())).resolves.toBe(paused);
    await expect(commands.resume(paused)).resolves.toBe(resumed);
    expect(adapter.pause).toHaveBeenCalledWith('channel-1');
    expect(adapter.resume).toHaveBeenCalledWith('channel-paused');
    expect(adapter.setAutomaticAcquisition).not.toHaveBeenCalled();
  });

  it('preserves history until an explicit source-bound unfollow confirmation', async () => {
    const adapter = {
      pause: vi.fn(),
      resume: vi.fn(),
      setAutomaticAcquisition: vi.fn(),
      remove: vi.fn().mockResolvedValue(undefined),
    };
    const commands = createChannelSubscriptionCommands(adapter);
    const current = channel();

    const confirmation = commands.prepareUnfollow(current);
    expect(adapter.remove).not.toHaveBeenCalled();
    await expect(commands.confirmUnfollow(channel({ id: 'changed-channel' }), confirmation)).rejects.toThrow('no longer matches');
    expect(adapter.remove).not.toHaveBeenCalled();

    await expect(commands.confirmUnfollow(current, confirmation)).resolves.toBeUndefined();
    expect(adapter.remove).toHaveBeenCalledOnce();
    expect(adapter.remove).toHaveBeenCalledWith(current.id);
  });

  it('surfaces persisted failure summary and the appropriate recovery destination', () => {
    expect(channelSubscriptionStatus(channel({
      last_error: 'This channel publishes members-only content.',
      last_run_summary: { status: 'failed', discovered: 7, queued: 2, failed: 1 },
    }))).toEqual({
      state: 'attention',
      label: 'Last check failed',
      error: 'This channel publishes members-only content.',
      recovery: 'retry',
      summary: { discovered: 7, queued: 2, failed: 1 },
    });

    expect(channelSubscriptionStatus(channel({ active: false, last_error: null }))).toMatchObject({
      state: 'paused',
      label: 'Paused',
      recovery: null,
    });
  });

  it('rejects a stale async result before the next household session can dispatch it', async () => {
    let resolvePause!: (automation: SourceAutomation) => void;
    const paused = channel({ active: false });
    const pauseResult = new Promise<SourceAutomation>((resolve) => { resolvePause = resolve; });
    let currentSession = 1;
    const dispatch = vi.fn();
    const commands = createSessionGuardedChannelSubscriptionCommands({
      pause: vi.fn().mockReturnValue(pauseResult),
      resume: vi.fn(),
      setAutomaticAcquisition: vi.fn(),
      remove: vi.fn(),
    }, { capture: () => currentSession, isCurrent: (token) => token === currentSession });

    const pending = commands.pause(channel()).then(dispatch);
    currentSession = 2;
    resolvePause(paused);

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(dispatch).not.toHaveBeenCalled();
  });
});
