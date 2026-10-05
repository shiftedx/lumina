import { type KeyboardEvent, type ReactNode, useId } from 'react';

import { formatDuration } from './utils';

export type TimelineChapter = {
  start_time: number;
  end_time?: number | null;
  title: string;
  /** Moment origin for marker styling: source | you | household | suggested. */
  origin?: string;
};

export type DescriptionTimestamp = {
  start: number;
  end: number;
  seconds: number;
  label: string;
};

/** Description text with its validated timestamp tokens rendered as seek buttons; untrusted text stays text. */
export function DescriptionWithTimestamps({ description, timestamps = [], onSeek }: {
  description: string;
  timestamps?: readonly DescriptionTimestamp[];
  onSeek: (seconds: number) => void;
}) {
  const tokens = normalizeTokens(description, timestamps);
  if (!tokens.length) return <p className="chapter-navigation__description">{description}</p>;
  const content: ReactNode[] = [];
  let offset = 0;
  for (const token of tokens) {
    content.push(description.slice(offset, token.start));
    content.push(
      <button aria-label={`Seek to ${token.label}`} className="chapter-navigation__timestamp" key={`${token.start}-${token.end}`} onClick={() => onSeek(token.seconds)} type="button">
        {token.label}
      </button>,
    );
    offset = token.end;
  }
  content.push(description.slice(offset));
  return <p className="chapter-navigation__description">{content}</p>;
}

export function ChapterSeekMarkers({ chapters, currentTime, duration, interactive = true, onSeek }: {
  chapters: readonly TimelineChapter[];
  currentTime: number;
  duration: number;
  interactive?: boolean;
  onSeek: (seconds: number) => void;
}) {
  const validChapters = normalizeChapters(chapters);
  const activeChapter = currentChapter(validChapters, currentTime);
  if (!Number.isFinite(duration) || duration <= 0 || !validChapters.length) return null;
  return <div aria-hidden={interactive ? undefined : true} aria-label={interactive ? 'Chapter seek markers' : undefined} className="chapter-seek-markers" data-chapter-markers="true">{validChapters.map((chapter) => interactive
    ? <button aria-current={activeChapter === chapter ? 'true' : undefined} aria-label={`Seek marker: ${chapter.title}`} data-chapter-marker="true" data-chapter-position={String((chapter.start_time / duration) * 100)} data-moment-origin={chapter.origin} key={`marker-${chapter.start_time}`} onClick={() => onSeek(chapter.start_time)} style={{ left: `${(chapter.start_time / duration) * 100}%` }} type="button" />
    : <span className={activeChapter === chapter ? 'active' : undefined} data-chapter-marker="true" data-moment-origin={chapter.origin} data-chapter-position={String((chapter.start_time / duration) * 100)} key={`marker-${chapter.start_time}`} style={{ left: `${(chapter.start_time / duration) * 100}%` }} />)}</div>;
}

export function formatChapterTime(seconds: number): string {
  const rounded = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainingSeconds = rounded % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
    : `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
}

function normalizeChapters(chapters: readonly TimelineChapter[]): TimelineChapter[] {
  const seen = new Set<number>();
  return chapters
    .filter((chapter): chapter is TimelineChapter => (
      Number.isFinite(chapter.start_time)
      && chapter.start_time >= 0
      && typeof chapter.title === 'string'
      && chapter.title.trim().length > 0
      && !seen.has(chapter.start_time)
      && (seen.add(chapter.start_time), true)
    ))
    .sort((left, right) => left.start_time - right.start_time);
}

function normalizeTokens(description: string, timestamps: readonly DescriptionTimestamp[]): DescriptionTimestamp[] {
  let offset = 0;
  return timestamps.filter((token): token is DescriptionTimestamp => {
    const valid = Number.isInteger(token.start)
      && Number.isInteger(token.end)
      && token.start >= offset
      && token.end > token.start
      && token.end <= description.length
      && Number.isFinite(token.seconds)
      && token.seconds >= 0
      && description.slice(token.start, token.end) === token.label;
    if (valid) offset = token.end;
    return valid;
  });
}

function currentChapter(chapters: readonly TimelineChapter[], currentTime: number): TimelineChapter | undefined {
  const time = Number.isFinite(currentTime) ? currentTime : 0;
  return chapters.reduce<TimelineChapter | undefined>((current, chapter) => (
    chapter.start_time <= time ? chapter : current
  ), undefined);
}

/** Chapters as a list under the description: timecode and name, the active one marked. */
export function ChapterList({ chapters, currentTime, onSeek }: { chapters: readonly TimelineChapter[]; currentTime: number; onSeek: (seconds: number) => void }) {
  const headingId = useId();
  if (chapters.length < 2) return null;
  const active = chapters.reduce((found, chapter, index) => (chapter.start_time <= currentTime ? index : found), 0);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
    if (!step) return;
    const rows = [...(event.currentTarget.closest('ol')?.querySelectorAll<HTMLButtonElement>('button') ?? [])];
    const next = rows[rows.indexOf(event.currentTarget) + step];
    if (!next) return;
    event.preventDefault();
    next.focus();
  };
  return (
    <section aria-labelledby={headingId} className="g-chapters">
      <h2 id={headingId}>Chapters</h2>
      <ol>
        {chapters.map((chapter, index) => (
          <li key={`${index}-${chapter.start_time}`}>
            <button aria-current={index === active ? 'true' : undefined} className={index === active ? 'is-active' : undefined} data-focus-item onClick={() => onSeek(chapter.start_time)} onKeyDown={onKeyDown} type="button">
              <span className="g-label">{formatDuration(chapter.start_time)}</span><span className="g-chapter-name">{chapter.title}</span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
