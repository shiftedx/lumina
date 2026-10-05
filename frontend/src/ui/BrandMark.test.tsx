import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BrandMark } from '.';

describe('BrandMark', () => {
  it('is decorative art named by its visible word', () => {
    const { container } = render(<BrandMark size="bar" />);
    expect(screen.getByText('Lumina')).not.toBeNull();
    expect(container.querySelector('svg')).toBeNull();
    const sun = container.querySelector('.g-brand-sun');
    expect(sun?.getAttribute('aria-hidden')).toBe('true');
    expect(sun?.querySelectorAll('i')).toHaveLength(5);
  });
  it('keeps the word for assistive tech when compact', () => {
    render(<BrandMark compact size="bar" />);
    expect(screen.getByText('Lumina').className).toContain('sr-only');
  });
});
