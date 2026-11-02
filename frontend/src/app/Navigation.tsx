import { Ellipsis, Server, X } from '../luminaIcons';
import { memo, type ReactNode, useRef, useLayoutEffect, type RefObject } from 'react';

import type { ServerNavigation } from '../types';
import { isNavigationSurfaceAvailable, videoLibraryViews } from './useServerNavigation';
import { LuminaRouteIcon, type LuminaRouteIconName } from '../LuminaRouteIcons';
import { BrandMark } from '../ui';
import { moveFocus } from '../features/media/focusNav';
import { type Surface, type VideoLibraryView } from './routes';

type NavItem = { surface: Surface; label: string; icon: LuminaRouteIconName; view?: VideoLibraryView };
/** Navigation only, grouped. Video, Audio and Books each have their own destination. */
export const navGroups: ReadonlyArray<{ label: string; items: readonly NavItem[] }> = [
  { label: 'Watch', items: [
    { surface: 'home', label: 'Home', icon: 'home' },
    { surface: 'video', label: 'Movies', icon: 'movies', view: 'movies' },
    { surface: 'video', label: 'Shows', icon: 'shows', view: 'shows' },
    { surface: 'video', label: 'Anime', icon: 'anime', view: 'anime' },
    { surface: 'streaming', label: 'Streaming', icon: 'live' },
    { surface: 'video', label: 'Recordings', icon: 'live', view: 'recordings' },
    { surface: 'audio', label: 'Music', icon: 'music' },
    { surface: 'books', label: 'Books', icon: 'books' },
    { surface: 'photos', label: 'Photos', icon: 'photos' },
    { surface: 'requests', label: 'Requests', icon: 'requests' },
  ] },
  { label: 'Vault', items: [
    { surface: 'downloads', label: 'Downloads', icon: 'downloads' },
    { surface: 'settings', label: 'My Settings', icon: 'settings' },
  ] },
];

// Watch is its own route context; it never pretends to be Home.
export function surfaceIsActive(current: Surface, target: Surface): boolean {
  // A followed channel's page is still Streaming.
  return current === target || (target === 'video' && current === 'library') || (target === 'streaming' && current === 'subscriptions');
}

export const MobileTabs = memo(function MobileTabs({ surface, activeDownloads, moreOpen, onNavigate, onSearch, onMore, moreButtonRef, videoView, onVideoView, navigation }: {
  navigation?: ServerNavigation | null;
  moreButtonRef?: RefObject<HTMLButtonElement | null>;
  videoView?: string;
  onVideoView?: (view: VideoLibraryView) => void;
  surface: Surface; activeDownloads: number; moreOpen: boolean; onNavigate: (surface: Surface) => void; onSearch: () => void; onMore: () => void;
}) {
  const views = videoLibraryViews(navigation);
  const currentVideo = views.find((view) => view === videoView) ?? views[0];
  const videoItem = navGroups[0].items.find((item) => item.view === currentVideo);
  const tab = (target: Surface, label: string, icon: LuminaRouteIconName) => {
    const active = surfaceIsActive(surface, target);
    return <button aria-current={active ? 'page' : undefined} className={active ? 'is-active' : ''} key={target} onClick={() => target === 'video' && onVideoView && currentVideo ? onVideoView(currentVideo) : onNavigate(target)} type="button"><LuminaRouteIcon route={icon} selected={active} /><span>{label}</span></button>;
  };
  return (
    <nav aria-label="Mobile primary navigation" className="g-mobile-tabs">
      {tab('home', 'Home', 'home')}
      {surface === 'audio' && isNavigationSurfaceAvailable(navigation, 'audio') ? tab('audio', 'Music', 'music') : surface === 'books' && isNavigationSurfaceAvailable(navigation, 'books') ? tab('books', 'Books', 'books') : surface === 'photos' && isNavigationSurfaceAvailable(navigation, 'photos') ? tab('photos', 'Photos', 'photos') : videoItem ? tab('video', videoItem.label, videoItem.icon) : null}
      {isNavigationSurfaceAvailable(navigation, 'streaming') ? tab('streaming', 'Streaming', 'live') : null}
      {isNavigationSurfaceAvailable(navigation, 'requests') ? tab('requests', 'Requests', 'requests') : null}
      <button ref={moreButtonRef} onPointerDownCapture={(event) => { event.currentTarget.dataset.navigationKeyboard = 'false'; }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') event.currentTarget.dataset.navigationKeyboard = 'true'; }} aria-expanded={moreOpen} aria-haspopup="dialog" aria-label={activeDownloads ? `Browse, ${activeDownloads} active downloads` : 'Browse'} onClick={onMore} type="button">
        <Ellipsis aria-hidden="true" /><span>Browse</span>{activeDownloads ? <b aria-hidden="true" className="g-nav-count">{activeDownloads}</b> : null}
      </button>
    </nav>
  );
});

/** A compact top-layer menu stays beside its trigger without reserving page space. */
export const NavigationPopover = memo(function NavigationPopover({ surface, activeDownloads, pendingRequests = 0, open, onNavigate, onClose, extra, onManageServer, managingServer = false, anchorRef, onVideoView, videoView, navigation }: {
  navigation?: ServerNavigation | null;
  surface: Surface; activeDownloads: number; open: boolean; onNavigate: (surface: Surface) => void; onClose: () => void;
  anchorRef?: RefObject<HTMLButtonElement | null>;
  onVideoView?: (view: VideoLibraryView) => void;
  videoView?: string;
  pendingRequests?: number;
  onManageServer?: () => void;
  managingServer?: boolean;
  /** Account shortcuts shown with navigation on phones. */
  extra?: ReactNode;
}) {
  const firstNavigationRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const keyboard = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearDismissal = () => { if (timer.current) clearTimeout(timer.current); };
  const leave = (pointerType: string) => {
    if (pointerType !== 'mouse') return;
    clearDismissal();
    timer.current = setTimeout(() => { if (!(keyboard.current && panelRef.current?.contains(document.activeElement))) onClose(); }, 700);
  };
  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!open || !panel) return;
    const anchor = anchorRef?.current ?? document.querySelector<HTMLButtonElement>('[aria-controls="primary-navigation"]');
    panel.showPopover?.();
    keyboard.current = anchor?.dataset.navigationKeyboard === 'true';
    if (keyboard.current) firstNavigationRef.current?.focus({ preventScroll: true });
    const dismiss = () => { panel.hidePopover?.(); onClose(); anchor?.focus({ preventScroll: true }); };
    const key = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Tab' || event.key.startsWith('Arrow')) keyboard.current = true;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismiss(); }
      if (event.key === 'Tab') {
        const controls = [...panel.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, select, summary, [tabindex="0"]')];
        const boundary = event.shiftKey ? controls[0] : controls.at(-1);
        if (document.activeElement === boundary) dismiss();
      }
    };
    const outside = (event: Event) => {
      const target = event.target as Node | null;
      if (target && !panel.contains(target) && !anchor?.contains(target)) onClose();
    };
    document.addEventListener('keydown', key, true);
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('focusin', outside, true);
    return () => {
      clearDismissal();
      document.removeEventListener('keydown', key, true);
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('focusin', outside, true);
    };
  }, [open, anchorRef, onClose]);
  const choose = (action: () => void) => { panelRef.current?.hidePopover?.(); onClose(); action(); };
  if (!open) return null;
  return (
    <div aria-label="Navigation" className="g-popover g-navigation-popover" onPointerEnter={clearDismissal} onPointerLeave={(event) => leave(event.pointerType)} onPointerDown={() => { keyboard.current = false; }} popover="manual" ref={panelRef} role="dialog">
      <div className="g-navigation-head"><BrandMark size="bar" /><button aria-label="Close menu" className="g-icon-button g-navigation-close" onClick={() => { panelRef.current?.hidePopover?.(); onClose(); anchorRef?.current?.focus({ preventScroll: true }); }} type="button"><X aria-hidden="true" /></button></div>
      <nav aria-label="Primary" className="g-navigation-grid" id="primary-navigation" onKeyDown={(event) => moveFocus(event, { targets: '.g-nav-item' })}>
        {navGroups.map(({ label: group, items }) => (
          <div className="g-nav-group" key={group}>
            <h3 className="g-nav-group-label">{group}</h3>
            {items.filter(({ surface: target, view }) => isNavigationSurfaceAvailable(navigation, target) && (!view || videoLibraryViews(navigation).includes(view))).map(({ surface: target, label, icon, view }) => {
              const active = surfaceIsActive(surface, target) && (target !== 'video' || (view ? videoView === view : !videoView || videoView === 'all')) && !(target === 'settings' && managingServer);
              const count = target === 'downloads' ? activeDownloads : target === 'requests' ? pendingRequests : 0;
              const countLabel = target === 'requests' ? 'waiting' : 'active';
              return <button aria-current={active ? 'page' : undefined} aria-label={count ? `${label}, ${count} ${countLabel}` : label} className={`g-nav-item ${active ? 'is-active' : ''}`} key={view ?? target} onClick={() => choose(() => view && onVideoView ? onVideoView(view) : onNavigate(target))} ref={target === 'home' ? firstNavigationRef : undefined} type="button">
                <LuminaRouteIcon route={icon} selected={active} /><span>{label}</span>{count ? <b aria-hidden="true" className="g-nav-count">{count}</b> : null}
              </button>;
            })}
            {group === 'Vault' && onManageServer ? <button aria-current={managingServer ? 'page' : undefined} className={`g-nav-item ${managingServer ? 'is-active' : ''}`} onClick={() => choose(onManageServer)} type="button"><Server aria-hidden="true" /><span>Manage Server</span></button> : null}
          </div>
        ))}
      </nav>
      {extra}
    </div>
  );
});
