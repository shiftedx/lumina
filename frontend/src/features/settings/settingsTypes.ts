import type { ComponentType, ReactNode } from 'react';

import type { SettingsSectionId } from '../../app/routes';

export type SettingsGroup = 'you' | 'server' | 'advanced';

/** One row in Settings: a setting, an action, a status or a re-housed list. */
export interface SettingEntry {
  /** Stable id `<section>.<name>`; becomes data-setting-id and the DOM ids. Never reuse a removed id. */
  id: string;
  /** Short topic name, shown as the row heading. Must not contain a button name from the same section. */
  label: string;
  /** ⓘ text: 2–3 plain sentences — what it does; what it needs or costs; who it affects. */
  info: string;
  /** Extra lower-case words search matches: inner field names, synonyms. */
  keywords?: readonly string[];
  /** One plain line under the label, for what the label alone leaves out. The ⓘ keeps the longer explanation. */
  description?: string;
  /** Technical tuning: hidden until the member turns on Show advanced settings; search always finds it. */
  advanced?: true;
  /** inline: label | one control in the control column. switch: label | one SettingSwitch, side by side at every width. block: control(s) below the label. Default inline. */
  layout?: 'inline' | 'switch' | 'block';
  /** The real control. Reads useSettingsHost() or its section's context; must also work inside search results. */
  Control: ComponentType;
  /** Optional live detail under the row: download progress, an import run, a validation message. */
  Detail?: ComponentType;
}

export interface SettingsSectionDef {
  id: SettingsSectionId;
  group: SettingsGroup;
  label: string;
  /** Lower-case old labels and Jellyfin names; search (and ⌘K) match them like the section label. */
  aliases?: readonly string[];
  /** One sentence under the section title. */
  summary: string;
  entries: readonly SettingEntry[];
  /** Loads shared data or owns a Save form for the rows; wraps them in the section and in each search-result group. */
  Provider?: ComponentType<{ children: ReactNode }>;
}
