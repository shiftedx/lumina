import { AccountPassword } from '../../../AccountPassword';
import type { AutoSkipPref } from '../../../types';
import { PLAYBACK_MAX_HEIGHTS } from '../../../workspace';
import { TMDB_ATTRIBUTION } from '../../titles/titleModel';
import { CaptionStyleRows } from '../../watch/CaptionStyleRows';
import { Avatar, Button, ButtonLink, Field, fieldProps, SegmentedControl, Select } from '../../../ui';
import { SettingSwitch } from '../SettingRow';
import { RequestNotifications } from './requestsNotifications';
import { ConnectedApps } from '../ConnectedApps';
import { TwoFactorSettings } from '../TwoFactorSettings';
import { JellyfinHistoryImport } from '../JellyfinHistoryImport';
import { useSettingsHost } from '../settingsHost';
import type { SettingsSectionDef } from '../settingsTypes';
import {
  DataExportCard, DEFAULT_REMOTE_PLAYBACK_CACHE, InterestSettingsCard, MUTE_COPY, MuteWords, PresetFallback, RecoHistoryCard, ProfileNameSettings,
  RemotePlaybackCacheSettings, SearchHistorySettingsCard, SKIP_LABELS, StreamingProvidersCard, SuppressionSettingsCard,
} from '../youCards';

function Profile() {
  const { user, onUpdateDisplayName, onLogout } = useSettingsHost();
  const name = user.display_name || user.username;
  return (
    <div className="g-subrows">
      <div className="g-subrow setting-profile-identity">
        <span className="setting-profile-who"><Avatar decorative name={name} size={48} /><span className="g-list-row-copy"><strong>{name}</strong><small>@{user.username} · {user.role === 'admin' ? 'Vault owner' : 'Family member'}</small></span></span>
        {onLogout ? <div className="g-actions"><Button onClick={onLogout} variant="secondary">Sign out</Button></div> : null}
      </div>
      {onUpdateDisplayName ? <ProfileNameSettings onSave={onUpdateDisplayName} user={user} /> : null}
    </div>
  );
}

function Autoplay() {
  const { autoplayUpNext, onAutoplayUpNextChange: change } = useSettingsHost();
  return change ? <SettingSwitch checked={Boolean(autoplayUpNext)} onChange={change} /> : null;
}

function MaxQuality() {
  const { playbackMaxHeight, onPlaybackMaxHeightChange: change } = useSettingsHost();
  if (!change) return null;
  return (
    <Field hideLabel label="Maximum quality">{(ids) => (
      <Select {...fieldProps(ids)} onChange={(event) => change(PLAYBACK_MAX_HEIGHTS.find((height) => String(height) === event.currentTarget.value) ?? null)} value={playbackMaxHeight ?? ''}>
        <option value="">Best available</option>
        {PLAYBACK_MAX_HEIGHTS.map((height) => <option key={height} value={height}>{height === 480 ? 'Data saver (up to 480p)' : `Up to ${height}p`}</option>)}
      </Select>
    )}</Field>
  );
}

function Loudness() {
  const { playbackPrefs: prefs, onPlaybackPrefsChange: change } = useSettingsHost();
  return prefs && change ? <SettingSwitch checked={prefs.normalizeLoudness} onChange={(normalizeLoudness) => change({ normalizeLoudness })} /> : null;
}

function AutoSkip() {
  const { playbackPrefs: prefs, onPlaybackPrefsChange: change } = useSettingsHost();
  if (!prefs || !change) return null;
  return (
    <fieldset className="g-fieldset"><legend className="sr-only">Skip automatically</legend>
      {(Object.keys(SKIP_LABELS) as Array<keyof AutoSkipPref>).map((key) => <SettingSwitch checked={prefs.autoSkip[key]} key={key} label={SKIP_LABELS[key]} onChange={(next) => change({ autoSkip: { ...prefs.autoSkip, [key]: next } })} />)}
    </fieldset>
  );
}

function Mute() {
  const { playbackPrefs: prefs, onPlaybackPrefsChange: change } = useSettingsHost();
  return prefs && change ? <SettingSwitch checked={prefs.profanity.enabled} onChange={(enabled) => change({ profanity: { ...prefs.profanity, enabled } })} /> : null;
}

function MuteList() {
  const { playbackPrefs: prefs, onPlaybackPrefsChange: change } = useSettingsHost();
  return prefs && change ? <MuteWords onChange={(profanity) => change({ profanity })} value={prefs.profanity} /> : null;
}

function StreamCache() {
  const { remotePlaybackCache, onRemotePlaybackCacheChange } = useSettingsHost();
  return <RemotePlaybackCacheSettings onChange={onRemotePlaybackCacheChange ?? (() => undefined)} value={remotePlaybackCache ?? DEFAULT_REMOTE_PLAYBACK_CACHE} />;
}

function Theme() {
  const { theme = 'system', onThemeChange } = useSettingsHost();
  return <SegmentedControl hideLegend legend="Theme" onChange={(value) => onThemeChange?.(value)} options={[{ value: 'system', label: 'System' }, { value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }]} value={theme} />;
}

function CaptionsControl() {
  const { captions, onCaptionsChange } = useSettingsHost();
  if (!captions || !onCaptionsChange) return null; // wired in LuminaApp
  return (
    <div className="g-subrows">
      <CaptionStyleRows onChange={onCaptionsChange} value={captions} />
      <p className="g-setting-note">On iPhone and iPad, full-screen captions follow the system's caption style.</p>
    </div>
  );
}

function Interests() {
  const { interests, onSaveInterests } = useSettingsHost();
  return interests && onSaveInterests ? <InterestSettingsCard interests={interests} onSave={onSaveInterests} /> : null;
}

function Hidden() {
  const { suppressions, onRestoreSuppression } = useSettingsHost();
  return suppressions && onRestoreSuppression ? <SuppressionSettingsCard onRestore={onRestoreSuppression} suppressions={suppressions} /> : null;
}

function RecoHistory() {
  const { onMessage } = useSettingsHost();
  return <RecoHistoryCard onMessage={onMessage} />;
}

function DownloadDefaults() {
  const { acquisitionDefaults, formatPreset = 'best', onFormatChange } = useSettingsHost();
  return <>{acquisitionDefaults ?? (onFormatChange ? <PresetFallback formatPreset={formatPreset} onFormatChange={onFormatChange} /> : null)}</>;
}

function Apps() {
  const { user } = useSettingsHost();
  return <ConnectedApps user={user} />;
}

function SearchHistory() {
  const { searchHistory, onClearSearchHistory } = useSettingsHost();
  return searchHistory && onClearSearchHistory ? <SearchHistorySettingsCard history={searchHistory} onClear={onClearSearchHistory} /> : null;
}

function Credits() {
  return (
    <div className="g-subrows">
      <div className="g-subrow">
        <p className="g-setting-note">{TMDB_ATTRIBUTION}</p>
        <div className="g-actions"><ButtonLink href="https://www.themoviedb.org/" rel="noreferrer noopener" target="_blank" variant="secondary">Visit TMDB</ButtonLink></div>
      </div>
      <div className="g-subrow">
        <p className="g-setting-note">Lumina is built on yt-dlp, FFmpeg (jellyfin-ffmpeg), llama.cpp and faster-whisper. Anime data comes from AniList and intro markers from TheIntroDB.</p>
        <div className="g-actions"><ButtonLink href="https://github.com/shiftedx/lumina/blob/main/CREDITS.md" rel="noreferrer noopener" target="_blank" variant="secondary">All credits</ButtonLink></div>
      </div>
    </div>
  );
}

/** "You" sections, in sidebar order (owner 2026-10-01: Jellyfin's Profile, Display, Home, Playback…). ⓘ copy is product copy: change it only with the owner. */
export const YOU_SECTIONS: readonly SettingsSectionDef[] = [
  {
    id: 'account', group: 'you', label: 'Profile', aliases: ['account'], summary: 'Your name, sign-in and password.',
    entries: [
      { id: 'account.profile', label: 'Profile', layout: 'block', keywords: ['display name', 'name', 'sign out', 'log out', 'account', 'role'], info: 'Your name as the rest of the household sees it, and signing out of this browser. A new name shows everywhere in Lumina straight away. Affects only you.', Control: Profile },
      { id: 'account.password', label: 'Password', layout: 'block', keywords: ['change password', 'current password', 'new password', 'security', 'sign in'], info: 'Changes the password you sign in with. You need your current password, and the new one must have at least 12 characters; your other devices are signed out. Affects only you.', Control: AccountPassword },
      { id: 'account.two-factor', label: 'Two-step verification', layout: 'block', keywords: ['2fa', 'two factor', 'authenticator', 'totp', 'recovery codes', 'security', 'sign in', 'app password'], info: 'Asks for a 6-digit code from an authenticator app, as well as your password, when you sign in. Turning it on shows recovery codes once, so keep them safe; apps like Infuse use an app password instead. Affects only you.', Control: TwoFactorSettings },
      { id: 'account.request-notifications', label: 'Request notifications', layout: 'block', keywords: ['requests', 'email', 'notify', 'notifications', 'sonarr', 'radarr', 'approved', 'available'], info: 'Emails you when a movie, show or anime you asked for is approved, declined, ready to watch or fails. It needs an email address, and Lumina sends only through the mail server the owner set up. Affects only you.', Control: RequestNotifications },
    ],
  },
  {
    id: 'appearance', group: 'you', label: 'Display', aliases: ['appearance'], summary: 'How Lumina looks for you.',
    entries: [
      { id: 'appearance.theme', label: 'Theme', keywords: ['dark mode', 'light mode', 'system', 'colours', 'colors'], info: "Chooses light or dark colours for Lumina. System follows your device's own light or dark setting. Affects only you, on every device you sign in to.", Control: Theme },
    ],
  },
  {
    id: 'discovery', group: 'you', label: 'Home & discovery', summary: 'What shapes your Home page and recommendations.',
    entries: [
      { id: 'discovery.interests', label: 'Your interests', layout: 'block', keywords: ['home', 'recommendations', 'categories', 'personalize', 'topics'], info: 'Broad topics that shape the picks on your Home page. Choosing them does not follow creators, download anything or change who can see your Library. Affects only you.', Control: Interests },
      { id: 'discovery.hidden', label: 'Hidden recommendations', layout: 'block', keywords: ['not interested', 'suppressed', 'restore', 'do not recommend', 'channel', 'show fewer', 'fewer', 'titles'], info: 'Videos, titles and channels you asked Lumina to stop recommending, and channels you asked to see less of for a while. Restoring one only lets it appear in future picks again; it never follows a channel or brings back removed media. Affects only you.', Control: Hidden },
      { id: 'discovery.history', label: 'Recommendation history', layout: 'block', keywords: ['clear', 'forget', 'reset', 'impressions', 'what was shown', 'recommendations'], info: 'What Lumina has recorded about the recommendations it showed you and which ones you opened. Clearing it starts your picks over without touching your watch history or hidden lists. Affects only you.', Control: RecoHistory },
    ],
  },
  {
    id: 'streaming', group: 'you', label: 'Streaming', summary: 'Which live and video providers you see.',
    entries: [
      { id: 'streaming.providers', label: 'Providers', layout: 'block', keywords: ['twitch', 'kick', 'youtube', 'live', 'channels', 'hide'], info: 'Chooses which providers show up in Streaming, Home\'s live row and search results. YouTube is always on; turning Twitch or Kick off only hides them, nothing is deleted. Affects only you.', Control: StreamingProvidersCard },
    ],
  },
  {
    id: 'playback', group: 'you', label: 'Playback', summary: 'How video plays for you on this profile.',
    entries: [
      { id: 'playback.autoplay', label: 'Autoplay next video', layout: 'switch', keywords: ['autoplay', 'up next', 'continuous play'], info: 'Starts the next related video when the one you are watching ends. Turn it off to stop at the end of each video. Affects only you.', Control: Autoplay },
      { id: 'playback.max-quality', label: 'Streaming quality', keywords: ['maximum quality', 'resolution', '1080p', '720p', '480p', 'data saver', 'bandwidth'], info: 'The highest quality Lumina picks automatically when a video streams. A quality you choose in the player still wins, and downloads are not affected. Lower it to save data; affects only you.', Control: MaxQuality },
      { id: 'playback.loudness', label: 'Even out loudness', layout: 'switch', keywords: ['sound', 'loudness', 'volume', 'normalize', 'quiet', 'loud'], info: 'Keeps quiet films and loud episodes at a similar volume. Lumina adjusts the sound in your browser as it plays, and the file never changes. Affects only you.', Control: Loudness },
      {
        id: 'playback.captions',
        label: 'Captions',
        info: "Sets how large captions are and what sits behind them. Changes apply to every video you watch in Lumina. On iPhone and iPad, full-screen captions follow the system's caption style.",
        keywords: ['subtitles', 'size', 'background', 'shadow', 'box'],
        layout: 'block',
        Control: CaptionsControl,
      },
      { id: 'playback.auto-skip', label: 'Skip automatically', layout: 'block', keywords: ['skip intros', 'skip recaps', 'skip credits', 'intro', 'recap', 'credits'], info: 'Skips intros, recaps or credits where Lumina knows them. A short notice lets you undo each skip. Affects only you.', Control: AutoSkip },
      { id: 'playback.mute', label: 'Mute strong language', layout: 'switch', keywords: ['profanity', 'swearing', 'curse words', 'filter'], info: MUTE_COPY, Control: Mute },
      { id: 'playback.mute-words', label: 'Extra words to mute', layout: 'block', keywords: ['profanity', 'swearing', 'curse words', 'your extra words', 'word list', 'names', 'mute strong language'], info: 'Words or names Lumina mutes on top of the common strong language it already knows. Add one per line, and end a word with * to match its endings. They apply while Mute strong language is on, and affect only you.', Control: MuteList },
      { id: 'playback.stream-cache', label: 'Recent stream cache', layout: 'block', advanced: true, keywords: ['cache', 'resume', 'recent videos kept ready', 'maximum storage', 'faster start'], info: 'Keeps a few recently watched streams ready so returning to where you stopped starts faster. Choose how many videos and how much space it may use; the oldest leaves first and everything expires after 7 days. Your watch progress is saved either way, and this affects only you.', Control: StreamCache },
    ],
  },
  {
    id: 'downloads', group: 'you', label: 'Downloads', summary: 'What “Download to vault” saves for you.',
    entries: [
      { id: 'downloads.defaults', label: 'Download defaults', layout: 'block', keywords: ['default quality', 'container', 'mp4', 'webm', 'mkv', 'subtitles', 'folder inside the library', 'format', 'preset', 'acquisition defaults'], info: "The quality, file format and subtitles used when you save a video to the vault. Files go to your own folder inside the server's Library, which the vault owner controls. Affects only your downloads.", Control: DownloadDefaults },
    ],
  },
  {
    id: 'apps', group: 'you', label: 'Connected apps', aliases: ['devices', 'quick connect'], summary: 'Players and tools that reach the vault as you.',
    entries: [
      { id: 'apps.connected', label: 'Connected apps', layout: 'block', keywords: ['infuse', 'swiftfin', 'jellyfin', 'devices', 'agent token', 'api token', 'revoke', 'home assistant'], info: "Players like Infuse, and tools you gave a token, that reach the vault as you. A token sees only what you can see and is shown once when you create it; revoke anything you no longer use. Vault owners also see everyone's apps here.", Control: Apps },
    ],
  },
  {
    id: 'privacy', group: 'you', label: 'Privacy & data', summary: 'Your own data: a copy to keep, history to bring over, and your search history.',
    entries: [
      { id: 'privacy.export', label: 'Data export', keywords: ['export my data', 'download my data', 'json', 'portable', 'copy'], info: 'Downloads a JSON file of your own preferences, follows, watch queue, notes, search history and playback progress. Nothing is shared or synced; it is a private copy for you.', Control: DataExportCard },
      { id: 'privacy.jellyfin-history', label: 'Watch history from Jellyfin', layout: 'block', keywords: ['import', 'jellyfin', 'migrate', 'move from jellyfin', 'watched', 'resume', 'favorites', 'preview', 'import history'], info: "Brings what you watched, where you stopped and your favorites over from the household's Jellyfin server, for the movies and episodes Lumina also has. Preview shows what would change first; nothing you watched more recently in Lumina is overwritten, and your Jellyfin password is used once and never saved. Affects only you.", Control: JellyfinHistoryImport },
      { id: 'privacy.search-history', label: 'Search history', keywords: ['recent searches', 'clear', 'suggestions'], info: 'Your recent searches, used for search suggestions. Clearing removes them all, and you can also remove one from the suggestions list. Affects only you.', Control: SearchHistory },
    ],
  },
  {
    id: 'about', group: 'you', label: 'About', summary: 'Credits for the details and artwork Lumina shows.',
    entries: [
      { id: 'about.credits', label: 'Credits', layout: 'block', keywords: ['yt-dlp', 'ffmpeg', 'anilist', 'tmdb', 'the movie database', 'attribution', 'artwork', 'licence', 'license'], info: 'Where movie and show details and artwork come from. Lumina uses TMDB for them when the vault owner adds a TMDB key. Nothing here changes a setting.', Control: Credits },
    ],
  },
];
