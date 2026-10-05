import type { SettingsSectionId } from '../../app/routes';
import type { UserProfile } from '../../types';
import { SERVER_SECTIONS } from './sections/server';
import { YOU_SECTIONS } from './sections/you';
import type { SettingEntry, SettingsGroup, SettingsSectionDef } from './settingsTypes';

/** The one typed list of every Settings row. Sections render from it; search reads it. */
export const SETTINGS_SECTIONS: readonly SettingsSectionDef[] = [...YOU_SECTIONS, ...SERVER_SECTIONS];
/** Jellyfin-familiar groups, in sidebar order (owner 2026-10-01). Only 'you' is visible to members. */
export const SETTINGS_GROUPS: ReadonlyArray<{ id: SettingsGroup; label: string }> = [{ id: 'you', label: 'You' }, { id: 'server', label: 'Server' }, { id: 'advanced', label: 'Advanced' }];
export const groupLabel = (group: SettingsGroup) => SETTINGS_GROUPS.find((entry) => entry.id === group)?.label ?? '';

export const sectionById = (id: SettingsSectionId): SettingsSectionDef | undefined => SETTINGS_SECTIONS.find((section) => section.id === id);

/** Server sections exist only for vault owners; hiding them is a courtesy, every admin API enforces the role. */
export const visibleSections = (user: Pick<UserProfile, 'role'>, sections: readonly SettingsSectionDef[] = SETTINGS_SECTIONS) =>
  sections.filter((section) => section.group === 'you' || user.role === 'admin');

/** A section made only of technical tuning: off the sidebar and home until Show advanced settings is on. */
export const isAdvancedSection = (section: SettingsSectionDef) => section.entries.every((entry) => entry.advanced);

/** The rows a section shows: every row with advanced on, or when the whole section is advanced (a deep link still works). */
export const shownEntries = (section: SettingsSectionDef, advanced: boolean) =>
  advanced || isAdvancedSection(section) ? section.entries : section.entries.filter((entry) => !entry.advanced);

export type SettingMatch = { section: SettingsSectionDef; entries: SettingEntry[] };

/** Every word of the query must appear in the row's label, ⓘ text, keywords or section name (case-insensitive). */
export function searchSettings(query: string, sections: readonly SettingsSectionDef[]): SettingMatch[] {
  const words = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return sections
    .map((section) => ({
      section,
      entries: section.entries.filter((entry) => {
        const text = [entry.label, entry.info, section.label, ...(section.aliases ?? []), ...(entry.keywords ?? [])].join(' ').toLocaleLowerCase();
        return words.every((word) => text.includes(word));
      }),
    }))
    .filter((match) => match.entries.length > 0);
}
