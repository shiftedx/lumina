import { type ReactNode, useContext } from 'react';

import { Button, StatusText } from '../../ui';
import { SettingsHostContext } from './settingsHost';
import { useUnsavedChanges } from './unsavedChanges';

export type FormStatus = { tone: 'ok' | 'error'; text: string } | null;
export type SectionFormProps = { label: string; dirty: boolean; saving: boolean; status: FormStatus; saveLabel: string; onSave: () => void; onDiscard: () => void; children: ReactNode;
  /** Every field this form saves sits in an advanced row: with those rows hidden and nothing unsaved, the Save bar goes too. */
  advanced?: boolean };

/**
 * A section's explicit Save: the rows, then one Save bar in the flow at the end; unsaved edits are guarded.
 * The bar sticks to the bottom of the screen only while there is something to save. `label` is the section's label.
 */
export function SectionForm({ label, dirty, saving, status, saveLabel, onSave, onDiscard, children, advanced = false }: SectionFormProps) {
  useUnsavedChanges(dirty, label);
  const showAdvanced = useContext(SettingsHostContext)?.showAdvanced;
  const hidden = advanced && showAdvanced === false;
  const error = status?.tone === 'error';
  return (
    <form aria-label={label} className="g-setting-form" onSubmit={(event) => { event.preventDefault(); onSave(); }}>
      {children}
      {hidden && !dirty && !status ? null : <div className={`g-save-bar${dirty || saving ? ' is-pending' : ''}`}>
        <p aria-live="polite" className="g-save-status" role={error ? 'alert' : 'status'}>
          {saving ? 'Saving…' : status ? <StatusText tone={error ? 'danger' : 'ok'}>{status.text}</StatusText> : dirty ? <span className="g-label">Unsaved changes</span> : ''}
        </p>
        <div className="g-save-actions">
          {dirty && !saving ? <Button onClick={onDiscard} variant="quiet">Discard</Button> : null}
          <Button busy={saving} disabled={!dirty && !saving} type="submit" variant="primary">{saveLabel}</Button>
        </div>
      </div>}
    </form>
  );
}
