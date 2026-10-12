import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { CHANNEL_ID } from '../../test/remoteFixtures';
import { CLAMP_CHARS, DESCRIPTION_MAX, WatchInfoColumn, type WatchInfoColumnProps } from './WatchInfoColumn';

const long = `${'Harbor lights and slow boats. '.repeat(40)}Jump to 12:34 for the storm. Shop: https://example.test/x 🌊`;
const at = long.indexOf('12:34');

function column(props: Partial<WatchInfoColumnProps> = {}) {
  const onSeek = vi.fn();
  const onFollow = vi.fn();
  const view = render(
    <WatchInfoColumn
      actions={<button type="button">Save to library</button>} badge={null} chapters={[]} channel={{ name: 'Harbor Films', id: CHANNEL_ID, followers: 2_100_000, imported: false, following: false, onFollow }}
      currentTime={0} description={long} meta="3 Oct 2025 · 1.2M views · 24:12" onSeek={onSeek} provider="YouTube" saved={false}
      timestamps={[{ start: at, end: at + 5, seconds: 754, label: '12:34' }]} title="Harbor walk at dawn" {...props}
    />,
  );
  return { onSeek, onFollow, ...view };
}

describe('WatchInfoColumn', () => {
  it('reads like a title page: kicker, serif headline, meta and a byline that links to the channel', () => {
    const { container } = column({ saved: true });
    expect(container.querySelector('.g-watch-kicker')?.textContent).toBe('YouTube · In your library');
    expect(screen.getByRole('heading', { level: 1, name: 'Harbor walk at dawn' })).toBeTruthy();
    expect(screen.getByText('3 Oct 2025 · 1.2M views · 24:12')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Harbor Films' }).getAttribute('href')).toBe(`/channel/youtube/${CHANNEL_ID}`);
    expect(screen.getByText('2.1M followers')).toBeTruthy();
  });

  it('puts the LIVE badge in the kicker and keeps the name plain text when no channel id is known', () => {
    const { container } = column({ badge: 'live', provider: 'Twitch', channel: { name: 'Streamer', id: null, followers: null, imported: false, following: true, onFollow: vi.fn() } });
    expect(container.querySelector('.g-watch-kicker .g-live.is-live.on-paper')).not.toBeNull();
    expect(screen.queryByRole('link', { name: 'Streamer' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Following' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('keeps imported media read-only', () => {
    column({ channel: { name: 'Imported media', id: null, followers: null, imported: true, following: false, onFollow: vi.fn() } });
    expect(screen.getByText('Imported · read-only')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Follow/ })).toBeNull();
  });

  it('shows the whole description with every timestamp a seek, and no links', async () => {
    const { container, onSeek } = column();
    const text = container.querySelector('.g-watch-description') as HTMLElement;
    expect(text.classList.contains('is-clamped')).toBe(true);
    expect(text.textContent).toContain('🌊');
    expect(container.querySelector('.g-watch-description a')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Seek to 12:34' }));
    expect(onSeek).toHaveBeenCalledWith(754);
    await userEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(text.classList.contains('is-clamped')).toBe(false);
    expect(screen.getByRole('button', { name: 'Less' })).toBeTruthy();
  });

  it('caps the description at 5,000 characters', () => {
    const { container } = column({ description: 'x'.repeat(6_000), timestamps: [] });
    expect(container.querySelector('.g-watch-description')?.textContent?.length).toBe(5_001);
  });

  it('gives two columns in one document distinct heading ids', () => {
    column();
    column();
    const ids = screen.getAllByRole('heading', { level: 1 }).map((heading) => heading.id);
    expect(ids[0]).not.toBe(ids[1]);
  });

  it('offers More for a short description with more lines than the clamp', () => {
    column({ description: Array.from({ length: 10 }, (_, i) => `${i}:00 Part`).join('\n'), timestamps: [] });
    expect(screen.getByRole('button', { name: 'More' })).toBeTruthy();
  });

  it('offers More only above the clamp (CLAMP_CHARS = 420, about six lines)', () => {
    const { unmount } = column({ description: 'a'.repeat(120), timestamps: [] });
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    unmount();
    column({ description: 'a'.repeat(CLAMP_CHARS + 480), timestamps: [] });
    expect(screen.getByRole('button', { name: 'More' })).toBeTruthy();
  });

  it('never cuts the description inside a surrogate pair', () => {
    const { container } = column({ description: `${'a'.repeat(DESCRIPTION_MAX - 1)}😀`, timestamps: [] });
    const text = container.querySelector('.g-watch-description')?.textContent ?? '';
    expect(/[\uD800-\uDBFF]…$/.test(text)).toBe(false);
    expect(text.endsWith('…')).toBe(true);
  });

  it('follows from the byline', async () => {
    const { onFollow } = column();
    await userEvent.click(screen.getByRole('button', { name: 'Follow' }));
    expect(onFollow).toHaveBeenCalled();
  });
});
