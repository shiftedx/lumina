import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const recorded = vi.hoisted(() => ({ impression: vi.fn() }));
vi.mock('./recoEvents', async (importOriginal) => ({ ...(await importOriginal<typeof import('./recoEvents')>()), recordRecoImpression: recorded.impression }));

import { recoEntry } from '../../test/recoFixtures';
import { remoteEntry } from '../../test/remoteFixtures';
import { CategoryRail, ForYouRail } from './ExploreRails';
import { RecoFeedbackProvider } from './recoFeedback';

class Observer {
  static all: Observer[] = [];
  constructor(readonly callback: IntersectionObserverCallback) { Observer.all.push(this); }
  seen = new Set<Element>();
  observe(element: Element) { this.seen.add(element); }
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
}

const forYou = [
  recoEntry('f1', 0, { title: 'Picked one', uploader: 'Harbor Films' }, { reason: 'Because you finished Harbor walk at dawn' }),
  recoEntry('f2', 1, { title: 'Picked two', uploader: 'Other Channel' }, { reason: 'Something different · like Harbor walk at dawn', slot: 'explore', reason_code: 'explore' }),
];

function setup(ui: React.ReactNode) {
  const handlers = { suppress: vi.fn().mockResolvedValue({ id: 'sup-1' }), restore: vi.fn().mockResolvedValue(undefined), onExpired: vi.fn(), onError: vi.fn() };
  const view = render(<RecoFeedbackProvider resetKey="m" {...handlers}>{ui}</RecoFeedbackProvider>);
  return { ...view, ...handlers };
}
const flush = () => act(async () => { await Promise.resolve(); });

beforeEach(() => {
  Observer.all = [];
  recorded.impression.mockReset();
  vi.stubGlobal('IntersectionObserver', Observer);
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('ForYouRail', () => {
  it('is a For you rail with each pick\'s reason in its menu, not under the card', () => {
    setup(<ForYouRail items={forYou} onOpen={vi.fn()} />);
    expect(screen.getByRole('heading', { name: 'For you' })).toBeTruthy();
    expect(screen.queryByText('Because you finished Harbor walk at dawn')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More options for Picked one' }));
    expect(screen.getByRole('group', { name: 'Because you finished Harbor walk at dawn' })).toBeTruthy();
  });

  it('renders nothing for an empty list', () => {
    const { container } = setup(<ForYouRail items={[]} onOpen={vi.fn()} />);
    expect(container.textContent).toBe('');
  });

  it('takes a hidden card out of the rail and shows its Undo row above it', async () => {
    const { suppress } = setup(<ForYouRail items={forYou} onOpen={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'More options for Picked one' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    await flush();
    expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'item', source_id: 'f1', list_id: forYou[0].reco!.list_id }));
    expect(screen.getByRole('status').textContent).toBe('Hidden. Undo');
    expect(screen.queryByRole('button', { name: 'More options for Picked one' })).toBeNull();
    expect(screen.getByRole('button', { name: 'More options for Picked two' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await flush();
    expect(screen.getByRole('button', { name: 'More options for Picked one' })).toBeTruthy();
  });

  it('reports an impression for a card half visible for a second', () => {
    vi.useFakeTimers();
    setup(<ForYouRail items={forYou} onOpen={vi.fn()} />);
    const foot = document.querySelector('.g-art-menu') as Element;
    const observer = Observer.all.find((candidate) => candidate.seen.has(foot)) as Observer;
    act(() => observer.callback([{ target: foot, isIntersecting: true, intersectionRatio: 0.8 } as IntersectionObserverEntry], observer as unknown as IntersectionObserver));
    act(() => { vi.advanceTimersByTime(1_000); });
    expect(recorded.impression).toHaveBeenCalledWith(expect.objectContaining({ key: forYou[0].reco!.key }));
  });
});

describe('CategoryRail', () => {
  const entries = [remoteEntry('c1', { title: 'Plain one', uploader: 'Harbor Films' }), remoteEntry('c2', { title: 'Plain two', uploader: 'Other Channel' })];
  const rail = { key: 'popular-music', label: 'Music', entries };

  it('gives each card the menu without a reason line, and reports no impressions', () => {
    setup(<CategoryRail onOpen={vi.fn()} rail={rail} />);
    expect(screen.getByRole('heading', { name: 'Music' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'More options for Plain one' })).toBeTruthy();
    expect(document.querySelector('.reco-reason')).toBeNull();
    expect(recorded.impression).not.toHaveBeenCalled();
  });

  it('hides a card on Don\'t recommend with the channel name', async () => {
    const { suppress } = setup(<CategoryRail onOpen={vi.fn()} rail={rail} />);
    fireEvent.click(screen.getByRole('button', { name: 'More options for Plain one' }));
    fireEvent.click(screen.getByRole('menuitem', { name: "Don't recommend Harbor Films" }));
    await flush();
    expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'channel', uploader: 'Harbor Films', list_id: null }));
    expect(screen.getByRole('status').textContent).toBe('Hidden. Undo');
  });

  it('keeps the actions the surface passes (Explore\'s own) beside the menu', () => {
    setup(<CategoryRail extra={(item) => <span>Extra for {item.title}</span>} onOpen={vi.fn()} rail={rail} />);
    expect(screen.getByText('Extra for Plain one')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'More options for Plain one' })).toBeTruthy();
  });
});
