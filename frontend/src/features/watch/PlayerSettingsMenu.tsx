import { createContext, type ReactNode, useContext, useId, useRef, useState } from 'react';
import { Settings2 } from '../../luminaIcons';

export const PlayerCastingContext = createContext<ReactNode>(null);

export function PlayerSettingsMenu({ children, onClose }: { children?: ReactNode; onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  const casting = useContext(PlayerCastingContext);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  function close() { setOpen(false); onClose?.(); }
  return <div className="playback-menu" data-has-options={Boolean(children)}
    onBlur={(event) => { if (open && event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) close(); }}
    onKeyDown={(event) => { if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); close(); trigger.current?.focus(); } }}>
    <button aria-controls={open ? id : undefined} aria-expanded={open} aria-label="Playback settings" onClick={() => { if (open) close(); else setOpen(true); }} ref={trigger} type="button"><Settings2 aria-hidden="true" /></button>
    <div aria-label="Playback settings" className="playback-menu-panel player-panel" hidden={!open} id={id} role="group">
      {/* Keep output owners mounted while the menu is closed, so closing it never stops a cast. */}
      <div className="player-casting-options">{casting}</div>
      {open ? children : null}
    </div>
  </div>;
}
