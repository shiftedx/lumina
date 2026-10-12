import { useEffect, useState } from 'react';
import { ApiRequestError, loginSession } from '../../api';
import { MEMBER_PICKER_EVENT, onCommand } from '../../app/commands';
import type { UserProfile } from '../../types';
import { Dialog } from '../../ui';
import { resetDeviceRing, useDeviceRing } from './deviceRing';
import { TwoFactorStep } from './TwoFactorStep';
import { SignInPanel } from './SignInPanel';
import { type BeforeMemberChange, changeMember, RATE_LIMITED, WhoIsWatching } from './WhoIsWatching';
import './auth.css';

/** Ring members other than the active one, or null while unknown. */
export function useDeviceRingSize(): number | null {
  const { members } = useDeviceRing();
  return members === null ? null : members.filter((member) => !member.active).length;
}

/** The signed-in "Who's watching?" dialog, opened by `openMemberPicker()`. */
export function MemberPickerHost({ currentUser, onSwitched, beforeChange }: { currentUser: UserProfile; onSwitched: () => void; beforeChange?: BeforeMemberChange }) {
  const [open, setOpen] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<string | null>(null);
  const { members, refresh } = useDeviceRing();
  useEffect(() => onCommand(MEMBER_PICKER_EVENT, () => { setSigningIn(false); setChallenge(null); setError(null); setOpen(true); void refresh(); }), [refresh]);
  const done = () => { setOpen(false); resetDeviceRing(); onSwitched(); };

  async function signIn(username: string, password: string, rememberOnDevice: boolean) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const result = await changeMember(beforeChange, () => loginSession({ username: username.trim(), password, remember_on_device: rememberOnDevice }), done);
      if ('two_factor_required' in result) { setChallenge(result.challenge); return; }
      done();
    } catch (failure) {
      setError(failure instanceof ApiRequestError && failure.status === 429 ? RATE_LIMITED : failure instanceof Error ? failure.message : 'Unable to sign in');
    } finally { setBusy(false); }
  }

  return (
    <Dialog busy={busy} onClose={() => setOpen(false)} open={open} size="lg" title={signingIn ? 'Sign in' : "Who's watching?"}>
      {challenge
        ? <TwoFactorStep challenge={challenge} onBack={(message) => { setChallenge(null); setError(message ?? null); }} onVerified={done} />
        : signingIn
        ? <SignInPanel busy={busy} error={error} onChoosePicker={() => setSigningIn(false)} onSubmit={(username, password, remember) => void signIn(username, password, remember)} />
        : <WhoIsWatching beforeChange={beforeChange} currentName={currentUser.display_name || currentUser.username} members={members ?? []} onRefresh={() => void refresh()} onSomeoneElse={() => setSigningIn(true)} onSwitched={done} variant="dialog" />}
    </Dialog>
  );
}
