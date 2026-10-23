import { useEffect, useMemo, useState } from 'react';

import { cancelMyResourceJob, getHouseholdResources, getMemberResources, getMyResourceJobs, getMyResources, updateHouseholdResources, updateMemberResources } from '../../api';
import type { HouseholdResources, MemberResources, ResourceAllowances, ResourceOverrides } from '../../types';
import { Button, Field, fieldProps, Input, Textarea } from '../../ui';
import { errorMessage } from '../../utils';
import { type FormStatus, SectionForm } from './SectionForm';

const allowanceFields: ReadonlyArray<{ key: keyof ResourceAllowances; label: string; hint: string; rate?: true }> = [
  { key: 'remote_stream_bps', label: 'Remote stream ceiling', hint: 'Mbps per remote stream.', rate: true },
  { key: 'remote_member_bps', label: 'Member remote bandwidth', hint: 'Mbps across this member’s remote devices.', rate: true },
  { key: 'playback_sessions', label: 'Playback sessions', hint: 'Simultaneous sessions.' },
  { key: 'video_encodes', label: 'Video encodes', hint: 'Simultaneous conversions.' },
  { key: 'download_running', label: 'Running downloads', hint: 'Active acquisitions.' },
  { key: 'download_queued', label: 'Queued downloads', hint: 'Waiting acquisitions.' },
  { key: 'ai_running', label: 'Running AI jobs', hint: 'Active summaries, transcription and related work.' },
  { key: 'ai_queued', label: 'Queued AI jobs', hint: 'Waiting AI work.' },
];

const mbps = (bps: number) => String(bps / 1_000_000);
const parseNumber = (value: string, rate = false) => Math.max(0, Math.round(Number(value || 0) * (rate ? 1_000_000 : 1)));
const parseNetworks = (value: string) => value.split(/[\n,]/).map((network) => network.trim()).filter(Boolean);
const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);

function AllowanceFields({ draft, onChange, inherited = false, effective }: { draft: ResourceAllowances | ResourceOverrides; onChange: (next: ResourceAllowances | ResourceOverrides) => void; inherited?: boolean; effective?: ResourceAllowances }) {
  return <div className="g-subrows g-compact-fields">{allowanceFields.map(({ key, label, hint, rate }) => {
    const value = draft[key];
    const shown = value === undefined || value === null ? '' : rate ? mbps(value) : String(value);
    const suffix = inherited && value == null ? `Inherits ${rate ? `${mbps(effective?.[key] ?? 0)} Mbps` : effective?.[key] ?? 0}` : hint;
    const effectiveUnlimited = inherited && value == null && effective?.[key] === 0;
    return <Field hint={`${suffix}${value === 0 || effectiveUnlimited ? ' Zero is unlimited.' : ''}`} key={key} label={label}>{(ids) => <Input {...fieldProps(ids)} inputMode="decimal" min="0" onChange={(event) => onChange({ ...draft, [key]: event.currentTarget.value === '' && inherited ? undefined : parseNumber(event.currentTarget.value, rate) })} placeholder={inherited ? 'Inherit' : undefined} step={rate ? '0.001' : '1'} type="number" value={shown} />}</Field>;
  })}</div>;
}

export function HouseholdResourcesSettings() {
  const [saved, setSaved] = useState<HouseholdResources | null>(null);
  const [draft, setDraft] = useState<HouseholdResources | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const [reload, setReload] = useState(0);
  const [lanNetworks, setLanNetworks] = useState('');
  useEffect(() => { let live = true; getHouseholdResources().then((value) => { if (live) { setSaved(value); setDraft(value); setLanNetworks(value.lan_networks.join('\n')); } }, (failure) => { if (live) setStatus({ tone: 'error', text: errorMessage(failure, 'Unable to load household resources.') }); }); return () => { live = false; }; }, [reload]);
  if (!draft || !saved) return status?.tone === 'error'
    ? <div className="g-subrows"><p className="auth-error" role="alert">{status.text}</p><Button onClick={() => { setStatus(null); setReload((value) => value + 1); }} variant="secondary">Retry</Button></div>
    : <p className="g-setting-note" role="status">Loading household resources…</p>;
  const resourceDraft = draft;
  const dirty = !same(resourceDraft, saved);
  async function save() { setSaving(true); setStatus(null); try { const next = await updateHouseholdResources(resourceDraft); setSaved(next); setDraft(next); setLanNetworks(next.lan_networks.join('\n')); setStatus({ tone: 'ok', text: 'Household resources saved.' }); } catch (failure) { setStatus({ tone: 'error', text: errorMessage(failure, 'Unable to save household resources.') }); } finally { setSaving(false); } }
  return <SectionForm dirty={dirty} label="Household resources" onDiscard={() => { setDraft(saved); setLanNetworks(saved.lan_networks.join('\n')); setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save resources" saving={saving} status={status}>
    <div className="g-subrows">
      <Field hint="Enter Mbps. Leave blank until you know the upload capacity. Remote delivery is not protected until this is enabled." label="Remote upload budget">{(ids) => <Input {...fieldProps(ids)} inputMode="decimal" min="0" onChange={(event) => setDraft({ ...resourceDraft, upload_bps: event.currentTarget.value === '' ? null : parseNumber(event.currentTarget.value, true) })} placeholder="Unconfigured" step="0.001" type="number" value={resourceDraft.upload_bps == null ? '' : mbps(resourceDraft.upload_bps)} />}</Field>
      <Field hint="One CIDR per line. Only verified traffic in these networks is exempt from remote delivery limits." label="LAN networks">{(ids) => <Textarea {...fieldProps(ids)} onChange={(event) => { const value = event.currentTarget.value; setLanNetworks(value); setDraft({ ...resourceDraft, lan_networks: parseNetworks(value) }); }} placeholder="192.168.1.0/24" rows={3} value={lanNetworks} />}</Field>
    </div>
    {resourceDraft.upload_bps == null ? <p className="g-setting-note">Unconfigured — Lumina keeps existing playback compatibility. Enter a budget to protect aggregate remote delivery; stream and member caps remain unlimited until you set them.</p> : null}
    <AllowanceFields draft={resourceDraft.defaults} onChange={(defaults) => setDraft({ ...resourceDraft, defaults: defaults as ResourceAllowances })} />
  </SectionForm>;
}

export function MemberResourcesSettings({ userId }: { userId: string }) {
  const [saved, setSaved] = useState<MemberResources | null>(null);
  const [draft, setDraft] = useState<ResourceOverrides>({});
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => { let live = true; getMemberResources(userId).then((value) => { if (live) { setSaved(value); setDraft(value.overrides); } }, (failure) => { if (live) setStatus({ tone: 'error', text: errorMessage(failure, 'Unable to load member resources.') }); }); return () => { live = false; }; }, [userId, reload]);
  if (!saved) return status?.tone === 'error'
    ? <div className="g-subrows"><p className="auth-error" role="alert">{status.text}</p><Button onClick={() => { setStatus(null); setReload((value) => value + 1); }} variant="secondary">Retry</Button></div>
    : <p className="g-setting-note" role="status">Loading resource allowances…</p>;
  const dirty = !same(draft, saved.overrides);
  async function save() { setSaving(true); setStatus(null); try { const next = await updateMemberResources(userId, draft); setSaved(next); setDraft(next.overrides); setStatus({ tone: 'ok', text: 'Member resources saved.' }); } catch (failure) { setStatus({ tone: 'error', text: errorMessage(failure, 'Unable to save member resources.') }); } finally { setSaving(false); } }
  return <SectionForm dirty={dirty} label="Member resources" onDiscard={() => { setDraft(saved.overrides); setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save resources" saving={saving} status={status}>
    <p className="g-setting-note">Leave a field blank to inherit the household default. Use zero for an unlimited member allowance; household caps still apply.</p>
    <AllowanceFields draft={draft} effective={saved.effective} inherited onChange={(next) => setDraft(next as ResourceOverrides)} />
  </SectionForm>;
}

export function PersonalResources() {
  const [resources, setResources] = useState<MemberResources | null>(null);
  const [jobs, setJobs] = useState<Awaited<ReturnType<typeof getMyResourceJobs>>>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { let live = true; Promise.all([getMyResources(), getMyResourceJobs()]).then(([policy, active]) => { if (live) { setResources(policy); setJobs(active); } }, (failure) => { if (live) setError(errorMessage(failure, 'Unable to load your resource allowances.')); }); return () => { live = false; }; }, []);
  const description = useMemo(() => resources ? `${resources.effective.remote_member_bps ? `${mbps(resources.effective.remote_member_bps)} Mbps remote allowance` : 'Unlimited remote allowance'} · ${resources.effective.ai_running || 'unlimited'} AI jobs at once` : '', [resources]);
  async function cancel(kind: string, id: string) { setBusy(id); try { await cancelMyResourceJob(kind, id); setJobs((current) => current.filter((job) => job.id !== id)); } catch (failure) { setError(errorMessage(failure, 'Unable to cancel this job.')); } finally { setBusy(null); } }
  return <div className="g-subrows" aria-busy={!resources && !error}>
    {error ? <p className="auth-error" role="alert">{error}</p> : null}
    {resources ? <p className="g-setting-note">{description}. Household caps apply even when an allowance is unlimited.</p> : <p className="g-setting-note" role="status">Loading your allowances…</p>}
    {jobs.map((job) => <div className="g-subrow" key={job.id}><span className="g-list-row-copy"><strong>{job.kind}</strong><small>{job.state}</small></span><Button busy={busy === job.id} onClick={() => { void cancel(job.kind, job.id); }} variant="secondary">Cancel</Button></div>)}
    {resources && !jobs.length ? <p className="g-setting-note">No active AI jobs.</p> : null}
  </div>;
}
