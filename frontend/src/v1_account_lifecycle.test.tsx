import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const changePassword = vi.fn();
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), changePassword }));

const { AccountPassword } = await import('./AccountPassword');
const { AuthScreen } = await import('./features/auth/AuthScreen');

afterEach(() => { changePassword.mockReset(); });

describe('account lifecycle', () => {
  it('changes the password only with the current password and clears the fields', async () => {
    const browser = userEvent.setup();
    changePassword.mockRejectedValueOnce(new Error('Current password is incorrect.')).mockResolvedValueOnce({});
    render(<AccountPassword />);

    await browser.type(screen.getByLabelText('Current password'), 'wrong');
    await browser.type(screen.getByLabelText('New password'), 'Strong pass 42!');
    await browser.click(screen.getByRole('button', { name: 'Change password' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Current password is incorrect.');
    expect((screen.getByLabelText('New password') as HTMLInputElement).value).toBe('Strong pass 42!');

    await browser.click(screen.getByRole('button', { name: 'Change password' }));
    expect(changePassword).toHaveBeenLastCalledWith('wrong', 'Strong pass 42!');
    expect((await screen.findByRole('status')).textContent).toContain('other devices were signed out');
    expect((screen.getByLabelText('Current password') as HTMLInputElement).value).toBe('');
  });

  it('asks only for a new password on a reset link', async () => {
    const browser = userEvent.setup();
    const onSetup = vi.fn();
    render(<AuthScreen busy={false} error={null} onLogin={vi.fn()} onRetrySession={vi.fn()} onSetup={onSetup} onSwitched={vi.fn()} sessionProblem={null} stage="reset" />);
    expect(screen.queryByRole('textbox')).toBeNull();
    await browser.type(screen.getByLabelText('New password'), 'Strong pass 42!');
    await browser.click(screen.getByRole('button', { name: 'Set new password' }));
    expect(onSetup).toHaveBeenCalledWith('', '', 'Strong pass 42!');
  });
});
