import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { liveEntry } from '../../test/remoteFixtures';
import { moveFocus } from '../media/focusNav';
import { resetImageLoader } from './imageLoader';
import { StillRail, type StillRailProps } from './StillRail';

const items = Array.from({ length: 30 }, (_, index) => liveEntry(`l${index}`, { title: `Stream ${index}`, webpage_url: `https://www.youtube.com/watch?v=l${index}` }));
const cards = (container: HTMLElement) => [...container.querySelectorAll<HTMLElement>('.g-remote-card button.g-still')];

function rail(props: Partial<StillRailProps> = {}) {
  const onOpen = vi.fn();
  const onSeeAll = vi.fn();
  const view = render(<div onKeyDown={moveFocus}><StillRail heading="Gaming" items={items} kicker="30 live" onOpen={onOpen} onSeeAll={onSeeAll} railKey="gaming" {...props} /></div>);
  return { onOpen, onSeeAll, ...view };
}

beforeEach(() => resetImageLoader());

describe('StillRail', () => {
  it('heads 24 cards with the count and See all, as one tab stop', () => {
    const { container } = rail();
    expect(screen.getByRole('heading', { level: 2, name: 'Gaming' })).toBeTruthy();
    expect(screen.getByText('30 live')).toBeTruthy();
    expect(cards(container)).toHaveLength(24);
    expect(cards(container).map((card) => card.tabIndex).filter((index) => index === 0)).toHaveLength(1);
    expect(container.querySelector('section.g-rail[data-rail-key="gaming"] .g-rail-scroller[data-focus-row]')).not.toBeNull();
  });

  it('moves Left and Right between cards only, and Home and End to the ends', () => {
    const { container } = rail({ actionsFor: () => <button className="g-remote-action" data-focus-item type="button">Record</button> });
    const [first, second] = cards(container);
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(second);
    expect(second.tabIndex).toBe(0);
    expect(first.tabIndex).toBe(-1);
    fireEvent.keyDown(second, { key: 'End' });
    expect(document.activeElement).toBe(cards(container)[23]);
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Home' });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(first);
    const record = container.querySelectorAll<HTMLElement>('.g-remote-action')[1];
    record.focus();
    fireEvent.keyDown(record, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(cards(container)[2]);
  });

  it('opens See all as an in-page wall of every card and focuses its first card', async () => {
    const { onSeeAll, rerender, container } = rail();
    await userEvent.click(screen.getByRole('button', { name: 'See all' }));
    expect(onSeeAll).toHaveBeenCalledWith('gaming');
    rerender(<div onKeyDown={moveFocus}><StillRail expanded heading="Gaming" items={items} onOpen={vi.fn()} onSeeAll={onSeeAll} railKey="gaming" /></div>);
    expect(container.querySelector('.g-rail-wall')).not.toBeNull();
    expect(cards(container)).toHaveLength(30);
    expect(document.activeElement).toBe(cards(container)[0]);
    expect(screen.queryByRole('button', { name: 'See all' })).toBeNull();
  });

  it('links See all elsewhere when given an address, and hides it when everything fits', () => {
    rail({ items: items.slice(0, 5), onSeeAll: undefined, seeAllHref: '/live' });
    expect(screen.getByRole('link', { name: 'See all' }).getAttribute('href')).toBe('/live');
    document.body.innerHTML = '';
    rail({ items: items.slice(0, 5) });
    expect(screen.queryByRole('button', { name: 'See all' })).toBeNull();
  });

  it('is lazy below the fold and eager above it, and marks kept cards ENDED', () => {
    const lazy = rail().container.querySelector('section.g-rail') as HTMLElement;
    expect(lazy.classList.contains('is-lazy')).toBe(true);
    document.body.innerHTML = '';
    const { container } = rail({ eager: true, endedKeys: new Set(['https://www.youtube.com/watch?v=l1']) });
    expect(container.querySelector('section.g-rail')?.classList.contains('is-lazy')).toBe(false);
    expect(container.querySelectorAll('.g-live.is-ended')).toHaveLength(1);
  });

  it('opens a card on Enter through the native button', async () => {
    const { container, onOpen } = rail();
    cards(container)[3].focus();
    await userEvent.keyboard('{Enter}');
    expect(onOpen).toHaveBeenCalledWith(items[3]);
  });
});

describe('StillRail counts and walls (2.4.0)', () => {
  const entry = (id: string) => liveEntry(id, { title: `Stream ${id}`, webpage_url: `https://www.youtube.com/watch?v=${id}` });
  let reveal: () => void;
  beforeEach(() => {
    vi.stubGlobal('IntersectionObserver', class { constructor(cb: IntersectionObserverCallback) { reveal = () => cb([{ isIntersecting: true } as IntersectionObserverEntry], this as never); } observe() {} disconnect() {} });
  });

  it('writes the provider count with separators and hides it when null', () => {
    const { rerender } = render(<StillRail heading="Gaming" items={items} liveCount={1240} onOpen={vi.fn()} railKey="gaming" />);
    expect(screen.getByText(`${(1240).toLocaleString()} live`)).toBeTruthy();
    rerender(<StillRail heading="Gaming" items={items} liveCount={null} onOpen={vi.fn()} railKey="gaming" />);
    expect(screen.queryByText(/live$/)).toBeNull();
  });

  it('loads the next page when the sentinel shows, dedupes by id, never refetches, and stops at a null cursor', async () => {
    const pages = [{ items: [entry('a'), entry('l0')], next_cursor: 'c1' }, { items: [entry('b')], next_cursor: null }];
    const loadPage = vi.fn((cursor: string | null) => Promise.resolve(pages[cursor ? 1 : 0]));
    const { container } = render(<StillRail expanded heading="Gaming" items={items.slice(0, 3)} loadPage={loadPage} onOpen={vi.fn()} railKey="gaming" />);
    expect(container.querySelector('.g-rail-more')).not.toBeNull();
    reveal();
    await screen.findAllByRole('button', { name: /Stream a/ });
    expect(loadPage).toHaveBeenLastCalledWith(null);
    expect(cards(container)).toHaveLength(4); // l0 was already there
    reveal();
    await screen.findAllByRole('button', { name: /Stream b/ });
    expect(loadPage).toHaveBeenLastCalledWith('c1');
    expect(loadPage).toHaveBeenCalledTimes(2);
    expect(container.querySelector('.g-rail-more')).toBeNull();
    expect(cards(container)).toHaveLength(5);
  });

  it('offers Try again when a page fails, and does not page a collapsed rail', async () => {
    const loadPage = vi.fn().mockRejectedValueOnce(new Error('x')).mockResolvedValueOnce({ items: [entry('z')], next_cursor: null });
    const view = render(<StillRail expanded heading="Gaming" items={items.slice(0, 2)} loadPage={loadPage} onOpen={vi.fn()} railKey="gaming" />);
    reveal();
    fireEvent.click(await screen.findByRole('button', { name: /Try again/ }));
    await screen.findAllByRole('button', { name: /Stream z/ });
    expect(loadPage).toHaveBeenCalledTimes(2);
    view.unmount();
    render(<StillRail heading="Gaming" items={items} loadPage={loadPage} onOpen={vi.fn()} railKey="gaming" />);
    expect(document.querySelector('.g-rail-more')).toBeNull();
  });
});
