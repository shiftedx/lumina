import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AuthScreen } from './features/auth/AuthScreen';

describe('household invitations', () => {
  it('lets the invitee choose a local account without selecting a role', async () => {
    const browser = userEvent.setup();
    const onSetup = vi.fn();
    render(<AuthScreen busy={false} error={null} onCancel={vi.fn()} onLogin={vi.fn()} onRetrySession={vi.fn()} onSetup={onSetup} onSwitched={vi.fn()} sessionProblem={null} stage="invite" />);

    expect(screen.getByRole('heading', { name: 'Join this household' })).not.toBeNull();
    expect(screen.queryByRole('combobox')).toBeNull();
    await browser.type(screen.getByRole('textbox', { name: 'Username' }), 'alex');
    await browser.type(screen.getByRole('textbox', { name: 'Display name' }), 'Alex');
    await browser.type(screen.getByLabelText('Password'), 'Strong pass 42!');
    await browser.click(screen.getByRole('button', { name: 'Join household' }));
    expect(onSetup).toHaveBeenCalledWith('alex', 'Alex', 'Strong pass 42!');
  });
});
