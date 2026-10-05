import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import type { UserProfile } from './types';

/** Member access admin UI against a mocked API. */
const api = vi.hoisted(() => ({ listUsers: vi.fn(), updateUser: vi.fn(), createPasswordResetLink: vi.fn(), unlockUserSignIn: vi.fn(), resetMemberTwoFactor: vi.fn(), requestJson: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));

const { LocalAddress, MembersList, MemberInvitations, PublicAddress } = await import('./features/admin/AdminMembers');
const { MemberPage, MEMBER_TABS, todayLine } = await import('./features/admin/MemberPage');
const { inviteAccess } = await import('./features/admin/InviteDialog');
const { applyPreset, limitsSummary, librariesSummary, NO_LIMITS, PRESETS, scheduleError, copyMondayToWeekdays, withDefaults } = await import('./features/admin/memberAccess');
const { SettingsHostContext } = await import('./features/settings/settingsHost');

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
afterEach(() => { vi.resetAllMocks(); });

const person = (id: string, name: string, role: 'admin' | 'viewer', extra = {}): UserProfile => ({ id, username: name.toLowerCase(), display_name: name, role, is_active: true, ...extra } as UserProfile);
const dana = person('owner', 'Dana', 'admin');
const sections = [{ id: 'movies', label: 'Movies', count: 1204 }, { id: 'shows', label: 'TV shows', count: 310 }, { id: 'anime', label: 'Anime', count: 42 }];
const kids = { ...withDefaults(PRESETS.kids.access), sections: ['movies', 'anime'] };
const mia = person('mia', 'Mia', 'viewer', { access: kids, screen_time_today_seconds: 3900 });
const accessState = { ...kids, screen_time_today_seconds: 3900, bonus_minutes_today: 0 };

function routes(extra: Record<string, unknown> = {}) {
  const table: Record<string, unknown> = { '/api/admin/sections': sections, '/api/admin/invites': [], '/api/admin/public-address': { public_address: null }, '/api/admin/local-address': { local_address: null, lan_http: false }, '/api/admin/members/mia/access': accessState, '/api/admin/members/mia/follows': [], ...extra };
  api.requestJson.mockImplementation(async (path: string) => { if (path in table) return table[path]; throw new Error(`unmocked ${path}`); });
}
const host = (extra = {}) => ({ user: dana, onMessage: vi.fn(), onOpenSection: vi.fn(() => true), ...extra });
const inHost = (node: React.ReactNode, value = host()) => render(<SettingsHostContext.Provider value={value}>{node}</SettingsHostContext.Provider>);

describe('member access rules', () => {
  it('summarises libraries and limits in the quiet row line', () => {
    expect(librariesSummary(NO_LIMITS)).toBe('All libraries');
    expect(librariesSummary(kids, sections)).toBe('Movies, Anime');
    expect(limitsSummary(NO_LIMITS)).toBe('');
    expect(limitsSummary(kids)).toBe('G · TV-Y7 · unrated hidden · no streaming · set hours · 2 h/day · no downloads');
    expect(limitsSummary({ ...NO_LIMITS, movie_rating_max: 'PG', streaming: { ...NO_LIMITS.streaming, youtube: false }, daily_limit_minutes: 90 })).toBe('PG · no YouTube · 1 h 30 min/day');
  });

  it('presets fill limits but never change the shared libraries', () => {
    const next = applyPreset({ ...NO_LIMITS, sections: ['shows'] }, 'teen');
    expect(next).toMatchObject({ sections: ['shows'], movie_rating_max: 'PG-13', tv_rating_max: 'TV-14', daily_limit_minutes: 240, can_download: true });
    expect(applyPreset(NO_LIMITS, 'guest')).toMatchObject({ sections: null, streaming: { youtube: false, open_search: false }, can_download: false });
    expect(PRESETS.kids.access.schedule?.mon).toEqual([['07:00', '20:00']]);
    // A preset is copied, never shared: editing the result leaves the preset alone.
    applyPreset(NO_LIMITS, 'kids').schedule!.mon![0][0] = '09:00';
    expect(PRESETS.kids.access.schedule?.mon).toEqual([['07:00', '20:00']]);
  });

  it('validates schedules and copies Monday to weekdays', () => {
    expect(scheduleError(null)).toBeNull();
    expect(scheduleError({ mon: [['07:00', '20:00']] })).toBeNull();
    expect(scheduleError({ tue: [['20:00', '07:00']] })).toBe('Tuesday: each start time must be before its end time.');
    expect(scheduleError({ wed: [['', '07:00']] })).toBe('Wednesday: enter both times.');
    expect(copyMondayToWeekdays({ mon: [['08:00', '19:00']], sat: [] })).toEqual({ mon: [['08:00', '19:00']], tue: [['08:00', '19:00']], wed: [['08:00', '19:00']], thu: [['08:00', '19:00']], fri: [['08:00', '19:00']], sat: [] });
  });

  it('builds an invite payload from limits, libraries and permissions', () => {
    expect(inviteAccess('guest', ['movies'], false, true)).toMatchObject({ sections: ['movies'], can_download: false, can_request: true, streaming: { youtube: false } });
    expect(inviteAccess('none', null, true, false)).toEqual({ ...NO_LIMITS, can_request: false });
  });

  it('reads today’s usage against the saved limit and bonus', () => {
    expect(todayLine({ ...accessState, screen_time_today_seconds: 3900 })).toBe('1 h 05 min watched · 55 min left');
    expect(todayLine({ ...accessState, bonus_minutes_today: 30, screen_time_today_seconds: 9000 })).toBe('2 h 30 min watched · time is up · includes 30 min extra');
    expect(todayLine({ ...NO_LIMITS, screen_time_today_seconds: 0 })).toBe('Nothing watched yet');
  });

  it('gives every member-page row a unique members.* id and 2–3 sentence ⓘ text', () => {
    const rows = MEMBER_TABS.flatMap((tab) => tab.entries);
    expect(MEMBER_TABS.map((tab) => tab.label)).toEqual(['Profile', 'Libraries', 'Ratings', 'Streaming', 'Schedule', 'Permissions', 'Activity']);
    expect(new Set(rows.map((row) => row.id)).size).toBe(rows.length);
    for (const row of rows) {
      expect(row.id.startsWith('members.'), row.id).toBe(true);
      expect((row.info.match(/[.!?](?=\s|$)/g) ?? []).length, row.id).toBeGreaterThanOrEqual(2);
      expect((row.info.match(/[.!?](?=\s|$)/g) ?? []).length, row.id).toBeLessThanOrEqual(3);
      expect(row.advanced, row.id).toBeUndefined(); // schedule and ratings are essential, never advanced
    }
  });
});

describe('Members list', () => {
  it('shows each member with role, libraries, limits and today’s screen time; owners read “Owner — no limits”', async () => {
    routes();
    api.listUsers.mockResolvedValue([dana, mia, person('sam', 'Sam', 'viewer', { access: null, is_active: false })]);
    const onOpenMember = vi.fn();
    render(<MembersList onOpenMember={onOpenMember} />);
    const list = await screen.findByRole('list', { name: 'Household members' });
    const miaRow = await within(list).findByRole('link', { name: /Mia/ });
    await waitFor(() => expect(miaRow.textContent).toContain('Household member · Movies, Anime · G · TV-Y7'));
    expect(miaRow.textContent).toContain('1 h 05 min today');
    expect(miaRow.getAttribute('href')).toBe('/settings/members/mia');
    expect(within(list).getByRole('link', { name: /Dana/ }).textContent).toContain('Vault owner · Owner — no limits');
    expect(within(list).getByRole('link', { name: /Sam/ }).textContent).toContain('Deactivated');
    expect(within(list).getByRole('link', { name: /Sam/ }).textContent).toContain('All libraries · no limits');
    await userEvent.click(miaRow);
    expect(onOpenMember).toHaveBeenCalledWith('mia');
  });
});

describe('Invitations', () => {
  async function openDialog(extra = {}) {
    routes(extra);
    const onMessage = vi.fn();
    render(<MemberInvitations onMessage={onMessage} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Invite someone' }));
    return { dialog: await screen.findByRole('dialog', { name: 'Invite someone' }), onMessage };
  }

  it('sends an invite with the chosen libraries, limits and permissions', async () => {
    const { dialog } = await openDialog();
    api.post.mockResolvedValue({ id: 'i9', email: 'ana@example.com', status: 'pending', invitation_url: 'https://lumina.example.com/#invite=t', email_sent: true });
    await userEvent.type(within(dialog).getByLabelText(/Email/), 'ana@example.com');
    await userEvent.type(within(dialog).getByLabelText('Name'), 'Ana');
    await userEvent.click(await within(dialog).findByRole('checkbox', { name: 'Movies' }));
    await userEvent.selectOptions(within(dialog).getByLabelText('Limits'), 'kids');
    await userEvent.click(within(dialog).getByRole('checkbox', { name: 'Request movies and shows' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Send email' }));
    expect(api.post).toHaveBeenCalledWith('/api/admin/invites', { email: 'ana@example.com', display_name: 'Ana', send: true, access: inviteAccess('kids', ['movies'], false, true) });
    const ready = await screen.findByRole('dialog', { name: 'Invitation ready' });
    expect(ready.textContent).toContain('Sent to ana@example.com');
    expect((within(ready).getByLabelText('Invite link') as HTMLInputElement).value).toBe('https://lumina.example.com/#invite=t');
  });

  it('when email is not set up, says so and shows the link to copy', async () => {
    const { dialog } = await openDialog();
    api.post.mockResolvedValue({ id: 'i9', email: 'ana@example.com', status: 'pending', invitation_url: 'http://lan/#invite=t', email_sent: false });
    await userEvent.type(within(dialog).getByLabelText(/Email/), 'ana@example.com');
    await userEvent.click(within(dialog).getByRole('checkbox', { name: 'All libraries' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Send email' }));
    const ready = await screen.findByRole('dialog', { name: 'Invitation ready' });
    expect(ready.textContent).toContain('Email isn’t set up, so nothing was sent');
    expect((within(ready).getByLabelText('Invite link') as HTMLInputElement).value).toBe('http://lan/#invite=t');
    expect(api.post.mock.calls[0][1]).toMatchObject({ send: true, access: { sections: null } });
  });

  it('copy link creates the invite without sending, and checks email and libraries first', async () => {
    const { dialog } = await openDialog();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Copy link instead' }));
    expect(await within(dialog).findByText('Enter an email address like name@example.com.')).not.toBeNull();
    await userEvent.type(within(dialog).getByLabelText(/Email/), 'ana@example.com');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Copy link instead' }));
    expect(await within(dialog).findByText('Choose at least one library to share.')).not.toBeNull();
    expect(api.post).not.toHaveBeenCalled();
    api.post.mockResolvedValue({ id: 'i9', email: 'ana@example.com', status: 'pending', invitation_url: 'http://lan/#invite=t', email_sent: false });
    await userEvent.click(within(dialog).getByRole('checkbox', { name: 'All libraries' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Copy link instead' }));
    expect(api.post.mock.calls[0][1]).toMatchObject({ send: false });
    expect((await screen.findByRole('dialog', { name: 'Invitation ready' })).textContent).toContain('Send this link to them yourself');
  });

  it('notes a missing public address with a way to set it', async () => {
    const { dialog } = await openDialog();
    expect(await within(dialog).findByRole('button', { name: 'Set the public address' })).not.toBeNull();
  });

  it('lists waiting invites with expiry; resend renews and revoke deletes', async () => {
    const waiting = { id: 'i1', email: 'ana@example.com', status: 'pending', created_at: '2026-10-01T09:00:00Z', expires_at: '2026-10-08T09:00:00Z', sent_at: '2026-10-01T09:00:00Z', libraries: ['Movies'], access: { display_name: 'Ana' } };
    routes({ '/api/admin/invites': [waiting, { ...waiting, id: 'i2', email: 'old@example.com', status: 'used' }] });
    const onMessage = vi.fn();
    render(<MemberInvitations onMessage={onMessage} />);
    const list = await screen.findByRole('list', { name: 'Waiting invitations' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(1);
    expect(list.textContent).toContain('Ana (ana@example.com)');
    expect(list.textContent).toMatch(/Movies · Sent .* · Expires /);
    api.post.mockResolvedValue({ ...waiting, invitation_url: 'http://lan/#invite=new', email_sent: false });
    await userEvent.click(within(list).getByRole('button', { name: 'Resend invitation to ana@example.com' }));
    expect(api.post).toHaveBeenCalledWith('/api/admin/invites/i1/resend');
    const renewed = await screen.findByRole('dialog', { name: 'Invitation renewed' });
    expect((within(renewed).getByLabelText('Invite link') as HTMLInputElement).value).toBe('http://lan/#invite=new');
    await userEvent.click(within(renewed).getByRole('button', { name: 'Done' }));
    api.del.mockResolvedValue(undefined);
    await userEvent.click(within(list).getByRole('button', { name: 'Revoke invitation to ana@example.com' }));
    expect(api.del).toHaveBeenCalledWith('/api/admin/invites/i1');
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Waiting invitations' })).toBeNull());
    expect(onMessage).toHaveBeenCalledWith('Invitation revoked. Its link no longer works.');
  });

  it('saves the public address and shows the server’s refusal', async () => {
    routes();
    render(<PublicAddress />);
    const field = await screen.findByRole('textbox', { name: 'Public address' });
    await userEvent.type(field, 'lumina.example.com');
    api.put.mockRejectedValueOnce(new Error('Enter a bare http(s)://host origin.'));
    await userEvent.click(screen.getByRole('button', { name: 'Save address' }));
    expect(await screen.findByText('Enter a bare http(s)://host origin.')).not.toBeNull();
    api.put.mockResolvedValueOnce({ public_address: 'https://lumina.example.com' });
    await userEvent.clear(field);
    await userEvent.type(field, 'https://lumina.example.com');
    await userEvent.click(screen.getByRole('button', { name: 'Save address' }));
    expect(api.put).toHaveBeenLastCalledWith('/api/admin/public-address', { public_address: 'https://lumina.example.com' });
    expect(await screen.findByText('Saved')).not.toBeNull();
  });

  it('saves the local address, and explains it needs LAN HTTP mode', async () => {
    routes({ '/api/admin/local-address': { local_address: null, lan_http: true } });
    render(<LocalAddress />);
    const field = await screen.findByRole('textbox', { name: 'Local address' });
    api.put.mockResolvedValueOnce({ local_address: 'http://lumina.home.arpa', lan_http: true });
    await userEvent.type(field, 'http://lumina.home.arpa');
    await userEvent.click(screen.getByRole('button', { name: 'Save address' }));
    expect(api.put).toHaveBeenLastCalledWith('/api/admin/local-address', { local_address: 'http://lumina.home.arpa' });
    expect(await screen.findByText('Saved')).not.toBeNull();
  });

  it('turns the local address off outside LAN HTTP mode', async () => {
    routes();
    render(<LocalAddress />);
    expect(await screen.findByText(/LAN HTTP mode/)).not.toBeNull();
    expect((screen.getByRole('textbox', { name: 'Local address' }) as HTMLInputElement).disabled).toBe(true);
  });
});

describe('Member page', () => {
  async function openMia(value = host()) {
    routes();
    api.listUsers.mockResolvedValue([dana, mia]);
    const onBack = vi.fn();
    inHost(<MemberPage memberId="mia" onBack={onBack} />, value);
    await screen.findByRole('heading', { name: 'Mia' });
    const tab = (name: string) => userEvent.click(screen.getByRole('tab', { name }));
    return { onBack, tab };
  }
  const save = () => userEvent.click(screen.getByRole('button', { name: 'Save' }));

  it('has the seven tabs and saves every tab’s edits in one PUT', async () => {
    const { tab } = await openMia();
    expect(screen.getAllByRole('tab').map((entry) => entry.textContent)).toEqual(['Profile', 'Libraries', 'Ratings', 'Streaming', 'Schedule', 'Permissions', 'Activity']);
    await tab('Libraries');
    await userEvent.click(screen.getByRole('checkbox', { name: 'TV shows' }));
    await tab('Ratings');
    await userEvent.click(screen.getByRole('radio', { name: 'PG' }));
    await userEvent.click(within(screen.getByRole('group', { name: 'TV up to' })).getByRole('radio', { name: 'No limit' }));
    await tab('Streaming');
    await userEvent.click(screen.getByRole('switch', { name: 'YouTube' }));
    await tab('Schedule');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Daily limit' }), '90');
    await tab('Permissions');
    await userEvent.click(screen.getByRole('switch', { name: 'Downloads' }));
    api.put.mockImplementation(async (_path: string, body: object) => ({ ...accessState, ...body }));
    await save();
    expect(api.put).toHaveBeenCalledWith('/api/admin/members/mia/access', {
      ...kids, sections: ['movies', 'shows', 'anime'], movie_rating_max: 'PG', tv_rating_max: null, streaming: { ...kids.streaming, youtube: true }, daily_limit_minutes: 90, can_download: true,
    });
    expect(api.updateUser).not.toHaveBeenCalled();
    expect(await screen.findByText('Saved.')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true);
  });

  it('All libraries sends null; turning it off starts from every library', async () => {
    const { tab } = await openMia();
    await tab('Libraries');
    const all = screen.getByRole('switch', { name: 'All libraries' });
    await userEvent.click(all);
    expect(screen.queryByRole('checkbox', { name: 'Movies' })).toBeNull();
    api.put.mockResolvedValue({ ...accessState, sections: null });
    await save();
    expect(api.put.mock.calls[0][1]).toMatchObject({ sections: null });
    await userEvent.click(all);
    expect(screen.getAllByRole('checkbox').every((box) => (box as HTMLInputElement).checked)).toBe(true);
  });

  it('a preset fills the form for review; Discard brings back the saved access', async () => {
    const { tab } = await openMia();
    await userEvent.click(screen.getByRole('button', { name: 'Apply a preset' }));
    await userEvent.click(screen.getByRole('menuitem', { hidden: true, name: /Teen/ }));
    expect(screen.getByText('Teen limits filled in. Review them, then save.')).not.toBeNull();
    await tab('Ratings');
    expect((screen.getByRole('radio', { name: 'PG-13' }) as HTMLInputElement).checked).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect((screen.getByRole('radio', { name: 'G' }) as HTMLInputElement).checked).toBe(true);
  });

  it('edits weekday hours, copies Monday to weekdays and refuses a backwards range', async () => {
    const { tab } = await openMia();
    await tab('Schedule');
    expect((screen.getByRole('switch', { name: 'Viewing hours' }) as HTMLInputElement).checked).toBe(true);
    const monday = screen.getByRole('group', { name: 'Monday' });
    fireEvent.change(within(monday).getByLabelText('Monday from'), { target: { value: '08:30' } });
    await userEvent.click(within(screen.getByRole('group', { name: 'Sunday' })).getByRole('button', { name: 'Remove Sunday 07:00–20:00' }));
    expect(within(screen.getByRole('group', { name: 'Sunday' })).getByText('No watching')).not.toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Copy Monday to weekdays' }));
    expect((within(screen.getByRole('group', { name: 'Friday' })).getByLabelText('Friday from') as HTMLInputElement).value).toBe('08:30');
    fireEvent.change(within(screen.getByRole('group', { name: 'Saturday' })).getByLabelText('Saturday until'), { target: { value: '06:00' } });
    await save();
    expect(api.put).not.toHaveBeenCalled();
    expect(screen.getAllByText('Saturday: each start time must be before its end time.').length).toBeGreaterThan(0);
    fireEvent.change(within(screen.getByRole('group', { name: 'Saturday' })).getByLabelText('Saturday until'), { target: { value: '21:00' } });
    api.put.mockResolvedValue(accessState);
    await save();
    expect(api.put.mock.calls[0][1].schedule).toMatchObject({ mon: [['08:30', '20:00']], fri: [['08:30', '20:00']], sat: [['07:00', '21:00']], sun: [] });
  });

  it('Streaming: follows channels for the member and unfollows them at once, outside Save', async () => {
    const cats = { id: 'f1', label: 'Cats', source_url: 'https://www.youtube.com/@cats' };
    routes({ '/api/admin/members/mia/follows': [cats] });
    api.listUsers.mockResolvedValue([dana, mia]);
    inHost(<MemberPage memberId="mia" onBack={vi.fn()} />);
    await screen.findByRole('heading', { name: 'Mia' });
    await userEvent.click(screen.getByRole('tab', { name: 'Streaming' }));
    const list = await screen.findByRole('list', { name: 'Followed channels' });
    expect(list.textContent).toContain('Cats');
    const dogs = { id: 'f2', label: 'Dogs', source_url: 'https://www.youtube.com/@dogs' };
    api.post.mockResolvedValue([cats, dogs]);
    await userEvent.type(screen.getByRole('textbox', { name: 'Channel address' }), 'youtube.com/@dogs{Enter}');
    expect(api.post).toHaveBeenCalledWith('/api/admin/members/mia/follows', { source_url: 'youtube.com/@dogs' });
    expect(await within(list).findByText('Dogs')).not.toBeNull();
    expect((screen.getByRole('textbox', { name: 'Channel address' }) as HTMLInputElement).value).toBe('');
    api.del.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole('button', { name: 'Unfollow Cats' }));
    expect(api.del).toHaveBeenCalledWith('/api/admin/members/mia/follows/f1');
    await waitFor(() => expect(within(list).queryByText('Cats')).toBeNull());
    expect(api.put).not.toHaveBeenCalled();
  });

  it('+30 min today posts the bonus and shows the new time left', async () => {
    const { tab } = await openMia();
    await tab('Schedule');
    expect(screen.getByText('1 h 05 min watched · 55 min left')).not.toBeNull();
    api.post.mockResolvedValue({ ...accessState, bonus_minutes_today: 30 });
    await userEvent.click(screen.getByRole('button', { name: '+30 min today' }));
    expect(api.post).toHaveBeenCalledWith('/api/admin/members/mia/access/bonus', { minutes: 30 });
    expect(await screen.findByText('1 h 05 min watched · 1 h 25 min left · includes 30 min extra')).not.toBeNull();
  });

  it('Profile: renames through the Save form; role and deactivation are confirmed and apply at once', async () => {
    const value = host();
    await openMia(value);
    const name = screen.getByRole('textbox', { name: 'Display name' });
    await userEvent.clear(name);
    await userEvent.type(name, 'Mia R');
    api.updateUser.mockResolvedValue({ ...mia, display_name: 'Mia R' });
    await save();
    expect(api.updateUser).toHaveBeenCalledWith('mia', { display_name: 'Mia R' });
    expect(api.put).not.toHaveBeenCalled();

    api.updateUser.mockRejectedValueOnce(new Error('At least one active vault owner is required'));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Role for Mia R' }), 'admin');
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Make Mia R a vault owner?' })).getByRole('button', { name: 'Make Mia R a vault owner' }));
    expect(await screen.findByText('At least one active vault owner is required')).not.toBeNull();
    expect((screen.getByRole('combobox', { name: 'Role for Mia R' }) as HTMLSelectElement).value).toBe('viewer');

    await userEvent.click(screen.getByRole('button', { name: 'Deactivate' }));
    const dialog = screen.getByRole('dialog', { name: 'Deactivate Mia R?' });
    expect(dialog.textContent).toContain('signed out on every device');
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }));
    api.updateUser.mockResolvedValueOnce({ ...mia, display_name: 'Mia R', is_active: false });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Deactivate Mia R' }));
    expect(api.updateUser).toHaveBeenLastCalledWith('mia', { is_active: false });
    expect(await screen.findByRole('button', { name: 'Reactivate' })).not.toBeNull();
    expect(value.onMessage).toHaveBeenCalledWith('Mia R’s access was updated.');
  });

  it('Profile: a reset link shows once in a dialog', async () => {
    await openMia();
    api.createPasswordResetLink.mockResolvedValue({ reset_url: 'http://lan/#reset=xyz' });
    await userEvent.click(screen.getByRole('button', { name: 'Create reset link' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reset link' });
    expect((within(dialog).getByLabelText('Reset link') as HTMLInputElement).value).toBe('http://lan/#reset=xyz');
  });

  it('Profile: a member locked out by wrong passwords can be unlocked', async () => {
    routes();
    api.listUsers.mockResolvedValue([dana, { ...mia, sign_in_locked: true }]);
    inHost(<MemberPage memberId="mia" onBack={vi.fn()} />);
    await screen.findByRole('heading', { name: 'Mia' });
    expect(screen.getByText(/Sign-in paused/)).not.toBeNull();
    api.unlockUserSignIn.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole('button', { name: 'Unlock sign-in' }));
    expect(api.unlockUserSignIn).toHaveBeenCalledWith('mia');
    await waitFor(() => expect(screen.queryByText(/Sign-in paused/)).toBeNull());
  });

  it('Profile: shows two-step status, and Reset asks first, then clears it', async () => {
    routes();
    api.listUsers.mockResolvedValue([dana, { ...mia, two_factor_enabled: true }]);
    api.resetMemberTwoFactor.mockResolvedValue(undefined);
    inHost(<MemberPage memberId="mia" onBack={vi.fn()} />);
    await screen.findByRole('heading', { name: 'Mia' });
    const row = document.querySelector<HTMLElement>('[data-setting-id="members.two-factor"]') as HTMLElement;
    expect(within(row).getByText('On')).not.toBeNull();
    await userEvent.click(within(row).getByRole('button', { name: 'Reset two-step verification' }));
    const dialog = screen.getByRole('dialog', { name: 'Reset two-step verification for Mia?' });
    expect(within(dialog).getByText(/sign in with just their password/)).not.toBeNull();
    expect(api.resetMemberTwoFactor).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reset two-step verification' }));
    await waitFor(() => expect(api.resetMemberTwoFactor).toHaveBeenCalledWith('mia'));
    await waitFor(() => expect(within(row).getByText('Off')).not.toBeNull());
  });

  it('Permissions link to request policies; Activity loads recent playback and screen time', async () => {
    const value = host();
    const { tab } = await openMia(value);
    await tab('Permissions');
    await userEvent.click(screen.getByRole('button', { name: 'Open request policies' }));
    expect(value.onOpenSection).toHaveBeenCalledWith('requests');
    routes({ '/api/admin/members/mia/activity': { screen_time_today_seconds: 3900, recent: [{ id: 'h1', title: 'Bluey', subtitle: 'S1 E3', started_at: '2026-10-04T08:00:00Z', watched_seconds: 420, client: { name: 'Lumina web' } }] } });
    await tab('Activity');
    expect(await screen.findByText('1 h 05 min')).not.toBeNull();
    expect(screen.getByRole('list', { name: 'Recent playback' }).textContent).toContain('Bluey');
  });

  it('an owner page shows only Profile and Activity, and the signed-in owner cannot demote or deactivate themself', async () => {
    routes();
    api.listUsers.mockResolvedValue([dana, mia]);
    inHost(<MemberPage memberId="owner" onBack={vi.fn()} />);
    await screen.findByRole('heading', { name: 'Dana' });
    expect(screen.getAllByRole('tab').map((entry) => entry.textContent)).toEqual(['Profile', 'Activity']);
    expect(screen.getByText(/Owner — no limits/)).not.toBeNull();
    expect(screen.getByRole('combobox', { name: 'Role for Dana' }).hasAttribute('disabled')).toBe(true);
    expect(screen.queryByRole('button', { name: 'Deactivate' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Create reset link' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Apply a preset' })).toBeNull();
    expect(api.requestJson).not.toHaveBeenCalledWith('/api/admin/members/owner/access');
  });

  it('tabs move with the arrow keys and Back returns to the list', async () => {
    const { onBack } = await openMia();
    const profile = screen.getByRole('tab', { name: 'Profile' });
    profile.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Libraries' }));
    expect(screen.getByRole('tab', { name: 'Libraries' }).getAttribute('aria-selected')).toBe('true');
    await userEvent.keyboard('{End}');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Activity' }));
    await userEvent.click(screen.getByRole('button', { name: 'Members' }));
    expect(onBack).toHaveBeenCalled();
  });
});
