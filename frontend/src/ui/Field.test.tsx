import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
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
  it('is a native select', () => {
    render(<label>Format<Select defaultValue="b"><option value="a">A</option><option value="b">B</option></Select></label>);
    expect((screen.getByRole('combobox', { name: 'Format' }) as HTMLInputElement).value).toBe('b');
  });
});
