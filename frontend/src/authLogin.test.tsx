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
  getOAuthProviders: vi.fn(),
  getShowcase: vi.fn(),
  loginSession: vi.fn(),
  useAuthenticatedWorkspace: vi.fn(),
}));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  getDeviceMembers: mocks.getDeviceMembers,
  getOAuthProviders: mocks.getOAuthProviders,
  getShowcase: mocks.getShowcase,
  loginSession: mocks.loginSession,
}));

vi.mock('./workspace', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./workspace')>()),
  useAuthenticatedWorkspace: mocks.useAuthenticatedWorkspace,
}));

beforeEach(() => {
  window.history.replaceState(null, '', '/');
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
  mocks.getOAuthProviders.mockResolvedValue([]);
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

it('consumes an OAuth error fragment and shows only a generic safe message', async () => {
  window.history.replaceState(null, '', '/#oauth-error');
  render(<LuminaApp />, { wrapper: ToastProvider });

  expect(await screen.findByText("We couldn't complete sign-in with that provider. Try again or use your Lumina account.")).toBeTruthy();
  await vi.waitFor(() => expect(window.location.hash).toBe(''));
});

it('consumes an OAuth two-factor fragment without exposing the challenge and uses the existing step', async () => {
  const challenge = 'opaque-challenge-value';
  window.history.replaceState(null, '', `/#oauth-two-factor=${encodeURIComponent(challenge)}`);
  const replaceState = vi.spyOn(window.history, 'replaceState');
  render(<LuminaApp />, { wrapper: ToastProvider });

  expect(await screen.findByRole('heading', { name: 'Two-step verification' })).toBeTruthy();
  await vi.waitFor(() => expect(window.location.hash).toBe(''));
  expect(document.body.textContent).not.toContain(challenge);
  expect(replaceState.mock.calls.at(-1)?.[2]).not.toContain(challenge);
});

it('shows an OAuth second factor even when another member still has a live session', async () => {
  const workspace = mocks.useAuthenticatedWorkspace.getMockImplementation()?.({ onError: vi.fn() });
  if (!workspace) throw new Error('Missing workspace fixture');
  workspace.state.authStage = 'ready';
  workspace.state.currentUser = { id: 'prior-member', username: 'prior', display_name: 'Prior', role: 'viewer' };
  window.history.replaceState(null, '', '/#oauth-two-factor=opaque-challenge');
  render(<LuminaApp />, { wrapper: ToastProvider });
  expect(await screen.findByRole('heading', { name: 'Two-step verification' })).toBeTruthy();
});
