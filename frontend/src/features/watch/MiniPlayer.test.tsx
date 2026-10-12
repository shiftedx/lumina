import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MiniPlayer } from './MiniPlayer';

function Host({ onExpand = vi.fn(), onClose = vi.fn() }) {
  return <div className="watch-surface watch-mini" id="mini-player"><video data-testid="media" /><MiniPlayer onClose={onClose} onExpand={onExpand} subtitle="S2 · E4" title="The Long Walk" /></div>;
}
afterEach(() => { vi.useRealTimers(); document.documentElement.style.removeProperty('--g-toast-offset'); });

describe('MiniPlayer', () => {
  it('is a labelled region with the serif title, sub-line and its three buttons', () => {
    render(<Host />);
    expect(screen.getByRole('region', { name: 'Mini player: The Long Walk' })).toBeTruthy();
    expect(screen.getByText('The Long Walk').classList.contains('mini-player-title')).toBe(true);
    expect(screen.getByText('Paused')).toBeTruthy();
    for (const name of ['Play', 'Expand player', 'Close player']) expect(screen.getByRole('button', { name })).toBeTruthy();
  });

  it('never opens a second media element', async () => {
    render(<Host />);
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
    await userEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(play).toHaveBeenCalledOnce();
    expect(document.querySelectorAll('video, audio')).toHaveLength(1);
  });

  it('throttles the progress line to 4 Hz', () => {
    vi.useFakeTimers();
    render(<Host />);
    const media = screen.getByTestId('media') as HTMLVideoElement;
    Object.defineProperty(media, 'duration', { configurable: true, value: 100 });
    const line = document.querySelector<HTMLElement>('.mini-player-progress')!;
    const writes = vi.spyOn(line.style, 'setProperty');
    for (let t = 1; t <= 20; t += 1) { Object.defineProperty(media, 'currentTime', { configurable: true, value: t }); fireEvent.timeUpdate(media); act(() => { vi.advanceTimersByTime(50); }); }
    expect(writes.mock.calls.length).toBeGreaterThanOrEqual(3); // a handler that never writes would otherwise pass
    expect(writes.mock.calls.length).toBeLessThanOrEqual(5); // 1 s of events at 20 Hz → at most 4 writes (+1 leading)
    expect(line.getAttribute('aria-hidden')).toBe('true');
  });

  it('moves the toast region above the card and back', () => {
    let fire: (height: number) => void = () => undefined;
    vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { fire = (height) => callback([{ borderBoxSize: [{ blockSize: height }], contentRect: { height } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver); } observe() {} disconnect() {} });
    const { unmount } = render(<Host />);
    act(() => fire(260));
    expect(document.documentElement.style.getPropertyValue('--g-toast-offset')).toBe('272px');
    unmount();
    expect(document.documentElement.style.getPropertyValue('--g-toast-offset')).toBe('');
    vi.unstubAllGlobals();
  });

  it('expands from the title and closes by button, never by gesture', async () => {
    const onExpand = vi.fn(); const onClose = vi.fn();
    render(<Host onClose={onClose} onExpand={onExpand} />);
    await userEvent.click(screen.getByRole('button', { name: 'Expand player' }));
    expect(onExpand).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole('button', { name: 'Close player' }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
