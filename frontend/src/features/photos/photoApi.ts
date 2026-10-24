import { apiBaseUrl, csrfHeaders, del, post, requestJson } from '../../api';

export type PhotoVisibility = 'private' | 'shared';

export interface Photo {
  id: string;
  title: string | null;
  filename: string;
  content_type: string;
  width: number;
  height: number;
  captured_at: string | null;
  added_at: string;
  camera: string | null;
  bytes: number;
  favorite: boolean;
  visibility: PhotoVisibility;
  thumbnail_url: string;
  preview_url: string;
  original_url: string | null;
  deleted_at: string | null;
  album_ids: string[];
  person_ids: string[];
  can_edit: boolean;
}

export interface PhotoPage {
  items: Photo[];
  total: number;
  next_cursor: string | null;
}

export interface PhotoAlbum {
  id: string;
  name: string;
  description: string | null;
  visibility: PhotoVisibility;
  photo_count: number;
  cover_url: string | null;
  created_at: string;
  deleted_at: string | null;
  can_edit: boolean;
}

export interface PhotoPerson {
  id: string;
  name: string | null;
  photo_count: number;
  face_count: number;
  thumbnail_url: string | null;
  hidden: boolean;
  revision: number;
}

export interface PhotoFace {
  id: string;
  person_id: string | null;
  box: { x: number; y: number; width: number; height: number };
  confidence: number;
  thumbnail_url: string | null;
}

export interface FaceSettings {
  enabled: boolean;
  auto_group: boolean;
  consented_at: string | null;
  model: { id: string; state: 'absent' | 'downloading' | 'verifying' | 'ready' | 'failed'; reason: string | null; bytes_done: number; bytes_total: number };
  jobs: Record<string, number>;
  pipeline_signature: string;
}

export type PhotoSourceState = 'queued' | 'scanning' | 'idle' | 'canceling' | 'offline' | 'failed';
export interface PhotoSource {
  id: string;
  root_id: string;
  root_label: string;
  relative_folder: string;
  owner_user_id: string;
  visibility: PhotoVisibility;
  state: PhotoSourceState;
  enabled: boolean;
  indexed_count: number;
  error_code: string | null;
  error_count: number;
  last_scan_at: string | null;
  last_completed_at: string | null;
}
export interface PhotoSourceRoot { id: string; label: string; online: boolean }
export interface PhotoSourcesPage { items: PhotoSource[]; roots: PhotoSourceRoot[] }

export type PhotoQuery = {
  cursor?: string | null;
  limit?: number;
  q?: string;
  favorite?: boolean;
  album_id?: string;
  person_id?: string;
  sort?: 'captured' | 'added';
  deleted?: boolean;
  camera?: string;
  date_from?: string;
  date_to?: string;
};

const enc = encodeURIComponent;

function queryString(query: Record<string, string | number | boolean | null | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== '') params.set(key, String(value)); });
  return params.size ? `?${params}` : '';
}

export const listPhotos = (query: PhotoQuery, signal?: AbortSignal): Promise<PhotoPage> =>
  requestJson(`/api/photos${queryString({ ...query, limit: query.limit ?? 100 })}`, undefined, { signal });

export const updatePhoto = (id: string, changes: Partial<Pick<Photo, 'title' | 'favorite' | 'visibility'>> & { deleted?: boolean }): Promise<Photo> =>
  requestJson(`/api/photos/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(changes) });

export const restorePhoto = (id: string): Promise<Photo> => post(`/api/photos/${enc(id)}/restore`);

export const listPhotoAlbums = (): Promise<{ items: PhotoAlbum[] }> => requestJson('/api/photo-albums');
export const createPhotoAlbum = (payload: Pick<PhotoAlbum, 'name' | 'visibility'> & { description?: string | null }): Promise<PhotoAlbum> => post('/api/photo-albums', payload);
export const updatePhotoAlbum = (id: string, payload: Partial<Pick<PhotoAlbum, 'name' | 'description' | 'visibility'>>): Promise<PhotoAlbum> =>
  requestJson(`/api/photo-albums/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
export const deletePhotoAlbum = (id: string): Promise<void> => del(`/api/photo-albums/${enc(id)}`);
export const addPhotoToAlbum = (albumId: string, photoId: string): Promise<void> => requestJson(`/api/photo-albums/${enc(albumId)}/photos/${enc(photoId)}`, { method: 'PUT' });
export const removePhotoFromAlbum = (albumId: string, photoId: string): Promise<void> => del(`/api/photo-albums/${enc(albumId)}/photos/${enc(photoId)}`);

export const listPhotoPeople = (includeHidden = false, signal?: AbortSignal, offset = 0, limit = 100): Promise<{ items: PhotoPerson[]; next_offset: number | null }> =>
  requestJson(`/api/photo-people${queryString({ include_hidden: includeHidden || undefined, offset, limit })}`, undefined, { signal });
export const updatePhotoPerson = (id: string, payload: { name?: string | null; hidden?: boolean }): Promise<PhotoPerson> =>
  requestJson(`/api/photo-people/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
export const createPhotoPerson = (name: string, faceIds: string[]): Promise<{ person: PhotoPerson }> => post('/api/photo-people', { name, face_ids: faceIds });
export const getFaceSettings = (signal?: AbortSignal): Promise<FaceSettings> => requestJson('/api/photo-faces/settings', undefined, { signal });
export const updateFaceSettings = (payload: { enabled: boolean; auto_group: boolean; acknowledge_biometric_processing?: boolean }): Promise<FaceSettings> =>
  requestJson('/api/photo-faces/settings', { method: 'PUT', body: JSON.stringify(payload) });
export const scanPhotoFaces = (): Promise<{ queued: number }> => post('/api/photo-faces/scan');
export const deletePhotoFaceData = (): Promise<void> => del('/api/photo-faces/data');
export const mergePhotoPeople = (sourceId: string, targetPersonId: string): Promise<PhotoPerson> => post(`/api/photo-people/${enc(sourceId)}/merge`, { target_person_id: targetPersonId });
export const splitPhotoPerson = (personId: string, faceIds: string[]): Promise<{ person: PhotoPerson }> => post(`/api/photo-people/${enc(personId)}/split`, { face_ids: faceIds });
export const listPhotoFaces = (photoId: string, signal?: AbortSignal): Promise<{ items: PhotoFace[] }> => requestJson(`/api/photos/${enc(photoId)}/faces`, undefined, { signal });
export const assignPhotoFace = (faceId: string, personId: string | null): Promise<{ id: string; person_id: string | null; assignment_source: string }> =>
  post(`/api/photo-faces/${enc(faceId)}/assign`, { person_id: personId });

const PHOTO_SOURCES = '/api/admin/photo-sources';
export const listPhotoSources = (): Promise<PhotoSourcesPage> => requestJson(PHOTO_SOURCES);
export const createPhotoSource = (payload: { root_id: string; relative_folder: string; visibility: PhotoVisibility }): Promise<PhotoSource> => post(PHOTO_SOURCES, payload);
export const scanPhotoSource = (id: string): Promise<PhotoSource> => post(`${PHOTO_SOURCES}/${enc(id)}/scan`);
export const cancelPhotoSource = (id: string): Promise<PhotoSource> => post(`${PHOTO_SOURCES}/${enc(id)}/cancel`);
export const deletePhotoSource = (id: string): Promise<void> => del(`${PHOTO_SOURCES}/${enc(id)}`);

export type UploadState = { loaded: number; total: number; progress: number };

/** One raw-file upload. XHR is intentional: fetch still has no upload-progress event. */
export function uploadPhoto(file: File, onProgress: (state: UploadState) => void): { request: Promise<Photo>; cancel: () => void } {
  const xhr = new XMLHttpRequest();
  const request = new Promise<Photo>((resolve, reject) => {
    xhr.open('PUT', `${apiBaseUrl}/api/photos/import?filename=${enc(file.name)}`);
    xhr.withCredentials = true;
    xhr.timeout = 120_000;
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    Object.entries(csrfHeaders()).forEach(([key, value]) => xhr.setRequestHeader(key, value));
    xhr.upload.onprogress = (event) => onProgress({ loaded: event.loaded, total: event.total || file.size, progress: event.lengthComputable && event.total ? event.loaded / event.total : 0 });
    xhr.onerror = () => reject(new Error('The upload connection was interrupted.'));
    xhr.onabort = () => reject(new DOMException('Upload cancelled.', 'AbortError'));
    xhr.ontimeout = () => reject(new Error('The upload timed out.'));
    xhr.onload = () => {
      let body: unknown;
      try { body = JSON.parse(xhr.responseText || 'null'); } catch { body = null; }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(body as Photo); return; }
      const detail = body && typeof body === 'object' && 'detail' in body ? (body as { detail?: unknown }).detail : null;
      reject(new Error(typeof detail === 'string' ? detail : `Upload failed with ${xhr.status}.`));
    };
    xhr.send(file);
  });
  return { request, cancel: () => xhr.abort() };
}
