import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { SourceAutomation } from '../../types';
import { FollowSettingsDialog } from './FollowSettingsDialog';

const automation = {
  id: 'f1', user_id: 'm', label: 'Harbor Films', source_url: 'https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv', source_type: 'channel',
  cron_expression: '0 */6 * * *', active: true, auto_download: false, format_selection: {}, output_profile: {}, rules: {},
  duplicate_policy: 'skip_same_source', last_run_summary: {}, created_at: '', updated_at: '',
} as unknown as SourceAutomation;
const commands = { pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(), prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn() };

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <div className="gallery">
      <button onClick={() => setOpen(true)} type="button">Follow settings</button>
      {open ? <FollowSettingsDialog automation={automation} commands={commands as never} onChange={vi.fn()} onClose={() => setOpen(false)} onRecover={vi.fn()} onRemoved={vi.fn()} /> : null}
    </div>
  );
}

beforeAll(() => {
  // jsdom has no modal dialog; model the method the drawer uses.
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
});

describe('FollowSettingsDialog', () => {
  it('opens as a modal drawer with the existing controls and returns focus to its opener', async () => {
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Follow settings' });
    await userEvent.click(opener);
    const dialog = document.querySelector('dialog.g-drawer') as HTMLDialogElement;
    expect(dialog.open).toBe(true);
    expect(screen.getByRole('heading', { name: 'Follow settings' })).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: /download new videos automatically/i })).toBeTruthy();
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(document.querySelector('dialog.g-drawer')).toBeNull();
    expect(document.activeElement).toBe(opener);
    await userEvent.click(opener);
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(document.activeElement).toBe(opener);
  });
});
