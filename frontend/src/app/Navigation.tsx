import { Ellipsis, Search } from 'lucide-react';
import { type KeyboardEvent, memo, type ReactNode, useEffect, useRef } from 'react';

import { allStreamingBlocked, useAccess } from '../features/access/access';
import { LuminaRouteIcon, type LuminaRouteIconName } from '../LuminaRouteIcons';
import { BrandMark } from '../ui';
import { type Surface } from './routes';

type NavItem = { surface: Surface; label: string; icon: LuminaRouteIconName };
/** Navigation only, grouped. Music lives under Library; Collections under the Library lens row. */
export const navGroups: ReadonlyArray<{ label: string; items: readonly NavItem[] }> = [
  { label: 'Watch', items: [
    { surface: 'home', label: 'Home', icon: 'home' },
    { surface: 'library', label: 'Library', icon: 'library' },
    { surface: 'streaming', label: 'Streaming', icon: 'live' },
    { surface: 'requests', label: 'Requests', icon: 'requests' },
  ] },
  { label: 'Vault', items: [
    { surface: 'downloads', label: 'Downloads', icon: 'downloads' },
    { surface: 'settings', label: 'Settings', icon: 'settings' },
  ] },
];

// Watch is its own route context; it never pretends to be Home.
export function surfaceIsActive(current: Surface, target: Surface): boolean {
  // A followed channel's page is still Streaming.
  return current === target || (target === 'streaming' && current === 'subscriptions');
}

export const MobileTabs = memo(function MobileTabs({ surface, activeDownloads, moreOpen, onNavigate, onSearch, onMore }: {
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
      {tab('library', 'Library', 'library')}
      {noStreaming ? null : tab('streaming', 'Streaming', 'live')}
      <button onClick={onSearch} type="button"><Search aria-hidden="true" /><span>Search</span></button>
      <button aria-expanded={moreOpen} aria-haspopup="dialog" aria-label={activeDownloads ? `More, ${activeDownloads} active downloads` : 'More'} onClick={onMore} type="button">
        <Ellipsis aria-hidden="true" /><span>More</span>{activeDownloads ? <b aria-hidden="true" className="g-nav-count">{activeDownloads}</b> : null}
      </button>
    </nav>
  );
});

export const Sidebar = memo(function Sidebar({ surface, activeDownloads, pendingRequests = 0, open, mobile, onNavigate, onClose, collapsed = false, extra }: {
  surface: Surface; activeDownloads: number; open: boolean; mobile: boolean; onNavigate: (surface: Surface) => void; onClose: () => void; collapsed?: boolean;
  /** Requests waiting for an admin's decision (admins only; 0 hides the badge). */
  pendingRequests?: number;
  /** The More drawer's profile items (phone only). */
  extra?: ReactNode;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const firstNavigationRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (mobile && open) requestAnimationFrame(() => firstNavigationRef.current?.focus());
  }, [mobile, open]);

  function onDrawerKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!mobile || !open) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])') || []);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  const navigationHidden = mobile ? !open : collapsed;
  const noStreaming = allStreamingBlocked(useAccess().access);

  return (
    <>
      <button aria-label="Close navigation" className={`g-scrim ${open ? 'is-visible' : ''}`} onClick={onClose} type="button" />
      <aside
        aria-hidden={navigationHidden ? true : undefined}
        aria-label={mobile ? 'Mobile navigation' : undefined}
        aria-modal={mobile && open ? true : undefined}
        className={`g-sidebar ${navigationHidden && !mobile ? 'is-hidden' : ''} ${open ? 'is-open' : ''}`}
        id="primary-navigation"
        inert={navigationHidden ? true : undefined}
        onKeyDown={onDrawerKeyDown}
        ref={drawerRef}
        role={mobile && open ? 'dialog' : undefined}
      >
        {mobile ? <BrandMark size="bar" /> : null}
        <nav aria-label="Primary">
          {navGroups.map((group) => (
            <div className="g-nav-group" key={group.label}>
              <p aria-hidden="true" className="g-label g-nav-group-label">{group.label}</p>
              {group.items.filter((item) => !(noStreaming && item.surface === 'streaming')).map(({ surface: target, label, icon }) => {
                const active = surfaceIsActive(surface, target);
                const count = target === 'downloads' ? activeDownloads : target === 'requests' ? pendingRequests : 0;
                const countLabel = target === 'requests' ? 'waiting' : 'active';
                return (
                  <button aria-current={active ? 'page' : undefined} aria-label={count ? `${label}, ${count} ${countLabel}` : label} className={`g-nav-item ${active ? 'is-active' : ''}`} key={target} onClick={() => onNavigate(target)} ref={target === 'home' ? firstNavigationRef : undefined} type="button">
                    <LuminaRouteIcon route={icon} /><span>{label}</span>{count ? <b aria-hidden="true" className="g-nav-count">{count}</b> : null}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        {mobile ? extra : null}
      </aside>
    </>
  );
});
