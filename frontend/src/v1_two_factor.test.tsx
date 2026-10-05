import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AppPasswordCreated, ConnectedApp, UserProfile } from './types';

const api = vi.hoisted(() => ({
  getTwoFactor: vi.fn(), setupTwoFactor: vi.fn(), enableTwoFactor: vi.fn(), disableTwoFactor: vi.fn(), regenerateRecoveryCodes: vi.fn(),
  listConnectedApps: vi.fn(), createAppPassword: vi.fn(), revokeConnectedApp: vi.fn(), signOutAllApps: vi.fn(), createAgentToken: vi.fn(),
  getAdminSettings: vi.fn(), updateAdminSettings: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { TwoFactorSettings } = await import('./features/settings/TwoFactorSettings');
const { ConnectedApps } = await import('./features/settings/ConnectedApps');
const { OwnerTwoFactorSwitch } = await import('./features/admin/AdminMembers');
const { SettingsHostContext } = await import('./features/settings/settingsHost');

afterEach(() => vi.resetAllMocks());
const user = { id: 'u1', username: 'one', role: 'admin', is_active: true } as UserProfile;
const host = (patch = {}, onOpenSection = vi.fn()) => ({ user: { ...user, ...patch }, onOpenSection });

describe('Two-step verification enrollment', () => {
  it('walks password, QR and key, code, recovery codes once, then the app-password prompt', async () => {
    api.getTwoFactor.mockResolvedValue({ enabled: false, recovery_codes_left: 0, required: false });
    api.setupTwoFactor.mockResolvedValue({ secret: 'ABCDEFGHJKLMNPQR', otpauth_uri: 'otpauth://totp/Lumina:one?secret=ABCDEFGHJKLMNPQR', qr_size: 29, qr_path: 'M4 4h1v1h-1z' });
    api.enableTwoFactor.mockResolvedValue({ recovery_codes: ['aaaa-bbbb-cccc-dddd', 'eeee-ffff-gggg-hhhh'] });
    const onOpenSection = vi.fn();
    const { SettingsHostContext: Ctx } = { SettingsHostContext };
    render(<Ctx.Provider value={host({ two_factor_setup_required: true }, onOpenSection)}><TwoFactorSettings /></Ctx.Provider>);
    expect((await screen.findByRole('alert')).textContent).toContain('requires two-step verification');
    await userEvent.click(screen.getByRole('button', { name: 'Turn on' }));
    await userEvent.type(screen.getByLabelText('Your password'), 'pw-pw-pw-pw-pw');
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(api.setupTwoFactor).toHaveBeenCalledWith('pw-pw-pw-pw-pw');
    const qr = await screen.findByRole('img', { name: 'QR code for your authenticator app' });
    expect(qr.getAttribute('viewBox')).toBe('0 0 29 29');
    expect(qr.querySelector('path')?.getAttribute('d')).toBe('M4 4h1v1h-1z');
    expect((screen.getByLabelText('Setup key (if you cannot scan)') as HTMLInputElement).value).toBe('ABCD EFGH JKLM NPQR');
    await userEvent.type(screen.getByLabelText('6-digit code'), '123456');
    api.getTwoFactor.mockResolvedValue({ enabled: true, recovery_codes_left: 2, required: false });
    await userEvent.click(screen.getByRole('button', { name: 'Turn on' }));
    expect(api.enableTwoFactor).toHaveBeenCalledWith('123456');
    const codes = await screen.findByRole('list', { name: 'Recovery codes' });
    expect(within(codes).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['aaaa-bbbb-cccc-dddd', 'eeee-ffff-gggg-hhhh']);
    expect(screen.getByRole('button', { name: 'Download .txt' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'I saved these' }));
    expect(screen.queryByText('aaaa-bbbb-cccc-dddd')).toBeNull();
    expect(screen.getByText(/Use Infuse or another app/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Create an app password' }));
    expect(onOpenSection).toHaveBeenCalledWith('apps');
  });

  it('shows a wrong password beside the field and keeps recovery-code count visible when on', async () => {
    api.getTwoFactor.mockResolvedValue({ enabled: true, recovery_codes_left: 7, required: true });
    render(<SettingsHostContext.Provider value={host()}><TwoFactorSettings /></SettingsHostContext.Provider>);
    expect(await screen.findByText('On. 7 recovery codes left.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Turn off' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'New recovery codes' }));
    api.regenerateRecoveryCodes.mockRejectedValue(new Error('Wrong password or code.'));
    await userEvent.type(screen.getByLabelText('Your password'), 'x');
    await userEvent.type(screen.getByLabelText('Code'), '000000');
    await userEvent.click(screen.getByRole('button', { name: 'Make new codes' }));
    expect(await screen.findByText('Wrong password or code.')).toBeTruthy();
    expect(api.regenerateRecoveryCodes).toHaveBeenCalledWith('x', '000000');
  });
});

describe('Connected apps: app passwords', () => {
  const row: ConnectedApp = { id: 'p1', user_id: 'u1', kind: 'app_password', scope: 'write', device_name: "Infuse on Dana's Apple TV", created_at: '2026-10-01T10:00:00Z', last_seen_at: null };
  const created: AppPasswordCreated = { app: row, password: 'apw-secret-1234', username: 'one', server_address: null };

  it('creates an app password and shows address, username and password with Copy', async () => {
    api.listConnectedApps.mockResolvedValue([]);
    api.createAppPassword.mockResolvedValue(created);
    render(<ConnectedApps user={user} />);
    await userEvent.type(await screen.findByLabelText('App name'), "Infuse on Dana's Apple TV");
    await userEvent.click(screen.getByRole('button', { name: 'Create app password' }));
    expect(api.createAppPassword).toHaveBeenCalledWith("Infuse on Dana's Apple TV");
    expect((await screen.findByLabelText('App password') as HTMLInputElement).value).toBe('apw-secret-1234');
    expect((screen.getByLabelText('Server address') as HTMLInputElement).value).toBe(window.location.origin);
    expect((screen.getByLabelText('Username') as HTMLInputElement).value).toBe('one');
    expect(screen.getByRole('button', { name: 'Copy app password' })).toBeTruthy();
    expect(screen.getByRole('table').textContent).toContain('App password');
  });

  it('shows the server detail when creating is refused', async () => {
    api.listConnectedApps.mockResolvedValue([]);
    api.createAppPassword.mockRejectedValue(new Error('Use a signed-in browser session.'));
    render(<ConnectedApps user={user} />);
    await userEvent.type(await screen.findByLabelText('App name'), 'x');
    await userEvent.click(screen.getByRole('button', { name: 'Create app password' }));
    expect(await screen.findByText('Use a signed-in browser session.')).toBeTruthy();
  });

  it('signs out all apps only after confirming, then refreshes', async () => {
    api.listConnectedApps.mockResolvedValue([row]);
    api.signOutAllApps.mockResolvedValue({ revoked: 2 });
    render(<ConnectedApps user={user} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out all apps' }));
    expect(api.signOutAllApps).not.toHaveBeenCalled();
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Sign out all apps' }));
    await waitFor(() => expect(api.signOutAllApps).toHaveBeenCalledOnce());
    expect(await screen.findByText('Signed out 2 apps. App passwords still work.')).toBeTruthy();
    expect(api.listConnectedApps).toHaveBeenCalledTimes(2);
  });
});

describe('Household rule', () => {
  it('saves the owner rule and shows the server detail when refused', async () => {
    api.getAdminSettings.mockResolvedValue({ require_owner_two_factor: false });
    api.updateAdminSettings.mockRejectedValueOnce(new Error('Turn on two-step verification for yourself first.')).mockResolvedValueOnce({});
    render(<OwnerTwoFactorSwitch />);
    const toggle = await screen.findByRole('switch', { name: 'Require two-step verification for vault owners' });
    await userEvent.click(toggle);
    expect(await screen.findByText('Turn on two-step verification for yourself first.')).toBeTruthy();
    await userEvent.click(toggle);
    await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true));
    expect(api.updateAdminSettings).toHaveBeenLastCalledWith({ require_owner_two_factor: true });
  });
});
