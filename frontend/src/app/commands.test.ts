import { describe, expect, it, vi } from 'vitest';
import { EDIT_HOME_EVENT, MEMBER_PICKER_EVENT, PALETTE_EVENT, onCommand, openMemberPicker, openPalette, requestHomeEdit } from './commands';

describe('command seams', () => {
  it('deliver each command to its listener until unsubscribed', () => {
    const palette = vi.fn(); const picker = vi.fn(); const home = vi.fn();
    const stops = [onCommand(PALETTE_EVENT, palette), onCommand(MEMBER_PICKER_EVENT, picker), onCommand(EDIT_HOME_EVENT, home)];
    openPalette({ mode: 'link' }); openPalette(); openMemberPicker(); requestHomeEdit();
    expect(palette.mock.calls).toEqual([[{ mode: 'link' }], [{ mode: 'search' }]]);
    expect(picker).toHaveBeenCalledOnce();
    expect(home).toHaveBeenCalledOnce();
    stops.forEach((stop) => stop());
    openMemberPicker();
    expect(picker).toHaveBeenCalledOnce();
  });
});
