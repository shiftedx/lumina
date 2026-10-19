import { createContext, type ReactNode, type Ref, useContext, useId } from 'react';

import { PlaybackPreview } from './PlaybackPreview';
import { InfoButton } from './InfoButton';
import type { SettingsPage } from './navigation';
import type { SettingEntry, SettingsSectionDef } from './settingsTypes';
import './settings.css';

/** DOM id for a registry id: `playback.loudness` → `setting-playback-loudness`. */
export const settingDomId = (id: string) => `setting-${id.replace(/[^A-Za-z0-9-]/g, '-')}`;

export type SettingRowProps = { id: string; label: string; info: string; description?: string; advanced?: boolean; layout?: SettingEntry['layout']; detail?: ReactNode; children?: ReactNode };

/** The row's heading and description ids, so a lone SettingSwitch is named by the row label instead of repeating it. */
const RowContext = createContext<{ labelId: string; descId?: string } | null>(null);

/** Label, description, ⓘ, control, and an optional live detail slot. A D-pad row: Left/Right move between its controls. */
export function SettingRow({ id, label, info, description, advanced = false, layout = 'inline', detail, children }: SettingRowProps) {
  const dom = settingDomId(id);
  const descId = description ? `${dom}-desc` : undefined;
  return (
    <div className={`setting-row g-setting-row ${layout === 'block' ? 'is-block' : layout === 'switch' ? 'is-switch' : 'is-inline'}`} data-focus-row data-setting-id={id} id={dom}>
      <div className="g-setting-head">
        <h3 className="g-setting-label" id={`${dom}-label`}>{label}</h3>
        <InfoButton id={`${dom}-info`} label={label} text={info} />
        {advanced ? <span className="g-setting-tag">Advanced</span> : null}
        {description ? <p className="g-setting-desc" id={descId}>{description}</p> : null}
      </div>
      <RowContext.Provider value={{ labelId: `${dom}-label`, descId }}>
        <div className="g-setting-control">{children}</div>
      </RowContext.Provider>
      {detail != null ? <div aria-live="polite" className="g-setting-detail">{detail}</div> : null}
    </div>
  );
}

export type SettingSwitchProps = { label?: ReactNode; hint?: ReactNode; checked: boolean; onChange: (next: boolean) => void; busy?: boolean; disabled?: boolean; ref?: Ref<HTMLInputElement> };

/**
 * Settings' on/off uses the shared Switch styles and a short text state beside standalone toggles.
 * With no `label` it is named by its row's heading (aria-labelledby), so the row never shows the same words twice.
 */
export function SettingSwitch({ label, hint, checked, onChange, busy = false, disabled, ref }: SettingSwitchProps) {
  const row = useContext(RowContext);
  const base = useId();
  const described = [hint ? `${base}hint` : null, label ? null : row?.descId].filter(Boolean).join(' ') || undefined;
  return (
    <label className="g-switch">
      {label ? <span className="g-switch-copy"><span className="g-switch-label" id={`${base}label`}>{label}</span>{hint ? <span className="g-switch-hint" id={`${base}hint`}>{hint}</span> : null}</span> : null}
      {!label ? <span aria-hidden="true" className="g-switch-state">{checked ? 'On' : 'Off'}</span> : null}
      <input aria-busy={busy || undefined} aria-describedby={described} aria-labelledby={label ? `${base}label` : row?.labelId} checked={checked} disabled={disabled} onChange={(event) => { if (!busy) onChange(event.target.checked); }} ref={ref} role="switch" type="checkbox" />
      <span aria-hidden="true" className="g-switch-track" />
    </label>
  );
}

/** A section's rows (all, or a search's subset) inside the section Provider when it has one. */
export function SectionRows({ section, entries = section.entries, pages }: { pages?: readonly SettingsPage[]; section: SettingsSectionDef; entries?: readonly SettingEntry[] }) {
  const renderRows = (list: readonly SettingEntry[]) => list.map(({ id, label, info, description, advanced, layout, Control, Detail }) => (
    <SettingRow advanced={!pages && advanced} description={description ?? (pages && layout !== 'block' ? info.split(/(?<=[.!?])\s/)[0] : undefined)} detail={Detail ? <Detail /> : undefined} id={id} info={info} key={id} label={label} layout={layout}><Control /></SettingRow>
  ));
  const rows = pages ? pages.map((page) => <section aria-label={page.label} className="g-settings-group" id={`settings-group-${page.id}`} key={page.id} tabIndex={-1}>
    <h3 className={pages.length === 1 || page.rows.length === 1 ? 'sr-only' : 'g-settings-group-title'}>{page.label}</h3>
    {page.id === 'watching' ? <PlaybackPreview /> : null}
    <div className="g-settings-rows">{renderRows(entries.filter((entry) => page.rows.includes(entry.id)))}</div>
  </section>) : renderRows(entries);
  return section.Provider ? <section.Provider>{rows}</section.Provider> : <>{rows}</>;
}
