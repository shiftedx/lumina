import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Copy } from 'lucide-react';

import { createAgentToken, createAppPassword, listConnectedApps, revokeConnectedApp, signOutAllApps } from '../../api';
import type { AppPasswordCreated, ConnectedApp, ConnectedAppCreated, ConnectedAppScope, UserProfile } from '../../types';
import { errorMessage, formatDateTime } from '../../utils';
import { Button, ConfirmDialog, Field, fieldProps, Input, Radio, StatusText } from '../../ui';
import { useAdminResource } from '../admin/useAdminResource';
import { CopyValue } from './CopyValue';

const KIND_LABELS: Record<ConnectedApp['kind'], string> = { jellyfin: 'Jellyfin app', agent: 'Agent token', app_password: 'App password' };
const SCOPE_LABELS: Record<ConnectedAppScope, string> = { read: 'Read only', write: 'Read and write' };

/** Players (Infuse, Swiftfin) and agent tokens that reach this vault on a member's behalf (ADR 0010). */
export function ConnectedApps({ user }: { user: UserProfile }) {
  const apps = useAdminResource(listConnectedApps, 'Lumina could not load connected apps.');
  const [name, setName] = useState('');
  const [scope, setScope] = useState<ConnectedAppScope>('read');
  // The only copy of a new token: component state, gone on Done or when the page closes.
  const [created, setCreated] = useState<ConnectedAppCreated | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ConnectedApp | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [appName, setAppName] = useState('');
  // Like a token, the only copy of a new app password lives in component state.
  const [appPassword, setAppPassword] = useState<AppPasswordCreated | null>(null);
  const [signingOutAll, setSigningOutAll] = useState(false);
  const tokenRef = useRef<HTMLInputElement>(null);
  const admin = user.role === 'admin';
  useEffect(() => { if (created) { tokenRef.current?.focus(); tokenRef.current?.select(); } }, [created]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy('create');
    setStatus(null);
    try {
      const next = await createAgentToken({ name: name.trim(), scope });
      setCreated(next);
      setName('');
      apps.setData((list) => [next.app, ...(list ?? [])]);
    } catch (failure) {
      setStatus(errorMessage(failure, 'Lumina could not create that token.'));
    } finally {
      setBusy(null);
    }
  }

  async function createApp(event: FormEvent) {
    event.preventDefault();
    setBusy('app');
    setStatus(null);
    try {
      const next = await createAppPassword(appName.trim());
      setAppPassword(next);
      setAppName('');
      apps.setData((list) => [next.app, ...(list ?? [])]);
    } catch (failure) {
      setStatus(errorMessage(failure, 'Lumina could not create that app password.'));
    } finally {
      setBusy(null);
    }
  }

  async function signOutAll() {
    setBusy('all');
    setStatus(null);
    try {
      const { revoked } = await signOutAllApps();
      apps.reload();
      setStatus(revoked ? `Signed out ${revoked} ${revoked === 1 ? 'app' : 'apps'}. App passwords still work.` : 'No apps were signed in. App passwords still work.');
    } catch (failure) {
      setStatus(errorMessage(failure, 'Lumina could not sign out your apps.'));
    } finally {
      setBusy(null);
      setSigningOutAll(false);
    }
  }

  async function revoke(app: ConnectedApp) {
    setBusy(app.id);
    setStatus(null);
    try {
      await revokeConnectedApp(app.id);
      apps.setData((list) => list?.filter((entry) => entry.id !== app.id) ?? null);
      if (created?.app.id === app.id) setCreated(null);
      setStatus(`${app.device_name} was revoked. It has to sign in again.`);
    } catch (failure) {
      setStatus(errorMessage(failure, 'Lumina could not revoke that app.'));
    } finally {
      setBusy(null);
      setRevoking(null); // the dialog keeps its target until the revoke settles
    }
  }

  async function copy() {
    if (!created) return;
    try { await navigator.clipboard.writeText(created.token); setStatus('Token copied.'); } catch { setStatus('Copy is unavailable here. Select the token and copy it.'); }
  }

  return (
    <div className="connected-apps">
      {apps.error ?<p className="auth-error" role="alert">{apps.error}</p> : !apps.data ? <p className="g-setting-note" role="status">Loading connected apps…</p> : apps.data.length ? (
        <div aria-label="Connected apps" className="admin-table-scroll" role="region" tabIndex={0}>
          <table className="g-table">
            <thead><tr><th scope="col">Name</th><th scope="col">Kind</th><th scope="col">Access</th><th scope="col">App</th><th scope="col">Last seen</th>{admin ? <th scope="col">Owner</th> : null}<th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {apps.data.map((app) => (
                <tr key={app.id}>
                  <th scope="row">{app.device_name}</th>
                  <td>{KIND_LABELS[app.kind]}</td>
                  <td>{SCOPE_LABELS[app.scope]}</td>
                  <td>{[app.client, app.client_version].filter(Boolean).join(' ') || '—'}</td>
                  <td>{formatDateTime(app.last_seen_at, 'Not yet')}</td>
                  {admin ? <td>{app.owner_display_name || '—'}</td> : null}
                  <td><Button aria-label={`Revoke ${app.device_name}`} disabled={busy !== null} onClick={() => setRevoking(app)} variant="danger">Revoke</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="g-setting-note">No apps yet. Apps appear here after their first sign-in.</p>}
      {apps.data?.length ? <div className="g-actions"><Button disabled={busy !== null} onClick={() => setSigningOutAll(true)} variant="secondary">Sign out all apps</Button></div> : null}
      {appPassword ? (
        <div className="connected-app-token">
          <strong>Type these into {appPassword.app.device_name}. Lumina shows the app password only once.</strong>
          <ol className="g-setting-note">
            <li>In Infuse: Add Files, then Jellyfin. In Swiftfin or another app, add a server.</li>
            <li>Type the server address, username and app password below.</li>
          </ol>
          {appPassword.local_server_address && appPassword.server_address && appPassword.local_server_address !== appPassword.server_address
            ? <><CopyValue label="Server address at home" value={appPassword.local_server_address} /><CopyValue label="Server address away from home" value={appPassword.server_address} /></>
            : appPassword.server_address
            ? <CopyValue label="Server address" value={appPassword.server_address} />
            : <><p className="g-setting-note">Use the address you are using now to open this page.</p><CopyValue label="Server address" value={window.location.origin} /></>}
          <CopyValue label="Username" value={appPassword.username} />
          <CopyValue copyLabel="Copy app password" label="App password" value={appPassword.password} />
          <div className="g-actions"><Button onClick={() => setAppPassword(null)} variant="primary">Done</Button></div>
        </div>
      ) : null}
      <form className="connected-app-create" onSubmit={(event) => void createApp(event)}>
        <h3>Add an app (Infuse, Swiftfin, …)</h3>
        <p className="g-setting-note">Apps like Infuse can't ask for your two-step code, so they use an app password instead. Revoke it here any time.</p>
        <Field label="App name">{(ids) => <Input {...fieldProps(ids)} maxLength={120} onChange={(event) => setAppName(event.target.value)} placeholder="Infuse on the living-room Apple TV" required value={appName} />}</Field>
        <Button busy={busy === 'app'} disabled={!appName.trim() || busy !== null} type="submit" variant="primary">{busy === 'app' ? 'Creating…' : 'Create app password'}</Button>
      </form>
      {created ? (
        <div className="connected-app-token">
          <strong>Copy this token now. Lumina shows it only once.</strong>
          <Field label={`Token for ${created.app.device_name}`}>{(ids) => <Input {...fieldProps(ids)} onFocus={(event) => event.currentTarget.select()} readOnly ref={tokenRef} spellCheck={false} value={created.token} />}</Field>
          <div className="g-actions">
            <Button icon={<Copy />} onClick={() => void copy()} variant="secondary">Copy</Button>
            <Button onClick={() => setCreated(null)} variant="quiet">Done</Button>
          </div>
        </div>
      ) : null}
      <form className="connected-app-create" onSubmit={(event) => void create(event)}>
        <h3>Agent token</h3>
        <p className="g-setting-note">For a script or home automation that reads this vault as you. It sees only what you can see.</p>
        <Field label="Token name">{(ids) => <Input {...fieldProps(ids)} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="Home Assistant" required value={name} />}</Field>
        <fieldset className="g-fieldset"><legend>Access</legend>
          {(Object.keys(SCOPE_LABELS) as ConnectedAppScope[]).map((value) => <Radio checked={scope === value} key={value} label={SCOPE_LABELS[value]} name="agent-token-scope" onChange={() => setScope(value)} />)}
        </fieldset>
        <Button busy={busy === 'create'} disabled={!name.trim() || busy !== null} type="submit" variant="primary">{busy === 'create' ? 'Creating…' : 'Create agent token'}</Button>
      </form>
      <p aria-live="polite" className="g-setting-note">{status ?? ''}</p>
      <ConfirmDialog
        body="Every Jellyfin app and agent token signed in as you has to sign in again. App passwords keep working."
        busy={busy === 'all'}
        confirmLabel="Sign out all apps"
        onCancel={() => { if (busy === null) setSigningOutAll(false); }}
        onConfirm={() => { if (busy === null) void signOutAll(); }}
        open={signingOutAll}
        title="Sign out all apps?"
      />
      <ConfirmDialog
        body={revoking?.kind === 'app_password' ? 'The app that uses this password is signed out and cannot sign in again.' : 'It will have to sign in again to reach this vault.'}
        busy={busy === revoking?.id}
        confirmLabel="Revoke"
        danger
        onCancel={() => { if (busy === null) setRevoking(null); }}
        onConfirm={() => { if (revoking && busy === null) void revoke(revoking); }}
        open={revoking !== null}
        title={revoking ? `Revoke ${revoking.device_name}?` : 'Revoke this app?'}
      />
    </div>
  );
}
