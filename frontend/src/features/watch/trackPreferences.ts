import type { AudioTrack, SubtitleTrack } from '../../types';

export type TrackPreferences = { audioLanguages: string[]; subtitleLanguages: string[]; subtitlesEnabled: boolean; forcedOnly: boolean; preferSdh: boolean };
export const DEFAULT_TRACK_PREFERENCES: TrackPreferences = { audioLanguages: [], subtitleLanguages: [], subtitlesEnabled: false, forcedOnly: false, preferSdh: false };

export function trackLanguage(value: string | null | undefined): string | null {
  try { return value ? new Intl.Locale(value.trim().replaceAll('_', '-')).language : null; } catch { return null; }
}

export function trackPreferences(value: unknown): TrackPreferences {
  const stored = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const languages = (key: string) => Array.isArray(stored[key]) ? [...new Set((stored[key] as unknown[]).filter((entry): entry is string => typeof entry === 'string' && entry.length <= 32).map(trackLanguage).filter((entry): entry is string => Boolean(entry)))].slice(0, 10) : [];
  return { audioLanguages: languages('audioLanguages'), subtitleLanguages: languages('subtitleLanguages'), subtitlesEnabled: stored.subtitlesEnabled === true, forcedOnly: stored.forcedOnly === true, preferSdh: stored.preferSdh === true };
}

export function preferredAudio(tracks: readonly AudioTrack[], prefs: TrackPreferences): number | null {
  for (const language of prefs.audioLanguages) {
    const match = tracks.find((track) => trackLanguage(track.language) === trackLanguage(language));
    if (match) return match.index;
  }
  return null;
}

export function preferredSubtitle(tracks: readonly SubtitleTrack[], prefs: TrackPreferences): SubtitleTrack | null {
  if (!prefs.subtitlesEnabled) return null;
  const candidates = tracks.filter((track) => !prefs.forcedOnly || track.forced);
  const languages = prefs.subtitleLanguages.length ? prefs.subtitleLanguages : [null];
  for (const language of languages) {
    const matches = candidates.filter((track) => language === null || trackLanguage(track.language) === trackLanguage(language));
    matches.sort((a, b) => Number(b.hearing_impaired === prefs.preferSdh) - Number(a.hearing_impaired === prefs.preferSdh) || Number(b.default) - Number(a.default) || Number(a.format === 'image') - Number(b.format === 'image'));
    if (matches.length) return matches[0];
  }
  return null;
}
