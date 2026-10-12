import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ChipInput } from './ChipInput';

function Harness({ start = ['Drama'], onChange = () => undefined, ...rest }: { start?: string[]; onChange?: (v: string[]) => void; maxItems?: number; suggestions?: string[] }) {
  const [values, setValues] = useState(start);
  return <ChipInput id="g" label="Genres" onChange={(next) => { setValues(next); onChange(next); }} values={values} {...rest} />;
}
const box = () => screen.getByRole('combobox') as HTMLInputElement;

describe('ChipInput', () => {
  it('adds a trimmed chip on Enter and on comma, without duplicates', async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.type(box(), '  Sci-Fi {Enter}');
    await userEvent.type(box(), 'drama,');
    await userEvent.type(box(), 'Mystery,');
    expect(onChange).toHaveBeenLastCalledWith(['Drama', 'Sci-Fi', 'Mystery']);
  });
  it('truncates to 60 characters and respects the cap', async () => {
    const onChange = vi.fn();
    render(<Harness maxItems={2} onChange={onChange} />);
    await userEvent.type(box(), `${'x'.repeat(70)}{Enter}y{Enter}`);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect((onChange.mock.calls[0][0] as string[])[1].length).toBe(60);
  });
  it('Backspace in an empty input removes the last chip; every chip has a remove button', async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} start={['A', 'B']} />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove A' }));
    expect(onChange).toHaveBeenLastCalledWith(['B']);
    await userEvent.type(box(), '{Backspace}');
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
  it('renders suggestions in a datalist and is a labelled group', () => {
    const { container } = render(<Harness suggestions={['Comedy', 'Horror']} />);
    expect(screen.getByRole('group', { name: 'Genres' })).toBeTruthy();
    expect(container.querySelectorAll('datalist option').length).toBe(2);
  });
});
