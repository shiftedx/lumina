import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlayerOverlays, SKIP_NOTICE_MS, UP_NEXT_COUNTDOWN_SECONDS } from './features/watch/PlayerOverlays';
import type { MediaSegment, RecapResponse } from './types';

afterEach(() => vi.useRealTimers());

const segment = (type: MediaSegment['type'], start: number, end: number): MediaSegment => ({ type, start_seconds: start, end_seconds: end, source: 'fingerprint', confidence: 0.9 });
const segments = [segment('intro', 30, 90), segment('credits', 1250, 1320)];
const noSkip = { intro: false, credits: false, recap: false };
const next = (label: string, onPlay = vi.fn(), extra = {}) => ({ label, action: 'Next episode', autoplay: true, onPlay, ...extra });

function overlays(props: Partial<Parameters<typeof PlayerOverlays>[0]> = {}) {
  const all = { currentTime: 0, duration: 1320, segments, autoSkip: noSkip, upNext: null, autoplay: true, recap: null, onSeek: vi.fn(), onCancelUpNext: vi.fn(), ...props };
  const view = render(<PlayerOverlays {...all} />);
  return { ...all, rerender: (next: Partial<typeof all>) => view.rerender(<PlayerOverlays {...all} {...next} />) };
}

describe('PlayerOverlays', () => {
  it('offers Skip intro inside the intro, and Skip credits only when nothing plays next', () => {
    const { onSeek, rerender } = overlays({ currentTime: 31 });
    fireEvent.click(screen.getByRole('button', { name: 'Skip intro' }));
    expect(onSeek).toHaveBeenCalledWith(90);
    rerender({ currentTime: 95 });
    expect(screen.queryByRole('button', { name: /Skip/ })).toBeNull();
    rerender({ currentTime: 1260 });
    expect(screen.getByRole('button', { name: 'Skip credits' })).toBeTruthy();
  });

  it('auto-skips a segment once, lets the member undo, and never re-skips after a seek back into it', () => {
    vi.useFakeTimers();
    const onSeek = vi.fn();
    const { rerender } = overlays({ autoSkip: { ...noSkip, intro: true }, currentTime: 30.4, onSeek });
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenCalledWith(90);
    expect(screen.getByRole('status').textContent).toContain('Skipped intro');
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onSeek).toHaveBeenLastCalledWith(30.4);
    rerender({ autoSkip: { ...noSkip, intro: true }, currentTime: 31, onSeek });
    rerender({ autoSkip: { ...noSkip, intro: true }, currentTime: 45, onSeek });
    expect(onSeek).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Skip intro' })).toBeTruthy();
    rerender({ autoSkip: { ...noSkip, intro: true }, currentTime: 1000, onSeek });
    rerender({ autoSkip: { ...noSkip, intro: true }, currentTime: 32, onSeek });
    expect(onSeek).toHaveBeenCalledTimes(2);
    act(() => { vi.advanceTimersByTime(SKIP_NOTICE_MS); });
    expect(screen.queryByText(/Skipped intro/)).toBeNull();
  });

  it('counts down to the next episode at credits, and Cancel stops it', () => {
    vi.useFakeTimers();
    const onPlay = vi.fn();
    const onCancelUpNext = vi.fn();
    const { rerender } = overlays({ currentTime: 1251, upNext: next('S1 · E4 · The Ferry', onPlay), onCancelUpNext });
    const card = screen.getByRole('region', { name: 'Next episode' });
    expect(card.textContent).toContain('S1 · E4 · The Ferry');
    expect(screen.queryByRole('button', { name: 'Skip credits' })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Play now' }));
    act(() => { vi.advanceTimersByTime((UP_NEXT_COUNTDOWN_SECONDS - 1) * 1000); });
    expect(onPlay).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1000); });
    expect(onPlay).toHaveBeenCalledTimes(1);
    act(() => { vi.advanceTimersByTime(5000); });
    expect(onPlay).toHaveBeenCalledTimes(1);

    rerender({ currentTime: 0 });
    rerender({ currentTime: 1251, upNext: next('S1 · E4 · The Ferry', onPlay), onCancelUpNext });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancelUpNext).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('region', { name: 'Next episode' })).toBeNull();
    act(() => { vi.advanceTimersByTime(20_000); });
    expect(onPlay).toHaveBeenCalledTimes(1);
    // Cancel keeps a quiet Next episode button in the chrome through the credits.
    fireEvent.click(screen.getByRole('button', { name: 'Next episode' }));
    expect(onPlay).toHaveBeenCalledTimes(2);
  });

  it('shows the next still and offers a collection film without ever starting it', () => {
    vi.useFakeTimers();
    const onPlay = vi.fn();
    overlays({ currentTime: 1251, upNext: next('Sequel', onPlay, { action: 'Next film', autoplay: false, still: '/api/titles/m2/images/Primary' }) });
    const card = screen.getByRole('region', { name: 'Next film' });
    expect(card.querySelector('img')?.getAttribute('src')).toContain('/api/titles/m2/images/Primary');
    expect(card.textContent).not.toContain('Playing in');
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(onPlay).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Play now' }));
    expect(onPlay).toHaveBeenCalledTimes(1);
  });

  it('waits for Play now when autoplay is off, and shows up next 30 s before the end without credits', () => {
    vi.useFakeTimers();
    const onPlay = vi.fn();
    overlays({ autoplay: false, currentTime: 1291, segments: [], upNext: next('S2 · E1 · Return', onPlay) });
    act(() => { vi.advanceTimersByTime(20_000); });
    expect(onPlay).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Play now' }));
    expect(onPlay).toHaveBeenCalledTimes(1);
  });

  it('offers a dismissible "Previously on" card at the start of a later episode', () => {
    const recap: RecapResponse = { episode_title_id: 'e5', state: 'fallback', points: [], fallback: [{ episode_id: 'e4', name: 'The Ferry', season_number: 1, index_number: 4, overview: 'The ferry runs aground.' }], suggest_preroll: true };
    const { rerender } = overlays({ currentTime: 2, recap });
    const card = screen.getByRole('region', { name: 'Previously on' });
    expect(card.textContent).toContain('The ferry runs aground.');
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss recap' }));
    expect(screen.queryByRole('region', { name: 'Previously on' })).toBeNull();
    rerender({ currentTime: 2, recap: { ...recap, suggest_preroll: false } });
    expect(screen.queryByRole('region', { name: 'Previously on' })).toBeNull();
  });

  it('renders the up-next, recap and skip-notice cards in the editorial overlay look', () => {
    vi.useFakeTimers();
    const recap: RecapResponse = { episode_title_id: 'e5', state: 'fallback', points: [], fallback: [{ episode_id: 'e4', name: 'The Ferry', season_number: 1, index_number: 4, overview: 'Aground.' }], suggest_preroll: true };
    const { rerender } = overlays({ currentTime: 2, recap, upNext: next('S1 · E4 · The Ferry') });
    expect(screen.getByRole('region', { name: 'Previously on' }).classList.contains('player-card')).toBe(true);
    rerender({ currentTime: 1251, recap: null });
    const card = screen.getByRole('region', { name: 'Next episode' });
    expect(card.classList.contains('player-card')).toBe(true);
    expect(card.querySelector('.player-card-title')?.textContent).toBe('Next: S1 · E4 · The Ferry');
    expect(screen.getByRole('button', { name: 'Play now' }).classList.contains('is-primary')).toBe(true);
    expect(screen.getByRole('button', { name: 'Cancel' }).textContent).toBe('Cancel');
  });

  it('styles the skip button and the skipped notice as overlay controls', () => {
    const { unmount } = render(<PlayerOverlays {...{ currentTime: 31, duration: 1320, segments, autoSkip: noSkip, upNext: null, autoplay: true, recap: null, onSeek: vi.fn(), onCancelUpNext: vi.fn() }} />);
    expect(screen.getByRole('button', { name: 'Skip intro' }).classList.contains('g-button')).toBe(true);
    unmount();
    overlays({ currentTime: 31, autoSkip: { ...noSkip, intro: true } });
    const notice = screen.getByRole('status');
    expect(notice.classList.contains('player-card')).toBe(true);
    expect(screen.getByRole('button', { name: 'Undo' }).classList.contains('g-text-button')).toBe(true);
  });
});
