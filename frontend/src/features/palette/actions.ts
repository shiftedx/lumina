import { Compass, Download, FolderPlus, Home, Image as ImageIcon, Library, ListVideo, LogOut, Monitor, Moon, Pencil, PanelLeft, Plus, Radio, RefreshCw, Rss, SlidersHorizontal, Sparkles, Sun, Tv, Users, type LucideIcon } from 'lucide-react';
import { openMemberPicker, requestHomeEdit } from '../../app/commands';
import type { ThemePreference } from '../../theme';
import type { ScanLibrariesResult, UserProfile } from '../../types';
import { matchesQuery } from './paletteModel';

export interface PaletteContext {
  user: UserProfile;
  mobile: boolean;
  sidebarCollapsed: boolean;
  theme: ThemePreference;
  navigate: (path: string) => void;
  setTheme: (theme: ThemePreference) => void;
  toggleSidebar: () => void;
  signOut: () => void;
  openLinkMode: () => void;
  /** The title on screen (title pages only; set by LuminaApp). */
  titleId?: string;
  /** The open title's type when known; only movies, series, seasons and episodes have an editor. */
  titleType?: string;
  /** Library scans (vault owners; filled by CommandPalette). */
  scanLibraries?: (rootId?: string) => void;
  scanRoots?: readonly { id: string; label: string }[];
}
export interface PaletteAction {
  id: string;
  label: string;
  keywords: string[];
  icon: LucideIcon;
  hint?: string;
  /** The page a navigation action opens. That action is the palette's only row for this destination. */
  path?: string;
  when: (ctx: PaletteContext) => boolean;
  run: (ctx: PaletteContext) => void;
}

const always = () => true;
const EDITABLE = ['movie', 'series', 'season', 'episode'];
const canEdit = (ctx: PaletteContext) => Boolean(ctx.titleId && ctx.user.can_edit_details && ctx.titleType && EDITABLE.includes(ctx.titleType));
const isAdmin = (ctx: PaletteContext) => ctx.user.role === 'admin';
const theme = (value: ThemePreference, label: string, icon: LucideIcon): PaletteAction => ({
  id: `theme-${value}`, label, keywords: ['theme', 'appearance', 'mode', value], icon, when: always, run: (ctx) => ctx.setTheme(value),
});

/** In display order. Labels that depend on state are resolved by filterActions. */
export const PALETTE_ACTIONS: readonly PaletteAction[] = [
  { id: 'add-link', label: 'Add a link…', keywords: ['paste', 'url', 'download', 'save'], icon: Plus, when: always, run: (ctx) => ctx.openLinkMode() },
  { id: 'go-downloads', label: 'Open downloads', keywords: ['activity', 'queue', 'vault'], icon: Download, when: always, path: '/downloads', run: (ctx) => ctx.navigate('/downloads') },
  { id: 'switch-member', label: 'Switch member…', keywords: ['who', 'watching', 'profile', 'account', 'user'], icon: Users, when: always, run: () => openMemberPicker() },
  theme('system', 'Use system theme', Monitor),
  theme('light', 'Use light theme', Sun),
  theme('dark', 'Use dark theme', Moon),
  { id: 'edit-home', label: 'Edit Home', keywords: ['shelves', 'rows', 'arrange', 'reorder'], icon: Home, when: always, run: (ctx) => { ctx.navigate('/'); requestHomeEdit(); } },
  { id: 'new-collection', label: 'New collection…', keywords: ['group', 'list'], icon: FolderPlus, when: always, run: (ctx) => ctx.navigate('/library/collections?new=collection') },
  { id: 'new-smart-collection', label: 'New smart collection…', keywords: ['rules', 'automatic'], icon: Sparkles, when: always, run: (ctx) => ctx.navigate('/library/collections?new=smart') },
  { id: 'toggle-sidebar', label: 'Collapse sidebar', keywords: ['sidebar', 'navigation', 'menu'], icon: PanelLeft, when: (ctx) => !ctx.mobile, run: (ctx) => ctx.toggleSidebar() },
  { id: 'settings-playback', label: 'Playback settings', keywords: ['captions', 'subtitles', 'quality', 'skip'], icon: Tv, when: always, path: '/settings/playback', run: (ctx) => ctx.navigate('/settings/playback') },
  // Live, YouTube and reco destinations.
  { id: 'go-streaming', label: 'Streaming', keywords: ['explore', 'discover', 'browse', 'popular', 'for you', 'recommended', 'youtube', 'twitch', 'kick', 'search'], icon: Compass, when: always, path: '/streaming', run: (ctx) => ctx.navigate('/streaming') },
  { id: 'go-live', label: 'Live now', keywords: ['live', 'streams', 'twitch', 'kick', 'youtube live', 'upcoming'], icon: Radio, when: always, path: '/streaming/live', run: (ctx) => ctx.navigate('/streaming/live') },
  { id: 'go-subscriptions', label: 'Your channels', keywords: ['subscriptions', 'follows', 'following', 'channels', 'subscribed'], icon: Rss, when: always, path: '/streaming/channels', run: (ctx) => ctx.navigate('/streaming/channels') },
  { id: 'go-channels', label: 'Open saved channels', keywords: ['channels', 'youtube channels', 'library', 'creators'], icon: ListVideo, when: always, path: '/library/youtube?view=channels', run: (ctx) => ctx.navigate('/library/youtube?view=channels') },
  { id: 'settings-discovery', label: 'Home & discovery', keywords: ['recommendations', 'picked for you', 'not interested', 'hidden channels', 'suppression', 'interests', 'personalised'], icon: SlidersHorizontal, when: always, path: '/settings/discovery', run: (ctx) => ctx.navigate('/settings/discovery') },
  { id: 'edit-title', label: 'Edit details…', keywords: ['edit', 'metadata', 'fix', 'rename', 'poster', 'artwork', 'genres', 'tags', 'lock'], icon: Pencil, when: canEdit, run: (ctx) => ctx.navigate(`/title/${encodeURIComponent(ctx.titleId!)}/edit`) },
  { id: 'edit-title-artwork', label: 'Change artwork…', keywords: ['poster', 'backdrop', 'logo', 'image', 'upload'], icon: ImageIcon, when: canEdit, run: (ctx) => ctx.navigate(`/title/${encodeURIComponent(ctx.titleId!)}/edit?tab=artwork`) },
  { id: 'scan-libraries', label: 'Scan libraries now', keywords: ['rescan', 'scan', 'refresh library', 'new media', 'new episodes', 'import'], icon: RefreshCw, when: isAdmin, run: (ctx) => ctx.scanLibraries?.() },
  // The one row for this page: schedule, watch and who-can-edit controls. Scanning is the scan action's (and per-root rows').
  { id: 'settings-library', label: 'Library settings', keywords: ['import', 'folders', 'library', 'storage', 'schedule', 'watch', 'editing', 'who can edit', 'metadata', 'members edit'], icon: Library, when: isAdmin, path: '/settings/library', run: (ctx) => ctx.navigate('/settings/library') },
  { id: 'sign-out', label: 'Sign out', keywords: ['log out', 'leave'], icon: LogOut, when: always, run: (ctx) => ctx.signOut() },
];
export const PINNED_ACTION_IDS = ['add-link', 'go-downloads', 'switch-member', 'edit-home', 'new-collection'] as const;

const scanRoot = (root: { id: string; label: string }): PaletteAction => ({
  id: `scan-root-${root.id}`, label: `Scan ${root.label} now`, keywords: [root.label, 'scan', 'rescan'], icon: RefreshCw, when: isAdmin, run: (ctx) => ctx.scanLibraries?.(root.id),
});

export function scanSummary({ started, queued, skipped }: ScanLibrariesResult): string {
  const count = started.length + queued.length;
  if (count) return `Scanning ${count} ${count === 1 ? 'library' : 'libraries'}${skipped.length ? ` · ${skipped.length} unavailable` : ''}`;
  return skipped.every((entry) => entry.reason === 'needs_first_import') ? 'No libraries to scan. Import a folder first.' : 'Those libraries are already scanning or unavailable.';
}

function resolved(action: PaletteAction, ctx: PaletteContext): PaletteAction {
  if (action.id === 'toggle-sidebar') return { ...action, label: ctx.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar' };
  if (action.id === `theme-${ctx.theme}`) return { ...action, hint: 'Current' };
  return action;
}

/** The empty query gives the pinned five (in pinned order); otherwise every allowed action whose label or keywords match. */
export function filterActions(query: string, ctx: PaletteContext): PaletteAction[] {
  const allowed = PALETTE_ACTIONS.filter((action) => action.when(ctx)).map((action) => resolved(action, ctx));
  if (!query.trim()) return PINNED_ACTION_IDS.map((id) => allowed.find((action) => action.id === id)).filter((action): action is PaletteAction => Boolean(action));
  const rootRows = isAdmin(ctx) ? (ctx.scanRoots ?? []).map(scanRoot) : [];
  return [...allowed, ...rootRows].filter((action) => matchesQuery(action.label, query, action.keywords));
}
