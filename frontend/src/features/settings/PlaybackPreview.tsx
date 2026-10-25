import type { CSSProperties } from 'react';
import { AudioLines, Play, SkipForward } from '../../luminaIcons';

import { captionStyle, DEFAULT_CAPTIONS } from '../watch/captionPrefs';
import { useSettingsHost } from './settingsHost';

/** A sample frame reflects the member's saved playback choices, without starting a stream. */
export function PlaybackPreview() {
  const { captions = DEFAULT_CAPTIONS, playbackMaxHeight, autoplayUpNext, playbackPrefs } = useSettingsHost();
  return <figure className="settings-playback-preview">
    <div className="settings-preview-frame" style={captionStyle(captions) as CSSProperties}>
      <svg aria-hidden="true" preserveAspectRatio="xMidYMid slice" viewBox="0 0 800 320">
        <rect fill="var(--g-reader-dark-paper)" width="800" height="320" />
        <circle cx="570" cy="100" r="44" fill="var(--g-gold)" />
        <path d="M0 205 180 60 350 225 510 140 800 265V320H0Z" fill="var(--g-overlay-ink-2)" opacity=".2" />
        <path d="m0 285 190-115 180 115 190-100 240 110v25H0Z" fill="var(--g-overlay-ink-2)" opacity=".3" />
        <path d="M0 310q220-80 410-5t390-15v30H0Z" fill="var(--g-overlay-bg)" opacity=".4" />
      </svg>
      <span className="settings-preview-label"><Play aria-hidden="true" /> Playback preview</span>
      <span className="settings-preview-quality">{playbackMaxHeight ? `${playbackMaxHeight}p maximum` : 'Best available quality'}</span>
      <span className="settings-preview-caption">Make time for a little wonder.</span>
    </div>
    <figcaption><span><SkipForward aria-hidden="true" /> Autoplay {autoplayUpNext ? 'on' : 'off'}</span><span><AudioLines aria-hidden="true" /> {playbackPrefs?.normalizeLoudness ? 'Balanced sound' : 'Original sound'}</span></figcaption>
  </figure>;
}
