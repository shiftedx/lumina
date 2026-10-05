import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { SegmentedControl } from '.';

type Theme = 'system' | 'light' | 'dark';
function Harness() {
  const [value, setValue] = useState<Theme>('system');
  return <SegmentedControl legend="Theme" onChange={setValue} options={[{ value: 'system', label: 'System' }, { value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }]} value={value} />;
}

describe('SegmentedControl', () => {
  it('is a fieldset of native radios that the arrow keys move', async () => {
    render(<Harness />);
    expect(document.body.contains(screen.getByRole('group', { name: 'Theme' }))).toBe(true);
    const system = screen.getByRole('radio', { name: 'System' });
    expect((system as HTMLInputElement).checked).toBe(true);
    system.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect((screen.getByRole('radio', { name: 'Light' }) as HTMLInputElement).checked).toBe(true);
  });
});
