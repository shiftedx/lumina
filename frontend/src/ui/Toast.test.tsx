import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { INLINE_NOTICE_MS, InlineNotice, ToastProvider, useToast, type ToastInput } from '.';

let push = (_toast: ToastInput): number => 0;
let toaster: ReturnType<typeof useToast> | null = null;
function Grab() { toaster = useToast(); push = toaster; return <button type="button">Elsewhere</button>; }
const setup = () => render(<ToastProvider><Grab /></ToastProvider>);
beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('Toast', () => {
  it('announces success politely and dismisses it after 5 s', () => {
    setup();
    act(() => push({ tone: 'success', message: 'Saved.' }));
    expect(screen.getByText('Saved.').closest('[aria-live="polite"]')).not.toBeNull();
    act(() => { vi.advanceTimersByTime(4900); });
    expect(screen.queryByText('Saved.')).not.toBeNull();
    act(() => { vi.advanceTimersByTime(400); });
    expect(screen.queryByText('Saved.')).toBeNull();
  });

  it('pauses while hovered or focused and resumes with the time left', () => {
    setup();
    act(() => push({ tone: 'info', message: 'Queued.' }));
    act(() => { vi.advanceTimersByTime(3000); });
    fireEvent.pointerEnter(screen.getByText('Queued.').closest('.g-toast')!);
    act(() => { vi.advanceTimersByTime(10000); });
    expect(screen.queryByText('Queued.')).not.toBeNull();
    fireEvent.pointerLeave(screen.getByText('Queued.').closest('.g-toast')!);
    act(() => { vi.advanceTimersByTime(1900); });
    expect(screen.queryByText('Queued.')).not.toBeNull();
    act(() => { vi.advanceTimersByTime(400); });
    expect(screen.queryByText('Queued.')).toBeNull();
  });

  it('an error toast stays until dismissed and focus stays put', () => {
    setup();
    const elsewhere = screen.getByRole('button', { name: 'Elsewhere' });
    elsewhere.focus();
    act(() => push({ tone: 'error', message: 'Restore failed: the file is no longer on disk.' }));
    expect(screen.getByRole('alert').textContent).toContain('Restore failed');
    expect(document.activeElement).toBe(elsewhere);
    act(() => { vi.advanceTimersByTime(60000); });
    expect(screen.queryByRole('alert')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Dismiss/ }));
    act(() => { vi.advanceTimersByTime(300); });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('keeps at most 3, dropping the oldest non-error first', () => {
    setup();
    act(() => { push({ tone: 'error', message: 'E1' }); push({ tone: 'success', message: 'S1' }); push({ tone: 'success', message: 'S2' }); push({ tone: 'info', message: 'I1' }); });
    act(() => { vi.advanceTimersByTime(300); });
    expect(screen.queryByText('S1')).toBeNull();
    expect(['E1', 'S2', 'I1'].every((text) => screen.queryByText(text))).toBe(true);
  });

  it('runs an action and dismisses', () => {
    setup();
    const undo = vi.fn();
    act(() => push({ tone: 'success', message: 'Deleted “Film”. It stays restorable for a while.', action: { label: 'Undo', onAction: undo } }));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    act(() => { vi.advanceTimersByTime(300); });
    expect(undo).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Deleted/)).toBeNull();
  });
});

// Ported from UndoRow.test.tsx.
const notice = (patch: Partial<Parameters<typeof InlineNotice>[0]> = {}) => (
  <InlineNotice action={{ label: 'Undo', onAction: vi.fn() }} message="Hidden." onExpire={vi.fn()} {...patch} />
);

describe('Toast clear and remove', () => {
  it('clear drops every toast at once so a pending Undo cannot run', () => {
    setup();
    const undo = vi.fn();
    act(() => { push({ tone: 'success', message: 'Hidden.', action: { label: 'Undo', onAction: undo } }); push({ tone: 'error', message: 'Broke.' }); });
    act(() => toaster!.clear());
    expect(screen.queryByText('Hidden.')).toBeNull();
    expect(screen.queryByText('Broke.')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(undo).not.toHaveBeenCalled();
  });

  it('push returns an id that remove takes down', () => {
    setup();
    let id = 0;
    act(() => { id = push({ tone: 'error', message: 'Offline.' }); });
    act(() => { toaster!.remove(id); vi.advanceTimersByTime(300); });
    expect(screen.queryByText('Offline.')).toBeNull();
  });
});

describe('InlineNotice', () => {
  it('is a polite status that reads the message and offers the action, with focus on the action', () => {
    render(notice());
    expect(screen.getByRole('status').textContent).toBe('Hidden. Undo');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Undo' }));
  });

  it('leaves focus alone when focusAction is false', () => {
    render(<><button type="button">Elsewhere</button>{notice({ focusAction: false })}</>);
    screen.getByRole('button', { name: 'Elsewhere' }).focus();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Elsewhere' }));
  });

  it('expires once after 8 seconds', () => {
    const onExpire = vi.fn();
    render(notice({ onExpire }));
    expect(INLINE_NOTICE_MS).toBe(8_000);
    act(() => { vi.advanceTimersByTime(7_999); });
    expect(onExpire).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(onExpire).toHaveBeenCalledTimes(1);
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it('never expires after the action is taken', () => {
    const onExpire = vi.fn(); const onAction = vi.fn();
    render(notice({ onExpire, action: { label: 'Undo', onAction } }));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onExpire).not.toHaveBeenCalled();
  });

  it('does not expire once it has left the page, and honours timeoutMs', () => {
    const gone = vi.fn(); const quick = vi.fn();
    render(notice({ onExpire: gone })).unmount();
    render(notice({ onExpire: quick, timeoutMs: 500 }));
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(gone).not.toHaveBeenCalled();
    expect(quick).toHaveBeenCalledTimes(1);
  });

  it('hands focus to the next card control when it expires, else the previous one', () => {
    const view = render(<ul data-focus-row><li><button data-focus-item type="button">A</button></li><li>{notice()}</li><li><button data-focus-item type="button">C</button></li></ul>);
    act(() => { vi.advanceTimersByTime(INLINE_NOTICE_MS); });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'C' }));
    view.unmount();
    render(<ul data-focus-row><li><button data-focus-item type="button">A</button></li><li>{notice()}</li></ul>);
    act(() => { vi.advanceTimersByTime(INLINE_NOTICE_MS); });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'A' }));
  });

  it('pauses while hovered or while the user brings focus into it, and resumes with the time left (WCAG 2.2.1)', () => {
    const onExpire = vi.fn();
    render(<><button type="button">Elsewhere</button>{notice({ onExpire })}</>);
    act(() => { vi.advanceTimersByTime(3_000); });
    const row = screen.getByRole('status');
    fireEvent.pointerEnter(row);
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(onExpire).not.toHaveBeenCalled();
    fireEvent.pointerLeave(row);
    act(() => screen.getByRole('button', { name: 'Elsewhere' }).focus());
    act(() => screen.getByRole('button', { name: 'Undo' }).focus()); // the user comes back to it
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(onExpire).not.toHaveBeenCalled();
    act(() => screen.getByRole('button', { name: 'Elsewhere' }).focus());
    act(() => { vi.advanceTimersByTime(4_999); });
    expect(onExpire).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it('leaves focus alone when it was somewhere else', () => {
    render(<div data-focus-row><button data-focus-item type="button">A</button>{notice()}</div>);
    screen.getByRole('button', { name: 'A' }).focus();
    act(() => { vi.advanceTimersByTime(INLINE_NOTICE_MS); });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'A' }));
  });
});
