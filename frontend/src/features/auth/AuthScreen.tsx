import { type FormEvent, useEffect, useState } from 'react';
import { getOAuthProviders, startOAuth, type OAuthProvider } from '../../api';
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

export type OAuthCallback = { kind: 'error' } | { kind: 'two-factor'; challenge: string };
export const OAUTH_ERROR_MESSAGE = "We couldn't complete sign-in with that provider. Try again or use your Lumina account.";

/** One-time links carry their secret in the URL fragment, which never reaches the server or Referer. */
export function readAccountLink(): AccountLink | null {
  const match = /^#(invite|reset)=([\w-]+)$/.exec(typeof window === 'undefined' ? '' : window.location.hash);
  return match ? { kind: match[1] as AccountLink['kind'], token: match[2] } : null;
}

/** OAuth callbacks keep the two-factor challenge in memory only; errors never carry provider details. */
export function readOAuthCallback(): OAuthCallback | null {
  if (typeof window === 'undefined') return null;
  const hash = window.location.hash;
  if (hash === '#oauth-error') return { kind: 'error' };
  const prefix = '#oauth-two-factor=';
  if (!hash.startsWith(prefix)) return null;
  try {
    const challenge = decodeURIComponent(hash.slice(prefix.length));
    return challenge ? { kind: 'two-factor', challenge } : { kind: 'error' };
  } catch {
    return { kind: 'error' };
  }
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
  setup: { kicker: 'Your household vault', title: 'Make Lumina yours', lede: 'Create your owner account. Next, we’ll help you connect your media and make your Home yours.' },
  invite: { kicker: 'Your invitation', title: 'Step inside the vault.', lede: 'Create your account, then make your Home yours. Your watch history stays private to you.' },
  reset: { kicker: 'Password reset', title: 'Choose a new password', lede: 'Choose a new password, then sign in with it.' },
} as const;
const PRIMARY = { login: 'Sign in', setup: 'Create household vault', invite: 'Create my account', reset: 'Set new password' } as const;

/** Sign-in, first-run setup, invitation and reset. No library artwork before sign-in (Decision 23): the
 *  art behind it is the public release showcase (Showcase.tsx), and without it the page keeps its calm two columns. */
export function AuthScreen({ stage: requestedStage, busy, error, onLogin, onSetup, onRetrySession, onCancel, sessionProblem, onSwitched, twoFactor }: AuthScreenProps) {
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [signingIn, setSigningIn] = useState(false); // "Someone else" chosen over the picker
  const [oauthProviders, setOAuthProviders] = useState<OAuthProvider[]>([]);
  const [oauthBusyProvider, setOAuthBusyProvider] = useState<string | null>(null);
  const [oauthError, setOAuthError] = useState<string | null>(null);
  // Only a browser that already holds a ring cookie gets members back; nothing lists them otherwise.
  const ring = useDeviceRing(requestedStage === 'login');
  const invitation = requestedStage === 'invite';
  const showcase = useShowcase(requestedStage !== 'loading' && !invitation);
  const show = invitation ? null : showcase;
  const pickerAvailable = requestedStage === 'login' && Boolean(ring.members?.length);
  // The form waits for the ring's answer (at most 3 s) so a remembered household never sees it flash first.
  const stage = requestedStage === 'login' && ring.members === null ? 'loading' : requestedStage;
  const reset = stage === 'reset';
  const copy = COPY[stage];

  useEffect(() => {
    if (requestedStage !== 'login') {
      setOAuthProviders([]);
      setOAuthError(null);
      setOAuthBusyProvider(null);
      return undefined;
    }
    let active = true;
    void getOAuthProviders().then((providers) => {
      if (!active) return;
      setOAuthProviders((Array.isArray(providers) ? providers : []).filter((provider): provider is OAuthProvider => Boolean(provider) && typeof provider.id === 'string' && Boolean(provider.id.trim()) && typeof provider.name === 'string' && Boolean(provider.name.trim())));
    }).catch(() => {
      // OAuth is optional: a provider outage never removes or disables the local form.
      if (active) setOAuthProviders([]);
    });
    return () => { active = false; };
  }, [requestedStage]);

  function handlePasswordSubmit(nextUsername: string, nextPassword: string, rememberOnDevice: boolean) {
    setOAuthError(null);
    onLogin(nextUsername, nextPassword, rememberOnDevice);
  }

  async function handleOAuth(providerId: string, rememberOnDevice: boolean) {
    if (oauthBusyProvider) return;
    setOAuthBusyProvider(providerId);
    setOAuthError(null);
    try {
      const result = await startOAuth(providerId, rememberOnDevice);
      if (!result.authorization_url) throw new Error('Missing authorization URL');
      window.location.assign(result.authorization_url);
    } catch {
      setOAuthError("We couldn't start sign-in with that provider. Try again or use your Lumina account.");
    } finally {
      setOAuthBusyProvider(null);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!busy) onSetup(username, displayName, password);
  }

  const picker = pickerAvailable && !signingIn && !twoFactor && !error && ring.members ? (
    <WhoIsWatching members={ring.members} onRefresh={() => { resetDeviceRing(); }} onSomeoneElse={() => setSigningIn(true)} onSwitched={onSwitched} variant="page" />
  ) : null;

  const form = stage === 'loading' ? (
    <Skeleton count={3} label="Preparing your collection…" shape="line" />
  ) : stage === 'login' && twoFactor ? (
    <TwoFactorStep {...twoFactor} />
  ) : stage === 'login' ? (
    <SignInPanel busy={busy} error={error} oauthBusyProvider={oauthBusyProvider} oauthError={oauthError} oauthProviders={oauthProviders} onChoosePicker={pickerAvailable ? () => setSigningIn(false) : undefined} onOAuth={(providerId, rememberOnDevice) => { void handleOAuth(providerId, rememberOnDevice); }} onSubmit={handlePasswordSubmit} />
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
    <main className={cx('g-auth', show && 'has-art g-dark', invitation && 'is-invitation')}>
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
        {invitation ? (
          <div className="g-invite-preview">
            <img alt="An illustrated vault filled with film reels, records, and books." className="g-invite-vault" height={911} src="/onboarding/vault.jpg" width={1100} />
            <div className="g-invite-promise"><p className="g-label">A shared vault. Your own Home.</p><h2>Find your next favorite.</h2><p>Discover what’s shared with you, follow the creators you love, and pick up right where you left off.</p></div>
          </div>
        ) : show ? <ShowcaseCaption show={show} /> : (
          <>
            <p className="g-auth-quote">Your films, your shows, your household.</p>
            <p className="g-label g-auth-host">Lumina · {window.location.hostname}</p>
          </>
        )}
      </aside>
    </main>
  );
}
