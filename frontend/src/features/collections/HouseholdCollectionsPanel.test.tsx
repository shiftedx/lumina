import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from '../../api';
import type { HouseholdCollection } from '../../types';
import { HouseholdCollectionsPanel } from './HouseholdCollections';

describe('household collection recovery', () => {
  it('labels owned and other-member shared collections without exposing identity or controls', async () => {
    const owned: HouseholdCollection = { id: 'owned', owner_user_id: 'viewer', name: 'Mine', visibility: 'private', revision: 0, item_count: 0, items: [], entries: [], created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z' };
    const shared: HouseholdCollection = { ...owned, id: 'shared', owner_user_id: 'opaque-owner-id', name: 'Household picks', visibility: 'shared' };
    render(<HouseholdCollectionsPanel api={{ list: vi.fn(async () => [owned, shared]), get: vi.fn(async (id) => id === owned.id ? owned : shared), create: vi.fn(), rename: vi.fn(), visibility: vi.fn(), remove: vi.fn(), addItem: vi.fn() }} currentUserId="viewer" library={[]} />);

    expect(await screen.findByText(/Your private collection/)).toBeTruthy();
    expect(screen.getByText(/Shared by another household member/)).toBeTruthy();
    expect(screen.queryByText('opaque-owner-id')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete Household picks' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete Mine' })).toBeTruthy();
  });

  it('drops a stale collection after access changes without exposing another member detail', async () => {
    const collection: HouseholdCollection = {
      id: 'collection-private', owner_user_id: 'viewer', name: 'Before access changed', visibility: 'private', revision: 0, item_count: 0, items: [], entries: [],
      created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z',
    };
    const list = vi.fn().mockResolvedValueOnce([collection]).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    render(<HouseholdCollectionsPanel
      api={{
        list,
        get: vi.fn(async () => collection),
        create: vi.fn(),
        rename: vi.fn(),
        visibility: vi.fn(),
        remove: vi.fn(async () => { throw new ApiRequestError('Owner-only collection', 403); }),
        addItem: vi.fn(),
      }}
      currentUserId="viewer"
      library={[]}
    />);

    expect(await screen.findByText('Before access changed')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /delete before access changed/i }));
    expect((await screen.findByRole('alert')).textContent).toContain('Collection access changed. The latest household view is shown.');
    await waitFor(() => expect(screen.queryByText('Before access changed')).toBeNull());
    expect(screen.getByRole('alert').textContent).not.toContain('Owner-only collection');
  });

  it('rolls a rejected controlled rename back and clears a successful membership choice', async () => {
    const collection: HouseholdCollection = {
      id: 'collection-one', owner_user_id: 'viewer', name: 'Original name', visibility: 'private', revision: 0, item_count: 0, items: [], entries: [],
      created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z',
    };
    const added: HouseholdCollection = { ...collection, item_count: 1, items: [{ id: 'item-one', title: 'First title' }] };
    const api = {
      list: vi.fn(async () => [collection]), get: vi.fn(async () => collection), create: vi.fn(),
      rename: vi.fn(async () => { throw new ApiRequestError('Database constraint detail', 409); }),
      visibility: vi.fn(), remove: vi.fn(), addItem: vi.fn(async () => added),
    };
    const user = userEvent.setup();
    render(<HouseholdCollectionsPanel api={api} currentUserId="viewer" library={[{ id: 'item-one', title: 'First title' }, { id: 'item-two', title: 'Second title' }]} />);

    const rename = await screen.findByRole('textbox', { name: /rename original name/i });
    await user.clear(rename);
    await user.type(rename, 'Rejected name');
    await user.tab();
    await waitFor(() => expect((rename as HTMLInputElement).value).toBe('Original name'));
    expect(screen.getByRole('alert').textContent).not.toContain('Database constraint detail');

    const chooser = screen.getByRole('combobox', { name: /choose a title for original name/i });
    await user.selectOptions(chooser, 'item-one');
    await user.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(api.addItem).toHaveBeenCalledWith('collection-one', 'item-one'));
    expect((chooser as HTMLSelectElement).value).toBe('');
  });

  it('does not render a previous member collection after a keyed session rollover', async () => {
    let releaseFirst: ((value: HouseholdCollection[]) => void) | undefined;
    const first = new Promise<HouseholdCollection[]>((resolve) => { releaseFirst = resolve; });
    const collection: HouseholdCollection = {
      id: 'member-a-only', owner_user_id: 'member-a', name: 'Member A private', visibility: 'private', revision: 0, item_count: 0, items: [], entries: [],
      created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z',
    };
    const api = {
      list: vi.fn().mockReturnValueOnce(first).mockResolvedValueOnce([]), get: vi.fn(async () => collection), create: vi.fn(),
      rename: vi.fn(), visibility: vi.fn(), remove: vi.fn(), addItem: vi.fn(),
    };
    const view = render(<HouseholdCollectionsPanel api={api} currentUserId="member-a" key="member-a" library={[]} />);
    view.rerender(<HouseholdCollectionsPanel api={api} currentUserId="member-b" key="member-b" library={[]} />);
    releaseFirst?.([collection]);

    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('Member A private')).toBeNull();
  });

  it('fetches entries only for open collections so a large household stays bounded', async () => {
    const collections = Array.from({ length: 6 }, (_, index): HouseholdCollection => ({
      id: `collection-${index}`, owner_user_id: 'viewer', name: `List ${index}`, visibility: 'private', revision: 0, item_count: 0, items: [], entries: [],
      created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z',
    }));
    const get = vi.fn(async (id: string) => collections.find((item) => item.id === id)!);
    const user = userEvent.setup();
    render(<HouseholdCollectionsPanel api={{
      list: vi.fn(async () => collections), get, create: vi.fn(), rename: vi.fn(), visibility: vi.fn(), remove: vi.fn(), addItem: vi.fn(),
    }} currentUserId="viewer" library={[]} />);

    expect(await screen.findByText('List 5')).toBeTruthy();
    expect(get.mock.calls.map((call) => call[0])).toEqual(['collection-0', 'collection-1', 'collection-2', 'collection-3']);
    await user.click(screen.getAllByText('Show titles')[1]);
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('collection-5'));
  });

  it('tracks overlapping mutations independently per collection', async () => {
    const collections = ['One', 'Two'].map((name, index): HouseholdCollection => ({
      id: `collection-${index}`, owner_user_id: 'viewer', name, visibility: 'private', revision: 0, item_count: 0, items: [], entries: [],
      created_at: '2026-07-16T00:00:00Z', updated_at: '2026-07-16T00:00:00Z',
    }));
    const remove = vi.fn((_id: string) => new Promise<void>(() => undefined));
    const user = userEvent.setup();
    render(<HouseholdCollectionsPanel api={{
      list: vi.fn(async () => collections), get: vi.fn(async (id) => collections.find((item) => item.id === id)!), create: vi.fn(),
      rename: vi.fn(), visibility: vi.fn(), remove, addItem: vi.fn(),
    }} currentUserId="viewer" library={[]} />);

    await user.click(await screen.findByRole('button', { name: 'Delete One' }));
    await user.click(screen.getByRole('button', { name: 'Delete Two' }));
    expect(remove.mock.calls.map((call) => call[0])).toEqual(['collection-0', 'collection-1']);
  });
});
