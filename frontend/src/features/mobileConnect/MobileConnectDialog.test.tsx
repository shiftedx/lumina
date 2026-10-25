import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createMobileConnect, enableMobileConnect, getMobileConnect, getMobileConnectCode, listUsers, revokeMobileConnect } from '../../api';
import { MobileConnectDialog } from './MobileConnectDialog';

vi.mock('../../api', () => ({
  createMobileConnect: vi.fn(), enableMobileConnect: vi.fn(), getMobileConnect: vi.fn(), getMobileConnectCode: vi.fn(), listUsers: vi.fn(), revokeMobileConnect: vi.fn(),
}));

const user = { id: 'u1', username: 'dana', display_name: 'Dana', role: 'viewer', is_active: true } as const;
const code = (id = 'pair-1') => ({ id, server_url: window.location.origin, expires_at: new Date(Date.now() + 300_000).toISOString(), qr_payload: 'opaque', qr_size: 29, qr_path: 'M4 4h1v1h-1z', email_sent: null });

describe('MobileConnectDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMobileConnect).mockResolvedValue({ enabled: true, email_configured: false });
    vi.mocked(getMobileConnectCode).mockResolvedValue({ status: 'pending' });
    vi.mocked(createMobileConnect).mockResolvedValue(code());
    vi.mocked(revokeMobileConnect).mockResolvedValue(undefined);
  });

  it('creates a short-lived QR for the current browser origin and replaces it when the address is overridden', async () => {
    render(<MobileConnectDialog onClose={vi.fn()} open user={user} />);
    await screen.findByRole('img', { name: 'QR code for Lumina mobile connect' });
    expect(createMobileConnect).toHaveBeenCalledWith({ server_url: window.location.origin });
    await userEvent.click(screen.getByText('Advanced'));
    await userEvent.clear(screen.getByLabelText('Server URL'));
    await userEvent.type(screen.getByLabelText('Server URL'), 'http://192.0.2.42:8096');
    vi.mocked(createMobileConnect).mockResolvedValueOnce(code('pair-2'));
    await userEvent.click(screen.getByRole('button', { name: 'Regenerate QR' }));
    await waitFor(() => expect(createMobileConnect).toHaveBeenLastCalledWith({ server_url: 'http://192.0.2.42:8096', replace_id: 'pair-1' }));
  });

  it('revokes an unshared code when the dialog closes', async () => {
    const onClose = vi.fn();
    render(<MobileConnectDialog onClose={onClose} open user={user} />);
    await screen.findByRole('img', { name: 'QR code for Lumina mobile connect' });
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledOnce();
    await waitFor(() => expect(revokeMobileConnect).toHaveBeenCalledWith('pair-1'));
  });

  it('lets an owner enable access and email a 15-minute household QR without revoking it on close', async () => {
    const owner = { ...user, role: 'admin' as const };
    vi.mocked(getMobileConnect).mockResolvedValue({ enabled: false, email_configured: true });
    vi.mocked(enableMobileConnect).mockResolvedValue({ enabled: true, email_configured: true });
    vi.mocked(listUsers).mockResolvedValue([owner, { ...user, id: 'u2', username: 'mira', display_name: 'Mira' }]);
    vi.mocked(createMobileConnect).mockImplementation(async (payload) => ({ ...code(payload.share ? 'pair-share' : 'pair-owner'), email_sent: payload.email ? true : null }));
    const onClose = vi.fn();
    render(<MobileConnectDialog onClose={onClose} open user={owner} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Enable mobile access' }));
    await screen.findByRole('img', { name: 'QR code for Lumina mobile connect' });
    await userEvent.click(screen.getByText('Share with a household member'));
    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.click(screen.getByRole('option', { name: 'Mira' }));
    await userEvent.type(screen.getByLabelText('Email'), 'mira@example.com');
    await userEvent.click(screen.getByRole('button', { name: 'Send email' }));
    await waitFor(() => expect(createMobileConnect).toHaveBeenLastCalledWith(expect.objectContaining({ share: true, user_id: 'u2', email: 'mira@example.com' })));
    expect(screen.getByRole('button', { name: 'Download QR' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(revokeMobileConnect).not.toHaveBeenCalled();
  });

  it('keeps mobile access unavailable to a viewer when the owner has not enabled it', async () => {
    vi.mocked(getMobileConnect).mockResolvedValue({ enabled: false, email_configured: false });
    render(<MobileConnectDialog onClose={vi.fn()} open user={user} />);
    expect(await screen.findByText('Ask your server owner to enable mobile access.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enable mobile access' })).toBeNull();
    expect(screen.queryByText('Share with a household member')).toBeNull();
  });

  it('explains the required owner verification step without exposing an API error code', async () => {
    vi.mocked(getMobileConnect).mockResolvedValue({ enabled: false, email_configured: false });
    vi.mocked(listUsers).mockResolvedValue([]);
    vi.mocked(enableMobileConnect).mockRejectedValue(new Error('two_factor_setup_required'));
    render(<MobileConnectDialog onClose={vi.fn()} open user={{ ...user, role: 'admin' }} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Enable mobile access' }));
    expect(await screen.findByText('Turn on two-step verification in My Settings first.')).toBeTruthy();
    expect(createMobileConnect).not.toHaveBeenCalled();
  });

  it('removes an expired QR and lets the member create another', async () => {
    vi.mocked(createMobileConnect).mockResolvedValueOnce({ ...code('expired'), expires_at: new Date(Date.now() - 1_000).toISOString() }).mockResolvedValueOnce(code('fresh'));
    render(<MobileConnectDialog onClose={vi.fn()} open user={user} />);
    await screen.findByText('This QR code expired.');
    await userEvent.click(screen.getByRole('button', { name: 'Show QR code' }));
    await screen.findByRole('img', { name: 'QR code for Lumina mobile connect' });
    expect(createMobileConnect).toHaveBeenCalledTimes(2);
  });
});
