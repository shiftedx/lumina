import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const recorded = vi.hoisted(() => ({ impression: vi.fn() }));
vi.mock('./recoEvents', async (importOriginal) => ({ ...(await importOriginal<typeof import('./recoEvents')>()), recordRecoImpression: recorded.impression }));

import { LIST_ID, OTHER_LIST_ID, recoAnnotation, remoteKey } from '../../test/recoFixtures';
import type { RecoAnnotation } from '../../types';
import { RecoImpressionScope, useRecoImpressionRef, useRecoImpressions } from './useRecoImpressions';

class MockObserver {
  static all: MockObserver[] = [];
  observed = new Set<Element>();
  constructor(readonly callback: IntersectionObserverCallback, readonly options?: IntersectionObserverInit) { MockObserver.all.push(this); }
  observe = vi.fn((element: Element) => { this.observed.add(element); });
  unobserve = vi.fn((element: Element) => { this.observed.delete(element); });
  disconnect = vi.fn(() => { this.observed.clear(); });
  takeRecords() { return []; }
  /** The browser reports a crossing: `ratio` of the element is visible now. */
  report(element: Element, ratio: number) {
    act(() => this.callback([{ target: element, isIntersecting: ratio > 0, intersectionRatio: ratio } as IntersectionObserverEntry], this as unknown as IntersectionObserver));
  }
}

const annotation = (n: number, patch: Partial<RecoAnnotation> = {}) => recoAnnotation({ key: remoteKey(`v${n}`), position: n, ...patch });
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });
const setVisibility = (state: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
};

function List({ annotations, listId = LIST_ID }: { annotations: Array<RecoAnnotation | null>; listId?: string | null }) {
  const observe = useRecoImpressions(listId);
  return <ul>{annotations.map((reco, index) => <li data-testid={`card-${index}`} key={index} ref={observe(reco)} />)}</ul>;
}

const card = (index: number) => document.querySelector(`[data-testid="card-${index}"]`) as Element;

beforeEach(() => {
  vi.useFakeTimers();
  MockObserver.all = [];
  recorded.impression.mockReset();
  vi.stubGlobal('IntersectionObserver', MockObserver);
  setVisibility('visible');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('useRecoImpressions', () => {
  it('uses one observer for the whole list, at a 0.5 threshold', () => {
    render(<List annotations={[annotation(1), annotation(2), annotation(3)]} />);
    expect(MockObserver.all).toHaveLength(1);
    expect(MockObserver.all[0].options?.threshold).toBe(0.5);
    expect(MockObserver.all[0].observed.size).toBe(3);
  });

  it('counts a card half visible for a full second, once', () => {
    render(<List annotations={[annotation(1)]} />);
    MockObserver.all[0].report(card(0), 0.6);
    advance(999);
    expect(recorded.impression).not.toHaveBeenCalled();
    advance(1);
    expect(recorded.impression).toHaveBeenCalledTimes(1);
    expect(recorded.impression).toHaveBeenCalledWith(expect.objectContaining({ key: remoteKey('v1'), list_id: LIST_ID }));
  });

  it('does not count a card that left before the second was up, and counts it on a full dwell later', () => {
    render(<List annotations={[annotation(1)]} />);
    MockObserver.all[0].report(card(0), 0.9);
    advance(600);
    MockObserver.all[0].report(card(0), 0.2);
    advance(5_000);
    expect(recorded.impression).not.toHaveBeenCalled();
    MockObserver.all[0].report(card(0), 0.7);
    advance(1_000);
    expect(recorded.impression).toHaveBeenCalledTimes(1);
  });

  it('does not count an overlap below half, though the browser calls it intersecting', () => {
    render(<List annotations={[annotation(1)]} />);
    MockObserver.all[0].report(card(0), 0.49);
    advance(5_000);
    expect(recorded.impression).not.toHaveBeenCalled();
  });

  it('pauses while the document is hidden and restarts the dwell when it is visible again', () => {
    render(<List annotations={[annotation(1)]} />);
    MockObserver.all[0].report(card(0), 1);
    advance(700);
    setVisibility('hidden');
    advance(10_000);
    expect(recorded.impression).not.toHaveBeenCalled();
    setVisibility('visible');
    advance(999);
    expect(recorded.impression).not.toHaveBeenCalled();
    advance(1);
    expect(recorded.impression).toHaveBeenCalledTimes(1);
  });

  it('starts no dwell for a card that became visible while the document was hidden', () => {
    setVisibility('hidden');
    render(<List annotations={[annotation(1)]} />);
    MockObserver.all[0].report(card(0), 1);
    advance(5_000);
    expect(recorded.impression).not.toHaveBeenCalled();
  });

  it('keeps each ref callback across renders, so a re-render never restarts a dwell', () => {
    const { rerender } = render(<List annotations={[annotation(1), annotation(2)]} />);
    MockObserver.all[0].report(card(0), 1);
    advance(600);
    const observer = MockObserver.all[0];
    const observed = observer.observe.mock.calls.length;
    rerender(<List annotations={[annotation(1), annotation(2)]} />);
    rerender(<List annotations={[annotation(1, { position: 5 }), annotation(2)]} />);
    expect(observer.observe.mock.calls.length).toBe(observed);
    expect(observer.unobserve).not.toHaveBeenCalled();
    advance(400);
    expect(recorded.impression).toHaveBeenCalledTimes(1);
  });

  it('observes nothing for a missing annotation or one from another list', () => {
    render(<List annotations={[null, annotation(2, { list_id: OTHER_LIST_ID }), annotation(3)]} />);
    expect(MockObserver.all[0].observed.size).toBe(1);
    expect(MockObserver.all[0].observed.has(card(2))).toBe(true);
  });

  it('observes nothing without a list id', () => {
    render(<List annotations={[annotation(1)]} listId={null} />);
    expect(MockObserver.all.every((observer) => observer.observed.size === 0)).toBe(true);
  });

  it('stops and forgets on unmount: no impression after the page left', () => {
    const { unmount } = render(<List annotations={[annotation(1)]} />);
    MockObserver.all[0].report(card(0), 1);
    advance(500);
    unmount();
    advance(5_000);
    expect(recorded.impression).not.toHaveBeenCalled();
    expect(MockObserver.all[0].disconnect).toHaveBeenCalled();
  });

  it('starts over for a new list: the old observer goes and a card of the new list is watched', () => {
    const { rerender } = render(<List annotations={[annotation(1)]} />);
    rerender(<List annotations={[annotation(1, { list_id: OTHER_LIST_ID })]} listId={OTHER_LIST_ID} />);
    expect(MockObserver.all[0].disconnect).toHaveBeenCalled();
    const current = MockObserver.all.at(-1) as MockObserver;
    current.report(card(0), 1);
    advance(1_000);
    expect(recorded.impression).toHaveBeenCalledWith(expect.objectContaining({ list_id: OTHER_LIST_ID }));
  });

  it('lets a leaf register itself through the scope (RecoFoot has no prop for it)', () => {
    function Leaf({ reco }: { reco: RecoAnnotation | null }) {
      return <div data-testid="leaf" ref={useRecoImpressionRef(reco)} />;
    }
    function Scoped() {
      const observe = useRecoImpressions(LIST_ID);
      return <RecoImpressionScope value={observe}><Leaf reco={annotation(1)} /><Leaf reco={null} /></RecoImpressionScope>;
    }
    render(<Scoped />);
    expect(MockObserver.all[0].observed.size).toBe(1);
    MockObserver.all[0].report(document.querySelector('[data-testid="leaf"]') as Element, 1);
    advance(1_000);
    expect(recorded.impression).toHaveBeenCalledTimes(1);
  });

  it('does nothing, and does not throw, where IntersectionObserver does not exist', () => {
    vi.stubGlobal('IntersectionObserver', undefined);
    expect(() => render(<List annotations={[annotation(1)]} />)).not.toThrow();
  });
});
