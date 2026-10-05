import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const prefetchRemote = vi.fn((_url: string) => Promise.resolve({ accepted: true }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), prefetchRemote: (url: string) => prefetchRemote(url) }));

import { remoteEntry } from '../../test/remoteFixtures';
import { RemoteStillCard } from './RemoteStillCard';
import { resetPlaybackIntent, usePlaybackIntent } from './usePlaybackIntent';

function Probe({ source }: { source: string | null }) {
  return <button type="button" {...usePlaybackIntent(source)}>card</button>;
}

beforeEach(() => { vi.useFakeTimers(); prefetchRemote.mockClear(); resetPlaybackIntent(); });
afterEach(() => vi.useRealTimers());

describe('usePlaybackIntent', () => {
  it('a pointer passing over a card for under 600 ms asks for nothing', () => {
    render(<Probe source="https://www.youtube.com/watch?v=a" />);
    fireEvent.pointerEnter(screen.getByRole('button'));
    act(() => { vi.advanceTimersByTime(299); });
    fireEvent.pointerLeave(screen.getByRole('button'));
    act(() => { vi.advanceTimersByTime(1000); });
    expect(prefetchRemote).not.toHaveBeenCalled();
  });

  it('600 ms of hover or focus asks once, and the same source is not asked again', () => {
    const { unmount } = render(<Probe source="https://www.youtube.com/watch?v=a" />);
    fireEvent.pointerEnter(screen.getByRole('button'));
    act(() => { vi.advanceTimersByTime(600); });
    expect(prefetchRemote).toHaveBeenCalledTimes(1);
    expect(prefetchRemote).toHaveBeenCalledWith('https://www.youtube.com/watch?v=a');
    fireEvent.pointerLeave(screen.getByRole('button'));
    fireEvent.focus(screen.getByRole('button'));
    act(() => { vi.advanceTimersByTime(600); });
    unmount();
    render(<Probe source="https://www.youtube.com/watch?v=a" />);  // the same source on another card
    fireEvent.pointerEnter(screen.getByRole('button'));
    act(() => { vi.advanceTimersByTime(600); });
    expect(prefetchRemote).toHaveBeenCalledTimes(1);
  });

  it('a sweep across many cards asks only for the one it rests on', () => {
    render(<>{['a', 'b', 'c', 'd'].map((id) => <Probe key={id} source={`https://www.youtube.com/watch?v=${id}`} />)}</>);
    for (const card of screen.getAllByRole('button')) {
      fireEvent.pointerEnter(card);
      act(() => { vi.advanceTimersByTime(100); });
      fireEvent.pointerLeave(card);
    }
    fireEvent.pointerEnter(screen.getAllByRole('button')[3]);
    act(() => { vi.advanceTimersByTime(600); });
    expect(prefetchRemote.mock.calls).toEqual([['https://www.youtube.com/watch?v=d']]);
  });

  it('a card with nothing to play asks for nothing, and unmounting cancels a pending ask', () => {
    render(<Probe source={null} />);
    fireEvent.pointerEnter(screen.getByRole('button'));
    act(() => { vi.advanceTimersByTime(600); });
    const { unmount } = render(<Probe source="https://www.youtube.com/watch?v=b" />);
    fireEvent.pointerEnter(screen.getAllByRole('button')[1]);
    unmount();
    act(() => { vi.advanceTimersByTime(600); });
    expect(prefetchRemote).not.toHaveBeenCalled();
  });

  it('a remote video card warms its own address; a playlist card does not', () => {
    const { container } = render(<RemoteStillCard item={remoteEntry()} onOpen={vi.fn()} priority={2} sizes="320px" />);
    fireEvent.pointerEnter(container.querySelector('.g-remote-card') as Element);
    act(() => { vi.advanceTimersByTime(600); });
    expect(prefetchRemote).toHaveBeenCalledWith(remoteEntry().webpage_url);
    prefetchRemote.mockClear();
    const playlist = render(<RemoteStillCard item={remoteEntry('p1', { kind: 'playlist', webpage_url: 'https://www.youtube.com/playlist?list=p1' })} onOpen={vi.fn()} priority={2} sizes="320px" />);
    fireEvent.pointerEnter(playlist.container.querySelector('.g-remote-card') as Element);
    act(() => { vi.advanceTimersByTime(600); });
    expect(prefetchRemote).not.toHaveBeenCalled();
  });
});
