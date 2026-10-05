import { createContext, type ReactNode, useContext } from 'react';

import type { SettingsSectionId } from '../../app/routes';
import type { AcquisitionFormat } from '../../mediaAcquisition';
import type { ThemePreference } from '../../theme';
import type { MemberInterests, RemotePlaybackCachePreferences, SearchHistoryEntry, SuppressionList, UserProfile } from '../../types';
import type { CaptionPrefs } from '../watch/captionPrefs';
import type { PlaybackMaxHeight, PlaybackPrefs } from '../../workspace';

/** What Settings rows read from the app shell. Only `user` is always present; a row whose callback is absent renders no control. */
export interface SettingsHost {
  user: UserProfile;
  onMessage?: (message: string) => void;
  theme?: ThemePreference;
  onThemeChange?: (value: ThemePreference) => void;
  formatPreset?: string;
  onFormatChange?: (value: AcquisitionFormat) => void;
  onLogout?: () => void;
  onUpdateDisplayName?: (displayName: string) => Promise<void>;
  acquisitionDefaults?: ReactNode;
  autoplayUpNext?: boolean;
  onAutoplayUpNextChange?: (value: boolean) => void;
  playbackMaxHeight?: PlaybackMaxHeight;
  onPlaybackMaxHeightChange?: (value: PlaybackMaxHeight) => void;
  playbackPrefs?: PlaybackPrefs;
  onPlaybackPrefsChange?: (patch: Partial<PlaybackPrefs>) => void;
  remotePlaybackCache?: RemotePlaybackCachePreferences;
  onRemotePlaybackCacheChange?: (value: RemotePlaybackCachePreferences) => Promise<void> | void;
  interests?: MemberInterests | null;
  onSaveInterests?: (keys: string[]) => Promise<void>;
  suppressions?: SuppressionList | null;
  onRestoreSuppression?: (id: string) => void;
  searchHistory?: SearchHistoryEntry[];
  onClearSearchHistory?: () => void;
  /** Caption style; passed in from LuminaApp. */
  captions?: CaptionPrefs;
  onCaptionsChange?: (next: CaptionPrefs) => void;
  /** The member's Show advanced settings choice (`ui_prefs.settings_advanced`), and how to change it. */
  advanced?: boolean;
  onAdvancedChange?: (on: boolean) => void;
  /** Set by the Settings shell: true while advanced rows (and advanced parts of a row) are on screen. Absent means show everything. */
  showAdvanced?: boolean;
  /** Clears the search and opens a section; false when the person keeps unsaved edits instead. Set by the Settings shell. */
  onOpenSection?: (section: SettingsSectionId) => boolean;
  /** Opens a member page under Members (or the list, with null). Set by the Settings shell. */
  onOpenMember?: (memberId: string | null) => void;
}

/** The props LuminaApp passes to Settings; the same set the old SettingsSurface took. */
export type SettingsSurfaceProps = SettingsHost & { formatPreset: string; onFormatChange: (value: AcquisitionFormat) => void; onLogout: () => void };

export const SettingsHostContext = createContext<SettingsHost | null>(null);

export function useSettingsHost(): SettingsHost {
  const host = useContext(SettingsHostContext);
  if (!host) throw new Error('Settings rows render inside SettingsHostContext.');
  return host;
}
