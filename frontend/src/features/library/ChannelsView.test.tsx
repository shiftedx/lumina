import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CHANNEL_ID, FIXED_NOW, libraryChannel } from '../../test/remoteFixtures';
import { ChannelsView, LibraryViewSwitch } from './ChannelsView';

const api = vi.hoisted(() => ({ listLibraryChannels: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(FIXED_NOW);
  api.listLibraryChannels.mockResolvedValue([
    libraryChannel('a', { name: 'Harbor Films', unwatched_count: 3, avatar_url: '/api/artwork/remote/av' }),
    libraryChannel('c', { name: 'Unknown', uploader: '', channel_id: null, unwatched_count: 0, count: 1 }),
    libraryChannel('b', { name: 'Tide Talk', uploader: 'Tide Talk', channel_id: null, unwatched_count: 0, count: 1 }),
  ]);
});
afterEach(() => vi.useRealTimers());

describe('Library Channels view', () => {
  it('walls the channels with the count badge, caption, avatar and the right destination', async () => {
    const { container } = render(<ChannelsView onWallChange={vi.fn()} state="view=channels" viewSwitch={null} />);
    const harbor = await screen.findByRole('link', { name: /^Harbor Films/ });
    expect(harbor.getAttribute('href')).toBe(`/channel/youtube/${CHANNEL_ID}?tab=library`);
    expect(within(harbor).getByText('24 videos · newest 3 days ago')).toBeTruthy();
    expect(harbor.querySelector('.g-marker-count')?.textContent).toBe('3');
    expect(harbor.querySelector('.g-avatar img')?.getAttribute('src')).toBe('/api/artwork/remote/av');
    expect(harbor.getAttribute('aria-label')).toBe('Harbor Films, 24 videos, 3 unwatched');
    const tide = screen.getByRole('link', { name: /^Tide Talk/ });
    expect(tide.getAttribute('href')).toBe('/library/youtube?channel=Tide+Talk');
    expect(screen.getByRole('link', { name: /^Unknown/ }).getAttribute('href')).toBe('/library/youtube?channel=');
    expect(tide.querySelector('.g-marker-count')).toBeNull();
    for (const image of container.querySelectorAll('img')) expect(image.getAttribute('src') ?? '').toMatch(/^\/api\//);
  });

  it('reads the kicker in the singular, is silent while loading', async () => {
    api.listLibraryChannels.mockResolvedValueOnce([libraryChannel('a', { name: 'Harbor Films', newest_item_id: 'video-1', count: 1 })]);
    const { container } = render(<ChannelsView onWallChange={vi.fn()} state="view=channels" viewSwitch={null} />);
    expect(container.querySelector('.g-kicker')).toBeNull();
    await screen.findByRole('link', { name: /^Harbor Films/ });
    expect(container.querySelector('.g-kicker')?.textContent).toBe('1 channel');
  });

  it('sorts through the address and asks the server for that order', async () => {
    const onWallChange = vi.fn();
    const { rerender } = render(<ChannelsView onWallChange={onWallChange} state="view=channels" viewSwitch={null} />);
    await screen.findByRole('link', { name: /^Harbor Films/ });
    vi.useRealTimers();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Sort' }), 'name');
    expect(onWallChange).toHaveBeenCalledWith('view=channels&sort=name');
    rerender(<ChannelsView onWallChange={onWallChange} state="view=channels&sort=name" viewSwitch={null} />);
    expect(api.listLibraryChannels).toHaveBeenLastCalledWith('name');
  });

  it('keeps a failure to itself with Try again', async () => {
    api.listLibraryChannels.mockRejectedValueOnce(new Error('x'));
    render(<ChannelsView onWallChange={vi.fn()} state="view=channels" viewSwitch={null} />);
    expect(await screen.findByText('Lumina could not load your channels.')).toBeTruthy();
    vi.useRealTimers();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('link', { name: /^Harbor Films/ })).toBeTruthy();
  });

  it('switches between Videos and Channels with pressed chips', async () => {
    const onWallChange = vi.fn();
    render(<LibraryViewSwitch current="channels" onWallChange={onWallChange} />);
    expect(screen.getByRole('button', { name: 'Channels' }).getAttribute('aria-pressed')).toBe('true');
    vi.useRealTimers();
    await userEvent.click(screen.getByRole('button', { name: 'Videos' }));
    expect(onWallChange).toHaveBeenCalledWith('');
  });
});
