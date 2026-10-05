/**
 * Follow settings: the existing SubscriptionControls (pause, auto-save, unfollow with its
 * confirmation) in the foundation's drawer: a modal <dialog> from the right on desktop, a bottom sheet on phone. Esc or
 * Done closes it; focus returns to whatever opened it.
 */
import { useEffect, useRef } from 'react';

import type { ChannelSubscriptionCommands } from '../../channelSubscriptions';
import { SubscriptionControls } from '../../SubscriptionControls';
import type { SourceAutomation } from '../../types';

export type FollowSettingsDialogProps = {
  automation: SourceAutomation;
  commands: ChannelSubscriptionCommands;
  onChange: (automation: SourceAutomation) => void;
  onRecover: (automation: SourceAutomation) => Promise<void>;
  onRemoved: (automationId: string) => void;
  onClose: () => void;
};

export function FollowSettingsDialog({ automation, commands, onChange, onRecover, onRemoved, onClose }: FollowSettingsDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    // Once: StrictMode re-runs this after the first cleanup, when the modal has already taken focus.
    opener.current ??= document.activeElement as HTMLElement | null;
    const element = dialog.current;
    if (element && !element.open) element.showModal?.();
    return () => opener.current?.focus?.();
  }, []);
  return (
    <dialog aria-labelledby="g-follow-settings-title" className="g-drawer g-follow-drawer" onCancel={(event) => { event.preventDefault(); onClose(); }} ref={dialog}>
      <div className="g-drawer-body">
        <span aria-hidden="true" className="g-sheet-handle" />
        <h2 className="g-drawer-title" id="g-follow-settings-title">Follow settings</h2>
        <SubscriptionControls automation={automation} commands={commands} onChange={onChange} onRecover={onRecover} onRemoved={(id) => { onClose(); onRemoved(id); }} />
      </div>
      <div className="g-drawer-footer"><button className="g-button" onClick={onClose} type="button">Done</button></div>
    </dialog>
  );
}
