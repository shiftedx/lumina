import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { AutocompleteInput } from './AutocompleteInput';

function Example() {
  const [value, setValue] = useState('');
  return <label>Genre<AutocompleteInput name="genre" onChange={(event) => setValue(event.currentTarget.value)} options={['Drama', 'Comedy']} value={value} /></label>;
}

describe('AutocompleteInput', () => {
  it('filters and chooses a suggestion without submitting, then accepts free text', async () => {
    const submit = vi.fn((event) => event.preventDefault());
    render(<form onSubmit={submit}><Example /></form>);
    const control = screen.getByRole('combobox', { name: 'Genre' }) as HTMLInputElement;
    await userEvent.type(control, 'dra');
    expect(screen.queryByRole('option', { name: 'Comedy' })).toBeNull();
    await userEvent.keyboard('{ArrowDown}{Enter}');
    expect(control.value).toBe('Drama');
    expect(document.activeElement).toBe(control);
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(submit).not.toHaveBeenCalled();
    await userEvent.clear(control);
    await userEvent.type(control, 'A new genre');
    expect(control.value).toBe('A new genre');
  });

  it('chooses descriptive values by pointer and dismisses suggestions without leaking Escape', () => {
    const parent = vi.fn();
    const change = vi.fn();
    render(<div onKeyDown={parent}><label>Episode group<AutocompleteInput onChange={change} options={[{ value: 'group-1', label: 'DVD order · 12 episodes' }]} /></label></div>);
    const control = screen.getByRole('combobox', { name: 'Episode group' }) as HTMLInputElement;
    fireEvent.focus(control);
    fireEvent.keyDown(control, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(parent).not.toHaveBeenCalled();
    fireEvent.focus(control);
    fireEvent.click(screen.getByRole('option', { name: 'DVD order · 12 episodes' }));
    expect(control.value).toBe('group-1');
    expect(change).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
