import { RotateCcw } from 'lucide-react';
import { useState } from 'react';

import { Button, Checkbox, Dialog, Input, StatusText, VisuallyHidden } from '../../../ui';
import { errorMessage } from '../../../utils';
import { putRequestPolicies, type Policy, type RequestKind, resetMemberPolicies } from '../requestsSettingsApi';
import { type FormStatus, SectionForm } from '../SectionForm';
import { useRequestsForm } from './requestsContext';

const KINDS: { kind: RequestKind; label: string }[] = [{ kind: 'movie', label: 'Movies' }, { kind: 'show', label: 'Shows' }, { kind: 'anime', label: 'Anime' }];
const FALLBACK: Policy = { kind: 'movie', can_request: true, auto_approve: false, quota_count: 10, quota_days: 7 };

/** One row per kind, always in Movies / Shows / Anime order; an override missing a kind falls back to the household default. */
export const completePolicies = (rows: Policy[], fallback: Policy[] = []): Policy[] =>
  KINDS.map(({ kind }) => rows.find((row) => row.kind === kind) ?? fallback.find((row) => row.kind === kind) ?? { ...FALLBACK, kind });

/** "Unlimited" is both quota fields null. */
export function PolicyTable({ policies, onChange, caption }: { policies: Policy[]; onChange: (next: Policy[]) => void; caption: string }) {
  const set = (kind: RequestKind, patch: Partial<Policy>) => onChange(policies.map((row) => (row.kind === kind ? { ...row, ...patch } : row)));
  return (
    <div className="g-req-scroll">
      <table className="g-table">
        <caption className="sr-only">{caption}</caption>
        <thead><tr><th scope="col">Kind</th><th scope="col">May request</th><th scope="col">Auto-approve</th><th scope="col">Limit</th></tr></thead>
        <tbody>
          {KINDS.map(({ kind, label }) => {
            const row = policies.find((entry) => entry.kind === kind) ?? { ...FALLBACK, kind };
            const unlimited = row.quota_count === null;
            return (
              <tr key={kind}>
                <th scope="row">{label}</th>
                <td><Checkbox checked={row.can_request} label={<VisuallyHidden>{label}: May request</VisuallyHidden>} onChange={(event) => set(kind, { can_request: event.currentTarget.checked })} /></td>
                <td><Checkbox checked={row.auto_approve} label={<VisuallyHidden>{label}: Auto-approve</VisuallyHidden>} onChange={(event) => set(kind, { auto_approve: event.currentTarget.checked })} /></td>
                <td>
                  <div className="g-req-quota">
                    <Checkbox checked={unlimited} label="Unlimited" onChange={(event) => set(kind, event.currentTarget.checked ? { quota_count: null, quota_days: null } : { quota_count: 10, quota_days: 7 })} />
                    {unlimited ? null : (
                      <>
                        <Input aria-label={`${label}: requests per period`} inputMode="numeric" min={1} onChange={(event) => set(kind, { quota_count: Math.max(1, Number(event.currentTarget.value) || 1) })} type="number" value={row.quota_count ?? ''} />
                        <span>per</span>
                        <Input aria-label={`${label}: period in days`} inputMode="numeric" min={1} onChange={(event) => set(kind, { quota_days: Math.max(1, Number(event.currentTarget.value) || 1) })} type="number" value={row.quota_days ?? ''} />
                        <span>days</span>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function HouseholdPolicies() {
  const { policies } = useRequestsForm();
  const [draft, setDraft] = useState<Policy[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const data = policies.data;
  if (!data) return policies.error ? <p className="auth-error" role="alert">{policies.error}</p> : <p className="admin-note" role="status">Loading…</p>;
  const saved = completePolicies(data.defaults);
  const current = draft ?? saved;
  const dirty = JSON.stringify(current) !== JSON.stringify(saved);
  async function save() {
    setSaving(true);
    setStatus(null);
    try {
      await putRequestPolicies(null, current);
      policies.reload();
      setDraft(null);
      setStatus({ tone: 'ok', text: 'Household defaults saved.' });
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, 'Unable to save the household defaults.') });
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="g-req">
      <SectionForm dirty={dirty} label="Household request policies" onDiscard={() => { setDraft(null); setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save household defaults" saving={saving} status={status}>
        <PolicyTable caption="Household defaults" onChange={(next) => { setStatus(null); setDraft(next); }} policies={current} />
        <p className="g-req-line">Vault owners can always request and are approved automatically.</p>
      </SectionForm>
    </div>
  );
}

export function MemberPolicies() {
  const { policies } = useRequestsForm();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Policy[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const data = policies.data;
  if (!data) return policies.error ? <p className="auth-error" role="alert">{policies.error}</p> : <p className="admin-note" role="status">Loading…</p>;
  const member = data.members.find((entry) => entry.user.id === editing);

  async function run(action: () => Promise<unknown>, done: string, failure: string) {
    setBusy(true);
    setStatus(null);
    try {
      await action();
      policies.reload();
      setEditing(null);
      setStatus({ tone: 'ok', text: done });
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, failure) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="g-req">
      {data.members.length === 0 ? <p className="admin-note">No members yet.</p> : (
        <ul className="g-req-members">
          {data.members.map(({ user, overrides }) => (
            <li className="g-req-member" key={user.id}>
              <span><strong>{user.name}</strong> {overrides.length ? <span className="g-setting-tag">Custom</span> : <small>Household defaults</small>}</span>
              <span className="g-actions">
                <Button disabled={user.role === 'admin'} onClick={() => { setDraft(completePolicies(overrides, data.defaults)); setEditing(user.id); }} variant="secondary">{`Edit ${user.name}`}</Button>
                {overrides.length ? <Button disabled={busy} icon={<RotateCcw />} onClick={() => { void run(() => resetMemberPolicies(user.id), `${user.name} now follows the household defaults.`, `Unable to reset ${user.name}.`); }} variant="quiet">{`Reset ${user.name} to defaults`}</Button> : null}
              </span>
            </li>
          ))}
        </ul>
      )}
      {status ? <p role={status.tone === 'error' ? 'alert' : 'status'}><StatusText tone={status.tone === 'error' ? 'danger' : 'ok'}>{status.text}</StatusText></p> : null}
      <Dialog
        busy={busy}
        footer={<><Button disabled={busy} onClick={() => setEditing(null)} variant="quiet">Cancel</Button><Button busy={busy} onClick={() => { if (member) void run(() => putRequestPolicies(member.user.id, draft), `${member.user.name}'s policies saved.`, `Unable to save ${member.user.name}'s policies.`); }} variant="primary">Save policies</Button></>}
        onClose={() => setEditing(null)}
        open={Boolean(member)}
        size="lg"
        title={`Request policies for ${member?.user.name ?? ''}`}
      >
        <PolicyTable caption={`Policies for ${member?.user.name ?? ''}`} onChange={setDraft} policies={draft} />
      </Dialog>
    </div>
  );
}
