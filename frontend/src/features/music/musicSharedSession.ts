import { requestJson } from '../../api';
import type { MusicRepeatMode } from './musicEngine';
import type { MusicTrack } from './musicTypes';

export type MusicQueueEntry = { kind: 'track'; item_id: string; state?: 'playable'; tracks?: MusicTrack[] }
  | { kind: 'acquisition'; request_id: string; state?: 'playable' | 'pending_approval' | 'requested' | 'downloading' | 'awaiting_import' | 'failed'; name?: string; message?: string; tracks?: MusicTrack[] };

export type MusicSharedSnapshot = {
  revision: number;
  queue: MusicTrack[];
  current_index: number | null;
  position_seconds: number;
  repeat: MusicRepeatMode;
  shuffle: boolean;
  active_output: { kind: 'browser' } | { kind: 'sonos'; ticket_id: string; speaker_id: string; queue_indices: number[] };
  updated_at: string | null;
  removed_count: number;
  entries?: MusicQueueEntry[];
  entry_index?: number | null;
  unavailable_policy?: 'skip' | 'wait';
};

export type MusicSharedWrite = {
  base_revision: number;
  item_ids: string[];
  current_index: number | null;
  position_seconds: number;
  repeat: MusicRepeatMode;
  shuffle: boolean;
  active_output: { kind: 'browser' };
  acknowledged_output_ticket?: string;
  entries?: Array<{ kind: 'track'; item_id: string } | { kind: 'acquisition'; request_id: string }> ;
  entry_index?: number | null;
  unavailable_policy?: 'skip' | 'wait';
};

export const getMusicSharedSession = () => requestJson<MusicSharedSnapshot>('/api/me/music-session');
export const putMusicSharedSession = (body: MusicSharedWrite) => requestJson<MusicSharedSnapshot>('/api/me/music-session', { method: 'PUT', body: JSON.stringify(body) });
