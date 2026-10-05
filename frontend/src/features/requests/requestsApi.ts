/** The member side of the Requests API: the catalog and the household's requests. */
import { ApiRequestError, del, post, requestJson } from '../../api';
import type { RequestKind } from '../../app/routes';

export type Kind = RequestKind;
export type RequestState = 'none' | 'pending' | 'approved' | 'processing' | 'partially_available' | 'available' | 'declined' | 'failed';
export type CatalogStatus = { state: RequestState; request_id?: string; progress?: number; library_title_id?: string };
export type AnimeSeason = 'WINTER' | 'SPRING' | 'SUMMER' | 'FALL';
export type AnimeInfo = {
  season?: AnimeSeason; season_year?: number; format?: string; episodes?: number; next_episode?: { number: number; airing_at: string };
  studios: string[]; score?: number; popularity?: number; color?: string;
};
export type CatalogItem = {
  key: string; kind: Kind; tmdb_id?: number; tvdb_id?: number; anilist_id?: number; media_type: 'movie' | 'tv';
  title: string; original_title?: string; year?: number; overview?: string; release_date?: string;
  poster_url?: string; backdrop_url?: string; logo_url?: string; rating?: number; genres: string[];
  anime?: AnimeInfo; status: CatalogStatus; requestable?: boolean; unrequestable_reason?: string;
};
export type Rail = { key: string; title: string; kind?: Kind; see_all?: string; items: CatalogItem[] };
export type Trailer = { youtube_id: string; name: string; type: string; official: boolean };
export type Season = { number: number; name: string; episode_count: number; air_date?: string; poster_url?: string };
export type CatalogDetail = CatalogItem & {
  tagline?: string; runtime?: number; certification?: string; status_text?: string; seasons: Season[]; trailers: Trailer[];
  cast: { name: string; character?: string; profile_url?: string }[]; crew: { name: string; job: string }[]; networks: string[]; studios: string[];
  recommendations: CatalogItem[]; similar: CatalogItem[]; anime_relations?: CatalogItem[];
};
export type CatalogPage = { items: CatalogItem[]; page: number; total_pages: number };
export type ScheduleDay = { date: string; entries: { item: CatalogItem; episode: number; airing_at: string }[] };
export type Member = { id: string; name: string };
export type Language = 'dub' | 'sub';
export type MediaRequest = {
  id: string; kind: Kind; media_type: 'movie' | 'tv'; key: string; tmdb_id?: number; tvdb_id?: number; anilist_id?: number;
  title: string; year?: number; poster_url?: string; seasons: number[] | 'all' | null; language: Language | null;
  status: RequestState; progress?: number; requested_by: Member; followers: Member[]; decided_by?: Member;
  decline_reason?: string; failure_reason?: string; library_title_id?: string; created_at: string; updated_at: string;
};
export type RequestsPage = { items: MediaRequest[]; page: number; total_pages: number; counts: Partial<Record<RequestState, number>> };
export type Quota = { kind: Kind; can_request: boolean; auto_approve: boolean; limit: number | null; days: number | null; used: number; remaining: number | null };
export type NewRequest = { kind: Kind; media_type?: 'movie'; tmdb_id?: number; anilist_id?: number; tvdb_id?: number; seasons?: number[] | 'all'; language?: Language };
export type Amend = { seasons?: number[] | 'all'; language?: Language };

const enc = encodeURIComponent;
const query = (params: Record<string, string | number | undefined | null>) => {
  const pairs = Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '').map(([key, value]) => `${enc(key)}=${enc(String(value))}`);
  return pairs.length ? `?${pairs.join('&')}` : '';
};
const CATALOG = '/api/requests/catalog';

export const getCatalogHome = (signal?: AbortSignal) => requestJson<{ hero: CatalogItem[]; rails: Rail[] }>(`${CATALOG}/home`, undefined, { signal });
export const getCatalogList = (kind: Kind, params: { section?: string; genre?: string; page?: number }, signal?: AbortSignal) =>
  requestJson<CatalogPage>(`${CATALOG}/list/${kind}${query(params)}`, undefined, { signal });
export const getAnimeSeason = (params: { season?: AnimeSeason; year?: number; sort?: 'popularity' | 'score' | 'title'; page?: number }, signal?: AbortSignal) =>
  requestJson<{ season: AnimeSeason; year: number; items: CatalogItem[] }>(`${CATALOG}/anime/season${query(params)}`, undefined, { signal });
export const getAnimeSchedule = (signal?: AbortSignal) => requestJson<{ days: ScheduleDay[] }>(`${CATALOG}/anime/schedule`, undefined, { signal });
export const searchCatalog = (q: string, page = 1, signal?: AbortSignal) => requestJson<CatalogPage>(`${CATALOG}/search${query({ q, page })}`, undefined, { signal });
export const getGenres = (kind: Kind, signal?: AbortSignal) => requestJson<{ genres: { id: string; name: string }[] }>(`${CATALOG}/genres/${kind}`, undefined, { signal });
export const getCatalogTitle = (kind: Kind, id: number, signal?: AbortSignal) => requestJson<CatalogDetail>(`${CATALOG}/title/${kind}/${id}`, undefined, { signal });

export const createRequest = (body: NewRequest) => post<MediaRequest>('/api/requests', body);
export const listRequests = (params: { scope: 'mine' | 'all'; status?: RequestState; page?: number }, signal?: AbortSignal) =>
  requestJson<RequestsPage>(`/api/requests${query(params)}`, undefined, { signal });
export const getQuotas = () => requestJson<{ quotas: Quota[] }>('/api/requests/quota');
export const approveRequest = (id: string, amend?: Amend) => post<MediaRequest>(`/api/requests/${enc(id)}/approve`, amend);
export const declineRequest = (id: string, reason?: string) => post<MediaRequest>(`/api/requests/${enc(id)}/decline`, reason ? { reason } : {});
export const retryRequest = (id: string) => post<MediaRequest>(`/api/requests/${enc(id)}/retry`);
export const cancelRequest = (id: string) => del(`/api/requests/${enc(id)}`);

/** The error envelope's code: `{"detail": "quota_exceeded"}` or `{"detail": {"code": …}}`; '' for anything else. */
export function errorCode(error: unknown): string {
  if (!(error instanceof ApiRequestError)) return '';
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string') return (detail as { code: string }).code;
  return '';
}
