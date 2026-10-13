export interface MusicTrack {
  id: string;
  title: string;
  artist: string;
  album_artist: string;
  album: string;
  album_id: string | null;
  artist_id: string | null;
  number: number | null;
  disc: number | null;
  duration: number | null;
  artwork_url: string | null;
  audio_url: string;
  codec: string | null;
  sample_rate: number | null;
  bit_depth: number | null;
  channels: number | null;
  gain_db: number | null;
  genres: string[];
  edited: boolean;
  peak?: number | null;
  album_gain_db?: number | null;
  album_peak?: number | null;
  gain_is_manual?: boolean;
}

export interface MusicLyrics {
  plain: string | null;
  cues: { start: number; text: string }[];
  source: string | null;
  instrumental: boolean;
  refreshing?: boolean;
  error?: string | null;
}

export interface MusicVideo {
  id: string;
  title: string;
  url: string;
  thumbnail_url: string | null;
  uploader: string | null;
  duration: number | null;
  performance: 'official' | 'live' | 'cover' | 'remix' | 'performance';
  association: 'recording' | 'artist';
  item_id?: string | null;
}
export interface MusicVideos { items: MusicVideo[]; refreshing: boolean; error: string | null }

export interface MusicTitleInfo {
  release_type: string | null;
  edition: string | null;
  label: string | null;
  catalogue_number: string | null;
  country: string | null;
  original_year: number | null;
  origin: string | null;
  active_since: string | null;
  active_until: string | null;
  styles: string[];
  mood: string[];
  discography: { id: string; name: string; year: string; type: string }[];
  candidates: { id: string; name: string; artist?: string; date?: string; disambiguation?: string }[];
  identified: boolean;
  credits?: { name: string; role: string; instrument?: string | null }[];
  release_group_id?: string | null;
  biography_url?: string | null;
  artwork_credits?: { image_type: 'Primary' | 'Backdrop'; source_url: string; author: string; license: string }[];
}

export interface SonosSpeaker { id: string; name: string; model: string; coordinator_id: string }

export interface MusicPlaylistRules { genres: string[]; artist_id?: string | null; year_from?: number | null; year_to?: number | null; favorites: boolean; unplayed: boolean }
export interface MusicPlaylistSave { name: string; shared: boolean; item_ids: string[]; rules?: MusicPlaylistRules | null; revision?: number }
export interface MusicPlaylist extends MusicPlaylistSave { id: string; count: number | null; can_edit: boolean; revision: number }
