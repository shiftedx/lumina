/** The request sheet: seasons, the anime language, the member's quota and where the request goes. */
import { useEffect, useState } from 'react';

import { Button, Checkbox, Dialog, Field, fieldProps, Select, SegmentedControl, Skeleton, useToast } from '../../ui';
import { type CatalogItem, type CatalogStatus, createRequest, errorCode, getCatalogTitle, type Language, type Quota, type Season } from './requestsApi';
import { useLoad } from './requestsContext';
import {
  buildRequest, canAskFor, catalogId, errorCopy, isSeries, needsLanguage, optimisticState, quotaLine, rememberedLanguage, rememberLanguage,
  requestableSeasons, routingLine, type SeasonMode, seasonsPayload, seasonsWords, unrequestableCopy,
} from './requestsModel';
import { Art } from './parts';
import { TRANSLATE_LANGUAGES } from '../watch/PlaybackMenu';

const SEASON_MODES = [{ value: 'all', label: 'All seasons' }, { value: 'latest', label: 'Latest' }, { value: 'pick', label: 'Choose' }] as const;

export function SeasonPicker({ seasons, mode, picked, onMode, onPicked }: { seasons: Season[]; mode: SeasonMode; picked: number[]; onMode: (mode: SeasonMode) => void; onPicked: (picked: number[]) => void }) {
  return (
    <div className="rq-sheet-block">
      <SegmentedControl legend="Seasons" onChange={onMode} options={SEASON_MODES} value={mode} />
      {mode === 'pick' ? (
        <div className="rq-season-picks">
          {requestableSeasons(seasons).map((season) => (
            <Checkbox
              checked={picked.includes(season.number)}
              hint={`${season.episode_count} episode${season.episode_count === 1 ? '' : 's'}${season.air_date ? ` · ${season.air_date.slice(0, 4)}` : ''}`}
              key={season.number}
              label={season.name}
              onChange={(event) => onPicked(event.currentTarget.checked ? [...picked, season.number] : picked.filter((value) => value !== season.number))}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

const LANGUAGES: Array<{ value: Language; title: string; body: string }> = [
  { value: 'dub', title: 'English audio', body: 'English voices, when a dub exists' },
  { value: 'sub', title: 'Japanese audio + English subtitles', body: 'Japanese voices with English subtitles' },
];

/** Two large choices, native radios underneath (arrow keys are the browser's). */
export function LanguageChoice({ value, onChange }: { value: Language | null; onChange: (value: Language) => void }) {
  return (
    <fieldset className="rq-language">
      <legend className="g-label">How do you want to watch it?</legend>
      {LANGUAGES.map((option) => (
        <label className={`rq-language-option ${value === option.value ? 'is-chosen' : ''}`} key={option.value}>
          <input checked={value === option.value} name="rq-language" onChange={() => onChange(option.value)} type="radio" value={option.value} />
          <span className="rq-language-title">{option.title}</span>
          <span className="rq-language-body">{option.body}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function languageName(code: string): string {
  try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) || code; } catch { return code; }
}

export function LanguagePreferences({ audio, subtitles, onAudio, onSubtitles, observed = [] }: { audio: string; subtitles: string; onAudio: (value: string) => void; onSubtitles: (value: string) => void; observed?: string[] }) {
  const codes = [...new Set([...TRANSLATE_LANGUAGES.map(([code]) => code), ...observed, audio, subtitles].filter(Boolean))];
  return <div className="rq-sheet-block">
    <Field label="Desired audio language">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => onAudio(event.currentTarget.value)} value={audio}><option value="">Any audio</option>{codes.map((code) => <option key={code} value={code}>{languageName(code)}</option>)}</Select>}</Field>
    <Field label="Desired subtitle language (optional)">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => onSubtitles(event.currentTarget.value)} value={subtitles}><option value="">No subtitle requirement</option>{codes.map((code) => <option key={code} value={code}>{languageName(code)}</option>)}</Select>}</Field>
  </div>;
}

export type RequestSheetProps = {
  item: CatalogItem;
  userId: string;
  quota: Quota | null;
  onClose: () => void;
  /** An optimistic status (null reverts to the catalog's own), then the server's. */
  onStatus: (key: string, status: CatalogStatus | null) => void;
  /** After a request was accepted (quotas changed). */
  onRequested: () => void;
};

export function RequestSheet({ item, userId, quota, onClose, onStatus, onRequested }: RequestSheetProps) {
  const toast = useToast();
  const id = catalogId(item);
  // Series need their seasons, and an anime still being matched may have resolved since the card loaded: always ask fresh.
  const needsDetail = Boolean(id) && (isSeries(item) || item.requestable === false);
  const detail = useLoad(needsDetail ? `${item.kind}:${id}` : '', (signal) => (needsDetail ? getCatalogTitle(item.kind, id!, signal) : Promise.resolve(null)));
  const current: CatalogItem = detail.data ?? item;
  const seasons = detail.data?.seasons ?? [];
  const [mode, setMode] = useState<SeasonMode>('all');
  const [picked, setPicked] = useState<number[]>([]);
  const [language, setLanguage] = useState<Language | null>(() => rememberedLanguage(userId));
  const [audio, setAudio] = useState('');
  const [subtitles, setSubtitles] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // The engine may decide a show or film is anime (TMDB tags it Japanese animation) and ask for the language.
  const [languageAsked, setLanguageAsked] = useState(false);
  useEffect(() => setProblem(null), [mode, picked, language, audio, subtitles]);

  const series = isSeries(current);
  const asksLanguage = needsLanguage(current) || languageAsked;
  const loading = needsDetail && detail.loading;
  const allowed = canAskFor(current) && quota?.can_request !== false && quota?.remaining !== 0;
  const chosenSeasons = series ? seasonsPayload(mode, picked, seasons) : null;
  const ready = !loading && allowed && (!series || chosenSeasons !== null) && (!asksLanguage || language !== null || Boolean(audio)) && (current.status.state !== 'available' || Boolean(audio || subtitles || language));

  async function submit() {
    if (!ready) return;
    setBusy(true);
    setProblem(null);
    if (item.status.state !== 'available') onStatus(item.key, { state: audio || subtitles ? 'pending' : optimisticState(quota) });
    try {
      const body = buildRequest(current, { seasons: chosenSeasons, language, audio_language: audio || undefined, subtitle_language: subtitles || undefined });
      const created = await createRequest(languageAsked && language ? { ...body, language } : body);
      if (asksLanguage && language) rememberLanguage(userId, language);
      // More seasons of a series already asked for come back as a new pending request for just those seasons.
      const followUp = Boolean(item.status.request_id) && created.id !== item.status.request_id && Array.isArray(created.seasons) && created.seasons.length > 0;
      onStatus(item.key, item.status.state === 'available' || followUp ? null : { state: created.status, request_id: created.id, progress: created.progress, library_title_id: created.library_title_id });
      const message = followUp && Array.isArray(created.seasons)
        ? `Asked for ${seasonsWords(created.seasons)} of ${item.title}${created.status === 'pending' ? '; an admin will review' : ''}.`
        : created.status === 'pending' ? `Requested ${item.title}. An admin will take a look.` : `Requested ${item.title}.`;
      toast({ tone: 'success', message });
      onRequested();
      onClose();
    } catch (error) {
      onStatus(item.key, null);
      const code = errorCode(error);
      if (code === 'language_required') setLanguageAsked(true);
      setProblem(errorCopy(code));
      setBusy(false);
    }
  }

  const blocked = !allowed && !loading
    ? (current.requestable === false ? unrequestableCopy(current.unrequestable_reason) : quota?.remaining === 0 ? errorCopy('quota_exceeded') : quota?.can_request === false ? quotaLine(quota) : 'This title already has a request.')
    : null;

  return (
    <Dialog
      busy={busy}
      className="rq-sheet"
      footer={<><Button onClick={onClose} variant="quiet">Cancel</Button><Button aria-disabled={!ready || undefined} busy={busy} onClick={() => void submit()} variant="primary">Request</Button></>}
      onClose={onClose}
      open
      size="md"
      title={`Request ${item.title}`}
    >
      <div className="rq-sheet-head">
        <span className="rq-frame rq-sheet-poster"><Art fallback={item.title} src={item.poster_url} /></span>
        <div>
          <p className="rq-sheet-title">{item.title}</p>
          {item.year ? <p className="rq-muted">{item.year}</p> : null}
          <p className="rq-sheet-route">{!quota?.auto_approve || (!audio && !subtitles) ? routingLine(quota, item.kind === 'anime' && item.media_type === 'movie' ? 'movie' : item.kind) : "An owner may need to review this request"}</p>
          {quota ? <p className="rq-muted">{quotaLine(quota)}</p> : null}
        </div>
      </div>
      {loading ? <Skeleton count={3} label="Loading seasons" shape="line" /> : null}
      {!loading && series && seasons.length && allowed ? <SeasonPicker mode={mode} onMode={setMode} onPicked={setPicked} picked={picked} seasons={seasons} /> : null}
      {!loading && asksLanguage && allowed ? <LanguageChoice onChange={(value) => { setLanguage(value); setAudio(value === 'dub' ? 'en' : 'ja'); setSubtitles(value === 'sub' ? 'en' : ''); }} value={language} /> : null}
      {!loading && allowed && (asksLanguage || current.kind === 'anime') ? <><LanguagePreferences audio={audio} onAudio={(value) => { setAudio(value); setLanguage(null); }} onSubtitles={(value) => { setSubtitles(value); setLanguage(null); }} subtitles={subtitles} observed={detail.data?.status.language_availability?.files.flatMap((file) => [...file.audio_languages, ...file.subtitle_languages])} /><p className="rq-muted">Language-specific requests may need an owner to review and search manually. Automatic anime searches follow the manager's settings and may replace an existing version.</p></> : null}
      {blocked ? <p className="rq-sheet-note">{blocked}</p> : null}
      {problem ? <p className="rq-sheet-note is-problem" role="alert">{problem}</p> : null}
    </Dialog>
  );
}
