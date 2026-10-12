import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AppBoundary, SurfaceBoundary } from './SurfaceBoundary';

function Broken(): never {
  throw new Error('bad payload');
}

describe('SurfaceBoundary', () => {
  it('contains a render crash to the surface', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(<><p>shell</p><SurfaceBoundary><Broken /></SurfaceBoundary></>);
    expect(screen.getByText('shell')).not.toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('This part of Lumina stopped working.');
  });

  it('replaces a crash of the whole app with a recoverable screen instead of a blank page', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const reload = vi.fn();
    render(<AppBoundary onReload={reload}><Broken /></AppBoundary>);
    expect(screen.getByRole('alert').textContent).toContain('Lumina hit a problem');
    fireEvent.click(screen.getByRole('button', { name: 'Reload Lumina' }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
