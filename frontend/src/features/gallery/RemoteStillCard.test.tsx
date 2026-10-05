import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FIXED_NOW, liveEntry, remoteEntry, upcomingEntry } from '../../test/remoteFixtures';
import { resetImageLoader } from './imageLoader';
import { RemoteStillCard, type RemoteStillCardProps } from './RemoteStillCard';

function card(props: Partial<RemoteStillCardProps> = {}) {
  const onOpen = vi.fn();
  const view = render(<RemoteStillCard item={remoteEntry()} onOpen={onOpen} priority={2} sizes="320px" {...props} />);
  return { onOpen, ...view };
}

beforeEach(() => { resetImageLoader(); vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(FIXED_NOW); });
afterEach(() => vi.useRealTimers());

describe('RemoteStillCard', () => {
  it('is the still card: one named button with the 16:9 art and the editorial caption', async () => {
    const { container, onOpen } = card();
    const button = screen.getByRole('button', { name: 'Harbor walk at dawn, Chan' });
    expect(button.classList.contains('g-still')).toBe(true);
    expect(button.hasAttribute('data-focus-item')).toBe(true);
    expect(container.querySelector('.g-still-card.g-remote-card.is-still .g-still-frame .g-art-still')).not.toBeNull();
    expect(container.querySelector('.g-still-title')?.textContent).toBe('Harbor walk at dawn');
    expect(container.querySelector('.g-still-caption .g-label')?.textContent).toBe('Chan · 3 days ago · 1.2M views');
    await userEvent.click(button);
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'v1' }));
  });

  it('puts the LIVE badge top-left and the viewers on the scrim, never a marker', () => {
    const { container } = card({ item: liveEntry('l1', { saved_item_id: 'i1' }) });
    expect(container.querySelector('.g-still-frame > .g-live.is-live.on-art.is-corner')?.textContent).toBe('LIVE');
    expect(container.querySelector('.g-remote-scrim')?.textContent).toBe('12.4K watching');
    expect(container.querySelector('.g-marker-triangle, .g-marker-progress')).toBeNull();
  });

  it('shows an upcoming start and ENDED when told the stream left', () => {
    const at = new Date(2026, 8, 30, 20, 30).toISOString();
    const upcoming = card({ item: upcomingEntry('u', at) });
    expect(upcoming.container.querySelector('.g-live.is-upcoming')?.textContent).toMatch(/^[A-Z]{3} /);
    upcoming.unmount();
    expect(card({ item: liveEntry('l'), ended: true }).container.querySelector('.g-live')?.textContent).toBe('ENDED');
  });

  it('marks a saved unwatched video with the small gold triangle', () => {
    const { container } = card({ item: remoteEntry('a', { saved_item_id: 'i1' }) });
    expect(container.querySelector('.g-marker-triangle.is-small')).not.toBeNull();
  });

  it('names the provider on the scrim only when asked', () => {
    expect(card().container.querySelector('.g-remote-provider')).toBeNull();
    document.body.innerHTML = '';
    expect(card({ item: liveEntry('t', { source: 'twitch' }), showProvider: true }).container.querySelector('.g-remote-provider')?.textContent).toBe('Twitch');
  });

  it('keeps its actions outside the main button, each its own stop, and reads its description', () => {
    const { container } = card({ actions: <button data-focus-item type="button">Record</button>, describedBy: 'boundary' });
    const main = container.querySelector('button.g-still') as HTMLElement;
    expect(main.getAttribute('aria-describedby')).toBe('boundary');
    expect(main.contains(screen.getByRole('button', { name: 'Record' }))).toBe(false);
    expect(container.querySelector('.g-remote-actions button[data-focus-item]')).not.toBeNull();
  });

  it('draws shorts at 9:16, the compact row and hides the caption on request', () => {
    expect(card({ shape: 'short' }).container.querySelector('.g-remote-card.is-short')).not.toBeNull();
    document.body.innerHTML = '';
    expect(card({ shape: 'compact' }).container.querySelector('.g-remote-card.is-compact .g-still-caption')).not.toBeNull();
    document.body.innerHTML = '';
    expect(card({ caption: false }).container.querySelector('.g-still-caption')).toBeNull();
  });

  it('never renders an image from outside Lumina', () => {
    const { container } = card({ item: remoteEntry('x', { artwork_url: 'https://i.ytimg.com/vi/x/hqdefault.jpg' }) });
    expect([...container.querySelectorAll('img')].map((image) => image.getAttribute('src'))).toEqual([]);
    expect(container.querySelector('.g-art')?.getAttribute('style')).toMatch(/background-color/);
  });

  it('keeps the lifecycle in text assistive tech reads, and adds none for video on demand', () => {
    const live = card({ item: liveEntry('l1') });
    expect(screen.getByRole('button', { name: /Live/i }).textContent).toMatch(/LIVE/);
    live.unmount();
    expect(card({ item: upcomingEntry('u', new Date(2026, 8, 30, 20, 30).toISOString()) }).container.querySelector('.g-live.is-upcoming')).not.toBeNull();
    document.body.innerHTML = '';
    expect(card({ item: remoteEntry('b', { title: 'VOD' }) }).container.textContent).not.toMatch(/\bLIVE\b|Upcoming/i);
  });
});
