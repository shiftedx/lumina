import { type FormEvent, useId, useState } from 'react';
import { Bookmark } from 'lucide-react';
import { createLibraryNote, deleteLibraryNote, type Summary } from '../../api';
import { formatChapterTime, type TimelineChapter } from '../../chapters';
import { type LibraryNote } from '../../types';

export type MomentOrigin = 'source' | 'you' | 'household' | 'suggested';
export type Moment = { key: string; seconds: number; title: string; kind: 'Chapter' | 'Key point' | 'Bookmark'; origin: MomentOrigin; detail: string; noteId?: string };

const ORIGIN_LABEL: Record<MomentOrigin, string> = { source: 'Source', you: 'You', household: 'Household', suggested: 'AI-suggested' };
const lines = (count: number) => `${count} transcript line${count === 1 ? '' : 's'}`;

/**
 * One timeline from three truthful origins: the source's own chapters, members' timestamped
 * notes (a bookmark IS a timestamped note) and the local summary's evidence-grounded chapters
 * and key points. Nothing is merged or rewritten; each moment keeps its origin.
 */
export function buildMoments(chapters: readonly TimelineChapter[], summary: Summary | null, notes: readonly LibraryNote[]): Moment[] {
  const moments: Moment[] = [];
  for (const chapter of chapters) {
    if (Number.isFinite(chapter.start_time) && chapter.start_time >= 0 && chapter.title?.trim()) {
      moments.push({ key: `source-${chapter.start_time}`, seconds: chapter.start_time, title: chapter.title, kind: 'Chapter', origin: 'source', detail: 'Chapter from the source' });
    }
  }
  for (const note of notes) {
    if (note.timestamp_ms == null) continue;
    const author = note.is_owner ? 'you' : note.author_display_name || note.author_username || 'a household member';
    moments.push({
      key: `note-${note.id}`, seconds: note.timestamp_ms / 1000, title: note.body.split('\n')[0], kind: 'Bookmark', origin: note.is_owner ? 'you' : 'household',
      detail: `Bookmark by ${author}${note.visibility === 'private' ? ' · private' : ' · shared with household'}`, noteId: note.can_delete ? note.id : undefined,
    });
  }
  for (const chapter of summary?.chapters || []) {
    moments.push({ key: `ai-chapter-${chapter.cue_ordinal}`, seconds: chapter.start_ms / 1000, title: chapter.title, kind: 'Chapter', origin: 'suggested', detail: 'AI-suggested chapter · from the transcript' });
  }
  (summary?.key_points || []).forEach((point, index) => {
    moments.push({ key: `ai-point-${index}`, seconds: point.start_ms / 1000, title: point.text, kind: 'Key point', origin: 'suggested', detail: `AI-suggested key point · grounded in ${lines(point.cue_ordinals.length)}` });
  });
  return moments.sort((a, b) => a.seconds - b.seconds);
}

/** Moments tool: every entry seeks; members bookmark the current time without leaving the player. */
export function MomentsPanel({ moments, currentTime, showSuggested, onShowSuggested, onSeek, itemId, getTime, onBookmarksChanged }: {
  moments: Moment[];
  currentTime: number;
  showSuggested: boolean;
  onShowSuggested: (show: boolean) => void;
  onSeek: (seconds: number) => void;
  /** Library item to bookmark; absent for streamed media, which has no owned record to attach to. */
  itemId?: string;
  getTime: () => number;
  onBookmarksChanged: () => void;
}) {
  const headingId = useId();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasSuggested = moments.some((moment) => moment.origin === 'suggested');
  const visible = showSuggested ? moments : moments.filter((moment) => moment.origin !== 'suggested');
  const active = visible.reduce<Moment | undefined>((current, moment) => (moment.seconds <= currentTime ? moment : current), undefined);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onBookmarksChanged();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to save this bookmark.');
    } finally {
      setBusy(false);
    }
  }

  function bookmark(event: FormEvent) {
    event.preventDefault();
    if (!itemId) return;
    const timestamp_ms = Math.max(0, Math.round(getTime() * 1000));
    void run(async () => {
      await createLibraryNote(itemId, { body: name.trim() || `Bookmark at ${formatChapterTime(timestamp_ms / 1000)}`, visibility: 'private', timestamp_ms });
      setName('');
    });
  }

  return (
    <section aria-labelledby={headingId} className="chapter-navigation moments-panel">
      <header className="chapter-navigation__header">
        <div>
          <h2 id={headingId}>Moments</h2>
          <p className="chapter-navigation__count">{visible.length} {visible.length === 1 ? 'moment' : 'moments'}</p>
        </div>
        {hasSuggested ? <label className="notes-toggle"><input checked={showSuggested} onChange={(event) => onShowSuggested(event.target.checked)} role="switch" type="checkbox" /> Show AI suggestions</label> : null}
      </header>
      {itemId ? (
        <form className="moments-bookmark" onSubmit={bookmark}>
          <input aria-label="Bookmark name (optional)" className="g-input" disabled={busy} maxLength={200} onChange={(event) => setName(event.target.value)} placeholder="Name this moment" value={name} />
          <button className="g-button" disabled={busy} type="submit"><Bookmark /> Bookmark {formatChapterTime(currentTime)}</button>
        </form>
      ) : null}
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      {visible.length ? (
        <ol aria-label="Moments" className="chapter-navigation__list">
          {visible.map((moment) => (
            <li className="chapter-navigation__item" key={moment.key}>
              <button
                aria-current={moment === active ? 'true' : undefined}
                aria-label={`Seek to ${formatChapterTime(moment.seconds)}: ${moment.title}. ${moment.detail}`}
                className="chapter-navigation__seek"
                onClick={() => onSeek(moment.seconds)}
                type="button"
              >
                <time className="chapter-navigation__time">{formatChapterTime(moment.seconds)}</time>
                <span className="chapter-navigation__title">{moment.title}<small>{moment.detail}</small></span>
                <span className="moment-origin" data-origin={moment.origin}>{ORIGIN_LABEL[moment.origin]}</span>
              </button>
              {moment.noteId ? <button aria-label={`Remove bookmark ${moment.title}`} className="g-text-button" disabled={busy} onClick={() => void run(() => deleteLibraryNote(moment.noteId!))} type="button">Remove</button> : null}
            </li>
          ))}
        </ol>
      ) : <p className="notes-empty">No moments yet. Bookmark the current time to find it again later.</p>}
    </section>
  );
}
