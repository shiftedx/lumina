import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../api';
import { openMemberPicker } from '../../app/commands';
import type { DeviceMember, UserProfile } from '../../types';
import { refreshDeviceRing, resetDeviceRing, ringCall } from './deviceRing';
import { MemberPickerHost, useDeviceRingSize } from './memberPicker';

const user = { id: 'm2', username: 'sam', display_name: 'Sam', role: 'viewer', is_active: true } as UserProfile;
const RING: DeviceMember[] = [
  { user_id: 'm1', display_name: 'Dana', username: 'dana', role: 'viewer', switch: 'instant', active: false },
  { user_id: 'm2', display_name: 'Sam', username: 'sam', role: 'viewer', switch: 'instant', active: true },
];
beforeEach(() => resetDeviceRing());
afterEach(() => vi.restoreAllMocks());

function Size() { const size = useDeviceRingSize(); return <p>size:{size === null ? 'unknown' : size}</p>; }

describe('member picker host', () => {
  it('opens on the command with the ring members, and counts the others', async () => {
    vi.spyOn(api, 'getDeviceMembers').mockResolvedValue(RING);
    render(<><MemberPickerHost currentUser={user} onSwitched={vi.fn()} /><Size /></>);
    await waitFor(() => expect(screen.getByText('size:1')).toBeTruthy());
    act(() => openMemberPicker());
    expect(await screen.findByRole('dialog', { name: "Who's watching?" })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Continue as Dana' })).toBeTruthy();
  });

  it('Someone else shows the sign-in form, which waits for the leaving member before signing in', async () => {
    vi.spyOn(api, 'getDeviceMembers').mockResolvedValue(RING);
    const order: string[] = [];
    const login = vi.spyOn(api, 'loginSession').mockImplementation(async () => { order.push('login'); return { user: { id: 'm3' } } as never; });
    const onSwitched = vi.fn();
    render(<MemberPickerHost beforeChange={async () => { order.push('before'); }} currentUser={user} onSwitched={onSwitched} />);
    act(() => openMemberPicker());
    await userEvent.click(await screen.findByRole('button', { name: 'Someone else' }));
    expect(await screen.findByRole('dialog', { name: 'Sign in' })).toBeTruthy();
    await userEvent.type(screen.getByLabelText('Username'), 'robin');
    await userEvent.type(screen.getByLabelText('Password'), 'robin-passphrase');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(onSwitched).toHaveBeenCalledOnce());
    expect(login).toHaveBeenCalledWith({ username: 'robin', password: 'robin-passphrase', remember_on_device: false });
    expect(order).toEqual(['before', 'login']);
  });
});

describe('device ring calls (re-review M-R2)', () => {
  it('a ring refresh waits for a pending switch, and a switch waits for an in-flight refresh', async () => {
    const order: string[] = [];
    let answer!: (members: DeviceMember[]) => void;
    vi.spyOn(api, 'getDeviceMembers').mockImplementation(() => { order.push('list'); return new Promise((resolve) => { answer = resolve; }); });
    const refreshed = refreshDeviceRing();
    const switched = ringCall(async () => { order.push('switch'); });
    await Promise.resolve();
    expect(order).toEqual(['list']); // the switch is not sent while the list is out
    answer(RING);
    await Promise.all([refreshed, switched]);
    expect(order).toEqual(['list', 'switch']);

    let done!: () => void;
    const pending = ringCall(() => { order.push('switch 2'); return new Promise<void>((resolve) => { done = resolve; }); });
    resetDeviceRing();
    const again = refreshDeviceRing();
    await new Promise((resolve) => { setTimeout(resolve, 10); });
    expect(order).toEqual(['list', 'switch', 'switch 2']); // no list while the switch is pending
    done();
    await waitFor(() => expect(order.at(-1)).toBe('list'));
    answer(RING);
    await Promise.all([pending, again]);
  });
});
