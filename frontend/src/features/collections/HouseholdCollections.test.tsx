import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { CollectionsPage } from './HouseholdCollections';
import { collection, fakeCollectionApi, poster } from './testFixtures';

const page = (api: ReturnType<typeof fakeCollectionApi>, extra: Partial<Parameters<typeof CollectionsPage>[0]> = {}) => render(
  <CollectionsPage api={api} currentUserId="me" library={[]} onBackToList={vi.fn()} onOpenCollection={vi.fn()} onRouteHandled={vi.fn()} route={{ collections: 'list' }} {...extra} />,
);

describe('Collections list', () => {
  it('has the masthead, the two create actions and a tile per collection', async () => {
    page(fakeCollectionApi([collection({ id: 'c1', name: 'Rainy Sundays', titles: [poster('t1'), poster('t2')] }), collection({ id: 'c2', name: 'Family videos', item_count: 12, owner_user_id: 'other' })]));
    expect(await screen.findByRole('heading', { level: 1, name: 'Collections' })).toBeTruthy();
    expect(document.querySelector('.g-kicker')).toBeNull();
    expect(screen.getByRole('button', { name: 'New collection' }).classList.contains('is-primary')).toBe(true);
    expect(screen.getByRole('button', { name: 'New smart collection' }).classList.contains('is-secondary')).toBe(true);
    expect(screen.getByRole('link', { name: /Rainy Sundays/ }).getAttribute('href')).toBe('/library/collections/c1');
    expect(within(screen.getByRole('link', { name: /Family videos/ })).getByText('12 items')).toBeTruthy();
  });

  it('the mosaic uses up to four posters and falls back to the name', async () => {
    page(fakeCollectionApi([collection({ id: 'c1', name: 'Many', titles: [1, 2, 3, 4, 5].map((n) => poster(`t${n}`)) }), collection({ id: 'c2', name: 'Bare', titles: [] })]));
    const many = await screen.findByRole('link', { name: /Many/ });
    expect(many.querySelectorAll('.g-mosaic .g-art')).toHaveLength(4);
    expect(screen.getByRole('link', { name: /Bare/ }).querySelector('.g-card-name')?.textContent).toBe('Bare');
  });

  it('is an empty state with both actions when there are none', async () => {
    page(fakeCollectionApi([]));
    expect(await screen.findByText('No collections yet.')).toBeTruthy();
    expect(screen.getByText('Group titles your household loves, or let a smart collection gather them.')).toBeTruthy();
  });

  it('?new=smart opens the builder once and leaves the address clean', async () => {
    const onRouteHandled = vi.fn();
    page(fakeCollectionApi([]), { onRouteHandled, route: { collections: 'list', create: 'smart' } });
    expect(await screen.findByRole('dialog', { name: 'New smart collection' })).toBeTruthy();
    expect(onRouteHandled).toHaveBeenCalledOnce();
  });

  it('an unknown collection id shows the list', async () => {
    page(fakeCollectionApi([collection({ id: 'c1', name: 'Rainy Sundays' })]), { route: { collections: 'detail', collectionId: 'gone' } });
    expect(await screen.findByRole('heading', { level: 1, name: 'Collections' })).toBeTruthy();
  });

  it('embedded renders tiles and a See all link and no masthead or create buttons', async () => {
    page(fakeCollectionApi([collection({ id: 'c1', name: 'Rainy Sundays' })]), { embedded: true });
    expect(await screen.findByRole('link', { name: /Rainy Sundays/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'See all' }).getAttribute('href')).toBe('/library/collections');
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
    expect(screen.queryByRole('button', { name: 'New collection' })).toBeNull();
  });

  it('loads with a skeleton and fails with Try again', async () => {
    page(fakeCollectionApi([], { failFirstList: true }));
    expect(screen.getByRole('status').textContent).toBe('Loading collections…');
    expect((await screen.findByRole('alert')).textContent).toContain('Lumina could not load your collections.');
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('No collections yet.')).toBeTruthy();
  });

  it('a failed create keeps the dialog open with the reason and the name', async () => {
    const api = fakeCollectionApi([]) as unknown as { create: ReturnType<typeof vi.fn> };
    api.create.mockRejectedValueOnce(new Error('duplicate'));
    page(api as never);
    await userEvent.click(await screen.findByRole('button', { name: 'New collection' }));
    const dialog = await screen.findByRole('dialog', { name: 'New collection' });
    await userEvent.type(within(dialog).getByRole('textbox'), 'Dupes');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect((await within(dialog).findByRole('alert')).textContent).toContain('could not be updated');
    expect((within(dialog).getByRole('textbox') as HTMLInputElement).value).toBe('Dupes');
    expect(screen.queryByText('Lumina could not load your collections.')).toBeNull();
  });

  it('a failed detail fetch shows an error with Try again instead of loading forever', async () => {
    const api = fakeCollectionApi([collection({ id: 'c1', name: 'Rainy Sundays' })]) as unknown as { get: ReturnType<typeof vi.fn> };
    const real = api.get.getMockImplementation()!;
    api.get.mockRejectedValueOnce(new Error('offline'));
    page(api as never, { route: { collections: 'detail', collectionId: 'c1' } });
    expect((await screen.findByRole('alert')).textContent).toContain('could not load');
    api.get.mockImplementation(real);
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: 'Collections' })).toBeTruthy();
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('a failed delete or rename tells the user and keeps the collection', async () => {
    const api = fakeCollectionApi([collection({ id: 'c1', name: 'Rainy Sundays' })]) as unknown as { remove: ReturnType<typeof vi.fn>; rename: ReturnType<typeof vi.fn> };
    api.rename.mockRejectedValueOnce(new Error('boom'));
    api.remove.mockRejectedValueOnce(new Error('boom'));
    page(api as never, { route: { collections: 'detail', collectionId: 'c1' } });
    await userEvent.click(await screen.findByRole('button', { name: 'Rename' }));
    await userEvent.type(within(screen.getByRole('dialog')).getByRole('textbox'), ' II');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect((await screen.findByRole('alert')).textContent).toContain('could not be updated');
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await vi.waitFor(() => expect(api.remove).toHaveBeenCalledTimes(1));
    expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Collections' })).toBeTruthy();
  });
});
