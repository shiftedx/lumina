import type { CSSProperties } from 'react';
import { SegmentedControl } from '../../ui';
import { CAPTION_BACKGROUNDS, CAPTION_SIZES, captionStyle, type CaptionPrefs } from './captionPrefs';

export function CaptionStyleRows({ value, onChange }: { value: CaptionPrefs; onChange: (next: CaptionPrefs) => void }) {
  return (
    <div className="caption-style-rows">
      <div className="caption-preview" style={captionStyle(value) as CSSProperties}><span>The quick fox waits for the ferry.</span></div>
      <SegmentedControl legend="Size" onChange={(size) => onChange({ ...value, size })} options={CAPTION_SIZES} value={value.size} />
      <SegmentedControl legend="Background" onChange={(background) => onChange({ ...value, background })} options={CAPTION_BACKGROUNDS} value={value.background} />
    </div>
  );
}
