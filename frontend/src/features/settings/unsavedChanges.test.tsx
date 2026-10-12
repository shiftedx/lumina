import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { confirmLeaveSettings, useUnsavedChanges } from './unsavedChanges';

function Form() { useUnsavedChanges(true, 'this title'); return null; }

afterEach(() => vi.restoreAllMocks());

it('a still-mounted dirty form keeps guarding after a confirmed leave cleared the registry', () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const view = render(<Form />);
  act(() => { expect(confirmLeaveSettings()).toBe(true); });
  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  const confirm = vi.mocked(window.confirm); confirm.mockClear();
  act(() => { confirmLeaveSettings(); });
  expect(confirm).toHaveBeenCalledTimes(1);
  view.unmount();
});
