import { del, post, put, requestJson } from '../../api';
import type { ActivityHistoryRow, UserProfile } from '../../types';
import { spanLabel } from './activityModel';

/** Admin API and rules for member access. */
export const MOVIE_RATINGS = ['G', 'PG', 'PG-13', 'R', 'NC-17'] as const;
export const TV_RATINGS = ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA'] as const;
export type MovieRating = (typeof MOVIE_RATINGS)[number];
export type TvRating = (typeof TV_RATINGS)[number];
export const WEEKDAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']] as const;
export type Weekday = (typeof WEEKDAYS)[number][0];
export type TimeRange = [string, string];
/** null = any time; a day with no ranges (or missing) = no watching that day. */
export type Schedule = Partial<Record<Weekday, TimeRange[]>>;
export type StreamingKind = 'youtube' | 'twitch' | 'kick' | 'live' | 'open_search';
export type StreamingLimits = Record<StreamingKind, boolean> & { followed_only: boolean };

/** The editable part of a member's access: the body of PUT /api/admin/members/{id}/access. */
export interface MemberAccess {
  sections: string[] | null;
  movie_rating_max: MovieRating | null;
  tv_rating_max: TvRating | null;
  unrated: 'allow' | 'hide';
  streaming: StreamingLimits;
  schedule: Schedule | null;
  daily_limit_minutes: number | null;
  can_download: boolean;
}
/** GET /api/admin/members/{id}/access (and the bonus response): the access plus today's figures. */
export type MemberAccessState = MemberAccess & { screen_time_today_seconds?: number; bonus_minutes_today?: number };
export type Section = { id: string; label: string; count: number };
export type MemberActivity = { screen_time_today_seconds: number; recent: ActivityHistoryRow[] };
/** /api/admin/users rows; members carry their access summary (null = all libraries, no limits). */
export type MemberRow = UserProfile & { access?: MemberAccess | null; screen_time_today_seconds?: number; sign_in_locked?: boolean };

export const NO_LIMITS: MemberAccess = {
  sections: null, movie_rating_max: null, tv_rating_max: null, unrated: 'allow',
  streaming: { youtube: true, twitch: true, kick: true, live: true, open_search: true, followed_only: false },
  schedule: null, daily_limit_minutes: null, can_download: true,
};

/** Just the editable fields of a server object, defaults filled in (missing streaming keys are allowed). */
export const withDefaults = (access: Partial<MemberAccess> | null | undefined): MemberAccess => {
  const pick = Object.fromEntries((Object.keys(NO_LIMITS) as (keyof MemberAccess)[]).map((key) => [key, access?.[key] ?? NO_LIMITS[key]])) as unknown as MemberAccess;
  return { ...pick, sections: access?.sections === undefined ? null : access.sections, streaming: { ...NO_LIMITS.streaming, ...access?.streaming } };
};

const everyDay = (from: string, to: string): Schedule => Object.fromEntries(WEEKDAYS.map(([day]) => [day, [[from, to]]]));
const allStreaming = (on: boolean): StreamingLimits => ({ youtube: on, twitch: on, kick: on, live: on, open_search: on, followed_only: false });

export type PresetId = 'kids' | 'teen' | 'guest';
/** Presets fill limits and downloads; they never change which libraries are shared. */
export const PRESETS: Record<PresetId, { label: string; hint: string; access: Omit<MemberAccess, 'sections'> }> = {
  kids: { label: 'Kids', hint: 'G and TV-Y7, no streaming, 7:00–20:00, 2 h a day', access: { movie_rating_max: 'G', tv_rating_max: 'TV-Y7', unrated: 'hide', streaming: allStreaming(false), schedule: everyDay('07:00', '20:00'), daily_limit_minutes: 120, can_download: false } },
  teen: { label: 'Teen', hint: 'PG-13 and TV-14, streaming on, 7:00–22:00, 4 h a day', access: { movie_rating_max: 'PG-13', tv_rating_max: 'TV-14', unrated: 'allow', streaming: { ...allStreaming(true), kick: false }, schedule: everyDay('07:00', '22:00'), daily_limit_minutes: 240, can_download: true } },
  guest: { label: 'Guest', hint: 'Shared libraries only: no streaming or downloads', access: { movie_rating_max: null, tv_rating_max: null, unrated: 'allow', streaming: allStreaming(false), schedule: null, daily_limit_minutes: null, can_download: false } },
};
export const applyPreset = (access: MemberAccess, preset: PresetId): MemberAccess => ({ ...structuredClone(PRESETS[preset].access), sections: access.sections });

const SECTION_LABELS: Record<string, string> = { movies: 'Movies', shows: 'TV shows', anime: 'Anime', music: 'Music', vault: 'Videos and downloads' };
export const sectionLabel = (id: string, sections?: readonly Section[] | null) => sections?.find((entry) => entry.id === id)?.label ?? SECTION_LABELS[id] ?? id;

export function librariesSummary(access: MemberAccess, sections?: readonly Section[] | null): string {
  if (access.sections === null) return 'All libraries';
  if (!access.sections.length) return 'No libraries';
  return access.sections.map((id) => sectionLabel(id, sections)).join(', ');
}

/** "1 h", "2 h 30 min", "45 min". */
export const minutesLabel = (minutes: number) => (minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ''}`);

/** The quiet limits line on a member row: "PG · TV-Y7 · no YouTube · 2 h/day"; empty when nothing is limited. */
export function limitsSummary(access: MemberAccess): string {
  const streaming = access.streaming;
  const blocked = (['youtube', 'twitch', 'kick'] as const).filter((kind) => !streaming[kind]);
  const parts = [
    access.movie_rating_max,
    access.tv_rating_max,
    access.unrated === 'hide' ? 'unrated hidden' : null,
    blocked.length === 3 ? 'no streaming' : blocked.map((kind) => `no ${STREAMING_NAMES[kind]}`).join(' · ') || null,
    streaming.followed_only ? 'followed channels only' : null,
    access.schedule ? 'set hours' : null,
    access.daily_limit_minutes ? `${minutesLabel(access.daily_limit_minutes)}/day` : null,
    access.can_download ? null : 'no downloads',
  ];
  return parts.filter(Boolean).join(' · ');
}
const STREAMING_NAMES = { youtube: 'YouTube', twitch: 'Twitch', kick: 'Kick' } as const;

export const screenTimeLabel = (seconds: number | undefined) => (seconds ? `${spanLabel(seconds)} today` : 'Nothing watched today');

/** The first problem in a schedule, or null: every range needs a start before its end. */
export function scheduleError(schedule: Schedule | null): string | null {
  if (!schedule) return null;
  for (const [day, name] of WEEKDAYS) {
    for (const [from, to] of schedule[day] ?? []) {
      if (!/^\d\d:\d\d$/.test(from) || !/^\d\d:\d\d$/.test(to)) return `${name}: enter both times.`;
      if (from >= to) return `${name}: each start time must be before its end time.`;
    }
  }
  return null;
}

/** Monday's hours on Tuesday to Friday. */
export const copyMondayToWeekdays = (schedule: Schedule): Schedule =>
  ({ ...schedule, ...Object.fromEntries((['tue', 'wed', 'thu', 'fri'] as const).map((day) => [day, structuredClone(schedule.mon ?? [])])) });

const enc = encodeURIComponent;
export const getMemberAccess = (id: string): Promise<MemberAccessState> => requestJson(`/api/admin/members/${enc(id)}/access`);
export const putMemberAccess = (id: string, access: MemberAccess): Promise<MemberAccessState> => put(`/api/admin/members/${enc(id)}/access`, access);
export const addBonusMinutes = (id: string, minutes: number): Promise<MemberAccessState> => post(`/api/admin/members/${enc(id)}/access/bonus`, { minutes });
export const getMemberActivity = (id: string): Promise<MemberActivity> => requestJson(`/api/admin/members/${enc(id)}/activity`);
/** Channel follows an admin keeps for a member (#166): the way past “Followed channels only”. */
export type MemberFollow = { id: string; label: string; source_url: string };
export const listMemberFollows = (id: string): Promise<MemberFollow[]> => requestJson(`/api/admin/members/${enc(id)}/follows`);
export const addMemberFollow = (id: string, source_url: string): Promise<MemberFollow[]> => post(`/api/admin/members/${enc(id)}/follows`, { source_url });
export const removeMemberFollow = (id: string, followId: string): Promise<void> => del(`/api/admin/members/${enc(id)}/follows/${enc(followId)}`);
export const listSections = (): Promise<Section[]> => requestJson('/api/admin/sections');

/** Email invites: what the invitee gets on redeem. */
export type InviteAccess = MemberAccess & { can_request: boolean };
export interface Invite {
  id: string; email: string | null; status: 'pending' | 'used' | 'redeemed' | 'revoked' | 'expired';
  created_at: string; expires_at: string; sent_at: string | null; libraries?: string[]; access?: Partial<InviteAccess> & { display_name?: string | null };
}
export type IssuedInvite = Invite & { invitation_url: string; email_sent: boolean };
export type InviteCreate = { email: string; display_name?: string; access: InviteAccess; send: boolean };
export const listInvites = (): Promise<Invite[]> => requestJson('/api/admin/invites');
export const createInvite = (body: InviteCreate): Promise<IssuedInvite> => post('/api/admin/invites', body);
export const resendInvite = (id: string): Promise<IssuedInvite> => post(`/api/admin/invites/${enc(id)}/resend`);
export const revokeInvite = (id: string): Promise<void> => del(`/api/admin/invites/${enc(id)}`);
export const getPublicAddress = (): Promise<{ public_address: string | null }> => requestJson('/api/admin/public-address');
export const putPublicAddress = (value: string | null): Promise<{ public_address: string | null }> => put('/api/admin/public-address', { public_address: value });
/** The home-network address beside the public one (LAN HTTP mode only), e.g. http://lumina.home.arpa. */
export type LocalAddressState = { local_address: string | null; lan_http: boolean };
export const getLocalAddress = (): Promise<LocalAddressState> => requestJson('/api/admin/local-address');
export const putLocalAddress = (value: string | null): Promise<LocalAddressState> => put('/api/admin/local-address', { local_address: value });
