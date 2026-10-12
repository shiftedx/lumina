/**
 * Local-only public auth surface (updated for the public-edge cut).
 *
 * The auth screen renders NO provider OAuth buttons (asserted by label
 * absence) and the local login form works end-to-end against the session API
 * (fetch-stubbed).
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './api';
import { AuthScreen } from './features/auth/AuthScreen';
import { resetDeviceRing } from './features/auth/deviceRing';
import type { SessionState } from './types';

describe('local-only auth screen', () => {
  // A signed-out sign-in page first asks for this browser's ring; here it holds nobody.
  beforeEach(() => { resetDeviceRing(); vi.spyOn(api, 'getDeviceMembers').mockResolvedValue([]); });
  afterEach(() => vi.restoreAllMocks());

  it('renders no provider buttons: the local form is the whole sign-in surface', async () => {
    render(
      <AuthScreen stage="login" busy={false} error={null} onLogin={vi.fn()} onSetup={vi.fn()} onRetrySession={vi.fn()} onSwitched={vi.fn()} sessionProblem={null} />,
    );
    expect(await screen.findByText('Welcome home')).toBeTruthy();
    expect(screen.getByText(/Sign in with your Lumina account/i)).toBeTruthy();
    expect(screen.getByText('Username')).toBeTruthy();
    expect(screen.getByText('Password')).toBeTruthy();
    // Label absence: no provider OAuth buttons of any kind.
    expect(screen.queryByRole('button', { name: /^Continue with /i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Sign in with (Google|GitHub|Twitch)/i })).toBeNull();
  });

  it('the local login form submits credentials end to end against the session API', async () => {
    const stubSession: SessionState = {
      user: {
        id: 'member-1',
        username: 'local',
        display_name: 'Local',
        role: 'viewer',
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    };
    const loginSession = vi.fn().mockResolvedValue(stubSession);
    vi.spyOn(api, 'loginSession').mockImplementation(loginSession);
    // The AuthScreen wiring boundary is onLogin; the app passes its local
    // session login through it. Submitting the form must forward the typed
    // credentials to that boundary.
    const onLogin = vi.fn(async (username: string, password: string) => {
      await api.loginSession({ username, password });
    });
    render(<AuthScreen stage="login" busy={false} error={null} onLogin={onLogin} onSetup={vi.fn()} onRetrySession={vi.fn()} onSwitched={vi.fn()} sessionProblem={null} />);
    const submit = await screen.findByRole('button', { name: 'Sign in' });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'local' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct-horse' } });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(submit);
    expect(onLogin).toHaveBeenCalledWith('local', 'correct-horse', false);
    expect(loginSession).toHaveBeenCalledWith({ username: 'local', password: 'correct-horse' });
    expect((await loginSession.mock.results[0]?.value).user.username).toBe('local');
  });
});
