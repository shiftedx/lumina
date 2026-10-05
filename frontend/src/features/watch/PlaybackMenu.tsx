import { useId, useRef, useState } from 'react';
import { Settings2 } from 'lucide-react';

import { apiBaseUrl, ApiRequestError, type EnrichmentCapabilities, getEnrichmentJob, listSubtitleTracks, requestSubtitleJob, type SubtitleJobRequest } from '../../api';
import type { LuminaPlayerTrack } from '../../LuminaPlayer';
import type { EnrichmentJob, LocalPlaybackOptions, PlaybackSessionRequest, SubtitleOrigin, SubtitleTrack, TitleVersion } from '../../types';
import { errorMessage } from '../../utils';
import type { PlaybackPrefs } from '../../workspace';
import { Button, TextButton } from '../../ui';
import { useFetched, versionLabel } from '../titles/titleModel';
import type { CaptionPrefs } from './captionPrefs';
import { CaptionStyleRows } from './CaptionStyleRows';
import { usePollJob } from './usePollJob';

const JOB_DONE: ReadonlySet<string> = new Set(['succeeded', 'failed', 'canceled', 'interrupted']);
const ORIGIN_LABELS: Record<SubtitleOrigin, string> = {
  embedded: 'In the file', sidecar: 'Subtitle file', caption: 'Source captions', generated: 'Generated from speech', synced: 'Synced to audio', translated: 'Translated',
};
const JOB_STATUS: Record<string, string> = {
  pending: 'Waiting to start…', queued: 'Waiting to start…', running: 'Working… this can take a few minutes.', succeeded: 'Done. The new subtitles are in the list.',
  canceled: 'Stopped before it finished.', interrupted: 'Stopped before it finished.',
};
/** Target languages offered for translation (ISO 639-1; the server validates against its ISO table). */
export const TRANSLATE_LANGUAGES: Array<[string, string]> = [
  ['en', 'English'], ['es', 'Spanish'], ['fr', 'French'], ['de', 'German'], ['it', 'Italian'], ['pt', 'Portuguese'], ['nl', 'Dutch'],
  ['sv', 'Swedish'], ['pl', 'Polish'], ['ru', 'Russian'], ['ja', 'Japanese'], ['ko', 'Korean'], ['zh', 'Chinese'],
];

const NO_TRACKS: SubtitleTrack[] = [];
const TURNED_OFF = 'Turned off by the vault owner.';

/** The item's subtitle tracks; `reload` after a job adds one. An unavailable list (404, offline) means none. */
export function useSubtitleTracks(itemId: string | null) {
  const [revision, setRevision] = useState(0);
  const tracks = useFetched(itemId, () => listSubtitleTracks(itemId as string), revision);
  return { tracks: tracks.data ?? NO_TRACKS, reload: () => setRevision((value) => value + 1) };
}

/** Text tracks become <track> elements; image tracks are burned in by the session instead. */
export function subtitleTrackSources(tracks: SubtitleTrack[]): LuminaPlayerTrack[] {
  return tracks.filter((track) => track.format === 'text' && track.url).map((track) => ({ id: track.id, label: track.label, language: track.language ?? null, src: `${apiBaseUrl}${track.url}` }));
}

/** Drives LocalLibraryPlayer `request`: it restarts where the member is, only when a session is needed. */
export function PlaybackMenu({ itemId, options, versions, currentVersionId, tracks, subtitle, onSubtitle, request, onRequestChange, prefs, onPrefs, capabilities, onTracksChanged, captions, onCaptionsChange }: {
  itemId: string;
  options: LocalPlaybackOptions | null;
  versions: TitleVersion[];
  /** The Library item that was opened; picking it again clears `version_id`. */
  currentVersionId: string;
  tracks: SubtitleTrack[];
  subtitle: string | null;
  onSubtitle: (track: SubtitleTrack | null) => void;
  request: PlaybackSessionRequest;
  onRequestChange: (next: PlaybackSessionRequest) => void;
  prefs: PlaybackPrefs;
  onPrefs: (patch: Partial<PlaybackPrefs>) => void;
  capabilities: EnrichmentCapabilities | null;
  onTracksChanged: () => void;
  captions?: CaptionPrefs;
  onCaptionsChange?: (next: CaptionPrefs) => void;
}) {
  const [open, setOpen] = useState(false);
  const [styling, setStyling] = useState(false);
  const [job, setJob] = useState<EnrichmentJob | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [language, setLanguage] = useState('en');
  const toggleRef = useRef<HTMLButtonElement>(null);
  const id = `playback-menu-${useId().replace(/:/g, '')}`;
  usePollJob(job && !JOB_DONE.has(job.state) ? job.id : null, getEnrichmentJob, JOB_DONE, (next) => {
    setJob(next);
    if (next.state === 'succeeded') onTracksChanged();
  });

  const selected = tracks.find((track) => track.id === subtitle) ?? null;
  const textTrack = selected?.format === 'text' ? selected : null;
  const busy = job !== null && !JOB_DONE.has(job.state);
  const off = (feature: string) => capabilities?.disabled_features?.includes(feature);
  const reasons = {
    generate: off('subtitles_from_speech') ? TURNED_OFF : capabilities?.asr ? null : 'Needs speech recognition. Ask an admin to turn this on.',
    sync: off('sync') ? TURNED_OFF : textTrack ? null : 'Choose a subtitle track to sync it.',
    translate: off('translate') ? TURNED_OFF : !capabilities?.ai_summaries ? 'Needs the assistant server. Ask an admin to turn this on.' : textTrack ? null : 'Choose a subtitle track to translate it.',
  };
  const quality = [{ value: null as number | null, label: options?.mode === 'direct' ? 'Original' : 'Auto' }, ...(options?.quality_heights ?? []).map((height) => ({ value: height as number | null, label: `${height}p` }))];
  const audio = options?.audio_tracks ?? [];
  const defaultAudio = audio.find((entry) => entry.default)?.index ?? audio[0]?.index ?? null;

  async function start(request: SubtitleJobRequest) {
    setJobError(null);
    try {
      setJob(await requestSubtitleJob(itemId, request));
    } catch (failure) {
      const conflict = failure instanceof ApiRequestError && failure.status === 409;
      setJobError(conflict && failure.message === 'ai_feature_disabled' ? TURNED_OFF : conflict ? 'This needs a server the vault owner has not set up yet.' : errorMessage(failure, 'Lumina could not start that.'));
    }
  }
  function close() {
    setOpen(false);
    setStyling(false);
    toggleRef.current?.focus();
  }
  const action = (key: keyof typeof reasons, label: string, request: SubtitleJobRequest | null) => (
    <>
      <Button aria-describedby={reasons[key] ? `${id}-${key}` : undefined} disabled={Boolean(reasons[key]) || busy || !request} onClick={() => { if (request) void start(request); }} variant="secondary">{label}</Button>
      {reasons[key] ? <p className="playback-menu-reason" id={`${id}-${key}`}>{reasons[key]}</p> : null}
    </>
  );

  return (
    <div
      className="playback-menu"
      // Close when focus moves to another control; a null target (clicking the video, a button turning disabled) keeps it open.
      onBlur={(event) => { if (open && event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false); }}
      onKeyDown={(event) => { if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); close(); } }}
    >
      <button aria-controls={open ? id : undefined} aria-expanded={open} aria-label="Playback settings" onClick={() => setOpen((value) => !value)} ref={toggleRef} type="button"><Settings2 aria-hidden="true" /></button>
      {open ? (
        <div aria-label="Playback settings" className="playback-menu-panel player-panel" id={id} role="group">
          {styling && captions && onCaptionsChange ? (
            <>
              <p className="g-label">Caption style</p>
              <CaptionStyleRows onChange={onCaptionsChange} value={captions} />
              <TextButton onClick={() => setStyling(false)}>Back</TextButton>
            </>
          ) : <>
          {quality.length > 1 ? (
            <fieldset><legend className="g-label">Quality</legend>
              {quality.map((entry) => <label key={entry.label}><input checked={(request.max_height ?? null) === entry.value} name={`${id}-quality`} onChange={() => onRequestChange({ ...request, max_height: entry.value })} type="radio" /> {entry.label}</label>)}
            </fieldset>
          ) : null}
          {versions.length > 1 ? (
            <fieldset><legend className="g-label">Version</legend>
              {versions.map((version) => <label key={version.item_id}><input checked={version.item_id === (request.version_id ?? currentVersionId)} name={`${id}-version`} onChange={() => onRequestChange({ ...request, version_id: version.item_id === currentVersionId ? null : version.item_id })} type="radio" /> {versionLabel(version)}</label>)}
            </fieldset>
          ) : null}
          {audio.length > 1 ? (
            <fieldset><legend className="g-label">Audio</legend>
              {audio.map((entry) => <label key={entry.index}><input checked={(request.audio_index ?? defaultAudio) === entry.index} name={`${id}-audio`} onChange={() => onRequestChange({ ...request, audio_index: entry.index === defaultAudio ? null : entry.index })} type="radio" /> {entry.label}</label>)}
            </fieldset>
          ) : null}
          <fieldset><legend className="g-label">Subtitles</legend>
            <label><input checked={subtitle === null} name={`${id}-subtitles`} onChange={() => onSubtitle(null)} type="radio" /> Off</label>
            {tracks.map((track) => <label key={track.id}><input checked={subtitle === track.id} name={`${id}-subtitles`} onChange={() => onSubtitle(track)} type="radio" /> {track.format === 'image' ? `${track.label} (burned in)` : track.label}<small>{ORIGIN_LABELS[track.origin]}</small></label>)}
            {captions && onCaptionsChange ? <TextButton onClick={() => setStyling(true)}>Caption style…</TextButton> : null}
          </fieldset>
          <div className="playback-menu-actions">
            {action('generate', 'Generate subtitles', { kind: 'generate' })}
            {action('sync', 'Sync to audio', textTrack ? { kind: 'sync', trackId: textTrack.id } : null)}
            <label>Translate to
              <select disabled={Boolean(reasons.translate)} onChange={(event) => setLanguage(event.target.value)} value={language}>{TRANSLATE_LANGUAGES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select>
            </label>
            {action('translate', 'Translate', textTrack ? { kind: 'translate', trackId: textTrack.id, targetLanguage: language } : null)}
            {job ? <p aria-live="polite" className="playback-menu-status" role="status">{job.state === 'failed' ? job.error || 'That did not work.' : JOB_STATUS[job.state] ?? job.state}</p> : null}
            {jobError ? <p className="playback-menu-status" role="alert">{jobError}</p> : null}
          </div>
          <fieldset><legend className="g-label">Sound and skipping</legend>
            <label><input checked={prefs.normalizeLoudness} onChange={(event) => onPrefs({ normalizeLoudness: event.currentTarget.checked })} type="checkbox" /> Even out loudness</label>
            <label><input checked={prefs.profanity.enabled} onChange={(event) => onPrefs({ profanity: { ...prefs.profanity, enabled: event.currentTarget.checked } })} type="checkbox" /> Mute strong language<small>Web player only · may miss a word</small></label>
            <label><input checked={prefs.autoSkip.intro} onChange={(event) => onPrefs({ autoSkip: { ...prefs.autoSkip, intro: event.currentTarget.checked } })} type="checkbox" /> Skip intros automatically</label>
          </fieldset>
          </>}
        </div>
      ) : null}
    </div>
  );
}
