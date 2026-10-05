import { MoreHorizontal, Plus } from 'lucide-react';
import { type FormEvent, useId, useRef, useState } from 'react';
import { ApiRequestError, forgetDeviceMember, loginSession, switchMember } from '../../api';
import type { DeviceMember } from '../../types';
import { Avatar, Button, IconButton, Masthead, Menu, PasswordInput, useToast } from '../../ui';
import { moveFocus } from '../media/focusNav';
import { ringCall } from './deviceRing';

/** Runs while the leaving member's cookie is still current (LuminaApp's prepareMemberChange): their buffered reco events,
 * playback writes and warm-up stop go out under their own session, never the next member's. It may return a function
 * that hands the leaving member their session back (reopens what it closed) when the change then fails. */
export type BeforeMemberChange = () => Promise<void | (() => void)>;

export interface WhoIsWatchingProps {
  members: DeviceMember[];
  variant: 'page' | 'dialog';
  /** The signed-in member's name, for the dialog's sign-out warning. */
  currentName?: string;
  onSwitched: () => void;
  onSomeoneElse: () => void;
  onRefresh: () => void;
  beforeChange?: BeforeMemberChange;
}

export const RATE_LIMITED = 'Too many attempts. Try again in a minute.';

/** The ring call may have committed (the cookie changed) even though no answer arrived: a network error, a timeout or a 5xx. */
const outcomeUnknown = (error: unknown) => !(error instanceof ApiRequestError) || error.status === 0 || error.status >= 500;

/** The leaving member finishes first, then the one ring call that changes the cookie; a refusal hands their session back.
 * When the call's outcome is unknown, `onSwitched` reloads the session so the UI follows whichever cookie the browser now
 * holds (security review M3); the error is still thrown so the caller does not also report a switch. */
export async function changeMember<T>(beforeChange: BeforeMemberChange | undefined, request: () => Promise<T>, onSwitched?: () => void): Promise<T> {
  let resume: void | (() => void) = undefined;
  try {
    resume = await beforeChange?.();
  } catch (error) {
    if (typeof resume === 'function') resume();
    throw error;
  }
  try {
    return await ringCall(request);
  } catch (error) {
    if (onSwitched && outcomeUnknown(error)) onSwitched();
    else if (typeof resume === 'function') resume();
    throw error;
  }
}

export function WhoIsWatching({ members, variant, currentName, onSwitched, onSomeoneElse, onRefresh, beforeChange }: WhoIsWatchingProps) {
  const toast = useToast();
  const [asking, setAsking] = useState<string | null>(null); // user_id whose tile shows the password field
  const [busy, setBusy] = useState<string | null>(null);
  const busyRef = useRef(false); // no second ring call while one is pending (M-R2), even within one render
  const [problem, setProblem] = useState<string | null>(null);
  const activeInRing = members.some((member) => member.active);

  async function pending<T>(id: string, run: () => Promise<T>): Promise<T | undefined> {
    if (busyRef.current) return undefined;
    busyRef.current = true; setBusy(id);
    try { return await run(); } finally { busyRef.current = false; setBusy(null); }
  }

  async function choose(member: DeviceMember) {
    if (member.active || busyRef.current) return;
    if (member.switch === 'password') { setAsking(member.user_id); return; }
    setProblem(null);
    await pending(member.user_id, async () => {
      try {
        await changeMember(beforeChange, () => switchMember(member.user_id), onSwitched);
        onSwitched();
      } catch (error) {
        const status = error instanceof ApiRequestError ? error.status : 0;
        if (status === 401) setAsking(member.user_id);
        else if (status === 404) { onRefresh(); toast({ tone: 'info', message: `${member.display_name} needs to sign in again.` }); }
        else if (status === 429) setProblem(RATE_LIMITED);
        else setProblem(error instanceof Error ? error.message : 'Unable to switch member');
      }
    });
  }

  async function forget(member: DeviceMember) {
    await pending(member.user_id, async () => {
      try { await ringCall(() => forgetDeviceMember(member.user_id)); } catch { /* The refresh shows what the ring holds. */ }
    });
    onRefresh();
  }

  async function signIn(member: DeviceMember, password: string): Promise<void> {
    await pending(member.user_id, async () => {
      await changeMember(beforeChange, () => loginSession({ username: member.username, password, remember_on_device: true }), onSwitched);
      onSwitched();
    });
  }

  return (
    <div className={`g-picker is-${variant}`}>
      {variant === 'page' ? <Masthead align="center" headingId="auth-title" title="Who's watching?" /> : null}
      {variant === 'dialog' && !activeInRing && currentName ? <p className="g-picker-note">{currentName} will be signed out on this device.</p> : null}
      <div aria-busy={busy !== null || undefined} aria-label="Members on this device" className="g-picker-tiles" onKeyDown={(event) => moveFocus(event)} role="group">
        {members.map((member) => asking === member.user_id ? (
          <PasswordTile busy={busy !== null} key={member.user_id} member={member} onCancel={() => setAsking(null)} onSubmit={(password) => signIn(member, password)} />
        ) : (
          <div className="g-picker-tile-wrap" key={member.user_id}>
            <button
              aria-disabled={member.active || busy !== null || undefined}
              aria-label={member.active ? `Watching now: ${member.display_name}` : member.switch === 'password' ? `${member.display_name}, vault owner, needs password` : `Continue as ${member.display_name}`}
              className="g-picker-tile"
              data-focus-item=""
              onClick={() => void choose(member)}
              type="button"
            >
              <Avatar decorative name={member.display_name} size={96} />
              <span className="g-picker-name">{member.display_name}</span>
              <span className="g-picker-label g-label">{member.active ? 'Watching now' : member.switch === 'password' ? 'Vault owner · password' : ''}</span>
            </button>
            {member.active && variant === 'dialog' ? null : (
              <Menu align="end" items={[{ kind: 'item', label: 'Forget on this device', danger: true, onSelect: () => void forget(member) }]}
                trigger={(props) => <IconButton {...props} disabled={busy !== null} icon={<MoreHorizontal />} label={`More for ${member.display_name}`} />} />
            )}
          </div>
        ))}
        <button aria-disabled={busy !== null || undefined} className="g-picker-tile is-new" data-focus-item="" onClick={() => { if (!busyRef.current) onSomeoneElse(); }} type="button">
          <span aria-hidden="true" className="g-picker-plus"><Plus /></span>
          <span className="g-picker-name">Someone else</span>
        </button>
      </div>
      {problem ? <p className="g-picker-problem" role="alert">{problem}</p> : null}
    </div>
  );
}

function PasswordTile({ member, busy, onCancel, onSubmit }: { member: DeviceMember; busy: boolean; onCancel: () => void; onSubmit: (password: string) => Promise<void> }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!password || busy) return;
    setError(null);
    try { await onSubmit(password); }
    catch (failure) { setError(failure instanceof ApiRequestError && failure.status === 429 ? RATE_LIMITED : failure instanceof Error ? failure.message : 'Unable to sign in'); }
  }
  return (
    <form className="g-picker-tile is-asking" onSubmit={submit}>
      <Avatar decorative name={member.display_name} size={48} />
      <span className="g-picker-name">{member.display_name}</span>
      <input aria-hidden="true" autoComplete="username" className="sr-only" readOnly tabIndex={-1} value={member.username} />
      <label className="sr-only" htmlFor={`${id}pw`}>{`Password for ${member.display_name}`}</label>
      <PasswordInput aria-describedby={error ? `${id}err` : undefined} aria-invalid={error ? true : undefined} autoComplete="current-password" autoFocus id={`${id}pw`} onChange={(event) => setPassword(event.target.value)} value={password} />
      {error ? <p className="g-field-error" id={`${id}err`} role="alert">{error}</p> : null}
      <Button busy={busy} disabled={!password} type="submit" variant="primary" wide>Continue</Button>
      <Button disabled={busy} onClick={onCancel} variant="quiet" wide>Cancel</Button>
    </form>
  );
}
