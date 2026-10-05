import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ clearRecoHistory: vi.fn(), sendRecoEvents: vi.fn(), beaconRecoEvents: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

import { flushRecoEvents, recordRecoImpression, resetRecoEvents } from '../reco/recoEvents';
import { RecoHistoryCard } from './youCards';

beforeEach(() => { resetRecoEvents(); api.clearRecoHistory.mockReset().mockResolvedValue(undefined); api.sendRecoEvents.mockReset().mockResolvedValue(undefined); });

describe('RecoHistoryCard', () => {
  it('asks first in a dialog, in the spec\'s words, and clears nothing until confirmed', async () => {
    render(<RecoHistoryCard onMessage={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Clear recommendation history' }));
    expect(screen.getByRole('dialog', { name: 'Clear recommendation history?' })).toBeTruthy();
    expect(screen.getByText('Lumina forgets what it showed you and what you opened. Your watch history and hidden lists stay.')).toBeTruthy();
    expect(api.clearRecoHistory).not.toHaveBeenCalled();
  });

  it('clears on confirmation and says so', async () => {
    const onMessage = vi.fn();
    render(<RecoHistoryCard onMessage={onMessage} />);
    await userEvent.click(screen.getByRole('button', { name: 'Clear recommendation history' }));
    await userEvent.click(screen.getByRole('button', { name: 'Clear history' }));
    await waitFor(() => expect(onMessage).toHaveBeenCalledWith('Recommendation history cleared.'));
    expect(api.clearRecoHistory).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('throws away buffered recommendation events so none is posted after the clear', async () => {
    recordRecoImpression({ list_id: 'list-1', key: 'k1' } as never);
    render(<RecoHistoryCard onMessage={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Clear recommendation history' }));
    await userEvent.click(screen.getByRole('button', { name: 'Clear history' }));
    await waitFor(() => expect(api.clearRecoHistory).toHaveBeenCalledTimes(1));
    await flushRecoEvents();
    expect(api.sendRecoEvents).not.toHaveBeenCalled();
  });

  it('Cancel closes the question and returns focus to the button', async () => {
    render(<RecoHistoryCard onMessage={vi.fn()} />);
    const open = screen.getByRole('button', { name: 'Clear recommendation history' });
    await userEvent.click(open);
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(open);
    expect(api.clearRecoHistory).not.toHaveBeenCalled();
  });

  it('Escape cancels and clears nothing', async () => {
    render(<RecoHistoryCard onMessage={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Clear recommendation history' }));
    // jsdom does not turn Escape into the dialog's cancel event, so fire what the browser would.
    fireEvent(screen.getByRole('dialog', { name: 'Clear recommendation history?' }), new Event('cancel', { cancelable: true }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.clearRecoHistory).not.toHaveBeenCalled();
  });

  it('keeps the question open and says so when clearing fails', async () => {
    api.clearRecoHistory.mockRejectedValue(new Error('offline'));
    const onMessage = vi.fn();
    render(<RecoHistoryCard onMessage={onMessage} />);
    await userEvent.click(screen.getByRole('button', { name: 'Clear recommendation history' }));
    await userEvent.click(screen.getByRole('button', { name: 'Clear history' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Could not clear recommendation history. Try again.');
    expect(screen.getByRole('dialog', { name: 'Clear recommendation history?' })).toBeTruthy();
    expect(onMessage).not.toHaveBeenCalled();
  });
});
