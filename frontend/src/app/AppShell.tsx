import { forwardRef, type FocusEventHandler, type ReactNode, type RefObject, useRef } from 'react';
import type { ThemePreference } from '../theme';
import type { UserProfile } from '../types';
import type { CollectionLoadState } from '../workspace';
import { LENS_LABELS, type LibraryLens } from '../features/gallery/libraryLens';
import { MobileTabs, NavigationPopover } from './Navigation';
import type { Surface } from './routes';
import { TopBar } from './TopBar';
import { useLibraryActivity } from './useLibraryActivity';
import './shell.css';

export function SkipLink({ unavailable = false }: { unavailable?: boolean }) {
  return <a aria-hidden={unavailable ? true : undefined} className="g-skip-link" href="#main-content" onClick={() => document.getElementById('main-content')?.focus()} tabIndex={unavailable ? -1 : undefined}>Skip to main content</a>;
}

export const MainLandmark = forwardRef<HTMLElement, { children: ReactNode; onFocusCapture?: FocusEventHandler<HTMLElement> }>(function MainLandmark({ children, onFocusCapture }, ref) {
  return <main aria-label="Main content" className="lumina-main" id="main-content" onFocusCapture={onFocusCapture} ref={ref} tabIndex={-1}>{children}</main>;
});

export function AppShell({ children, surface, pendingRequests = 0, user, mobile, compact, focused = false, navigationOpen, menuButtonRef, mainRef, announcer, activity, theme, extraSkipLinks, onNavigate, onMenu, onCloseNavigation, onSearch, onAddLink, onSettings, onManageServer, managingServer = false, onTheme, onSignOut, moreItems, videoView, onVideoView, contextualSearch = false, hideContextLabel = false }: {
  videoView?: string;
  hideContextLabel?: boolean;
  onVideoView?: (view: 'movies' | 'shows') => void;
  contextualSearch?: boolean;
  children: ReactNode;
  surface: Surface;
  /** Requests waiting for an admin (the Requests nav badge); 0 for members. */
  pendingRequests?: number;
  user: UserProfile;
  /** Phone, <= 680px: the tab bar and the icon-only Add link. */
  mobile: boolean;
  compact: boolean;
  /** A self-contained reader owns its own controls while retaining the app landmark and skip link. */
  focused?: boolean;
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
  const libraryLabel = videoView && Object.hasOwn(LENS_LABELS, videoView) ? LENS_LABELS[videoView as LibraryLens] : videoView === 'collections' ? 'Collections' : 'All video';
  const anchorRef = useRef<HTMLButtonElement | null>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const libraryActivity = useLibraryActivity(user.id);
  if (!navigationOpen) anchorRef.current = menuButtonRef.current;
  const current = (activity.state === 'ready' || activity.state === 'empty') && !activity.retrying;
  const trustedActive = current ? activity.active : 0;
  return (
    <div className="lumina-app" data-surface={surface}>
      <SkipLink />
      {extraSkipLinks}
      {focused ? null : <NavigationPopover videoView={videoView} onVideoView={onVideoView} anchorRef={anchorRef} onManageServer={onManageServer} managingServer={managingServer} activeDownloads={trustedActive} pendingRequests={pendingRequests} extra={mobile ? moreItems : null} onClose={onCloseNavigation} onNavigate={onNavigate} open={navigationOpen} surface={surface} />}
      <div className="app-background">
        {announcer}
        {focused ? null : <TopBar contextLabel={hideContextLabel ? undefined : surface === 'audio' ? 'Music' : surface === 'video' || surface === 'library' ? libraryLabel : surface === 'settings' ? managingServer ? 'Manage server' : 'My settings' : surface === 'subscriptions' ? 'Streaming' : surface[0].toUpperCase() + surface.slice(1)} hideSearch={contextualSearch || surface === 'settings'} activity={activity} compact={compact} libraryActivity={libraryActivity} menuButtonRef={menuButtonRef} menuExpanded={navigationOpen} menuLabel={navigationOpen ? 'Close navigation' : 'Open navigation'} mobile={mobile} onActivity={() => onNavigate('downloads')} onAddLink={onAddLink} onHome={() => onNavigate('home')} onMenu={() => { anchorRef.current = menuButtonRef.current; onMenu(); }} onSearch={onSearch} onSettings={onSettings} onManageServer={onManageServer} onSignOut={onSignOut} onTheme={onTheme} theme={theme} user={user} />}
        <MainLandmark onFocusCapture={surface === 'settings' ? (event) => {
          const target = event.target;
          if (target.closest('.music-dock, .music-mini, .watch-mini, dialog') || !target.matches('button, a, input, textarea, select, summary, [role=combobox]')) return;
          requestAnimationFrame(() => {
            if (!target.isConnected || !mainRef.current) return;
            const bounds = target.getBoundingClientRect();
            const top = [...mainRef.current.querySelectorAll<HTMLElement>('.music-dock, .music-mini, .watch-mini')].reduce((edge, player) => {
              const box = player.getBoundingClientRect();
              return box.height && bounds.right > box.left && bounds.left < box.right ? Math.min(edge, box.top) : edge;
            }, window.innerHeight);
            if (top < window.innerHeight && bounds.bottom > top - 16) window.scrollBy({ top: bounds.bottom - top + 16, behavior: 'instant' });
          });
        } : undefined} ref={mainRef}>{children}</MainLandmark>
        {focused || surface === 'settings' ? null : <MobileTabs videoView={videoView} onVideoView={onVideoView} activeDownloads={trustedActive} moreOpen={navigationOpen} moreButtonRef={moreButtonRef} onMore={() => { anchorRef.current = moreButtonRef.current; onMenu(); }} onNavigate={onNavigate} onSearch={onSearch} surface={surface} />}
      </div>
    </div>
  );
}
