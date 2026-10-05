import { createContext, type ReactNode, type Ref, useContext, useId } from 'react';

import { InfoButton } from './InfoButton';
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
 * Settings' on/off: the ui Switch's look (same classes) without its visible On echo, since the track already shows the state.
 * With no `label` it is named by its row's heading (aria-labelledby), so the row never shows the same words twice.
 */
export function SettingSwitch({ label, hint, checked, onChange, busy = false, disabled, ref }: SettingSwitchProps) {
  const row = useContext(RowContext);
  const base = useId();
  const described = [hint ? `${base}hint` : null, label ? null : row?.descId].filter(Boolean).join(' ') || undefined;
  return (
    <label className="g-switch">
      {label ? <span className="g-switch-copy"><span className="g-switch-label" id={`${base}label`}>{label}</span>{hint ? <span className="g-switch-hint" id={`${base}hint`}>{hint}</span> : null}</span> : null}
      <input aria-busy={busy || undefined} aria-describedby={described} aria-labelledby={label ? `${base}label` : row?.labelId} checked={checked} disabled={disabled} onChange={(event) => { if (!busy) onChange(event.target.checked); }} ref={ref} role="switch" type="checkbox" />
      <span aria-hidden="true" className="g-switch-track" />
    </label>
  );
}

/** A section's rows (all, or a search's subset) inside the section Provider when it has one. */
export function SectionRows({ section, entries = section.entries }: { section: SettingsSectionDef; entries?: readonly SettingEntry[] }) {
  const rows = entries.map(({ id, label, info, description, advanced, layout, Control, Detail }) => (
    <SettingRow advanced={advanced} description={description} detail={Detail ? <Detail /> : undefined} id={id} info={info} key={id} label={label} layout={layout}><Control /></SettingRow>
  ));
  return section.Provider ? <section.Provider>{rows}</section.Provider> : <>{rows}</>;
}
