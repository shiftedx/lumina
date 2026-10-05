import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from '../../api';
import { stillItem } from '../../test/galleryFixtures';
import { CHANNEL_ID, channelPage, libraryChannel, remoteEntry } from '../../test/remoteFixtures';
import type { SourceAutomation } from '../../types';
import { ChannelPage, type ChannelPageProps } from './ChannelPage';
import { rememberChannel } from './channelMention';

const api = vi.hoisted(() => ({ getChannelPage: vi.fn(), listLibraryChannels: vi.fn(), listLibrary: vi.fn(), createLiveRecording: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

function page(props: Partial<ChannelPageProps> = {}) {
  const all: ChannelPageProps = {
    channelId: CHANNEL_ID, tab: 'videos', onTabChange: vi.fn(), follows: [], onFollow: vi.fn(), commands: {} as never,
    onAutomationChange: vi.fn(), onAutomationRemoved: vi.fn(), onRecover: vi.fn(), onOpen: vi.fn(), onQueue: vi.fn(), isQueueing: () => false, onPlayLibrary: vi.fn(),
    ...props,
  };
  return { props: all, ...render(<ChannelPage {...all} />) };
}

beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); }; // jsdom has no showModal
  api.getChannelPage.mockResolvedValue(channelPage());
  api.listLibraryChannels.mockResolvedValue([]);
  api.listLibrary.mockResolvedValue({ items: [stillItem()], next_cursor: null });
});

describe('the YouTube channel page', () => {
  it('draws the header from the mention before the response, with the wall busy', async () => {
    let resolve!: (value: unknown) => void;
    api.getChannelPage.mockReturnValue(new Promise((done) => { resolve = done; }));
    rememberChannel({ id: CHANNEL_ID, name: 'Harbor Films', avatarUrl: '/api/artwork/remote/avatar' });
    const { container } = page();
    expect(screen.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeTruthy();
    expect(container.querySelector('.g-channel-wall[aria-busy="true"]')).not.toBeNull();
    resolve(channelPage());
    expect(await screen.findByText('YOUTUBE · @harborfilms · 1.2M subscribers · 845 videos')).toBeTruthy();
  });

  it('shows the tabs the response lists, In your library with its count, and switches through the address', async () => {
    api.listLibraryChannels.mockResolvedValue([libraryChannel('k', { channel_id: CHANNEL_ID, count: 12 })]);
    const { props } = page();
    const tablist = await screen.findByRole('tablist');
    await waitFor(() => expect(within(tablist).getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Videos', 'Live', 'Shorts', 'Playlists', 'In your library (12)']));
    await userEvent.click(within(tablist).getByRole('tab', { name: 'Live' }));
    expect(props.onTabChange).toHaveBeenCalledWith('live');
  });

  it('asks the API for the tab by its API name, then 120 on Show more, then notes the cap', async () => {
    const { rerender, props } = page({ tab: 'live' });
    await waitFor(() => expect(api.getChannelPage).toHaveBeenLastCalledWith(CHANNEL_ID, { tab: 'streams', limit: 60 }, expect.anything()));
    rerender(<ChannelPage {...props} tab="videos" />);
    await userEvent.click(await screen.findByRole('button', { name: 'Show more' }));
    await waitFor(() => expect(api.getChannelPage).toHaveBeenLastCalledWith(CHANNEL_ID, { tab: 'videos', limit: 120 }, expect.anything()));
    api.getChannelPage.mockResolvedValue(channelPage({ has_more: true }));
    expect(await screen.findByText('Showing the latest 120 · Open on YouTube for more')).toBeTruthy();
  });

  it('sorts Popular by views on the loaded entries', async () => {
    api.getChannelPage.mockResolvedValue(channelPage({ entries: [remoteEntry('a', { view_count: 1 }), remoteEntry('b', { view_count: 9 })], has_more: false }));
    const { container } = page();
    await screen.findByRole('combobox', { name: 'Sort' });
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Sort' }), 'popular');
    expect([...container.querySelectorAll('.g-channel-wall [data-remote-key]')].map((card) => card.getAttribute('data-remote-key'))).toEqual(['https://www.youtube.com/watch?v=b', 'https://www.youtube.com/watch?v=a']);
  });

  it('follows optimistically and reverts with a line when it fails', async () => {
    const onFollow = vi.fn().mockRejectedValue(new Error('nope'));
    page({ onFollow });
    await screen.findByText(/^YOUTUBE ·/); // Follow is enabled once the header has loaded
    const follow = screen.getByRole('button', { name: 'Follow' });
    await userEvent.click(follow);
    expect(onFollow).toHaveBeenCalledWith({ name: 'Harbor Films', url: `https://www.youtube.com/channel/${CHANNEL_ID}` });
    expect(await screen.findByText('Lumina could not follow this channel. Try again.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Follow' })).toBeTruthy();
  });

  it('opens Follow settings from Following, never unfollowing on one press', async () => {
    const followed = { id: 'f1', label: 'Harbor Films', source_url: `https://www.youtube.com/channel/${CHANNEL_ID}`, source_type: 'channel', active: true, auto_download: false, rules: {} } as unknown as SourceAutomation;
    api.getChannelPage.mockResolvedValue(channelPage({ channel: { ...channelPage().channel, follow_id: 'f1' } }));
    page({ follows: [followed], commands: { pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(), prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn() } as never });
    const following = await screen.findByRole('button', { name: 'Following' });
    expect(following.getAttribute('aria-pressed')).toBe('true');
    await userEvent.click(following);
    expect((document.querySelector('dialog.g-drawer') as HTMLDialogElement).open).toBe(true);
  });

  it('shows the live band with Watch live and Record when the channel is live', async () => {
    api.getChannelPage.mockResolvedValue(channelPage({ channel: { ...channelPage().channel, live: { webpage_url: 'https://www.youtube.com/watch?v=live1', title: 'Harbor at night', view_count: 12_412, artwork_url: '/api/artwork/remote/live1' } } }));
    const { props } = page();
    const band = await screen.findByRole('region', { name: 'Live now' });
    expect(within(band).getByText('12.4K watching')).toBeTruthy();
    expect(band.querySelector('.g-live.is-live')).not.toBeNull();
    await userEvent.click(within(band).getByRole('button', { name: 'Watch live' }));
    expect(props.onOpen).toHaveBeenCalledWith(expect.objectContaining({ webpage_url: 'https://www.youtube.com/watch?v=live1' }));
    expect(within(band).getByRole('button', { name: 'Record' })).toBeTruthy();
  });

  it.each([
    [new ApiRequestError('channel_unavailable', 404), 'This channel isn\'t available.'],
    [new ApiRequestError('YouTube did not respond in time.', 504), 'Lumina couldn\'t reach YouTube for this channel.'],
  ])('reads the right state for %s and still shows saved videos', async (error, line) => {
    api.getChannelPage.mockRejectedValue(error);
    api.listLibraryChannels.mockResolvedValue([libraryChannel('k', { channel_id: CHANNEL_ID, count: 1 })]);
    rememberChannel({ id: CHANNEL_ID, name: 'Harbor Films' });
    page();
    expect(await screen.findByText(line)).toBeTruthy();
    expect(await screen.findByRole('heading', { level: 2, name: 'In your library' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Harbor walk at dawn/ })).toBeTruthy();
  });

  it('never shows the last tab\'s videos under a new tab, and a failed switch reads the failure with Try again', async () => {
    const { rerender, props } = page();
    expect(await screen.findByRole('button', { name: /^Harbor film 1\b/ })).toBeTruthy();
    let reject!: (error: unknown) => void;
    api.getChannelPage.mockReturnValue(new Promise((_, fail) => { reject = fail; }));
    rerender(<ChannelPage {...props} tab="live" />);
    await waitFor(() => expect(api.getChannelPage).toHaveBeenLastCalledWith(CHANNEL_ID, { tab: 'streams', limit: 60 }, expect.anything()));
    expect(screen.queryByRole('button', { name: /^Harbor film 1\b/ })).toBeNull();
    reject(new ApiRequestError('YouTube did not respond in time.', 504));
    expect(await screen.findByText('Lumina couldn\'t reach YouTube for this channel.')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: /Harbor Films/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Harbor film 1\b/ })).toBeNull();
    api.getChannelPage.mockResolvedValue(channelPage({ tab: 'streams' }));
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: /^Harbor film 1\b/ })).toBeTruthy();
  });

  it('reads the failure with Try again when Show more fails, never claiming the latest 120', async () => {
    api.getChannelPage.mockResolvedValue(channelPage({ has_more: true }));
    page();
    api.getChannelPage.mockRejectedValue(new ApiRequestError('YouTube did not respond in time.', 504));
    await userEvent.click(await screen.findByRole('button', { name: 'Show more' }));
    expect(await screen.findByText('Lumina couldn\'t reach YouTube for this channel.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(screen.queryByText(/Showing the latest 120/)).toBeNull();
  });

  it('says a restricted tab needs an account and an empty tab is empty', async () => {
    api.getChannelPage.mockResolvedValue(channelPage({ tab: 'playlists', restricted: true, entries: [], has_more: false }));
    const first = page({ tab: 'playlists' });
    expect(await screen.findByText('YouTube requires an account for these videos.')).toBeTruthy();
    first.unmount();
    api.getChannelPage.mockResolvedValue(channelPage({ tab: 'shorts', entries: [], has_more: false }));
    page({ tab: 'shorts' });
    expect(await screen.findByText('No shorts on this channel.')).toBeTruthy();
  });

  it('keeps description links as text and never renders an image from outside Lumina', async () => {
    const { container } = page();
    await screen.findByText(/Films about harbors/);
    expect(container.querySelector('.g-channel-description a')).toBeNull();
    for (const image of container.querySelectorAll('img')) expect(image.getAttribute('src') ?? '').toMatch(/^(\/api\/|data:)/);
    expect(screen.getByRole('link', { name: 'Open on YouTube' }).getAttribute('rel')).toBe('noopener noreferrer');
  });
});
