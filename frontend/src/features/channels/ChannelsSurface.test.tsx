import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CHANNEL_ID, liveEntry, liveSnapshot } from '../../test/remoteFixtures';
import type { SourceAutomation } from '../../types';
import { followOutcome } from '../../subscriptionFeed';
import { ChannelsSurface } from './ChannelsSurface';

const api = vi.hoisted(() => ({ resolveChannel: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const follow = (id: string, label: string, url: string, patch: Partial<SourceAutomation> = {}) => ({
  id, user_id: 'm', label, source_url: url, source_type: 'channel', artwork_url: '/api/artwork/remote/a', cron_expression: '0 */6 * * *', active: true,
  auto_download: false, format_selection: {}, output_profile: {}, rules: {}, duplicate_policy: 'skip_same_source', last_run_summary: {},
  last_checked_at: '2026-09-29T20:37:00Z', feed_entries: [], created_at: '', updated_at: '', ...patch,
}) as unknown as SourceAutomation;

const harbor = follow('f1', 'Harbor Films', `https://www.youtube.com/channel/${CHANNEL_ID}`);
const handle = follow('f2', 'Handle Channel', 'https://www.youtube.com/@handlechannel');
const twitch = follow('f3', 'Streamer', 'https://www.twitch.tv/streamer', { last_error: 'Twitch refused the check' });

function surface(props: Record<string, unknown> = {}) {
  const channels = (props.channels as SourceAutomation[]) ?? [harbor, handle, twitch];
  const commands = { pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(), prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn() };
  return render(
    <ChannelsSurface channelId={null} channels={channels} commands={commands as never} isQueueing={() => false} library={[]} live={liveSnapshot({ hero: [liveEntry('h', { uploader: 'Harbor Films' })] })} onAutomationChange={vi.fn()} onAutomationRemoved={vi.fn()} onExplore={vi.fn()} onOpen={vi.fn()} onOpenChannel={vi.fn()} onQueue={vi.fn()} onRecover={vi.fn()} onRetry={vi.fn()} outcomes={channels.map(followOutcome)} refreshing={false} {...props} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/subscriptions');
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
});

describe('Subscriptions', () => {
  it('shows the masthead with its kicker, the lede, and the live band from the followed hero', () => {
    surface();
    expect(screen.getByRole('heading', { level: 1, name: 'Subscriptions' })).toBeTruthy();
    expect(screen.getByText(/^3 channels · 1 live now · checked /)).toBeTruthy();
    expect(screen.getByText('Following here never subscribes you on the platform.')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: 'Live from your follows' })).toBeTruthy();
  });

  it('draws avatar tiles: YouTube to its channel page, others to their follow detail, live with the ring and badge', () => {
    surface();
    const tiles = screen.getAllByRole('link', { name: /Harbor Films|Handle Channel|Streamer/ });
    const byName = Object.fromEntries(tiles.map((tile) => [tile.getAttribute('aria-label')?.split(',')[0], tile]));
    expect(byName['Harbor Films'].getAttribute('href')).toBe(`/channel/youtube/${CHANNEL_ID}`);
    expect(byName['Handle Channel'].getAttribute('href')).toBe('/subscriptions/f2');
    expect(byName.Streamer.getAttribute('href')).toBe('/subscriptions/f3');
    expect(byName['Harbor Films'].querySelector('.g-avatar.is-live')).not.toBeNull();
    expect(byName['Harbor Films'].querySelector('.g-live.is-live')).not.toBeNull();
    expect(within(byName.Streamer).getByText(/Last check failed/)).toBeTruthy();
  });

  it('redirects a YouTube follow to its channel page, by id at once or by one resolve', async () => {
    const heard = vi.fn();
    window.addEventListener('popstate', heard);
    surface({ channelId: 'f1' });
    expect(window.location.pathname).toBe(`/channel/youtube/${CHANNEL_ID}`);
    api.resolveChannel.mockResolvedValue({ provider: 'youtube', channel_id: CHANNEL_ID });
    window.history.replaceState(null, '', '/subscriptions/f2');
    surface({ channelId: 'f2' });
    await waitFor(() => expect(api.resolveChannel).toHaveBeenCalledWith('https://www.youtube.com/@handlechannel'));
    await waitFor(() => expect(heard).toHaveBeenCalledTimes(2));
    expect(window.location.pathname).toBe(`/channel/youtube/${CHANNEL_ID}`);
    window.removeEventListener('popstate', heard);
  });

  it('shows the follow detail for Twitch with its settings in the drawer', async () => {
    surface({ channelId: 'f3' });
    expect(screen.getByRole('heading', { level: 1, name: 'Streamer' })).toBeTruthy();
    expect(screen.queryByRole('tablist')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Follow settings' }));
    expect((document.querySelector('dialog.g-drawer') as HTMLDialogElement).open).toBe(true);
  });

  it('says so when no channel is followed', () => {
    surface({ channels: [] });
    expect(screen.getByText('You\'re not following any channels yet.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Explore' })).toBeTruthy();
  });
});
