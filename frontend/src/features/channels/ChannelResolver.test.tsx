import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChannelResolver } from './ChannelResolver';

const api = vi.hoisted(() => ({ resolveChannel: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

beforeEach(() => { vi.clearAllMocks(); window.history.replaceState(null, '', '/channel?url=x'); });

describe('the /channel?url= resolver', () => {
  it('replaces itself with the canonical page', async () => {
    api.resolveChannel.mockResolvedValue({ provider: 'youtube', channel_id: 'UCabcdefghijklmnopqrstuv' });
    const before = window.history.length;
    render(<ChannelResolver url="https://www.youtube.com/@harborfilms" />);
    expect(screen.getByRole('status', { name: 'Finding the channel' })).toBeTruthy();
    await waitFor(() => expect(window.location.pathname).toBe('/channel/youtube/UCabcdefghijklmnopqrstuv'));
    expect(window.history.length).toBe(before);
    expect(api.resolveChannel).toHaveBeenCalledTimes(1);
  });

  it('says it could not find the channel and offers Back', async () => {
    api.resolveChannel.mockRejectedValue(new Error('channel_unavailable'));
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    render(<ChannelResolver url="https://www.youtube.com/@gone" />);
    expect(await screen.findByText('Lumina couldn\'t find that channel.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(back).toHaveBeenCalled();
  });
});
