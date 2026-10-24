import { Ellipsis, Search, Server } from 'lucide-react';
import { memo, type ReactNode, useRef, useLayoutEffect, type RefObject } from 'react';

import { allStreamingBlocked, useAccess } from '../features/access/access';
import { LuminaRouteIcon, type LuminaRouteIconName } from '../LuminaRouteIcons';
import { place } from '../ui/placement';
import { moveFocus } from '../features/media/focusNav';
import { type Surface } from './routes';

type NavItem = { surface: Surface; label: string; icon: LuminaRouteIconName };
/** Navigation only, grouped. Video, Audio and Books each have their own destination. */
export const navGroups: ReadonlyArray<{ label: string; items: readonly NavItem[] }> = [
  { label: 'Watch', items: [
    { surface: 'home', label: 'Home', icon: 'home' },
    { surface: 'video', label: 'Video', icon: 'library' },
    { surface: 'audio', label: 'Audio', icon: 'music' },
    { surface: 'books', label: 'Books', icon: 'books' },
    { surface: 'photos', label: 'Photos', icon: 'photos' },
    { surface: 'streaming', label: 'Streaming', icon: 'live' },
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

export const MobileTabs = memo(function MobileTabs({ surface, activeDownloads, moreOpen, onNavigate, onSearch, onMore, moreButtonRef }: {
  moreButtonRef?: RefObject<HTMLButtonElement | null>;
  surface: Surface; activeDownloads: number; moreOpen: boolean; onNavigate: (surface: Surface) => void; onSearch: () => void; onMore: () => void;
}) {
  const noStreaming = allStreamingBlocked(useAccess().access);
  const tab = (target: Surface, label: string, icon: LuminaRouteIconName) => {
    const active = surfaceIsActive(surface, target);
    return <button aria-current={active ? 'page' : undefined} className={active ? 'is-active' : ''} key={target} onClick={() => onNavigate(target)} type="button"><LuminaRouteIcon route={icon} /><span>{label}</span></button>;
  };
  return (
    <nav aria-label="Mobile primary navigation" className="g-mobile-tabs">
      {tab('home', 'Home', 'home')}
      {tab('video', 'Video', 'library')}
      {tab('audio', 'Audio', 'music')}
      {tab('books', 'Books', 'books')}
      <button ref={moreButtonRef} aria-expanded={moreOpen} aria-haspopup="dialog" aria-label={activeDownloads ? `More, ${activeDownloads} active downloads` : 'More'} onClick={onMore} type="button">
        <Ellipsis aria-hidden="true" /><span>More</span>{activeDownloads ? <b aria-hidden="true" className="g-nav-count">{activeDownloads}</b> : null}
      </button>
    </nav>
  );
});

/** A compact top-layer menu stays beside its trigger without reserving page space. */
export const NavigationPopover = memo(function NavigationPopover({ surface, activeDownloads, pendingRequests = 0, open, onNavigate, onClose, extra, onManageServer, managingServer = false, anchorRef }: {
  surface: Surface; activeDownloads: number; open: boolean; onNavigate: (surface: Surface) => void; onClose: () => void;
  anchorRef?: RefObject<HTMLButtonElement | null>;
  pendingRequests?: number;
  onManageServer?: () => void;
  managingServer?: boolean;
  /** Account shortcuts shown with navigation on phones. */
  extra?: ReactNode;
}) {
  const firstNavigationRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const noStreaming = allStreamingBlocked(useAccess().access);
  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!open || !panel) return;
    const anchor = anchorRef?.current ?? document.querySelector<HTMLButtonElement>('[aria-controls="primary-navigation"]');
    panel.showPopover?.();
    const follow = () => {
      if (!anchor) return;
      const bounds = anchor.getBoundingClientRect();
      panel.style.maxHeight = `${Math.max(0, Math.max(window.innerHeight - bounds.bottom - 12, bounds.top - 12))}px`;
      place(panel, anchor, 'start');
    };
    follow();
    firstNavigationRef.current?.focus({ preventScroll: true });
    const dismiss = () => { panel.hidePopover?.(); onClose(); anchor?.focus({ preventScroll: true }); };
    const key = (event: globalThis.KeyboardEvent) => {
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
    window.addEventListener('resize', follow);
    document.addEventListener('scroll', follow, true);
    return () => {
      document.removeEventListener('keydown', key, true);
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('focusin', outside, true);
      window.removeEventListener('resize', follow);
      document.removeEventListener('scroll', follow, true);
    };
  }, [open, anchorRef, onClose]);
  const choose = (action: () => void) => { panelRef.current?.hidePopover?.(); onClose(); action(); };
  if (!open) return null;
  return (
    <div aria-label="Navigation" className="g-popover g-navigation-popover" popover="manual" ref={panelRef} role="dialog">
      <nav aria-label="Primary" className="g-navigation-grid" id="primary-navigation" onKeyDown={(event) => moveFocus(event, { targets: '.g-nav-item' })}>
        {navGroups.map(({ label: group, items }) => (
          <div className="g-nav-group" key={group}>
            <h3 className="g-nav-group-label">{group}</h3>
            {items.filter(({ surface: target }) => target !== 'streaming' || !noStreaming).map(({ surface: target, label, icon }) => {
              const active = surfaceIsActive(surface, target) && !(target === 'settings' && managingServer);
              const count = target === 'downloads' ? activeDownloads : target === 'requests' ? pendingRequests : 0;
              const countLabel = target === 'requests' ? 'waiting' : 'active';
              return <button aria-current={active ? 'page' : undefined} aria-label={count ? `${label}, ${count} ${countLabel}` : label} className={`g-nav-item ${active ? 'is-active' : ''}`} key={target} onClick={() => choose(() => onNavigate(target))} ref={target === 'home' ? firstNavigationRef : undefined} type="button">
                <LuminaRouteIcon route={icon} /><span>{label}</span>{count ? <b aria-hidden="true" className="g-nav-count">{count}</b> : null}
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
