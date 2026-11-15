import { ChevronDown } from '../../luminaIcons';
import type { AlbumAudioQuality, FileAudioQuality } from '../../types';

export function audioCodec(codec: string): string {
  return codec.startsWith('pcm_') ? 'PCM' : codec.toUpperCase();
}

export function audioResolution(depths: number[], rates: number[]): string {
  const range = (values: number[], unit: string) => values.length ? `${values.length === 1 ? values[0] : `${values[0]}–${values.at(-1)}`} ${unit}` : '';
  return [depths.length ? `${depths.length === 1 ? depths[0] : `${depths[0]}–${depths.at(-1)}`}-bit` : '', range(rates.map((rate) => rate / 1000), 'kHz')].filter(Boolean).join(' / ');
}

export function audioBitrate(rate: number | null | undefined): string {
  return rate && Number.isFinite(rate) && rate > 0 ? `${rate < 1 ? '<1' : Math.round(rate).toLocaleString()} kbps` : 'Bitrate unavailable';
}

export function fileQualityLabel(quality: FileAudioQuality): string {
  return [quality.codec ? audioCodec(quality.codec) : 'Format unavailable',
    audioResolution(quality.bit_depth ? [quality.bit_depth] : [], quality.sample_rate ? [quality.sample_rate] : []),
    audioBitrate(quality.bitrate_kbps)].filter(Boolean).join(' · ');
}

function albumFormat(quality: AlbumAudioQuality, showPartial = true): string {
  const format = quality.codecs.length > 1 ? `Mixed formats · ${quality.codecs.map(audioCodec).join(' + ')}` : quality.codecs[0] ? audioCodec(quality.codecs[0]) : 'Format unavailable';
  const resolution = audioResolution(quality.codecs.length > 1 ? [] : quality.bit_depths, quality.sample_rates);
  return [format, resolution, showPartial && !quality.formats_complete && quality.codecs.length ? 'Partial file metadata' : null].filter(Boolean).join(' · ');
}

export function albumQualityLabel(quality: AlbumAudioQuality): string {
  return `${albumFormat(quality)}, ${audioBitrate(quality.bitrate_kbps)}${quality.bitrate_kbps ? ' average' : ''}`;
}

export function AlbumCardQuality({ quality }: { quality: AlbumAudioQuality }) {
  return <span className="g-album-card-quality"><span>{albumFormat(quality)}</span><span>{audioBitrate(quality.bitrate_kbps)}{quality.bitrate_kbps ? ' avg' : ''}</span></span>;
}

export function AlbumFileQuality({ quality, label = 'Source file details' }: { quality: AlbumAudioQuality; label?: string }) {
  return <details className="g-album-file-quality">
    <summary aria-label={`${label}: ${albumQualityLabel(quality)}`} data-focus-item><span className="g-quality-format">{albumFormat(quality, false)}</span><span className="g-quality-rate">{quality.bitrate_kbps ? audioBitrate(quality.bitrate_kbps) : 'Details'}<ChevronDown aria-hidden="true" /></span></summary>
    <div className="g-album-file-facts">
      <dl>
        <div><dt>Format</dt><dd>{quality.codecs.map(audioCodec).join(' + ') || 'Unavailable'}</dd></div>
        <div><dt>Bit depth</dt><dd>{quality.bit_depths.length ? `${quality.bit_depths.join(' / ')}-bit${quality.codecs.length > 1 ? ' (where reported)' : ''}` : 'Not reported'}</dd></div>
        <div><dt>Sample rate</dt><dd>{audioResolution([], quality.sample_rates) || 'Not reported'}</dd></div>
        <div><dt>Average bitrate</dt><dd>{audioBitrate(quality.bitrate_kbps)}</dd></div>
      </dl>
      {!quality.formats_complete ? <p>Some file metadata is unavailable. The formats and resolutions shown cover known tracks.</p> : null}
      <p>Album bitrate is averaged by track duration and shown only when every visible track has a bitrate and duration. These are source-file facts; playback output may differ.</p>
    </div>
  </details>;
}
