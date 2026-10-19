import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Field, fieldProps, Input, PasswordInput, Select } from '.';

/** The accessible description: aria-describedby's targets' text, joined (no jest-dom in this repo). */
const description = (element: Element) => (element.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean).map((id) => document.getElementById(id)?.textContent?.trim() ?? '').join(' ');

describe('Field', () => {
  it('wires the label, hint and error to the control', () => {
    const { rerender } = render(<Field hint="12 characters or more." label="Password">{(ids) => <Input {...fieldProps(ids)} />}</Field>);
    const input = screen.getByLabelText('Password');
    expect(description(input)).toBe('12 characters or more.');
    expect(input.hasAttribute('aria-invalid')).toBe(false);
    rerender(<Field error="Too short." hint="12 characters or more." label="Password">{(ids) => <Input {...fieldProps(ids)} />}</Field>);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(description(input)).toBe('12 characters or more. Too short.');
    expect(screen.getByRole('alert').textContent).toContain('Too short.');
  });

  it('announces an error only when it first appears', () => {
    const { rerender } = render(<Field error="Too short." label="Name">{(ids) => <Input {...fieldProps(ids)} />}</Field>);
    expect(document.body.contains(screen.getByRole('alert'))).toBe(true);
    rerender(<Field error="Too short." label="Name">{(ids) => <Input {...fieldProps(ids)} value="a" onChange={() => undefined} />}</Field>);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(document.body.contains(screen.getByText('Too short.'))).toBe(true);
  });

  it('can hide its label visually', () => {
    render(<Field hideLabel label="Search Lumina">{(ids) => <Input {...fieldProps(ids)} />}</Field>);
    expect(document.body.contains(screen.getByLabelText('Search Lumina'))).toBe(true);
    expect(screen.getByText('Search Lumina').className).toContain('sr-only');
  });
});

describe('PasswordInput', () => {
  it('toggles visibility without submitting and passes autocomplete through', async () => {
    let submitted = false;
    render(<form onSubmit={(event) => { event.preventDefault(); submitted = true; }}><label>Password<PasswordInput autoComplete="current-password" /></label></form>);
    const input = screen.getByLabelText('Password');
    expect(input.getAttribute('type')).toBe('password');
    expect(input.getAttribute('autocomplete')).toBe('current-password');
    const toggle = screen.getByRole('button', { name: 'Show password' });
    await userEvent.click(toggle);
    expect(input.getAttribute('type')).toBe('text');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(submitted).toBe(false);
  });
});

describe('Select', () => {
  it('exposes one branded combobox with the selected option and Field labelling', () => {
    render(<Field hint="Used for new files." label="Format">{(ids) => <Select {...fieldProps(ids)} defaultValue="b"><option value="a">A</option><option value="b">B</option></Select>}</Field>);
    const select = screen.getByRole('combobox', { name: 'Format' });
    expect(select.tagName).toBe('BUTTON');
    expect(select.textContent).toContain('B');
    expect(description(select)).toBe('Used for new files.');
    expect(screen.getAllByRole('combobox')).toHaveLength(1);
  });

  it('opens a grouped listbox, marks the selection, skips disabled options and changes through the native bridge', async () => {
    const changes: string[][] = [];
    render(
      <label>
        Format
        <Select defaultValue="b" onChange={(event) => changes.push([event.target.value, event.currentTarget.value, event.target.tagName])}>
          <optgroup label="Common"><option value="a">Alpha</option><option value="b">Beta</option></optgroup>
          <optgroup disabled label="Unavailable"><option value="c">Charlie</option></optgroup>
          <option value="d">Delta</option>
        </Select>
      </label>,
    );
    const trigger = screen.getByRole('combobox', { name: 'Format' });
    await userEvent.click(trigger);
    expect(screen.getByRole('listbox')).not.toBeNull();
    expect(screen.getByRole('group', { name: 'Common' })).not.toBeNull();
    expect(screen.getByRole('option', { name: 'Beta' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('option', { name: 'Charlie' }).getAttribute('aria-disabled')).toBe('true');
    await userEvent.click(screen.getByRole('option', { name: 'Charlie' }));
    expect(screen.getByRole('listbox')).not.toBeNull();
    await userEvent.keyboard('{ArrowDown}{Enter}');
    expect(changes).toEqual([['d', 'd', 'SELECT']]);
    expect(trigger.textContent).toContain('Delta');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('supports controlled values and does not visually accept a declined change', async () => {
    const changed = vi.fn();
    render(<label>Format<Select onChange={changed} value="a"><option value="a">Alpha</option><option value="b">Beta</option></Select></label>);
    const trigger = screen.getByRole('combobox', { name: 'Format' });
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('option', { name: 'Beta' }));
    expect(changed).toHaveBeenCalledOnce();
    expect(trigger.textContent).toContain('Alpha');
  });

  it('tracks a controlled value when its owner accepts the native change event', async () => {
    function Controlled() {
      const [value, setValue] = useState('a');
      return <label>Format<Select onChange={(event) => setValue(event.currentTarget.value)} value={value}><option value="a">Alpha</option><option value="b">Beta</option></Select></label>;
    }
    render(<Controlled />);
    const trigger = screen.getByRole('combobox', { name: 'Format' });
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('option', { name: 'Beta' }));
    expect(trigger.textContent).toContain('Beta');
  });

  it('handles Home, End, arrows, Escape, Tab and type-ahead without leaking handled keys', async () => {
    const parentKey = vi.fn();
    render(<div onKeyDown={parentKey}><label>Format<Select defaultValue="b"><option value="a">Alpha</option><option value="b">Beta</option><option disabled value="c">Charlie</option><option value="d">Delta</option></Select></label><button type="button">After</button></div>);
    const trigger = screen.getByRole('combobox', { name: 'Format' });
    trigger.focus();
    await userEvent.keyboard('{Home}{Enter}');
    expect(trigger.textContent).toContain('Alpha');
    expect(parentKey).not.toHaveBeenCalled();
    await userEvent.keyboard('{End}{Escape}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await userEvent.keyboard('d');
    expect(trigger.textContent).toContain('Delta');
    await userEvent.keyboard('{Enter}{Tab}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'After' }));
  });

  it('closes on outside interaction and when Escape is sent to the listbox', async () => {
    render(<><label>Format<Select><option>Alpha</option><option>Beta</option></Select></label><button type="button">After</button></>);
    const trigger = screen.getByRole('combobox', { name: 'Format' });
    await userEvent.click(trigger);
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('button', { name: 'After' }));
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('does not let a wrapping label reopen a selection or a sibling select', async () => {
    render(<><label>First<Select><option>Alpha</option><option>Beta</option></Select></label><label>Second<Select><option>One</option><option>Two</option></Select></label></>);
    await userEvent.click(screen.getByRole('combobox', { name: 'First' }));
    await userEvent.click(screen.getByRole('option', { name: 'Beta' }));
    expect(screen.queryByRole('listbox')).toBeNull();
    await userEvent.click(screen.getByRole('combobox', { name: 'Second' }));
    expect(screen.getAllByRole('listbox')).toHaveLength(1);
    expect(screen.getByRole('option', { name: 'One' })).not.toBeNull();
  });

  it('submits and resets through its hidden native select form bridge', async () => {
    const { container } = render(<form><label>Format<Select defaultValue="a" name="format"><option value="a">Alpha</option><option value="b">Beta</option></Select></label><button type="reset">Reset</button></form>);
    const form = container.querySelector('form')!;
    const trigger = screen.getByRole('combobox', { name: 'Format' });
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole('option', { name: 'Beta' }));
    expect(new FormData(form).get('format')).toBe('b');
    await userEvent.click(screen.getByRole('button', { name: 'Reset' }));
    await waitFor(() => expect(trigger.textContent).toContain('Alpha'));
    expect(new FormData(form).get('format')).toBe('a');
  });

  it('blocks an invalid form and focuses the visible trigger instead of its hidden native bridge', async () => {
    const submit = vi.fn();
    const { container } = render(<form onSubmit={(event) => { event.preventDefault(); submit(); }}><label>Format<Select defaultValue="" name="format" required><option value="">Choose…</option><option value="a">Alpha</option></Select></label><button type="submit">Save</button></form>);
    const trigger = screen.getByRole('combobox', { name: 'Format' });
    const native = container.querySelector<HTMLSelectElement>('.g-select-native')!;
    expect(native.hidden).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(submit).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);
    expect(trigger.getAttribute('aria-invalid')).toBe('true');
  });
});
