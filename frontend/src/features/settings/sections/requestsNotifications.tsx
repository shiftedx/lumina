import { useCallback, useState } from 'react';

import { Field, fieldProps, Input } from '../../../ui';
import { errorMessage } from '../../../utils';
import { useAdminResource } from '../../admin/useAdminResource';
import { getNotificationPrefs, putNotificationPrefs } from '../requestsSettingsApi';
import { type FormStatus, SectionForm } from '../SectionForm';
import { SettingSwitch } from '../SettingRow';

/** A member's request emails: GET/PUT /api/requests/notifications. */
export function RequestNotifications() {
  const load = useCallback(getNotificationPrefs, []);
  const prefs = useAdminResource(load, 'Unable to load your request notifications.');
  const [draft, setDraft] = useState<{ email: string; enabled: boolean } | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const data = prefs.data;
  if (!data) return prefs.error ? <p className="auth-error" role="alert">{prefs.error}</p> : <p className="admin-note" role="status">Loading…</p>;
  const saved = { email: data.email ?? '', enabled: data.enabled };
  const current = draft ?? saved;
  const dirty = current.email.trim() !== saved.email || current.enabled !== saved.enabled;
  const edit = (patch: Partial<typeof current>) => { setStatus(null); setDraft({ ...current, ...patch }); };
  const missing = current.enabled && !current.email.trim();

  async function save() {
    if (missing) { setStatus({ tone: 'error', text: 'Add an email address to get request emails.' }); return; }
    setSaving(true);
    setStatus(null);
    try {
      prefs.setData(await putNotificationPrefs({ email: current.email.trim() || null, enabled: current.enabled }));
      setDraft(null);
      setStatus({ tone: 'ok', text: 'Request notifications saved.' });
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, 'Unable to save request notifications.') });
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="g-req">
      <SectionForm dirty={dirty} label="Request notifications" onDiscard={() => { setDraft(null); setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save request notifications" saving={saving} status={status}>
        <div className="g-subrows">
          <SettingSwitch checked={current.enabled} label="Email me about my requests" onChange={(enabled) => edit({ enabled })} />
          <Field label="Email address">{(ids) => <Input {...fieldProps(ids)} autoComplete="email" inputMode="email" onChange={(event) => edit({ email: event.currentTarget.value })} type="email" value={current.email} />}</Field>
        </div>
      </SectionForm>
    </div>
  );
}
