/**
 * Pure rules for the gallery title page. The page, the episode row and
 * key scenes share them, and titlePageModel.test.ts pins every string.
 */
import type { PersonType, TitleDetail, TitlePerson, TitleSummary, TitleVersion } from '../../types';
import { formatBytes } from '../../utils';
import { episodeCode, formatRuntime, versionQuality } from '../titles/titleModel';
import { type GalleryTheme, posterMarker, readableAccent, safeColour } from './galleryModel';

/** AI extras abandon their request after 8 s. */
export const AI_TIMEOUT = { timeoutMs: 8_000 };
/** Logo art replaces the headline only for names longer than this. */
export const LOGO_AFTER_CHARS = 30;
/** How far the card before the next episode peeks into view. */
export const EPISODE_PEEK_PX = 40;

const plural = (count: number, noun: string): string => `${count} ${noun}${count === 1 ? '' : 's'}`;
/** Specials are not a season in "3 seasons". */
const regularSeasons = (title: TitleDetail): number => title.children.filter((child) => child.type === 'season' && (child.index_number ?? 0) > 0).length;

/** Whole minutes left in a started, unfinished title; null when not started, finished, or of unknown length. */
export function minutesLeft(title: TitleSummary): number | null {
  const { played, position_seconds: position } = title.user_data;
  const duration = title.user_data.duration_seconds || title.runtime_seconds || 0;
  if (played || position <= 0 || duration <= position) return null;
  return Math.max(1, Math.round((duration - position) / 60));
}

/** The label line under the headline. Works on a summary too, so the instant render has it. */
export function metaLine(title: TitleSummary | TitleDetail): string[] {
  const detail = 'children' in title ? title : null;
  const parts: Array<string | null> = [title.year ? String(title.year) : null, title.official_rating ?? null];
  if (title.type === 'series') {
    const seasons = detail ? regularSeasons(detail) : 0;
    parts.push(seasons ? plural(seasons, 'season') : null, ...title.genres.slice(0, 3));
    const next = detail?.play_next;
    const code = next ? episodeCode(next) : '';
    if (next && code) {
      const started = !next.user_data.played && next.user_data.position_seconds > 0;
      const left = minutesLeft(next);
      parts.push(started ? `Continue ${code}${left ? `, ${left} min left` : ''}` : `Up next ${code}`);
    }
  } else {
    parts.push(formatRuntime(title.runtime_seconds), ...title.genres.slice(0, 3));
    const best = detail?.versions.reduce<TitleVersion | null>((top, version) => ((version.height ?? 0) > (top?.height ?? 0) ? version : top), null);
    parts.push(best ? versionQuality(best) : null);
  }
  return parts.filter((part): part is string => Boolean(part));
}

export type SideBlock = { label: string; text: string };

function movieLibrary(detail: TitleDetail): string {
  const versions = [...detail.versions].sort((a, b) => (b.height ?? 0) - (a.height ?? 0));
  const bytes = versions.reduce((sum, version) => sum + (version.file_size ?? 0), 0);
  return [...new Set(versions.map((version) => versionQuality(version))), bytes ? formatBytes(bytes) : null].filter(Boolean).join(' · ');
}

function seriesLibrary(detail: TitleDetail): string {
  const seasons = regularSeasons(detail);
  return [
    seasons ? plural(seasons, 'season') : null,
    detail.episode_count ? plural(detail.episode_count, 'episode') : null,
    detail.best_height ? versionQuality({ hdr: false, height: detail.best_height }) : null,
  ].filter(Boolean).join(' · ');
}

/** The Cast row lists performers only; the crew is credited in the side column (cast photos addendum). */
export const castOf = (people: TitlePerson[]): TitlePerson[] => people.filter((person) => person.type === 'Actor' || person.type === 'GuestStar');

/** Side column blocks; a block with nothing to say is left out. "Part of" is a link, so the page adds it. */
export function sideBlocks(detail: TitleDetail): SideBlock[] {
  const names = (type: PersonType, limit = Infinity) => detail.people.filter((person) => person.type === type).slice(0, limit).map((person) => person.name).join(' · ');
  const series = detail.type === 'series';
  return [
    { label: series ? 'Created by' : 'Directed by', text: names(series ? 'Creator' : 'Director') },
    { label: 'Written by', text: names('Writer', 3) },
    { label: 'Produced by', text: names('Producer', 3) },
    { label: 'Music by', text: names('Composer', 2) },
    { label: 'Starring', text: names('Actor', 3) },
    { label: 'In your library', text: series ? seriesLibrary(detail) : movieLibrary(detail) },
  ].filter((block) => block.text);
}

export const usesLogo = (title: Pick<TitleDetail, 'name' | 'logo'>): boolean => title.name.length > LOGO_AFTER_CHARS && Boolean(title.logo);

/** No drop cap when the overview starts with anything other than a letter, nor on one too short to wrap past it. */
export const DROP_CAP_MIN_CHARS = 160;
export const hasDropCap = (text: string | null | undefined): boolean => Boolean(text && text.length >= DROP_CAP_MIN_CHARS && /^\p{L}/u.test(text));

/** The story so far appears only for a show the member is in the middle of. */
export const storyVisible = (detail: TitleDetail): boolean =>
  detail.type === 'series' && Boolean(detail.user_data.last_watched_at) && (detail.user_data.unplayed_count ?? 0) > 0 && Boolean(detail.play_next);

/** --g-accent: the Backdrop accent, else the Primary accent, else ink, kept readable on paper. */
export function pageAccent(title: Pick<TitleSummary, 'poster' | 'backdrop'>, theme: GalleryTheme): string {
  return readableAccent(safeColour(title.backdrop?.accent) ?? safeColour(title.poster?.accent), theme);
}

/** "4. The Channel", "4–5. The Channel", "Special 2. Behind the Lamp"; the bare name when unnumbered. */
export function episodeHeading(episode: TitleSummary): string {
  const { index_number: index, index_number_end: end, season_number: season } = episode;
  if (index == null) return episode.name;
  const number = end != null && end > index ? `${index}–${end}` : `${index}`;
  return `${season === 0 ? `Special ${number}` : number}. ${episode.name}`;
}

/** "44 min", or "28 min left" while in progress. */
export function episodeMeta(episode: TitleSummary): string | null {
  const left = minutesLeft(episode);
  if (left) return `${left} min left`;
  return episode.runtime_seconds && episode.runtime_seconds >= 60 ? `${Math.round(episode.runtime_seconds / 60)} min` : null;
}

/** Watched episodes may show their AI summary; unwatched and in-progress ones only ever the teaser (criterion 9). */
export const episodeBlurb = (episode: TitleSummary, summary: string | undefined): string | null =>
  (episode.user_data.played && summary ? summary : episode.overview || null);

/** The poster marker's state in words, as posterLabel says it: ", unwatched", ", in progress, 28 minutes left", ", watched". */
function episodeState(episode: TitleSummary): string {
  const marker = posterMarker(episode);
  if (marker.kind === 'unwatched') return ', unwatched';
  if (marker.kind === 'progress') {
    const left = minutesLeft(episode);
    return left ? `, in progress, ${plural(left, 'minute')} left` : ', in progress';
  }
  return episode.user_data.played ? ', watched' : '';
}

/** An episode card's accessible name holds its visible heading (WCAG 2.5.3): "Play 4. The Channel, S2 · E4, watched". */
export const episodeLabel = (episode: TitleSummary): string =>
  `Play ${[episodeHeading(episode), episodeCode(episode)].filter(Boolean).join(', ')}${episodeState(episode)}`;

/** scrollLeft that makes the card at `cardOffset` the first fully visible one, with the card before peeking in. */
export const playNextScrollLeft = (cardOffset: number, gap: number): number => Math.max(0, cardOffset - gap - EPISODE_PEEK_PX);

/** The first four stills in view load at class 2, the rest at class 3. */
export const stillPriority = (index: number, firstInView: number): 2 | 3 => (index >= firstInView && index < firstInView + 4 ? 2 : 3);
