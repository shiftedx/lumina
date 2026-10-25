import { useEffect, useRef, useState } from 'react';

import { createMobileConnect, enableMobileConnect, getMobileConnect, getMobileConnectCode, listUsers, revokeMobileConnect } from '../../api';
import type { MobileConnectCode, MobileConnectStatus, UserProfile } from '../../types';
import { errorMessage } from '../../utils';
import { Button, Dialog, Field, fieldProps, Input, Select, StatusText } from '../../ui';

type Props = { open: boolean; user: UserProfile; onClose: () => void };

const origin = () => window.location.origin;
const expired = (code: MobileConnectCode | null, now: number) => !code || Number.isNaN(Date.parse(code.expires_at)) || Date.parse(code.expires_at) <= now;
const remaining = (code: MobileConnectCode, now: number) => {
  const seconds = Math.max(0, Math.ceil((Date.parse(code.expires_at) - now) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};
const revoke = (id: string) => { void revokeMobileConnect(id).catch(() => {}); };
const pairingError = (failure: unknown, fallback: string) => {
  const message = errorMessage(failure, fallback);
  return message === 'two_factor_setup_required' ? 'Turn on two-step verification in My Settings first.' : message;
};

function download(code: MobileConnectCode) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${code.qr_size} ${code.qr_size}"><rect width="100%" height="100%" fill="white"/><path d="${code.qr_path}" fill="black"/></svg>`;
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'lumina-mobile-connect.svg';
  anchor.click();
  URL.revokeObjectURL(url);
}

/** One-time pairing stays in a dialog because the secret QR should disappear with its initiating account menu. */
export function MobileConnectDialog({ open, user, onClose }: Props) {
  const [status, setStatus] = useState<MobileConnectStatus | null>(null);
  const [code, setCode] = useState<MobileConnectCode | null>(null);
  const [serverURL, setServerURL] = useState(origin);
  const [advanced, setAdvanced] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [members, setMembers] = useState<UserProfile[]>([]);
  const [memberID, setMemberID] = useState(user.id);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [now, setNow] = useState(Date.now());
  const request = useRef(0);
  const openRef = useRef(open);
  const privateCode = useRef<string | null>(null);
  const createdForOpen = useRef(false);
  const pollRequest = useRef(0);
  const [issued, setIssued] = useState<{ shared: boolean; member: string } | null>(null);

  const isOwner = user.role === 'admin';
  const isExpired = expired(code, now);
  const usableCode = code && !isExpired ? code : null;

  useEffect(() => { openRef.current = open; }, [open]);
  useEffect(() => () => {
    openRef.current = false;
    request.current += 1;
    const id = privateCode.current;
    privateCode.current = null;
    if (id) revoke(id);
  }, []);
  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    createdForOpen.current = false;
    setStatus(null); setCode(null); setIssued(null); setMessage(null); setAccessError(null); setBusy(false); setNow(Date.now()); setServerURL(origin()); setAdvanced(false); setSharing(false); setMemberID(user.id); setEmail('');
    getMobileConnect().then((value) => { if (alive) setStatus(value); }).catch((failure) => { if (alive) setAccessError(errorMessage(failure, 'Mobile access is unavailable.')); });
    if (isOwner) listUsers().then((value) => { if (alive) setMembers(value.filter((member) => member.is_active)); }).catch(() => { /* A code for the owner still works if member lookup is unavailable. */ });
    return () => { alive = false; };
  }, [isOwner, open, retry, user.id]);
  useEffect(() => {
    if (!open || !code || isExpired) return undefined;
    const pollToken = pollRequest.current;
    const poll = window.setInterval(() => {
      getMobileConnectCode(code.id).then((result) => {
        if (!openRef.current || pollToken !== request.current || result.status === 'pending') return;
        privateCode.current = null;
        setMessage(result.status === 'used' ? 'Connected.' : result.status === 'expired' ? 'This QR code expired.' : 'This QR code is no longer available.');
        setCode(null);
      }).catch(() => { /* The next refresh gives the member a route forward. */ });
    }, 2500);
    return () => window.clearInterval(poll);
  }, [code, isExpired, open]);
  useEffect(() => {
    if (!open || !code) return undefined;
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, [code, open]);
  useEffect(() => {
    if (code && isExpired) { privateCode.current = null; setMessage('This QR code expired.'); setCode(null); }
  }, [code, isExpired]);

  const makeCode = async (sendEmail = false, share = sharing) => {
    if (!status?.enabled) return;
    const token = ++request.current;
    const prior = code;
    setBusy(true); setMessage(null);
    try {
      const created = await createMobileConnect({
        server_url: serverURL,
        ...(share ? { share: true, user_id: memberID, ...(sendEmail && email.trim() ? { email: email.trim() } : {}) } : {}),
        ...(prior ? { replace_id: prior.id } : {}),
      });
      if (!openRef.current || token !== request.current) { if (!share) revoke(created.id); return; }
      privateCode.current = share ? null : created.id;
      pollRequest.current = token;
      setNow(Date.now());
      setCode(created);
      setIssued({ shared: share, member: share ? (members.find((member) => member.id === memberID)?.display_name || (memberID === user.id ? user.display_name || user.username : 'household member')) : 'you' });
      if (sendEmail) setMessage(created.email_sent ? 'Email sent.' : 'Could not send email. Download the QR instead.');
    } catch (failure) {
      if (openRef.current && token === request.current) setMessage(pairingError(failure, 'Lumina could not create a QR code.'));
    } finally {
      if (openRef.current && token === request.current) setBusy(false);
    }
  };

  useEffect(() => {
    if (!open || !status?.enabled || createdForOpen.current) return;
    createdForOpen.current = true;
    void makeCode();
  }, [open, status?.enabled]);

  const close = () => {
    openRef.current = false;
    request.current += 1;
    const id = privateCode.current;
    privateCode.current = null;
    if (id) revoke(id);
    onClose();
  };

  const enable = async () => {
    const token = ++request.current;
    setBusy(true); setMessage(null);
    try { const result = await enableMobileConnect(); if (openRef.current && token === request.current) setStatus(result); }
    catch (failure) { if (openRef.current && token === request.current) setMessage(pairingError(failure, 'Lumina could not enable mobile access.')); }
    finally { if (openRef.current && token === request.current) setBusy(false); }
  };

  return (
    <Dialog onClose={close} open={open} size="sm" title="Mobile connect">
      <section style={{ display: 'grid', gap: 16 }}>
        {message ? <StatusText tone={message === 'Connected.' ? 'ok' : 'attention'}>{message}</StatusText> : null}
        {status === null ? accessError ? <><p>{accessError}</p><div className="g-actions"><Button onClick={() => setRetry((value) => value + 1)} variant="secondary">Retry access check</Button></div></> : <p>Checking mobile access…</p> : !status.enabled ? <>
          <p>{isOwner ? 'Enable mobile access to connect Lumina.' : 'Ask your server owner to enable mobile access.'}</p>
          {isOwner ? <div className="g-actions"><Button busy={busy} onClick={() => { void enable(); }}>Enable mobile access</Button></div> : null}
        </> : <>
          {usableCode ? <div style={{ display: 'grid', gap: 12, justifyItems: 'center' }}>
            <svg aria-label="QR code for Lumina mobile connect" role="img" style={{ background: 'Canvas', color: 'CanvasText', colorScheme: 'light', maxWidth: 264, width: '100%' }} viewBox={`0 0 ${usableCode.qr_size} ${usableCode.qr_size}`}><path d={usableCode.qr_path} /></svg>
            <p>Scan with Lumina{issued?.shared ? ` · for ${issued.member}` : ''}</p>
            <small>One use · {remaining(usableCode, now)} remaining</small>
          </div> : <div className="g-actions"><Button busy={busy} onClick={() => { void makeCode(); }}>Show QR code</Button></div>}
          <details open={advanced} onToggle={(event) => setAdvanced(event.currentTarget.open)}>
            <summary>Advanced</summary>
            <Field hint="Use the address this phone can reach." label="Server URL">
              {(ids) => <Input {...fieldProps(ids)} onChange={(event) => setServerURL(event.target.value)} value={serverURL} />}
            </Field>
            <Button busy={busy} disabled={!serverURL.trim()} onClick={() => { void makeCode(); }} variant="secondary">Regenerate QR</Button>
          </details>
          <details>
            <summary>Trouble connecting?</summary>
            <p>Join your home Wi-Fi. Away from home, use an external domain or VPN. Update Server URL in Advanced.</p>
          </details>
          {isOwner ? <details>
            <summary>Share with a household member</summary>
            <p>Creates a disposable QR code that expires in 15 minutes.</p>
            <Field label="Member">
              {(ids) => <Select {...fieldProps(ids)} onChange={(event) => setMemberID(event.target.value)} value={memberID}>{[{ id: user.id, display_name: user.display_name || user.username }, ...members.filter((member) => member.id !== user.id)].map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}</Select>}
            </Field>
            <Field hint={status.email_configured ? 'Optional. Sending creates a fresh code.' : 'Email is not configured on this server.'} label="Email">
              {(ids) => <Input {...fieldProps(ids)} disabled={!status.email_configured} onChange={(event) => setEmail(event.target.value)} type="email" value={email} />}
            </Field>
            <div className="g-actions">
              <Button busy={busy} onClick={() => { setSharing(true); void makeCode(false, true); }} variant="secondary">Create share QR</Button>
              {usableCode && issued?.shared ? <Button onClick={() => download(usableCode)} variant="secondary">Download QR</Button> : null}
              {status.email_configured ? <Button busy={busy} disabled={!email.trim()} onClick={() => { setSharing(true); void makeCode(true, true); }} variant="secondary">Send email</Button> : null}
            </div>
          </details> : null}
        </>}
      </section>
    </Dialog>
  );
}
