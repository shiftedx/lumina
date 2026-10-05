import { createContext, type ReactNode, useContext, useEffect, useState } from 'react';

import { getAdminSettings, updateAdminSettings } from '../../../api';
import type { AdminSettings } from '../../../types';
import { errorMessage } from '../../../utils';
import { AdminOverview } from '../../admin/AdminOverview';
import { useAdminResource } from '../../admin/useAdminResource';
import { type FormStatus, SectionForm } from '../SectionForm';
import type { SettingsSectionDef } from '../settingsTypes';

type Limits = Record<'concurrency' | 'max_active_jobs_per_user' | 'min_free_disk_mb' | 'extra_source_ports', string>;
type LimitsForm = { values: Limits | null; set: (key: keyof Limits, value: string) => void; savedAt: number };
const LimitsContext = createContext<LimitsForm | null>(null);
const toLimits = (settings: AdminSettings): Limits => ({ concurrency: String(settings.concurrency), max_active_jobs_per_user: String(settings.max_active_jobs_per_user), min_free_disk_mb: String(settings.min_free_disk_mb), extra_source_ports: (settings.extra_source_ports ?? []).join(', ') });
/** "8000, 8080" → [8000, 8080]; null when a part is not a whole number (the server checks range and refused ports). */
const parsePorts = (text: string): number[] | null => {
  const parts = text.split(/[\s,]+/).filter(Boolean);
  return parts.every((part) => /^\d{1,5}$/.test(part)) ? parts.map(Number) : null;
};

function useLimits(): LimitsForm {
  const form = useContext(LimitsContext);
  if (!form) throw new Error('Overview rows render inside the Download limits form.');
  return form;
}

/** Overview's Save form: the three download limits. The status row refreshes after a save. */
function DownloadLimitsForm({ children }: { children: ReactNode }) {
  const settings = useAdminResource(getAdminSettings, 'Unable to load download limits.');
  const [values, setValues] = useState<Limits | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const [savedAt, setSavedAt] = useState(0);
  useEffect(() => { if (settings.data) setValues(toLimits(settings.data)); }, [settings.data]);
  const saved = settings.data ? toLimits(settings.data) : null;
  const dirty = Boolean(values && saved && (Object.keys(values) as Array<keyof Limits>).some((key) => values[key] !== saved[key]));

  async function save() {
    if (!values) return;
    const payload: Parameters<typeof updateAdminSettings>[0] = { concurrency: Number(values.concurrency), max_active_jobs_per_user: Number(values.max_active_jobs_per_user), min_free_disk_mb: Number(values.min_free_disk_mb) };
    if (saved && values.extra_source_ports !== saved.extra_source_ports) {
      const ports = parsePorts(values.extra_source_ports);
      if (!ports) { setStatus({ tone: 'error', text: 'Enter port numbers separated by commas, such as 8000, 8080.' }); return; }
      payload.extra_source_ports = ports;
    }
    setSaving(true);
    setStatus(null);
    try {
      const next = await updateAdminSettings(payload);
      settings.setData((current) => current && { ...current, ...payload, ...(next?.extra_source_ports ? { extra_source_ports: next.extra_source_ports } : {}) });
      setStatus({ tone: 'ok', text: 'Saved. Per-member and free-space limits apply to the next download; simultaneous downloads change after a server restart.' });
      setSavedAt(Date.now());
    } catch (error) {
      // Entered values stay in place so the owner can correct them.
      setStatus({ tone: 'error', text: errorMessage(error, 'Unable to save download limits.') });
    } finally {
      setSaving(false);
    }
  }
  const set = (key: keyof Limits, value: string) => { setStatus(null); setValues((current) => current && { ...current, [key]: value }); };

  return (
    <LimitsContext.Provider value={{ values, set, savedAt }}>
      <SectionForm advanced dirty={dirty} label="Overview" onDiscard={() => { setStatus(null); if (settings.data) setValues(toLimits(settings.data)); }} onSave={() => { void save(); }} saveLabel="Save download limits" saving={saving} status={status ?? (settings.error ? { tone: 'error', text: settings.error } : null)}>{children}</SectionForm>
    </LimitsContext.Provider>
  );
}

function Status() {
  const { savedAt } = useLimits();
  return <AdminOverview refreshKey={savedAt} />;
}

function LimitField({ name, label, min, max }: { name: keyof Limits; label: string; min: number; max: number }) {
  const { values, set } = useLimits();
  if (!values) return <p className="admin-note">Loading…</p>;
  return <label className="g-field"><span className="sr-only">{label}</span><input className="g-input" inputMode="numeric" max={max} min={min} onChange={(event) => set(name, event.target.value)} required type="number" value={values[name]} /></label>;
}
const Concurrency = () => <LimitField label="Simultaneous downloads" max={8} min={1} name="concurrency" />;
const PerMember = () => <LimitField label="Queued or running per member" max={500} min={1} name="max_active_jobs_per_user" />;
const MinFree = () => <LimitField label="Keep free on the library volume (MB)" max={1_048_576} min={0} name="min_free_disk_mb" />;
function ExtraPorts() {
  const { values, set } = useLimits();
  if (!values) return <p className="admin-note">Loading…</p>;
  return <label className="g-field"><span className="sr-only">Extra allowed source ports</span><input autoComplete="off" className="g-input" inputMode="numeric" onChange={(event) => set('extra_source_ports', event.target.value)} placeholder="8000, 8080" type="text" value={values.extra_source_ports} /></label>;
}

/** Overview (the Server landing): vault status, then the download limits with an explicit Save. */
export const OVERVIEW_SECTION: SettingsSectionDef = {
  id: 'overview', group: 'server', label: 'Dashboard', aliases: ['overview'], summary: 'Library health, storage and download activity across the whole vault.',
  Provider: DownloadLimitsForm,
  entries: [
    { id: 'overview.status', label: 'Vault overview', layout: 'block', keywords: ['health', 'library items', 'missing', 'storage roots', 'free space', 'active downloads', 'failures', 'database writes', 'refresh', 'event streams'], info: 'A snapshot of the whole vault: Library size, free space, download activity, recent failures and database health. It is sampled when you open it or press Refresh. Covers everyone on this server.', Control: Status },
    { id: 'overview.concurrency', advanced: true, label: 'Downloads at once', keywords: ['simultaneous downloads', 'download limit', 'parallel', 'concurrency'], info: 'How many downloads run at the same time for the whole server, from 1 to 8. More is faster but uses more bandwidth and disk, and the change applies after Lumina restarts. Affects everyone on this server.', Control: Concurrency },
    { id: 'overview.per-member', advanced: true, label: 'Downloads per member', keywords: ['queued or running per member', 'queue limit', 'per person'], info: 'The most downloads one member can have queued or running at once, from 1 to 500. It stops one person filling the queue for everyone and applies to the next download. Affects everyone on this server.', Control: PerMember },
    { id: 'overview.min-free', advanced: true, label: 'Free space to keep', keywords: ['keep free on the library volume', 'disk space', 'reserve', 'pause downloads', 'mb'], info: 'New downloads pause when the library volume has less free space than this, in MB. 0 turns the reserve off. Affects everyone on this server.', Control: MinFree },
    { id: 'overview.source-ports', advanced: true, label: 'Extra allowed source ports', keywords: ['port', 'ports', 'network', 'icecast', '8000', '8080', 'firewall', 'source address', 'blocked address'], info: 'Downloads and streams only reach public web addresses on ports 80 and 443; list other ports here, such as 8000 for an Icecast radio, separated by commas. Private and home-network addresses stay blocked on every port, and ports for SSH, mail and databases are always refused. Affects every member who adds a source.', Control: ExtraPorts },
  ],
};
