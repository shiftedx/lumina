import { useEffect, useSyncExternalStore } from 'react';

/** Section forms with unsaved edits, by token → section label. */
const dirtyForms = new Map<symbol, string>();
const listeners = new Set<() => void>();
let dirtyLabels: readonly string[] = [];
let epoch = 0; // bumps when a confirmed leave clears the registry, so still-mounted forms re-register
function publish() {
  dirtyLabels = [...new Set(dirtyForms.values())];
  listeners.forEach((listener) => listener());
}

/** Labels of sections with unsaved edits, live; search keeps those sections pinned in its results. */
export function useDirtyLabels(): readonly string[] {
  return useSyncExternalStore((listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; }, () => dirtyLabels);
}

/** Registers a Save form's unsaved edits: in-app navigation asks first (confirmLeaveSettings) and a page unload warns. */
export function useUnsavedChanges(dirty: boolean, label: string) {
  const cleared = useSyncExternalStore((listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; }, () => epoch);
  useEffect(() => {
    if (!dirty) return undefined;
    const token = Symbol(label);
    dirtyForms.set(token, label);
    publish();
    const onBeforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => { dirtyForms.delete(token); publish(); window.removeEventListener('beforeunload', onBeforeUnload); };
  }, [dirty, label, cleared]);
}

/** True when nothing is unsaved or the person agrees to discard it; call before leaving a section. */
export function confirmLeaveSettings(): boolean {
  if (!dirtyForms.size) return true;
  const labels = [...new Set(dirtyForms.values())].join(', ');
  if (!window.confirm(`You have unsaved changes in ${labels}. Leave without saving?`)) return false;
  dirtyForms.clear();
  epoch += 1;
  publish();
  return true;
}
