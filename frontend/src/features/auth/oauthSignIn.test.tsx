import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { AuthScreen } from './AuthScreen';
import { resetDeviceRing } from './deviceRing';

beforeEach(() => {
  resetDeviceRing();
  vi.spyOn(api, 'getDeviceMembers').mockResolvedValue([]);
  vi.spyOn(api, 'getOAuthProviders').mockResolvedValue([]);
});
afterEach(() => vi.restoreAllMocks());

const authProps = (onLogin = vi.fn()) => ({
  busy: false,
  error: null,
  onLogin,
  onRetrySession: vi.fn(),
  onSetup: vi.fn(),
  onSwitched: vi.fn(),
  sessionProblem: null,
  stage: 'login' as const,
});

describe('OAuth web sign-in', () => {
  it('discovers providers, preserves remember-on-device, and starts the provider redirect', async () => {
    vi.mocked(api.getOAuthProviders).mockResolvedValue([{ id: 'google', name: 'Google' }, { id: 'github', name: 'GitHub' }]);
    const start = vi.spyOn(api, 'startOAuth').mockReturnValue(new Promise(() => undefined));
    render(<AuthScreen {...authProps()} />);

    expect(await screen.findByRole('button', { name: 'Continue with Google' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Continue with GitHub' })).toBeTruthy();
    await userEvent.click(screen.getByRole('checkbox', { name: "Show me in Who's watching on this device" }));
    await userEvent.click(screen.getByRole('button', { name: 'Continue with Google' }));

    await waitFor(() => expect(start).toHaveBeenCalledWith('google', true));
  });

  it('keeps the password form usable when there are no providers', async () => {
    const onLogin = vi.fn();
    render(<AuthScreen {...authProps(onLogin)} />);

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Continue with / })).toBeNull();
    await userEvent.type(screen.getByLabelText('Username'), 'local');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(onLogin).toHaveBeenCalledWith('local', 'correct-horse', false);
  });

  it('keeps the password form usable when provider discovery fails', async () => {
    vi.mocked(api.getOAuthProviders).mockRejectedValue(new Error('provider service unavailable'));
    const onLogin = vi.fn();
    render(<AuthScreen {...authProps(onLogin)} />);

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeTruthy();
    await userEvent.type(screen.getByLabelText('Username'), 'local');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(onLogin).toHaveBeenCalledWith('local', 'correct-horse', false);
    expect(screen.queryByRole('button', { name: /^Continue with / })).toBeNull();
  });

  it('shows a generic retryable message when a provider start fails', async () => {
    vi.mocked(api.getOAuthProviders).mockResolvedValue([{ id: 'google', name: 'Google' }]);
    vi.spyOn(api, 'startOAuth').mockRejectedValue(new Error('provider secret leaked by backend'));
    render(<AuthScreen {...authProps()} />);

    await userEvent.click(await screen.findByRole('button', { name: 'Continue with Google' }));
    expect((await screen.findByRole('alert')).textContent).toContain("We couldn't start sign-in with that provider.");
    expect(screen.queryByText('provider secret leaked by backend')).toBeNull();
  });
  it('keeps the OAuth second factor visible ahead of the remembered member picker', async () => {
    vi.spyOn(api, 'getDeviceMembers').mockResolvedValue([{ user_id: 'remembered', display_name: 'Remembered', username: 'remembered', role: 'viewer', switch: 'instant', active: false }]);
    render(<AuthScreen {...authProps()} twoFactor={{ challenge: 'synthetic-challenge', onVerified: vi.fn(), onBack: vi.fn() }} />);
    expect(await screen.findByRole('heading', { name: 'Two-step verification' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Continue as Remembered' })).toBeNull();
  });

});
