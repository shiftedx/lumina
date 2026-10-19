import { type KeyboardEvent, type MouseEvent, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Bell, Captions, ChevronRight, CircleUserRound, Compass, Download, FolderOpen, Gauge, HardDrive, KeyRound, Palette, Play, Plug, ShieldCheck, Sparkles, Users, Wrench } from 'lucide-react';

import { routePath, type SettingsSectionId } from '../../app/routes';
import { Button, Field, fieldProps, Input, Masthead, Select } from '../../ui';
import { MemberPage } from '../admin/MemberPage';
import { moveFocus } from '../media/focusNav';
import { categoryForPage, pageById, pageForRow, resolveSettingsPage, SETTINGS_CATEGORIES, SETTINGS_PAGES, type SettingsCategory, type SettingsPage, type SettingsSpace } from './navigation';
import { sectionById, visibleSections } from './registry';
import { SectionRows, settingDomId } from './SettingRow';
import { SettingsSearchResults } from './SettingsSearch';
import { SettingsHostContext, type SettingsSurfaceProps } from './settingsHost';
import { confirmLeaveSettings } from './unsavedChanges';
import './settings.css';

export type SettingsLocation = { section: SettingsSectionId | null; page: string | null; space: SettingsSpace };
export type SettingsShellProps = SettingsSurfaceProps & {
  section?: SettingsSectionId | null;
  page?: string | null;
  space?: SettingsSpace;
  onLocation?: (location: SettingsLocation) => void;
  onSection?: (section: SettingsSectionId | null) => void;
  member?: string | null;
  onMember?: (member: string | null) => void;
};

const NAV_TARGETS = '.g-settings-search input, .g-settings-link';
const PANE_TARGETS = ['.g-settings-back', '.g-settings-page-link', '.g-settings-page-select .g-select-trigger', '.g-settings-local-advanced summary', '.g-settings-result-link', '.g-member-nav .g-button', '.g-member-nav [aria-selected="true"]', ...['button', 'a[href]', 'input', 'select', 'textarea', 'summary'].map((tag) => `.setting-row ${tag}:not(:disabled)`), '.setting-form-bar button:not(:disabled)', '.g-save-bar button:not(:disabled)'].join(', ');
const VERTICAL_EXIT = 'input:not([type="checkbox"]):not([type="radio"]), select';
const OWNS_LEFT = 'input:not([type="checkbox"]), textarea, select, [role="combobox"], [role="radiogroup"], [role="tablist"]';
const CATEGORY_ICONS = { profile: CircleUserRound, playback: Play, display: Palette, discovery: Compass, downloads: Download, privacy: ShieldCheck, overview: Gauge, household: Users, library: FolderOpen, connections: Plug, ai: Sparkles, maintenance: Wrench } satisfies Record<SettingsCategory, typeof Play>;
const QUICK_CHANGES: Record<SettingsSpace, Array<{ page: string; label: string; icon: typeof Play; row?: string }>> = {
  personal: [
    { page: 'captions', label: 'Adjust subtitles', icon: Captions },
    { page: 'display', label: 'Change appearance', icon: Palette },
    { page: 'watching', label: 'Set streaming quality', icon: Play, row: 'playback.max-quality' },
    { page: 'security', label: 'Change password', icon: KeyRound, row: 'account.password' },
  ],
  server: [
    { page: 'invitations', label: 'Invite someone', icon: Users },
    { page: 'folders', label: 'Manage media folders', icon: FolderOpen },
    { page: 'backups', label: 'Create a backup', icon: HardDrive },
    { page: 'tasks', label: 'Check background tasks', icon: Bell },
  ],
};

/** Two spaces, six categories each. Every registry row has a searchable, bookmarkable subpage. */
export function SettingsSurface({ section = null, page = null, space, onLocation, onSection = () => undefined, member = null, onMember = () => undefined, ...host }: SettingsShellProps) {
  const [local, setLocal] = useState<SettingsLocation | null>(null);
  const [seen, setSeen] = useState({ section, page, space });
  if (section !== seen.section || page !== seen.page || space !== seen.space) {
    setSeen({ section, page, space });
    // Hosts using the older section callback still keep the subpage chosen in this shell.
    if (local?.section !== section || onLocation) setLocal(null);
  }
  const effectiveSection = local ? local.section : section;
  const requestedPage = effectiveSection ? resolveSettingsPage(effectiveSection, (local ? local.page : page)) : null;
  const selectedSpace = requestedPage ? categoryForPage(requestedPage).space : local?.space ?? space ?? 'personal';
  const forbidden = host.user.role !== 'admin' && selectedSpace === 'server';
  const activeSpace = forbidden ? 'personal' : selectedSpace;
  const categories = SETTINGS_CATEGORIES.filter((category) => category.space === activeSpace);
  const current = requestedPage ?? pageById(activeSpace === 'server' ? 'dashboard' : 'watching')!;
  const category = categoryForPage(current);
  const pages = SETTINGS_PAGES.filter((entry) => entry.category === category.id && entry.id !== 'about');
  const shown = sectionById(current.section)!;
  const allowed = visibleSections(host.user);
  const navRef = useRef<HTMLElement>(null);
  const paneRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  const searching = query.trim() !== '';
  const home = !effectiveSection && !searching && !member;
  const locationKey = `${effectiveSection}:${(local ? local.page : page)}:${activeSpace}`;
  const [seenLocation, setSeenLocation] = useState(locationKey);
  if (locationKey !== seenLocation) { setSeenLocation(locationKey); setQuery(''); }
  const pendingFocus = useRef<string | null>(null);

  function choose(next: SettingsLocation, row?: string): boolean {
    if (next.section === effectiveSection && next.page === current.id && next.space === activeSpace && !searching && !member) return true;
    if (!confirmLeaveSettings()) return false;
    pendingFocus.current = row ?? (next.section ? 'settings-section-title' : 'settings-home-title');
    setQuery('');
    if (member) onMember(null);
    if (onLocation) onLocation(next);
    else { setLocal(next); if (next.section !== section) onSection(next.section); }
    return true;
  }
  function openPage(next: SettingsPage, row?: string) {
    return choose({ section: next.section, page: next.id, space: categoryForPage(next).space }, row);
  }
  function follow(event: MouseEvent<HTMLAnchorElement>, next: SettingsPage) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    openPage(next);
  }
  function changeQuery(next: string) {
    if ((next.trim() !== '') !== searching && !confirmLeaveSettings()) return;
    setQuery(next);
  }

  useEffect(() => {
    const row = pendingFocus.current ?? (window.location.hash.startsWith('#setting-') ? window.location.hash.slice(1) : null);
    if (!row || searching) return;
    const waitingFrom = document.activeElement;
    const focus = () => {
      if (document.activeElement !== waitingFrom && document.activeElement !== document.body) { pendingFocus.current = null; return true; }
      const heading = row === 'settings-section-title' || row === 'settings-home-title';
      const target = document.getElementById(heading || row.startsWith('setting-') ? row : settingDomId(row));
      if (!target || (!heading && !paneRef.current?.contains(target))) return false;
      const details = target.closest('details');
      if (details) details.open = true;
      const controls = target.querySelector('.g-setting-control') ?? target;
      const control = controls.querySelector<HTMLElement>('input:not(:disabled), select:not(:disabled):not([hidden]), textarea:not(:disabled)') ?? controls.querySelector<HTMLElement>('button:not(:disabled), a[href]');
      (control ?? target).focus();
      target.scrollIntoView?.({ block: 'nearest' });
      pendingFocus.current = null;
      return true;
    };
    if (focus()) return;
    // Providers load asynchronously; focus once the searched row is present.
    const observer = new MutationObserver(() => { if (focus()) observer.disconnect(); });
    if (paneRef.current) observer.observe(paneRef.current, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [locationKey, searching, member]);

  function onNavKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (home) return;
    const target = event.target as HTMLElement;
    moveFocus(event, { targets: NAV_TARGETS, verticalExit: VERTICAL_EXIT });
    if (event.defaultPrevented || event.key !== 'ArrowRight' || target.matches('input')) return;
    const phone = window.matchMedia?.('(max-width: 680px)').matches ?? false;
    const first = paneRef.current?.querySelector<HTMLElement>(phone ? '.g-settings-page-select .g-select-trigger' : '.g-settings-page-link[aria-current="page"]') ?? paneRef.current?.querySelector<HTMLElement>(PANE_TARGETS);
    if (first) { event.preventDefault(); first.focus(); }
  }
  function moveToAdjacentSettingRow(event: KeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement;
    const pane = paneRef.current;
    if (!pane) return false;
    const row = target.closest('.setting-row');
    if (row && (!row.classList.contains('is-block') || !target.closest('.g-setting-control')) && (event.key === 'ArrowDown' || event.key === 'ArrowUp') && !target.closest('textarea')) {
      const rows = [...pane.querySelectorAll<HTMLElement>('.setting-row')].filter((entry) => !entry.closest('[hidden], details:not([open]) > :not(summary)'));
      const next = rows[rows.indexOf(row as HTMLElement) + (event.key === 'ArrowDown' ? 1 : -1)];
      const selector = target.closest('.g-setting-control') ? '.g-setting-control input:not(:disabled), .g-setting-control select:not(:disabled):not([hidden]), .g-setting-control button:not(:disabled), .g-setting-control a[href]' : '.g-info-button';
      const control = next?.querySelector<HTMLElement>(selector) ?? next?.querySelector<HTMLElement>('.g-info-button');
      if (control) { event.preventDefault(); control.focus(); control.scrollIntoView?.({ block: 'nearest' }); return true; }
    }
    return false;
  }
  function onPaneKeyDownCapture(event: KeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement;
    if (target.matches('[role="combobox"][aria-expanded="false"]') && moveToAdjacentSettingRow(event)) event.stopPropagation();
  }
  function onPaneKeyDown(event: KeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement;
    if (!paneRef.current?.contains(target) || event.defaultPrevented || target.closest('[role="listbox"]')) return;
    if (event.key === 'Enter' && target.matches('input[type="checkbox"], input[type="radio"]')) { event.preventDefault(); target.click(); return; }
    if (moveToAdjacentSettingRow(event)) return;
    const phone = window.matchMedia?.('(max-width: 680px)').matches ?? false;
    const targets = PANE_TARGETS.replace(phone ? '.g-settings-page-link,' : '.g-settings-page-select .g-select-trigger,', '');
    moveFocus(event, { targets, verticalExit: VERTICAL_EXIT });
    if (event.defaultPrevented || event.key !== 'ArrowLeft' || target.closest(OWNS_LEFT)) return;
    const link = navRef.current?.querySelector<HTMLElement>('.g-settings-link[aria-current="page"]') ?? navRef.current?.querySelector<HTMLElement>('.g-settings-link');
    if (link) { event.preventDefault(); link.focus(); }
  }

  return (
    <SettingsHostContext.Provider value={{ ...host, showAdvanced: true, visibleSettingIds: current.rows, onOpenSection: (id) => openPage(resolveSettingsPage(id)), onOpenSetting: (id) => { const next = pageForRow(id); return next ? openPage(next, id) : false; }, onOpenMember: (id) => { if (!confirmLeaveSettings()) return; onMember(id); } }}>
      <div className="g-settings" data-view={home ? 'list' : 'detail'} data-space={activeSpace} onKeyDown={home ? (event) => moveFocus(event, { targets: '.g-settings-spaces a, .g-settings-search input, .g-settings-shortcut, .g-settings-sidebar .g-settings-link', verticalExit: VERTICAL_EXIT }) : undefined}>
        <header className="g-settings-header">
          <div className="g-settings-heading"><Masthead title={activeSpace === 'server' ? 'Manage Server' : 'My Settings'} />{home ? <p className="g-settings-welcome">{activeSpace === 'server' ? 'Keep your household running smoothly.' : 'Settle in. Make Lumina yours.'}</p> : null}</div>
          {host.user.role === 'admin' ? <nav aria-label="Settings spaces" className="g-settings-spaces" data-focus-row onKeyDown={(event) => moveFocus(event, { targets: 'a' })}>
            {(['personal', 'server'] as const).map((next) => <a className="g-settings-link" aria-current={activeSpace === next ? 'page' : undefined} href={routePath({ surface: 'settings', space: next })} key={next} onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault(); choose({ section: null, page: null, space: next });
            }}>{next === 'personal' ? 'My Settings' : 'Manage Server'}</a>)}
          </nav> : null}
        </header>
        <div className="g-settings-search" role="search" onKeyDown={(event) => { if (event.key === 'ArrowDown') { const first = searching ? paneRef.current?.querySelector<HTMLElement>('.g-settings-result-link') : home ? event.currentTarget.parentElement?.querySelector<HTMLElement>('.g-settings-shortcut') : navRef.current?.querySelector<HTMLElement>('.g-settings-link'); if (first) { event.preventDefault(); first.focus(); } } }}>
          <Field hideLabel label="Search settings">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" onChange={(event) => changeQuery(event.target.value)} placeholder={home ? 'Find a setting — try “subtitles” or “quality”' : 'Search settings'} type="search" value={query} />}</Field>
        </div>
        {home ? <section aria-labelledby="settings-home-title" className="g-settings-quick" onKeyDown={(event) => moveFocus(event, { targets: 'a' })}>
          <h2 id="settings-home-title" tabIndex={-1}>Quick changes <span>Get straight to it.</span></h2>
          <div className="g-settings-shortcuts" data-focus-row>{QUICK_CHANGES[activeSpace].map((shortcut) => {
            const next = pageById(shortcut.page)!;
            return <a className="g-settings-shortcut" href={`${routePath({ surface: 'settings', section: next.section, page: next.id, space: activeSpace })}${shortcut.row ? `#${settingDomId(shortcut.row)}` : ''}`} key={next.id} onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault(); openPage(next, shortcut.row);
            }}><shortcut.icon aria-hidden="true" /><span>{shortcut.label}</span><ArrowRight aria-hidden="true" /></a>;
          })}</div>
        </section> : null}
        <nav aria-label="Settings sections" className="g-settings-sidebar" onKeyDown={onNavKeyDown} ref={navRef}>
          {home ? <h2>Browse settings</h2> : null}
          <ul data-focus-row={home || undefined}>
            {categories.map((entry) => {
              const next = pageById(entry.page)!;
              const Icon = CATEGORY_ICONS[entry.id];
              return <li data-focus-row={!home || undefined} key={entry.id}><a aria-label={entry.label} aria-describedby={home ? `settings-category-${entry.id}` : undefined} aria-current={!home && !forbidden && !searching && category.id === entry.id ? 'page' : undefined} className="g-settings-link" href={routePath({ surface: 'settings', section: next.section, page: next.id, space: activeSpace })} onClick={(event) => follow(event, next)}><Icon aria-hidden="true" /><span className="g-settings-category-copy"><span>{entry.label}</span>{home ? <span className="g-settings-category-summary" id={`settings-category-${entry.id}`}>{entry.summary}</span> : null}</span>{home ? <ChevronRight aria-hidden="true" className="g-settings-category-arrow" /> : null}</a></li>;
            })}
          </ul>
          <p className="g-settings-scope-note">{activeSpace === 'personal' ? 'Preferences for your profile.' : 'Entire household · Server changes affect everyone.'}</p>
          <a className="g-settings-link g-settings-about" href="/settings/about" onClick={(event) => follow(event, pageById('about')!)}>About Lumina</a>
        </nav>
        <div className="g-settings-pane" hidden={home} onKeyDown={onPaneKeyDown} onKeyDownCapture={onPaneKeyDownCapture} ref={paneRef}>
          {!home ? <Button className="g-settings-back" icon={<ArrowLeft />} onClick={() => choose({ section: null, page: null, space: activeSpace })} variant="quiet">All settings</Button> : null}
          {home ? null : searching ? <SettingsSearchResults onOpen={openPage} query={query} sections={allowed} /> : forbidden ? <p className="g-settings-summary" role="status">This part of Settings is for vault owners. Ask a vault owner if something on this server needs changing.</p> : current.section === 'members' && member ? (
            <MemberPage key={member} memberId={member} onBack={() => { if (confirmLeaveSettings()) onMember(null); }} />
          ) : <section aria-labelledby="settings-section-title" className="g-settings-section">
            <header className="g-settings-section-head"><div><h2 id="settings-section-title" tabIndex={-1}>{current.id === 'about' ? current.label : category.label}</h2><p className="g-settings-summary">{current.summary}</p></div><span className="g-settings-scope">{['watching', 'captions', 'display'].includes(current.id) ? 'Saves automatically · Your profile' : activeSpace === 'personal' ? 'Your profile' : 'Entire household'}</span></header>
            {pages.length > 1 && current.id !== 'about' ? <>
              <nav aria-label={`${category.label} pages`} className="g-settings-pages" data-focus-row>{pages.map((next) => <a aria-current={next.id === current.id ? 'page' : undefined} className="g-settings-link g-settings-page-link" href={routePath({ surface: 'settings', section: next.section, page: next.id, space: activeSpace })} key={next.id} onClick={(event) => follow(event, next)}>{next.label}</a>)}</nav>
              <div className="g-settings-page-select"><Field label="Settings page">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => { const next = pageById(event.target.value); if (next) openPage(next); }} value={current.id}>{pages.map((next) => <option key={next.id} value={next.id}>{next.label}</option>)}</Select>}</Field></div>
            </> : null}
            <SectionRows discloseAdvanced entries={shown.entries.filter((entry) => current.rows.includes(entry.id))} essential={current.essential} groups={current.groups} key={`${current.section}:${current.id}`} section={shown} />
          </section>}
        </div>
      </div>
    </SettingsHostContext.Provider>
  );
}
