import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  discoverHouseholdTransfer: vi.fn(), getAdminMediaSetup: vi.fn(), getMediaServerSettings: vi.fn(), listImports: vi.fn(), listStorageRoots: vi.fn(),
}));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const { FirstOwnerSetup } = await import('./features/onboarding/FirstOwnerSetup');

beforeEach(() => {
  window.scrollTo = vi.fn();
  api.getAdminMediaSetup.mockResolvedValue({ mount_parents: ['/media'] });
  api.getMediaServerSettings.mockResolvedValue({ jellyfin_import_url: null, plex_import_url: null });
  api.listStorageRoots.mockResolvedValue([]);
  api.listImports.mockResolvedValue([]);
});
afterEach(() => vi.resetAllMocks());

it('keeps media setup mounted when visiting the optional transfer and returning', async () => {
  const browser = userEvent.setup();
  const onContinue = vi.fn().mockResolvedValue(undefined);
  render(<FirstOwnerSetup onContinue={onContinue} />);
  await browser.type(await screen.findByLabelText(/^Folder name/), 'Family films');
  await browser.click(screen.getByRole('button', { name: 'Set up media later' }));
  expect(await screen.findByRole('heading', { name: 'Bring your household along.' })).toBeTruthy();
  expect(onContinue).not.toHaveBeenCalled();
  await browser.click(screen.getByRole('button', { name: 'Back to media' }));
  expect(await screen.findByRole('heading', { name: 'Give your vault a home.' })).toBeTruthy();
  expect((screen.getByLabelText(/^Folder name/) as HTMLInputElement).value).toBe('Family films');
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Give your vault a home.' })));
  expect(api.getAdminMediaSetup).toHaveBeenCalledTimes(1);
});
