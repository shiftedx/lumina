import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { channelPagePath, followAppLink, openAppPath, recallChannel, rememberChannel, youtubeChannelId } from './channelMention';

const id = 'UCabcdefghijklmnopqrstuv';

describe('channel mentions', () => {
  it('finds a UC id in the id fields first, then in a /channel/ address on a YouTube host', () => {
    expect(youtubeChannelId({ uploader_id: id })).toBe(id);
    expect(youtubeChannelId({ uploader_id: '@harborfilms', channel_url: `https://www.youtube.com/channel/${id}/videos` })).toBe(id);
    expect(youtubeChannelId({ uploader_url: `https://m.youtube.com/channel/${id}` })).toBe(id);
    expect(youtubeChannelId({ uploader_url: `https://evil.com/channel/${id}` })).toBeNull();
    expect(youtubeChannelId({ uploader_id: `${id}x`, uploader_url: 'not a url' })).toBeNull();
    expect(youtubeChannelId({})).toBeNull();
  });

  it('remembers the last 64 mentions and only real channel ids', () => {
    rememberChannel({ id, name: 'Harbor Films', avatarUrl: '/api/artwork/remote/a' });
    expect(recallChannel(id)).toEqual({ id, name: 'Harbor Films', avatarUrl: '/api/artwork/remote/a' });
    rememberChannel({ id: 'nope', name: 'x' });
    expect(recallChannel('nope')).toBeNull();
    for (let n = 0; n < 64; n += 1) rememberChannel({ id: `UC${String(n).padStart(22, '0')}`, name: `c${n}` });
    expect(recallChannel(id)).toBeNull();
    expect(recallChannel(`UC${'63'.padStart(22, '0')}`)?.name).toBe('c63');
  });

  it('builds the canonical page path', () => {
    expect(channelPagePath(id)).toBe(`/channel/youtube/${id}`);
    expect(channelPagePath(id, 'videos')).toBe(`/channel/youtube/${id}`);
    expect(channelPagePath(id, 'live')).toBe(`/channel/youtube/${id}?tab=live`);
  });

  it('opens an in-app link through popstate, and leaves modified clicks to the browser', () => {
    const heard = vi.fn();
    window.addEventListener('popstate', heard);
    const { getByRole } = render(<a href={channelPagePath(id)} onClick={followAppLink}>Harbor Films</a>);
    const link = getByRole('link', { name: 'Harbor Films' });
    fireEvent.click(link, { metaKey: true });
    expect(heard).not.toHaveBeenCalled();
    fireEvent.click(link);
    expect(window.location.pathname).toBe(`/channel/youtube/${id}`);
    expect(heard).toHaveBeenCalledTimes(1);
    openAppPath('/live', true);
    expect(window.location.pathname).toBe('/live');
    expect(heard).toHaveBeenCalledTimes(2);
    window.removeEventListener('popstate', heard);
    window.history.replaceState(null, '', '/');
  });
});
