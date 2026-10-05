import { useEffect, useState } from 'react';

import { Checkbox, Field, fieldProps, Input, Select } from './ui';
import {
  normalizeOutputFolder,
  outputFolderProblem,
  type AcquisitionDefaults,
} from './sourcePreferences';

const qualityLabels = {
  best: 'Best available',
  best_1080p: 'Video · 1080p',
  best_editable: 'Editable (H.264)',
  audio_only: 'Audio only',
} as const;

export function SourceSettings({
  onChange,
  value,
}: {
  onChange: (patch: Partial<AcquisitionDefaults>) => void;
  value: AcquisitionDefaults;
}) {
  const [outputFolderDraft, setOutputFolderDraft] = useState(value.outputFolder);
  const folderProblem = outputFolderProblem(outputFolderDraft);
  // Dispatch only the changed key: the value prop can lag a workspace
  // re-render, and re-sending a stale full value would clobber an earlier
  // choice the user made through another control.
  const update = (patch: Partial<AcquisitionDefaults>) => onChange(patch);

  useEffect(() => setOutputFolderDraft(value.outputFolder), [value.outputFolder]);

  return (
    <section aria-label="Acquisition defaults" className="g-source-settings">
      <div className="g-source-settings">
          <Field label="Default quality">{(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => update({ formatPreset: event.currentTarget.value as AcquisitionDefaults['formatPreset'] })} value={value.formatPreset}>
              {Object.entries(qualityLabels).map(([preset, label]) => <option key={preset} value={preset}>{label}</option>)}
            </Select>
          )}</Field>
          <Field label="Container">{(ids) => (
            <Select {...fieldProps(ids)} onChange={(event) => update({ outputContainer: event.currentTarget.value as AcquisitionDefaults['outputContainer'] })} value={value.outputContainer}>
              <option value="mp4">MP4</option>
              <option value="webm">WebM</option>
              <option value="mkv">MKV</option>
            </Select>
          )}</Field>
          <Checkbox checked={value.downloadSubtitles} label="Download subtitles when available" onChange={(event) => update({ downloadSubtitles: event.currentTarget.checked })} />
          <Field label="Folder inside the Library">{(ids) => (
            <Input
              {...fieldProps(ids)}
              aria-describedby={folderProblem ? 'source-output-folder-problem' : undefined}
              aria-invalid={folderProblem ? 'true' : undefined}
              onBlur={() => { if (!folderProblem) update({ outputFolder: normalizeOutputFolder(outputFolderDraft) }); }}
              onChange={(event) => {
                const next = event.currentTarget.value;
                setOutputFolderDraft(next);
                if (!outputFolderProblem(next)) update({ outputFolder: normalizeOutputFolder(next) });
              }}
              type="text"
              value={outputFolderDraft}
            />
          )}</Field>
            {folderProblem ? <p id="source-output-folder-problem" role="alert">{folderProblem}</p> : null}
            <p className="g-setting-note">This is a relative folder inside the server-controlled Library.</p>
      </div>
    </section>
  );
}
