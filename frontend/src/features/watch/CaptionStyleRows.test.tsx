import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CaptionStyleRows } from './CaptionStyleRows';

describe('CaptionStyleRows', () => {
  it('previews the choice on an always-dark block and reports changes', async () => {
    const onChange = vi.fn();
    render(<CaptionStyleRows onChange={onChange} value={{ size: 'medium', background: 'shadow' }} />);
    const preview = screen.getByText('The quick fox waits for the ferry.').closest<HTMLElement>('.caption-preview');
    expect(preview).not.toBeNull();
    expect(preview!.style.getPropertyValue('--caption-scale')).toBe('1');
    await userEvent.click(screen.getByRole('radio', { name: 'Extra large' }));
    expect(onChange).toHaveBeenCalledWith({ size: 'xlarge', background: 'shadow' });
    await userEvent.click(screen.getByRole('radio', { name: 'Solid' }));
    expect(onChange).toHaveBeenLastCalledWith({ size: 'medium', background: 'solid' });
  });
});
