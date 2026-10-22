import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

import { ApiRequestError } from './api';
import LuminaApp from './LuminaApp';
import { resetDeviceRing } from './features/auth/deviceRing';
import { ToastProvider } from './ui';
import { createInitialWorkspaceState } from './workspace';

const mocks = vi.hoisted(() => ({
  getDeviceMembers: vi.fn(),
  getShowcase: vi.fn(),
  loginSession: vi.fn(),
  useAuthenticatedWorkspace: vi.fn(),
}));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  getDeviceMembers: mocks.getDeviceMembers,
  getShowcase: mocks.getShowcase,
  loginSession: mocks.loginSession,
}));

vi.mock('./workspace', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./workspace')>()),
  useAuthenticatedWorkspace: mocks.useAuthenticatedWorkspace,
}));

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
  resetDeviceRing();
  mocks.getDeviceMembers.mockResolvedValue([]);
  mocks.getShowcase.mockResolvedValue({ slides: [] });
  mocks.loginSession.mockRejectedValue(new ApiRequestError('rate_limited', 429));

  const state = createInitialWorkspaceState();
  state.authStage = 'login';
  mocks.useAuthenticatedWorkspace.mockReturnValue({
    state,
    dispatch: vi.fn(),
    loadAuthenticated: vi.fn(),
    loadPublic: vi.fn(),
    retryCollection: vi.fn(),
    refreshLibraryCollection: vi.fn(),
    loadMoreLibrary: vi.fn(),
    loadMoreJobs: vi.fn(),
    captureSessionToken: vi.fn(() => 1),
    isSessionTokenCurrent: vi.fn(() => true),
    reset: vi.fn(),
    holdEvents: vi.fn(),
  });
});

it('tells a rate-limited member to wait a minute', async () => {
  const member = userEvent.setup();
  render(<LuminaApp />, { wrapper: ToastProvider });

  await member.type(await screen.findByLabelText('Username'), 'sam');
  await member.type(screen.getByLabelText('Password'), 'synthetic-passphrase');
  await member.click(screen.getByRole('button', { name: 'Sign in' }));

  expect(await screen.findByText('Too many attempts. Try again in a minute.')).toBeTruthy();
  expect(mocks.loginSession).toHaveBeenCalledWith({ username: 'sam', password: 'synthetic-passphrase', remember_on_device: false });
});
