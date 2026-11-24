export type * from './contracts/media';
export type * from './contracts/library';
export type * from './contracts/titles';
export type * from './contracts/admin';
export type * from './contracts/automation';

/** e:{stream} embedded text | s:{n} sidecar | t:{transcript id} | i:{stream} image (burned in). */
export const SUBTITLE_TRACK_ID_PATTERN = /^(?:[esi]:\d{1,4}|t:[0-9a-f-]{36})$/;
