import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MemberActivityDialog, type MemberActivityDialogProps } from './MemberActivityDialog';

const base = (): MemberActivityDialogProps => ({
  user: { id: 'member-1', username: 'sam', display_name: 'Sam Rivera', role: 'admin', is_active: true },
  theme: 'dark', onTheme: vi.fn(), switchMemberLabel: 'Switch member…', onSwitchMember: vi.fn(), onEditHome: vi.fn(), onMobileConnect: vi.fn(), onSettings: vi.fn(), onManageServer: vi.fn(), onSignOut: vi.fn(), onClose: vi.fn(),
  activityState: 'ready', onRetryActivity: vi.fn(),
  activity: { generated_at: '2026-11-02T12:00:00Z', period_days: 14, timezone: 'UTC', watched_seconds: 8_100, sessions: 3, active_days: 2, latest_activity_at: '2026-11-01T21:00:00Z', days: [{ date: '2026-10-20', watched_seconds: 0, sessions: 0 }, { date: '2026-10-21', watched_seconds: 3_600, sessions: 1 }, { date: '2026-10-22', watched_seconds: 4_500, sessions: 2 }] },
});

describe('MemberActivityDialog', () => {
  it('makes the personal activity timeline and all account controls available', async () => {
    const props = base();
    render(<MemberActivityDialog {...props} />);
    const dialog = screen.getByRole('dialog', { name: 'Your activity' });
    expect(within(dialog).getByText('2h 15m', { exact: false })).toBeTruthy();
    expect(within(dialog).getByRole('list', { name: /Playback activity over the last 14 days, shown in UTC/ }).children).toHaveLength(3);
    expect(within(dialog).getByRole('radio', { name: 'Dark' }).getAttribute('checked')).not.toBeNull();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Manage Server' }));
    expect(props.onClose).toHaveBeenCalledOnce();
    expect(props.onManageServer).toHaveBeenCalledOnce();
  });

  it('never replaces unavailable activity with invented zeroes', () => {
    const props = base();
    props.activityState = 'failed';
    props.activity = null;
    render(<MemberActivityDialog {...props} />);
    expect(screen.getByText('Your activity is unavailable')).toBeTruthy();
    expect(screen.queryByText(/recorded playback/)).toBeNull();
  });
});
