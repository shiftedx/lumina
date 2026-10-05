import { forwardRef, type KeyboardEvent, type ReactNode, type RefObject } from 'react';
import type { ThemePreference } from '../theme';
import type { UserProfile } from '../types';
import type { CollectionLoadState } from '../workspace';
import { MobileTabs, Sidebar } from './Navigation';
import type { Surface } from './routes';
import { TopBar } from './TopBar';
import './shell.css';

export function SkipLink({ unavailable = false }: { unavailable?: boolean }) {
  return <a aria-hidden={unavailable ? true : undefined} className="g-skip-link" href="#main-content" onClick={() => document.getElementById('main-content')?.focus()} tabIndex={unavailable ? -1 : undefined}>Skip to main content</a>;
}

export const MainLandmark = forwardRef<HTMLElement, { children: ReactNode }>(function MainLandmark({ children }, ref) {
  return <main aria-label="Main content" className="lumina-main" id="main-content" ref={ref} tabIndex={-1}>{children}</main>;
});

export function AppShell({ children, surface, pendingRequests = 0, user, mobile, drawer, compact, navigationOpen, sidebarCollapsed, menuButtonRef, mainRef, announcer, activity, theme, extraSkipLinks, onNavigate, onMenu, onCloseNavigation, onSearch, onAddLink, onSettings, onTheme, onSignOut, moreItems }: {
  children: ReactNode;
  surface: Surface;
  /** Requests waiting for an admin (the Requests nav badge); 0 for members. */
  pendingRequests?: number;
  user: UserProfile;
  /** Phone, <= 680px: the tab bar and the icon-only Add link. */
  mobile: boolean;
  /** <= 960px: the sidebar is a drawer (Sidebar's `mobile`). Always true when `mobile` is. */
  drawer: boolean;
  compact: boolean;
  navigationOpen: boolean;
  sidebarCollapsed: boolean;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
  mainRef: RefObject<HTMLElement | null>;
  announcer: ReactNode;
  activity: { state: CollectionLoadState; retrying: boolean; active: number };
  theme: ThemePreference;
  /** The "Skip to mini player", rendered after the main skip link while the mini player shows. */
  extraSkipLinks?: ReactNode;
  onNavigate: (surface: Surface) => void;
  onMenu: () => void;
  onCloseNavigation: () => void;
  onSearch: () => void;
  onAddLink: () => void;
  onSettings: () => void;
  onTheme: (theme: ThemePreference) => void;
  onSignOut: () => void;
  moreItems: ReactNode;
}) {
  const current = (activity.state === 'ready' || activity.state === 'empty') && !activity.retrying;
  const trustedActive = current ? activity.active : 0;
  const menuLabel = drawer || sidebarCollapsed ? 'Open navigation' : 'Close navigation';
  const onMenuKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (drawer || sidebarCollapsed || event.key !== 'Tab' || event.shiftKey) return;
    const first = document.querySelector<HTMLElement>('#primary-navigation nav button');
    first?.focus();
    // Consume the Tab only when focus actually moved: a focus() on a still-hidden item is a silent no-op, and a
    // swallowed Tab is lost. Otherwise the browser's own Tab proceeds.
    if (first && document.activeElement === first) event.preventDefault();
  };
  return (
    <div className={`lumina-app ${sidebarCollapsed ? 'sidebar-collapsed' : 'sidebar-expanded'}`}>
      <SkipLink unavailable={drawer && navigationOpen} />
      {extraSkipLinks}
      <Sidebar activeDownloads={trustedActive} pendingRequests={pendingRequests} collapsed={sidebarCollapsed} extra={moreItems} mobile={drawer} onClose={onCloseNavigation} onNavigate={onNavigate} open={navigationOpen} surface={surface} />
      <div className="app-background" inert={drawer && navigationOpen ? true : undefined}>
        {announcer}
        <TopBar activity={activity} compact={compact} drawer={drawer} menuButtonRef={menuButtonRef} menuExpanded={drawer ? navigationOpen : !sidebarCollapsed} menuLabel={menuLabel} mobile={mobile} onActivity={() => onNavigate('downloads')} onAddLink={onAddLink} onHome={() => onNavigate('home')} onMenu={onMenu} onMenuKeyDown={onMenuKeyDown} onSearch={onSearch} onSettings={onSettings} onSignOut={onSignOut} onTheme={onTheme} theme={theme} user={user} />
        <MainLandmark ref={mainRef}>{children}</MainLandmark>
        <MobileTabs activeDownloads={trustedActive} moreOpen={navigationOpen} onMore={onMenu} onNavigate={onNavigate} onSearch={onSearch} surface={surface} />
      </div>
    </div>
  );
}
