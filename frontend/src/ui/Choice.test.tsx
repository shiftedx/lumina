import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { Checkbox, Radio, Switch } from '.';

/** The accessible description: aria-describedby's targets' text, joined (no jest-dom in this repo). */
const description = (element: Element) => (element.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean).map((id) => document.getElementById(id)?.textContent?.trim() ?? '').join(' ');

describe('Checkbox and Radio', () => {
  it('make the whole row the label and support indeterminate', () => {
    render(<><Checkbox hint="42 videos" indeterminate label="Select all" /><Radio label="Any rule" name="m" /></>);
    const all = screen.getByRole('checkbox', { name: 'Select all' });
    expect((all as HTMLInputElement).indeterminate).toBe(true);
    expect(description(all)).toBe('42 videos');
    expect(screen.getByRole('radio', { name: 'Any rule' }).closest('label')).not.toBeNull();
  });
});

describe('Switch', () => {
  function Harness({ busy = false }: { busy?: boolean }) {
    const [on, setOn] = useState(false);
    return <Switch busy={busy} checked={on} hint="Skip intros automatically." label="Skip intros" onChange={setOn} />;
  }
  it('is a native checkbox with role switch and says On when on', async () => {
    render(<Harness />);
    const control = screen.getByRole('switch', { name: 'Skip intros' });
    expect(control.getAttribute('type')).toBe('checkbox');
    expect((control as HTMLInputElement).checked).toBe(false);
    await userEvent.click(control);
    expect((control as HTMLInputElement).checked).toBe(true);
    expect(description(control)).toBe('Skip intros automatically. On');
  });
  it('ignores changes while busy', async () => {
    render(<Harness busy />);
    await userEvent.click(screen.getByRole('switch', { name: 'Skip intros' }));
    expect((screen.getByRole('switch', { name: 'Skip intros' }) as HTMLInputElement).checked).toBe(false);
  });
});
