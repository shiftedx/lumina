import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ConnectedApp, UserProfile } from './types';

const api = vi.hoisted(() => ({ listConnectedApps: vi.fn(), createAgentToken: vi.fn(), revokeConnectedApp: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { ConnectedApps } = await import('./features/settings/ConnectedApps');

afterEach(() => vi.resetAllMocks());

const member = { id: 'u1', username: 'one', role: 'viewer', is_active: true } as UserProfile;
const admin = { ...member, id: 'a1', role: 'admin' } as UserProfile;
const infuse: ConnectedApp = { id: 'd1', user_id: 'u1', owner_display_name: 'One', kind: 'jellyfin', scope: 'write', device_name: 'Living room Apple TV', client: 'Infuse-Direct', client_version: '8.1', created_at: '2026-09-20T10:00:00Z', last_seen_at: '2026-09-25T08:00:00Z' };
const TOKEN = 'lum_agent_7f3c9d2e0b1a';

describe('Connected apps', () => {
  it('shows a new agent token once, and never again after leaving', async () => {
    api.listConnectedApps.mockResolvedValue([infuse]);
    api.createAgentToken.mockResolvedValue({ app: { ...infuse, id: 'd2', kind: 'agent', scope: 'read', device_name: 'Home Assistant', client: null, client_version: null, last_seen_at: null }, token: TOKEN });
    const view = render(<ConnectedApps user={member} />);
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Living room Apple TV')).toBeTruthy();
    expect(within(table).getByText('Jellyfin app')).toBeTruthy();
    expect(within(table).queryByRole('columnheader', { name: 'Owner' })).toBeNull();
    await userEvent.type(screen.getByLabelText('Token name'), 'Home Assistant');
    await userEvent.click(screen.getByRole('button', { name: 'Create agent token' }));
    expect(api.createAgentToken).toHaveBeenCalledWith({ name: 'Home Assistant', scope: 'read' });
    const token = await screen.findByRole('textbox', { name: 'Token for Home Assistant' }) as HTMLInputElement;
    expect(token.value).toBe(TOKEN);
    expect(token.readOnly).toBe(true);
    expect(screen.getByText('Copy this token now. Lumina shows it only once.')).toBeTruthy();
    expect(window.localStorage.length).toBe(0);
    view.unmount();
    api.listConnectedApps.mockResolvedValue([infuse]);
    render(<ConnectedApps user={member} />);
    await screen.findByRole('table');
    expect(screen.queryByDisplayValue(TOKEN)).toBeNull();
  });

  it('revoking an app asks first and does nothing on cancel', async () => {
    api.listConnectedApps.mockResolvedValue([infuse]);
    api.revokeConnectedApp.mockResolvedValue(undefined);
    render(<ConnectedApps user={member} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke Living room Apple TV' }));
    const dialog = screen.getByRole('dialog', { name: 'Revoke Living room Apple TV?' });
    expect(within(dialog).getByText('It will have to sign in again to reach this vault.')).toBeTruthy();
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(api.revokeConnectedApp).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Revoke Living room Apple TV' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(api.revokeConnectedApp).toHaveBeenCalledWith('d1'));
    expect(await screen.findByText('Living room Apple TV was revoked. It has to sign in again.')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('shows admins every member’s apps with an owner column', async () => {
    api.listConnectedApps.mockResolvedValue([infuse]);
    render(<ConnectedApps user={admin} />);
    const table = await screen.findByRole('table');
    expect(within(table).getByRole('columnheader', { name: 'Owner' })).toBeTruthy();
    expect(within(table).getByText('One')).toBeTruthy();
  });

  it('keeps the revoke dialog, its title and a busy confirm until the revoke settles', async () => {
    api.listConnectedApps.mockResolvedValue([infuse]);
    let settle: () => void = () => undefined;
    api.revokeConnectedApp.mockReturnValue(new Promise<void>((resolve) => { settle = resolve; }));
    render(<ConnectedApps user={member} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke Living room Apple TV' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(api.revokeConnectedApp).toHaveBeenCalledWith('d1'));
    const dialog = screen.getByRole('dialog', { name: 'Revoke Living room Apple TV?' });
    expect(within(dialog).getByRole('button', { name: 'Revoke' }).getAttribute('aria-busy')).toBe('true');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('dialog', { name: 'Revoke Living room Apple TV?' })).toBeTruthy();
    settle();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('closes the revoke dialog when the revoke fails and shows why', async () => {
    api.listConnectedApps.mockResolvedValue([infuse]);
    api.revokeConnectedApp.mockRejectedValue(new Error('Server said no.'));
    render(<ConnectedApps user={member} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke Living room Apple TV' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(await screen.findByText('Server said no.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Revoke Living room Apple TV' })).toBeTruthy();
  });
});
