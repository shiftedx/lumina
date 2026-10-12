import { type FormEvent, useState } from 'react';
import type { CollectionLoadProblem } from '../../workspace';
import { cx } from '../../ui/cx';
import { BrandMark, Button, ErrorState, Field, fieldProps, Input, Masthead, PasswordInput, Skeleton } from '../../ui';
import { resetDeviceRing, useDeviceRing } from './deviceRing';
import { ShowcaseArt, ShowcaseCaption, useShowcase } from './Showcase';
import { SignInPanel } from './SignInPanel';
import { TwoFactorStep } from './TwoFactorStep';
import { WhoIsWatching } from './WhoIsWatching';
import './auth.css';

export type AccountLink = { kind: 'invite' | 'reset'; token: string };

/** One-time links carry their secret in the URL fragment, which never reaches the server or Referer. */
export function readAccountLink(): AccountLink | null {
  const match = /^#(invite|reset)=([\w-]+)$/.exec(typeof window === 'undefined' ? '' : window.location.hash);
  return match ? { kind: match[1] as AccountLink['kind'], token: match[2] } : null;
}

export interface AuthScreenProps {
  stage: 'loading' | 'login' | 'setup' | 'invite' | 'reset';
  busy: boolean;
  error: string | null;
  onLogin: (username: string, password: string, rememberOnDevice: boolean) => void;
  onSetup: (username: string, displayName: string, password: string) => void;
  onRetrySession: () => void;
  onCancel?: () => void;
  sessionProblem: CollectionLoadProblem | null;
  /** A remembered member was chosen on "Who's watching?". */
  onSwitched: () => void;
  /** Set after the password was accepted for an account with two-step verification. */
  twoFactor?: { challenge: string; onVerified: () => void | Promise<void>; onBack: (message?: string) => void } | null;
}

const COPY = {
  loading: { kicker: undefined, title: 'Opening your vault', lede: undefined },
  login: { kicker: 'Sign in', title: 'Welcome home', lede: 'Sign in with your Lumina account.' },
  setup: { kicker: 'First-run setup', title: 'Make Lumina yours', lede: 'Create the first household account. Technical setup can wait until you are inside.' },
  invite: { kicker: 'Invitation', title: 'Join this household', lede: 'You were invited to this Lumina vault. Your library activity stays private to you.' },
  reset: { kicker: 'Password reset', title: 'Choose a new password', lede: 'Choose a new password, then sign in with it.' },
} as const;
const PRIMARY = { login: 'Sign in', setup: 'Create household vault', invite: 'Join household', reset: 'Set new password' } as const;

/** Sign-in, first-run setup, invitation and reset. No library artwork before sign-in (Decision 23): the
 *  art behind it is the public release showcase (Showcase.tsx), and without it the page keeps its calm two columns. */
export function AuthScreen({ stage: requestedStage, busy, error, onLogin, onSetup, onRetrySession, onCancel, sessionProblem, onSwitched, twoFactor }: AuthScreenProps) {
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [signingIn, setSigningIn] = useState(false); // "Someone else" chosen over the picker
  // Only a browser that already holds a ring cookie gets members back; nothing lists them otherwise.
  const ring = useDeviceRing(requestedStage === 'login');
  const show = useShowcase(requestedStage !== 'loading');
  const pickerAvailable = requestedStage === 'login' && Boolean(ring.members?.length);
  // The form waits for the ring's answer (at most 3 s) so a remembered household never sees it flash first.
  const stage = requestedStage === 'login' && ring.members === null ? 'loading' : requestedStage;
  const reset = stage === 'reset';
  const copy = COPY[stage];

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!busy) onSetup(username, displayName, password);
  }

  const picker = pickerAvailable && !signingIn && ring.members ? (
    <WhoIsWatching members={ring.members} onRefresh={() => { resetDeviceRing(); }} onSomeoneElse={() => setSigningIn(true)} onSwitched={onSwitched} variant="page" />
  ) : null;

  const form = stage === 'loading' ? (
    <Skeleton count={3} label="Preparing your collection…" shape="line" />
  ) : stage === 'login' && twoFactor ? (
    <TwoFactorStep {...twoFactor} />
  ) : stage === 'login' ? (
    <SignInPanel busy={busy} error={error} onChoosePicker={pickerAvailable ? () => setSigningIn(false) : undefined} onSubmit={onLogin} />
  ) : (
    <form className="g-auth-form" noValidate onSubmit={submit}>
      {reset ? null : <Field label="Username">{(ids) => <Input {...fieldProps(ids)} autoComplete="username" onChange={(event) => setUsername(event.target.value)} value={username} />}</Field>}
      {reset ? null : <Field label="Display name">{(ids) => <Input {...fieldProps(ids)} autoComplete="name" onChange={(event) => setDisplayName(event.target.value)} value={displayName} />}</Field>}
      <Field error={error} hint="At least 12 characters." label={reset ? 'New password' : 'Password'}>
        {(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="new-password" onChange={(event) => setPassword(event.target.value)} value={password} />}
      </Field>
      <Button busy={busy} disabled={(!reset && !username.trim()) || !password} type="submit" variant="primary" wide>{PRIMARY[stage]}</Button>
      {onCancel ? <Button onClick={onCancel} variant="quiet" wide>Back to sign in</Button> : null}
    </form>
  );

  // The panel comes first in the DOM so Tab reaches the form before the showcase controls; the grid places it right.
  return (
    <main className={cx('g-auth', show && 'has-art g-dark')}>
      {show ? <ShowcaseArt show={show} /> : null}
      <div className="g-auth-bar"><BrandMark size="bar" /></div>
      <section aria-labelledby="auth-title" className="g-auth-main">
        {picker ?? (
          <div className="g-auth-column">
            <Masthead headingId="auth-title" kicker={copy.kicker} lede={copy.lede} title={copy.title} />
            {form}
            {sessionProblem ? <ErrorState body={sessionProblem.message} onRetry={onRetrySession} retrying={busy} retryLabel="Try opening the vault again" title="The vault did not respond" /> : null}
          </div>
        )}
      </section>
      <aside className="g-auth-side">
        <BrandMark size="hero" />
        {show ? <ShowcaseCaption show={show} /> : (
          <>
            <p className="g-auth-quote">Your films, your shows, your household.</p>
            <p className="g-label g-auth-host">Lumina · {window.location.hostname}</p>
          </>
        )}
      </aside>
    </main>
  );
}
