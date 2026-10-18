import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AdminUpdateStatus } from '../../types';

const api = vi.hoisted(() => ({
  checkAdminUpdates: vi.fn(),
  getAdminUpdateStatus: vi.fn(),
  updateAdminUpdateSettings: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));

const { AdminUpdates, trustedReleaseUrl } = await import('./AdminUpdates');

const status = (patch: Partial<AdminUpdateStatus> = {}): AdminUpdateStatus => ({
  current_version: '3.1.0',
  latest_release: null,
  status: 'not_checked',
  checked_at: null,
  error: null,
  checking: false,
  automatic_checks: true,
  email_notifications: false,
  email_configured: true,
  ...patch,
});

afterEach(() => {
  vi.resetAllMocks();
  vi.useRealTimers();
});

describe('AdminUpdates', () => {
  it('trusts only canonical release pages from the Lumina GitHub repository', () => {
    expect(trustedReleaseUrl('https://github.com/shiftedx/lumina/releases/tag/v3.2.0')).toBe('https://github.com/shiftedx/lumina/releases/tag/v3.2.0');
    expect(trustedReleaseUrl('https://github.com/shiftedx/lumina/releases/tag/v3.2.0?download=1')).toBeNull();
    expect(trustedReleaseUrl('https://github.com/shiftedx/lumina/releases/tag/v03.2.0')).toBeNull();
    expect(trustedReleaseUrl('https://user@github.com/shiftedx/lumina/releases/tag/v3.2.0')).toBeNull();
    expect(trustedReleaseUrl('https://github.com/shiftedx/lumina/releases/latest')).toBeNull();
  });

  it('shows the installed version, an honest not-checked state, and why email is unavailable', async () => {
    api.getAdminUpdateStatus.mockResolvedValue(status({ email_configured: false }));
    render(<AdminUpdates />);

    expect(await screen.findByText('Lumina has not checked for updates yet.')).toBeTruthy();
    expect(screen.getByText('3.1.0')).toBeTruthy();
    expect(screen.getByText(/No update check has completed yet\./)).toBeTruthy();
    expect((screen.getByRole('switch', { name: 'Check automatically each day' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('switch', { name: 'Email vault owners about new releases' }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/Set up server email under Connections/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: /release notes/ })).toBeNull();
  });

  it('starts a check, polls until it finishes, and exposes only a trusted release link', async () => {
    const checking = status({ checking: true });
    const available = status({
      latest_release: { version: '3.2.0', url: 'https://github.com/shiftedx/lumina/releases/tag/v3.2.0', published_at: '2026-10-18T12:00:00Z' },
      status: 'update_available', checked_at: '2026-10-18T12:05:00Z',
    });
    api.getAdminUpdateStatus.mockResolvedValueOnce(status()).mockResolvedValueOnce(available);
    api.checkAdminUpdates.mockResolvedValue(checking);
    render(<AdminUpdates />);
    const button = await screen.findByRole('button', { name: 'Check for updates' });

    vi.useFakeTimers();
    fireEvent.click(button);
    await act(async () => {});
    expect(screen.getByRole('button', { name: 'Checking…' }).getAttribute('aria-busy')).toBe('true');
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });

    expect(screen.getByText('Lumina 3.2.0 is available.')).toBeTruthy();
    const release = screen.getByRole('link', { name: 'Read 3.2.0 release notes' });
    expect(release.getAttribute('href')).toBe('https://github.com/shiftedx/lumina/releases/tag/v3.2.0');
    expect(release.getAttribute('rel')).toContain('noopener');
    expect(api.getAdminUpdateStatus).toHaveBeenCalledTimes(2);
  });

  it('keeps cached release information when a manual check fails', async () => {
    const cached = status({
      latest_release: { version: '3.2.0', url: 'https://github.com/shiftedx/lumina/releases/tag/v3.2.0', published_at: null },
      status: 'update_available', checked_at: '2026-10-18T12:05:00Z', error: 'The last automatic check timed out.',
    });
    api.getAdminUpdateStatus.mockResolvedValue(cached);
    api.checkAdminUpdates.mockRejectedValue(new Error('GitHub is unavailable.'));
    render(<AdminUpdates />);

    await screen.findByText('Lumina 3.2.0 is available.');
    await userEvent.click(screen.getByRole('button', { name: 'Check for updates' }));

    expect((await screen.findByRole('alert')).textContent).toContain('GitHub is unavailable.');
    expect(screen.getByText('Lumina 3.2.0 is available.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Read 3.2.0 release notes' })).toBeTruthy();
  });

  it('qualifies a cached up-to-date result when a later check failed', async () => {
    api.getAdminUpdateStatus.mockResolvedValue(status({
      status: 'up_to_date', checked_at: '2026-10-18T12:05:00Z', error: 'The latest check timed out.',
    }));
    render(<AdminUpdates />);

    expect(await screen.findByText('The last successful check found no newer release.')).toBeTruthy();
    expect(screen.queryByText('Lumina is up to date.')).toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('The latest check timed out.');
    expect(screen.getByText(/Last checked/).textContent).toContain('2026');
  });

  it('completes a manual check when mounted under StrictMode', async () => {
    api.getAdminUpdateStatus.mockResolvedValue(status());
    api.checkAdminUpdates.mockResolvedValue(status({ status: 'up_to_date', checked_at: '2026-10-18T12:05:00Z' }));
    render(<StrictMode><AdminUpdates /></StrictMode>);

    await userEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByText('Lumina is up to date.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Check for updates' }).getAttribute('aria-busy')).toBeNull();
  });

  it('saves each notification choice and retains the server value on failure', async () => {
    api.getAdminUpdateStatus.mockResolvedValue(status());
    api.updateAdminUpdateSettings
      .mockResolvedValueOnce(status({ automatic_checks: false }))
      .mockRejectedValueOnce(new Error('Email settings could not be saved.'));
    render(<AdminUpdates />);

    const automatic = await screen.findByRole('switch', { name: 'Check automatically each day' });
    await userEvent.click(automatic);
    await waitFor(() => expect(api.updateAdminUpdateSettings).toHaveBeenCalledWith({ automatic_checks: false }));
    await waitFor(() => expect((automatic as HTMLInputElement).checked).toBe(false));

    const email = screen.getByRole('switch', { name: 'Email vault owners about new releases' });
    await userEvent.click(email);
    expect((await screen.findByRole('alert')).textContent).toContain('Email settings could not be saved.');
    expect((email as HTMLInputElement).checked).toBe(false);
  });

  it('does not render an untrusted release URL and cancels a pending poll on unmount', async () => {
    api.getAdminUpdateStatus.mockResolvedValue(status({
      checking: true,
      latest_release: { version: '3.2.0', url: 'javascript:alert(1)', published_at: null },
      status: 'update_available',
    }));
    vi.useFakeTimers();
    const view = render(<AdminUpdates />);
    await act(async () => {});
    expect(screen.queryByRole('link', { name: /release notes/ })).toBeNull();
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(api.getAdminUpdateStatus).toHaveBeenCalledTimes(1);
  });

  it('offers a status refresh after bounded polling without starting another check', async () => {
    api.getAdminUpdateStatus.mockResolvedValue(status({ checking: true }));
    vi.useFakeTimers();
    render(<AdminUpdates />);
    await act(async () => {});

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByText('The update check is still running.')).toBeTruthy();
    const refresh = screen.getByRole('button', { name: 'Refresh status' });
    fireEvent.click(refresh);
    await act(async () => {});

    expect(api.checkAdminUpdates).not.toHaveBeenCalled();
    expect(api.getAdminUpdateStatus).toHaveBeenCalledTimes(32);
  });
});
