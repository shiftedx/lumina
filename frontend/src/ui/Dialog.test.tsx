import { readFileSync } from 'node:fs';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode, useRef, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialog, Dialog } from '.';

function Harness({ busy = false, dismissible = true, withField = false, withInitial = false }: { busy?: boolean; dismissible?: boolean; withField?: boolean; withInitial?: boolean }) {
  const [open, setOpen] = useState(false);
  const save = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button onClick={() => setOpen(true)} type="button">Edit rules</button>
      <Dialog busy={busy} dismissible={dismissible} footer={<button ref={save} type="button">Save</button>} initialFocus={withInitial ? save : undefined} onClose={() => setOpen(false)} open={open} title="Edit rules">
        {withField ? <label>Name<input /></label> : <p>Body</p>}
      </Dialog>
    </>
  );
}
const cancel = (dialog: HTMLElement) => act(() => { dialog.dispatchEvent(new Event('cancel', { cancelable: true })); });

describe('Dialog', () => {
  it('opens as a labelled modal', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'Edit rules' }));
    const dialog = screen.getByRole('dialog', { name: 'Edit rules' });
    expect(dialog.hasAttribute('open')).toBe(true);
    expect(dialog.dataset.modal).toBe('true');
  });

  it('returns focus to the opener on Esc, backdrop and close', async () => {
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Edit rules' });
    await userEvent.click(opener);
    cancel(screen.getByRole('dialog'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
    await userEvent.click(opener);
    fireEvent.click(screen.getByRole('dialog'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
    await userEvent.click(opener);
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(document.activeElement).toBe(opener);
  });

  it('ignores Esc and the backdrop while busy', async () => {
    render(<Harness busy />);
    await userEvent.click(screen.getByRole('button', { name: 'Edit rules' }));
    cancel(screen.getByRole('dialog'));
    fireEvent.click(screen.getByRole('dialog'));
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('has no Close and ignores Esc when not dismissible', async () => {
    render(<Harness dismissible={false} />);
    await userEvent.click(screen.getByRole('button', { name: 'Edit rules' }));
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    cancel(screen.getByRole('dialog'));
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('focuses initialFocus, else the first field, else Close', async () => {
    const { unmount } = render(<Harness withInitial />);
    await userEvent.click(screen.getByRole('button', { name: 'Edit rules' }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Save' }));
    unmount();
    const second = render(<Harness withField />);
    await userEvent.click(screen.getByRole('button', { name: 'Edit rules' }));
    expect(document.activeElement).toBe(screen.getByLabelText('Name'));
    second.unmount();
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'Edit rules' }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }));
  });

  it('keeps the page from jumping while open', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'Edit rules' }));
    expect(document.documentElement.classList.contains('g-modal-open')).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(document.documentElement.classList.contains('g-modal-open')).toBe(false);
  });

  it('keeps g-modal-open until the last of two stacked dialogs closes', async () => {
    render(<><Harness /><Dialog onClose={() => undefined} open title="Underneath"><p>Below</p></Dialog></>);
    await userEvent.click(screen.getByRole('button', { name: 'Edit rules', hidden: true }));
    expect(screen.getAllByRole('dialog')).toHaveLength(2);
    await userEvent.click(screen.getAllByRole('button', { name: 'Close', hidden: true })[0]);
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(document.documentElement.classList.contains('g-modal-open')).toBe(true); // the other dialog is still modal
  });

  it('does not report a close for StrictMode re-running its effects', () => {
    const onClose = vi.fn();
    render(<StrictMode><Dialog onClose={onClose} open title="Edit rules"><p>Body</p></Dialog></StrictMode>);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog').hasAttribute('open')).toBe(true);
  });

  it('ignores a close event that arrives after StrictMode re-opened the dialog (browsers queue it)', () => {
    const onClose = vi.fn();
    render(<StrictMode><Dialog onClose={onClose} open title="Edit rules"><p>Body</p></Dialog></StrictMode>);
    act(() => { screen.getByRole('dialog').dispatchEvent(new Event('close')); });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('reports a native close it did not ask for (Chrome force-closes a repeated Esc)', () => {
    const onClose = vi.fn();
    render(<Dialog busy onClose={onClose} open title="Edit rules"><p>Body</p></Dialog>);
    act(() => { (screen.getByRole('dialog') as HTMLDialogElement).close(); });
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe('ConfirmDialog', () => {
  it('focuses Cancel when danger, and confirms or cancels', async () => {
    const onConfirm = vi.fn(); const onCancel = vi.fn();
    render(<ConfirmDialog body="It stays restorable for a while." confirmLabel="Delete" danger onCancel={onCancel} onConfirm={onConfirm} open title="Delete “Family videos”?" />);
    expect(screen.getByRole('dialog', { name: 'Delete “Family videos”?' })).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onConfirm).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});

it('the dialog paper is an opaque token, never translucent', () => {
  const rule = /\.g-dialog \{([^}]*)\}/.exec(readFileSync('src/ui/ui.css', 'utf8'))?.[1] ?? '';
  expect(rule).toMatch(/background: var\(--g-paper-3\)/);
  expect(rule).not.toMatch(/rgba|color-mix|transparent|opacity/);
});
