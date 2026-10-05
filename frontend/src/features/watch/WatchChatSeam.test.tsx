import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { capabilities } from '../../test/remoteFixtures';
import { WatchChatSeam, type WatchChatSeamProps } from './WatchChatSeam';

vi.mock('./LiveChatPanel', () => ({ LiveChatPanel: ({ sourceUrl }: { sourceUrl: string }) => <div data-testid="live-chat">{sourceUrl}</div> }));

vi.mock('../../chatReplayRail', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../chatReplayRail')>()),
  ChatReplayRail: () => <div data-testid="replay">replay</div>,
}));

const seam = (props: Partial<WatchChatSeamProps>) => render(<WatchChatSeam currentTime={0} duration={100} ended={false} onSeek={vi.fn()} phone={false} provider="youtube" sourceIdentity="youtube:abc" sourceUrl="https://www.youtube.com/watch?v=abc" {...props} capabilities={props.capabilities ?? capabilities('youtube', 'vod')} />);

describe('WatchChatSeam', () => {
  it('shows live chat for a live YouTube or Twitch stream', () => {
    seam({ capabilities: capabilities('youtube', 'live', { chat: { live: 'available', replay: 'unavailable' } }) });
    expect(screen.getByRole('heading', { name: 'Chat' })).toBeTruthy();
    expect(screen.getByTestId('live-chat').textContent).toBe('https://www.youtube.com/watch?v=abc');
    document.body.innerHTML = '';
    seam({ provider: 'twitch', sourceUrl: 'https://www.twitch.tv/wardogs', capabilities: capabilities('twitch', 'live', { chat: { live: 'available', replay: 'unavailable' } }) });
    expect(screen.getByTestId('live-chat').textContent).toBe('https://www.twitch.tv/wardogs');
  });

  it('uses the account note for Twitch and the replay rail for a replay', () => {
    seam({ provider: 'twitch', capabilities: capabilities('twitch', 'live', { chat: { live: 'unavailable', replay: 'unavailable', live_reason: 'authentication_required' } }) });
    expect(screen.getByRole('note').textContent).toMatch(/Twitch live chat needs a Twitch account/);
    document.body.innerHTML = '';
    seam({ capabilities: capabilities('youtube', 'completed_live', { chat: { live: 'unavailable', replay: 'available' } }) });
    expect(screen.getByTestId('replay')).toBeTruthy();
  });

  it('shows nothing for video on demand without chat, and collapses on phone', () => {
    expect(seam({}).container.innerHTML).toBe('');
    document.body.innerHTML = '';
    const { container } = seam({ capabilities: capabilities('youtube', 'live', { chat: { live: 'available', replay: 'unavailable' } }), phone: true });
    expect(container.querySelector('details > summary')?.textContent).toBe('Chat');
  });
});
