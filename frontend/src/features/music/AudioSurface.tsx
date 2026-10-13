import type { LibraryItem, TitleSummary } from '../../types';
import { useLibrarySections } from '../gallery/LibraryTabs';
import { MusicTab } from '../gallery/MusicTab';
import { AudioSoundstage } from './AudioSoundstage';
import { MusicCuration } from './MusicCuration';
import { MusicPlaylists } from './MusicPlaylists';
import './music.css';

export interface AudioSurfaceProps {
  user: { id: string; role: string; can_edit_details?: boolean };
  wall?: string;
  onWallChange: (wall: string) => void;
  onOpenTitle: (title: TitleSummary) => void;
  onPlay: (item: LibraryItem) => void;
  onItemChanged: (item: LibraryItem) => void;
}

export function AudioSurface({ user, wall, onWallChange, onOpenTitle, onPlay, onItemChanged }: AudioSurfaceProps) {
  const { sections } = useLibrarySections(user.id);
  const canEdit = user.can_edit_details ?? user.role === 'admin';
  const requestedPane = new URLSearchParams(wall).get('pane') ?? 'music';
  const pane = requestedPane === 'manage' && !canEdit ? 'music' : requestedPane;
  const musicState = requestedPane === 'manage' && !canEdit ? '' : wall;
  const destinations = ([['music', 'Music'], ['playlists', 'Playlists & mixes'], ...(canEdit ? [['manage', 'Manage music'] as const] : [])] as const);
  return <div className="audio-space"><nav aria-label="Audio" className="audio-local-nav">{destinations.map(([value, label]) => <a key={value} aria-current={pane === value ? 'page' : undefined} href={value === 'music' ? '/audio' : `/audio?pane=${value}`} onClick={(event) => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); onWallChange(value === 'music' ? '' : `pane=${value}`); } }}>{label}</a>)}<a className="audio-offline-link" href="/music-offline.html">Offline listening</a></nav>
    {pane === 'manage' ? <MusicCuration user={user} onOpenTitle={onOpenTitle} /> : pane === 'playlists' ? <MusicPlaylists owner={user.id} /> : <><AudioSoundstage onOpenArtist={onOpenTitle} owner={user.id} /><MusicTab canDelete={(item) => user.role === 'admin' || item.user_id === user.id} onItemChanged={onItemChanged} onOpen={onOpenTitle} onPlay={onPlay} onWallChange={onWallChange} sections={sections} state={musicState} /></>}
  </div>;
}
