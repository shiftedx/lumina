/**
 * The one hover menu on cover art: a ⋯ that fades in on the art's corner (hover or keyboard focus within the card) and
 * opens the shared Menu. What it offers follows the subject and the member: a library title (play, watched, favorite,
 * watchlist, edit details, change artwork), a web video (play, queue, save to the vault, open channel), a saved video,
 * and, inside a recommendation card, the reason as a quiet header with Not interested and the rest. The card's host
 * (PosterCard, AlbumCard, StillFrame, RemoteStillCard) renders it beside its button, never inside it.
 */
import { useCanDownload } from '../access/access';
import { MoreHorizontal } from 'lucide-react';
import { createContext, type ReactNode, useContext, useState } from 'react';

import { setFavorite, setTitleWatched } from '../../api';
import type { LibraryItem, RemoteEntry, TitleSummary } from '../../types';
import { Menu, type MenuEntry, useToast } from '../../ui';
import { youtubeChannelId } from '../channels/channelMention';
import { useRecoFeedback, useRecoArt } from '../reco/recoFeedback';
import { recoMenuItems } from '../reco/recoModel';
import { useRecoPersonal } from '../reco/recoPersonal';
import { useRecoImpressionRef } from '../reco/useRecoImpressions';
import { queueMedia, remoteQueueRef } from '../watch/WatchQueue';
import './artMenu.css';
import { liveBadgeState } from './LiveBadge';
import { forgetTitle } from './titleCache';

export type ArtSubject = { kind: 'title'; title: TitleSummary } | { kind: 'remote'; item: RemoteEntry } | { kind: 'library'; item: LibraryItem };

/** What the app lets a card do; mounted once by LuminaApp. Without it only the recommendation and queue entries show. */
export type ArtActions = {
  user: { role: string; can_edit_details?: boolean } | null;
  navigate: (path: string) => void;
  playLibrary: (libraryId: string) => void;
  openRemote: (item: RemoteEntry) => void;
  saveRemote: (item: RemoteEntry) => void;
};
const ActionsContext = createContext<ArtActions | null>(null);
export const ArtActionsProvider = ActionsContext.Provider;

const EDITABLE = ['movie', 'series', 'season', 'episode'];
const subjectName = (subject: ArtSubject): string => (subject.kind === 'title' ? subject.title.name : subject.item.title || 'video');
const item = (label: string, onSelect: () => void, extra?: Partial<MenuEntry>): MenuEntry => ({ kind: 'item', label, onSelect, ...extra }) as MenuEntry;
const block = (...parts: MenuEntry[][]): MenuEntry[] => parts.filter((part) => part.length).flatMap((part, index) => (index ? [{ kind: 'separator' } as MenuEntry, ...part] : part));

export function ArtMenu({ subject, extra = [], label }: { subject: ArtSubject; /** Entries the card's surface adds (Delete, Add to queue). */ extra?: MenuEntry[]; label?: string }) {
  const canDownload = useCanDownload();
  const actions = useContext(ActionsContext);
  const toast = useToast();
  const art = useRecoArt();
  const feedback = useRecoFeedback();
  const personal = useRecoPersonal();
  const observe = useRecoImpressionRef(art?.reco);
  // A change made here shows in the menu at once; the lists behind refetch on their own schedule.
  const [seen, setSeen] = useState<{ played?: boolean; favorite?: boolean }>({});

  const guarded = (work: () => Promise<void>, failure: string) => () => { work().catch(() => toast({ tone: 'error', message: failure })); };
  const enqueue = async (ref: Parameters<typeof queueMedia>[0], done: string): Promise<void> => { toast(await queueMedia(ref, 'end') ? { tone: 'success', message: done } : { tone: 'error', message: 'Could not update your queue' }); };

  const play: MenuEntry[] = [];
  const own: MenuEntry[] = [];
  if (subject.kind === 'title' && actions) {
    const { title } = subject;
    const played = seen.played ?? title.user_data.played;
    const favorite = seen.favorite ?? title.user_data.is_favorite;
    if (title.play_item_id) play.push(item(!played && title.user_data.position_seconds > 0 ? 'Resume' : 'Play', () => actions.playLibrary(title.play_item_id!)));
    if (title.play_item_id && EDITABLE.includes(title.type)) play.push(item('Add to watchlist', guarded(() => enqueue({ kind: 'library', library_item_id: title.play_item_id! }, 'Added to your watchlist'), 'Could not update your watchlist')));
    if (EDITABLE.includes(title.type)) play.push(item(played ? 'Mark unwatched' : 'Mark watched', guarded(async () => { await setTitleWatched(title.id, !played); forgetTitle(title.id); setSeen((current) => ({ ...current, played: !played })); }, 'Could not update watched state')));
    play.push(item(favorite ? 'Remove from favorites' : 'Favorite', guarded(async () => { await setFavorite(title.id, !favorite); forgetTitle(title.id); setSeen((current) => ({ ...current, favorite: !favorite })); }, 'Could not update your favorites')));
    if (actions.user?.can_edit_details && EDITABLE.includes(title.type)) {
      const path = `/title/${encodeURIComponent(title.id)}/edit`;
      own.push(item('Edit details', () => actions.navigate(path)), item('Change artwork', () => actions.navigate(`${path}?tab=artwork`)));
    }
  } else if (subject.kind === 'remote') {
    const entry = subject.item;
    const live = Boolean(liveBadgeState(entry.capabilities?.lifecycle, false));
    const channel = youtubeChannelId(entry);
    if (actions) play.push(item('Play', () => actions.openRemote(entry)));
    const ref = remoteQueueRef(entry);
    if (ref) play.push(item('Add to queue', guarded(() => enqueue(ref, 'Added to your queue'), 'Could not update your queue')));
    if (actions && canDownload && !live && entry.webpage_url) play.push(item('Save to vault', () => actions.saveRemote(entry)));
    if (actions && channel) own.push(item('Open channel', () => actions.navigate(`/channel/youtube/${channel}`)));
  } else if (subject.kind === 'library' && actions) {
    play.push(item('Play', () => actions.playLibrary(subject.item.id)));
  }

  const reco: MenuEntry[] = art && feedback
    ? recoMenuItems(art.target, false, personal).filter((entry) => entry.id !== 'why' && entry.id !== 'queue').map((entry) => item(entry.label, () => { void feedback.feedback(entry.id as 'not_interested' | 'fewer' | 'hide_channel', art.target, art.reco, art.rail); }))
    : [];
  const entries = block(play, extra, own, reco);
  if (!entries.length) return null;
  const items: MenuEntry[] = art?.reco ? [{ kind: 'group', label: art.reco.reason }, ...entries] : entries;
  const name = label ?? subjectName(subject);
  return (
    <span className="g-art-menu" ref={observe}>
      <Menu align="start" items={items} trigger={(props) => <button {...props} aria-label={`More options for ${name}`} className="g-art-menu-button" type="button"><MoreHorizontal aria-hidden="true" /></button>} />
    </span>
  );
}

/** A card whose root is a button: the host puts the menu beside it, inside one positioned box. */
export function ArtHost({ children, hasBadge = false }: { children: ReactNode; /** A LIVE badge holds the top-left corner. */ hasBadge?: boolean }) {
  return <div className={`g-art-host${hasBadge ? ' has-badge' : ''}`}>{children}</div>;
}
