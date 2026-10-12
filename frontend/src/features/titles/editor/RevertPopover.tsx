import { type ReactElement } from 'react';

import type { KeptValue } from '../../../types';
import { Button, Popover } from '../../../ui';
import type { MenuTriggerProps } from '../../../ui';
import { revertCopy } from './editorModel';

/** "Unlock" on a saved edit: say what comes back, then Revert or Keep my edit. Focus returns to the lock button on close. */
export function RevertPopover({ label, kept, onRevert, trigger }: {
  label: string;
  kept: KeptValue | null;
  onRevert: () => void;
  trigger: (props: Omit<MenuTriggerProps, 'aria-haspopup'> & { 'aria-haspopup': 'dialog' }) => ReactElement;
}) {
  const copy = revertCopy(kept);
  // Popover closes itself on Escape (and returns focus to the trigger); that is the one close path it offers.
  const close = () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', cancelable: true }));
  return (
    <Popover label={`Revert ${label}`} trigger={trigger}>
      <div className="ed-revert">
        <p>{copy.question}</p>
        {copy.quote ? <blockquote>{copy.quote}</blockquote> : null}
        <div className="ed-revert-actions">
          <Button onClick={() => { close(); onRevert(); }} variant="primary">Revert</Button>
          <Button onClick={close} variant="quiet">Keep my edit</Button>
        </div>
      </div>
    </Popover>
  );
}
