import { ChevronLeft, Plus, X } from 'lucide-react';
import { createContext, type KeyboardEvent, useContext, useEffect, useRef, useState } from 'react';

import { createPasswordResetLink, listUsers, resetMemberTwoFactor, unlockUserSignIn, updateUser } from '../../api';
import { Avatar, Button, Checkbox, ConfirmDialog, Dialog, Field, fieldProps, IconButton, Input, Menu, SegmentedControl, Select, StatusText } from '../../ui';
import { errorMessage, formatDateTime } from '../../utils';
import { type FormStatus, SectionForm } from '../settings/SectionForm';
import { SettingRow, SettingSwitch } from '../settings/SettingRow';
import { useSettingsHost } from '../settings/settingsHost';
import type { SettingEntry } from '../settings/settingsTypes';
import { spanLabel } from './activityModel';
import { CopyableLink, nameOf, roleLabel } from './AdminMembers';
import {
  addBonusMinutes, addMemberFollow, applyPreset, copyMondayToWeekdays, getMemberAccess, getMemberActivity, limitsSummary, librariesSummary, listMemberFollows, listSections, type MemberAccess, type MemberAccessState, type MemberFollow,
  type MemberActivity, type MemberRow, minutesLabel, MOVIE_RATINGS, PRESETS, type PresetId, putMemberAccess, removeMemberFollow, scheduleError, type Section, type StreamingKind, TV_RATINGS, WEEKDAYS, withDefaults,
} from './memberAccess';

type Role = 'admin' | 'viewer';
type Ctx = {
  member: MemberRow; self: boolean; name: string; setName: (name: string) => void;
  access: MemberAccess; set: (patch: Partial<MemberAccess>) => void; saved: MemberAccessState | null; setSaved: (next: MemberAccessState) => void;
  sections: Section[] | null; onMember: (member: MemberRow) => void;
};
const MemberContext = createContext<Ctx | null>(null);
const useMember = () => { const ctx = useContext(MemberContext); if (!ctx) throw new Error('Member rows render inside MemberPage.'); return ctx; };

const DAILY_OPTIONS = [30, 60, 90, 120, 180, 240, 300, 360, 480];

// ---- Profile ----
function DisplayName() {
  const { name, setName } = useMember();
  return <Field hideLabel label="Display name">{(ids) => <Input {...fieldProps(ids)} maxLength={120} onChange={(event) => setName(event.target.value)} value={name} />}</Field>;
}

function useImmediate() {
  const { member, onMember } = useMember();
  const { onMessage } = useSettingsHost();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function apply(change: { role?: Role; is_active?: boolean }) {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateUser(member.id, change);
      onMember({ ...member, ...updated });
      onMessage?.(`${nameOf(updated)}’s access was updated.`);
    } catch (failure) {
      // The page keeps the server-confirmed state; the reason (e.g. last owner) stays next to the control.
      setError(errorMessage(failure, 'Unable to update this household member.'));
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, apply };
}

function RoleControl() {
  const { member, self } = useMember();
  const { busy, error, apply } = useImmediate();
  const [pending, setPending] = useState<Role | null>(null);
  const name = nameOf(member);
  return (
    <div className="g-control-stack">
      <Field hideLabel label={`Role for ${name}`}>
        {(ids) => (
          <Select {...fieldProps(ids)} disabled={self || busy} onChange={(event) => setPending(event.target.value as Role)} value={member.role}>
            <option value="viewer">Household member</option>
            <option value="admin">Vault owner</option>
          </Select>
        )}
      </Field>
      {self ? <p className="g-setting-note">Your owner access stays on so the household cannot be locked out.</p> : null}
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <ConfirmDialog
        body={pending === 'admin' ? `${name} will be able to open the Server settings: manage members, storage and download limits, with no limits of their own.` : `${name} loses access to the Server settings. At least one active vault owner must remain.`}
        busy={busy} confirmLabel={pending === 'admin' ? `Make ${name} a vault owner` : `Make ${name} a household member`}
        onCancel={() => setPending(null)} onConfirm={() => { const role = pending; setPending(null); if (role) void apply({ role }); }} open={pending !== null}
        title={pending === 'admin' ? `Make ${name} a vault owner?` : `Make ${name} a household member?`}
      />
    </div>
  );
}

function ResetLink() {
  const { member } = useMember();
  const [state, setState] = useState<{ busy?: boolean; url?: string; error?: string }>({});
  async function issue() {
    setState({ busy: true });
    try { setState({ url: (await createPasswordResetLink(member.id)).reset_url }); } catch (failure) { setState({ error: errorMessage(failure, 'Unable to create the link.') }); }
  }
  return (
    <div className="g-control-stack">
      <div className="g-actions"><Button busy={state.busy} onClick={() => { void issue(); }}>Create reset link</Button></div>
      {state.error ? <p className="auth-error" role="alert">{state.error}</p> : null}
      <Dialog footer={<Button onClick={() => setState({})} variant="primary">Done</Button>} onClose={() => setState({})} open={Boolean(state.url)} size="md" title="Reset link"><CopyableLink copyLabel="Copy link" label="Reset link" url={state.url ?? ''} /></Dialog>
    </div>
  );
}

function TwoFactor() {
  const { member, onMember } = useMember();
  const { onMessage } = useSettingsHost();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = nameOf(member);
  async function reset() {
    setBusy(true);
    setError(null);
    try {
      await resetMemberTwoFactor(member.id);
      onMember({ ...member, two_factor_enabled: false });
      onMessage?.(`Two-step verification was reset for ${name}.`);
    } catch (failure) {
      setError(errorMessage(failure, 'Unable to reset two-step verification.'));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }
  return (
    <div className="g-control-stack">
      <div className="g-actions">
        <StatusText tone={member.two_factor_enabled ? 'ok' : 'muted'}>{member.two_factor_enabled ? 'On' : 'Off'}</StatusText>
        {member.two_factor_enabled ? <Button onClick={() => setConfirming(true)} variant="quiet">Reset two-step verification</Button> : null}
      </div>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <ConfirmDialog
        body={`${name} will sign in with just their password, and can set up two-step verification again.`}
        busy={busy} confirmLabel="Reset two-step verification" danger onCancel={() => setConfirming(false)} onConfirm={() => { void reset(); }} open={confirming} title={`Reset two-step verification for ${name}?`}
      />
    </div>
  );
}

function AccountStatus() {
  const { member } = useMember();
  const { busy, error, apply } = useImmediate();
  const [confirming, setConfirming] = useState(false);
  const name = nameOf(member);
  return (
    <div className="g-control-stack">
      <div className="g-actions">
        <StatusText tone={member.is_active ? 'ok' : 'muted'}>{member.is_active ? 'Active' : 'Deactivated'}</StatusText>
        <Button busy={busy} onClick={() => (member.is_active ? setConfirming(true) : void apply({ is_active: true }))} variant={member.is_active ? 'quiet' : 'secondary'}>{member.is_active ? 'Deactivate' : 'Reactivate'}</Button>
      </div>
      {member.sign_in_locked ? <SignInLock /> : null}
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <ConfirmDialog
        body={`${name} is signed out on every device and their queued or running downloads stop. Their library, history and notes are kept, and you can reactivate them at any time.`}
        busy={busy} confirmLabel={`Deactivate ${name}`} danger onCancel={() => setConfirming(false)} onConfirm={() => { setConfirming(false); void apply({ is_active: false }); }} open={confirming} title={`Deactivate ${name}?`}
      />
    </div>
  );
}

function SignInLock() {
  const { member, onMember } = useMember();
  const [state, setState] = useState<{ busy?: boolean; error?: string }>({});
  async function unlock() {
    setState({ busy: true });
    try { await unlockUserSignIn(member.id); onMember({ ...member, sign_in_locked: false }); } catch (failure) { setState({ error: errorMessage(failure, 'Unable to unlock sign-in.') }); }
  }
  return (
    <>
      <div className="g-actions">
        <StatusText tone="muted">Sign-in paused after repeated wrong passwords on the public address</StatusText>
        <Button busy={state.busy} onClick={() => { void unlock(); }} variant="secondary">Unlock sign-in</Button>
      </div>
      {state.error ? <p className="auth-error" role="alert">{state.error}</p> : null}
    </>
  );
}

// ---- Libraries ----
function AllLibraries() {
  const { access, set, sections } = useMember();
  // Turning "all" off starts from every library checked, so nothing disappears until one is unchecked.
  return <SettingSwitch checked={access.sections === null} onChange={(on) => set({ sections: on ? null : (sections ?? []).map((section) => section.id) })} />;
}

function LibraryChecklist() {
  const { access, set, sections } = useMember();
  if (access.sections === null) return <p className="g-setting-note">Every library is shared, including ones added later.</p>;
  const chosen = new Set(access.sections);
  const toggle = (id: string, on: boolean) => set({ sections: (sections ?? []).map((section) => section.id).filter((entry) => (entry === id ? on : chosen.has(entry))) });
  return (
    <div className="g-subrows">
      {(sections ?? []).map((section) => (
        <Checkbox checked={chosen.has(section.id)} hint={`${section.count.toLocaleString()} titles`} key={section.id} label={section.label} onChange={(event) => toggle(section.id, event.currentTarget.checked)} />
      ))}
      {!access.sections.length ? <p className="g-setting-note" role="status">No library is shared: this member sees nothing in the Library.</p> : null}
    </div>
  );
}

// ---- Ratings ----
function FilmRating() {
  const { access, set } = useMember();
  return <SegmentedControl hideLegend legend="Films up to" onChange={(value) => set({ movie_rating_max: value === 'none' ? null : value })} options={[...MOVIE_RATINGS.map((value) => ({ value, label: value })), { value: 'none' as const, label: 'No limit' }]} value={access.movie_rating_max ?? 'none'} />;
}
function TvRating() {
  const { access, set } = useMember();
  return <SegmentedControl hideLegend legend="TV up to" onChange={(value) => set({ tv_rating_max: value === 'none' ? null : value })} options={[...TV_RATINGS.map((value) => ({ value, label: value })), { value: 'none' as const, label: 'No limit' }]} value={access.tv_rating_max ?? 'none'} />;
}
function HideUnrated() {
  const { access, set } = useMember();
  return <SettingSwitch checked={access.unrated === 'hide'} onChange={(on) => set({ unrated: on ? 'hide' : 'allow' })} />;
}

// ---- Streaming ----
const streamingSwitch = (kind: StreamingKind) => function StreamingSwitch() {
  const { access, set } = useMember();
  return <SettingSwitch checked={access.streaming[kind]} onChange={(on) => set({ streaming: { ...access.streaming, [kind]: on } })} />;
};
function FollowedOnly() {
  const { access, set } = useMember();
  const none = !access.streaming.youtube && !access.streaming.twitch && !access.streaming.kick;
  return <SettingSwitch checked={access.streaming.followed_only} disabled={none} onChange={(on) => set({ streaming: { ...access.streaming, followed_only: on } })} />;
}

/** Follows apply at once, outside the Save form: the member's own new follows are refused under “Followed channels only”. */
function FollowedChannels() {
  const { member } = useMember();
  const [follows, setFollows] = useState<MemberFollow[] | null>(null);
  const [address, setAddress] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    listMemberFollows(member.id).then((rows) => { if (live) setFollows(rows); }, (failure) => { if (live) setError(errorMessage(failure, 'Unable to load followed channels.')); });
    return () => { live = false; };
  }, [member.id]);
  async function run(change: () => Promise<MemberFollow[]>) {
    setBusy(true);
    setError(null);
    try { setFollows(await change()); return true; } catch (failure) { setError(errorMessage(failure, 'Unable to update followed channels.')); return false; } finally { setBusy(false); }
  }
  async function add() {
    if (address.trim() && await run(() => addMemberFollow(member.id, address.trim()))) setAddress('');
  }
  return (
    <div className="g-control-stack">
      {follows === null ? (error ? null : <p className="g-setting-note">Loading…</p>) : follows.length ? (
        <ul aria-label="Followed channels" className="g-list">
          {follows.map((follow) => (
            <li className="g-list-row" key={follow.id}>
              <span className="g-list-row-copy">
                <span className="g-list-row-title">{follow.label}</span>
                <span className="g-list-row-meta">{follow.source_url}</span>
              </span>
              <IconButton disabled={busy} icon={<X />} label={`Unfollow ${follow.label}`} onClick={() => { void run(async () => { await removeMemberFollow(member.id, follow.id); return follows.filter((entry) => entry.id !== follow.id); }); }} />
            </li>
          ))}
        </ul>
      ) : <p className="g-setting-note">Not following any channels yet.</p>}
      <div className="g-actions">
        <Input aria-label="Channel address" onChange={(event) => setAddress(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void add(); } }} placeholder="youtube.com/@channel" value={address} />
        <Button busy={busy} disabled={!address.trim()} onClick={() => { void add(); }}>Follow</Button>
      </div>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
    </div>
  );
}

// ---- Schedule ----
function HoursSwitch() {
  const { access, set } = useMember();
  return <SettingSwitch checked={access.schedule !== null} onChange={(on) => set({ schedule: on ? Object.fromEntries(WEEKDAYS.map(([day]) => [day, [['07:00', '20:00']]])) : null })} />;
}

function WeekHours() {
  const { access, set } = useMember();
  const schedule = access.schedule;
  if (!schedule) return <p className="g-setting-note">Any time of day. Turn on Viewing hours to choose when.</p>;
  const setDay = (day: (typeof WEEKDAYS)[number][0], ranges: [string, string][]) => set({ schedule: { ...schedule, [day]: ranges } });
  const error = scheduleError(schedule);
  return (
    <div className="g-subrows">
      {WEEKDAYS.map(([day, label]) => {
        const ranges = schedule[day] ?? [];
        return (
          <div aria-label={label} className="g-subrow g-day" key={day} role="group">
            <span className="g-day-name">{label}</span>
            <div className="g-day-ranges">
              {ranges.length ? null : <span className="g-setting-note">No watching</span>}
              {ranges.map(([from, to], index) => {
                const edit = (next: [string, string]) => setDay(day, ranges.map((range, at) => (at === index ? next : range)));
                return (
                  <span className="g-day-range" key={index}>
                    <Input aria-label={`${label} from`} onChange={(event) => edit([event.target.value, to])} type="time" value={from} />
                    <span aria-hidden="true">–</span>
                    <Input aria-label={`${label} until`} onChange={(event) => edit([from, event.target.value])} type="time" value={to} />
                    <IconButton icon={<X />} label={`Remove ${label} ${from}–${to}`} onClick={() => setDay(day, ranges.filter((_, at) => at !== index))} />
                  </span>
                );
              })}
              <IconButton icon={<Plus />} label={`Add hours on ${label}`} onClick={() => setDay(day, [...ranges, ranges.length ? ['16:00', '20:00'] : ['07:00', '20:00']])} />
            </div>
          </div>
        );
      })}
      <div className="g-actions"><Button onClick={() => set({ schedule: copyMondayToWeekdays(schedule) })} variant="quiet">Copy Monday to weekdays</Button></div>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
    </div>
  );
}

function DailyLimit() {
  const { access, set } = useMember();
  const options = [...new Set([...DAILY_OPTIONS, ...(access.daily_limit_minutes ? [access.daily_limit_minutes] : [])])].sort((a, b) => a - b);
  return (
    <Field hideLabel label="Daily limit">
      {(ids) => (
        <Select {...fieldProps(ids)} onChange={(event) => set({ daily_limit_minutes: Number(event.target.value) || null })} value={access.daily_limit_minutes ?? 0}>
          <option value={0}>No limit</option>
          {options.map((minutes) => <option key={minutes} value={minutes}>{minutesLabel(minutes)} a day</option>)}
        </Select>
      )}
    </Field>
  );
}

/** "1 h 05 min watched · 55 min left"; the bonus applies to the saved daily limit. */
export function todayLine(state: MemberAccessState | null): string {
  if (!state) return '';
  const watched = state.screen_time_today_seconds ?? 0;
  const parts = [watched ? `${spanLabel(watched)} watched` : 'Nothing watched yet'];
  if (state.daily_limit_minutes) {
    const left = Math.max(0, state.daily_limit_minutes + (state.bonus_minutes_today ?? 0) - Math.floor(watched / 60));
    parts.push(left ? `${minutesLabel(left)} left` : 'time is up');
    if (state.bonus_minutes_today) parts.push(`includes ${minutesLabel(state.bonus_minutes_today)} extra`);
  }
  return parts.join(' · ');
}

function Today() {
  const { member, saved, setSaved } = useMember();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function bonus() {
    setBusy(true);
    setError(null);
    try { setSaved(await addBonusMinutes(member.id, 30)); } catch (failure) { setError(errorMessage(failure, 'Unable to add time.')); } finally { setBusy(false); }
  }
  return (
    <div className="g-control-stack">
      <div className="g-actions">
        <span aria-live="polite" className="g-setting-note">{todayLine(saved)}</span>
        <Button busy={busy} disabled={!saved?.daily_limit_minutes} onClick={() => { void bonus(); }}>+30 min today</Button>
      </div>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
    </div>
  );
}

// ---- Permissions ----
function Downloads() {
  const { access, set } = useMember();
  return <SettingSwitch checked={access.can_download} onChange={(on) => set({ can_download: on })} />;
}
function RequestsLink() {
  const { onOpenSection } = useSettingsHost();
  return <div className="g-actions"><Button onClick={() => onOpenSection?.('requests')} variant="quiet">Open request policies</Button></div>;
}
function EditingLink() {
  const { onOpenSection } = useSettingsHost();
  return <div className="g-actions"><Button onClick={() => onOpenSection?.('library')} variant="quiet">Open Library & storage</Button></div>;
}

// ---- Activity ----
const ActivityContext = createContext<{ data: MemberActivity | null; error: string | null } | null>(null);
function ScreenTimeToday() {
  const activity = useContext(ActivityContext);
  if (activity?.error) return <p className="auth-error" role="alert">{activity.error}</p>;
  return <span className="g-setting-note g-tabular">{activity?.data ? (activity.data.screen_time_today_seconds ? spanLabel(activity.data.screen_time_today_seconds) : 'Nothing yet') : 'Loading…'}</span>;
}
function RecentPlayback() {
  const activity = useContext(ActivityContext);
  if (!activity?.data) return null;
  if (!activity.data.recent.length) return <p className="g-setting-note">Nothing played in the last 90 days.</p>;
  return (
    <ul aria-label="Recent playback" className="g-list">
      {activity.data.recent.map((row) => (
        <li className="g-list-row" key={row.id}>
          <span className="g-list-row-copy">
            <span className="g-list-row-title">{row.title}</span>
            <span className="g-list-row-meta">{[row.subtitle, formatDateTime(row.started_at), `${spanLabel(row.watched_seconds)} watched`, row.client.name].filter(Boolean).join(' · ')}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The member page's rows, by tab: the registry for /settings/members/<id> (each row has ⓘ text like every Settings row). */
export const MEMBER_TABS: ReadonlyArray<{ id: string; label: string; ownerToo?: true; entries: readonly SettingEntry[] }> = [
  { id: 'profile', label: 'Profile', ownerToo: true, entries: [
    { id: 'members.name', label: 'Display name', info: 'The name shown on Home, in Who’s watching and to other members. Saving changes it everywhere at once. The sign-in username stays the same.', Control: DisplayName },
    { id: 'members.role', label: 'Role', info: 'Vault owners open the Server settings and have no limits; household members see only what you share with them. Changing a role takes effect at once, and at least one active vault owner must remain.', Control: RoleControl },
    { id: 'members.reset', label: 'Password', description: 'A one-time link to choose a new password.', info: 'Creates a single-use link this member opens to choose a new password; you never see or set it. Send it to them yourself. Creating another link replaces the last one.', Control: ResetLink },
    { id: 'members.two-factor', label: 'Two-step verification', info: 'Whether this member also needs a code from an authenticator app to sign in. If they lose their phone and recovery codes, resetting it lets them in with just their password so they can set it up again. Affects only this member.', Control: TwoFactor },
    { id: 'members.status', label: 'Account', info: 'Deactivating signs this member out everywhere and stops their downloads, but keeps their library, history and notes. Reactivating lets them sign in again, and Unlock sign-in lifts the pause of up to 15 minutes after repeated wrong passwords on the public address. Both take effect at once.', Control: AccountStatus },
  ] },
  { id: 'libraries', label: 'Libraries', entries: [
    { id: 'members.all-libraries', label: 'All libraries', layout: 'switch', description: 'Includes libraries added later.', info: 'Shares every library on this server with this member, including ones you add later. Turn it off to choose libraries one by one. Titles in other libraries never appear for them anywhere, including in apps like Infuse.', Control: AllLibraries },
    { id: 'members.libraries', label: 'Shared libraries', layout: 'block', info: 'The libraries this member can browse, search and play. Unchecked libraries are hidden everywhere for them, including Home, search and connected apps. The counts are titles in each library.', Control: LibraryChecklist },
  ] },
  { id: 'ratings', label: 'Ratings', entries: [
    { id: 'members.film-rating', label: 'Films up to', layout: 'block', info: 'The highest film certificate this member can see, using US ratings. Films rated above it are hidden everywhere for them. No limit shows every film.', Control: FilmRating },
    { id: 'members.tv-rating', label: 'TV up to', layout: 'block', info: 'The highest TV rating this member can see, using US TV ratings. Shows rated above it, and all their episodes, are hidden everywhere for them. No limit shows every show.', Control: TvRating },
    { id: 'members.unrated', label: 'Hide unrated titles', layout: 'switch', description: 'Titles with no known rating.', info: 'Hides films and shows Lumina has no rating for yet, such as home videos and downloads. Leave it off to show them. It only matters while a rating limit is set.', Control: HideUnrated },
  ] },
  { id: 'streaming', label: 'Streaming', entries: [
    { id: 'members.youtube', label: 'YouTube', layout: 'switch', info: 'Lets this member search, browse and watch YouTube through Lumina. Turning it off hides YouTube from their Streaming tab and search. Saved videos follow their library choices instead.', Control: streamingSwitch('youtube') },
    { id: 'members.twitch', label: 'Twitch', layout: 'switch', info: 'Lets this member browse and watch Twitch channels and videos. Turning it off hides Twitch everywhere for them. Recordings already saved follow their library choices.', Control: streamingSwitch('twitch') },
    { id: 'members.kick', label: 'Kick', layout: 'switch', info: 'Lets this member browse and watch Kick channels. Turning it off hides Kick everywhere for them. Recordings already saved follow their library choices.', Control: streamingSwitch('kick') },
    { id: 'members.live', label: 'Live broadcasts', layout: 'switch', info: 'Lets this member watch broadcasts while they are live, with live chat. Turning it off hides Live and every live stream for them. Finished broadcasts follow the switches above.', Control: streamingSwitch('live') },
    { id: 'members.open-search', label: 'Search the open internet', layout: 'switch', info: 'Lets this member’s searches reach YouTube and other sites, not just this vault. Turning it off keeps search to what is already here and the channels they follow. Streaming switches above still apply.', Control: streamingSwitch('open_search') },
    { id: 'members.followed-only', label: 'Followed channels only', layout: 'switch', info: 'Limits streaming to channels this member follows; anything else stays hidden. They cannot follow new channels themselves, so add them under Followed channels. It needs at least one streaming service on.', Control: FollowedOnly },
    { id: 'members.follows', label: 'Followed channels', layout: 'block', keywords: ['follow', 'subscriptions', 'channels'], info: 'The channels this member follows. Adding or removing one here applies at once, without Save. With Followed channels only on, these are the only channels they can watch.', Control: FollowedChannels },
  ] },
  { id: 'schedule', label: 'Schedule', entries: [
    { id: 'members.hours', label: 'Viewing hours', layout: 'switch', description: 'Watching only at the times you set.', info: 'Limits when this member can start or keep watching, by day of the week, in this server’s time zone. Outside those hours playback stops calmly and tells them when they can watch again. Off means any time.', Control: HoursSwitch },
    { id: 'members.week', label: 'Allowed times', layout: 'block', keywords: ['weekday', 'bedtime', 'schedule'], info: 'Each day can have one or more time ranges, or none for no watching that day. Copy Monday to weekdays fills Tuesday to Friday with Monday’s times. Changes apply from the next minute after saving.', Control: WeekHours },
    { id: 'members.daily', label: 'Daily limit', info: 'The most this member can watch in one day, counted across Lumina and connected apps. When it runs out, playback stops and says so until tomorrow. Time added today is extra on top.', Control: DailyLimit },
    { id: 'members.today', label: 'Today', info: 'How long this member has watched today and how much of their daily limit is left. Adding 30 minutes applies only to today and needs a saved daily limit. It takes effect at once.', Control: Today },
  ] },
  { id: 'permissions', label: 'Permissions', entries: [
    { id: 'members.downloads', label: 'Downloads', layout: 'switch', description: 'Save media to the vault.', info: 'Lets this member save videos and recordings to the vault. Turning it off removes the save buttons for them and refuses new downloads. What they already saved stays.', Control: Downloads },
    { id: 'members.requests', label: 'Requests', description: 'Set by the request policies.', info: 'Whether this member may request movies and shows follows the household request policies. You can give this member their own rule under Requests. Vault owners can always request.', Control: RequestsLink },
    { id: 'members.editing', label: 'Details editing', description: 'One household switch for every member.', info: 'Whether members can edit titles, artwork and details is one switch for the whole household. It lives under Library & storage. Vault owners can always edit.', Control: EditingLink },
  ] },
  { id: 'activity', label: 'Activity', ownerToo: true, entries: [
    { id: 'members.screen-time', label: 'Screen time today', info: 'Everything this member watched today, counted from playback in Lumina and in connected apps. The day starts at midnight in this server’s time zone. It is only shown to vault owners.', Control: ScreenTimeToday },
    { id: 'members.recent', label: 'Recent playback', layout: 'block', info: 'What this member played lately, newest first, with how long and on which app. It comes from the household playback history, kept for 90 days. Only vault owners see it.', Control: RecentPlayback },
  ] },
];

const sameAccess = (a: MemberAccess, b: MemberAccess) => JSON.stringify(withDefaults(a)) === JSON.stringify(withDefaults(b));

/** Settings → Members → one member: tabs over one Save form; Profile actions (role, reset link, deactivate) apply at once. */
export function MemberPage({ memberId, onBack }: { memberId: string; onBack: () => void }) {
  const { user } = useSettingsHost();
  const [member, setMember] = useState<MemberRow | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saved, setSaved] = useState<MemberAccessState | null>(null);
  const [sections, setSections] = useState<Section[] | null>(null);
  const [draft, setDraft] = useState<{ name: string; access: MemberAccess } | null>(null);
  const [tab, setTab] = useState('profile');
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const activity = useActivityOnce(memberId, tab === 'activity');

  useEffect(() => {
    let live = true;
    (listUsers() as Promise<MemberRow[]>).then(async (users) => {
      const found = users.find((entry) => entry.id === memberId);
      if (!found) throw new Error('This member no longer exists.');
      const [state, shared] = found.role === 'admin' ? [null, null] : await Promise.all([getMemberAccess(memberId), listSections()]);
      if (!live) return;
      setMember(found); setSaved(state); setSections(shared);
      setDraft({ name: found.display_name, access: withDefaults(state) });
    }).catch((failure) => { if (live) setLoadError(errorMessage(failure, 'Unable to load this member.')); });
    return () => { live = false; };
  }, [memberId]);

  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (member) heading.current?.focus({ preventScroll: true }); }, [member?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const backLink = (
    <div className="g-member-nav">
      <Button icon={<ChevronLeft />} onClick={onBack} variant="quiet">Members</Button>
    </div>
  );
  if (!member || !draft) {
    return <section aria-labelledby="settings-section-title" className="g-settings-section">{backLink}<h2 className="sr-only" id="settings-section-title">Member</h2>{loadError ? <p className="auth-error" role="alert">{loadError}</p> : <p className="g-setting-note" role="status">Loading member…</p>}</section>;
  }

  const owner = member.role === 'admin';
  const name = nameOf(member);
  const accessDirty = !owner && saved !== null && !sameAccess(draft.access, saved);
  const dirty = draft.name.trim() !== member.display_name || accessDirty;
  const tabs = MEMBER_TABS.filter((entry) => !owner || entry.ownerToo);
  const shownTab = tabs.find((entry) => entry.id === tab) ?? tabs[0];
  const self = member.id === user.id;
  const entries = shownTab.entries.filter((entry) => !(self && (entry.id === 'members.reset' || entry.id === 'members.status')));

  async function save() {
    if (!member || !draft) return;
    const problem = scheduleError(draft.access.schedule);
    if (problem) { setStatus({ tone: 'error', text: problem }); setTab('schedule'); return; }
    if (!draft.name.trim()) { setStatus({ tone: 'error', text: 'Enter a display name.' }); setTab('profile'); return; }
    setSaving(true);
    setStatus(null);
    try {
      if (draft.name.trim() !== member.display_name) setMember({ ...member, ...(await updateUser(member.id, { display_name: draft.name.trim() })) });
      if (accessDirty) setSaved(await putMemberAccess(member.id, draft.access));
      setDraft({ ...draft, name: draft.name.trim() });
      setStatus({ tone: 'ok', text: 'Saved.' });
    } catch (failure) {
      setStatus({ tone: 'error', text: errorMessage(failure, 'Unable to save. Nothing was changed.') });
    } finally {
      setSaving(false);
    }
  }

  function onTabsKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const at = tabs.indexOf(shownTab);
    const to = event.key === 'ArrowRight' ? (at + 1) % tabs.length : event.key === 'ArrowLeft' ? (at + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1;
    if (to < 0) return;
    event.preventDefault();
    setTab(tabs[to].id);
    document.getElementById(`member-tab-${tabs[to].id}`)?.focus();
  }

  const presets = (Object.keys(PRESETS) as PresetId[]).map((id) => ({
    kind: 'item' as const, label: PRESETS[id].label, hint: PRESETS[id].hint,
    onSelect: () => { setDraft({ ...draft, access: applyPreset(draft.access, id) }); setStatus({ tone: 'ok', text: `${PRESETS[id].label} limits filled in. Review them, then save.` }); },
  }));
  const summary = owner ? 'Owner — no limits' : [librariesSummary(draft.access, sections), limitsSummary(draft.access) || 'no limits'].join(' · ');
  const ctx: Ctx = {
    member, self, name: draft.name, setName: (next) => setDraft({ ...draft, name: next }),
    access: draft.access, set: (patch) => { setStatus(null); setDraft({ ...draft, access: { ...draft.access, ...patch } }); },
    saved, setSaved, sections, onMember: (next) => setMember(next),
  };

  return (
    <section aria-labelledby="settings-section-title" className="g-settings-section">
      {backLink}
      <header className="g-settings-section-head g-member-head">
        <Avatar decorative name={name} size={48} />
        <div>
          <h2 id="settings-section-title" ref={heading} tabIndex={-1}>{name}</h2>
          <p className="g-settings-summary">{roleLabel(member.role)} · {summary}</p>
        </div>
        {owner ? null : <div className="g-member-nav"><Menu align="end" items={presets} label="Presets" trigger={(props) => <Button {...props}>Apply a preset</Button>} /></div>}
      </header>
      <div aria-label={`${name} settings`} className="g-tabs g-member-nav" onKeyDown={onTabsKeyDown} role="tablist">
        {tabs.map((entry) => (
          <button aria-controls="member-panel" aria-selected={entry === shownTab} id={`member-tab-${entry.id}`} key={entry.id} onClick={() => setTab(entry.id)} role="tab" tabIndex={entry === shownTab ? 0 : -1} type="button">{entry.label}</button>
        ))}
      </div>
      <MemberContext.Provider value={ctx}>
        <ActivityContext.Provider value={activity}>
          <SectionForm dirty={dirty} label={`${name}’s settings`} onDiscard={() => { setDraft({ name: member.display_name, access: withDefaults(saved) }); setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save" saving={saving} status={status}>
            <div aria-labelledby={`member-tab-${shownTab.id}`} id="member-panel" role="tabpanel">
              {entries.map(({ id, label, info, description, layout, Control }) => <SettingRow description={description} id={id} info={info} key={id} label={label} layout={layout}><Control /></SettingRow>)}
            </div>
          </SectionForm>
        </ActivityContext.Provider>
      </MemberContext.Provider>
    </section>
  );
}

/** Activity loads each time its tab opens; the last result stays on screen meanwhile. */
function useActivityOnce(memberId: string, wanted: boolean) {
  const [state, setState] = useState<{ data: MemberActivity | null; error: string | null }>({ data: null, error: null });
  useEffect(() => {
    if (!wanted) return undefined;
    let live = true;
    getMemberActivity(memberId).then((data) => { if (live) setState({ data, error: null }); }, (failure) => { if (live) setState({ data: null, error: errorMessage(failure, 'Unable to load activity.') }); });
    return () => { live = false; };
  }, [memberId, wanted]);
  return state;
}
