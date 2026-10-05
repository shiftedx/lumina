import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { RecoFeedbackProvider } from '../reco/recoFeedback';
import { recoEntry } from '../../test/recoFixtures';
import { liveEntry, remoteEntry } from '../../test/remoteFixtures';
import { WatchRelated } from './WatchRelated';

const provider = (ui: React.ReactNode, suppress = vi.fn().mockResolvedValue({ id: 'sup-1' })) => (
  <RecoFeedbackProvider onError={vi.fn()} onExpired={vi.fn()} resetKey="m" restore={vi.fn()} suppress={suppress}>{ui}</RecoFeedbackProvider>
);
const props = { autoplay: true, autoplayIndex: 0, loading: false, onAutoplayChange: vi.fn(), onOpen: vi.fn(), remote: true, showAutoplay: true };

describe('WatchRelated', () => {
  const items = [remoteEntry('a', { title: 'Actual next video' }), liveEntry('b', { title: 'Live stream' })];

  it('lists compact rows, describes the autoplay target and keeps the switch', async () => {
    const onOpen = vi.fn();
    const onAutoplayChange = vi.fn();
    render(provider(<WatchRelated {...props} items={items} onAutoplayChange={onAutoplayChange} onOpen={onOpen} />));
    expect(screen.getByRole('heading', { name: 'Up next' })).toBeTruthy();
    const next = screen.getByRole('button', { name: /^Actual next video/ });
    expect(screen.getByText('Plays next')).toBeTruthy();
    expect(next.getAttribute('aria-describedby')?.split(' ')).toContain(screen.getByText('Plays next').id);
    await userEvent.click(next);
    expect(onOpen).toHaveBeenCalledWith(items[0]);
    await userEvent.click(screen.getByRole('checkbox', { name: 'Autoplay next video' }));
    expect(onAutoplayChange).toHaveBeenCalledWith(false);
    expect(screen.getAllByRole('button', { name: /^More options for / })).toHaveLength(2);
    expect(document.querySelectorAll('.g-remote-card.is-compact')).toHaveLength(2);
  });

  it('says why the list is empty', () => {
    render(provider(<WatchRelated {...props} autoplay={false} autoplayIndex={-1} items={[]} loading showAutoplay={false} />));
    expect(screen.getByText('Finding related videos…')).toBeTruthy();
    expect(screen.queryByRole('checkbox', { name: 'Autoplay next video' })).toBeNull();
  });

  it('keeps the reason in the row\'s menu and "Plays next" as its description', () => {
    const picks = [recoEntry('a', 0, { title: 'Actual next video' }, { reason: 'Because you finished Harbor walk at dawn' }), recoEntry('b', 1, { title: 'Second video' }, { reason: 'More from Channel b', reason_code: 'same_channel' })];
    render(provider(<WatchRelated {...props} items={picks} />));
    expect(screen.getByRole('button', { name: /^Actual next video/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Second video/ })).toBeTruthy();
    expect(screen.queryByText('Because you finished Harbor walk at dawn')).toBeNull();
    expect(screen.getByText('Plays next')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More options for Second video' }));
    expect(screen.getByRole('group', { name: 'More from Channel b' })).toBeTruthy();
  });

  it('replaces a row with Hidden. Undo in place and leaves the other rows alone', async () => {
    const picks = [recoEntry('a', 0, { title: 'Actual next video', uploader: 'Harbor Films' }), recoEntry('b', 1, { title: 'Second video' })];
    const suppress = vi.fn().mockResolvedValue({ id: 'sup-1' });
    render(provider(<WatchRelated {...props} items={picks} />, suppress));
    fireEvent.click(screen.getByRole('button', { name: 'More options for Actual next video' }));
    fireEvent.click(screen.getByRole('menuitem', { name: "Don't recommend Harbor Films" }));
    expect(await screen.findByText('Hidden.')).toBeTruthy();
    expect(suppress).toHaveBeenCalledWith(expect.objectContaining({ scope: 'channel', uploader: 'Harbor Films', list_id: picks[0].reco!.list_id }));
    expect(screen.queryByRole('button', { name: /^Actual next video/ })).toBeNull();
    expect(screen.getByRole('button', { name: /^Second video/ })).toBeTruthy();
  });

  it('shows the menu without a reason when the rows carry no annotation', () => {
    render(provider(<WatchRelated {...props} items={items} />));
    expect(document.querySelector('.reco-reason')).toBeNull();
    expect(screen.getAllByRole('button', { name: /^More options for / })).toHaveLength(2);
  });

  it('leaves out Show fewer when no row is annotated (the switch is off)', () => {
    render(provider(<WatchRelated {...props} items={[remoteEntry('a', { title: 'Actual next video', uploader: 'Harbor Films' })]} />));
    fireEvent.click(screen.getByRole('button', { name: 'More options for Actual next video' }));
    expect(screen.queryByRole('menuitem', { name: /^Show fewer from / })).toBeNull();
    expect(screen.getByRole('menuitem', { name: "Don't recommend Harbor Films" })).toBeTruthy();
  });
});
