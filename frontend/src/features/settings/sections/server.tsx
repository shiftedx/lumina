import { AdminBackups, BackupSchedule } from '../../admin/AdminBackups';
import { ActivityBackground, ActivityHistory, ActivityNowPlaying, ActivityProvider, ActivityServerStats } from '../../admin/AdminActivity';
import { AdminDiagnostics } from '../../admin/AdminDiagnostics';
import { LocalAddress, MemberInvitations, MembersList, OwnerTwoFactorSwitch, PublicAddress } from '../../admin/AdminMembers';
import { AdminTasks } from '../../admin/AdminTasks';
import { JellyfinHouseholdImport } from '../JellyfinHouseholdImport';
import { useSettingsHost } from '../settingsHost';
import type { SettingsSectionDef } from '../settingsTypes';
import { AI_SECTION } from './ai';
import { LIBRARY_SECTION } from './library';
import { MEDIA_SECTIONS } from './media';
import { REQUESTS_SECTION } from './requests';
import { OVERVIEW_SECTION } from './overview';

function Members() {
  const { onOpenMember } = useSettingsHost();
  return <MembersList onOpenMember={onOpenMember} />;
}
function Invitations() {
  const { onMessage = () => undefined } = useSettingsHost();
  return <MemberInvitations onMessage={onMessage} />;
}

/** Vault-owner sections: "Server" then "Advanced", in sidebar order (owner 2026-10-01). */
export const SERVER_SECTIONS: readonly SettingsSectionDef[] = [
  OVERVIEW_SECTION,
  {
    id: 'activity', group: 'server', label: 'Activity', aliases: ['now playing', 'sessions', 'streams'], summary: 'Who is watching right now, how the server is coping, and what was played lately.', Provider: ActivityProvider,
    entries: [
      { id: 'activity.now', label: 'Now playing', layout: 'block', keywords: ['sessions', 'streams', 'stop', 'transcode', 'direct play', 'remux', 'relay', 'who is watching'], info: 'Everyone playing something through Lumina or a connected app, and how each stream is being delivered. Stopping a stream ends it and tells that member the server owner stopped it. Affects the member you stop.', Control: ActivityNowPlaying },
      { id: 'activity.server', label: 'Server load', layout: 'block', keywords: ['cpu', 'memory', 'load', 'uptime', 'ffmpeg', 'hardware', 'qsv', 'vaapi', 'encoder', 'transcoders'], info: 'Processor, memory and load on the machine, and whether hardware video encoding is working. Figures marked not available are not reported on this system. Nothing here changes a setting.', Control: ActivityServerStats },
      { id: 'activity.background', label: 'Downloads and recordings', layout: 'block', keywords: ['downloads', 'recordings', 'live recording', 'progress', 'running'], info: 'Downloads and live recordings running for any member right now, with their progress. Manage or cancel them from Tasks. Covers every member.', Control: ActivityBackground },
      { id: 'activity.history', label: 'Playback history', layout: 'block', keywords: ['history', 'played', 'watched', 'log', 'search', 'member', 'stopped by owner'], info: 'What was played, by whom and on which device, newest first, kept for 90 days. Filter by member or search by title. Covers every member.', Control: ActivityHistory },
    ],
  },
  {
    id: 'members', group: 'server', label: 'Members', aliases: ['users'], summary: 'Who can sign in to this vault, and with what access.',
    entries: [
      { id: 'members.list', label: 'Household members', layout: 'block', keywords: ['users', 'accounts', 'household members', 'role', 'vault owner', 'deactivate', 'reactivate', 'reset link', 'password reset', 'parental controls', 'libraries', 'ratings', 'age rating', 'schedule', 'bedtime', 'screen time', 'daily limit', 'youtube', 'twitch', 'kick', 'kids', 'teen', 'guest', 'presets', 'permissions', 'downloads'], info: 'Everyone who can sign in to this vault, with the libraries and limits each one has. Open a member to choose their libraries, ratings, streaming, viewing hours and permissions. Vault owners have no limits.', Control: Members },
      { id: 'members.invitations', label: 'Invitations', layout: 'block', keywords: ['invite', 'invite link', 'email', 'resend', 'revoke invitation', 'expires', 'guest'], info: 'Invite someone by email, or create a link to send yourself, with the libraries and limits they will get. Links work once and expire after 7 days; resending makes a new link and the old one stops working. Email uses the mail settings under Requests.', Control: Invitations },
      { id: 'members.public-address', label: 'Public address', keywords: ['public url', 'domain', 'https', 'outside', 'remote access', 'invite link address', 'hostname'], info: 'The https address people outside your home use to reach Lumina; invite emails link here. Leave it empty to use the address you open Lumina on. Enter just the origin, such as https://lumina.example.com.', Control: PublicAddress },
      { id: 'members.local-address', label: 'Local address', keywords: ['local url', 'lan', 'home network', 'local domain', '.local', 'home.arpa', 'reverse proxy', 'traefik', 'hostname', 'infuse at home'], info: 'A name for Lumina on your home network, such as http://lumina.home.arpa, served through your reverse proxy; it needs LAN HTTP mode. It must end in .local, .lan, .home.arpa or .internal and differ from the public address. Apps at home are shown it beside the public address.', Control: LocalAddress },
      { id: 'members.owner-two-factor', label: 'Owner two-step verification', layout: 'block', keywords: ['2fa', 'two factor', 'authenticator', 'require', 'security', 'vault owner'], info: 'Makes every vault owner sign in with a code from an authenticator app as well as a password. Owners who have not set it up are asked to before they can open the server settings, and you must turn it on for yourself first. Affects every vault owner.', Control: OwnerTwoFactorSwitch },
      { id: 'members.jellyfin', label: 'Bring members over from Jellyfin', layout: 'block', keywords: ['jellyfin', 'import', 'migrate', 'move from jellyfin', 'bring over', 'watch history', 'password link'], info: "Signs in to the household's Jellyfin server with a Jellyfin administrator account and brings the users you choose over, with what they watched, where they stopped and their favorites. People without a Lumina account join as household members with a one-time link to choose a password. Your Jellyfin password is used once and never saved.", Control: JellyfinHouseholdImport },
    ],
  },
  LIBRARY_SECTION,
  // Requests sits right after Media server and before Transcoding (the second media section).
  MEDIA_SECTIONS[0],
  REQUESTS_SECTION,
  ...MEDIA_SECTIONS.slice(1),
  AI_SECTION,
  {
    id: 'tasks', group: 'advanced', label: 'Tasks', aliases: ['scheduled tasks'], summary: 'Downloads, transcriptions and summaries running for everyone, with their real failure reasons.',
    entries: [
      { id: 'tasks.list', label: 'Background tasks', layout: 'block', advanced: true, keywords: ['downloads', 'transcriptions', 'summaries', 'jobs', 'queue', 'failed', 'retry', 'cancel', 'in progress'], info: "Downloads, transcriptions and summaries running for everyone, with their real failure reasons. You can cancel work in progress or retry a failed download. Covers every member's tasks.", Control: AdminTasks },
    ],
  },
  {
    id: 'backups', group: 'advanced', label: 'Backups', summary: 'Verifiable copies of the vault database, and how to restore one.',
    entries: [
      { id: 'backups.list', label: 'Database backups', layout: 'block', keywords: ['back up now', 'verify', 'download backup', 'delete backup', 'restore', 'sqlite', 'copy'], info: 'Copies of accounts, library records, notes, history and settings, taken while Lumina keeps running. Media files are not included, so back up your storage separately; downloading a copy asks for your password. Covers the whole server.', Control: AdminBackups },
      { id: 'backups.schedule', label: 'Automatic backups', layout: 'block', keywords: ['daily', 'every day', 'schedule', 'keep', 'retention'], info: 'Backs up the database once a day and keeps the newest copies, up to the number you choose. Manual backups stay until you delete them. Covers the whole server.', Control: BackupSchedule },
    ],
  },
  {
    id: 'diagnostics', group: 'advanced', label: 'Diagnostics', aliases: ['logs'], summary: 'Versions, runtime health and recent errors, redacted so the report is safe to share.',
    entries: [
      { id: 'diagnostics.report', label: 'Health report', layout: 'block', advanced: true, keywords: ['diagnostics', 'versions', 'runtime', 'recent errors', 'ffmpeg', 'yt-dlp', 'maintenance', 'copy report', 'support'], info: 'Versions, runtime health and recent errors, with paths, tokens and keys removed so the report is safe to share. Copy it when you ask for help. Nothing here changes a setting.', Control: AdminDiagnostics },
    ],
  },
];
