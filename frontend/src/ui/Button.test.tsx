import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Plus } from 'lucide-react';
import { describe, expect, it, vi } from 'vitest';
import { Button, ButtonLink, TextButton } from '.';

describe('Button', () => {
  it('defaults to a secondary type=button and maps variants to classes', () => {
    render(<><Button>Save</Button><Button variant="primary" wide>Go</Button><Button variant="danger">Delete</Button></>);
    expect(screen.getByRole('button', { name: 'Save' }).getAttribute('type')).toBe('button');
    expect(screen.getByRole('button', { name: 'Save' }).className).toContain('is-secondary');
    expect(screen.getByRole('button', { name: 'Go' }).className).toMatch(/is-primary.*is-wide/);
    expect(screen.getByRole('button', { name: 'Delete' }).className).toContain('is-danger');
  });

  it('Button busy keeps focus and blocks the click and the submit', async () => {
    const onClick = vi.fn(); const onSubmit = vi.fn((event: Event) => event.preventDefault());
    const { rerender } = render(<form onSubmit={(event) => onSubmit(event.nativeEvent)}><Button icon={<Plus />} onClick={onClick} type="submit">Save</Button></form>);
    const button = screen.getByRole('button', { name: 'Save' });
    button.focus();
    rerender(<form onSubmit={(event) => onSubmit(event.nativeEvent)}><Button busy icon={<Plus />} onClick={onClick} type="submit">Save</Button></form>);
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect((button as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(button.querySelector('.g-spinner')).not.toBeNull();
    expect(button.textContent).toContain('Save');
  });

  it('pressed sets aria-pressed', () => {
    render(<Button pressed>Follow</Button>);
    expect(screen.getByRole('button', { name: 'Follow' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('ButtonLink is a real link and TextButton a text-styled button', () => {
    render(<><ButtonLink href="/downloads">Open downloads</ButtonLink><TextButton onClick={() => undefined}>Undo</TextButton></>);
    expect(screen.getByRole('link', { name: 'Open downloads' }).getAttribute('href')).toBe('/downloads');
    expect(screen.getByRole('button', { name: 'Undo' }).className).toContain('g-text-button');
  });

  it('spreads data attributes for focusNav', () => {
    render(<Button data-focus-item="">Row</Button>);
    expect(screen.getByRole('button', { name: 'Row' }).getAttribute('data-focus-item')).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Row' }));
  });
});
