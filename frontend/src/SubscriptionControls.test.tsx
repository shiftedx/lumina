/* @vitest-environment jsdom */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SubscriptionControls } from './SubscriptionControls';
import type { SourceAutomation } from './types';

function failedChannel(): SourceAutomation {
  return {
    id: 'channel-1',
    user_id: 'member-1',
    label: 'Mars Astronomy',
    source_url: 'https://www.youtube.com/@mars/videos',
    source_type: 'channel',
    cron_expression: '0 */6 * * *',
    active: true,
    auto_download: false,
    format_selection: { preset: 'best', output_container: 'mp4' },
    output_profile: { base_path: null, subdir: '', organize_by: 'uploader' },
    rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video' },
    duplicate_policy: 'skip_same_source',
    max_items_per_run: 20,
    max_items_per_day: null,
    backfill_limit: 10,
    last_error: 'The channel check timed out.',
    last_run_summary: { status: 'failed', discovered: 7, queued: 2, failed: 1 },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

describe('SubscriptionControls', () => {
  it('runs an immediate Source automation check from a visible control', async () => {
    const browser = userEvent.setup();
    const automation = { ...failedChannel(), last_error: null, last_run_summary: {} };
    const onRecover = vi.fn().mockResolvedValue(undefined);
    render(<SubscriptionControls automation={automation} commands={{
      pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(), prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn(),
    }} onChange={vi.fn()} onRecover={onRecover} onRemoved={vi.fn()} />);

    await browser.click(screen.getByRole('button', { name: 'Check Mars Astronomy now' }));

    expect(onRecover).toHaveBeenCalledWith(automation);
  });

  it('explains that automatic download choices are captured when the channel is followed', () => {
    const automation = { ...failedChannel(), auto_download: true };
    render(<SubscriptionControls automation={automation} commands={{
      pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(), prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn(),
    }} onChange={vi.fn()} onRecover={vi.fn().mockResolvedValue(undefined)} onRemoved={vi.fn()} />);

    expect(screen.getByText('Automatic downloads use the choices captured when you followed this channel. Later changes to your acquisition defaults do not update this Source automation.')).not.toBeNull();
    expect(screen.queryByText(/uses your saved download defaults/i)).toBeNull();
  });

  it('presents progressive lifecycle, failure recovery, and explicit unfollow confirmation', async () => {
    const browser = userEvent.setup();
    const automation = failedChannel();
    const confirmation = { automationId: automation.id, sourceIdentity: automation.source_url };
    const commands = {
      pause: vi.fn().mockResolvedValue({ ...automation, active: false }),
      resume: vi.fn(),
      setAutomaticAcquisition: vi.fn().mockResolvedValue({ ...automation, auto_download: true }),
      prepareUnfollow: vi.fn().mockReturnValue(confirmation),
      confirmUnfollow: vi.fn().mockResolvedValue(undefined),
    };
    const onChange = vi.fn();
    const onRecover = vi.fn();
    const onRemoved = vi.fn();

    render(<SubscriptionControls automation={automation} commands={commands} onChange={onChange} onRecover={onRecover} onRemoved={onRemoved} />);

    expect(screen.getByRole('heading', { name: 'Mars Astronomy' })).not.toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('The channel check timed out.');
    await browser.click(screen.getByRole('button', { name: 'Try check again' }));
    expect(onRecover).toHaveBeenCalledWith(automation);

    const automatic = screen.getByRole('checkbox', { name: 'Download new videos automatically' });
    expect((automatic as HTMLInputElement).checked).toBe(false);
    await browser.click(automatic);
    expect(commands.setAutomaticAcquisition).toHaveBeenCalledWith(automation, true);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ auto_download: true }));

    const advanced = screen.getByText('More automation options').closest('details');
    expect(advanced?.hasAttribute('open')).toBe(false);
    expect(advanced?.textContent).toContain('0 */6 * * *');
    expect(advanced?.textContent).toContain(automation.source_url);
    expect(advanced?.textContent).toContain('7 found');

    await browser.click(screen.getByRole('button', { name: 'Pause checks for Mars Astronomy' }));
    expect(commands.pause).toHaveBeenCalledWith(automation);

    await browser.click(screen.getByRole('button', { name: 'Unfollow Mars Astronomy' }));
    expect(commands.prepareUnfollow).toHaveBeenCalledWith(automation);
    expect(commands.confirmUnfollow).not.toHaveBeenCalled();
    const unfollowDialog = screen.getByRole('alertdialog');
    expect(unfollowDialog.textContent).toContain('Videos already in your library stay');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Keep following' }));
    await browser.click(screen.getByRole('button', { name: 'Confirm unfollow Mars Astronomy' }));
    expect(commands.confirmUnfollow).toHaveBeenCalledWith(automation, confirmation);
    expect(onRemoved).toHaveBeenCalledWith(automation.id);
  });

  it('contains keyboard focus, closes on Escape, and restores the unfollow trigger', async () => {
    const browser = userEvent.setup();
    const automation = failedChannel();
    const commands = {
      pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(),
      prepareUnfollow: vi.fn().mockReturnValue({ automationId: automation.id, sourceIdentity: automation.source_url }),
      confirmUnfollow: vi.fn(),
    };
    render(<SubscriptionControls automation={automation} commands={commands} onChange={vi.fn()} onRecover={vi.fn().mockResolvedValue(undefined)} onRemoved={vi.fn()} />);
    const trigger = screen.getByRole('button', { name: 'Unfollow Mars Astronomy' });
    await browser.click(trigger);
    const cancel = screen.getByRole('button', { name: 'Keep following' });
    const confirm = screen.getByRole('button', { name: 'Confirm unfollow Mars Astronomy' });
    expect(document.activeElement).toBe(cancel);
    await browser.tab({ shift: true });
    expect(document.activeElement).toBe(confirm);
    await browser.tab();
    expect(document.activeElement).toBe(cancel);
    await browser.keyboard('{Escape}');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('restores focus and announces an unfollow failure', async () => {
    const browser = userEvent.setup();
    const automation = failedChannel();
    const commands = {
      pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(),
      prepareUnfollow: vi.fn().mockReturnValue({ automationId: automation.id, sourceIdentity: automation.source_url }),
      confirmUnfollow: vi.fn().mockRejectedValue(new Error('Could not unfollow now.')),
    };
    render(<SubscriptionControls automation={automation} commands={commands} onChange={vi.fn()} onRecover={vi.fn().mockResolvedValue(undefined)} onRemoved={vi.fn()} />);
    const trigger = screen.getByRole('button', { name: 'Unfollow Mars Astronomy' });
    await browser.click(trigger);
    await browser.click(screen.getByRole('button', { name: 'Confirm unfollow Mars Astronomy' }));
    expect((await screen.findByText('Could not unfollow now.')).getAttribute('role')).toBe('alert');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('announces a persisted failed manual retry returned with HTTP success', async () => {
    const browser = userEvent.setup();
    const automation = { ...failedChannel(), last_error: 'Last check failed.' };
    const onRecover = vi.fn().mockRejectedValue(new Error('This channel could not be checked again.'));
    render(<SubscriptionControls automation={automation} commands={{
      pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(), prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn(),
    }} onChange={vi.fn()} onRecover={onRecover} onRemoved={vi.fn()} />);

    await browser.click(screen.getByRole('button', { name: 'Try check again' }));

    expect(onRecover).toHaveBeenCalledWith(automation);
    expect((await screen.findByText('This channel could not be checked again.')).getAttribute('role')).toBe('alert');
  });
});
