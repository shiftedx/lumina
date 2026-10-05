import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { sectionById, SETTINGS_SECTIONS, searchSettings, visibleSections } from './registry';
import { SectionRows } from './SettingRow';
import { SettingsHostContext } from './settingsHost';

const SERVER = { id: 's1', kind: 'sonarr', name: 'Sonarr', base_url: 'http://sonarr.lan', api_key_set: true, root_folder: '/tv/Shows/', quality_profile_id: 1, anime_root_folder: '/tv/Anime/', anime_quality_profile_id: 1, dub_profile_id: null, sub_profile_id: null, path_mappings: [{ remote: '/tv', local: '/media/tv' }], enabled: true, last_ok_at: null, last_error: 'boom' };
const TEST = { ok: true, version: '4.0.1', root_folders: [{ path: '/tv/Shows/' }, { path: '/tv/Anime/' }], quality_profiles: [{ id: 1, name: 'Any' }, { id: 7, name: 'HD-1080p' }] };
const POLICIES = { defaults: [{ kind: 'movie', can_request: true, auto_approve: false, quota_count: 10, quota_days: 7 }, { kind: 'show', can_request: true, auto_approve: false, quota_count: 10, quota_days: 7 }, { kind: 'anime', can_request: true, auto_approve: false, quota_count: 10, quota_days: 7 }], members: [{ user: { id: 'u2', name: 'Sam', role: 'viewer' }, overrides: [] }] };

let calls: { method: string; url: string; body: unknown }[] = [];
let servers: unknown[] = [SERVER];
const reply = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
beforeEach(() => {
  calls = [];
  servers = [SERVER];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith('/servers/test')) return reply(TEST);
    if (url.endsWith('/servers')) return reply(method === 'GET' ? servers : SERVER);
    if (url.includes('/servers/')) return reply(SERVER);
    if (url.endsWith('/settings')) return reply({ requests_enabled: true, smtp: { host: 'mail.lan', port: 587, security: 'starttls', username: 'u', from: 'l@x.io', password_set: true } });
    if (url.endsWith('/policies')) return reply(method === 'GET' ? POLICIES : {});
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

const user = { id: 'admin', username: 'owner', role: 'admin', is_active: true } as never;
const renderSection = () => render(<SettingsHostContext.Provider value={{ user, onMessage: vi.fn() }}><SectionRows section={sectionById('requests')!} /></SettingsHostContext.Provider>);
const sent = (method: string, suffix: string) => calls.filter((call) => call.method === method && call.url.endsWith(suffix));

describe('Requests settings', () => {
  it('is a server section for admins only, after Media server', () => {
    const ids = (role: string) => visibleSections({ role } as never).map((section) => section.id);
    expect(ids('admin')).toContain('requests');
    expect(ids('viewer')).not.toContain('requests');
    const server = SETTINGS_SECTIONS.filter((section) => section.group === 'server').map((section) => section.id);
    expect(server[server.indexOf('media') + 1]).toBe('requests');
  });

  it.each(['sonarr', 'radarr', 'smtp', 'dub', 'quota', 'anime', 'approval', 'email'])('settings search finds %s', (query) => {
    expect(searchSettings(query, SETTINGS_SECTIONS).map((match) => match.section.id)).toContain('requests');
  });

  it('test connection fills the root folder and profile selects, and the saved key is never echoed', async () => {
    renderSection();
    const key = await screen.findByLabelText(/^Sonarr API key/);
    expect((key as HTMLInputElement).value).toBe('');
    expect((key as HTMLInputElement).placeholder).toBe('Saved key hidden');
    await userEvent.click(screen.getByRole('button', { name: 'Test Sonarr connection' }));
    await screen.findByText('Connected to Sonarr 4.0.1.');
    const profile = screen.getByLabelText('Sonarr quality profile') as HTMLSelectElement;
    await waitFor(() => expect(within(profile).getByRole('option', { name: 'HD-1080p' })).toBeTruthy());
    expect(within(screen.getByLabelText('Sonarr root folder')).getByRole('option', { name: '/tv/Anime/' })).toBeTruthy();
    expect(document.body.innerHTML).not.toContain('api_key');
  });

  it('saves an edited server with only a typed key and the path mappings', async () => {
    renderSection();
    await screen.findByLabelText(/^Sonarr API key/);
    await userEvent.type(screen.getByLabelText(/^Sonarr API key/), 'k3y');
    await userEvent.click(screen.getByRole('button', { name: 'Save Sonarr' }));
    await waitFor(() => expect(sent('PUT', '/servers/s1')).toHaveLength(1));
    expect(sent('PUT', '/servers/s1')[0].body).toMatchObject({ kind: 'sonarr', base_url: 'http://sonarr.lan', api_key: 'k3y', path_mappings: [{ remote: '/tv', local: '/media/tv' }] });
  });

  it('prefills Radarr with the /movies path mapping when nothing is saved', async () => {
    renderSection();
    expect(await screen.findByLabelText('Radarr path 1')).toHaveProperty('value', '/movies');
    expect(screen.getByLabelText('Radarr path 1 in Lumina')).toHaveProperty('value', '/media/movies');
  });

  it('edits household policies into the right PUT payload', async () => {
    renderSection();
    const table = (await screen.findAllByRole('table', { name: 'Household defaults' }))[0];
    await userEvent.click(within(table).getByRole('checkbox', { name: 'Anime: Auto-approve' }));
    await userEvent.click(within(table).getAllByRole('checkbox', { name: 'Unlimited' })[0]);
    await userEvent.click(screen.getByRole('button', { name: 'Save household defaults' }));
    await waitFor(() => expect(sent('PUT', '/policies')).toHaveLength(1));
    expect(sent('PUT', '/policies')[0].body).toEqual({
      user_id: null,
      policies: [
        { kind: 'movie', can_request: true, auto_approve: false, quota_count: null, quota_days: null },
        { kind: 'show', can_request: true, auto_approve: false, quota_count: 10, quota_days: 7 },
        { kind: 'anime', can_request: true, auto_approve: true, quota_count: 10, quota_days: 7 },
      ],
    });
  });

  it('saves a member override from the edit dialog', async () => {
    renderSection();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Sam' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('checkbox', { name: 'Shows: May request' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save policies' }));
    await waitFor(() => expect(sent('PUT', '/policies')).toHaveLength(1));
    expect(sent('PUT', '/policies')[0].body).toMatchObject({ user_id: 'u2', policies: [{ kind: 'movie' }, { kind: 'show', can_request: false }, { kind: 'anime' }] });
  });

  it('never sends the SMTP password unless typed', async () => {
    renderSection();
    await userEvent.type(await screen.findByLabelText('SMTP host'), '2');
    await userEvent.click(screen.getByRole('button', { name: 'Save email settings' }));
    await waitFor(() => expect(sent('PUT', '/settings')).toHaveLength(1));
    const body = sent('PUT', '/settings')[0].body as { smtp: Record<string, unknown> };
    expect(body.smtp.host).toBe('mail.lan2');
    expect('password' in body.smtp).toBe(false);
  });

  it('renders the real backend\'s null payloads and wrapper without crashing', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url.endsWith('/servers')) return reply({ servers: [] });
      if (url.endsWith('/settings')) return reply({ requests_enabled: true, smtp: { host: null, port: null, security: 'starttls', username: null, from: null, password_set: false } });
      if (url.endsWith('/policies')) return reply(POLICIES);
      return reply({});
    });
    renderSection();
    expect(await screen.findByLabelText('SMTP host')).toHaveProperty('value', '');
    expect(screen.getByLabelText('SMTP port')).toHaveProperty('value', '');
    await userEvent.type(screen.getByLabelText('SMTP host'), 'mail.lan');
    await userEvent.click(screen.getByRole('button', { name: 'Save email settings' }));
    await waitFor(() => expect(sent('PUT', '/settings')).toHaveLength(1));
    expect(sent('PUT', '/settings')[0].body).toEqual({ requests_enabled: true, smtp: { host: 'mail.lan', port: null, security: 'starttls', username: null, from: null } });
  });

  it('asks for the key again when the address changed (api_key_required)', async () => {
    renderSection();
    const address = await screen.findByLabelText('Sonarr address');
    await userEvent.clear(address);
    await userEvent.type(address, 'http://other.lan');
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async () => new Response(JSON.stringify({ detail: 'api_key_required' }), { status: 400, headers: { 'Content-Type': 'application/json' } }));
    await userEvent.click(screen.getByRole('button', { name: 'Test Sonarr connection' }));
    expect(await screen.findByText('Enter the API key again for the new address')).toBeTruthy();
    expect(screen.getByLabelText(/^Sonarr API key/).getAttribute('aria-invalid')).toBe('true');
  });
});
