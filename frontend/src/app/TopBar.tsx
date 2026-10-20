import { flushSync } from 'react-dom';
import { Menu as MenuIcon, Plus, Search } from 'lucide-react';
import { type CSSProperties, type RefObject } from 'react';
import { providerBlocked, useAccess } from '../features/access/access';
import { openMemberPicker, requestHomeEdit } from './commands';
import { useDeviceRingSize } from '../features/auth/memberPicker';
import type { ThemePreference } from '../theme';
import type { LibraryActivity, UserProfile } from '../types';
import { Avatar, BrandMark, Button, IconButton, Kbd, Menu, modKeyLabel, Popover, StatusText } from '../ui';
import type { CollectionLoadState } from '../workspace';
import type { LibraryActivitySnapshot } from './useLibraryActivity';

export interface TopBarProps {
  user: UserProfile;
  mobile: boolean;
  compact: boolean;
  menuLabel: 'Open navigation' | 'Close navigation';
  menuExpanded: boolean;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
  activity: { state: CollectionLoadState; retrying: boolean; active: number };
  libraryActivity: LibraryActivitySnapshot;
  theme: ThemePreference;
  onMenu: () => void;
  onHome: () => void;
  onSearch: () => void;
  onAddLink: () => void;
  onActivity: () => void;
  onSettings: () => void;
  onManageServer?: () => void;
  onTheme: (theme: ThemePreference) => void;
  onSignOut: () => void;
}

/** Download status remains distinct inside the combined household activity summary. */
export function downloadActivityLabel({ state, retrying, active }: TopBarProps['activity']): string {
  if (retrying) return 'Refreshing download activity';
  if (state === 'loading') return 'Checking download activity';
  if (state === 'stale') return 'Download activity may be out of date';
  if (state === 'offline' || state === 'failed') return 'Download activity unavailable';
  return active ? `${active} ${active === 1 ? 'download' : 'downloads'} in progress` : 'No active downloads';
}

export function libraryIsActive(activity: LibraryActivity | null): boolean {
  return Boolean(activity && (activity.scanning_roots > (activity.stalled_roots ?? 0) || activity.preparing_roots));
}

function artworkButtonLabel(activity: LibraryActivity | null): string | null {
  const pending = activity?.artwork_pending;
  if (typeof pending !== 'number' || pending <= 0) return null;
  const thumbnails = `${pending} ${pending === 1 ? 'thumbnail' : 'thumbnails'}`;
  return activity?.artwork_paused ? `${thumbnails} queued` : `Preparing ${thumbnails}`;
}

function libraryButtonLabel(snapshot: LibraryActivitySnapshot): string | null {
  if (snapshot.state === 'loading') return 'Checking library activity';
  if (snapshot.state === 'failed') return 'Library activity unavailable';
  const activity = snapshot.activity;
  if (!activity) return null;
  if (activity.stalled_roots) return 'Library scan needs attention';
  if (!libraryIsActive(activity)) {
    if (activity.waiting_error_files) return `${activity.waiting_error_files} ${activity.waiting_error_files === 1 ? 'file' : 'files'} waiting to be checked`;
    if (activity.waiting_files) return `${activity.waiting_files} ${activity.waiting_files === 1 ? 'file' : 'files'} waiting to index`;
    return null;
  }
  if (activity.discovering_roots) return `Discovering files${activity.discovered_files ? `, ${activity.discovered_files} found` : ''}`;
  if (activity.indexing_roots) return activity.total_files !== null
    ? `Indexing library, ${activity.completed_files} of ${activity.total_files} checked`
    : `Indexing library${activity.completed_files || activity.inspected_files ? `, ${activity.completed_files || activity.inspected_files} files checked` : ''}`;
  if (activity.preparing_roots) return 'Preparing library scan';
  if (activity.waiting_files && !activity.scanning_roots) return `${activity.waiting_files} ${activity.waiting_files === 1 ? 'file' : 'files'} waiting to index`;
  return `Library scanning${activity.inspected_files ? `, ${activity.inspected_files} files checked` : ''}`;
}

export function activityLabel(downloads: TopBarProps['activity'], library: LibraryActivitySnapshot): string {
  const current = (downloads.state === 'ready' || downloads.state === 'empty') && !downloads.retrying;
  const parts = [current && downloads.active ? `${downloads.active} ${downloads.active === 1 ? 'download' : 'downloads'}` : current ? null : downloadActivityLabel(downloads), libraryButtonLabel(library), artworkButtonLabel(library.activity)].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'No active downloads';
}

export function TopBar(props: TopBarProps) {
  const { user, mobile, compact, activity, libraryActivity, theme } = props;
  const ringSize = useDeviceRingSize();
  const canAddLink = !providerBlocked(useAccess().access, 'open_search');
  const name = user.display_name || user.username;
  const current = (activity.state === 'ready' || activity.state === 'empty') && !activity.retrying;
  const active = current ? activity.active : 0;
  const label = activityLabel(activity, libraryActivity);
  const library = libraryActivity.state === 'ready' ? libraryActivity.activity : null;
  const scanning = libraryIsActive(library);
  const stalledCount = library?.stalled_roots ?? 0;
  const waitingFiles = library?.waiting_files ?? 0;
  const waitingErrorFiles = library?.waiting_error_files ?? 0;
  const artworkPending = typeof library?.artwork_pending === 'number' ? library.artwork_pending : null;
  const artworkMissing = typeof library?.artwork_missing === 'number' ? library.artwork_missing : null;
  const artworkFailed = typeof library?.artwork_failed === 'number' ? library.artwork_failed : null;
  const artworkPaused = library?.artwork_paused === true;
  const artworkCountsKnown = artworkPending !== null && artworkMissing !== null && artworkFailed !== null;
  const artworkActive = (artworkPending ?? 0) > 0 && !artworkPaused;
  const stalled = Boolean(stalledCount);
  const waiting = Boolean(waitingFiles);
  const indexingProgress = library?.indexing_roots && library.total_files !== null && library.progress !== null ? Math.max(0, Math.min(100, library.progress)) : null;
  const indeterminate = artworkActive || (scanning && indexingProgress === null);
  const ringProgress = indexingProgress === null ? (active ? 360 : 0) : Math.round(indexingProgress * 3.6);
  const shortcut = modKeyLabel();
  const activityButton = (triggerProps: Record<string, unknown> = {}) => (
    <button {...triggerProps} aria-label={label} className="g-activity" type="button">
      <span aria-hidden="true" className={`g-activity-ring${indeterminate ? ' is-indeterminate' : ''}`} style={{ '--progress': `${ringProgress}deg` } as CSSProperties} />
      {active ? <span aria-hidden="true" className="g-activity-count">{active}</span> : null}
    </button>
  );
  return (
    <header className="g-topbar">
      <div className="g-topbar-start">
        <IconButton aria-controls="primary-navigation" aria-expanded={props.menuExpanded} aria-haspopup="dialog" icon={<MenuIcon />} label={props.menuLabel} onClick={props.onMenu} ref={props.menuButtonRef} />
        <a className="g-topbar-brand" href="/" onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return; event.preventDefault(); props.onHome(); }}>
          <BrandMark compact={compact} size="bar" />
        </a>
      </div>
      <button aria-label={`Search Lumina, ${shortcut}`} className="g-search-trigger" onClick={props.onSearch} type="button">
        <Search aria-hidden="true" />
        <span className="g-search-trigger-text">Search Lumina</span>
        <Kbd>{shortcut}</Kbd>
      </button>
      <div className="g-topbar-end">
        {!canAddLink ? null : mobile ? <IconButton icon={<Plus />} label="Add a link" onClick={props.onAddLink} /> : <Button aria-label="Add a link" icon={<Plus />} onClick={props.onAddLink} variant="quiet">Add link</Button>}
        <Popover label="Activity" openOnHover trigger={(triggerProps) => activityButton(triggerProps)}>
          <div className="g-activity-popover">
            <section>
              <h2>Downloads</h2>
              <StatusText tone={current ? active ? 'attention' : 'muted' : 'attention'}>{downloadActivityLabel(activity)}</StatusText>
              <Button onClick={props.onActivity} variant="quiet">Open Downloads</Button>
            </section>
            <section>
              <h2>Library</h2>
              {libraryActivity.state === 'loading' ? <StatusText tone="muted">Checking library activity</StatusText> : null}
              {libraryActivity.state === 'failed' ? <StatusText tone="attention">Library activity is unavailable</StatusText> : null}
              {libraryActivity.state === 'ready' && !scanning && !stalled && !waiting ? <StatusText tone="muted">No library scan running</StatusText> : null}
              {libraryActivity.state === 'ready' && (scanning || stalled || waiting) ? (
                <div className="g-library-activity">
                  {stalled ? <><StatusText tone="danger">Library scan needs attention</StatusText><p>{stalledCount === 1 ? 'One scan has' : `${stalledCount} scans have`} stopped reporting progress. Lumina will keep retrying.</p></> : null}
                  {scanning && library!.discovering_roots ? <><StatusText tone="attention">Discovering files</StatusText><p>{library!.discovered_files ? `${library!.discovered_files} ${library!.discovered_files === 1 ? 'file' : 'files'} found. ` : ''}Counting candidates before indexing begins.</p></> : null}
                  {scanning && !library!.discovering_roots && library!.indexing_roots ? <><StatusText tone="attention">Indexing library</StatusText>{indexingProgress === null || library!.total_files === null ? <p>{library!.completed_files || library!.inspected_files ? `${library!.completed_files || library!.inspected_files} files checked` : 'Checking files'}</p> : <><progress aria-label="Library indexing progress" max={100} value={indexingProgress} /><p>{library!.completed_files} of {library!.total_files} checked · {Math.round(indexingProgress)}%</p></>}</> : null}
                  {scanning && !library!.discovering_roots && !library!.indexing_roots && library!.preparing_roots ? <StatusText tone="attention">Preparing library scan</StatusText> : null}
                  {scanning && !library!.discovering_roots && !library!.indexing_roots && !library!.preparing_roots && library!.scanning_roots ? <><StatusText tone="attention">Scanning library</StatusText>{library!.inspected_files ? <p>{library!.inspected_files} files checked</p> : null}</> : null}
                  {waitingFiles > waitingErrorFiles ? <p>{waitingFiles - waitingErrorFiles} {waitingFiles - waitingErrorFiles === 1 ? 'file is' : 'files are'} still copying or stabilizing. {waitingFiles - waitingErrorFiles === 1 ? 'It' : 'They'} will be indexed when ready.</p> : null}
                  {waitingErrorFiles ? <p>{waitingErrorFiles} {waitingErrorFiles === 1 ? 'file could' : 'files could'} not be checked yet and will be retried.</p> : null}
                  {scanning || waiting ? <p>New files appear in the library after indexing.</p> : null}
                </div>
              ) : null}
            </section>
            <section>
              <h2>Artwork</h2>
              {libraryActivity.state !== 'ready' ? <StatusText tone="muted">Artwork preparation status is unavailable</StatusText> : null}
              {libraryActivity.state === 'ready' && !artworkCountsKnown ? <StatusText tone="muted">Artwork preparation status is unavailable on this server</StatusText> : null}
              {libraryActivity.state === 'ready' && artworkCountsKnown ? <div className="g-library-activity">
                {artworkPending ? <><StatusText tone={artworkPaused ? 'muted' : 'attention'}>{artworkPaused ? 'Thumbnail preparation paused' : 'Preparing thumbnails'}</StatusText><p>{artworkPending} {artworkPending === 1 ? 'thumbnail is' : 'thumbnails are'} queued for preparation.</p></> : <StatusText tone="muted">Thumbnail preparation idle</StatusText>}
                {artworkPaused ? <p>Paused while a video is being converted.</p> : null}
                {artworkMissing ? <p>{artworkMissing} {artworkMissing === 1 ? 'image still needs' : 'images still need'} a thumbnail.</p> : null}
                {artworkFailed ? <p>{artworkFailed} {artworkFailed === 1 ? 'thumbnail could' : 'thumbnails could'} not be prepared.</p> : null}
              </div> : null}
            </section>
          </div>
        </Popover>
        <Menu
          align="end"
          items={[
            { kind: 'group', label: `${name} · ${user.role === 'admin' ? 'Vault owner' : 'Household member'}` },
            { kind: 'item', label: ringSize === 0 ? 'Sign in as someone else…' : 'Switch member…', onSelect: openMemberPicker },
            { kind: 'item', label: 'Edit home', onSelect: () => { flushSync(props.onHome); requestAnimationFrame(() => requestHomeEdit()); } },
            { kind: 'item', label: 'My Settings', onSelect: props.onSettings },
            ...(props.onManageServer ? [{ kind: 'item' as const, label: 'Manage Server', onSelect: props.onManageServer }] : []),
            { kind: 'group', label: 'Theme' },
            { kind: 'radio', label: 'System', checked: theme === 'system', hint: theme === 'system' ? 'Current' : undefined, onSelect: () => props.onTheme('system') },
            { kind: 'radio', label: 'Light', checked: theme === 'light', hint: theme === 'light' ? 'Current' : undefined, onSelect: () => props.onTheme('light') },
            { kind: 'radio', label: 'Dark', checked: theme === 'dark', hint: theme === 'dark' ? 'Current' : undefined, onSelect: () => props.onTheme('dark') },
            { kind: 'radio', label: 'OLED', checked: theme === 'oled', hint: theme === 'oled' ? 'Current' : undefined, onSelect: () => props.onTheme('oled') },
            { kind: 'separator' },
            { kind: 'item', label: 'Sign out', onSelect: props.onSignOut },
          ]}
          trigger={(triggerProps) => (
            <button {...triggerProps} aria-label={`${name}, account menu`} className="g-profile-trigger" type="button"><Avatar decorative name={name} size={34} /></button>
          )}
        />
      </div>
    </header>
  );
}
