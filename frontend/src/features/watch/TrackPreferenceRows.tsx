import { useState } from 'react';
import { Field, fieldProps, Input, Switch } from '../../ui';
import type { CaptionPrefs } from './captionPrefs';
import { DEFAULT_TRACK_PREFERENCES, trackLanguage, type TrackPreferences } from './trackPreferences';

export function TrackPreferenceRows({ value, onChange }: { value: CaptionPrefs; onChange: (next: CaptionPrefs) => void }) {
  const prefs = value.tracks ?? DEFAULT_TRACK_PREFERENCES;
  const [error, setError] = useState<string | null>(null);
  const update = (patch: Partial<TrackPreferences>) => onChange({ ...value, tracks: { ...prefs, ...patch } });
  const saveLanguages = (key: 'audioLanguages' | 'subtitleLanguages', raw: string) => {
    const entries = raw.split(',').map((entry) => entry.trim()).filter(Boolean);
    if (entries.length > 10 || entries.some((entry) => entry.length > 32 || !trackLanguage(entry))) { setError('Use up to ten language codes, separated by commas, such as en, ja, es.'); return; }
    setError(null);
    update({ [key]: [...new Set(entries.map((entry) => trackLanguage(entry)!))] });
  };
  return <div aria-label="Audio and subtitle defaults" className="g-subrows" role="group"><strong>Audio and subtitle defaults</strong>
    <Field label="Audio languages" hint="In preference order, such as en, ja, es. Leave empty to use the file’s default audio.">{(ids) => <Input {...fieldProps(ids)} defaultValue={prefs.audioLanguages.join(', ')} onBlur={(event) => saveLanguages('audioLanguages', event.currentTarget.value)} />}</Field>
    <Field label="Subtitle languages" hint="In preference order. Leave empty to use an available track.">{(ids) => <Input {...fieldProps(ids)} defaultValue={prefs.subtitleLanguages.join(', ')} onBlur={(event) => saveLanguages('subtitleLanguages', event.currentTarget.value)} />}</Field>
    <Switch checked={prefs.subtitlesEnabled} label="Enable subtitles by default" onChange={(subtitlesEnabled) => update({ subtitlesEnabled })} />
    <Switch checked={prefs.forcedOnly} label="Use only forced subtitles" onChange={(forcedOnly) => update({ forcedOnly })} />
    <Switch checked={prefs.preferSdh} label="Prefer subtitles for deaf and hard of hearing (SDH)" onChange={(preferSdh) => update({ preferSdh })} />
    <p className="g-setting-note">Defaults apply when playback starts. Your choices in the player take priority.</p>
    {error ? <p className="auth-error" role="alert">{error}</p> : null}
  </div>;
}
