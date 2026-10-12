/**
 * Home's pure rules: the masthead text, the hero's pick, art and meta line, and the captions and
 * accessible names of Home's 16:9 title cards.
 */
import type { PlaybackProgress, TitleArt, TitleDetail, TitleRow, TitleSummary } from '../../types';
import { episodeCode, formatRuntime } from '../titles/titleModel';

export function greeting(now = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

/** The masthead kicker, e.g. "Tuesday 29 September" (the label style sets it in capitals). */
export const mastheadDate = (now = new Date()): string => new Intl.DateTimeFormat(undefined, { weekday: 'long', day: 'numeric', month: 'long' }).format(now);

export const firstName = (user: { display_name?: string | null; username: string }): string => user.display_name?.split(' ')[0] || user.username;

export type HeroPick =
  | { kind: 'continue'; entry: PlaybackProgress; title: TitleSummary }
  | { kind: 'newest'; title: TitleSummary }
  | { kind: 'recommended'; title: TitleSummary; reason: string | null };
/** How many of the newest titles the hero looks through for one with a backdrop. */
export const NEWEST_SCAN = 10;

/**
 * The first Continue entry with a Media title (Continue is ordered by last watched); else the
 * first of the newest 10 with a backdrop, else the newest; else no hero.
 */
export function pickHero(continueWatching: readonly PlaybackProgress[], newest: readonly TitleSummary[] | null): HeroPick | null {
  const entry = continueWatching.find((candidate) => candidate.title);
  if (entry?.title) return { kind: 'continue', entry, title: entry.title };
  const title = newest?.slice(0, NEWEST_SCAN).find((candidate) => candidate.backdrop) ?? newest?.[0];
  return title ? { kind: 'newest', title } : null;
}

/** The title whose detail the hero loads: an episode anchors on its series. */
export const heroAnchor = (title: TitleSummary): string => title.series_id ?? title.id;

/** The hero carousel holds at most this many slides, of which at most HERO_CONTINUE resume something. */
export const HERO_SLIDES = 8;
export const HERO_CONTINUE = 5;
export const heroKey = (pick: HeroPick): string => (pick.kind === 'continue' ? pick.entry.id : `${pick.kind}:${pick.title.id}`);

/**
 * The hero carousel's slides: the member's Continue titles first (else the newest pick), then their recommendations
 * (the Recommended row, then Because you watched with its row title as the reason), one slide per anchor.
 */
export function heroSlides(continueWatching: readonly PlaybackProgress[], rows: readonly TitleRow[] | null, newest: readonly TitleSummary[] | null): HeroPick[] {
  const resume = continueWatching.flatMap((entry): HeroPick[] => (entry.title ? [{ kind: 'continue', entry, title: entry.title }] : [])).slice(0, HERO_CONTINUE);
  const fallback = resume.length ? null : pickHero([], newest);
  const slides = fallback ? [fallback] : resume;
  const seen = new Set(slides.map((pick) => heroAnchor(pick.title)));
  const ranked = [...(rows ?? []).filter((row) => row.kind === 'recommended'), ...(rows ?? []).filter((row) => row.kind === 'because_you_watched')];
  for (const row of ranked) {
    for (const title of row.items) {
      if (slides.length >= HERO_SLIDES) return slides;
      if (seen.has(heroAnchor(title))) continue;
      seen.add(heroAnchor(title));
      slides.push({ kind: 'recommended', title, reason: row.kind === 'because_you_watched' ? row.title : null });
    }
  }
  return slides;
}

export type HeroArt = { kind: 'backdrop'; art: TitleArt } | { kind: 'poster'; art: TitleArt } | { kind: 'field' };

/** Art wide enough to fill the hero; unanalysed art (no size yet) is trusted to be what its slot says. */
const wide = (art: TitleArt, slotIsWide = true): boolean => (art.width && art.height ? art.width >= art.height * 1.2 : slotIsWide);
const fit = (art: TitleArt, slotIsWide: boolean): HeroArt => (wide(art, slotIsWide) ? { kind: 'backdrop', art } : { kind: 'poster', art });

/**
 * With the anchor's detail, its backdrop, else an episode's own still, else the anchor's poster, else the
 * colour field. Portrait art (a poster filed as a backdrop, a show poster standing in for a still) is composed as a
 * poster, never stretched across the hero. Before the detail arrives (or when it fails), the summary's own wide
 * backdrop or the field.
 */
export function heroArt(title: TitleSummary, detail: TitleDetail | null): HeroArt {
  if (!detail) return title.type !== 'episode' && title.backdrop && wide(title.backdrop) ? { kind: 'backdrop', art: title.backdrop } : { kind: 'field' };
  if (detail.backdrop && wide(detail.backdrop)) return { kind: 'backdrop', art: detail.backdrop };
  if (title.type === 'episode' && title.poster) return fit(title.poster, true);
  if (detail.poster) return fit(detail.poster, false);
  return detail.backdrop ? { kind: 'poster', art: detail.backdrop } : { kind: 'field' };
}

/** "28 min left", "1h 12m left", "1h left"; null when not started, finished or of unknown length. */
export function timeLeft(position: number, duration: number | null | undefined): string | null {
  if (!duration || position <= 0 || position >= duration) return null;
  const minutes = Math.max(1, Math.round((duration - position) / 60));
  if (minutes < 60) return `${minutes} min left`;
  return minutes % 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m left` : `${minutes / 60}h left`;
}

/** A Continue entry's gold bar (foundation 1.6): its own position; null when finished or unstarted. */
export function entryPercent(entry: PlaybackProgress): number | null {
  const duration = entry.duration_seconds ?? entry.item.duration ?? null;
  if (entry.completed || entry.position_seconds <= 0 || !duration) return null;
  return Math.min(100, Math.max(4, (entry.position_seconds / duration) * 100));
}

export const entryLeft = (entry: PlaybackProgress): string | null =>
  timeLeft(entry.position_seconds, entry.duration_seconds ?? entry.title?.runtime_seconds ?? entry.item.duration);

/** "S2 · E4 · The Channel · 28 min left" for an episode, "1h 12m left" for a movie, "year · runtime · genres" for the newest. */
export function heroMeta(pick: HeroPick): string {
  const { title } = pick;
  if (pick.kind === 'continue') {
    const left = entryLeft(pick.entry);
    return (title.type === 'episode' ? [episodeCode(title), title.name, left] : [left]).filter(Boolean).join(' · ');
  }
  return [title.year ? String(title.year) : null, formatRuntime(title.runtime_seconds), ...title.genres.slice(0, 3)].filter(Boolean).join(' · ');
}

export type Caption = { name: string; line: string };

/** A 16:9 title card: line 1 the series (else the title), line 2 the episode code and time left. */
export function titleCaption(title: TitleSummary, left: string | null = null): Caption {
  const code = title.type === 'episode' ? episodeCode(title) : '';
  return { name: (title.type === 'episode' && title.series_name) || title.name, line: [code, left].filter(Boolean).join(' · ') };
}

/** The card's accessible name is its caption: "Harbor Lights, S2 · E4 · 28 min left". */
export const captionLabel = ({ name, line }: Caption): string => (line ? `${name}, ${line}` : name);
