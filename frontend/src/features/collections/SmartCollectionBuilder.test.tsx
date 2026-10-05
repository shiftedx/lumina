import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { HouseholdCollection, SmartCollectionRule } from '../../types';

const api = vi.hoisted(() => ({ buildSmartCollectionRules: vi.fn(), previewSmartCollectionRules: vi.fn(), createHouseholdCollection: vi.fn(), setSmartCollectionRules: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { SmartCollectionBuilder } = await import('./SmartCollectionBuilder');
const { ApiRequestError } = await import('../../api');

const noop = () => undefined;
beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  api.previewSmartCollectionRules.mockResolvedValue({ count: 34, sample: [] });
});
afterEach(() => { vi.resetAllMocks(); vi.useRealTimers(); });

const props = { aiAvailable: false, mode: 'new', onCancel: noop, onCreated: noop, open: true } as const;
const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
const dialog = () => screen.getByRole('dialog');
const isChecked = (el: HTMLElement) => (el as HTMLInputElement).checked;

describe('Smart collection builder', () => {
  it('is a dialog with Name, Match, rule rows and Add rule', async () => {
    render(<SmartCollectionBuilder {...props} />);
    const d = screen.getByRole('dialog', { name: 'New smart collection' });
    expect(within(d).getByLabelText('Name')).toBeTruthy();
    expect(within(d).getByRole('group', { name: 'Match' })).toBeTruthy();
    expect(isChecked(within(d).getByRole('radio', { name: 'All rules' }))).toBe(true);
    await user().click(within(d).getByRole('button', { name: 'Add rule' }));
    expect(within(d).getAllByRole('combobox', { name: /^Field/ })).toHaveLength(2);
    expect(within(d).getAllByRole('button', { name: /^Remove rule/ })).toHaveLength(2);
  });

  it('a bad value shows a field error and blocks Save', async () => {
    const u = user();
    render(<SmartCollectionBuilder {...props} />);
    await u.type(within(dialog()).getByLabelText('Name'), 'Long films');
    await u.selectOptions(within(dialog()).getByRole('combobox', { name: 'Field 1' }), 'runtime');
    await u.type(within(dialog()).getByLabelText('Value 1'), 'long');
    await u.click(within(dialog()).getByRole('button', { name: 'Save' }));
    expect(within(dialog()).getByLabelText('Value 1').getAttribute('aria-invalid')).toBe('true');
    expect(api.createHouseholdCollection).not.toHaveBeenCalled();
  });

  it('saves a new collection with the rows as rules', async () => {
    api.createHouseholdCollection.mockResolvedValue({ id: 'c9', name: 'Dramas' });
    const onCreated = vi.fn();
    const u = user();
    render(<SmartCollectionBuilder {...props} onCreated={onCreated} />);
    await u.type(within(dialog()).getByLabelText('Name'), 'Dramas');
    await u.type(within(dialog()).getByLabelText('Value 1'), 'Drama');
    await u.click(within(dialog()).getByRole('button', { name: 'Save' }));
    expect(api.createHouseholdCollection).toHaveBeenCalledWith({ name: 'Dramas', visibility: 'private', rules: { type: 'movie', match: 'all', conditions: [{ field: 'genre', op: 'is', value: 'Drama' }], limit: 100 } });
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 'c9', name: 'Dramas' }));
  });

  it('the preview is debounced and ignored after close', async () => {
    const u = user();
    const view = render(<SmartCollectionBuilder {...props} />);
    await u.type(within(dialog()).getByLabelText('Value 1'), 'Drama');
    expect(api.previewSmartCollectionRules).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(api.previewSmartCollectionRules).toHaveBeenCalledTimes(1);
    expect(await within(dialog()).findByText('Matches 34 titles')).toBeTruthy();
    view.rerender(<SmartCollectionBuilder {...props} open={false} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a preview that answers after close is dropped', async () => {
    let resolve: (value: { count: number; sample: never[] }) => void = noop;
    api.previewSmartCollectionRules.mockReturnValue(new Promise((done) => { resolve = done; }));
    const u = user();
    const view = render(<SmartCollectionBuilder {...props} />);
    await u.type(within(dialog()).getByLabelText('Value 1'), 'Drama');
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    view.rerender(<SmartCollectionBuilder {...props} open={false} />);
    view.rerender(<SmartCollectionBuilder {...props} />);
    await act(async () => { resolve({ count: 99, sample: [] }); });
    expect(screen.queryByText('Matches 99 titles')).toBeNull();
  });

  it('says so when nothing matches', async () => {
    api.previewSmartCollectionRules.mockResolvedValue({ count: 0, sample: [] });
    render(<SmartCollectionBuilder {...props} />);
    await user().type(within(dialog()).getByLabelText('Value 1'), 'Drama');
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(await within(dialog()).findByText('Nothing matches these rules.')).toBeTruthy();
  });

  it('labels the assistant suggestion when AI is available, and drafts rows from words', async () => {
    const drafted: SmartCollectionRule = { type: 'movie', match: 'all', conditions: [{ field: 'genre', op: 'is', value: 'Comedy' }, { field: 'year', op: 'gte', value: 1990 }], limit: 100 };
    api.buildSmartCollectionRules.mockResolvedValue({ rule: drafted, preview: { count: 12, sample: [] }, description: 'Comedies from 1990 on.' });
    const u = user();
    render(<SmartCollectionBuilder {...props} aiAvailable />);
    expect(screen.getByText('Suggested by your assistant')).toBeTruthy();
    await u.type(screen.getByLabelText('Describe it'), '90s comedies');
    await u.click(screen.getByRole('button', { name: 'Draft rules' }));
    expect(await screen.findByText('Matches 12 titles')).toBeTruthy();
    expect((screen.getByLabelText('Value 2') as HTMLInputElement).value).toBe('1990');
  });

  it('explains a rejected draft and hides drafting without an AI endpoint', async () => {
    api.buildSmartCollectionRules.mockRejectedValueOnce(new ApiRequestError('invalid', 422)).mockRejectedValueOnce(new ApiRequestError('Not found', 404));
    const u = user();
    render(<SmartCollectionBuilder {...props} aiAvailable />);
    await u.type(screen.getByLabelText('Describe it'), 'something odd');
    await u.click(screen.getByRole('button', { name: 'Draft rules' }));
    expect(await screen.findByText('Lumina could not turn that into rules. Try other words, or add rules below.')).toBeTruthy();
    await u.click(screen.getByRole('button', { name: 'Draft rules' }));
    await waitFor(() => expect(screen.queryByLabelText('Describe it')).toBeNull());
  });

  it('edit mode is titled Edit rules, starts from the rules and saves through setSmartCollectionRules', async () => {
    const rules: SmartCollectionRule = { type: 'series', match: 'any', conditions: [{ field: 'year', op: 'gte', value: 2000 }], limit: 100 };
    const collection = { id: 'c1', name: 'Recent', rules } as unknown as HouseholdCollection;
    api.setSmartCollectionRules.mockResolvedValue({ ...collection, revision: 2 });
    const onCreated = vi.fn();
    const u = user();
    render(<SmartCollectionBuilder {...props} collection={collection} mode="edit" onCreated={onCreated} />);
    expect(screen.getByRole('dialog', { name: 'Edit rules' })).toBeTruthy();
    expect(within(dialog()).queryByLabelText('Name')).toBeNull();
    expect((within(dialog()).getByLabelText('Value 1') as HTMLInputElement).value).toBe('2000');
    expect(isChecked(within(dialog()).getByRole('radio', { name: 'Any rule' }))).toBe(true);
    await u.clear(within(dialog()).getByLabelText('Value 1'));
    await u.type(within(dialog()).getByLabelText('Value 1'), '2010');
    await u.click(within(dialog()).getByRole('button', { name: 'Save' }));
    expect(api.setSmartCollectionRules).toHaveBeenCalledWith('c1', { ...rules, conditions: [{ field: 'year', op: 'gte', value: 2010 }] });
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
  });
  it('edit keeps the stored sort and limit', async () => {
    const rules = { type: 'movie', match: 'all', conditions: [{ field: 'year', op: 'gte', value: 2000 }], sort: { field: 'year', order: 'desc' }, limit: 25 } as unknown as SmartCollectionRule;
    const collection = { id: 'c2', name: 'Sorted', rules } as unknown as HouseholdCollection;
    api.setSmartCollectionRules.mockResolvedValue({ ...collection, revision: 2 });
    render(<SmartCollectionBuilder {...props} collection={collection} mode="edit" />);
    await user().click(within(dialog()).getByRole('button', { name: 'Save' }));
    expect(api.setSmartCollectionRules).toHaveBeenCalledWith('c2', rules);
  });

  it('a drafted rule brings its sort and limit into the saved rule', async () => {
    const drafted = { type: 'movie', match: 'all', conditions: [{ field: 'genre', op: 'is', value: 'Comedy' }], sort: { field: 'year', order: 'desc' }, limit: 12 } as unknown as SmartCollectionRule;
    api.buildSmartCollectionRules.mockResolvedValue({ rule: drafted, preview: { count: 3, sample: [] }, description: 'x' });
    api.setSmartCollectionRules.mockResolvedValue({ id: 'c3' });
    const collection = { id: 'c3', name: 'D', rules: { type: 'movie', match: 'all', conditions: [{ field: 'year', op: 'gte', value: 1 }], limit: 100 } } as unknown as HouseholdCollection;
    const u = user();
    render(<SmartCollectionBuilder {...props} aiAvailable collection={collection} mode="edit" />);
    await u.type(screen.getByLabelText('Describe it'), 'comedies');
    await u.click(screen.getByRole('button', { name: 'Draft rules' }));
    await screen.findByText('Matches 3 titles');
    await u.click(within(dialog()).getByRole('button', { name: 'Save' }));
    expect(api.setSmartCollectionRules).toHaveBeenCalledWith('c3', drafted);
  });
});
