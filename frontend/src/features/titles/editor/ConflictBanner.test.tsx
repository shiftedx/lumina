import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ConflictBanner } from './ConflictBanner';

describe('ConflictBanner', () => {
  it('names the fields and offers both ways out', () => {
    const onTheirs = vi.fn();
    const onMine = vi.fn();
    render(<ConflictBanner fields={['overview', 'name']} onMine={onMine} onTheirs={onTheirs} />);
    expect(screen.getByRole('alert').textContent).toContain('Someone changed Overview and Name while you were editing.');
    fireEvent.click(screen.getByRole('button', { name: 'Load their version' }));
    fireEvent.click(screen.getByRole('button', { name: 'Keep mine and save' }));
    expect(onTheirs).toHaveBeenCalledOnce();
    expect(onMine).toHaveBeenCalledOnce();
  });
  it('lists three or more with commas', () => {
    render(<ConflictBanner fields={['overview', 'name', 'genres']} onMine={() => undefined} onTheirs={() => undefined} />);
    expect(screen.getByRole('alert').textContent).toContain('Overview, Name and Genres');
  });
});
