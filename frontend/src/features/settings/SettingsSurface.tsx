import { type KeyboardEvent, type MouseEvent, useEffect, useRef, useState } from 'react';
import { CircleUserRound, Compass, Download, FolderOpen, Gauge, Palette, Play, Plug, ShieldCheck, Sparkles, Users, Wrench } from '../../luminaIcons';

import { routePath, type SettingsSectionId } from '../../app/routes';
import { Field, fieldProps, Input, Masthead } from '../../ui';
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
const PANE_TARGETS = ['.g-settings-page-link', '.g-settings-result-link', '.g-member-nav .g-button', '.g-member-nav [aria-selected="true"]', ...['button', 'a[href]', 'input', 'select', 'textarea', 'summary'].map((tag) => `.setting-row ${tag}:not(:disabled)`), '.setting-form-bar button:not(:disabled)', '.g-save-bar button:not(:disabled)'].join(', ');
const VERTICAL_EXIT = 'input:not([type="checkbox"]):not([type="radio"]), select';
const OWNS_LEFT = 'input:not([type="checkbox"]), textarea, select, [role="combobox"], [role="radiogroup"], [role="tablist"]';
const CATEGORY_ICONS = { profile: CircleUserRound, playback: Play, display: Palette, discovery: Compass, downloads: Download, privacy: ShieldCheck, overview: Gauge, household: Users, library: FolderOpen, connections: Plug, ai: Sparkles, maintenance: Wrench } satisfies Record<SettingsCategory, typeof Play>;
/** Each area is one continuous workspace. Stable page routes are in-page destinations. */
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
  const categories = SETTINGS_CATEGORIES.filter((category) => host.user.role === 'admin' || category.space === 'personal');
  const current = requestedPage ?? pageById(activeSpace === 'server' ? 'dashboard' : 'watching')!;
  const category = categoryForPage(current);
  const pages = current.id === 'about' ? [current] : SETTINGS_PAGES.filter((entry) => entry.category === category.id && entry.id !== 'about');
  const rows = pages.flatMap((entry) => entry.rows);
  const sections = [...new Set(pages.map((entry) => entry.section))].map((id) => sectionById(id)!);
  const allowed = visibleSections(host.user);
  const navRef = useRef<HTMLElement>(null);
  const paneRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  const searching = query.trim() !== '';
  const locationKey = `${effectiveSection}:${(local ? local.page : page)}:${activeSpace}`;
  const [seenLocation, setSeenLocation] = useState(locationKey);
  if (locationKey !== seenLocation) { setSeenLocation(locationKey); setQuery(''); }
  const bookmark = window.location.hash.slice(1);
  const pendingFocus = useRef<string | null>(bookmark.startsWith('setting-') || bookmark.startsWith('settings-group-') ? bookmark : page && requestedPage ? `settings-group-${requestedPage.id}` : null);

  function choose(next: SettingsLocation, row?: string): boolean {
    const sameArea = next.section && categoryForPage(resolveSettingsPage(next.section, next.page)).id === category.id && (next.page === 'about') === (current.id === 'about');
    if ((!sameArea || searching || member) && !confirmLeaveSettings()) return false;
    const target = row ?? 'settings-section-title';
    if (next.page === current.id && !searching && !member) {
      const element = document.getElementById(target.startsWith('settings-') ? target : settingDomId(target));
      element?.scrollIntoView?.({ block: 'start', behavior: 'instant' });
      element?.focus();
      return true;
    }
    pendingFocus.current = target;
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
    const row = pendingFocus.current ?? (bookmark.startsWith('setting-') || bookmark.startsWith('settings-group-') ? bookmark : null);
    if (!row || searching) return;
    const waitingFrom = document.activeElement;
    const focus = () => {
      if (document.activeElement !== waitingFrom && document.activeElement !== document.body) { pendingFocus.current = null; return true; }
      const heading = row.startsWith('settings-');
      const target = document.getElementById(heading || row.startsWith('setting-') ? row : settingDomId(row));
      if (!target || (!heading && !paneRef.current?.contains(target))) return false;
      const details = target.closest('details');
      if (details) details.open = true;
      const controls = target.querySelector('.g-setting-control') ?? target;
      const control = controls.querySelector<HTMLElement>('input:not(:disabled), select:not(:disabled):not([hidden]), textarea:not(:disabled)') ?? controls.querySelector<HTMLElement>('button:not(:disabled), a[href]');
      (heading ? target : control ?? target).focus();
      target.scrollIntoView?.({ block: 'nearest', behavior: 'instant' });
      pendingFocus.current = null;
      return true;
    };
    if (focus()) return;
    // Providers load asynchronously; focus once the searched row is present.
    const observer = new MutationObserver(() => { if (focus()) observer.disconnect(); });
    if (paneRef.current) observer.observe(paneRef.current, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [locationKey, searching, member, bookmark]);

  function firstNavControl(selected = false) {
    return (selected ? navRef.current?.querySelector<HTMLElement>('.g-settings-link[aria-current="page"]') : null) ?? navRef.current?.querySelector<HTMLElement>('.g-settings-link');
  }
  function onNavKeyDown(event: KeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement;
    moveFocus(event, { targets: NAV_TARGETS, verticalExit: VERTICAL_EXIT });
    if (event.defaultPrevented || event.key !== 'ArrowRight' || target.matches('input')) return;
    const first = paneRef.current?.querySelector<HTMLElement>(PANE_TARGETS);
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
      if (control) { event.preventDefault(); control.focus(); control.scrollIntoView?.({ block: 'nearest', behavior: 'instant' }); return true; }
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
    moveFocus(event, { targets: PANE_TARGETS, verticalExit: VERTICAL_EXIT });
    if (event.defaultPrevented || event.key !== 'ArrowLeft' || target.closest(OWNS_LEFT)) return;
    const link = firstNavControl(true);
    if (link) { event.preventDefault(); link.focus(); }
  }

  return (
    <SettingsHostContext.Provider value={{ ...host, showAdvanced: true, visibleSettingIds: rows, onOpenSection: (id) => openPage(resolveSettingsPage(id)), onOpenSetting: (id) => { const next = pageForRow(id); return next ? openPage(next, id) : false; }, onOpenMember: (id) => { if (!confirmLeaveSettings()) return; onMember(id); } }}>
      <div className="g-settings" data-view="detail" data-space={activeSpace}>
        <header className="g-settings-header"><Masthead title="Settings" /><p className="g-settings-welcome">A little more your way.</p></header>
        <div className="g-settings-search" role="search" onKeyDown={(event) => { if (event.key === 'ArrowDown') { const first = searching ? paneRef.current?.querySelector<HTMLElement>('.g-settings-result-link') : firstNavControl(); if (first) { event.preventDefault(); first.focus(); } } }}>
          <Field hideLabel label="Search settings">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" onChange={(event) => changeQuery(event.target.value)} placeholder="Find any setting" type="search" value={query} />}</Field>
        </div>
        <div className="g-settings-navigation">
        <nav aria-label="Settings sections" className="g-settings-sidebar" onKeyDown={onNavKeyDown} ref={navRef}>
          {(['personal', 'server'] as const).filter((scope) => scope === 'personal' || host.user.role === 'admin').map((scope) => <div className="g-settings-nav-group" key={scope}>
            <h2>{scope === 'personal' ? 'For you' : 'Your household'}</h2>
            <ul>{categories.filter((entry) => entry.space === scope).map((entry) => {
              const next = pageById(entry.page)!;
              const Icon = CATEGORY_ICONS[entry.id];
              return <li data-focus-row key={entry.id}><a aria-label={entry.label} aria-current={!forbidden && !searching && !member && current.id !== 'about' && category.id === entry.id ? 'page' : undefined} className="g-settings-link" href={routePath({ surface: 'settings', section: next.section, page: next.id, space: scope })} onClick={(event) => follow(event, next)}><Icon aria-hidden="true" /><span>{entry.label}</span></a></li>;
            })}</ul>
          </div>)}
          <a aria-current={current.id === 'about' ? 'page' : undefined} className="g-settings-link g-settings-about" href="/settings/about" onClick={(event) => follow(event, pageById('about')!)}>About Lumina</a>
        </nav>
        </div>
        <div className="g-settings-pane" onKeyDown={onPaneKeyDown} onKeyDownCapture={onPaneKeyDownCapture} ref={paneRef}>
          {searching ? <SettingsSearchResults onOpen={openPage} query={query} sections={allowed} /> : forbidden ? <p className="g-settings-summary" role="status">This part of Settings is for vault owners. Ask a vault owner if something on this server needs changing.</p> : member ? (
            <MemberPage key={member} memberId={member} onBack={() => { if (confirmLeaveSettings()) { pendingFocus.current = 'settings-group-members'; onMember(null); } }} />
          ) : <section aria-labelledby="settings-section-title" className="g-settings-section">
            <header className="g-settings-section-head"><div><p className="g-label">{activeSpace === 'personal' ? 'For you' : 'Your household'}</p><h2 id="settings-section-title" tabIndex={-1}>{current.id === 'about' ? current.label : category.label}</h2><p className="g-settings-summary">{current.id === 'about' ? current.summary : category.summary}</p></div><span className="g-settings-scope">{category.id === 'playback' || category.id === 'display' ? 'Saves automatically' : activeSpace === 'personal' ? 'Your profile' : 'Applies to everyone'}</span></header>
            {pages.length > 1 ? <nav aria-label="Jump to setting group" className="g-settings-jumps" data-focus-row>{pages.map((next) => <a className="g-settings-page-link" href={`#settings-group-${next.id}`} key={next.id} onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault(); const target = document.getElementById(`settings-group-${next.id}`); target?.focus(); target?.scrollIntoView?.({ block: 'start', behavior: 'instant' });
            }}>{next.label}</a>)}</nav> : null}
            {sections.map((shown) => <SectionRows entries={shown.entries.filter((entry) => rows.includes(entry.id))} key={`${category.id}:${shown.id}`} pages={pages.filter((entry) => entry.section === shown.id)} section={shown} />)}
          </section>}
        </div>
      </div>
    </SettingsHostContext.Provider>
  );
}
