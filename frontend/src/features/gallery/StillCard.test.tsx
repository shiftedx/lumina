import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { stillItem } from '../../test/galleryFixtures';
import type { LibraryItem } from '../../types';
import { resetImageLoader } from './imageLoader';
import { StillCard, type StillCardProps, StillFrame, stillLabel, stillLine } from './StillCard';

const started = { position_seconds: 161, duration_seconds: 761, completed: false };
const finished = { position_seconds: 761, duration_seconds: 761, completed: true };

function card(item: LibraryItem = stillItem(), props: Partial<StillCardProps> = {}) {
  const onPlay = vi.fn();
  const view = render(<StillCard item={item} kind="video" onPlay={onPlay} priority={2} shape="still" sizes="280px" {...props} />);
  return { onPlay, ...view };
}

beforeEach(() => resetImageLoader());

describe('StillCard', () => {
  it('names a saved item by title, channel, length and where the member is in it', () => {
    expect(stillLabel(stillItem())).toBe('Harbor walk at dawn, Chan, 12:41');
    expect(stillLabel(stillItem('v', { progress: started }))).toBe('Harbor walk at dawn, Chan, 12:41, 10 minutes left');
    expect(stillLabel(stillItem('v', { progress: { ...started, position_seconds: 740 } }))).toBe('Harbor walk at dawn, Chan, 12:41, 1 minute left');
    expect(stillLabel(stillItem('v', { progress: finished }))).toBe('Harbor walk at dawn, Chan, 12:41, watched');
    expect(stillLabel(stillItem('v', { uploader: null, duration: null }))).toBe('Harbor walk at dawn');
  });

  it('captions each kind in its own words', () => {
    expect(stillLine(stillItem(), 'video')).toBe('Chan · 12:41');
    expect(stillLine(stillItem('v', { progress: finished }), 'video')).toBe('Chan · 12:41 · Watched');
    const at = '2026-09-03T12:00:00';
    expect(stillLine(stillItem('r', { kind: 'recording', duration: 8045, downloaded_at: at }), 'recording')).toBe(`Chan · ${new Date(at).toLocaleDateString(undefined, { dateStyle: 'medium' })} · 2:14:05`);
    expect(stillLine(stillItem('a', { kind: 'audio' }), 'audio')).toBe('Chan');
  });

  it('shows a progress bar only for a started, unfinished item, and never an unwatched triangle', () => {
    const fresh = card();
    expect(fresh.container.querySelector('.g-marker-progress, .g-marker-triangle')).toBeNull();
    fresh.unmount();
    const going = card(stillItem('v', { progress: started }));
    expect((going.container.querySelector('.g-marker-progress > span') as HTMLElement).style.width).toBe(`${(161 / 761) * 100}%`);
    going.unmount();
    expect(card(stillItem('v', { progress: finished })).container.querySelector('.g-marker-progress')).toBeNull();
  });

  it('plays its item, as a 16:9 still with its caption', async () => {
    const item = stillItem();
    const { onPlay, container } = card(item);
    expect(container.querySelector('.g-art-still')).not.toBeNull();
    expect(container.querySelector('.g-still-title')?.textContent).toBe('Harbor walk at dawn');
    await userEvent.click(screen.getByRole('button', { name: 'Harbor walk at dawn, Chan, 12:41' }));
    expect(onPlay).toHaveBeenCalledWith(item);
  });

  it('draws saved audio at 1:1 and leaves the caption out when asked', () => {
    const { container } = card(stillItem('a', { kind: 'audio' }), { kind: 'audio', shape: 'square', caption: false });
    expect(container.querySelector('.g-art-square')).not.toBeNull();
    expect(container.querySelector('.g-still-caption')).toBeNull();
  });

  it('offers Delete from Lumina in its More menu, focused, only when it may delete', async () => {
    expect(card().container.querySelector('.g-still-more')).toBeNull();
    document.body.innerHTML = '';
    const onDelete = vi.fn();
    card(stillItem(), { onDelete });
    const more = screen.getByRole('button', { name: 'More options for Harbor walk at dawn' });
    expect(more.getAttribute('aria-expanded')).toBe('false');
    await userEvent.click(more);
    const remove = screen.getByRole('menuitem', { name: 'Delete from Lumina' });
    expect(document.activeElement).toBe(remove);
    await userEvent.click(remove);
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 'video-1' }));
    expect(screen.queryByRole('menuitem', { name: 'Delete from Lumina' })).toBeNull();
  });

  it('offers Add to queue before Delete, and Add to queue alone when it may not delete', async () => {
    const onQueue = vi.fn();
    card(stillItem(), { onQueue, onDelete: vi.fn() });
    await userEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk at dawn' }));
    expect([...document.querySelectorAll('[role="menuitem"]')].map((item) => item.textContent)).toEqual(['Add to queue', 'Delete from Lumina']);
    await userEvent.click(screen.getByRole('menuitem', { name: 'Add to queue' }));
    expect(onQueue).toHaveBeenCalledWith(expect.objectContaining({ id: 'video-1' }));
    document.body.innerHTML = '';
    card(stillItem(), { onQueue });
    await userEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk at dawn' }));
    expect(screen.getByRole('menuitem', { name: 'Add to queue' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Delete from Lumina' })).toBeNull();
  });

  it('closes its menu on Escape and gives focus back to More', async () => {
    card(stillItem(), { onDelete: vi.fn() });
    const more = screen.getByRole('button', { name: 'More options for Harbor walk at dawn' });
    await userEvent.click(more);
    fireEvent.keyDown(screen.getByRole('menuitem', { name: 'Delete from Lumina' }), { key: 'Escape' });
    expect(screen.queryByRole('menuitem', { name: 'Delete from Lumina' })).toBeNull();
    expect(document.activeElement).toBe(more);
  });
});

describe('StillFrame', () => {
  const frame = (patch: Partial<Parameters<typeof StillFrame>[0]> = {}) => {
    const onActivate = vi.fn();
    render(
      <StillFrame
        art={null} colour={{ colour: '#3d4a3f', fromPalette: true }} data-focus-item label="Live: Night run, Channel" line="Channel · 12.4K watching"
        name="Night run" onActivate={onActivate} priority={2} shape="still" sizes="320px" {...patch}
      />,
    );
    return { onActivate, button: screen.getByRole('button', { name: 'Live: Night run, Channel' }) };
  };

  it('shows the kicker, both caption lines, the badge and the progress bar, and is named by its label', () => {
    const { onActivate, button } = frame({ badge: <span data-testid="badge" />, kicker: 'TWITCH', percent: 40 });
    expect(button.hasAttribute('data-focus-item')).toBe(true);
    expect(button.querySelector('.g-still-caption')?.textContent).toBe('TWITCHNight runChannel · 12.4K watching');
    expect(button.querySelector('[data-testid="badge"]')).not.toBeNull();
    expect((button.querySelector('.g-marker-progress > span') as HTMLElement).style.width).toBe('40%');
    fireEvent.click(button);
    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it('draws no bar without a percent and no caption when captions are off', () => {
    const { button } = frame({ caption: false });
    expect(button.querySelector('.g-marker-progress')).toBeNull();
    expect(button.querySelector('.g-still-caption')).toBeNull();
    expect(button.querySelector('.g-art-still')).not.toBeNull();
  });
});
