import { render, screen } from '@testing-library/react';
import { X } from 'lucide-react';
import { describe, expect, it } from 'vitest';
import { IconButton } from '.';

describe('IconButton', () => {
  it('requires a label, which names it and titles it; the icon is hidden', () => {
    render(<IconButton icon={<X />} label="Close" />);
    const button = screen.getByRole('button', { name: 'Close' });
    expect(button.getAttribute('title')).toBe('Close');
    expect(button.querySelector('svg')?.closest('[aria-hidden="true"]')).not.toBeNull();
    // @ts-expect-error label is required
    const missing = <IconButton icon={<X />} />;
    expect(missing).toBeTruthy();
  });

  it('maps the variant and pressed state', () => {
    render(<IconButton icon={<X />} label="Mute" pressed variant="overlay" />);
    const button = screen.getByRole('button', { name: 'Mute' });
    expect(button.className).toContain('is-overlay');
    expect(button.getAttribute('aria-pressed')).toBe('true');
  });
});
