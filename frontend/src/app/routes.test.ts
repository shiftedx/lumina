import { describe, expect, it } from 'vitest';

import { LIBRARY_LENSES } from '../features/gallery/libraryLens';
import { libraryBackRoute, parseRoute, routeFocusKey, routePath, SETTINGS_SECTION_IDS, type AppRoute } from './routes';

describe('app routes', () => {
  it('round-trips every canonical route', () => {
    const routes: AppRoute[] = [
      { surface: 'home' }, { surface: 'library' }, { surface: 'settings' },
      { surface: 'streaming', view: 'home' }, { surface: 'streaming', view: 'search', query: 'slow tv & trains' },
      ...SETTINGS_SECTION_IDS.map((section): AppRoute => ({ surface: 'settings', section })), { surface: 'settings', section: 'members', memberId: '6f1c2b9e-0d4a-4c1e-9a7b-2f3d4e5f6a7b' },
      { surface: 'subscriptions', channelId: 'follow-1' }, { surface: 'watch', libraryId: 'library-1' }, { surface: 'watch', url: 'https://www.youtube.com/watch?v=abc&t=3' },
      { surface: 'library', view: 'movies' }, { surface: 'library', view: 'shows' },
      { surface: 'library', titleId: 'title-1' }, { surface: 'library', titleId: 'title-1', season: 0 }, { surface: 'library', titleId: 'title-1', season: 2 },
      { surface: 'library', view: 'movies', wall: 'sort=name&unwatched=1&genre=Drama&genre=Sci-Fi' }, { surface: 'watch', libraryId: 'library-1', startSeconds: 0 }, { surface: 'watch', libraryId: 'library-1', startSeconds: 761 },
    ];
    for (const route of routes) {
      const url = new URL(routePath(route), 'http://lumina.test');
      expect(parseRoute(url.pathname, url.search)).toEqual(route);
    }
  });

  it('redirects every old Administration link to its Settings section', () => {
    const cases: Array<[string, string]> = [
      ['/admin', 'overview'], ['/admin/', 'overview'], ['/admin/overview', 'overview'], ['/admin/members', 'members'], ['/admin/tasks', 'tasks'],
      ['/admin/storage', 'library'], ['/admin/imports/', 'library'], ['/admin/media', 'media'], ['/admin/ai', 'ai'], ['/admin/backups', 'backups'],
      ['/admin/diagnostics', 'diagnostics'], ['/admin/nope', 'overview'], ['/admin/constructor', 'overview'], ['/admin/tostring', 'overview'],
      ['/admin/Members', 'members'], ['/ADMIN/Tasks', 'tasks'], ['/admin/toString', 'overview'],
    ];
    for (const [path, section] of cases) expect(parseRoute(path, ''), path).toEqual({ surface: 'settings', section });
    for (const path of ['/settings/nope', '/settings/constructor', '/settings/Playback', '/settings/']) expect(parseRoute(path, ''), path).toEqual({ surface: 'settings' });
  });

  it('opens a member page under Settings → Members with a strictly checked id', () => {
    const page = { surface: 'settings', section: 'members', memberId: 'u-1_A' };
    expect(parseRoute('/settings/members/u-1_A', '')).toEqual(page);
    expect(parseRoute('/settings/members/u-1_A/', '')).toEqual(page);
    expect(routePath(page as AppRoute)).toBe('/settings/members/u-1_A');
    const list = { surface: 'settings', section: 'members' };
    for (const path of ['/settings/members/..%2F..%2Fapi', '/settings/members/a%2Fb', '/settings/members/%E0%A4%A', `/settings/members/${'a'.repeat(65)}`, '/settings/members/a b', '/settings/members/<x>']) expect(parseRoute(path, ''), path).toEqual(list);
    expect(parseRoute('/settings/members/a/b', '')).toEqual({ surface: 'home' });
    expect(parseRoute('/settings/playback/u1', '')).toEqual({ surface: 'home' });
    expect(routeFocusKey(parseRoute('/settings/members/u1', ''))).toBe(routeFocusKey(parseRoute('/settings/members', '')));
  });

  it('resolves unknown, malformed, huge or unsafe references to Home', () => {
    const home = { surface: 'home' };
    expect(parseRoute('/nope', '')).toEqual(home);
    expect(parseRoute('/watch', '?url=javascript:alert(1)')).toEqual(home);
    expect(parseRoute('/watch', '?url=file:///etc/passwd')).toEqual(home);
    expect(parseRoute('/watch', `?url=${encodeURIComponent(`https://x.test/${'a'.repeat(3000)}`)}`)).toEqual(home);
    expect(parseRoute('/watch/library/..%2F..%2Fapi', '')).toEqual(home);
    expect(parseRoute('/watch/library/%E0%A4%A', '')).toEqual(home);
    expect(parseRoute(`/watch/library/${'a'.repeat(65)}`, '')).toEqual(home);
    expect(parseRoute('/admin/storage/extra', '')).toEqual(home);
    expect(parseRoute('/subscriptions/..%2Fapi', '')).toEqual(home);
    expect(parseRoute('/explore', `?q=${'q'.repeat(500)}`)).toEqual({ surface: 'streaming', view: 'search', query: 'q'.repeat(200) });
    expect(parseRoute('/title/..%2F..%2Fapi', '')).toEqual(home);
    expect(parseRoute('/title/%E0%A4%A', '')).toEqual(home);
    expect(parseRoute(`/title/${'a'.repeat(65)}`, '')).toEqual(home);
    expect(parseRoute('/library/series', '')).toEqual(home);
    for (const season of ['-1', 'abc', '1234', '1.5', '']) {
      expect(parseRoute('/title/t1', `?season=${season}`)).toEqual({ surface: 'library', titleId: 't1' });
    }
  });
});

describe('gallery route parameters', () => {
  it('ignores an invalid or out-of-range watch start time', () => {
    for (const t of ['-1', '86401', '1.5', 'abc', '', '123456']) expect(parseRoute('/watch/library/item-1', `?t=${t}`), t).toEqual({ surface: 'watch', libraryId: 'item-1' });
    expect(parseRoute('/watch/library/item-1', '?t=86400')).toEqual({ surface: 'watch', libraryId: 'item-1', startSeconds: 86400 });
  });

  it('keeps a wall query opaque and drops an oversized one', () => {
    expect(parseRoute('/library/shows', '?sort=year')).toEqual({ surface: 'library', view: 'shows', wall: 'sort=year' });
    expect(parseRoute('/library/shows', `?q=${'a'.repeat(1000)}`)).toEqual({ surface: 'library', view: 'shows' });
    expect(parseRoute('/library/shows', '')).toEqual({ surface: 'library', view: 'shows' });
  });
});

describe('library lenses', () => {
  it('parses and round-trips every lens, with and without a wall', () => {
    for (const view of LIBRARY_LENSES) {
      for (const route of [{ surface: 'library', view }, { surface: 'library', view, wall: 'sort=title' }] as AppRoute[]) {
        const url = new URL(routePath(route), 'http://lumina.test');
        expect(parseRoute(url.pathname, url.search), routePath(route)).toEqual(route);
      }
    }
    expect(parseRoute('/library', '')).toEqual({ surface: 'library' });
  });

  it('sends /music to the Music lens', () => {
    expect(parseRoute('/music', '')).toEqual({ surface: 'library', view: 'music' });
    expect(parseRoute('/music/', '')).toEqual({ surface: 'library', view: 'music' });
    expect(routePath({ surface: 'library', view: 'music' })).toBe('/library/music');
  });

  it('sends an unknown or miscased lens Home', () => {
    for (const path of ['/library/videos', '/library/Anime', '/library/all', '/library/music/x', '/library/deleted/1', '/musics']) expect(parseRoute(path, ''), path).toEqual({ surface: 'home' });
  });

  it('returns a title page to where it was opened, else to its own lens', () => {
    const walls = { movies: 'sort=name', anime: 'genre=Drama' };
    expect(libraryBackRoute('movie', 'movies', 'all', walls)).toEqual({ surface: 'library' });
    expect(libraryBackRoute('series', 'anime', 'anime', walls)).toEqual({ surface: 'library', view: 'anime', wall: 'genre=Drama' });
    expect(libraryBackRoute('album', null, 'music', {})).toEqual({ surface: 'library', view: 'music' });
    expect(libraryBackRoute('movie', 'movies', null, walls)).toEqual({ surface: 'library', view: 'movies', wall: 'sort=name' });
    expect(libraryBackRoute('movie', 'anime', null, walls)).toEqual({ surface: 'library', view: 'anime', wall: 'genre=Drama' });
    expect(libraryBackRoute('episode', 'anime', null, {})).toEqual({ surface: 'library', view: 'anime' });
    expect(libraryBackRoute('season', 'shows', null, {})).toEqual({ surface: 'library', view: 'shows' });
    expect(libraryBackRoute('artist', null, null, {})).toEqual({ surface: 'library', view: 'music' });
    expect(libraryBackRoute(null, null, null, walls)).toEqual({ surface: 'library' });
  });
});

describe('live and YouTube routes', () => {
  const id = 'UCabcdefghijklmnopqrstuv';

  it('round-trips the channel page, the resolver and the rail state', () => {
    const routes: AppRoute[] = [
      { surface: 'subscriptions', youtubeChannelId: id }, { surface: 'subscriptions', youtubeChannelId: id, tab: 'live' },
      { surface: 'subscriptions', youtubeChannelId: id, tab: 'library' },
      { surface: 'subscriptions', channelUrl: 'https://www.youtube.com/@harborfilms' },
      { surface: 'streaming', view: 'live', rail: 'gaming' }, { surface: 'streaming', view: 'home', rail: 'popular-music' },
    ];
    for (const route of routes) {
      const url = new URL(routePath(route), 'http://lumina.test');
      expect(parseRoute(url.pathname, url.search)).toEqual(route);
    }
    expect(routePath({ surface: 'subscriptions', youtubeChannelId: id, tab: 'videos' })).toBe(`/channel/youtube/${id}`);
    expect(parseRoute(`/channel/youtube/${id}`, '?tab=videos')).toEqual({ surface: 'subscriptions', youtubeChannelId: id });
    expect(parseRoute(`/channel/youtube/${id}`, '?tab=community')).toEqual({ surface: 'subscriptions', youtubeChannelId: id });
    expect(parseRoute('/live', '?rail=Bad Key')).toEqual({ surface: 'streaming', view: 'live' });
    expect(parseRoute('/explore', '?q=trains&rail=gaming')).toEqual({ surface: 'streaming', view: 'search', query: 'trains' });
  });

  it('sends every malformed channel address Home', () => {
    for (const [path, search] of [
      [`/channel/youtube/${id}x`, ''], ['/channel/youtube/UCshort', ''], [`/channel/youtube/${id}/videos`, ''], ['/channel/twitch/somebody', ''],
      ['/channel', ''], ['/channel', '?url=javascript:alert(1)'], ['/channel', '?url=http://www.youtube.com/@x'], ['/channel', '?url=https://evil.com/@x'],
      ['/channel', `?url=${encodeURIComponent(`https://www.youtube.com/@${'x'.repeat(2040)}`)}`], ['/channel', '?url=https://www.youtube.com/watch?v=abc'],
    ]) expect(parseRoute(path, search), `${path}${search}`).toEqual({ surface: 'home' });
    expect(parseRoute('/channel', '?url=https://m.youtube.com/c/Harbor')).toEqual({ surface: 'subscriptions', channelUrl: 'https://m.youtube.com/c/Harbor' });
  });
});

describe('collection routes', () => {
  it('collection routes parse, validate and round-trip', () => {
    expect(parseRoute('/library/collections', '')).toEqual({ surface: 'library', collections: 'list' });
    expect(parseRoute('/library/collections/', '')).toEqual({ surface: 'library', collections: 'list' });
    expect(parseRoute('/library/collections', '?new=smart')).toEqual({ surface: 'library', collections: 'list', create: 'smart' });
    expect(parseRoute('/library/collections', '?new=bogus')).toEqual({ surface: 'library', collections: 'list' });
    expect(parseRoute('/library/collections/col_1-a', '')).toEqual({ surface: 'library', collections: 'detail', collectionId: 'col_1-a' });
    expect(parseRoute(`/library/collections/${'x'.repeat(65)}`, '')).toEqual({ surface: 'library', collections: 'list' });
    expect(parseRoute('/library/collections/%2E%2E', '')).toEqual({ surface: 'library', collections: 'list' });
    expect(parseRoute('/library/collections/a/b', '')).toEqual({ surface: 'home' });
    for (const route of [{ surface: 'library', collections: 'list' }, { surface: 'library', collections: 'list', create: 'collection' }, { surface: 'library', collections: 'detail', collectionId: 'col-1' }] as const) {
      const url = new URL(routePath(route), 'http://x');
      expect(parseRoute(url.pathname, url.search)).toEqual(route);
    }
  });

  it('does not disturb Library Channels, Subscriptions or the lens routes', () => {
    expect(parseRoute('/library/youtube', '?view=channels')).toEqual({ surface: 'library', view: 'youtube', wall: 'view=channels' });
    expect(parseRoute('/library/youtube', '?view=channels&sort=name')).toEqual({ surface: 'library', view: 'youtube', wall: 'view=channels&sort=name' });
    expect(parseRoute('/library/deleted', '')).toEqual({ surface: 'library', view: 'deleted' });
    expect(parseRoute('/subscriptions/c1', '')).toEqual({ surface: 'subscriptions', channelId: 'c1' });
    expect(parseRoute('/library', '')).toEqual({ surface: 'library' });
  });
});

describe('routeFocusKey: a new key means a new page, so focus moves to its heading', () => {
  const key = (path: string) => routeFocusKey(parseRoute(path.split('?')[0], path.includes('?') ? `?${path.split('?')[1]}` : ''));

  it('is the surface for surfaces without sub-pages', () => {
    expect(key('/')).toBe('home');
    expect(key('/downloads')).toBe('downloads');
    expect(key('/explore?q=alpine')).toBe(key('/streaming/live'));
  });

  it('round-trips every Streaming address', () => {
    const routes: AppRoute[] = [
      { surface: 'streaming', view: 'home' }, { surface: 'streaming', view: 'live' }, { surface: 'streaming', view: 'live', rail: 'gaming' },
      { surface: 'streaming', view: 'search', query: 'a b&c' }, { surface: 'streaming', view: 'channels' },
      { surface: 'streaming', view: 'home', provider: 'twitch' }, { surface: 'streaming', view: 'live', provider: 'kick', rail: 'irl' },
      { surface: 'streaming', view: 'search', provider: 'twitch', query: 'speedrun' },
    ];
    for (const route of routes) {
      const url = new URL(routePath(route), 'http://lumina.test');
      expect(parseRoute(url.pathname, url.search)).toEqual(route);
    }
    expect(routePath({ surface: 'streaming', view: 'search', provider: 'youtube', query: 'x' })).toBe('/streaming/search?q=x');
    expect(parseRoute('/streaming', '?provider=bogus')).toEqual({ surface: 'streaming', view: 'home' });
    expect(parseRoute('/streaming/search', '')).toEqual({ surface: 'streaming', view: 'home' });
  });

  it('redirects the old Live, Explore and Subscriptions addresses with their query intact', () => {
    const canonical = (path: string, search = '') => { const r = parseRoute(path, search); const u = routePath(r); return u; };
    expect(canonical('/live')).toBe('/streaming/live');
    expect(canonical('/live', '?rail=x')).toBe('/streaming/live?rail=x');
    expect(canonical('/explore')).toBe('/streaming');
    expect(canonical('/explore', '?q=a b')).toBe('/streaming/search?q=a%20b');
    expect(canonical('/explore', '?rail=popular-gaming')).toBe('/streaming?rail=popular-gaming');
    expect(canonical('/subscriptions')).toBe('/streaming/channels');
    expect(canonical('/subscriptions/c1')).toBe('/subscriptions/c1');
  });

  it('changes when a Subscriptions tile opens a channel page, and between channels', () => {
    const list = key('/subscriptions');
    const channel = key('/channel/youtube/UCabcdefghijklmnopqrstuv');
    expect(channel).not.toBe(list);
    expect(key('/channel/youtube/UCzzzzzzzzzzzzzzzzzzzzzz')).not.toBe(channel);
    expect(key('/subscriptions/f1')).not.toBe(list);
    expect(key('/channel?url=https%3A%2F%2Fwww.youtube.com%2F%40alpine')).not.toBe(list);
  });

  it('does not change for tabs, lenses, walls, rails or Settings sections (focus stays where the member put it)', () => {
    expect(key('/channel/youtube/UCabcdefghijklmnopqrstuv?tab=shorts')).toBe(key('/channel/youtube/UCabcdefghijklmnopqrstuv'));
    expect(key('/library/movies')).toBe(key('/library/shows'));
    expect(key('/library/youtube?view=channels')).toBe(key('/library/youtube'));
    expect(key('/live?rail=now')).toBe(key('/streaming/channels'));
    expect(key('/settings/discovery')).toBe(key('/settings/account'));
  });

  it('changes for a Library title page and between titles', () => {
    expect(key('/title/m1')).not.toBe(key('/library/movies'));
    expect(key('/title/m2')).not.toBe(key('/title/m1'));
    expect(key('/title/m1?season=2')).toBe(key('/title/m1'));
  });

  it('changes between Watch items', () => {
    expect(key('/watch/library/a')).not.toBe(key('/watch/library/b'));
  });

  it('is empty-safe', () => { expect(routeFocusKey(null)).toBe(''); });
});

describe('the editor route (2.1.0)', () => {
  it('parses /title/{id}/edit with and without a tab', () => {
    expect(parseRoute('/title/abc/edit', '')).toEqual({ surface: 'library', titleId: 'abc', edit: true });
    expect(parseRoute('/title/abc/edit', '?tab=artwork')).toEqual({ surface: 'library', titleId: 'abc', edit: true, tab: 'artwork' });
  });
  it('ignores an unknown tab and refuses a malformed id', () => {
    expect(parseRoute('/title/abc/edit', '?tab=nope')).toEqual({ surface: 'library', titleId: 'abc', edit: true });
    expect(parseRoute('/title/%E0%A4%A/edit', '')).toEqual({ surface: 'home' });
    expect(parseRoute('/title/a b/edit', '')).toEqual({ surface: 'home' });
  });
  it('round-trips through routePath', () => {
    expect(routePath({ surface: 'library', titleId: 'abc', edit: true })).toBe('/title/abc/edit');
    expect(routePath({ surface: 'library', titleId: 'abc', edit: true, tab: 'history' })).toBe('/title/abc/edit?tab=history');
  });
  it('gives the editor its own focus key, apart from the title page', () => {
    expect(routeFocusKey({ surface: 'library', titleId: 'abc', edit: true })).toBe('library:edit:abc');
    expect(routeFocusKey({ surface: 'library', titleId: 'abc' })).toBe('library:title:abc');
  });
});

describe('Requests routes (Requests tab UX)', () => {
  it('round-trips every Requests page', () => {
    const routes: AppRoute[] = [
      { surface: 'requests', view: 'discover' }, { surface: 'requests', view: 'movies' }, { surface: 'requests', view: 'shows' },
      { surface: 'requests', view: 'anime' }, { surface: 'requests', view: 'mine' }, { surface: 'requests', view: 'manage' },
      { surface: 'requests', view: 'search', query: 'spirited away & co' },
      { surface: 'requests', view: 'movies', section: 'upcoming' }, { surface: 'requests', view: 'shows', section: 'on_the_air', genre: '18' },
      { surface: 'requests', view: 'anime', section: 'next_season' }, { surface: 'requests', view: 'anime', genre: 'Slice of Life' },
      { surface: 'requests', view: 'title', kind: 'movie', id: 603 }, { surface: 'requests', view: 'title', kind: 'show', id: 1399 }, { surface: 'requests', view: 'title', kind: 'anime', id: 16498 },
    ];
    for (const route of routes) {
      const url = new URL(routePath(route), 'http://lumina.test');
      expect(parseRoute(url.pathname, url.search), routePath(route)).toEqual(route);
    }
    expect(routePath({ surface: 'requests', view: 'discover' })).toBe('/requests');
  });

  it('sends anything unknown or unsafe under /requests to Discover', () => {
    const discover = { surface: 'requests', view: 'discover' };
    for (const path of ['/requests/', '/requests/nope', '/requests/Movies', '/requests/title/movie', '/requests/title/movie/abc', '/requests/title/movie/0', '/requests/title/movie/-1',
      '/requests/title/movie/1.5', '/requests/title/tv/1', '/requests/title/movie/..%2F..%2Fapi', '/requests/title/movie/12345678901', '/requests/title/movie/9999999999', '/requests/title/movie/1/extra', '/requests/constructor']) {
      expect(parseRoute(path, ''), path).toEqual(discover);
    }
    expect(parseRoute('/requests/search', '')).toEqual(discover);
    expect(parseRoute('/requests/search', '?q=%20%20')).toEqual(discover);
    expect(parseRoute('/requests/search', `?q=${'a'.repeat(500)}`)).toEqual({ surface: 'requests', view: 'search', query: 'a'.repeat(200) });
    expect(parseRoute('/requestsx', '')).toEqual({ surface: 'home' });
    expect(parseRoute('/requests/movies', '?section=Upcoming&genre=%3Cscript%3E')).toEqual({ surface: 'requests', view: 'movies' });
    expect(parseRoute('/requests/mine', '?section=upcoming')).toEqual({ surface: 'requests', view: 'mine' });
  });

  it('gives a Requests title page its own focus key; the tabs and search share one', () => {
    expect(routeFocusKey({ surface: 'requests', view: 'movies' })).toBe('requests');
    expect(routeFocusKey({ surface: 'requests', view: 'search', query: 'x' })).toBe('requests');
    expect(routeFocusKey({ surface: 'requests', view: 'title', kind: 'anime', id: 5 })).toBe('requests:title:anime:5');
  });
});
