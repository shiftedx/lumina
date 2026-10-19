import { fireEvent, screen, within } from '@testing-library/react';

/** Choose a visible option through the same branded control members use. */
export function chooseSelect(control: HTMLElement, optionName: string | RegExp): void {
  fireEvent.click(control);
  fireEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: optionName }));
}

/** Inspect the offered choices without depending on a hidden form bridge. */
export function selectLabels(control: HTMLElement): string[] {
  fireEvent.click(control);
  const list = screen.getByRole('listbox');
  const labels = within(list).getAllByRole('option').map((option) => option.textContent?.trim() ?? '');
  fireEvent.keyDown(list, { key: 'Escape' });
  return labels;
}
