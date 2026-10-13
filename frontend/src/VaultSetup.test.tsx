import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getAdminMediaSetup: vi.fn(),
  listImports: vi.fn(),
  listStorageRoots: vi.fn(),
  createStorageRoot: vi.fn(),
  probeStorageRoot: vi.fn(),
  startImport: vi.fn(),
}));
vi.mock('./api', () => api);

const { VaultSetup } = await import('./features/onboarding/VaultSetup');

const root = (id: string, label: string, state = 'available', mode: 'managed' | 'external' = 'external') => ({
  id,
  label,
  path: `/media/${id}`,
  mode,
  enabled: true,
  minimum_free_bytes: 0,
  artifact_count: 0,
  observation: { state },
});

beforeEach(() => {
  api.getAdminMediaSetup.mockResolvedValue({ mount_parents: ['/media', '/mnt/library'] });
  api.listStorageRoots.mockResolvedValue([]);
  api.listImports.mockResolvedValue([]);
  api.startImport.mockResolvedValue({ id: 'run-1' });
});

afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

function setup(onContinue = vi.fn().mockResolvedValue(undefined)) {
  const view = render(<VaultSetup onContinue={onContinue} />);
  return { browser: userEvent.setup(), onContinue, ...view };
}

async function fillFolder(browser: ReturnType<typeof userEvent.setup>, label = 'Family films', path = '/media/films') {
  await browser.type(await screen.findByLabelText(/^Folder name/), label);
  await browser.type(screen.getByLabelText(/^Mounted path/), path);
}

describe('admin vault setup', () => {
  it('creates an external root with no reserve and starts a household scan', async () => {
    const created = root('films', 'Family films');
    api.createStorageRoot.mockResolvedValue(created);
    const { browser } = setup();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1, name: 'Give your vault a home.' })));
    await fillFolder(browser);

    const household = screen.getByRole('radio', { name: 'Household' }) as HTMLInputElement;
    expect(household.type).toBe('radio');
    expect(household.checked).toBe(true);
    await browser.click(screen.getByRole('button', { name: 'Add and scan' }));

    await waitFor(() => expect(api.createStorageRoot).toHaveBeenCalledWith({
      label: 'Family films', container_path: '/media/films', mode: 'external', minimum_free_bytes: 0,
    }));
    await waitFor(() => expect(api.startImport).toHaveBeenCalledWith({ root_id: 'films', visibility: 'shared' }));
    expect(await screen.findByText('Scan underway. New media will appear in your Library.')).toBeTruthy();
  });

  it('lets an admin make a newly added folder private', async () => {
    api.createStorageRoot.mockResolvedValue(root('archive', 'Private archive'));
    const { browser } = setup();
    await fillFolder(browser, 'Private archive', '/media/archive');
    await browser.click(screen.getByRole('radio', { name: 'Private' }));
    await browser.click(screen.getByRole('button', { name: 'Add and scan' }));

    await waitFor(() => expect(api.startImport).toHaveBeenCalledWith({ root_id: 'archive', visibility: 'private' }));
  });

  it('retries a failed scan without creating the root twice', async () => {
    api.createStorageRoot.mockResolvedValue(root('films', 'Family films'));
    api.startImport.mockRejectedValueOnce(new Error('Scanner is warming up.')).mockResolvedValueOnce({ id: 'run-2' });
    const { browser } = setup();
    await fillFolder(browser);
    await browser.click(screen.getByRole('button', { name: 'Add and scan' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Scanner is warming up.');
    await browser.click(screen.getByRole('button', { name: 'Retry scan' }));
    await waitFor(() => expect(api.startImport).toHaveBeenCalledTimes(2));
    expect(api.createStorageRoot).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('Scan underway. New media will appear in your Library.')).toBeTruthy();
  });

  it('keeps the skip action available after a load failure and can retry loading', async () => {
    api.listStorageRoots.mockRejectedValueOnce(new Error('Storage service unavailable.')).mockResolvedValueOnce([]);
    const { browser, onContinue } = setup();

    expect(await screen.findByText('Storage service unavailable.')).toBeTruthy();
    const skip = screen.getByRole('button', { name: 'Set up media later' }) as HTMLButtonElement;
    expect(skip.disabled).toBe(false);
    await browser.click(skip);
    expect(onContinue).toHaveBeenCalledTimes(1);
    await browser.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(api.listStorageRoots).toHaveBeenCalledTimes(2));
    expect(await screen.findByLabelText(/^Folder name/)).toBeTruthy();
  });

  it('probes an offline folder before scanning and only scans the available result', async () => {
    const offline = root('nas', 'Family NAS', 'offline');
    api.listStorageRoots.mockResolvedValue([offline]);
    api.probeStorageRoot.mockResolvedValue(root('nas', 'Family NAS', 'available'));
    const { browser } = setup();

    const folder = within(await screen.findByRole('list', { name: 'Media folders' })).getByRole('listitem');
    expect(within(folder).getByText('Offline')).toBeTruthy();
    await browser.click(within(folder).getByRole('button', { name: 'Check and scan' }));
    await waitFor(() => expect(api.probeStorageRoot).toHaveBeenCalledWith('nas'));
    await waitFor(() => expect(api.startImport).toHaveBeenCalledWith({ root_id: 'nas', visibility: 'shared' }));
    expect(await within(folder).findByText('Available')).toBeTruthy();
  });

  it('keeps an unavailable probe actionable without starting an import', async () => {
    api.listStorageRoots.mockResolvedValue([root('nas', 'Family NAS', 'offline')]);
    api.probeStorageRoot.mockResolvedValue(root('nas', 'Family NAS', 'offline'));
    const { browser } = setup();

    await browser.click(await screen.findByRole('button', { name: 'Check and scan' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/Offline.*Mount this folder/);
    expect(api.startImport).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Retry scan' })).toBeTruthy();
  });

  it('shows every external share and hides managed download storage', async () => {
    api.listStorageRoots.mockResolvedValue([
      root('films', 'Films'),
      root('music', 'Music'),
      root('downloads', 'Download cache', 'available', 'managed'),
    ]);
    setup();

    const list = await screen.findByRole('list', { name: 'Media folders' });
    expect(within(list).getByText('Films')).toBeTruthy();
    expect(within(list).getByText('Music')).toBeTruthy();
    expect(within(list).queryByText('Download cache')).toBeNull();
    expect(within(list).getAllByRole('button', { name: 'Start scan' })).toHaveLength(2);
  });

  it('enables Continue after a media folder exists', async () => {
    api.listStorageRoots.mockResolvedValue([root('films', 'Films')]);
    const { browser, onContinue } = setup();

    const continueButton = await screen.findByRole('button', { name: 'Continue' }) as HTMLButtonElement;
    expect(continueButton.disabled).toBe(false);
    await browser.click(continueButton);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it('keeps a disabled external folder paused until it is enabled in Admin settings', async () => {
    api.listStorageRoots.mockResolvedValue([{ ...root('archive', 'Archive'), enabled: false }]);
    setup();

    const list = await screen.findByRole('list', { name: 'Media folders' });
    expect(within(list).getByText('Paused')).toBeTruthy();
    expect(within(list).getByText('Enable this folder in Admin settings before scanning.')).toBeTruthy();
    expect((within(list).getByRole('button', { name: 'Scan unavailable' }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.probeStorageRoot).not.toHaveBeenCalled();
    expect(api.startImport).not.toHaveBeenCalled();
  });

  it('restores the latest durable scan state without offering a duplicate scan', async () => {
    api.listStorageRoots.mockResolvedValue([root('films', 'Films'), root('music', 'Music')]);
    api.listImports.mockResolvedValue([
      { id: 'run-new', root_id: 'films', state: 'running', visibility: 'private', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-12T12:00:00Z', finished_at: null },
      { id: 'run-old', root_id: 'films', state: 'succeeded', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-11T12:00:00Z', finished_at: '2026-10-11T12:02:00Z' },
      { id: 'run-music', root_id: 'music', state: 'succeeded', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-10T12:00:00Z', finished_at: '2026-10-10T12:02:00Z' },
    ]);
    setup();

    const list = await screen.findByRole('list', { name: 'Media folders' });
    const films = within(list).getByText('Films').closest('li') as HTMLElement;
    const music = within(list).getByText('Music').closest('li') as HTMLElement;
    expect((within(films).getByRole('button', { name: 'Scan underway' }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(films).getByRole('radio', { name: 'Private' }) as HTMLInputElement).checked).toBe(true);
    expect((within(music).getByRole('button', { name: 'Scan complete' }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.startImport).not.toHaveBeenCalled();
  });

  it.each([
    ['partial', 'Some files need attention in Imports after you enter your vault.'],
    ['needs_confirmation', 'This scan needs review in Imports after you enter your vault.'],
  ])('restores %s scans as attention states without claiming completion', async (state, message) => {
    api.listStorageRoots.mockResolvedValue([root('films', 'Films')]);
    api.listImports.mockResolvedValue([
      { id: `run-${state}`, root_id: 'films', state, visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-12T12:00:00Z', finished_at: null },
    ]);
    setup();

    const list = await screen.findByRole('list', { name: 'Media folders' });
    expect(within(list).getByText(message)).toBeTruthy();
    expect((within(list).getByRole('button', { name: 'Review in Imports' }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(list).queryByRole('button', { name: 'Scan complete' })).toBeNull();
    expect(api.startImport).not.toHaveBeenCalled();
  });

  it('recovers a root created despite a lost response before scanning it', async () => {
    const recovered = root('films', 'Family films');
    api.listStorageRoots.mockResolvedValueOnce([]).mockResolvedValueOnce([recovered]);
    api.createStorageRoot.mockRejectedValue(new Error('Connection closed.'));
    const { browser } = setup();
    await fillFolder(browser);
    await browser.click(screen.getByRole('button', { name: 'Add and scan' }));

    await waitFor(() => expect(api.listStorageRoots).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.startImport).toHaveBeenCalledWith({ root_id: 'films', visibility: 'shared' }));
    expect(api.createStorageRoot).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Connection closed.')).toBeNull();
  });

  it('recovers an import committed despite a lost start response', async () => {
    api.listStorageRoots.mockResolvedValue([root('films', 'Films')]);
    api.listImports
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: 'run-committed', root_id: 'films', state: 'running', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-12T12:00:00Z', finished_at: null }]);
    api.startImport.mockRejectedValue(new Error('Connection closed.'));
    const { browser } = setup();
    await browser.click(await screen.findByRole('button', { name: 'Start scan' }));

    await waitFor(() => expect(api.listImports).toHaveBeenCalledTimes(2));
    expect((await screen.findByRole('button', { name: 'Scan underway' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Retry scan' })).toBeNull();
  });

  it('polls only active scans, stops at terminal state, and preserves another root error', async () => {
    vi.useFakeTimers();
    api.listStorageRoots.mockResolvedValue([root('films', 'Films'), root('music', 'Music')]);
    api.listImports
      .mockResolvedValueOnce([
        { id: 'run-films', root_id: 'films', state: 'running', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-12T12:00:00Z', finished_at: null },
        { id: 'run-music', root_id: 'music', state: 'failed', visibility: 'private', counters: {}, coverage: 'complete', error: 'Drive disconnected.', created_at: '2026-10-12T11:00:00Z', finished_at: '2026-10-12T11:01:00Z' },
      ])
      .mockResolvedValueOnce([
        { id: 'run-films', root_id: 'films', state: 'succeeded', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-12T12:00:00Z', finished_at: '2026-10-12T12:02:00Z' },
      ]);
    setup();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Scan underway' })).toBeTruthy();
    expect(screen.getByText('Drive disconnected.')).toBeTruthy();

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByRole('button', { name: 'Scan complete' })).toBeTruthy();
    expect(screen.getByText('Drive disconnected.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry scan' })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(api.listImports).toHaveBeenCalledTimes(2);
  });

  it('keeps polling an active scan after a transient refresh failure', async () => {
    vi.useFakeTimers();
    api.listStorageRoots.mockResolvedValue([root('films', 'Films')]);
    api.listImports
      .mockResolvedValueOnce([{ id: 'run-films', root_id: 'films', state: 'running', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-12T12:00:00Z', finished_at: null }])
      .mockRejectedValueOnce(new Error('Temporary network failure.'))
      .mockResolvedValueOnce([{ id: 'run-films', root_id: 'films', state: 'failed', visibility: 'shared', counters: {}, coverage: 'complete', error: 'Drive disconnected.', created_at: '2026-10-12T12:00:00Z', finished_at: '2026-10-12T12:02:00Z' }]);
    setup();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByRole('button', { name: 'Scan underway' })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText('Drive disconnected.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry scan' })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(api.listImports).toHaveBeenCalledTimes(3);
  });

  it('fences an in-flight active-scan poll after cleanup', async () => {
    vi.useFakeTimers();
    let resolvePoll!: (runs: unknown[]) => void;
    api.listStorageRoots.mockResolvedValue([root('films', 'Films')]);
    api.listImports
      .mockResolvedValueOnce([{ id: 'run-films', root_id: 'films', state: 'running', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '2026-10-12T12:00:00Z', finished_at: null }])
      .mockReturnValueOnce(new Promise((resolve) => { resolvePoll = resolve; }));
    const { unmount } = setup();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.listImports).toHaveBeenCalledTimes(2);

    unmount();
    resolvePoll([{ id: 'run-films', root_id: 'films', state: 'succeeded' }]);
    await act(async () => { await Promise.resolve(); await vi.advanceTimersByTimeAsync(10_000); });
    expect(api.listImports).toHaveBeenCalledTimes(2);
  });

  it('disables add and completion controls while an async add is running', async () => {
    let resolveCreate!: (value: ReturnType<typeof root>) => void;
    api.createStorageRoot.mockReturnValue(new Promise((resolve) => { resolveCreate = resolve; }));
    const { browser } = setup();
    await fillFolder(browser);
    await browser.click(screen.getByRole('button', { name: 'Add and scan' }));

    expect((screen.getByRole('button', { name: 'Add and scan' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Set up media later' }) as HTMLButtonElement).disabled).toBe(true);
    resolveCreate(root('films', 'Family films'));
    await waitFor(() => expect(api.startImport).toHaveBeenCalled());
  });

  it('explains the server prerequisite when no mount parent is configured', async () => {
    api.getAdminMediaSetup.mockResolvedValue({ mount_parents: [] });
    setup();

    expect(await screen.findByText('Server setup needed.')).toBeTruthy();
    expect(screen.getByText(/LUMINA_STORAGE_MOUNT_PARENTS: \/media/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Add and scan' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Set up media later' }) as HTMLButtonElement).disabled).toBe(false);
  });
});
