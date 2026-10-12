import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../api';
import { ApiRequestError } from '../../api';
import type { DeviceMember } from '../../types';
import { ToastProvider } from '../../ui';
import { WhoIsWatching } from './WhoIsWatching';

const member = (patch: Partial<DeviceMember>): DeviceMember => ({ user_id: 'm1', display_name: 'Dana', username: 'dana', role: 'viewer', switch: 'instant', active: false, ...patch });
const RING = [member({}), member({ user_id: 'owner', display_name: 'Alex', username: 'alex', role: 'admin', switch: 'password' }), member({ user_id: 'm2', display_name: 'Sam', username: 'sam', active: true })];
afterEach(() => vi.restoreAllMocks());

function setup(members = RING, variant: 'page' | 'dialog' = 'dialog') {
  const onSwitched = vi.fn(); const onSomeoneElse = vi.fn(); const onRefresh = vi.fn();
  render(<ToastProvider><WhoIsWatching members={members} onRefresh={onRefresh} onSomeoneElse={onSomeoneElse} onSwitched={onSwitched} variant={variant} /></ToastProvider>);
  return { onSwitched, onSomeoneElse, onRefresh };
}

describe('WhoIsWatching', () => {
  it('renders ring members only, with instant, password and active tiles', () => {
    setup(RING, 'page');
    expect(screen.getByRole('heading', { name: "Who's watching?" })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Continue as Dana' }).getAttribute('aria-disabled')).toBeNull();
    expect(screen.getByRole('button', { name: 'Alex, vault owner, needs password' }).textContent).toContain('Vault owner · password');
    expect(screen.getByRole('button', { name: 'Watching now: Sam' }).getAttribute('aria-disabled')).toBe('true');
    expect(screen.getByRole('button', { name: 'Someone else' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /^(Continue as|Watching now|.*needs password)/ })).toHaveLength(3);
  });

  it('switches instantly and reports success', async () => {
    const switchMember = vi.spyOn(api, 'switchMember').mockResolvedValue({ user: { id: 'm1' } } as never);
    const { onSwitched } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    expect(switchMember).toHaveBeenCalledWith('m1');
    expect(onSwitched).toHaveBeenCalledOnce();
  });

  it('sends no ring call while a switch is pending (re-review M-R2)', async () => {
    let finish!: (v: never) => void;
    const switchMember = vi.spyOn(api, 'switchMember').mockReturnValue(new Promise<never>((r) => { finish = r; }));
    const forget = vi.spyOn(api, 'forgetDeviceMember').mockResolvedValue(undefined as never);
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' })); // a double click
    await userEvent.click(screen.getByRole('button', { name: 'More for Dana' })); // the menu is off while switching
    expect(screen.queryByText('Forget on this device')).toBeNull();
    expect(switchMember).toHaveBeenCalledTimes(1);
    expect(forget).not.toHaveBeenCalled();
    finish({ user: { id: 'm1' } } as never);
  });

  it('expands a password tile and signs in with remember kept on', async () => {
    const login = vi.spyOn(api, 'loginSession').mockResolvedValue({ user: { id: 'owner' } } as never);
    const switchMember = vi.spyOn(api, 'switchMember');
    const { onSwitched } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Alex, vault owner, needs password' }));
    expect(switchMember).not.toHaveBeenCalled();
    const password = screen.getByLabelText('Password for Alex');
    expect(document.activeElement).toBe(password);
    expect(password.getAttribute('autocomplete')).toBe('current-password');
    expect(screen.getByDisplayValue('alex').getAttribute('autocomplete')).toBe('username');
    await userEvent.type(password, 'owner-passphrase{Enter}');
    expect(login).toHaveBeenCalledWith({ username: 'alex', password: 'owner-passphrase', remember_on_device: true });
    expect(onSwitched).toHaveBeenCalledOnce();
  });

  it('falls back to the password field on password_required', async () => {
    vi.spyOn(api, 'switchMember').mockRejectedValue(new ApiRequestError('password_required', 401));
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    expect(document.activeElement).toBe(await screen.findByLabelText('Password for Dana'));
  });

  it('refreshes and toasts when a member expired meanwhile; says when rate limited', async () => {
    vi.spyOn(api, 'switchMember').mockRejectedValueOnce(new ApiRequestError('not_in_ring', 404)).mockRejectedValueOnce(new ApiRequestError('Too many requests.', 429));
    const { onRefresh } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
    expect(await screen.findByText('Dana needs to sign in again.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    expect(await screen.findByText('Too many attempts. Try again in a minute.')).toBeTruthy();
  });

  it('warns that an un-remembered member will be signed out', () => {
    render(<ToastProvider><WhoIsWatching currentName="Robin" members={RING.map((entry) => ({ ...entry, active: false }))} onRefresh={vi.fn()} onSomeoneElse={vi.fn()} onSwitched={vi.fn()} variant="dialog" /></ToastProvider>);
    expect(screen.getByText('Robin will be signed out on this device.')).toBeTruthy();
  });

  it('forgets a member from the tile menu', async () => {
    const forget = vi.spyOn(api, 'forgetDeviceMember').mockResolvedValue();
    const { onRefresh } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'More for Dana' }));
    // jsdom's UA sheet hides every [popover] (no :popover-open), so the open menu is found by its text.
    await userEvent.click(screen.getByText('Forget on this device'));
    expect(forget).toHaveBeenCalledWith('m1');
    await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
    expect(screen.queryByRole('button', { name: 'More for Sam' })).toBeNull(); // the active member signs out instead
  });

  it('lets the leaving member finish before the cookie changes (1.9.0 reco events, playback warm-up)', async () => {
    const order: string[] = [];
    vi.spyOn(api, 'switchMember').mockImplementation(async () => { order.push('switch'); return { user: { id: 'm1' } } as never; });
    const beforeChange = vi.fn(async () => { order.push('before'); });
    render(<ToastProvider><WhoIsWatching beforeChange={beforeChange} members={RING} onRefresh={vi.fn()} onSomeoneElse={vi.fn()} onSwitched={vi.fn()} variant="dialog" /></ToastProvider>);
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    await waitFor(() => expect(order).toEqual(['before', 'switch']));
  });

  it('hands the leaving member back their session work when the switch fails', async () => {
    vi.spyOn(api, 'switchMember').mockRejectedValue(new ApiRequestError('Too many requests.', 429));
    const resume = vi.fn();
    const onSwitched = vi.fn();
    render(<ToastProvider><WhoIsWatching beforeChange={async () => resume} members={RING} onRefresh={vi.fn()} onSomeoneElse={vi.fn()} onSwitched={onSwitched} variant="dialog" /></ToastProvider>);
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    await waitFor(() => expect(resume).toHaveBeenCalledOnce());
    expect(onSwitched).not.toHaveBeenCalled();
  });

  it('Someone else opens the sign-in form', async () => {
    const { onSomeoneElse } = setup(RING, 'page');
    await userEvent.click(screen.getByRole('button', { name: 'Someone else' }));
    expect(onSomeoneElse).toHaveBeenCalledOnce();
  });

  it('reloads the session when a switch ends unanswered or with a server error (security review M3)', async () => {
    for (const failure of [new ApiRequestError('Request timed out.', 0), new ApiRequestError('Bad gateway', 502), new TypeError('Failed to fetch')]) {
      vi.spyOn(api, 'switchMember').mockRejectedValueOnce(failure);
      const { onSwitched } = setup();
      await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
      await waitFor(() => expect(onSwitched).toHaveBeenCalledOnce());
      cleanup();
    }
  });

  it('keeps the screen when the server refused the switch (429) and does not reload', async () => {
    vi.spyOn(api, 'switchMember').mockRejectedValue(new ApiRequestError('Too many requests.', 429));
    const { onSwitched } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    await screen.findByText(/Too many attempts/);
    expect(onSwitched).not.toHaveBeenCalled();
  });
});
