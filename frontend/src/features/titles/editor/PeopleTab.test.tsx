import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { PersonRef } from '../../../types';
import { docFixture, fieldState } from './editorFixtures';
import type { TabProps } from './editorModel';

const api = vi.hoisted(() => ({ searchMetadataPeople: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { default: PeopleTab, moveItem } = await import('./PeopleTab');

const credit = (name: string, id: string | null = `p-${name}`, type: PersonRef['type'] = 'Actor'): PersonRef => ({ person_id: id, name, role: 'Role', type });
function setup(people: PersonRef[]) {
  api.searchMetadataPeople.mockResolvedValue([]);
  const doc = docFixture('series');
  doc.fields.people = fieldState(people);
  const setField = vi.fn();
  const togglePin = vi.fn();
  const props = { doc, draft: {}, user: { role: 'admin' }, setField, togglePin } as unknown as TabProps;
  render(<PeopleTab {...props} />);
  return { setField, togglePin, people };
}

describe('PeopleTab', () => {
  it('lists each credit with name, role and a type select', () => {
    setup([credit('A'), credit('B', null, 'Director')]);
    expect((screen.getByLabelText('Name 1') as HTMLInputElement).value).toBe('A');
    expect((screen.getByLabelText('Role 2') as HTMLInputElement).value).toBe('Role');
    expect((screen.getByLabelText('Type 2') as HTMLSelectElement).value).toBe('Director');
  });

  it('Move down and Move up reorder by button and announce it', async () => {
    const { setField, people } = setup([credit('A'), credit('B'), credit('C')]);
    expect((screen.getByRole('button', { name: 'Move up A' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Move down C' }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Move down A' }));
    expect(setField).toHaveBeenCalledWith('t1', 'people', [people[1], people[0], people[2]], people);
    expect(screen.getByRole('status').textContent).toBe('A moved to position 2 of 3.');
  });

  it('keeps focus on the moved row, so pressing Move up twice carries one person to the top', async () => {
    setup([credit('A'), credit('B'), credit('C')]);
    screen.getByRole('button', { name: 'Move up C' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Move up C' }));
    await userEvent.keyboard('{Enter}');
    expect((screen.getByLabelText('Name 1') as HTMLInputElement).value).toBe('C');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Move down C' }));
  });

  it('debounces the people search and keeps only the newest answer', async () => {
    vi.useFakeTimers();
    try {
      setup([credit('A')]);
      const name = screen.getByLabelText('Name 1');
      fireEvent.change(name, { target: { value: 'Ab' } });
      fireEvent.change(name, { target: { value: 'Abc' } });
      expect(api.searchMetadataPeople).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(260);
      expect(api.searchMetadataPeople).toHaveBeenCalledTimes(1);
      expect(api.searchMetadataPeople).toHaveBeenCalledWith('Abc', 8);
    } finally { vi.useRealTimers(); }
  });

  it('Remove drops the row; Add person appends an empty row, focuses it and sends nothing for it', async () => {
    const { setField, people } = setup([credit('A'), credit('B')]);
    await userEvent.click(screen.getByRole('button', { name: 'Remove A' }));
    expect(setField).toHaveBeenLastCalledWith('t1', 'people', [people[1]], people);
    await userEvent.click(screen.getByRole('button', { name: 'Add person' }));
    expect(document.activeElement).toBe(screen.getByLabelText('Name 2'));
    expect(setField).toHaveBeenLastCalledWith('t1', 'people', [people[1]], people);
  });

  it('keeps a known person_id when picked and sends null for a fresh name', () => {
    const { setField, people } = setup([credit('Adam Scott', 'p1'), credit('Zed', null)]);
    fireEvent.change(screen.getByLabelText('Name 2'), { target: { value: 'Adam Scott' } });
    expect((setField.mock.calls[0][2] as PersonRef[])[1].person_id).toBe('p1');
    fireEvent.change(screen.getByLabelText('Name 2'), { target: { value: 'Someone New' } });
    expect((setField.mock.calls[1][2] as PersonRef[])[1].person_id).toBeNull();
    expect(setField.mock.calls[1][3]).toBe(people);
  });

  it('has one lock for the whole list', async () => {
    const { togglePin } = setup([credit('A')]);
    await userEvent.click(screen.getByRole('button', { name: 'Lock Cast' }));
    expect(togglePin).toHaveBeenCalledWith('t1', 'people');
  });

  it('moveItem stays in bounds', () => {
    const list = [1, 2, 3];
    expect(moveItem(list, 0, 2)).toEqual([2, 3, 1]);
    expect(moveItem(list, 0, 3)).toBe(list);
  });
});
