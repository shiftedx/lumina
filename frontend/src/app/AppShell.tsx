import { forwardRef, type ReactNode, type RefObject, useRef } from 'react';
import type { ThemePreference } from '../theme';
import type { UserProfile } from '../types';
import type { CollectionLoadState } from '../workspace';
import { MobileTabs, NavigationPopover } from './Navigation';
import type { Surface } from './routes';
import { TopBar } from './TopBar';
import './shell.css';

export function SkipLink({ unavailable = false }: { unavailable?: boolean }) {
  return <a aria-hidden={unavailable ? true : undefined} className="g-skip-link" href="#main-content" onClick={() => document.getElementById('main-content')?.focus()} tabIndex={unavailable ? -1 : undefined}>Skip to main content</a>;
}

export const MainLandmark = forwardRef<HTMLElement, { children: ReactNode }>(function MainLandmark({ children }, ref) {
  return <main aria-label="Main content" className="lumina-main" id="main-content" ref={ref} tabIndex={-1}>{children}</main>;
});

export function AppShell({ children, surface, pendingRequests = 0, user, mobile, compact, navigationOpen, menuButtonRef, mainRef, announcer, activity, theme, extraSkipLinks, onNavigate, onMenu, onCloseNavigation, onSearch, onAddLink, onSettings, onManageServer, managingServer = false, onTheme, onSignOut, moreItems }: {
  children: ReactNode;
  surface: Surface;
  /** Requests waiting for an admin (the Requests nav badge); 0 for members. */
  pendingRequests?: number;
  user: UserProfile;
  /** Phone, <= 680px: the tab bar and the icon-only Add link. */
  mobile: boolean;
  compact: boolean;
  navigationOpen: boolean;
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
  onManageServer?: () => void;
  managingServer?: boolean;
  onTheme: (theme: ThemePreference) => void;
  onSignOut: () => void;
  moreItems: ReactNode;
}) {
  const anchorRef = useRef<HTMLButtonElement | null>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  if (!navigationOpen) anchorRef.current = menuButtonRef.current;
  const current = (activity.state === 'ready' || activity.state === 'empty') && !activity.retrying;
  const trustedActive = current ? activity.active : 0;
  return (
    <div className="lumina-app">
      <SkipLink />
      {extraSkipLinks}
      <NavigationPopover anchorRef={anchorRef} onManageServer={onManageServer} managingServer={managingServer} activeDownloads={trustedActive} pendingRequests={pendingRequests} extra={mobile ? moreItems : null} onClose={onCloseNavigation} onNavigate={onNavigate} open={navigationOpen} surface={surface} />
      <div className="app-background">
        {announcer}
        <TopBar activity={activity} compact={compact} menuButtonRef={menuButtonRef} menuExpanded={navigationOpen} menuLabel="Open navigation" mobile={mobile} onActivity={() => onNavigate('downloads')} onAddLink={onAddLink} onHome={() => onNavigate('home')} onMenu={() => { anchorRef.current = menuButtonRef.current; onMenu(); }} onSearch={onSearch} onSettings={onSettings} onManageServer={onManageServer} onSignOut={onSignOut} onTheme={onTheme} theme={theme} user={user} />
        <MainLandmark ref={mainRef}>{children}</MainLandmark>
        <MobileTabs activeDownloads={trustedActive} moreOpen={navigationOpen} moreButtonRef={moreButtonRef} onMore={() => { anchorRef.current = moreButtonRef.current; onMenu(); }} onNavigate={onNavigate} onSearch={onSearch} surface={surface} />
      </div>
    </div>
  );
}
