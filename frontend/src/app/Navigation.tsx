import { Ellipsis, Search, Server } from 'lucide-react';
import { memo, type ReactNode, useRef } from 'react';

import { allStreamingBlocked, useAccess } from '../features/access/access';
import { LuminaRouteIcon, type LuminaRouteIconName } from '../LuminaRouteIcons';
import { Dialog } from '../ui';
import { moveFocus } from '../features/media/focusNav';
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
    { surface: 'settings', label: 'My Settings', icon: 'settings' },
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

/** Primary destinations live in a modal; the page keeps the entire width beneath the header. */
export const NavigationDialog = memo(function NavigationDialog({ surface, activeDownloads, pendingRequests = 0, open, onNavigate, onClose, extra, onManageServer, managingServer = false }: {
  surface: Surface; activeDownloads: number; open: boolean; onNavigate: (surface: Surface) => void; onClose: () => void;
  pendingRequests?: number;
  onManageServer?: () => void;
  managingServer?: boolean;
  /** Account shortcuts shown with navigation on phones. */
  extra?: ReactNode;
}) {
  const firstNavigationRef = useRef<HTMLButtonElement>(null);
  const noStreaming = allStreamingBlocked(useAccess().access);
  const choose = (action: () => void) => { onClose(); action(); };
  return (
    <Dialog className="g-navigation-modal" initialFocus={firstNavigationRef} onClose={onClose} open={open} title="Navigation">
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
    </Dialog>
  );
});
