import { type MouseEvent, useEffect, useRef, useState } from 'react';

import { getAdminSettings, listUsers, updateAdminSettings } from '../../api';
import { routePath } from '../../app/routes';
import { Avatar, Button, Dialog, Field, fieldProps, Input, StatusText, Switch } from '../../ui';
import { errorMessage as errorText, formatDateTime as date } from '../../utils';
import { InviteDialog } from './InviteDialog';
import { getLocalAddress, getPublicAddress, type Invite, type IssuedInvite, limitsSummary, librariesSummary, listInvites, listSections, type MemberRow, putLocalAddress, putPublicAddress, resendInvite, revokeInvite, screenTimeLabel, withDefaults } from './memberAccess';
import { useAdminResource } from './useAdminResource';

export const nameOf = (member: { display_name: string; username: string }) => member.display_name || member.username;
export const roleLabel = (role: 'admin' | 'viewer') => (role === 'admin' ? 'Vault owner' : 'Household member');

export function CopyableLink({ label, url, copyLabel }: { label: string; url: string; copyLabel?: string }) {
  const [status, setStatus] = useState('');
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setStatus('Link copied.');
    } catch {
      setStatus('Copy was blocked. Select the link and copy it manually.');
    }
  }
  return (
    <div className="admin-issued-link">
      <Field label={label}>{(ids) => <Input {...fieldProps(ids)} className="g-mono" onFocus={(event) => event.currentTarget.select()} readOnly value={url} />}</Field>
      <Button onClick={() => { void copy(); }} variant="primary">{copyLabel ?? `Copy ${label.toLowerCase()}`}</Button>
      <p className="g-setting-note">Shown only now. If it is lost, create a new one.</p>
      <span aria-live="polite" role="status">{status ? <StatusText tone="muted">{status}</StatusText> : null}</span>
    </div>
  );
}

/** Settings → Members: every account as a row that opens its member page (avatar, role, libraries, limits, today's screen time). */
export function MembersList({ onOpenMember }: { onOpenMember?: (id: string) => void }) {
  const { data, loading, error } = useAdminResource(listUsers as () => Promise<MemberRow[]>, 'Unable to load household members.');
  const { data: sections } = useAdminResource(listSections, 'Unable to load libraries.');
  function open(event: MouseEvent<HTMLAnchorElement>, id: string) {
    // Plain clicks stay in the app; modified clicks keep open-in-new-tab.
    if (!onOpenMember || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    onOpenMember(id);
  }
  return (
    <div aria-busy={loading}>
      {loading && !data ? <p className="g-setting-note" role="status">Loading household members…</p> : null}
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <ul aria-label="Household members" className="g-list">
        {(data ?? []).map((member) => {
          const name = nameOf(member);
          const access = withDefaults(member.access);
          const limits = member.role === 'admin' ? 'Owner — no limits' : [librariesSummary(access, sections), limitsSummary(access) || 'no limits'].join(' · ');
          return (
            <li key={member.id}>
              <a className="g-list-row g-member-link" href={routePath({ surface: 'settings', section: 'members', memberId: member.id })} onClick={(event) => open(event, member.id)}>
                <Avatar decorative name={name} size={34} />
                <span className="g-list-row-copy">
                  <span className="g-list-row-title">{name}{member.is_active ? null : <span className="g-list-row-meta"> · Deactivated</span>}</span>
                  <span className="g-list-row-meta">{roleLabel(member.role)} · {limits}</span>
                </span>
                <span className="g-list-row-meta">{member.role === 'admin' ? '' : screenTimeLabel(member.screen_time_today_seconds)}</span>
              </a>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const VISIBLE = new Set(['pending', 'expired']);

/** Invite someone by email or link, and the invitations still waiting (resend renews the link, revoke kills it). */
export function MemberInvitations({ onMessage }: { onMessage: (message: string) => void }) {
  const { data, setData, error, reload } = useAdminResource(listInvites, 'Unable to load invitations.');
  const [inviting, setInviting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [issued, setIssued] = useState<IssuedInvite | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function act(invite: Invite, action: 'resend' | 'revoke') {
    setBusy(invite.id);
    setRowError(null);
    try {
      if (action === 'revoke') {
        await revokeInvite(invite.id);
        if (!mounted.current) return;
        setData((list) => list?.map((entry) => (entry.id === invite.id ? { ...entry, status: 'revoked' } : entry)) ?? null);
        onMessage('Invitation revoked. Its link no longer works.');
      } else {
        const next = await resendInvite(invite.id);
        if (!mounted.current) return;
        setData((list) => list?.map((entry) => (entry.id === invite.id ? next : entry)) ?? null);
        if (next.email_sent) onMessage(`Invitation sent again to ${invite.email}.`);
        else setIssued(next);
      }
    } catch (failure) {
      if (mounted.current) setRowError(errorText(failure, `Unable to ${action} the invitation.`));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  const waiting = (data ?? []).filter((invite) => VISIBLE.has(invite.status));
  return (
    <div className="g-control-stack">
      <div className="g-actions"><Button onClick={() => setInviting(true)} variant="primary">Invite someone</Button></div>
      {error || rowError ? <p className="auth-error" role="alert">{error || rowError}</p> : null}
      {waiting.length ? (
        <ul aria-label="Waiting invitations" className="g-list">
          {waiting.map((invite) => {
            const who = invite.access?.display_name ? `${invite.access.display_name} (${invite.email})` : invite.email;
            const sent = invite.sent_at ? `Sent ${date(invite.sent_at)}` : 'Link only';
            return (
              <li className="g-list-row" key={invite.id}>
                <span className="g-list-row-copy">
                  <span className="g-list-row-title">{who}</span>
                  <span className="g-list-row-meta">{[invite.libraries?.join(', '), sent, invite.status === 'expired' ? 'Expired' : `Expires ${date(invite.expires_at)}`].filter(Boolean).join(' · ')}</span>
                </span>
                <span className="g-list-row-actions">
                  <Button aria-label={`Resend invitation to ${invite.email}`} busy={busy === invite.id} onClick={() => { void act(invite, 'resend'); }} variant="quiet">Resend</Button>
                  {invite.status === 'pending' ? <Button aria-label={`Revoke invitation to ${invite.email}`} disabled={busy === invite.id} onClick={() => { void act(invite, 'revoke'); }} variant="quiet">Revoke</Button> : null}
                </span>
              </li>
            );
          })}
        </ul>
      ) : data ? <p className="g-setting-note">No invitations waiting.</p> : null}
      <InviteDialog onClose={() => setInviting(false)} onIssued={reload} open={inviting} />
      <Dialog footer={<Button onClick={() => setIssued(null)} variant="primary">Done</Button>} onClose={() => setIssued(null)} open={issued !== null} size="md" title="Invitation renewed">
        <p className="g-setting-note"><StatusText tone="attention">Email isn’t set up, so nothing was sent. Copy this link and send it yourself; the old link no longer works.</StatusText></p>
        <CopyableLink copyLabel="Copy link" label="Invite link" url={issued?.invitation_url ?? ''} />
      </Dialog>
    </div>
  );
}

/** The https origin people outside the home use; invite links point here. Saves on its own button. */
type AddressProps<T> = { label: string; placeholder: string; load: () => Promise<T>; store: (value: string | null) => Promise<T>; read: (data: T) => string | null; note?: (data: T) => string | null };

function AddressSetting<T>({ label, placeholder, load, store, read, note }: AddressProps<T>) {
  const { data, setData, error } = useAdminResource(load, `Unable to load the ${label.toLowerCase()}.`);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const value = draft ?? (data ? read(data) : null) ?? '';
  const blocked = data && note ? note(data) : null;
  async function save() {
    setSaving(true);
    setProblem(null);
    setSaved(false);
    try {
      setData(await store(value.trim() || null));
      setDraft(null);
      setSaved(true);
    } catch (failure) {
      setProblem(errorText(failure, `Enter an address like ${placeholder}.`));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="g-control-stack">
      <Field error={problem ?? error} hideLabel label={label}>
        {(ids) => <Input {...fieldProps(ids)} autoComplete="off" disabled={Boolean(blocked)} inputMode="url" onChange={(event) => { setDraft(event.target.value); setSaved(false); }} placeholder={placeholder} type="url" value={value} />}
      </Field>
      {blocked ? <p className="g-setting-note">{blocked}</p> : null}
      <div className="g-actions">
        {saved ? <span role="status"><StatusText tone="ok">Saved</StatusText></span> : null}
        <Button busy={saving} disabled={draft === null || !data} onClick={() => { void save(); }}>Save address</Button>
      </div>
    </div>
  );
}

export function PublicAddress() {
  return <AddressSetting label="Public address" load={getPublicAddress} placeholder="https://lumina.example.com" read={(data) => data.public_address} store={putPublicAddress} />;
}

export function LocalAddress() {
  return (
    <AddressSetting
      label="Local address" load={getLocalAddress} placeholder="http://lumina.home.arpa" read={(data) => data.local_address} store={putLocalAddress}
      note={(data) => (data.lan_http || data.local_address ? null : 'Available when Lumina runs in LAN HTTP mode (LUMINA_LAN_HTTP=true).')}
    />
  );
}

/** Household rule: vault owners must use two-step verification. The server refuses until the acting owner has turned it on. */
export function OwnerTwoFactorSwitch() {
  const settings = useAdminResource(getAdminSettings, 'Unable to load this setting.');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function change(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      await updateAdminSettings({ require_owner_two_factor: next });
      settings.setData((current) => current && { ...current, require_owner_two_factor: next });
    } catch (failure) {
      setError(errorText(failure, 'Unable to save this setting.'));
    } finally {
      setBusy(false);
    }
  }
  if (settings.error) return <p className="auth-error" role="alert">{settings.error}</p>;
  if (!settings.data) return <p className="g-setting-note" role="status">Loading…</p>;
  return (
    <div className="g-control-stack">
      <Switch busy={busy} checked={Boolean(settings.data.require_owner_two_factor)} label="Require two-step verification for vault owners" onChange={(next) => { void change(next); }} />
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
    </div>
  );
}
