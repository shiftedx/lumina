import { type MouseEvent } from 'react';
import { ChevronRight } from '../../luminaIcons';

import { routePath } from '../../app/routes';
import { categoryForPage, pageForRow, type SettingsPage } from './navigation';
import { searchSettings } from './registry';
import type { SettingsSectionDef } from './settingsTypes';

/** Search locates settings without mounting unrelated forms or starting owner-only requests. */
export function SettingsSearchResults({ query, sections, onOpen }: { query: string; sections: readonly SettingsSectionDef[]; onOpen: (page: SettingsPage, row: string) => boolean }) {
  const matches = searchSettings(query, sections);
  const count = matches.reduce((total, match) => total + match.entries.length, 0);
  function follow(event: MouseEvent<HTMLAnchorElement>, page: SettingsPage, row: string) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    onOpen(page, row);
  }
  return (
    <section aria-label="Search results" className="settings-results">
      <p className="settings-section-summary" role="status">{count ? `${count} ${count === 1 ? 'setting matches' : 'settings match'}` : 'No settings match'}</p>
      <p className="g-setting-note">Open a setting to change it on its own page.</p>
      <ul className="g-settings-results-list">
        {matches.flatMap(({ entries }) => entries.map((entry) => {
          const page = pageForRow(entry.id)!;
          const category = categoryForPage(page);
          return <li key={entry.id}>
            <a className="g-settings-result-link" href={`${routePath({ surface: 'settings', section: page.section, page: page.id, space: category.space })}#setting-${entry.id.replace(/[^A-Za-z0-9-]/g, '-')}`} onClick={(event) => follow(event, page, entry.id)}>
              <span><strong>{entry.label}</strong><small>{category.space === 'personal' ? 'My Settings' : 'Manage Server'} · {category.label} · {page.label}</small><span>{entry.description ?? entry.info.split(/(?<=[.!?])\s/)[0]}</span></span>
              <ChevronRight aria-hidden="true" />
            </a>
          </li>;
        }))}
      </ul>
    </section>
  );
}
