import { useEffect, useState } from 'react';

import { getRecordingRetention, updateRecordingRetention } from '../../api';
import { Field, fieldProps, Input } from '../../ui';
import { type FormStatus, SectionForm } from '../settings/SectionForm';

/** Admin retention policy for finished live recordings. 0 turns a bound off. */
export function RecordingRetentionForm() {
  const [days, setDays] = useState<string | null>(null);
  const [gb, setGb] = useState('0');
  const [saved, setSaved] = useState<{ days: string; gb: string } | null>(null);
  const [status, setStatus] = useState<FormStatus>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getRecordingRetention()
      .then((policy) => { const next = { days: String(policy.keep_days), gb: String(policy.max_gb) }; setDays(next.days); setGb(next.gb); setSaved(next); })
      .catch(() => setStatus({ tone: 'error', text: 'Unable to load the recording cleanup policy.' }));
  }, []);
  const dirty = Boolean(saved && (days !== saved.days || gb !== saved.gb));

  async function save() {
    if (days === null) return;
    setSaving(true);
    setStatus(null);
    try {
      const next = await updateRecordingRetention({ keep_days: Number(days), max_gb: Number(gb) });
      const normalized = { days: String(next.keep_days), gb: String(next.max_gb) };
      setDays(normalized.days);
      setGb(normalized.gb);
      setSaved(normalized);
      setStatus({ tone: 'ok', text: 'Saved.' });
    } catch {
      setStatus({ tone: 'error', text: 'Unable to save the recording cleanup policy.' });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div aria-busy={days === null}>
      <SectionForm dirty={dirty} label="Live recording cleanup" onDiscard={() => { if (saved) { setDays(saved.days); setGb(saved.gb); } setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save cleanup policy" saving={saving} status={status}>
        <div className="g-subrows g-compact-fields">
          <Field label="Keep recordings for (days)">
            {(ids) => <Input {...fieldProps(ids)} disabled={days === null} inputMode="numeric" max={3650} min={0} onChange={(event) => { setDays(event.target.value); setStatus(null); }} required type="number" value={days ?? ''} />}
          </Field>
          <Field label="Total size for recordings (GB)">
            {(ids) => <Input {...fieldProps(ids)} disabled={days === null} inputMode="numeric" max={100000} min={0} onChange={(event) => { setGb(event.target.value); setStatus(null); }} required type="number" value={gb} />}
          </Field>
        </div>
      </SectionForm>
    </div>
  );
}
