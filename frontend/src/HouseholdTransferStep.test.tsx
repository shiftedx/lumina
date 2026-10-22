import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { HouseholdTransferEvent, HouseholdTransferMember } from './types';

const api = vi.hoisted(() => ({
  discoverHouseholdTransfer: vi.fn(),
  getMediaServerSettings: vi.fn(),
  listImports: vi.fn(),
  listStorageRoots: vi.fn(),
  updateMediaServerSettings: vi.fn(),
}));
const transfer = vi.hoisted(() => ({ streamHouseholdTransfer: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
vi.mock('./features/onboarding/onboardingTransferApi', () => transfer);

const { HouseholdTransferStep } = await import('./features/onboarding/HouseholdTransferStep');

const mediaSettings = { jellyfin_import_url: null, plex_import_url: 'http://plex.local:32400' };
const root = { id: 'films', label: 'Films', path: '/media/films', mode: 'external', enabled: true, minimum_free_bytes: 0, artifact_count: 12, observation: { state: 'available' } };
const member = (source_id: string, source_name: string, action: 'import' | 'create' | 'skip', extra = {}): HouseholdTransferMember => ({
  source_id, source_name, action, disabled: false, lumina_username: source_name.toLowerCase(), reason: null, error: null, summary: null, reset_url: null, reset_expires_at: null, ...extra,
});

beforeEach(() => {
  api.getMediaServerSettings.mockResolvedValue(mediaSettings);
  api.listStorageRoots.mockResolvedValue([root]);
  api.listImports.mockResolvedValue([]);
  api.updateMediaServerSettings.mockResolvedValue(mediaSettings);
  api.discoverHouseholdTransfer.mockResolvedValue({ members: [] });
});

afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

describe('first-owner household transfer', () => {
  it('waits for Find people, selects available members, streams results, and keeps a reset link after a later failure', async () => {
    const members = [
      member('owner', 'Owner', 'import'),
      member('sam', 'Sam', 'create'),
      member('pat', 'Pat', 'create', { error: 'History is unavailable.' }),
      member('lee', 'Lee', 'skip', { disabled: true, reason: 'History is unavailable.' }),
    ];
    api.discoverHouseholdTransfer.mockResolvedValue({ members });
    let emit!: (event: HouseholdTransferEvent) => void;
    let reject!: (failure: Error) => void;
    transfer.streamHouseholdTransfer.mockImplementation((_provider, _payload, onEvent) => new Promise<void>((_resolve, rejectPromise) => {
      emit = onEvent;
      reject = rejectPromise;
    }));
    const browser = userEvent.setup();
    render(<HouseholdTransferStep onBack={vi.fn()} onFinish={vi.fn().mockResolvedValue(undefined)} />);

    await screen.findByRole('heading', { name: 'Where are you moving from?' });
    expect(api.discoverHouseholdTransfer).not.toHaveBeenCalled();
    await browser.click(screen.getByRole('radio', { name: /Plex/ }));
    await browser.click(screen.getByRole('button', { name: 'Continue' }));
    await browser.clear(screen.getByLabelText(/^Plex server address/));
    await browser.type(screen.getByLabelText(/^Plex server address/), 'http://new-plex.local:32400');
    await browser.type(screen.getByLabelText(/^Plex server owner token/), ' owner-token ');
    await browser.click(screen.getByRole('button', { name: 'Find people' }));

    await screen.findByRole('heading', { name: 'Choose people to bring over' });
    expect(api.updateMediaServerSettings).toHaveBeenCalledWith({ plex_import_url: 'http://new-plex.local:32400' }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(api.discoverHouseholdTransfer).toHaveBeenCalledWith('plex', { token: 'owner-token' }, expect.any(AbortSignal));
    const list = screen.getByRole('list', { name: 'People found' });
    expect((within(list).getByRole('checkbox', { name: 'Owner' }) as HTMLInputElement).checked).toBe(true);
    expect((within(list).getByRole('checkbox', { name: 'Sam' }) as HTMLInputElement).checked).toBe(true);
    expect((within(list).getByRole('checkbox', { name: 'Pat' }) as HTMLInputElement).disabled).toBe(true);
    expect((within(list).getByRole('checkbox', { name: 'Lee (disabled)' }) as HTMLInputElement).checked).toBe(false);

    await browser.click(screen.getByRole('button', { name: 'Bring over 2 people' }));
    expect(transfer.streamHouseholdTransfer).toHaveBeenCalledWith('plex', { token: 'owner-token', member_ids: ['owner', 'sam'] }, expect.any(Function), expect.any(AbortSignal));
    act(() => emit({ type: 'progress', source_id: 'owner', source_name: 'Owner', completed: 0, total: 2 }));
    expect(screen.getByText(/Working on Owner/)).toBeTruthy();
    act(() => emit({ type: 'member', member: member('owner', 'Owner', 'import', { reset_url: 'http://lumina.test/reset/owner', summary: { watched: 14, in_progress: 2, favorites: 8, up_to_date: 1, unmatched: 3, unmatched_names: [] } }), completed: 1, total: 2 }));
    expect((await screen.findByLabelText('Password link for owner') as HTMLInputElement).value).toBe('http://lumina.test/reset/owner');
    expect(screen.getByText('14 watched · 2 in progress · 3 unmatched')).toBeTruthy();
    expect(screen.queryByText(/8 favorites/)).toBeNull();
    await act(async () => { reject(new Error('Plex stopped responding.')); });

    expect(await screen.findByRole('heading', { name: 'Transfer needs attention' })).toBeTruthy();
    expect((screen.getByLabelText('Password link for owner') as HTMLInputElement).value).toBe('http://lumina.test/reset/owner');
    await browser.click(screen.getByRole('button', { name: 'Retry failed or unfinished' }));
    expect((screen.getByLabelText(/^Plex server owner token/) as HTMLInputElement).value).toBe('');
    expect((screen.getByRole('button', { name: 'Retry 1 person' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('allows discovery during a scan but waits for its terminal state before importing and stops polling', async () => {
    api.listImports
      .mockResolvedValueOnce([{ id: 'scan', root_id: 'films', state: 'running', visibility: 'shared', counters: {}, coverage: 'incomplete', error: null, created_at: '', finished_at: null }])
      .mockResolvedValueOnce([{ id: 'scan', root_id: 'films', state: 'succeeded', visibility: 'shared', counters: {}, coverage: 'complete', error: null, created_at: '', finished_at: '' }]);
    api.discoverHouseholdTransfer.mockResolvedValue({ members: [member('sam', 'Sam', 'create')] });
    const timer = vi.spyOn(window, 'setTimeout');
    const browser = userEvent.setup();
    render(<HouseholdTransferStep onBack={vi.fn()} onFinish={vi.fn().mockResolvedValue(undefined)} />);
    await screen.findByRole('heading', { name: 'Where are you moving from?' });
    await browser.click(screen.getByRole('radio', { name: /Plex/ }));
    await browser.click(screen.getByRole('button', { name: 'Continue' }));
    await browser.type(screen.getByLabelText(/^Plex server owner token/), 'owner-token');
    await browser.click(screen.getByRole('button', { name: 'Find people' }));
    expect(await screen.findByRole('progressbar', { name: 'Media scan underway' })).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Bring over 1 person' }) as HTMLButtonElement).disabled).toBe(true);

    const poll = timer.mock.calls.find(([, delay]) => delay === 5000)?.[0];
    expect(poll).toBeTypeOf('function');
    await act(async () => { (poll as TimerHandler as () => void)(); await Promise.resolve(); });
    await waitFor(() => expect((screen.getByRole('button', { name: 'Bring over 1 person' }) as HTMLButtonElement).disabled).toBe(false));
    expect(api.listImports).toHaveBeenCalledTimes(2);
    expect(timer.mock.calls.filter(([, delay]) => delay === 5000)).toHaveLength(1);
  });

  it('accepts a Jellyfin administrator without a password', async () => {
    const browser = userEvent.setup();
    render(<HouseholdTransferStep onBack={vi.fn()} onFinish={vi.fn().mockResolvedValue(undefined)} />);
    await screen.findByRole('heading', { name: 'Where are you moving from?' });
    await browser.click(screen.getByRole('radio', { name: /Jellyfin/ }));
    await browser.click(screen.getByRole('button', { name: 'Continue' }));
    await browser.type(screen.getByLabelText(/^Jellyfin server address/), 'http://jellyfin.local:8096');
    await browser.type(screen.getByLabelText(/^Jellyfin administrator username/), 'owner');
    expect((screen.getByLabelText('Jellyfin administrator password') as HTMLInputElement).required).toBe(false);
    expect((screen.getByRole('button', { name: 'Find people' }) as HTMLButtonElement).disabled).toBe(false);
    await browser.click(screen.getByRole('button', { name: 'Find people' }));
    expect(api.discoverHouseholdTransfer).toHaveBeenCalledWith('jellyfin', { username: 'owner', password: '' }, expect.any(AbortSignal));
  });

  it('can leave an in-flight discovery without showing its stale error', async () => {
    let reject!: (failure: Error) => void;
    api.discoverHouseholdTransfer.mockReturnValue(new Promise((_resolve, rejectPromise) => { reject = rejectPromise; }));
    const browser = userEvent.setup();
    render(<HouseholdTransferStep onBack={vi.fn()} onFinish={vi.fn().mockResolvedValue(undefined)} />);
    await screen.findByRole('heading', { name: 'Where are you moving from?' });
    await browser.click(screen.getByRole('radio', { name: /Plex/ }));
    await browser.click(screen.getByRole('button', { name: 'Continue' }));
    await browser.type(screen.getByLabelText(/^Plex server owner token/), 'owner-token');
    await browser.click(screen.getByRole('button', { name: 'Find people' }));
    expect((screen.getByLabelText(/^Plex server owner token/) as HTMLInputElement).disabled).toBe(true);
    await browser.click(screen.getByRole('button', { name: 'Back' }));
    await act(async () => { reject(new Error('Late Plex failure.')); });
    expect(screen.getByRole('heading', { name: 'Where are you moving from?' })).toBeTruthy();
    expect(screen.queryByText('Late Plex failure.')).toBeNull();
  });

  it('clears the source secret when discovery fails', async () => {
    api.discoverHouseholdTransfer.mockRejectedValue(new Error('Plex did not answer.'));
    const browser = userEvent.setup();
    render(<HouseholdTransferStep onBack={vi.fn()} onFinish={vi.fn().mockResolvedValue(undefined)} />);
    await screen.findByRole('heading', { name: 'Where are you moving from?' });
    await browser.click(screen.getByRole('radio', { name: /Plex/ }));
    await browser.click(screen.getByRole('button', { name: 'Continue' }));
    const token = screen.getByLabelText(/^Plex server owner token/) as HTMLInputElement;
    await browser.type(token, 'temporary-token');
    await browser.click(screen.getByRole('button', { name: 'Find people' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Plex did not answer.');
    expect(token.value).toBe('');
  });

  it('stops an import responsively, preserves committed links, and ignores late stream callbacks', async () => {
    api.discoverHouseholdTransfer.mockResolvedValue({ members: [member('owner', 'Owner', 'import'), member('sam', 'Sam', 'create')] });
    let emit!: (event: HouseholdTransferEvent) => void;
    let finish!: () => void;
    let streamSignal!: AbortSignal;
    transfer.streamHouseholdTransfer.mockImplementation((_provider, _payload, onEvent, signal) => {
      emit = onEvent;
      streamSignal = signal;
      return new Promise<void>((resolve) => { finish = resolve; });
    });
    const browser = userEvent.setup();
    render(<HouseholdTransferStep onBack={vi.fn()} onFinish={vi.fn().mockResolvedValue(undefined)} />);
    await screen.findByRole('heading', { name: 'Where are you moving from?' });
    await browser.click(screen.getByRole('radio', { name: /Plex/ }));
    await browser.click(screen.getByRole('button', { name: 'Continue' }));
    await browser.type(screen.getByLabelText(/^Plex server owner token/), 'owner-token');
    await browser.click(screen.getByRole('button', { name: 'Find people' }));
    await browser.click(await screen.findByRole('button', { name: 'Bring over 2 people' }));
    act(() => emit({ type: 'member', member: member('owner', 'Owner', 'import', { reset_url: 'http://lumina.test/reset/owner' }), completed: 1, total: 2 }));
    await browser.click(screen.getByRole('button', { name: 'Stop transfer' }));

    expect(streamSignal.aborted).toBe(true);
    expect(await screen.findByText(/current person may still finish on the server/i)).toBeTruthy();
    expect((screen.getByLabelText('Password link for owner') as HTMLInputElement).value).toBe('http://lumina.test/reset/owner');
    act(() => {
      emit({ type: 'member', member: member('sam', 'Sam', 'create', { reset_url: 'http://lumina.test/reset/sam' }), completed: 2, total: 2 });
      finish();
    });
    expect(screen.queryByLabelText('Password link for sam')).toBeNull();
    await browser.click(screen.getByRole('button', { name: 'Find people again' }));
    expect((screen.getByLabelText(/^Plex server address/) as HTMLInputElement).readOnly).toBe(true);
    expect((screen.getByLabelText(/^Plex server owner token/) as HTMLInputElement).value).toBe('');
    await browser.click(screen.getByRole('button', { name: 'Back' }));
    expect((screen.getByLabelText('Password link for owner') as HTMLInputElement).value).toBe('http://lumina.test/reset/owner');

    await browser.click(screen.getByRole('button', { name: 'Find people again' }));
    await browser.type(screen.getByLabelText(/^Plex server owner token/), 'fresh-token');
    await browser.click(screen.getByRole('button', { name: 'Find people' }));
    await browser.click(await screen.findByRole('button', { name: 'Bring over 2 people' }));
    await browser.click(screen.getByRole('button', { name: 'Stop transfer' }));
    expect((screen.getByLabelText('Password link for owner') as HTMLInputElement).value).toBe('http://lumina.test/reset/owner');
    expect(screen.queryByLabelText('Password link for sam')).toBeNull();
  });
});
