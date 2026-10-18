import type { SettingsSectionId } from '../../app/routes';

export type SettingsSpace = 'personal' | 'server';
export type SettingsCategory = 'profile' | 'playback' | 'display' | 'discovery' | 'downloads' | 'privacy' | 'overview' | 'household' | 'library' | 'connections' | 'ai' | 'maintenance';
export type SettingsPage = {
  id: string;
  category: SettingsCategory;
  section: SettingsSectionId;
  label: string;
  summary: string;
  rows: readonly string[];
  /** These setup fields belong in the main form even though the old registry marked them advanced. */
  essential?: readonly string[];
  groups?: ReadonlyArray<{ label: string; rows: readonly string[] }>;
};

/** Stable registry rows keep their controls and providers; navigation gives each exactly one home. */
export const SETTINGS_PAGES: readonly SettingsPage[] = [
  { id: 'profile', category: 'profile', section: 'account', label: 'Profile', summary: 'Your name and household identity.', rows: ['account.profile'] },
  { id: 'allowances', category: 'profile', section: 'account', label: 'Your allowances', summary: 'Review remote playback and AI work allowances that apply to you.', rows: ['account.resources'] },
  { id: 'security', category: 'profile', section: 'account', label: 'Security', summary: 'Your password and two-step verification.', rows: ['account.password', 'account.two-factor'] },
  { id: 'notifications', category: 'profile', section: 'account', label: 'Notifications', summary: 'Email updates about your requests.', rows: ['account.request-notifications'] },
  { id: 'watching', category: 'playback', section: 'playback', label: 'Watching', summary: 'Quality, autoplay and sound for your profile.', rows: ['playback.autoplay', 'playback.max-quality', 'playback.loudness', 'playback.stream-cache'] },
  { id: 'captions', category: 'playback', section: 'playback', label: 'Subtitles', summary: 'Preview and adjust your caption appearance.', rows: ['playback.captions'] },
  { id: 'skipping', category: 'playback', section: 'playback', label: 'Skipping & language', summary: 'Automatic skips and words to mute during playback.', rows: ['playback.auto-skip', 'playback.mute', 'playback.mute-words'] },
  { id: 'display', category: 'display', section: 'appearance', label: 'Display', summary: 'Choose how Lumina looks for you.', rows: ['appearance.theme'] },
  { id: 'interests', category: 'discovery', section: 'discovery', label: 'Interests', summary: 'Choose what you want to see more of.', rows: ['discovery.interests'] },
  { id: 'hidden', category: 'discovery', section: 'discovery', label: 'Hidden recommendations', summary: 'Restore recommendations and channels you have hidden.', rows: ['discovery.hidden'] },
  { id: 'recommendation-history', category: 'discovery', section: 'discovery', label: 'History', summary: 'Review or clear your recommendation history.', rows: ['discovery.history'] },
  { id: 'providers', category: 'discovery', section: 'streaming', label: 'Providers', summary: 'Choose your streaming providers.', rows: ['streaming.providers'] },
  { id: 'downloads', category: 'downloads', section: 'downloads', label: 'Downloads', summary: 'Quality, format and destination for your new saves.', rows: ['downloads.defaults'] },
  { id: 'data', category: 'privacy', section: 'privacy', label: 'Your data', summary: 'Export your data and manage your search history.', rows: ['privacy.export', 'privacy.search-history'] },
  { id: 'watch-history', category: 'privacy', section: 'privacy', label: 'Import watch history', summary: 'Preview your watched state from Jellyfin before importing it.', rows: ['privacy.jellyfin-history'] },
  { id: 'listening-export', category: 'playback', section: 'playback', label: 'Listening history export', summary: 'Optionally connect your Last.fm or ListenBrainz account.', rows: ['playback.listening-export'] },
  { id: 'apps', category: 'privacy', section: 'apps', label: 'Connected apps', summary: 'Review players and tokens that can access your account.', rows: ['apps.connected'] },
  { id: 'about', category: 'privacy', section: 'about', label: 'About Lumina', summary: 'Credits for the details and artwork Lumina shows.', rows: ['about.credits'] },
  { id: 'dashboard', category: 'overview', section: 'overview', label: 'Dashboard', summary: 'Library health and download activity across the vault.', rows: ['overview.status'] },
  { id: 'playing', category: 'overview', section: 'activity', label: 'Now playing', summary: 'Active streams, server load and background work.', rows: ['activity.now', 'activity.server', 'activity.background'] },
  { id: 'playback-history', category: 'overview', section: 'activity', label: 'Playback history', summary: 'What members played, and on which devices.', rows: ['activity.history'] },
  { id: 'members', category: 'household', section: 'members', label: 'Members', summary: 'Open a member to manage their libraries and viewing limits.', rows: ['members.list'] },
  { id: 'resources', category: 'household', section: 'members', label: 'Resources', summary: 'Set upload protection and household defaults for playback and work.', rows: ['members.resources'] },
  { id: 'invitations', category: 'household', section: 'members', label: 'Invitations', summary: 'Choose access before inviting someone to your household.', rows: ['members.invitations'] },
  { id: 'request-rules', category: 'household', section: 'requests', label: 'Request rules', summary: 'Approval and request limits for the household and individual members.', rows: ['requests.enabled', 'requests.policies', 'requests.member-policies'] },
  { id: 'household-security', category: 'household', section: 'members', label: 'Security', summary: 'Require two-step verification for vault owners.', rows: ['members.owner-two-factor'] },
  { id: 'member-import', category: 'household', section: 'members', label: 'Import members', summary: 'Bring selected accounts and watch history over from Jellyfin.', rows: ['members.jellyfin'] },
  { id: 'folders', category: 'library', section: 'library', label: 'Folders', summary: 'Media folders and destinations for new downloads.', rows: ['library.storage'] },
  { id: 'imports', category: 'library', section: 'library', label: 'Imports', summary: 'Bring in existing media without moving your files.', rows: ['library.imports'] },
  { id: 'automation', category: 'library', section: 'library', label: 'Automation', summary: 'Folder watching, scans and recording cleanup.', rows: ['library.automation', 'library.retention'], essential: ['library.automation', 'library.retention'] },
  { id: 'organization', category: 'library', section: 'library', label: 'Organization', summary: 'Anime folders, editing permissions and artwork preparation.', rows: ['library.anime', 'library.editing', 'library.artwork'], essential: ['library.anime', 'library.artwork'] },
  { id: 'matching', category: 'library', section: 'media', label: 'Fix matches', summary: 'Identify movies and shows that could not be matched.', rows: ['media.unmatched'] },
  { id: 'metadata', category: 'connections', section: 'media', label: 'Metadata', summary: 'Movie details, artwork and intro timing.', rows: ['media.tmdb-key', 'media.language', 'media.introdb', 'media.refresh-all'] },
  { id: 'players', category: 'connections', section: 'media', label: 'Media apps', summary: 'Let compatible players such as Infuse connect.', rows: ['media.jellyfin', 'media.jellyfin-address'] },
  { id: 'addresses', category: 'connections', section: 'members', label: 'Addresses', summary: 'The public and local addresses used by apps and invitations.', rows: ['members.public-address', 'members.local-address'] },
  { id: 'sonarr', category: 'connections', section: 'requests', label: 'Sonarr', summary: 'Connect downloads for requested shows and anime.', rows: ['requests.sonarr', 'requests.anime-language'] },
  { id: 'radarr', category: 'connections', section: 'requests', label: 'Radarr', summary: 'Connect downloads for requested movies.', rows: ['requests.radarr'] },
  { id: 'integrations', category: 'connections', section: 'requests', label: 'Integrations', summary: 'Connect media services and manage acquisition activity.', rows: ['requests.integrations'] },
  { id: 'email', category: 'connections', section: 'requests', label: 'Email', summary: 'Email delivery for invitations and request updates.', rows: ['requests.smtp', 'requests.smtp-test'] },
  { id: 'jellyfin', category: 'connections', section: 'media', label: 'Jellyfin import', summary: 'Connect the Jellyfin server you want to import from.', rows: ['media.jellyfin-import'], essential: ['media.jellyfin-import'] },
  { id: 'features', category: 'ai', section: 'ai', label: 'Features', summary: 'Choose the AI features available to your household.', rows: ['ai.semantic-search', 'ai.subtitles-from-speech', 'ai.sync', 'ai.translate', 'ai.recap', 'ai.episode-summaries', 'ai.key-scenes', 'ai.smart-collection-builder', 'ai.match-tie-breaker', 'ai.mute-strong-language', 'ai.personal-recommendations'], groups: [{ label: 'Summaries & translations', rows: ['ai.translate', 'ai.recap', 'ai.episode-summaries', 'ai.key-scenes'] }, { label: 'Recommendations & library helpers', rows: ['ai.smart-collection-builder', 'ai.match-tie-breaker', 'ai.mute-strong-language', 'ai.personal-recommendations'] }] },
  { id: 'models', category: 'ai', section: 'ai', label: 'Local models', summary: 'Search and speech models that run on this server.', rows: ['ai.search-model', 'ai.speech-model', 'ai.model-threads'] },
  { id: 'assistant', category: 'ai', section: 'ai', label: 'Assistant server', summary: 'Connect a server for summaries, recaps and translations. Text is sent to this connection.', rows: ['ai.assistant-address', 'ai.assistant-model', 'ai.api-key', 'ai.assistant-test', 'ai.max-concurrency', 'ai.context-window', 'ai.embedding-model'], essential: ['ai.assistant-address', 'ai.assistant-model', 'ai.api-key', 'ai.assistant-test'] },
  { id: 'speech', category: 'ai', section: 'ai', label: 'Speech server', summary: 'An optional speech server when no local speech model is installed.', rows: ['ai.speech-address', 'ai.speech-server-model'], essential: ['ai.speech-address', 'ai.speech-server-model'] },
  { id: 'ai-activity', category: 'ai', section: 'ai', label: 'Activity', summary: 'AI availability and the progress of enrichment jobs.', rows: ['ai.status'] },
  { id: 'backups', category: 'maintenance', section: 'backups', label: 'Backups', summary: 'Create, verify and schedule copies of the vault database.', rows: ['backups.list', 'backups.schedule'] },
  { id: 'tasks', category: 'maintenance', section: 'tasks', label: 'Tasks', summary: 'Background work, failures, cancellation and retries.', rows: ['tasks.list'], essential: ['tasks.list'] },
  { id: 'updates', category: 'maintenance', section: 'diagnostics', label: 'Updates', summary: 'Check releases and choose how vault owners are notified.', rows: ['diagnostics.updates'] },
  { id: 'diagnostics', category: 'maintenance', section: 'diagnostics', label: 'Diagnostics', summary: 'Versions and a redacted health report for troubleshooting.', rows: ['diagnostics.report'], essential: ['diagnostics.report'] },
  { id: 'transcoding', category: 'maintenance', section: 'transcoding', label: 'Video conversion', summary: 'Hardware acceleration and limits for video your devices cannot play directly.', rows: ['transcoding.hwaccel', 'transcoding.max-sessions', 'transcoding.cache', 'transcoding.diagnostics'], essential: ['transcoding.hwaccel', 'transcoding.max-sessions', 'transcoding.cache', 'transcoding.diagnostics'] },
  { id: 'download-limits', category: 'maintenance', section: 'overview', label: 'Download limits', summary: 'Server limits for downloads, disk space and source ports.', rows: ['overview.concurrency', 'overview.per-member', 'overview.min-free', 'overview.source-ports'], essential: ['overview.concurrency', 'overview.per-member', 'overview.min-free', 'overview.source-ports'] },
];

export const SETTINGS_CATEGORIES: ReadonlyArray<{ id: SettingsCategory; space: SettingsSpace; label: string; summary: string; page: string }> = [
  { id: 'profile', space: 'personal', label: 'Profile & security', summary: 'Your identity, sign-in and notifications.', page: 'profile' },
  { id: 'playback', space: 'personal', label: 'Playback', summary: 'Quality, sound, subtitles and automatic skips.', page: 'watching' },
  { id: 'display', space: 'personal', label: 'Display', summary: 'How Lumina looks for you.', page: 'display' },
  { id: 'discovery', space: 'personal', label: 'Discovery & streaming', summary: 'Recommendations, interests and streaming providers.', page: 'interests' },
  { id: 'downloads', space: 'personal', label: 'Downloads', summary: 'Quality and format for your new saves.', page: 'downloads' },
  { id: 'privacy', space: 'personal', label: 'Privacy & connected apps', summary: 'Your data, history and app access.', page: 'data' },
  { id: 'overview', space: 'server', label: 'Overview & activity', summary: 'Server health and household playback.', page: 'dashboard' },
  { id: 'household', space: 'server', label: 'Household', summary: 'Members, invitations and request rules.', page: 'members' },
  { id: 'library', space: 'server', label: 'Library & storage', summary: 'Folders, imports and library upkeep.', page: 'folders' },
  { id: 'connections', space: 'server', label: 'Connections', summary: 'Media services, request downloads and email.', page: 'metadata' },
  { id: 'ai', space: 'server', label: 'AI features', summary: 'Features, local models and external servers.', page: 'features' },
  { id: 'maintenance', space: 'server', label: 'Maintenance', summary: 'Backups, tasks, diagnostics and tuning.', page: 'backups' },
];

export const pageById = (id: string) => SETTINGS_PAGES.find((page) => page.id === id);
export const pageForRow = (id: string) => SETTINGS_PAGES.find((page) => page.rows.includes(id));
const SECTION_DEFAULTS: Partial<Record<SettingsSectionId, string>> = { media: 'metadata', requests: 'sonarr' };
export const resolveSettingsPage = (section: SettingsSectionId, id?: string | null) => SETTINGS_PAGES.find((page) => page.section === section && page.id === id) ?? SETTINGS_PAGES.find((page) => page.section === section && (!SECTION_DEFAULTS[section] || page.id === SECTION_DEFAULTS[section]))!;
export const categoryForPage = (page: SettingsPage) => SETTINGS_CATEGORIES.find((category) => category.id === page.category)!;
