import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../api';
import { AuthScreen, type AuthScreenProps } from './AuthScreen';
import { resetDeviceRing } from './deviceRing';
import { TwoFactorStep } from './TwoFactorStep';

beforeEach(() => { resetDeviceRing(); vi.spyOn(api, 'getDeviceMembers').mockResolvedValue([]); });
afterEach(() => vi.restoreAllMocks());

describe('TwoFactorStep', () => {
  it('sends the code with the trust choice and continues once verified', async () => {
    const verify = vi.spyOn(api, 'verifyTwoFactor').mockResolvedValue({ user: { id: 'u1' } } as never);
    const onVerified = vi.fn();
    render(<TwoFactorStep challenge="c1" onBack={vi.fn()} onVerified={onVerified} />);
    expect(screen.getByRole('heading', { name: 'Two-step verification' })).toBeTruthy();
    const input = screen.getByLabelText('Code');
    expect(input.getAttribute('autocomplete')).toBe('one-time-code');
    expect(input.getAttribute('inputmode')).toBe('numeric');
    await userEvent.click(screen.getByRole('checkbox', { name: "Don't ask again on this device for 30 days" }));
    await userEvent.type(input, '123456');
    await waitFor(() => expect(verify).toHaveBeenCalledWith({ challenge: 'c1', code: '123456', trust_device: true }));
    await waitFor(() => expect(onVerified).toHaveBeenCalledOnce());
  });

  it('announces a wrong code, clears and refocuses the input', async () => {
    vi.spyOn(api, 'verifyTwoFactor').mockRejectedValue(new api.ApiRequestError('That code did not match.', 401));
    render(<TwoFactorStep challenge="c1" onBack={vi.fn()} onVerified={vi.fn()} />);
    await userEvent.type(screen.getByLabelText('Code'), '000000');
    expect((await screen.findByRole('alert')).textContent).toContain('That code did not match.');
    const input = screen.getByLabelText('Code') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(document.activeElement).toBe(input);
  });

  it('takes a recovery code through the same call', async () => {
    const verify = vi.spyOn(api, 'verifyTwoFactor').mockResolvedValue({ user: { id: 'u1' } } as never);
    render(<TwoFactorStep challenge="c1" onBack={vi.fn()} onVerified={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Use a recovery code instead' }));
    const input = screen.getByLabelText('Recovery code');
    expect(input.getAttribute('autocomplete')).toBe('off');
    await userEvent.type(input, 'abcd-efgh-jkmn-pqrs');
    await userEvent.click(screen.getByRole('button', { name: 'Verify' }));
    await waitFor(() => expect(verify).toHaveBeenCalledWith({ challenge: 'c1', recovery_code: 'abcd-efgh-jkmn-pqrs', trust_device: false }));
  });

  it('goes back to the password with a message when the challenge expired', async () => {
    vi.spyOn(api, 'verifyTwoFactor').mockRejectedValue(new api.ApiRequestError('two_factor_challenge_expired', 410));
    const onBack = vi.fn();
    render(<TwoFactorStep challenge="c1" onBack={onBack} onVerified={vi.fn()} />);
    await userEvent.type(screen.getByLabelText('Code'), '123456');
    await waitFor(() => expect(onBack).toHaveBeenCalledWith('Your sign-in timed out. Enter your password again.'));
  });

  it('shows the second step in the sign-in screen and Back returns to the password form', async () => {
    const onBack = vi.fn();
    const props: AuthScreenProps = { stage: 'login', busy: false, error: null, onLogin: vi.fn(), onSetup: vi.fn(), onRetrySession: vi.fn(), onSwitched: vi.fn(), sessionProblem: null, twoFactor: { challenge: 'c1', onVerified: vi.fn(), onBack } };
    render(<AuthScreen {...props} />);
    expect(await screen.findByRole('heading', { name: 'Two-step verification' })).toBeTruthy();
    expect(screen.queryByLabelText('Username')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(onBack).toHaveBeenCalledWith();
  });
});
