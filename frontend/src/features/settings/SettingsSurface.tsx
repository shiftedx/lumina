import { type KeyboardEvent, type MouseEvent, useEffect, useRef, useState } from 'react';

import { routePath, type SettingsSectionId } from '../../app/routes';
import { Button, Field, fieldProps, Input, Masthead } from '../../ui';
import { MemberPage } from '../admin/MemberPage';
import { moveFocus } from '../media/focusNav';
import { isAdvancedSection, SETTINGS_GROUPS, shownEntries, visibleSections } from './registry';
import { SectionRows, SettingSwitch } from './SettingRow';
import { SettingsSearchResults } from './SettingsSearch';
import { SettingsHostContext, type SettingsSurfaceProps } from './settingsHost';
import type { SettingsSectionDef } from './settingsTypes';
import { confirmLeaveSettings } from './unsavedChanges';
import './settings.css';

export type SettingsShellProps = SettingsSurfaceProps & {
  /** The open section; null is bare /settings: the Settings home (phones: the section list). */
  section?: SettingsSectionId | null;
  onSection?: (section: SettingsSectionId | null) => void;
  /** The open member page under Members (/settings/members/<id>), or null for the list. */
  member?: string | null;
  onMember?: (member: string | null) => void;
};

/** What arrow keys move between in each column. The phone-only "All settings" button is left out: it is hidden wider than 680 px. */
const NAV_TARGETS = '.g-settings-search input, .g-settings-advanced input, .g-settings-link';
const PANE_TARGETS = ['.g-settings-card-link', '.g-member-nav .g-button', '.g-member-nav [aria-selected="true"]', ...['button', 'a[href]', 'input', 'select', 'textarea', 'summary'].map((tag) => `.setting-row ${tag}:not(:disabled)`), '.setting-form-bar button:not(:disabled)', '.g-save-bar button:not(:disabled)'].join(', ');
/** Single-line fields and selects give Up/Down back to the D-pad; Left/Right still edit. Textareas keep every arrow. */
const VERTICAL_EXIT = 'input:not([type="checkbox"]):not([type="radio"]), select';
/** Controls that own Left for themselves, so Left never jumps back to the sidebar from inside them. */
const OWNS_LEFT = 'input:not([type="checkbox"]), textarea, select, [role="radiogroup"], [role="tablist"]';

/**
 * One Settings: sidebar on desktop and TV, list then detail on phones (CSS, keyed by data-view).
 * A member following a Server link sees a note, never a Server section or an admin request.
 */
export function SettingsSurface({ section = null, onSection = () => undefined, member = null, onMember = () => undefined, ...host }: SettingsShellProps) {
  // Remembered per member in ui_prefs when the app passes it; a bare shell (tests) keeps it for the visit.
  const [ownAdvanced, setOwnAdvanced] = useState(false);
  const advanced = host.advanced ?? ownAdvanced;
  function changeAdvanced(next: boolean) {
    // Hiding rows could unmount a draft, so unsaved edits are confirmed first.
    if (!next && !confirmLeaveSettings()) return;
    (host.onAdvancedChange ?? setOwnAdvanced)(next);
  }
  const allowed = visibleSections(host.user);
  const shown = section ? allowed.find((entry) => entry.id === section) : undefined;
  // All-advanced sections leave the sidebar and home while advanced is off; one opened by a link stays listed.
  const sections = allowed.filter((entry) => advanced || !isAdvancedSection(entry) || entry === shown);
  const navRef = useRef<HTMLElement>(null);
  const paneRef = useRef<HTMLDivElement>(null);

  const [query, setQuery] = useState('');
  // A section change from outside (browser Back, already confirmed by LuminaApp) ends the search too.
  const [seenSection, setSeenSection] = useState(section);
  if (section !== seenSection) { setSeenSection(section); setQuery(''); }
  const searching = query.trim() !== '';
  function changeQuery(next: string) {
    // Starting or ending a search swaps the pane, so unsaved edits there are confirmed first.
    if ((next.trim() !== '') !== searching && !confirmLeaveSettings()) return;
    setQuery(next);
  }

  function choose(next: SettingsSectionId | null): boolean {
    if (next === section && !searching && !member) return true;
    if (!confirmLeaveSettings()) return false;
    setQuery('');
    if (member) onMember(null);
    if (next !== section) onSection(next);
    return true;
  }
  // A followed link can unmount (home card) or hide (phone list) with focus on it; land on the opened heading instead of <body>.
  const refocus = useRef(false);
  useEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    const active = document.activeElement;
    if (!active || active === document.body || !active.isConnected) document.getElementById('settings-section-title')?.focus();
  }, [section]);
  function follow(event: MouseEvent<HTMLAnchorElement>, id: SettingsSectionId) {
    // Plain clicks stay in the app; modified clicks keep native open-in-new-tab behaviour.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    // Only a section change re-runs the effect that consumes the flag; the current section's own link must not leave it set.
    refocus.current = choose(id) && id !== section;
  }

  function onNavKeyDown(event: KeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement;
    if (event.key === 'Enter' && target.matches('input[type="checkbox"]')) { event.preventDefault(); target.click(); return; }
    moveFocus(event, { targets: NAV_TARGETS, verticalExit: VERTICAL_EXIT });
    if (event.defaultPrevented || event.key !== 'ArrowRight' || (target.matches('input') && !target.matches('[type="checkbox"]'))) return;
    const first = paneRef.current?.querySelector<HTMLElement>(PANE_TARGETS);
    if (first) { event.preventDefault(); first.focus(); }
  }

  function onPaneKeyDown(event: KeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement;
    // Keys from a portal (the Fix match dialog) bubble here through React; they are not the pane's to move.
    if (!paneRef.current?.contains(target)) return;
    // A TV remote's OK is Enter; native checkboxes and radios toggle only on Space.
    if (event.key === 'Enter' && target.matches('input[type="checkbox"], input[type="radio"]')) { event.preventDefault(); target.click(); return; }
    moveFocus(event, { targets: PANE_TARGETS, verticalExit: VERTICAL_EXIT });
    if (event.defaultPrevented || event.key !== 'ArrowLeft' || target.closest(OWNS_LEFT)) return;
    const link = navRef.current?.querySelector<HTMLElement>('.g-settings-link[aria-current="page"]') ?? navRef.current?.querySelector<HTMLElement>('.g-settings-link');
    if (link) { event.preventDefault(); link.focus(); }
  }

  return (
    <SettingsHostContext.Provider value={{ ...host, advanced, onAdvancedChange: changeAdvanced, showAdvanced: advanced || searching, onOpenSection: choose, onOpenMember: (id) => { if (id === null && !confirmLeaveSettings()) return; onMember(id); } }}>
      <div className="g-settings" data-view={section || searching ? 'detail' : 'list'}>
        <Masthead title="Settings" />
        <nav aria-label="Settings sections" className="g-settings-sidebar" onKeyDown={onNavKeyDown} ref={navRef}>
          <div className="g-settings-search" role="search">
            <Field hideLabel label="Search settings">
              {(ids) => <Input {...fieldProps(ids)} autoComplete="off" onChange={(event) => changeQuery(event.target.value)} placeholder="Search settings" type="search" value={query} />}
            </Field>
            <div className="g-settings-advanced"><SettingSwitch checked={advanced} label="Show advanced settings" onChange={changeAdvanced} /></div>
          </div>
          {SETTINGS_GROUPS.map(({ id: group, label: heading }) => {
            const list = sections.filter((entry) => entry.group === group);
            return list.length ? (
              <div className="g-settings-group" key={group}>
                <p aria-hidden="true" className="g-label">{heading}</p>
                <ul aria-label={heading}>
                  {list.map((entry) => (
                    <li data-focus-row key={entry.id}>
                      <a aria-current={entry === shown && !searching ? 'page' : undefined} className="g-settings-link" href={routePath({ surface: 'settings', section: entry.id })} onClick={(event) => follow(event, entry.id)}>{entry.label}</a>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null;
          })}
        </nav>
        <div className="g-settings-pane" onKeyDown={onPaneKeyDown} ref={paneRef}>
          {section || searching ? <Button className="g-settings-back" onClick={() => choose(null)} variant="quiet">All settings</Button> : null}
          {searching ? <SettingsSearchResults advanced={advanced} onReveal={() => changeAdvanced(true)} query={query} sections={allowed} /> : shown?.id === 'members' && member ? (
            <MemberPage key={member} memberId={member} onBack={() => { if (confirmLeaveSettings()) onMember(null); }} />
          ) : shown ? (
            <section aria-labelledby="settings-section-title" className="g-settings-section">
              <header className="g-settings-section-head">
                <h2 id="settings-section-title" tabIndex={-1}>{shown.label}</h2>
                <p className="g-settings-summary">{shown.summary}</p>
              </header>
              {!advanced && isAdvancedSection(shown) ? (
                <div className="g-settings-advanced-note" role="note">
                  <span>Everything here is an advanced setting, so it is hidden from the sidebar.</span>
                  <SettingSwitch checked={advanced} label="Show advanced settings" onChange={changeAdvanced} />
                </div>
              ) : null}
              <SectionRows entries={shownEntries(shown, advanced)} key={shown.id} section={shown} />
            </section>
          ) : section ? <p className="g-settings-summary" role="status">This part of Settings is for vault owners. Ask a vault owner if something on this server needs changing.</p> : <SettingsHome follow={follow} sections={sections} />}
        </div>
      </div>
    </SettingsHostContext.Provider>
  );
}

/** Bare /settings on desktop and TV (owner 2026-10-01): every visible group as a grid of section cards, like Jellyfin's dashboard home. */
function SettingsHome({ sections, follow }: { sections: readonly SettingsSectionDef[]; follow: (event: MouseEvent<HTMLAnchorElement>, id: SettingsSectionId) => void }) {
  return (
    <section aria-label="All settings" className="g-settings-home">
      {SETTINGS_GROUPS.map(({ id, label }) => {
        const list = sections.filter((entry) => entry.group === id);
        return list.length ? (
          <section aria-labelledby={`settings-home-${id}`} className="g-settings-home-group" key={id}>
            <h2 className="g-settings-home-head" id={`settings-home-${id}`}>{label}</h2>
            {/* One D-pad row per group: Left/Right stay in the group; Left from its first card returns to the sidebar. */}
            <ul className="g-settings-cards" data-focus-row>
              {list.map((entry) => (
                <li className="g-settings-card" key={entry.id}>
                  <a aria-describedby={`settings-card-${entry.id}`} className="g-settings-card-link" href={routePath({ surface: 'settings', section: entry.id })} onClick={(event) => follow(event, entry.id)}>{entry.label}</a>
                  <p className="g-settings-card-summary" id={`settings-card-${entry.id}`}>{entry.summary}</p>
                </li>
              ))}
            </ul>
          </section>
        ) : null;
      })}
    </section>
  );
}
