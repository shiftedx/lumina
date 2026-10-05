import { Button } from '../../ui';
import { searchSettings } from './registry';
import { SectionRows } from './SettingRow';
import type { SettingsSectionDef } from './settingsTypes';
import { useDirtyLabels } from './unsavedChanges';

/**
 * Search results: grouped by section; each row is the real control inside its section's Provider,
 * so it can be changed here. A section with unsaved edits shows all its rows, and stays (pinned) until saved or
 * discarded, so typing never unmounts its Provider or a row that owns a draft. Advanced rows are always found, tagged,
 * with an offer to keep them on screen.
 */
export function SettingsSearchResults({ query, sections, advanced = true, onReveal }: { query: string; sections: readonly SettingsSectionDef[]; advanced?: boolean; onReveal?: () => void }) {
  const dirty = useDirtyLabels();
  const matches = searchSettings(query, sections);
  const count = matches.reduce((total, match) => total + match.entries.length, 0);
  const groups = sections.flatMap((section) => {
    const match = matches.find((entry) => entry.section === section);
    // A dirty section keeps all its rows: some rows own their draft (storage rules), so narrowing must not unmount them.
    if (dirty.includes(section.label)) return [{ section, entries: section.entries, pinned: !match }];
    return match ? [{ section, entries: match.entries, pinned: false }] : [];
  });
  return (
    <section aria-label="Search results" className="settings-results">
      <p className="settings-section-summary" role="status">{count ? `${count} ${count === 1 ? 'setting matches' : 'settings match'}` : 'No settings match'}</p>
      {!advanced && onReveal && matches.some((match) => match.entries.some((entry) => entry.advanced)) ? (
        <p className="g-settings-advanced-note" role="note">Some of these are advanced settings, hidden outside search. <Button onClick={onReveal} variant="quiet">Show advanced settings</Button></p>
      ) : null}
      {groups.map(({ section, entries, pinned }) => (
        <section aria-labelledby={`settings-results-${section.id}`} className="settings-result-group" key={section.id}>
          <h2 id={`settings-results-${section.id}`}>{section.label}</h2>
          {pinned ? <p className="settings-pinned-note" role="note">Unsaved changes. This section stays here until you save or discard.</p> : null}
          <SectionRows entries={entries} section={section} />
        </section>
      ))}
    </section>
  );
}
