/** Cross-surface commands (app polish. Callers never import the palette or the picker. */
export const PALETTE_EVENT = 'lumina:palette';
export const MEMBER_PICKER_EVENT = 'lumina:member-picker';
export const EDIT_HOME_EVENT = 'lumina:edit-home';

export type PaletteMode = 'search' | 'link';
export interface PaletteRequest { mode: PaletteMode; query?: string }

export function openPalette(request: PaletteRequest = { mode: 'search' }): void {
  window.dispatchEvent(new CustomEvent<PaletteRequest>(PALETTE_EVENT, { detail: request }));
}
export function openMemberPicker(): void {
  window.dispatchEvent(new CustomEvent(MEMBER_PICKER_EVENT));
}
export function requestHomeEdit(): void {
  window.dispatchEvent(new CustomEvent(EDIT_HOME_EVENT));
}
export function onCommand<T = undefined>(name: string, handler: (detail: T) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<T>).detail);
  window.addEventListener(name, listener);
  return () => window.removeEventListener(name, listener);
}
