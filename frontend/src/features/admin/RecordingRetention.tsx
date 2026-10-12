import { type FormEvent, useEffect, useState } from 'react';

import { getRecordingRetention, updateRecordingRetention } from '../../api';
import { Button, Field, fieldProps, Input, StatusText } from '../../ui';

/** Admin retention policy for finished live recordings. 0 turns a bound off. */
export function RecordingRetentionForm() {
  const [days, setDays] = useState<string | null>(null);
  const [gb, setGb] = useState('0');
  const [status, setStatus] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getRecordingRetention()
      .then((policy) => { setDays(String(policy.keep_days)); setGb(String(policy.max_gb)); })
      .catch(() => setStatus({ tone: 'error', text: 'Unable to load the recording cleanup policy.' }));
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setStatus(null);
    try {
      await updateRecordingRetention({ keep_days: Number(days), max_gb: Number(gb) });
      setStatus({ tone: 'ok', text: 'Saved.' });
    } catch {
      setStatus({ tone: 'error', text: 'Unable to save the recording cleanup policy.' });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form aria-busy={days === null} aria-label="Live recording cleanup" className="g-subrows" onSubmit={(event) => { void save(event); }}>
      <Field label="Keep recordings for (days)">
        {(ids) => <Input {...fieldProps(ids)} disabled={days === null} inputMode="numeric" max={3650} min={0} onChange={(event) => setDays(event.target.value)} required type="number" value={days ?? ''} />}
      </Field>
      <Field label="Total size for recordings (GB)">
        {(ids) => <Input {...fieldProps(ids)} disabled={days === null} inputMode="numeric" max={100000} min={0} onChange={(event) => setGb(event.target.value)} required type="number" value={gb} />}
      </Field>
      <div className="g-actions">
        {status ? <div role={status.tone === 'error' ? 'alert' : 'status'}><StatusText tone={status.tone === 'error' ? 'danger' : 'ok'}>{status.text}</StatusText></div> : null}
        <Button busy={saving} disabled={days === null} type="submit" variant="primary">{saving ? 'Saving…' : 'Save cleanup policy'}</Button>
      </div>
    </form>
  );
}
