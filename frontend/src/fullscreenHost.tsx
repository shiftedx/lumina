/**
 * Fullscreen that survives the next episode: the Watch player asks for fullscreen on this host, which stays mounted while
 * the keyed Watch surface inside it is replaced item by item, so autoplay never leaves fullscreen (a browser cannot
 * re-enter it without a gesture). Safari's prefixed API is used where the standard one is missing; an iPhone (video-only
 * fullscreen) keeps the player's own fallback and plays the next episode inline.
 *
 * Picture-in-picture belongs to one media element, so the player hands its element here when it unmounts in
 * picture-in-picture and the next player takes it back with the next item's source: the floating window stays open
 * across autoplay. Parked (still connected, or the browser closes the window) only between the two players.
 */
import { createContext, type ReactNode, type RefObject, useEffect, useRef, useState } from 'react';

type WebkitDocument = Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => Promise<void> | void };
type WebkitElement = HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void };
const CHANGE_EVENTS = ['fullscreenchange', 'webkitfullscreenchange'] as const;

export const FullscreenHostContext = createContext<RefObject<HTMLElement | null> | null>(null);

export const fullscreenElement = (): Element | null => document.fullscreenElement ?? (document as WebkitDocument).webkitFullscreenElement ?? null;

/** False when this element cannot go fullscreen at all (an iPhone). */
export async function requestFullscreenOn(element: HTMLElement): Promise<boolean> {
  const target = element as WebkitElement;
  if (target.requestFullscreen) await target.requestFullscreen();
  else if (target.webkitRequestFullscreen) await target.webkitRequestFullscreen();
  else return false;
  return true;
}

export async function exitFullscreen(): Promise<void> {
  const doc = document as WebkitDocument;
  if (doc.exitFullscreen) await doc.exitFullscreen();
  else await doc.webkitExitFullscreen?.();
}

export function onFullscreenChange(listener: () => void): () => void {
  CHANGE_EVENTS.forEach((name) => document.addEventListener(name, listener));
  return () => CHANGE_EVENTS.forEach((name) => document.removeEventListener(name, listener));
}

type PresentationVideo = HTMLVideoElement & { webkitPresentationMode?: string };
type MovingParent = Element & { moveBefore?: (node: Node, child: Node | null) => void };
let parked: HTMLMediaElement | null = null;

/** Standard picture-in-picture, or Safari's webkit presentation mode. */
export function inPictureInPicture(media: Element | null): boolean {
  return Boolean(media) && (document.pictureInPictureElement === media || (media as PresentationVideo).webkitPresentationMode === 'picture-in-picture');
}

/** Moves without disconnecting where the browser can (moveBefore), so picture-in-picture survives the move. */
export function placeMedia(parent: Element, media: HTMLMediaElement, before: Node | null) {
  const { moveBefore } = parent as MovingParent;
  if (moveBefore && media.isConnected && parent.isConnected) {
    try { moveBefore.call(parent, media, before); return; } catch { /* a cross-document move: insert instead */ }
  }
  parent.insertBefore(media, before);
}

/** The element in picture-in-picture from the previous item, else a new one. */
export function takeMediaElement(kind: 'video' | 'audio'): HTMLMediaElement {
  const keep = parked && kind === 'video' && inPictureInPicture(parked) ? parked : null;
  if (!keep) dropParkedMedia();
  parked = null;
  keep?.removeAttribute('style');
  return keep ?? document.createElement(kind);
}

/** A player letting go of its element parks it while it is in picture-in-picture (true); otherwise the player drops it. */
export function releaseMediaElement(media: HTMLMediaElement): boolean {
  if (!inPictureInPicture(media)) return false;
  dropParkedMedia();
  media.pause(); // the next item's source autoplays; a manual switch must not keep the old one going meanwhile
  media.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none';
  placeMedia(document.body, media, null);
  parked = media;
  return true;
}

/** Nothing took the parked element (Watch closed): removing it closes the window. */
export function dropParkedMedia() {
  const media = parked;
  parked = null;
  if (!media) return;
  media.remove();
  media.removeAttribute('src');
  media.load();
}

/** `active` false (left Watch, or watching stopped by access) hands the screen back so the page behind is visible. */
export function WatchFullscreenHost({ active, children }: { active: boolean; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => onFullscreenChange(() => setFullscreen(fullscreenElement() === ref.current)), []);
  useEffect(() => {
    if (active) return;
    dropParkedMedia();
    if (fullscreenElement() === ref.current) void exitFullscreen().catch(() => undefined);
  }, [active]);
  return (
    <FullscreenHostContext.Provider value={ref}>
      <div className="watch-host" data-fullscreen={fullscreen || undefined} ref={ref}>{children}</div>
    </FullscreenHostContext.Provider>
  );
}
