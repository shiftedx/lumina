import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { recoAnnotation, recoEntry, recoTitle } from '../../test/recoFixtures';
import type { RemoteEntry } from '../../types';
import { ArtMenu } from '../gallery/ArtMenu';
import { remoteTarget, titleTarget } from './recoModel';
import { RecoCard, RecoFeedbackProvider, RecoHiddenRows, useAutoplayEligible, useRecoFeedback, useRecoFilter, useRecoGeneration } from './recoFeedback';

const entry = recoEntry('v1', 0, { uploader: 'Harbor Films', title: 'Harbor walk' }, { reason: 'Because you finished Harbor walk at dawn' });
const flush = () => act(async () => { await Promise.resolve(); });

function Probe() {
  return <span data-testid="generation">{useRecoGeneration()}</span>;
}
function Card({ item = entry }: { item?: RemoteEntry }) {
  return (
    <RecoCard reco={item.reco} target={remoteTarget(item)}>
      <div><button data-focus-item type="button">Open Harbor walk</button><ArtMenu subject={{ kind: 'remote', item }} /></div>
    </RecoCard>
  );
}
function setup(props: Partial<Parameters<typeof RecoFeedbackProvider>[0]> = {}, children: React.ReactNode = <Card />) {
  const handlers = {
    suppress: vi.fn().mockResolvedValue({ id: 'sup-1' }),
    restore: vi.fn().mockResolvedValue(undefined),
    onExpired: vi.fn(),
    onError: vi.fn(),
  };
  const view = (resetKey: string) => (
    <RecoFeedbackProvider resetKey={resetKey} {...handlers} {...props}>{children}<Probe /></RecoFeedbackProvider>
  );
  const rendered = render(view('member-1'));
  return { ...handlers, ...rendered, rerenderWith: (resetKey: string) => rendered.rerender(view(resetKey)) };
}
const choose = async (name: string) => {
  fireEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk' }));
  fireEvent.click(screen.getByRole('menuitem', { name }));
  await flush();
};

// Only the timers the Undo window uses are faked: React's async act must still flush promises.
beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); });
afterEach(() => { vi.useRealTimers(); });

describe('RecoCard with a provider', () => {
  it('keeps the reason out of the card and in the menu, as a quiet header', () => {
    setup();
    expect(document.querySelector('.reco-reason')).toBeNull();
    expect(screen.queryByText('Because you finished Harbor walk at dawn')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk' }));
    expect(screen.getByRole('group', { name: 'Because you finished Harbor walk at dawn' })).toBeTruthy();
  });

  it('replaces the card with "Hidden. Undo" after Not interested, saving the item with its list', async () => {
    const { suppress } = setup();
    await choose('Not interested');
    expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'item', source_id: 'v1', list_id: entry.reco!.list_id, key: entry.reco!.key }));
    expect(screen.queryByRole('button', { name: 'Open Harbor walk' })).toBeNull();
    expect(screen.getByRole('status').textContent).toBe('Hidden. Undo');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Undo' }));
  });

  it('says what show-fewer did, naming the channel', async () => {
    setup();
    await choose('Show fewer from Harbor Films');
    expect(screen.getByRole('status').textContent).toBe('Showing fewer from Harbor Films. Undo');
  });

  it('Undo restores the row the choice created and focuses the card again', async () => {
    const { restore } = setup();
    await choose("Don't recommend Harbor Films");
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await flush();
    expect(restore).toHaveBeenCalledWith('sup-1');
    expect(screen.getByRole('button', { name: 'Open Harbor walk' })).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Open Harbor walk' }));
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(screen.getByRole('button', { name: 'Open Harbor walk' })).toBeTruthy();
  });

  it('when the window closes the card is gone, the app is told once, and surfaces are asked to re-fetch', async () => {
    const { onExpired } = setup();
    await choose('Not interested');
    act(() => { vi.advanceTimersByTime(8_000); });
    expect(onExpired).toHaveBeenCalledTimes(1);
    expect(onExpired).toHaveBeenCalledWith({ feedback: 'not_interested', target: remoteTarget(entry) });
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Open Harbor walk' })).toBeNull();
    expect(screen.getByTestId('generation').textContent).toBe('1');
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it('brings a gone card back when the key changes (a restore in Settings, or another member)', async () => {
    const { rerenderWith } = setup();
    await choose('Not interested');
    act(() => { vi.advanceTimersByTime(8_000); });
    rerenderWith('member-1:restored');
    expect(screen.getByRole('button', { name: 'Open Harbor walk' })).toBeTruthy();
  });

  it('clears a hidden card (not only a gone one) when the key changes', async () => {
    const { rerenderWith } = setup();
    await choose('Not interested');
    expect(screen.getByRole('status')).toBeTruthy();
    rerenderWith('member-2');
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByRole('button', { name: 'Open Harbor walk' })).toBeTruthy();
  });

  it('drops a save that resolves after the member changed', async () => {
    let finish!: (value: { id: string }) => void;
    const suppress = vi.fn(() => new Promise<{ id: string }>((resolve) => { finish = resolve; }));
    const { rerenderWith } = setup({ suppress });
    fireEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    rerenderWith('member-2');
    finish({ id: 'sup-1' });
    await flush();
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByRole('button', { name: 'Open Harbor walk' })).toBeTruthy();
  });

  it('drops a failed save that settles after the member changed', async () => {
    let fail!: (reason: Error) => void;
    const suppress = vi.fn(() => new Promise<{ id: string }>((_, reject) => { fail = reject; }));
    const { rerenderWith, onError } = setup({ suppress });
    fireEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    rerenderWith('member-2');
    fail(new Error('offline'));
    await flush();
    expect(onError).not.toHaveBeenCalled();
  });

  it('does not let an Undo that resolves after the member changed touch the new member', async () => {
    let finish!: () => void;
    const restore = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const { rerenderWith, onError } = setup({ restore });
    await choose('Not interested');
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    rerenderWith('member-2');
    await choose("Don't recommend Harbor Films");
    finish();
    await flush();
    expect(screen.getByRole('status')).toBeTruthy();
    expect(onError).not.toHaveBeenCalled();
  });

  it('keeps the card and says so when saving fails', async () => {
    const { onError } = setup({ suppress: vi.fn().mockRejectedValue(new Error('offline')) });
    await choose('Not interested');
    expect(onError).toHaveBeenCalledWith('Could not save that. Try again.');
    expect(screen.getByRole('button', { name: 'Open Harbor walk' })).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('keeps the row and says so when Undo fails', async () => {
    const { onError } = setup({ restore: vi.fn().mockRejectedValue(new Error('offline')) });
    await choose('Not interested');
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await flush();
    expect(onError).toHaveBeenCalledWith('Could not undo that. Try again.');
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('saves once when a choice is made twice while the first is still saving', async () => {
    let finish!: (value: { id: string }) => void;
    const suppress = vi.fn(() => new Promise<{ id: string }>((resolve) => { finish = resolve; }));
    setup({ suppress });
    fireEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    fireEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Not interested' }));
    expect(suppress).toHaveBeenCalledTimes(1);
    finish({ id: 'sup-1' });
    await flush();
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('gives a remote card without an annotation the feedback entries but no reason header', () => {
    const plain: RemoteEntry = { ...entry, reco: null };
    setup({}, <Card item={plain} />);
    fireEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk' }));
    expect(screen.getByRole('menuitem', { name: 'Not interested' })).toBeTruthy();
    expect(screen.queryByRole('group')).toBeNull();
  });

  it('gives a title without an annotation no foot at all', () => {
    const film = { ...recoTitle(1), reco: null };
    setup({}, <RecoCard reco={film.reco} target={titleTarget(film)}><div><button type="button">Open film</button><ArtMenu subject={{ kind: 'title', title: film }} /></div></RecoCard>);
    expect(screen.getByRole('button', { name: 'Open film' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /More options/ })).toBeNull();
  });
});

describe('without a provider', () => {
  it('renders the card with no feedback entries in its menu', () => {
    render(<Card />);
    fireEvent.click(screen.getByRole('button', { name: 'More options for Harbor walk' }));
    expect(screen.queryByRole('menuitem', { name: 'Not interested' })).toBeNull();
  });
});

describe('list helpers', () => {
  const other = recoEntry('v2', 1, { uploader: 'Other', title: 'Other video' });
  function Filtered({ keepHidden }: { keepHidden?: boolean }) {
    const items = useRecoFilter([entry, other], remoteTarget, keepHidden);
    return <ul>{items.map((item) => <li key={item.id}>{item.title}</li>)}</ul>;
  }

  it('drops gone items, and hidden ones unless asked to keep them', async () => {
    setup({}, <><Card /><Filtered /><div data-testid="keep"><Filtered keepHidden /></div></>);
    await choose('Not interested');
    expect(screen.getAllByText('Harbor walk')).toHaveLength(1);
    expect(screen.getByTestId('keep').textContent).toContain('Harbor walk');
    act(() => { vi.advanceTimersByTime(8_000); });
    expect(screen.queryByText('Harbor walk')).toBeNull();
    expect(screen.getAllByText('Other video')).toHaveLength(2);
  });

  it('lists a hidden target\'s Undo row where the card cannot be swapped (rail menus)', async () => {
    function Hider() {
      const api = useRecoFeedback();
      return <button onClick={() => { void api?.feedback('not_interested', remoteTarget(entry), entry.reco!); }} type="button">Hide it</button>;
    }
    setup({}, <><Hider /><RecoHiddenRows targets={[remoteTarget(entry), remoteTarget(other)]} /></>);
    expect(screen.queryByRole('status')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Hide it' }));
    await flush();
    expect(screen.getAllByRole('status')).toHaveLength(1);
    expect(screen.getByRole('status').textContent).toBe('Hidden. Undo');
  });

  it('autoplay skips an exploration slot, a hidden card and a gone one', async () => {
    let eligible!: (item: RemoteEntry) => boolean;
    function Capture() { eligible = useAutoplayEligible(); return null; }
    setup({}, <><Card /><Capture /></>);
    const explore = recoEntry('v3', 11, {}, { slot: 'explore', reason_code: 'explore' });
    expect(eligible(entry)).toBe(true);
    expect(eligible(explore)).toBe(false);
    expect(eligible({ ...entry, reco: recoAnnotation({ slot: 'pinned' }) })).toBe(true);
    await choose('Not interested');
    expect(eligible(entry)).toBe(false);
    act(() => { vi.advanceTimersByTime(8_000); });
    expect(eligible(entry)).toBe(false);
  });
});
