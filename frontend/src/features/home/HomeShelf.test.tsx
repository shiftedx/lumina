import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { episodeSummary, movieSummary } from '../../test/galleryFixtures';
import { recoTitle } from '../../test/recoFixtures';
import { RecoFeedbackProvider } from '../reco/recoFeedback';
import { forgetHomeCache, rememberShelf } from './homeCache';
import { type CardSlot, FetchedShelf, HomeShelf, type ShelfActions } from './HomeShelf';
import { createVisit } from './shelfSources';

const api = vi.hoisted(() => ({ listNextUp: vi.fn(), listTitles: vi.fn(), getHomeTitleRows: vi.fn(), listHouseholdCollections: vi.fn(), getHouseholdCollection: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

/** A stand-in IntersectionObserver: nothing is near until the test says so. */
class Observer {
  static all: Observer[] = [];
  options: IntersectionObserverInit | undefined;
  constructor(private callback: IntersectionObserverCallback, options?: IntersectionObserverInit) { this.options = options; Observer.all.push(this); }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
  static near() { act(() => { for (const observer of Observer.all) observer.callback([{ isIntersecting: true } as IntersectionObserverEntry], observer as unknown as IntersectionObserver); }); }
}

const actions = (): ShelfActions => ({ onOpenTitle: vi.fn(), onOpenLibrary: vi.fn(), onOpenRoute: vi.fn() });
const shelf = (id: Parameters<typeof FetchedShelf>[0]['id'], props: Partial<Parameters<typeof FetchedShelf>[0]> = {}) =>
  <FetchedShelf actions={actions()} id={id} onStatus={vi.fn()} userId="member-1" visit={createVisit()} {...props} />;

beforeEach(() => {
  Observer.all = [];
  vi.stubGlobal('IntersectionObserver', Observer);
  Object.values(api).forEach((mock) => mock.mockReset());
  forgetHomeCache();
});
afterEach(() => vi.unstubAllGlobals());

describe('FetchedShelf', () => {
  it('makes no request until its placeholder is near, then loads once', async () => {
    api.listNextUp.mockResolvedValue([episodeSummary(2, 5)]);
    render(shelf('next_up'));
    const placeholder = screen.getByRole('region', { name: 'Next up' });
    expect(placeholder.getAttribute('aria-busy')).toBe('true');
    expect(placeholder.querySelectorAll('.h-frame')).toHaveLength(4);
    expect(api.listNextUp).not.toHaveBeenCalled();
    expect(Observer.all[0].options?.rootMargin).toBe('0px 0px 100% 0px');
    Observer.near();
    await screen.findByRole('button', { name: 'Harbor Lights, S2 · E5' });
    expect(api.listNextUp).toHaveBeenCalledTimes(1);
  });

  it('puts the reason and a menu under each recommended poster and nothing under a plain row', async () => {
    api.getHomeTitleRows.mockResolvedValue({ rows: [{ id: 'row-1', kind: 'because_you_watched', title: 'Because you watched Arrival', items: [recoTitle(1, 0), recoTitle(2, 1)] }] });
    const suppress = vi.fn().mockResolvedValue({ id: 'sup-1' });
    const wrap = (ui: React.ReactNode) => <RecoFeedbackProvider onError={vi.fn()} onExpired={vi.fn()} resetKey="m" restore={vi.fn()} suppress={suppress}>{ui}</RecoFeedbackProvider>;
    const first = render(wrap(shelf('because_you_watched')));
    Observer.near();
    await screen.findAllByRole('button', { name: /^More options for Recommended Film/ });
    expect(screen.queryByText('Like Arrival')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More options for Recommended Film 1' }));
    expect(screen.getByRole('group', { name: 'Like Arrival' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    await waitFor(() => expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'title', title_id: recoTitle(1).id })));
    expect(await screen.findByText('Hidden.')).toBeTruthy();
    first.unmount();

    api.listTitles.mockResolvedValue({ items: [movieSummary('plain', { name: 'Plain Film' })] });
    render(wrap(shelf('new_in_library')));
    Observer.near();
    await screen.findByRole('button', { name: /Plain Film/ });
    expect(screen.queryByRole('button', { name: /More options/ })).toBeNull();
  });

  it('a failed shelf says so, and Try again retries only that shelf', async () => {
    api.listNextUp.mockRejectedValueOnce(new Error('503')).mockResolvedValue([episodeSummary(2, 5)]);
    api.listTitles.mockResolvedValue({ items: [movieSummary('a', { name: 'Spirited' })] });
    render(<>{shelf('next_up')}{shelf('new_anime')}</>);
    Observer.near();
    const nextUp = await screen.findByRole('region', { name: 'Next up' });
    await waitFor(() => expect(nextUp.querySelector('.h-shelf-error')?.textContent).toContain('Lumina could not load Next up.'));
    const tryAgain = within(nextUp).getByRole('button', { name: 'Try again' });
    expect(tryAgain.hasAttribute('data-focus-item')).toBe(true); // E-I3: reachable by arrow keys and a TV remote
    fireEvent.click(tryAgain);
    await screen.findByRole('button', { name: 'Harbor Lights, S2 · E5' });
    expect(api.listNextUp).toHaveBeenCalledTimes(2);
    expect(api.listTitles).toHaveBeenCalledTimes(1);
  });

  it('an empty shelf removes itself and reports empty', async () => {
    api.listTitles.mockResolvedValue({ items: [] });
    const onStatus = vi.fn();
    render(shelf('new_anime', { onStatus }));
    Observer.near();
    await waitFor(() => expect(onStatus).toHaveBeenLastCalledWith('new_anime', 'empty'));
    expect(screen.queryByRole('region')).toBeNull();
  });

  it('See all opens the shelf\'s destination', async () => {
    api.listTitles.mockResolvedValue({ items: [movieSummary('a')] });
    const shelfActions = actions();
    render(shelf('new_anime', { actions: shelfActions }));
    Observer.near();
    const seeAll = await screen.findByRole('button', { name: 'See all' });
    expect(seeAll.hasAttribute('data-focus-item')).toBe(true); // E-I3: reachable by arrow keys and a TV remote
    fireEvent.click(seeAll);
    expect(shelfActions.onOpenRoute).toHaveBeenCalledWith({ surface: 'library', view: 'anime' });
  });

  it('remounting inside one visit (Edit then Done) reuses the loaded rows instead of refetching', async () => {
    api.listNextUp.mockResolvedValue([episodeSummary(2, 5)]);
    const visit = createVisit();
    const first = render(shelf('next_up', { visit }));
    Observer.near();
    await screen.findByRole('button', { name: 'Harbor Lights, S2 · E5' });
    first.unmount();
    render(shelf('next_up', { visit }));
    Observer.near();
    expect(screen.getByRole('button', { name: 'Harbor Lights, S2 · E5' })).toBeTruthy();
    expect(api.listNextUp).toHaveBeenCalledTimes(1);
  });
  it('paints cached rows at once on a return visit and replaces them when near', async () => {
    rememberShelf('member-1', 'next_up', [{ key: 'next_up', heading: 'Next up', shape: 'still', titles: [episodeSummary(2, 4)] }]);
    api.listNextUp.mockResolvedValue([episodeSummary(2, 5)]);
    render(shelf('next_up'));
    expect(screen.getByRole('button', { name: 'Harbor Lights, S2 · E4' })).toBeTruthy();
    Observer.near();
    await screen.findByRole('button', { name: 'Harbor Lights, S2 · E5' });
    expect(screen.queryByRole('button', { name: 'Harbor Lights, S2 · E4' })).toBeNull();
  });

  it('leaves out titles dismissed on this visit, and hides a shelf they empty', async () => {
    api.listNextUp.mockResolvedValue([episodeSummary(2, 5)]);
    const onDismiss = vi.fn();
    const { rerender } = render(shelf('next_up', { actions: { ...actions(), onDismiss } }));
    Observer.near();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Harbor Lights from Next up' }));
    expect(onDismiss).toHaveBeenCalledWith(episodeSummary(2, 5));
    rerender(shelf('next_up', { actions: { ...actions(), onDismiss }, hidden: new Set(['ep-2-5']) }));
    expect(screen.queryByRole('region', { name: 'Next up' })).toBeNull();
  });
});

describe('HomeShelf', () => {
  const slots: CardSlot[] = [];
  const render8 = () => render(<HomeShelf heading="New in your library" itemKey={String} items={[0, 1, 2, 3, 4, 5, 6, 7]} renderCard={(item, slot) => { slots[item] = slot; return <button data-focus-item type="button">{item}</button>; }} sectionKey="new_in_library" shape="poster" state="ready" />);

  it('loads the first viewport\'s visible cards at class 1, the next two at 3, the rest at 4', () => {
    render8();
    expect(slots.map((slot) => slot.priority)).toEqual([1, 1, 1, 1, 3, 3, 4, 4]);
    expect(slots.map((slot) => slot.position)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });

  it('a shelf below the first viewport loads its visible cards at class 2', () => {
    const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 5000, top: 5000, left: 0, right: 800, bottom: 5300, width: 800, height: 300, toJSON: () => ({}) } as DOMRect);
    render8();
    expect(slots.slice(0, 4).map((slot) => slot.priority)).toEqual([2, 2, 2, 2]);
    rect.mockRestore();
  });

  it('hides poster captions on a phone and keeps them on stills', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(max-width: 599px)', addEventListener() {}, removeEventListener() {} }));
    render8();
    expect(slots[0].caption).toBe(false);
    render(<HomeShelf heading="Next up" itemKey={String} items={[0]} renderCard={(item, slot) => { slots[9] = slot; return <span>{item}</span>; }} sectionKey="next_up" shape="still" state="ready" />);
    expect(slots[9].caption).toBe(true);
  });

  it('offers pointer-only row buttons on desktop, out of the tab order', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('pointer: fine'), addEventListener() {}, removeEventListener() {} }));
    vi.spyOn(Element.prototype, 'scrollWidth', 'get').mockReturnValue(2000);
    vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(800);
    render8();
    const right = screen.getByRole('button', { name: 'Scroll New in your library right' });
    expect(right.getAttribute('tabindex')).toBe('-1');
    expect(screen.getByRole('button', { name: 'Scroll New in your library left' }).hasAttribute('disabled')).toBe(true);
    vi.restoreAllMocks();
  });
});
