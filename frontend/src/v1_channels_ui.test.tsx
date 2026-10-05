/* @vitest-environment jsdom */

import { useState } from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { followProvider } from './channelSubscriptions';
import { ChannelsSurface, checkedAgo } from './features/channels/ChannelsSurface';
import { followOutcome } from './subscriptionFeed';
import type { SourceAutomation } from './types';

function follow(id: string, label: string, sourceUrl: string, extra: Partial<SourceAutomation> = {}): SourceAutomation {
  return {
    id, user_id: 'member-1', label, source_url: sourceUrl, source_type: 'channel', cron_expression: '*/30 * * * *', active: true,
    auto_download: false, format_selection: { preset: 'best' }, output_profile: {},
    rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
    duplicate_policy: 'skip_same_source', max_items_per_run: 5, max_items_per_day: 20, last_checked_at: '2026-01-01T12:00:00', last_error: null,
    last_run_summary: {}, feed_entries: [], created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', ...extra,
  };
}

const follows = [
  follow('yt', 'Veritasium', 'https://www.youtube.com/@veritasium', { feed_entries: [{ id: 'yt-1', title: 'Equations everywhere', webpage_url: 'https://www.youtube.com/watch?v=yt-1' }] }),
  follow('tw', 'Streamer', 'https://www.twitch.tv/streamer', {
    last_error: 'HTTP Error 503: Service Unavailable',
    feed_entries: [{ id: 'tw-1', title: 'Last-known Twitch broadcast', webpage_url: 'https://www.twitch.tv/videos/1' }],
  }),
  follow('kick', 'xQc', 'https://kick.com/xqc', {
    feed_entries: [{ id: 'kick-live', title: 'Live now', webpage_url: 'https://kick.com/xqc', capabilities: { provider: 'kick', lifecycle: 'live', can_play: false, can_acquire: false, chat: { live: 'unavailable', replay: 'unavailable' } } }],
  }),
];

function setup(initialId: string | null = null) {
  const commands = {
    pause: vi.fn(async (automation: SourceAutomation) => ({ ...automation, active: false })),
    resume: vi.fn(async (automation: SourceAutomation) => ({ ...automation, active: true })),
    setAutomaticAcquisition: vi.fn(async (automation: SourceAutomation, enabled: boolean) => ({ ...automation, auto_download: enabled })),
    prepareUnfollow: vi.fn((automation: SourceAutomation) => ({ automationId: automation.id, sourceIdentity: automation.source_url })),
    confirmUnfollow: vi.fn().mockResolvedValue(undefined),
  };
  const onRecover = vi.fn().mockResolvedValue(undefined);
  function Harness() {
    const [channels, setChannels] = useState(follows);
    const [channelId, setChannelId] = useState<string | null>(initialId);
    return <>
      {/* The browser's Back: tiles are links, so the surface itself has no back button. */}
      <button onClick={() => setChannelId(null)} type="button">Browser back</button>
      <ChannelsSurface
        channelId={channelId} channels={channels} commands={commands} isQueueing={() => false} library={[]}
        onAutomationChange={(next) => setChannels((current) => current.map((row) => row.id === next.id ? next : row))}
        onAutomationRemoved={(id) => setChannels((current) => current.filter((row) => row.id !== id))}
        onExplore={vi.fn()} onOpen={vi.fn()} onOpenChannel={setChannelId} onQueue={vi.fn()} onRecover={onRecover} onRetry={vi.fn()}
        outcomes={channels.map(followOutcome)} refreshing={false}
      />
    </>;
  }
  render(<Harness />);
  return { commands, onRecover, browser: userEvent.setup() };
}

const tiles = () => screen.getAllByRole('link', { name: /^(Veritasium|Streamer|xQc),/ });

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
});

describe('channels surface', () => {
  it('test_channels_cross_source_filter', async () => {
    const { browser, commands } = setup();
    expect(tiles()).toHaveLength(3);
    const filter = screen.getByRole('group', { name: 'Filter by source' });
    await browser.click(within(filter).getByRole('button', { name: 'Kick' }));
    expect(screen.getByRole('link', { name: /^xQc, Kick · Checked / })).not.toBeNull();
    expect(tiles()).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Equations everywhere' })).toBeNull();
    await browser.click(within(filter).getByRole('button', { name: 'All sources' }));
    expect(tiles()).toHaveLength(3);
    await browser.type(screen.getByRole('searchbox', { name: 'Find a followed channel' }), 'veri');
    expect(tiles()).toHaveLength(1);
    // Filtering is presentation only: no follow was touched.
    Object.values(commands).forEach((command) => expect(command).not.toHaveBeenCalled());
  });

  it('test_unfollow_no_artifact_delete', async () => {
    const { browser, commands } = setup('kick');
    await browser.click(screen.getByRole('button', { name: 'Follow settings' }));
    await browser.click(screen.getByRole('button', { name: 'Unfollow xQc' }));
    expect(screen.getByRole('alertdialog').textContent).toContain('Videos already in your library stay');
    await browser.click(screen.getByRole('button', { name: 'Confirm unfollow xQc' }));
    expect(commands.confirmUnfollow).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('heading', { level: 1, name: 'Subscriptions' })).not.toBeNull();
    expect(screen.queryByRole('link', { name: /^xQc/ })).toBeNull();
    expect(tiles()).toHaveLength(2);
  });

  it('test_channels_automation_explicit', async () => {
    const { browser, commands } = setup('tw');
    await browser.click(screen.getByRole('button', { name: 'Follow settings' }));
    await browser.click(screen.getByRole('button', { name: 'Pause checks for Streamer' }));
    await browser.click(screen.getByRole('button', { name: 'Resume checks for Streamer' }));
    expect(commands.setAutomaticAcquisition).not.toHaveBeenCalled();
    const checkbox = screen.getByRole('checkbox', { name: 'Download new videos automatically' }) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    expect(screen.getByText(/When on, saves Best available, up to 5 per check, 20 per day\./)).not.toBeNull();
    await browser.click(checkbox);
    expect(commands.setAutomaticAcquisition).toHaveBeenCalledWith(expect.objectContaining({ id: 'tw' }), true);
    await browser.click(screen.getByRole('button', { name: 'Done' }));
    await browser.click(screen.getByRole('button', { name: 'Browser back' }));
    expect(screen.getByRole('link', { name: 'Streamer, Twitch · Last check failed · Auto-save on' })).not.toBeNull();
  });

  it('test_creator_error_preserves_identity', async () => {
    const { browser } = setup();
    const tile = screen.getByRole('link', { name: 'Streamer, Twitch · Last check failed' });
    expect(tile.getAttribute('href')).toBe('/subscriptions/tw');
    expect(tile.textContent).toContain('Last check failed');
    const attention = screen.getByRole('region', { name: 'Channels needing attention' });
    expect(within(attention).getByText('Streamer')).not.toBeNull();
    expect(within(attention).getByText('HTTP Error 503: Service Unavailable')).not.toBeNull();
    cleanup();
    const { onRecover } = setup('tw');
    expect(screen.getByRole('heading', { level: 1, name: 'Streamer' })).not.toBeNull();
    expect(screen.getByRole('link', { name: /Open on Twitch/ }).getAttribute('href')).toBe('https://www.twitch.tv/streamer');
    expect(screen.getByRole('button', { name: 'Last-known Twitch broadcast' })).not.toBeNull();
    await browser.click(screen.getByRole('button', { name: 'Follow settings' }));
    await browser.click(screen.getByRole('button', { name: 'Try check again' }));
    expect(onRecover).toHaveBeenCalledWith(expect.objectContaining({ id: 'tw' }));
  });

  it('shows an honest not-found state for an unknown channel link', () => {
    setup('gone');
    expect(screen.getByText('You don’t follow this channel')).not.toBeNull();
  });

  it('derives provider and check age', () => {
    expect(['https://m.youtube.com/@a', 'https://www.twitch.tv/b', 'https://kick.com/c', 'https://soundcloud.com/d', 'https://example.test/e', 'nope'].map(followProvider))
      .toEqual(['youtube', 'twitch', 'kick', 'soundcloud', null, null]);
    const now = Date.parse('2026-01-01T12:10:00Z');
    expect(checkedAgo('2026-01-01T12:00:00', now)).toBe(checkedAgo('2026-01-01T12:00:00Z', now));
    expect(checkedAgo('2026-01-01T12:09:50Z', now)).toBe('just now');
    expect(checkedAgo(null)).toBeNull();
  });
});
