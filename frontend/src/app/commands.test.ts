import { describe, expect, it, vi } from 'vitest';
import { MEMBER_PICKER_EVENT, PALETTE_EVENT, onCommand, onHomeEdit, openMemberPicker, openPalette, requestHomeEdit } from './commands';

describe('command seams', () => {
  it('deliver each command to its listener until unsubscribed', () => {
    const palette = vi.fn(); const picker = vi.fn(); const home = vi.fn();
    const stops = [onCommand(PALETTE_EVENT, palette), onCommand(MEMBER_PICKER_EVENT, picker), onHomeEdit(home)];
    openPalette({ mode: 'link' }); openPalette(); openMemberPicker(); requestHomeEdit();
    expect(palette.mock.calls).toEqual([[{ mode: 'link' }], [{ mode: 'search' }]]);
    expect(picker).toHaveBeenCalledOnce();
    expect(home).toHaveBeenCalledOnce();
    stops.forEach((stop) => stop());
    openMemberPicker();
    expect(picker).toHaveBeenCalledOnce();
  });

  it('delivers one coalesced Home edit to a late consumer and respects cleanup', () => {
    requestHomeEdit(); requestHomeEdit();
    const first = vi.fn();
    const stop = onHomeEdit(first);
    expect(first).toHaveBeenCalledOnce();
    requestHomeEdit();
    expect(first).toHaveBeenCalledTimes(2);
    stop();

    requestHomeEdit(); requestHomeEdit();
    const late = vi.fn();
    const stopLate = onHomeEdit(late);
    expect(late).toHaveBeenCalledOnce();
    stopLate();
  });
});
