import { type FormEvent, useState } from 'react';

import { Button, Checkbox, Dialog, Field, fieldProps, Input, Select, StatusText, TextButton } from '../../ui';
import { errorMessage } from '../../utils';
import { settingDomId } from '../settings/SettingRow';
import { CopyableLink } from './AdminMembers';
import { createInvite, getPublicAddress, type InviteAccess, type IssuedInvite, listSections, NO_LIMITS, PRESETS, type PresetId } from './memberAccess';
import { useAdminResource } from './useAdminResource';

type Limits = PresetId | 'none';
const LIMIT_OPTIONS: { value: Limits; label: string }[] = [
  ...(Object.keys(PRESETS) as PresetId[]).map((id) => ({ value: id, label: `${PRESETS[id].label} — ${PRESETS[id].hint}` })),
  { value: 'none', label: 'No limits' },
];

/** The access an invite carries: the chosen limits, then the libraries and permissions picked here. */
export function inviteAccess(limits: Limits, sections: string[] | null, canDownload: boolean, canRequest: boolean): InviteAccess {
  const base = limits === 'none' ? NO_LIMITS : { ...NO_LIMITS, ...PRESETS[limits].access };
  return { ...structuredClone(base), sections, can_download: canDownload, can_request: canRequest };
}

/** Invite someone from outside: email, name, libraries, limits, permissions; then send the email or copy the link. */
export function InviteDialog({ open, onClose, onIssued }: { open: boolean; onClose: () => void; onIssued: () => void }) {
  return open ? <InviteForm onClose={onClose} onIssued={onIssued} /> : null;
}

function InviteForm({ onClose, onIssued }: { onClose: () => void; onIssued: () => void }) {
  const { data: sections } = useAdminResource(listSections, 'Unable to load libraries.');
  const { data: address } = useAdminResource(getPublicAddress, '');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [all, setAll] = useState(false);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [limits, setLimits] = useState<Limits>('guest');
  const [canDownload, setCanDownload] = useState(false);
  const [canRequest, setCanRequest] = useState(false);
  const [problem, setProblem] = useState<{ field: 'email' | 'libraries' | 'form'; text: string } | null>(null);
  const [busy, setBusy] = useState<'send' | 'link' | null>(null);
  const [issued, setIssued] = useState<{ invite: IssuedInvite; send: boolean } | null>(null);

  async function submit(send: boolean) {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) { setProblem({ field: 'email', text: 'Enter an email address like name@example.com.' }); return; }
    if (!all && !chosen.size) { setProblem({ field: 'libraries', text: 'Choose at least one library to share.' }); return; }
    setProblem(null);
    setBusy(send ? 'send' : 'link');
    try {
      const sectionIds = all ? null : (sections ?? []).map((entry) => entry.id).filter((id) => chosen.has(id));
      const invite = await createInvite({ email: email.trim(), ...(name.trim() ? { display_name: name.trim() } : {}), access: inviteAccess(limits, sectionIds, canDownload, canRequest), send });
      setIssued({ invite, send });
      onIssued();
    } catch (failure) {
      setProblem({ field: 'form', text: errorMessage(failure, 'Unable to create the invitation.') });
    } finally {
      setBusy(null);
    }
  }

  function goToAddress() {
    onClose();
    // The row sits in this same section; focus lands after the dialog has given focus back.
    window.setTimeout(() => document.getElementById(settingDomId('members.public-address'))?.querySelector('input')?.focus(), 0);
  }

  if (issued) {
    const { invite, send } = issued;
    return (
      <Dialog footer={<Button onClick={onClose} variant="primary">Done</Button>} onClose={onClose} open size="md" title="Invitation ready">
        <p className="g-setting-note">
          {send && invite.email_sent ? <StatusText tone="ok">Sent to {invite.email}. The link works for 7 days.</StatusText>
            : send ? <StatusText tone="attention">Email isn’t set up, so nothing was sent. Copy this link and send it yourself.</StatusText>
              : 'Send this link to them yourself. It works once, for 7 days.'}
        </p>
        <CopyableLink copyLabel="Copy link" label="Invite link" url={invite.invitation_url} />
      </Dialog>
    );
  }

  const onSubmit = (event: FormEvent) => { event.preventDefault(); void submit(true); };
  return (
    <Dialog
      busy={busy !== null}
      footer={<>
        <Button busy={busy === 'link'} disabled={busy === 'send'} onClick={() => { void submit(false); }}>Copy link instead</Button>
        <Button busy={busy === 'send'} disabled={busy === 'link'} form="invite-form" type="submit" variant="primary">Send email</Button>
      </>}
      description="They get a link that works once, for 7 days, with the libraries and limits you choose here."
      onClose={onClose}
      open
      size="md"
      title="Invite someone"
    >
      <form className="g-invite-form" id="invite-form" noValidate onSubmit={onSubmit}>
        <Field error={problem?.field === 'email' ? problem.text : null} label="Email" required>
          {(ids) => <Input {...fieldProps(ids)} autoComplete="off" onChange={(event) => setEmail(event.target.value)} type="email" value={email} />}
        </Field>
        <Field hint="Their name on this server; they can change it." label="Name">
          {(ids) => <Input {...fieldProps(ids)} autoComplete="off" onChange={(event) => setName(event.target.value)} value={name} />}
        </Field>
        <fieldset className="g-fieldset">
          <legend>Libraries</legend>
          <Checkbox checked={all} hint="Includes libraries added later." label="All libraries" onChange={(event) => setAll(event.currentTarget.checked)} />
          {all ? null : (sections ?? []).map((section) => (
            <Checkbox checked={chosen.has(section.id)} hint={`${section.count.toLocaleString()} titles`} key={section.id} label={section.label}
              onChange={(event) => { const on = event.currentTarget.checked; setChosen((set) => { const next = new Set(set); if (on) next.add(section.id); else next.delete(section.id); return next; }); }} />
          ))}
          {problem?.field === 'libraries' ? <p className="auth-error" role="alert">{problem.text}</p> : null}
        </fieldset>
        <Field hint="Fine-tune ratings, hours and streaming on their member page later." label="Limits">
          {(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => { const next = event.target.value as Limits; setLimits(next); setCanDownload(next === 'none' ? true : PRESETS[next].access.can_download); }} value={limits}>
              {LIMIT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </Select>
          )}
        </Field>
        <fieldset className="g-fieldset">
          <legend>Permissions</legend>
          <Checkbox checked={canDownload} label="Save media to the vault" onChange={(event) => setCanDownload(event.currentTarget.checked)} />
          <Checkbox checked={canRequest} label="Request movies and shows" onChange={(event) => setCanRequest(event.currentTarget.checked)} />
        </fieldset>
        {address && !address.public_address ? (
          <p className="g-setting-note">No public address is set, so the link uses the address you are on now. <TextButton onClick={goToAddress}>Set the public address</TextButton></p>
        ) : null}
        {problem?.field === 'form' ? <p className="auth-error" role="alert">{problem.text}</p> : null}
      </form>
    </Dialog>
  );
}
