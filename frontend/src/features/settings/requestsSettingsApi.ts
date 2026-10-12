import { del, post, put, requestJson } from '../../api';

/** Admin + member API for Requests settings. */
export type RequestKind = 'movie' | 'show' | 'anime';
export type ArrKind = 'sonarr' | 'radarr';
export type PathMapping = { remote: string; local: string };

export type ArrServer = {
  id: string; kind: ArrKind; name: string; base_url: string;
  /** The key is write-only; the server only says whether one is saved. */
  api_key_set?: boolean; has_api_key?: boolean;
  root_folder: string | null; quality_profile_id: number | null;
  anime_root_folder?: string | null; anime_quality_profile_id?: number | null;
  dub_profile_id?: number | null; sub_profile_id?: number | null;
  path_mappings: PathMapping[]; enabled: boolean; last_ok_at: string | null; last_error: string | null;
};
export type ArrServerInput = Partial<Omit<ArrServer, 'id' | 'api_key_set' | 'has_api_key' | 'last_ok_at' | 'last_error'>> & { kind?: ArrKind; api_key?: string };
export type ArrTest = { ok: boolean; version?: string; root_folders: { path: string; free_space?: number }[]; quality_profiles: { id: number; name: string }[]; error?: string };

export type SmtpSecurity = 'starttls' | 'ssl' | 'none';
export type RequestsSettings = { requests_enabled: boolean; smtp: { host: string | null; port: number | null; security: SmtpSecurity; username: string | null; from: string | null; password_set: boolean } };
export type RequestsSettingsInput = { requests_enabled: boolean; smtp: Omit<RequestsSettings['smtp'], 'password_set'> & { password?: string } };

export type Policy = { kind: RequestKind; can_request: boolean; auto_approve: boolean; quota_count: number | null; quota_days: number | null };
export type PoliciesResponse = { defaults: Policy[]; members: { user: { id: string; name: string; role: string }; overrides: Policy[] }[] };
export type NotificationPrefs = { email: string | null; enabled: boolean };

const base = '/api/admin/requests';
export const listArrServers = (): Promise<ArrServer[]> => requestJson<ArrServer[] | { servers: ArrServer[] }>(`${base}/servers`).then((body) => (Array.isArray(body) ? body : body.servers));
export const createArrServer = (input: ArrServerInput): Promise<ArrServer> => post(`${base}/servers`, input);
export const updateArrServer = (id: string, input: ArrServerInput): Promise<ArrServer> => put(`${base}/servers/${encodeURIComponent(id)}`, input);
export const deleteArrServer = (id: string): Promise<void> => del(`${base}/servers/${encodeURIComponent(id)}`);
export const testArrServer = (input: { kind: ArrKind; base_url: string; api_key?: string; id?: string }): Promise<ArrTest> => post(`${base}/servers/test`, input, { timeoutMs: 30_000 });
export const createAnimeLanguageProfiles = (id: string): Promise<ArrServer> => post(`${base}/servers/${encodeURIComponent(id)}/anime-language-profiles`, undefined, { timeoutMs: 60_000 });
export const getRequestsSettings = (): Promise<RequestsSettings> => requestJson(`${base}/settings`);
export const updateRequestsSettings = (input: RequestsSettingsInput): Promise<RequestsSettings> => put(`${base}/settings`, input);
export const sendSmtpTest = (to: string): Promise<{ ok?: boolean; error?: string } | void> => post(`${base}/smtp/test`, { to }, { timeoutMs: 30_000 });
export const getRequestPolicies = (): Promise<PoliciesResponse> => requestJson(`${base}/policies`);
export const putRequestPolicies = (userId: string | null, policies: Policy[]): Promise<unknown> => put(`${base}/policies`, { user_id: userId, policies });
export const resetMemberPolicies = (userId: string): Promise<void> => del(`${base}/policies/${encodeURIComponent(userId)}`);
export const getNotificationPrefs = (): Promise<NotificationPrefs> => requestJson('/api/requests/notifications');
export const putNotificationPrefs = (input: NotificationPrefs): Promise<NotificationPrefs> => put('/api/requests/notifications', input);
