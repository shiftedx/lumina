import { describe, expect, it, vi } from 'vitest';
import { EDIT_HOME_EVENT, MEMBER_PICKER_EVENT, onCommand } from '../../app/commands';
import { parseRoute } from '../../app/routes';
import type { UserProfile } from '../../types';
import { filterActions, PALETTE_ACTIONS, PINNED_ACTION_IDS, scanSummary, type PaletteContext } from './actions';
import { ACTION_DESTINATIONS, buildPalette, flatOptions, type PaletteInput } from './paletteModel';

const owner = { id: 'u0', username: 'o', display_name: 'O', role: 'admin', is_active: true } as UserProfile;
const viewer = { id: 'u1', username: 'a', display_name: 'A', role: 'viewer', is_active: true } as UserProfile;
const paletteBase: PaletteInput = { query: '', mode: 'search', user: owner, history: [], local: null, remote: [], channels: [], actions: [] };

const ctx = (patch: Partial<PaletteContext> = {}): PaletteContext => ({
  user: { id: 'u1', username: 'a', display_name: 'A', role: 'viewer', is_active: true } as UserProfile,
  mobile: false, sidebarCollapsed: false, theme: 'system',
  navigate: vi.fn(), setTheme: vi.fn(), toggleSidebar: vi.fn(), signOut: vi.fn(), openLinkMode: vi.fn(),
  ...patch,
});

describe('palette actions', () => {
  it('shows the edit actions only on a title page for someone who can edit', () => {
    const user = { ...viewer, can_edit_details: true } as UserProfile;
    const editor = ctx({ titleId: 't1', titleType: 'movie', user });
    expect(filterActions('edit details', editor).map((a) => a.id)).toContain('edit-title');
    expect(filterActions('artwork', editor).map((a) => a.id)).toContain('edit-title-artwork');
    expect(filterActions('edit details', ctx({ user })).map((a) => a.id)).not.toContain('edit-title');
    expect(filterActions('edit details', ctx({ titleId: 't1' })).map((a) => a.id)).not.toContain('edit-title');
  });
  it('hides the edit actions for types the editor cannot open', () => {
    const user = { ...viewer, can_edit_details: true } as UserProfile;
    for (const titleType of ['boxset', 'album', 'artist', undefined]) expect(filterActions('edit details', ctx({ titleId: 't1', titleType, user })).map((a) => a.id)).not.toContain('edit-title');
  });
  it('runs edit-title and edit-title-artwork against the open title', () => {
    const navigate = vi.fn();
    const editor = ctx({ titleId: 't 1', titleType: 'episode', navigate, user: { ...viewer, can_edit_details: true } as UserProfile });
    PALETTE_ACTIONS.find((a) => a.id === 'edit-title')!.run(editor);
    PALETTE_ACTIONS.find((a) => a.id === 'edit-title-artwork')!.run(editor);
    expect(navigate.mock.calls).toEqual([['/title/t%201/edit'], ['/title/t%201/edit?tab=artwork']]);
  });

  it('has the spec 4.4 ids, labels and pinned five', () => {
    expect(PALETTE_ACTIONS.map((action) => action.id)).toEqual(['add-link', 'go-downloads', 'switch-member', 'theme-system', 'theme-light', 'theme-dark', 'edit-home', 'new-collection', 'new-smart-collection', 'toggle-sidebar', 'settings-playback', 'go-streaming', 'go-live', 'go-subscriptions', 'go-channels', 'settings-discovery', 'edit-title', 'edit-title-artwork', 'scan-libraries', 'settings-library', 'sign-out']);
    expect(filterActions('', ctx()).map((action) => action.id)).toEqual([...PINNED_ACTION_IDS]);
  });

  it('Scan libraries now and Library settings are admin-only; "scan" ranks the scan action first', () => {
    const admin = { role: 'admin' } as UserProfile;
    expect(filterActions('scan', ctx()).map((action) => action.id)).toEqual([]);
    expect(filterActions('scan', ctx({ user: admin })).map((action) => [action.id, action.label])).toEqual([['scan-libraries', 'Scan libraries now']]);
    expect(filterActions('rescan', ctx({ user: admin }))[0]?.id).toBe('scan-libraries');
    expect(filterActions('library', ctx({ user: admin })).map((action) => action.id)).toContain('settings-library');
    expect(filterActions('schedule', ctx({ user: admin })).map((action) => action.id)).toContain('settings-library');
    expect(filterActions('scan', ctx({ user: admin })).map((action) => action.id)).not.toContain('settings-library');
  });

  it('Scan libraries now calls scanLibraries with no root', () => {
    const scanLibraries = vi.fn();
    filterActions('scan', ctx({ user: { role: 'admin' } as UserProfile, scanLibraries }))[0]!.run(ctx({ scanLibraries }));
    expect(scanLibraries).toHaveBeenCalledWith();
  });

  it('per-root scan rows need a query, an admin and the loaded roots', () => {
    const scanRoots = [{ id: 'r1', label: 'TV' }, { id: 'r2', label: 'Movies' }];
    const admin = ctx({ user: { role: 'admin' } as UserProfile, scanRoots, scanLibraries: vi.fn() });
    expect(filterActions('', admin).map((action) => action.id)).toEqual([...PINNED_ACTION_IDS]);
    expect(filterActions('scan', admin).map((action) => [action.id, action.label])).toEqual([['scan-libraries', 'Scan libraries now'], ['scan-root-r1', 'Scan TV now'], ['scan-root-r2', 'Scan Movies now']]);
    expect(filterActions('tv', admin).map((action) => action.id)).toEqual(['scan-root-r1']);
    expect(filterActions('scan', { ...admin, user: { role: 'viewer' } as UserProfile }).map((action) => action.id)).toEqual([]);
    filterActions('tv', admin)[0]!.run(admin);
    expect(admin.scanLibraries).toHaveBeenCalledWith('r1');
    expect(filterActions('scan', admin).every((action) => action.path === undefined)).toBe(true);
  });

  it('summarises a scan result', () => {
    const none = { started: [], queued: [], skipped: [] };
    expect(scanSummary({ ...none, started: [{ root_id: 'a', run_id: 'x' }] })).toBe('Scanning 1 library');
    expect(scanSummary({ ...none, started: [{ root_id: 'a', run_id: 'x' }], queued: ['b'], skipped: [{ root_id: 'c', reason: 'offline' }] })).toBe('Scanning 2 libraries · 1 unavailable');
    expect(scanSummary(none)).toBe('No libraries to scan. Import a folder first.');
    expect(scanSummary({ ...none, skipped: [{ root_id: 'c', reason: 'needs_first_import' }] })).toBe('No libraries to scan. Import a folder first.');
    expect(scanSummary({ ...none, skipped: [{ root_id: 'c', reason: 'scanning' }] })).toBe('Those libraries are already scanning or unavailable.');
  });

  it('one row per destination: the actions are the only rows for their pages', () => {
    const paths = PALETTE_ACTIONS.flatMap((action) => (action.path ? [action.path] : []));
    expect([...ACTION_DESTINATIONS].sort()).toEqual([...paths].sort());
    const context = { user: owner, mobile: false, sidebarCollapsed: false, theme: 'system', navigate: () => undefined, setTheme: () => undefined, toggleSidebar: () => undefined, signOut: () => undefined, openLinkMode: () => undefined } as PaletteContext;
    const queries = ['live', 'explore', 'subscriptions', 'channels', 'saved', 'discovery', 'home', 'downloads', 'playback', 'library', 'settings', 'open', ...PALETTE_ACTIONS.flatMap((action) => action.keywords)];
    for (const user of [owner, viewer]) {
      for (const query of queries) {
        const destinations = flatOptions(buildPalette({ ...paletteBase, user, query, actions: filterActions(query, { ...context, user }) }))
          .flatMap((option) => (option.kind === 'goto' ? [option.path] : option.kind === 'action' && option.action.path ? [option.action.path] : []));
        expect(new Set(destinations).size, `${user.role} "${query}": ${destinations.join(', ')}`).toBe(destinations.length);
      }
    }
    // Go to never offers an action's page, even when no action is passed in.
    for (const query of queries) {
      const goto = flatOptions(buildPalette({ ...paletteBase, query })).flatMap((option) => (option.kind === 'goto' ? [option.path] : []));
      expect(goto.filter((path) => ACTION_DESTINATIONS.has(path)), query).toEqual([]);
    }
  });

  it('the live, YouTube and reco destinations navigate to paths that parse back to their routes', () => {
    const cases: Array<[string, string, string, Record<string, unknown>]> = [
      ['go-streaming', 'Streaming', '/streaming', { surface: 'streaming', view: 'home' }],
      ['go-live', 'Live now', '/streaming/live', { surface: 'streaming', view: 'live' }],
      ['go-subscriptions', 'Your channels', '/streaming/channels', { surface: 'streaming', view: 'channels' }],
      ['go-channels', 'Open saved channels', '/library/youtube?view=channels', { surface: 'library' }],
      ['settings-discovery', 'Home & discovery', '/settings/discovery', { surface: 'settings', section: 'discovery' }],
      ['settings-library', 'Library settings', '/settings/library', { surface: 'settings', section: 'library' }],
    ];
    const context = ctx({ user: { role: 'admin' } as UserProfile });
    for (const [id, label, path, route] of cases) {
      const action = PALETTE_ACTIONS.find((candidate) => candidate.id === id)!;
      expect(action.label).toBe(label);
      action.run(context);
      expect(context.navigate).toHaveBeenLastCalledWith(path);
      const [pathname, search = ''] = path.split('?');
      expect(parseRoute(pathname, search ? `?${search}` : '')).toMatchObject(route);
    }
  });

  it('toggles the sidebar label and hides it on phones', () => {
    expect(filterActions('sidebar', ctx()).map((action) => action.label)).toEqual(['Collapse sidebar']);
    expect(filterActions('sidebar', ctx({ sidebarCollapsed: true })).map((action) => action.label)).toEqual(['Expand sidebar']);
    expect(filterActions('sidebar', ctx({ mobile: true }))).toEqual([]);
  });

  it('marks the current theme and runs each action through the context', () => {
    const context = ctx({ theme: 'dark' });
    const themes = filterActions('theme', context);
    expect(themes.map((action) => [action.label, action.hint ?? null])).toEqual([['Use system theme', null], ['Use light theme', null], ['Use dark theme', 'Current']]);
    themes[1].run(context);
    expect(context.setTheme).toHaveBeenCalledWith('light');
    const picker = vi.fn(); const home = vi.fn();
    const stops = [onCommand(MEMBER_PICKER_EVENT, picker), onCommand(EDIT_HOME_EVENT, home)];
    filterActions('switch', context)[0].run(context);
    filterActions('edit home', context)[0].run(context);
    expect(picker).toHaveBeenCalledOnce();
    expect(home).toHaveBeenCalledOnce();
    expect(context.navigate).toHaveBeenCalledWith('/');
    filterActions('new smart', context)[0].run(context);
    expect(context.navigate).toHaveBeenCalledWith('/library/collections?new=smart');
    stops.forEach((stop) => stop());
  });
});
