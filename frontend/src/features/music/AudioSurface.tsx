import { useState } from 'react';
import { ChevronDown } from '../../luminaIcons';
import type { LibraryItem, TitleSummary } from '../../types';
import { Button, Menu } from '../../ui';
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
  musicBrowseView?: 'albums' | 'artists';
  onMusicBrowseViewChange?: (view: 'albums' | 'artists') => void;
}

export function AudioSurface({ user, wall, onWallChange, onOpenTitle, onPlay, onItemChanged, musicBrowseView, onMusicBrowseViewChange }: AudioSurfaceProps) {
  const { sections, refresh: refreshSections } = useLibrarySections(user.id);
  const [collectionOpen, setCollectionOpen] = useState(false);
  const canEdit = user.can_edit_details ?? user.role === 'admin';
  const requestedPane = new URLSearchParams(wall).get('pane') ?? 'music';
  const pane = requestedPane === 'manage' && !canEdit ? 'music' : requestedPane;
  const musicState = requestedPane === 'manage' && !canEdit ? '' : wall;
  const destinations = ([['music', 'Music'], ['playlists', 'Playlists & mixes'], ...(canEdit ? [['manage', 'Manage music'] as const] : [])] as const);
  const navigation = <nav aria-label="Audio" className="audio-local-nav">{destinations.map(([value, label]) => <a key={value} aria-current={pane === value ? 'page' : undefined} href={value === 'music' ? '/audio' : `/audio?pane=${value}`} onClick={(event) => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); onWallChange(value === 'music' ? '' : `pane=${value}`); } }}>{label}</a>)}<a className="audio-offline-link" href="/music-offline.html">Offline listening</a></nav>;
  const tools = <Menu align="end" label="Music tools" items={[
    ...destinations.map(([value, label]) => ({ kind: 'item' as const, label, onSelect: () => onWallChange(value === 'music' ? '' : `pane=${value}`) })),
    { kind: 'item', label: 'Offline listening', href: '/music-offline.html' },
  ]} trigger={(props) => <Button {...props} variant="quiet">Music tools<ChevronDown aria-hidden="true" /></Button>} />;
  return <div className="audio-space">{pane === 'music' ? null : navigation}
    {pane === 'manage' ? <MusicCuration user={user} onOpenTitle={onOpenTitle} /> : pane === 'playlists' ? <MusicPlaylists owner={user.id} /> : <><MusicTab tools={tools} canDelete={(item) => user.role === 'admin' || item.user_id === user.id} onBrowseViewChange={onMusicBrowseViewChange} onItemChanged={(item) => { refreshSections(); onItemChanged(item); }} onOpen={onOpenTitle} onPlay={onPlay} onWallChange={onWallChange} preferredView={musicBrowseView} sections={sections} state={musicState} /><details className="audio-collection-disclosure" onToggle={(event) => setCollectionOpen(event.currentTarget.open)}><summary><span>From your collection</span><span className="g-label">Artist notes and a wider sampler</span></summary>{collectionOpen ? <AudioSoundstage onOpenArtist={onOpenTitle} owner={user.id} /> : null}</details></>}
  </div>;
}
