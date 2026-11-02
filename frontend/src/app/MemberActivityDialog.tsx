import { type CSSProperties } from 'react';
import { Avatar, Button, Dialog, ErrorState, Radio, Skeleton } from '../ui';
import type { ThemePreference } from '../theme';
import type { UserProfile } from '../types';
import { firstName, greeting, mastheadDate } from '../features/home/homeModel';
import './MemberActivityDialog.css';

export interface MemberActivityDay {
  date: string;
  watched_seconds: number;
  sessions: number;
}

/** A member-scoped rollup of durable, finished playback sessions. Durations are never inferred. */
export interface MemberActivitySummary {
  generated_at: string;
  period_days: number;
  timezone: string;
  watched_seconds: number;
  sessions: number;
  active_days: number;
  latest_activity_at: string | null;
  days: MemberActivityDay[];
}

export type MemberActivityState = 'loading' | 'ready' | 'failed';

export interface MemberActivityDialogProps {
  /** The header only mounts this while open; retaining an explicit override makes it reusable. */
  open?: boolean;
  onClose: () => void;
  user: UserProfile;
  theme: ThemePreference;
  onTheme: (theme: ThemePreference) => void;
  switchMemberLabel: string;
  onSwitchMember: () => void;
  onEditHome: () => void;
  onMobileConnect: () => void;
  onSettings: () => void;
  onManageServer?: () => void;
  onSignOut: () => void;
  activityState: MemberActivityState;
  activity: MemberActivitySummary | null;
  onRetryActivity: () => void;
}

function formatDuration(seconds: number): string {
  const minutes = Math.round(Math.max(0, seconds) / 60);
  if (seconds > 0 && minutes === 0) return '< 1 min';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

function dayLabel(date: string): string {
  const value = new Date(`${date}T00:00:00Z`);
  return Number.isNaN(value.getTime()) ? date : new Intl.DateTimeFormat(undefined, { weekday: 'short', timeZone: 'UTC' }).format(value);
}

function fullDayLabel(date: string): string {
  const value = new Date(`${date}T00:00:00Z`);
  return Number.isNaN(value.getTime()) ? date : new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(value);
}

function ActivityVisual({ activity }: { activity: MemberActivitySummary }) {
  const peak = Math.max(...activity.days.map((day) => day.watched_seconds), 1);
  const range = `the last ${activity.period_days} days`;
  return (
    <section aria-labelledby="member-activity-heading" className="member-activity-visual">
      <div className="member-activity-overview">
        <p className="member-activity-kicker">Your last {activity.period_days} days</p>
        <h3 id="member-activity-heading">{activity.watched_seconds ? <>{formatDuration(activity.watched_seconds)} <span>recorded playback</span></> : 'A quiet stretch'}</h3>
        {activity.watched_seconds ? <dl className="member-activity-stats"><div><dt>Sessions</dt><dd>{activity.sessions}</dd></div><div><dt>Active days</dt><dd>{activity.active_days}</dd></div></dl> : <p>Your finished watching and listening sessions will appear here.</p>}
      </div>
      <ol aria-label={`Playback activity over ${range}, shown in UTC`} className="member-activity-chart">
        {activity.days.map((day) => {
          const height = day.watched_seconds ? Math.max(8, Math.round((day.watched_seconds / peak) * 100)) : 3;
          const label = `${fullDayLabel(day.date)}: ${day.watched_seconds ? `${formatDuration(day.watched_seconds)}, ` : ''}${day.sessions} ${day.sessions === 1 ? 'session' : 'sessions'}`;
          return <li aria-label={label} key={day.date} title={label}><span aria-hidden="true" className={day.watched_seconds ? 'is-active' : ''} style={{ '--member-activity-height': `${height}%` } as CSSProperties} /><small aria-hidden="true">{dayLabel(day.date).slice(0, 1)}</small></li>;
        })}
      </ol>
      <p className="member-activity-zone">Activity is grouped by finished session in {activity.timezone}.</p>
    </section>
  );
}

function ActivityPanel({ activityState, activity, onRetryActivity }: Pick<MemberActivityDialogProps, 'activityState' | 'activity' | 'onRetryActivity'>) {
  if (activityState === 'loading') return <section aria-label="Loading your activity" className="member-activity-loading"><Skeleton count={1} label="Loading your activity" shape="block" /></section>;
  if (activityState === 'failed') return <ErrorState onRetry={onRetryActivity} title="Your activity is unavailable" body="Try again to load your recent ended playback sessions." />;
  if (!activity) return null;
  return <ActivityVisual activity={activity} />;
}

export function MemberActivityDialog(props: MemberActivityDialogProps) {
  const name = props.user.display_name || props.user.username;
  const closeThen = (action: () => void) => () => { props.onClose(); action(); };
  return (
    <Dialog className="member-activity-dialog" hideTitle onClose={props.onClose} open={props.open ?? true} size="lg" title="Your activity">
      <div className="member-activity-profile">
        <Avatar decorative name={name} size={48} />
        <div>
          <p>{greeting()}, {firstName(props.user)}</p>
          <h2>{mastheadDate()}</h2>
          <small>{props.user.role === 'admin' ? 'Vault owner' : 'Household member'}</small>
        </div>
      </div>
      <ActivityPanel activity={props.activity} activityState={props.activityState} onRetryActivity={props.onRetryActivity} />
      <section aria-labelledby="member-account-heading" className="member-activity-account">
        <h3 id="member-account-heading">Account</h3>
        <div className="member-activity-actions">
          <Button onClick={closeThen(props.onSwitchMember)} variant="quiet">{props.switchMemberLabel}</Button>
          <Button onClick={closeThen(props.onEditHome)} variant="quiet">Edit home</Button>
          <Button onClick={closeThen(props.onMobileConnect)} variant="quiet">Mobile connect</Button>
          <Button onClick={closeThen(props.onSettings)} variant="quiet">My Settings</Button>
          {props.onManageServer ? <Button onClick={closeThen(props.onManageServer)} variant="quiet">Manage Server</Button> : null}
        </div>
      </section>
      <fieldset className="member-activity-themes">
        <legend>Appearance</legend>
        {(['system', 'light', 'dark', 'oled'] as const).map((choice) => <Radio checked={props.theme === choice} key={choice} label={choice === 'oled' ? 'OLED' : choice[0].toUpperCase() + choice.slice(1)} name="member-activity-theme" onChange={() => props.onTheme(choice)} />)}
      </fieldset>
      <div className="member-activity-signout"><Button onClick={closeThen(props.onSignOut)} variant="quiet">Sign out</Button></div>
    </Dialog>
  );
}
