/**
 * Home's edit mode: every catalogue shelf as one row with a drag handle, a show/hide switch and Move up /
 * Move down. Every change is applied and saved at once through onChange; there is no Cancel. Keyboard
 * and TV remote pick a shelf up with Space or Enter on its handle; pointers drag the handle. Announcements go to one
 * polite live region, except "Home layout saved." and the Reset message, which HomeSurface says.
 *
 */
import { ChevronDown, ChevronUp, GripVertical } from 'lucide-react';
import { type KeyboardEvent, type PointerEvent, useEffect, useLayoutEffect, useRef, useState } from 'react';

import {
  cancelText, DEFAULT_HOME_ORDER, dropText, enterText, HOME_CATALOGUE, type HomeShelfId, type HomeShelfPref, moveShelf, pickUpText, positionText,
  setShelfVisible, visibilityText,
} from './homeShelves';
import './homeEditor.css';

export type HomeEditorProps = {
  /** The member's layout, normalised, in display order. */
  shelves: HomeShelfPref[];
  /** Shelves whose last load had nothing: their row says "Nothing to show right now". */
  empty: ReadonlySet<HomeShelfId>;
  /** Every change, applied and saved at once. null = Reset to default. */
  onChange: (next: HomeShelfPref[] | null) => void;
  /** Done, or Escape / remote Back while no shelf is picked up. */
  onDone: () => void;
};

/** Escape on keyboards; TV remotes send GoBack or BrowserBack, and Backspace is Back as elsewhere in the app. */
const BACK_KEYS = new Set(['Escape', 'GoBack', 'BrowserBack', 'Backspace']);
type Part = 'handle' | 'switch' | 'up' | 'down';
/** The row lifts after this much vertical movement; closer to the viewport edge than EDGE_PX scrolls, up to MAX_SCROLL_PX a frame. */
const DRAG_START_PX = 4;
const EDGE_PX = 48;
const MAX_SCROLL_PX = 12;
/** One pointer drag: `offset` is the pointer's distance below the list top at the press; `pitch` the measured row pitch. */
type Drag = { id: HomeShelfId; from: number; pointerId: number; offset: number; pitch: number; y: number; dy: number; lifted: boolean; target: number };

export function HomeEditor({ shelves, empty, onChange, onDone }: HomeEditorProps) {
  const [message, setMessage] = useState('');
  const [picked, setPicked] = useState<{ id: HomeShelfId; from: number } | null>(null);
  const list = useRef<HTMLOListElement>(null);
  /** The control to focus once a change has re-rendered the rows: a moved row's DOM node can lose focus. */
  const refocus = useRef<{ id: HomeShelfId; part: Part } | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const setDragState = (next: Drag | null) => { dragRef.current = next; setDrag(next); };
  const count = shelves.length;
  const name = (id: HomeShelfId) => HOME_CATALOGUE[id].name;
  const indexOf = (id: HomeShelfId) => shelves.findIndex((shelf) => shelf.id === id);
  const focusPart = (id: HomeShelfId, part: Part) => list.current?.querySelector<HTMLElement>(`[data-shelf-row="${id}"] [data-part="${part}"]`)?.focus();

  useEffect(() => {
    setMessage(enterText(count));
    if (shelves[0]) focusPart(shelves[0].id, 'handle');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- on entering edit mode only
  useLayoutEffect(() => {
    const target = refocus.current;
    refocus.current = null;
    if (target) focusPart(target.id, target.part);
  }, [shelves]);

  function apply(next: HomeShelfPref[], focus: { id: HomeShelfId; part: Part }) {
    refocus.current = focus;
    onChange(next);
  }
  function move(id: HomeShelfId, to: number, part: Part) {
    const next = moveShelf(shelves, id, to);
    if (next === shelves) return;
    apply(next, { id, part });
    setMessage(positionText(name(id), next.findIndex((shelf) => shelf.id === id) + 1, count));
  }
  function drop() {
    if (!picked) return;
    setMessage(dropText(name(picked.id), indexOf(picked.id) + 1, count));
    setPicked(null);
  }
  function cancel() {
    if (!picked) return;
    const next = moveShelf(shelves, picked.id, picked.from);
    if (next !== shelves) apply(next, { id: picked.id, part: 'handle' });
    setMessage(cancelText(name(picked.id), picked.from + 1));
    setPicked(null);
  }
  function toggle(id: HomeShelfId, visible: boolean) {
    apply(setShelfVisible(shelves, id, !visible), { id, part: 'switch' });
    setMessage(visibilityText(name(id), !visible));
  }
  function reset() {
    const first = DEFAULT_HOME_ORDER[0];
    setPicked(null);
    // The first row keeps its node when it is already the default's first, so focus it now; otherwise after the re-render.
    if (shelves[0]?.id === first) focusPart(first, 'handle');
    else refocus.current = { id: first, part: 'handle' };
    onChange(null);
  }

  function onPointerDown(event: PointerEvent<HTMLButtonElement>, id: HomeShelfId) {
    if (event.button !== 0 || picked || dragRef.current || !list.current) return;
    const rows = list.current.querySelectorAll<HTMLElement>('[data-shelf-row]');
    const first = rows[0]?.getBoundingClientRect();
    const pitch = rows.length > 1 ? rows[1].getBoundingClientRect().top - first.top : first?.height ?? 0;
    if (!pitch) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const from = indexOf(id);
    const top = list.current.getBoundingClientRect().top;
    setDragState({ id, from, pointerId: event.pointerId, offset: event.clientY - top, pitch, y: event.clientY, dy: 0, lifted: false, target: from });
  }
  /** Re-reads the list top each time: auto-scroll moves it under a still pointer. Target = clamp(round((y − top − pitch/2) / pitch)). */
  function track(y: number) {
    const current = dragRef.current;
    if (!current || !list.current) return;
    const top = list.current.getBoundingClientRect().top;
    const dy = y - top - current.offset;
    const lifted = current.lifted || Math.abs(dy) >= DRAG_START_PX;
    const target = Math.min(count - 1, Math.max(0, Math.round((y - top - current.pitch / 2) / current.pitch)));
    setDragState({ ...current, y, dy, lifted, target });
  }
  function onPointerUp(event: PointerEvent<HTMLButtonElement>) {
    const current = dragRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    setDragState(null);
    if (!current.lifted) return; // a tap on the handle is not a drag, and never picks up
    if (current.target !== current.from) apply(moveShelf(shelves, current.id, current.target), { id: current.id, part: 'handle' });
    setMessage(dropText(name(current.id), current.target + 1, count));
  }
  /** pointercancel, capture lost without a pointerup, or Escape: nothing was saved, so nothing to undo. */
  function cancelDrag() {
    const current = dragRef.current;
    if (!current) return;
    setDragState(null);
    if (current.lifted) setMessage(cancelText(name(current.id), current.from + 1));
  }
  /** How far row `index` is drawn from its slot while a drag is lifted. */
  function offsetOf(index: number): number {
    if (!drag?.lifted) return 0;
    if (index === drag.from) return drag.dy;
    if (drag.from < drag.target && index > drag.from && index <= drag.target) return -drag.pitch;
    if (drag.target < drag.from && index >= drag.target && index < drag.from) return drag.pitch;
    return 0;
  }

  useEffect(() => {
    if (!drag?.lifted) return undefined;
    let frame = requestAnimationFrame(function step() {
      const current = dragRef.current;
      if (!current) return;
      const edge = current.y < EDGE_PX ? current.y - EDGE_PX : current.y > window.innerHeight - EDGE_PX ? current.y - (window.innerHeight - EDGE_PX) : 0;
      if (edge) {
        // A two-argument scrollBy follows html { scroll-behavior: smooth } (lumina.css), restarting a smooth scroll
        // every frame and crawling at a fraction of speed (E-I2); 'instant' moves the full per-frame amount.
        window.scrollBy({ top: Math.max(-1, Math.min(1, edge / EDGE_PX)) * MAX_SCROLL_PX, behavior: 'instant' });
        track(current.y);
      }
      frame = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(frame);
  }, [drag?.lifted]); // eslint-disable-line react-hooks/exhaustive-deps -- one loop per lifted drag; it reads dragRef
  useEffect(() => {
    if (!drag) return undefined;
    // Capture phase on window: Escape during a drag cancels it and never reaches the editor's "leave edit mode".
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (!BACK_KEYS.has(event.key)) return;
      event.preventDefault();
      event.stopPropagation();
      cancelDrag();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [drag !== null]); // eslint-disable-line react-hooks/exhaustive-deps -- cancelDrag reads dragRef

  function onHandleKeyDown(event: KeyboardEvent<HTMLButtonElement>, id: HomeShelfId) {
    const { key } = event;
    if (picked?.id !== id) {
      // Idle: Space or Enter picks up (not click, so a pointer tap never does); arrows go on to focusNav.
      if (key !== ' ' && key !== 'Enter') return;
      event.preventDefault();
      const at = indexOf(id);
      setPicked({ id, from: at });
      setMessage(pickUpText(name(id), at + 1, count));
      return;
    }
    if (key === ' ' || key === 'Enter') {
      event.preventDefault();
      drop();
    } else if (key === 'ArrowUp' || key === 'ArrowDown') {
      event.preventDefault(); // focusNav skips a prevented event, so Up/Down move the shelf, not focus
      move(id, indexOf(id) + (key === 'ArrowUp' ? -1 : 1), 'handle');
    } else if (key === 'Home' || key === 'End') {
      event.preventDefault();
      move(id, key === 'Home' ? 0 : count - 1, 'handle');
    } else if (BACK_KEYS.has(key)) {
      event.preventDefault();
      event.stopPropagation(); // cancels the pick-up only; the next Back leaves edit mode
      cancel();
    } else if (key === 'Tab') {
      drop();
    }
  }
  function onEditorKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!BACK_KEYS.has(event.key) || picked) return;
    event.preventDefault();
    onDone();
  }

  return (
    <div className="h-editor" onKeyDown={onEditorKeyDown}>
      <h2 className="sr-only" id="h-edit-heading">Home shelves</h2>
      <p className="h-edit-hint" id="h-edit-hint">Drag, or use Move up and Move down, to reorder. Switch a shelf off to hide it.</p>
      <ol aria-labelledby="h-edit-heading" className="h-edit" ref={list}>
        {shelves.map(({ id, visible }, index) => {
          const label = name(id);
          const first = index === 0;
          const last = index === count - 1;
          return (
            <li
              className={`h-edit-row${visible ? '' : ' is-hidden'}`}
              data-dragging={drag?.lifted && drag.id === id ? '' : undefined}
              data-focus-row
              data-shelf-row={id}
              key={id}
              style={{ transform: offsetOf(index) ? `translateY(${offsetOf(index)}px)` : undefined }}
            >
              <button
                aria-describedby="h-edit-hint"
                aria-label={`Reorder ${label}`}
                aria-pressed={picked?.id === id}
                className="h-handle"
                data-focus-item
                data-part="handle"
                onBlur={() => { if (picked?.id === id && !refocus.current) drop(); }}
                onKeyDown={(event) => onHandleKeyDown(event, id)}
                onLostPointerCapture={cancelDrag}
                onPointerCancel={cancelDrag}
                onPointerDown={(event) => onPointerDown(event, id)}
                onPointerMove={(event) => { if (event.pointerId === dragRef.current?.pointerId) track(event.clientY); }}
                onPointerUp={onPointerUp}
                type="button"
              >
                <GripVertical aria-hidden="true" />
              </button>
              <span className="h-edit-name">{label}</span>
              <span className="h-edit-status g-label">{!visible ? 'Hidden' : empty.has(id) ? 'Nothing to show right now' : ''}</span>
              <button aria-checked={visible} aria-label={`Show ${label}`} className="h-switch" data-focus-item data-part="switch" onClick={() => toggle(id, visible)} role="switch" type="button">
                <span aria-hidden="true" className="h-switch-knob" />
              </button>
              <button aria-disabled={first} aria-label={`Move ${label} up`} className="h-move" data-focus-item data-part="up" onClick={() => { if (!first) move(id, index - 1, 'up'); }} type="button">
                <ChevronUp aria-hidden="true" />
              </button>
              <button aria-disabled={last} aria-label={`Move ${label} down`} className="h-move" data-focus-item data-part="down" onClick={() => { if (!last) move(id, index + 1, 'down'); }} type="button">
                <ChevronDown aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ol>
      <div className="h-edit-actions">
        <button className="g-button g-button-text" data-focus-item onClick={reset} type="button">Reset to default</button>
        <button className="g-button g-button-text is-primary" data-focus-item onClick={onDone} type="button">Done</button>
      </div>
      <p aria-live="polite" className="sr-only" role="status">{message}</p>
    </div>
  );
}
