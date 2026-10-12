import { useState } from 'react';

import { getMediaServerSettings, updateMediaServerSettings } from '../../../api';
import { SettingSwitch } from '../SettingRow';
import { useAdminResource } from '../../admin/useAdminResource';

/** One household flag over /api/admin/media-server; it saves at once, with no Save form. */
export function EditingSwitch() {
  const resource = useAdminResource(getMediaServerSettings, 'Unable to load this setting.');
  const [saveError, setSaveError] = useState(false);
  const [saving, setSaving] = useState(false);
  const { data, setData } = resource;

  if (resource.error) return <p className="auth-error" role="alert">{resource.error}</p>;

  const change = async (next: boolean) => {
    if (!data) return;
    setSaveError(false);
    setSaving(true);
    setData({ ...data, members_edit_metadata: next });
    try {
      setData(await updateMediaServerSettings({ members_edit_metadata: next }));
    } catch {
      setData(data);
      setSaveError(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <SettingSwitch busy={!data || saving} checked={data?.members_edit_metadata ?? false} onChange={(next) => void change(next)} />
      {saveError ? <p className="auth-error" role="alert">Lumina could not save this setting. Try again.</p> : null}
    </>
  );
}
