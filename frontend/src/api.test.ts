import { afterEach, describe, expect, it, vi } from 'vitest';

import { releaseBackground, holdBackground } from './backgroundGate';
import { ApiRequestError, bulkEditMetadata, chooseTitleImage, getMetadataHistory, getMetadataVocabulary, listImageCandidates, listMetadataEpisodes, previewMetadataRefresh, removeTitleImage, reorderBackdrops, revertMetadata, searchMetadataPeople, undoMetadataBatch, uploadTitleImage, getTitleMetadata, saveMetadataEdits, forgetDeviceMember, getDeviceMembers, loginSession, switchMember, beaconRecoEvents, clearRecoHistory, getUpNext, sendRecoEvents, suppressRecommendation, getChannelPage, listLibraryChannels, resolveChannel, activateLocalModel, addHouseholdCollectionItem, cancelLocalModelDownload, downloadLocalModel, removeLocalModel, browserSupportedPlaybackProfiles, markHevcFailed, completeOnboarding, confirmImport, createAcquisitionBatch, deleteHouseholdCollection, deleteLibraryNote, dismissNextUp, getChannelSuggestions, getHomeRecommendations, getLibrarySections, getLocalPlaybackOptions, getMemberInterests, getTitleFacets, hideContinueWatching, listJobs, listLibrary, listLibraryNotes, listNextUp, listSeasonEpisodes, listTitles, previewJellyfinImport, previewUrl, refreshLibrary, runJellyfinImport, renameHouseholdCollection, requestSubtitleJob, resolveApiBaseUrl, retryAcquisitionEntry, searchChannels, searchLibrary, searchTitleMatches, setFavorite, skipOnboarding, unhideContinueWatching, updateLibraryNote, updateMemberInterests, updateUser } from './api';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function stubFetch(body: unknown) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('side requests of a Play', () => {
  it('wait for the first frame, while the Play\'s own requests go at once', async () => {
    const fetchMock = stubFetch([]);
    holdBackground();
    try {
      const notes = listLibraryNotes('library-1');
      await getLocalPlaybackOptions('library-1');
      expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([expect.stringContaining('/playback-options')]);
      releaseBackground();
      await notes;
      expect(fetchMock.mock.calls.map(([url]) => String(url))).toContain('/api/library/library-1/notes');
    } finally { releaseBackground(); }
  });
});

describe('paged collection contracts', () => {
  it('requests the first library page without a cursor and parses the page shape', async () => {
    const fetchMock = stubFetch({ items: [{ id: 'library-1', title: 'Saved film' }], next_cursor: 'cursor-2' });

    const page = await listLibrary({ limit: 60 });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/library?limit=60');
    expect(page.items).toEqual([{ id: 'library-1', title: 'Saved film' }]);
    expect(page.next_cursor).toBe('cursor-2');
  });

  it('round-trips an opaque cursor and search term into a single library page request', async () => {
    const fetchMock = stubFetch({ items: [], next_cursor: null });

    const page = await listLibrary({ search: 'jazz', cursor: 'opaque-cursor', limit: 60 });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/library?search=jazz&cursor=opaque-cursor&limit=60');
    expect(page.next_cursor).toBeNull();
  });

  it('requests the bare library route when no paging arguments are given', async () => {
    const fetchMock = stubFetch({ items: [], next_cursor: null });

    await listLibrary();

    expect(fetchMock.mock.calls[0][0]).toBe('/api/library');
  });

  it('adopts the first-page library response returned by an explicit refresh', async () => {
    const fetchMock = stubFetch({ items: [{ id: 'library-9', title: 'Freshly rescanned' }], next_cursor: 'cursor-10' });

    const page = await refreshLibrary();

    expect(fetchMock.mock.calls[0][0]).toBe('/api/library/refresh');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
    expect(page.items).toEqual([{ id: 'library-9', title: 'Freshly rescanned' }]);
    expect(page.next_cursor).toBe('cursor-10');
  });

  it('pages the jobs feed with a cursor and parses its page shape', async () => {
    const fetchMock = stubFetch({ items: [{ id: 'job-1', status: 'running' }], next_cursor: 'jobs-cursor-2' });

    const page = await listJobs({ cursor: 'jobs-cursor-1', limit: 50 });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/jobs?cursor=jobs-cursor-1&limit=50');
    expect(page.items).toEqual([{ id: 'job-1', status: 'running' }]);
    expect(page.next_cursor).toBe('jobs-cursor-2');
  });
});

describe('production API boundary', () => {
  it('uses the authenticated member interest and Home recommendation contracts', async () => {
    const fetchMock = stubFetch({ categories: [], selected_keys: [], items: [], state: 'empty', refreshing: false, stale: false });

    await getMemberInterests();
    await updateMemberInterests(['music']);
    await getHomeRecommendations();

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ['/api/discovery/interests', undefined, undefined],
      ['/api/discovery/interests', 'PUT', JSON.stringify({ keys: ['music'] })],
      ['/api/discovery/home', undefined, undefined],
    ]);
  });

  it('saves and skips onboarding through the durable member-scoped contracts', async () => {
    const fetchMock = stubFetch({ status: 'completed', selected_keys: ['music'] });

    await completeOnboarding(['music'], [{ source_url: 'https://www.youtube.com/@veritasium', display_name: 'Veritasium' }]);
    await skipOnboarding();

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ['/api/onboarding/complete', 'POST', JSON.stringify({ keys: ['music'], follows: [{ source_url: 'https://www.youtube.com/@veritasium', display_name: 'Veritasium' }] })],
      ['/api/onboarding/skip', 'POST', undefined],
    ]);
  });

  it('requests channel discovery suggestions and search results', async () => {
    const fetchMock = stubFetch({ categories: [] });

    await getChannelSuggestions(['music', 'science-technology']);
    await searchChannels('veritasium');

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ['/api/discovery/channels?keys=music&keys=science-technology', undefined, undefined],
      ['/api/discovery/channels/search', 'POST', JSON.stringify({ query: 'veritasium' })],
    ]);
  });

  it('defaults to same-origin requests when no development override is configured', () => {
    expect(resolveApiBaseUrl(undefined)).toBe('');
    expect(resolveApiBaseUrl('http://127.0.0.1:8765/')).toBe('http://127.0.0.1:8765');
  });

  it('forwards caller cancellation to an in-flight preview request', async () => {
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    }));
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    const request = previewUrl({ source_url: 'https://example.test/video' }, { signal: controller.signal });
    controller.abort();

    await expect(request).rejects.toMatchObject({ name: 'AbortError' });
    expect((fetchMock.mock.calls[0][1]?.signal as AbortSignal).aborted).toBe(true);
  });

  it.each([
    ['preview', previewJellyfinImport],
    ['import', runJellyfinImport],
  ])('gives a Jellyfin %s three minutes, not the default 20 s, because the server keeps working', async (_name, call) => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      }));
      vi.stubGlobal('fetch', fetchMock);
      const request = call('alice', 'pw');
      request.catch(() => undefined);
      const signal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;
      await vi.advanceTimersByTimeAsync(179_000);
      expect(signal.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1_000);
      expect(signal.aborted).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('sends the browser playback profiles that unlock the remote quality ladder', async () => {
    const fetchMock = stubFetch({ kind: 'video', entries: [], raw: {} });

    await previewUrl({
      source_url: 'https://example.test/video',
      supported_profiles: ['mp4-avc-aac', 'm4a-aac'],
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.supported_profiles).toEqual(['mp4-avc-aac', 'm4a-aac']);
  });

  it('advertises SegmentBase playback only when MediaSource accepts split MP4 video and audio', () => {
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('probably');
    const isTypeSupported = vi.fn((mime: string) => mime.includes('video/mp4') || mime.includes('audio/mp4'));
    vi.stubGlobal('MediaSource', { isTypeSupported });

    expect(browserSupportedPlaybackProfiles()).toContain('dash-segment-base');
    expect(browserSupportedPlaybackProfiles()).not.toContain('dash-webm-vp9');

    isTypeSupported.mockImplementation((mime: string) => mime.includes('/mp4') || mime.includes('vp9'));
    expect(browserSupportedPlaybackProfiles()).toContain('dash-webm-vp9');

    isTypeSupported.mockImplementation((mime: string) => mime.includes('video/mp4'));
    expect(browserSupportedPlaybackProfiles()).not.toContain('dash-segment-base');
  });

  it('advertises HEVC, AC-3 and HDR when the browser reports them, and sends them for local playback', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockImplementation((mime: string) => (mime.includes('hvc1') || mime.includes('ac-3') || mime.includes('avc1') ? 'probably' : ''));
    vi.stubGlobal('matchMedia', vi.fn((query: string) => ({ matches: query === '(dynamic-range: high)' })));
    const profiles = browserSupportedPlaybackProfiles();
    expect(profiles).toEqual(expect.arrayContaining(['mp4-avc-aac', 'mp4-hevc-aac', 'mp4-hevc10-aac', 'mp4-ac3', 'hdr']));
    expect(profiles).not.toContain('mp4-eac3');

    const fetchMock = stubFetch({ mode: 'direct', reason: null, facts: null });
    await getLocalPlaybackOptions('item-1');
    expect(String(fetchMock.mock.calls[0][0])).toContain(`/api/library/item-1/playback-options?profiles=${encodeURIComponent(profiles.join(','))}`);
  });

  it('offers HEVC only when the converted path can play it, and never after it failed here', () => {
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockImplementation((mime: string) => (mime.includes('hvc1') || mime.includes('avc1') ? 'probably' : ''));
    // Firefox on Windows: the element says yes, MediaSource (hls.js) says no.
    vi.stubGlobal('MediaSource', { isTypeSupported: (mime: string) => !mime.includes('hvc1') });
    expect(browserSupportedPlaybackProfiles()).not.toContain('mp4-hevc-aac');
    expect(browserSupportedPlaybackProfiles()).toContain('mp4-avc-aac');
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    expect(browserSupportedPlaybackProfiles()).toEqual(expect.arrayContaining(['mp4-hevc-aac', 'mp4-hevc10-aac']));
    // iPhone Safari: no MediaSource, native HLS plays what the element plays.
    vi.stubGlobal('MediaSource', undefined);
    expect(browserSupportedPlaybackProfiles()).toContain('mp4-hevc-aac');
    markHevcFailed();
    try {
      expect(browserSupportedPlaybackProfiles()).not.toContain('mp4-hevc-aac');
      expect(browserSupportedPlaybackProfiles()).not.toContain('mp4-hevc10-aac');
    } finally { localStorage.clear(); }
  });

  it('preserves structured acquisition failure categories for callers', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      detail: {
        category: 'format_unavailable',
        message: 'No matching media format is available.',
      },
    }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })));

    await expect(previewUrl({ source_url: 'https://example.test/watch-1' })).rejects.toMatchObject({
      name: 'ApiRequestError',
      status: 400,
      category: 'format_unavailable',
      message: 'No matching media format is available.',
    });
  });

  it('requests the bounded ranked local discovery contract', async () => {
    const fetchMock = stubFetch({
      query: 'space exploration',
      mode: 'hybrid',
      matches: [],
      items: [],
      index_generation: 1,
    });

    const response = await searchLibrary('space exploration', 7);

    expect(fetchMock.mock.calls[0][0]).toBe('/api/search?q=space%20exploration&limit=7');
    expect(response.mode).toBe('hybrid');
  });

  it('uses the authenticated household and comment mutation routes', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => new Response(
      init?.method === 'DELETE' ? null : JSON.stringify({ id: 'record-1' }),
      { status: init?.method === 'DELETE' ? 204 : 200, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    await updateUser('member one', { role: 'admin' });
    await updateLibraryNote('note one', { body: 'Updated', visibility: 'household' });
    await deleteLibraryNote('note one');

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ['/api/admin/users/member%20one', 'PUT', JSON.stringify({ role: 'admin' })],
      ['/api/library/notes/note%20one', 'PUT', JSON.stringify({ body: 'Updated', visibility: 'household' })],
      ['/api/library/notes/note%20one', 'DELETE', undefined],
    ]);
  });

  it('encodes acquisition batch and household collection mutation routes', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(
      init?.method === 'DELETE' ? null : JSON.stringify({ id: 'record-1' }),
      { status: init?.method === 'DELETE' ? 204 : 200, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    await createAcquisitionBatch({
      source_url: 'https://example.com/list',
      format_selection: { preset: 'best', output_container: 'mp4' },
      output_profile: { organize_by: 'playlist' },
      entries: [{ source_url: 'https://example.com/one' }],
    });
    await retryAcquisitionEntry('batch one', 'entry one');
    await renameHouseholdCollection('family one', 'New name');
    await addHouseholdCollectionItem('family one', 'item one');
    await deleteHouseholdCollection('family one');

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/acquisition-batches', 'POST'],
      ['/api/acquisition-batches/batch%20one/entries/entry%20one/retry', 'POST'],
      ['/api/collections/family%20one/name', 'PUT'],
      ['/api/collections/family%20one/items/item%20one', 'POST'],
      ['/api/collections/family%20one', 'DELETE'],
    ]);
  });
});

describe('media vault client', () => {
  it('builds title, episode and next-up requests', async () => {
    const fetchMock = stubFetch({ items: [], next_cursor: null });
    await listTitles({ type: 'movie', sort: 'created', limit: 12 });
    await listSeasonEpisodes('series-1', 0);
    await listNextUp();
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/titles?type=movie&sort=created&limit=12',
      '/api/titles/series-1/episodes?season=0',
      '/api/titles/next-up?limit=12',
    ]);
  });
  it('asks for category and music walls, facets and Library sections', async () => {
    const fetchMock = stubFetch({});
    await listTitles({ category: 'anime', sort: 'name', limit: 40 });
    await listTitles({ type: 'album', sort: 'created' });
    await getTitleFacets({ category: 'anime' });
    await getTitleFacets({ type: 'album' });
    await getLibrarySections();
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/titles?category=anime&sort=name&limit=40',
      '/api/titles?type=album&sort=created',
      '/api/titles/facets?category=anime',
      '/api/titles/facets?type=album',
      '/api/library/sections',
    ]);
  });

  it('sends favorites and continue-watching dismissals with the right verbs', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    await setFavorite('t 1', true);
    await setFavorite('t 1', false);
    await hideContinueWatching('i1');
    await unhideContinueWatching('i1');
    await dismissNextUp('series-1');
    expect(fetchMock.mock.calls.map(([url, init]) => `${init?.method} ${url}`)).toEqual([
      'PUT /api/me/favorites/t%201',
      'DELETE /api/me/favorites/t%201',
      'POST /api/library/i1/playback/dismiss',
      'DELETE /api/library/i1/playback/dismiss',
      'POST /api/titles/series-1/next-up/dismiss',
    ]);
  });

  it('routes subtitle jobs by kind and escapes track ids', async () => {
    const fetchMock = stubFetch({ id: 'job-1', state: 'queued' });
    await requestSubtitleJob('i1', { kind: 'generate' });
    await requestSubtitleJob('i1', { kind: 'sync', trackId: 's:0' });
    await requestSubtitleJob('i1', { kind: 'translate', trackId: 'e:2', targetLanguage: 'es' });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/library/i1/subtitle-tracks/generate',
      '/api/library/i1/subtitle-tracks/s%3A0/sync',
      '/api/library/i1/subtitle-tracks/e%3A2/translate',
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({ target_language: 'es' });
  });

  it('confirms an import and searches identify candidates with an optional year', async () => {
    const fetchMock = stubFetch({});
    await confirmImport('run-1');
    await searchTitleMatches('t1', 'Arrival', 2016);
    await searchTitleMatches('t1', 'Arrival');
    expect(fetchMock.mock.calls.map(([url, init]) => `${init?.method ?? 'GET'} ${url}`)).toEqual([
      'POST /api/admin/imports/run-1/confirm',
      'GET /api/admin/titles/t1/identify?q=Arrival&year=2016',
      'GET /api/admin/titles/t1/identify?q=Arrival',
    ]);
  });
});

describe('on-device model contracts', () => {
  it('calls the admin model routes with an encoded id', async () => {
    const fetchMock = stubFetch({ id: 'granite', state: 'downloading' });

    await downloadLocalModel('granite');
    await cancelLocalModelDownload('granite');
    await activateLocalModel('granite');
    await removeLocalModel('a/b');

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/admin/models/granite/download', 'POST'],
      ['/api/admin/models/granite/cancel', 'POST'],
      ['/api/admin/models/granite/activate', 'POST'],
      ['/api/admin/models/a%2Fb', 'DELETE'],
    ]);
  });
});

describe('live and YouTube client calls', () => {
  it('asks for a channel page by id, tab and limit, and waits past the server\'s 15 s cold miss', async () => {
    const fetchMock = stubFetch({ channel: {}, tab: 'streams', entries: [], has_more: false, restricted: false, fetched_at: '', stale: false });
    await getChannelPage('UCabcdefghijklmnopqrstuv', { tab: 'streams', limit: 120 });
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/channels\/youtube\/UCabcdefghijklmnopqrstuv\?tab=streams&limit=120$/);
    await getChannelPage('UCabcdefghijklmnopqrstuv');
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/api\/channels\/youtube\/UCabcdefghijklmnopqrstuv$/);
  });

  it('resolves a channel address with a POST and lists Library channels by sort', async () => {
    const fetchMock = stubFetch([]);
    await resolveChannel('https://www.youtube.com/@harborfilms');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST', body: JSON.stringify({ url: 'https://www.youtube.com/@harborfilms' }) });
    await listLibraryChannels('name');
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/api\/library\/channels\?source=youtube&sort=name$/);
  });
});


describe('recommendation calls', () => {
  const event = { kind: 'impression' as const, list_id: '0123456789abcdef', key: 'a'.repeat(64), age_ms: 1200 };

  function stubNoContent() {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it('posts impressions and opens as one batch, without a body token', async () => {
    const fetchMock = stubNoContent();

    await sendRecoEvents([event]);

    expect(fetchMock.mock.calls[0][0]).toBe('/api/reco/events');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ events: [event] });
  });

  it('beacons on pagehide with the CSRF token in the body, and reports a refusal', async () => {
    const sendBeacon = vi.fn((_url: string, _body?: BodyInit | null) => true);
    vi.stubGlobal('navigator', { sendBeacon });

    expect(beaconRecoEvents([event])).toBe(true);
    const [url, blob] = sendBeacon.mock.calls[0];
    expect(url).toBe('/api/reco/events');
    const body = JSON.parse(await (blob as Blob).text());
    expect(body.events).toEqual([event]);
    expect('csrf' in body).toBe(true);

    sendBeacon.mockReturnValueOnce(false);
    expect(beaconRecoEvents([event])).toBe(false);
  });

  it('clears the member\'s recommendation history', async () => {
    const fetchMock = stubNoContent();

    await clearRecoHistory();

    expect(fetchMock.mock.calls[0][0]).toBe('/api/reco/history');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('DELETE');
  });

  it('sends a show-fewer with its stable channel and list, and Up Next with the current channel', async () => {
    const fetchMock = stubFetch({});
    const channel = `UC${'a'.repeat(22)}`;

    await suppressRecommendation({ scope: 'fewer', uploader: 'Chan', channel_id: channel, list_id: event.list_id, key: event.key });
    await getUpNext({ source_url: 'https://www.youtube.com/watch?v=x', channel_id: channel });

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ scope: 'fewer', uploader: 'Chan', channel_id: channel, list_id: event.list_id, key: event.key });
    expect(fetchMock.mock.calls[1][0]).toBe('/api/discovery/up-next');
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({ channel_id: channel });
  });
});

describe('device ring client', () => {
  it('sends remember_on_device, reads members, switches with a fresh CSRF token and forgets', async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      if (url.endsWith('/api/session/device-members')) return new Response(JSON.stringify([{ user_id: 'm1', display_name: 'Member 1', username: 'member1', role: 'viewer', switch: 'instant', active: false }]), { status: 200 });
      if (url.endsWith('/api/session/forget')) return new Response(null, { status: 204 });
      return new Response(JSON.stringify({ user: { id: 'm1' }, csrf_token: url.endsWith('/api/session/switch') ? 'fresh' : 'login' }), { status: 200 });
    }));
    await loginSession({ username: 'member1', password: 'x', remember_on_device: true });
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ username: 'member1', password: 'x', remember_on_device: true });
    expect((await getDeviceMembers())[0].switch).toBe('instant');
    await switchMember('m1');
    expect(JSON.parse(String(calls[2][1]?.body))).toEqual({ user_id: 'm1' });
    await forgetDeviceMember('m1');
    expect(new Headers(calls[3][1]?.headers).get('X-CSRF-Token')).toBe('fresh');
    vi.unstubAllGlobals();
  });
});

describe('2.1.0 contract', () => {
  it('keeps the JSON body of an error response on ApiRequestError', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'invalid_field', title_id: 't1', field: 'year', reason: 'range' }), {
      status: 422, headers: { 'Content-Type': 'application/json' },
    })));
    const error = await listJobs().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).status).toBe(422);
    expect((error as ApiRequestError).body).toEqual({ detail: 'invalid_field', title_id: 't1', field: 'year', reason: 'range' });
  });

});

describe('2.1.0 metadata editor and artwork calls', () => {
  const call = (fetchMock: ReturnType<typeof stubFetch>) => ({ url: fetchMock.mock.calls[0][0], init: fetchMock.mock.calls[0][1] as RequestInit });
  const failWith = (status: number, body: unknown) => vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })));
  const cases: [string, () => Promise<unknown>, string, string, unknown][] = [
    ['getTitleMetadata', () => getTitleMetadata('t 1'), '/api/titles/t%201/metadata', 'GET', undefined],
    ['saveMetadataEdits', () => saveMetadataEdits([{ title_id: 't1', changes: { overview: { value: 'x', base: 'y' } } }]), '/api/metadata/edits', 'POST', { edits: [{ title_id: 't1', changes: { overview: { value: 'x', base: 'y' } } }] }],
    ['revertMetadata', () => revertMetadata('t1', ['overview']), '/api/titles/t1/metadata/revert', 'POST', { fields: ['overview'] }],
    ['getMetadataHistory', () => getMetadataHistory('t1', 'c1', 20), '/api/titles/t1/metadata/history?cursor=c1&limit=20', 'GET', undefined],
    ['undoMetadataBatch', () => undoMetadataBatch('b1'), '/api/metadata/batches/b1/undo', 'POST', undefined],
    ['previewMetadataRefresh', () => previewMetadataRefresh('t1'), '/api/titles/t1/metadata/refresh-preview', 'POST', undefined],
    ['listMetadataEpisodes', () => listMetadataEpisodes('s1', 'se1'), '/api/titles/s1/metadata/episodes?season_id=se1', 'GET', undefined],
    ['getMetadataVocabulary', () => getMetadataVocabulary('genres'), '/api/metadata/vocabulary?field=genres', 'GET', undefined],
    ['searchMetadataPeople', () => searchMetadataPeople('ann', 5), '/api/metadata/people?q=ann&limit=5', 'GET', undefined],
    ['listImageCandidates', () => listImageCandidates('t1', 'Backdrop', 2), '/api/titles/t1/images/Backdrop/candidates?index=2', 'GET', undefined],
    ['chooseTitleImage', () => chooseTitleImage('t1', 'Primary', 0, { tmdb_path: '/a.jpg', base_tag: 'x' }), '/api/titles/t1/images/Primary/0', 'PUT', { tmdb_path: '/a.jpg', base_tag: 'x' }],
    ['removeTitleImage', () => removeTitleImage('t1', 'Backdrop', 1, 'tag1'), '/api/titles/t1/images/Backdrop/1?base_tag=tag1', 'DELETE', undefined],
    ['removeTitleImage without tag', () => removeTitleImage('t1', 'Logo', 0, null), '/api/titles/t1/images/Logo/0', 'DELETE', undefined],
    ['reorderBackdrops', () => reorderBackdrops('t1', ['b', 'a']), '/api/titles/t1/images/Backdrop/order', 'POST', { tags: ['b', 'a'] }],
    ['bulkEditMetadata', () => bulkEditMetadata({ title_ids: ['t1'], ops: [] } as never), '/api/metadata/bulk', 'POST', { title_ids: ['t1'], ops: [] }],
  ];
  it.each(cases)('%s sends the expected request', async (_name, run, url, method, body) => {
    const fetchMock = stubFetch([]);
    await run();
    const sent = call(fetchMock);
    expect(sent.url).toBe(url);
    expect(sent.init.method ?? 'GET').toBe(method);
    expect(sent.init.body === undefined ? undefined : JSON.parse(sent.init.body as string)).toEqual(body);
  });

  it('uploads the raw file with its own content type, base_tag and a 60 s budget', async () => {
    const fetchMock = stubFetch([]);
    const file = new Blob(['x'], { type: 'image/png' });
    await uploadTitleImage('t1', 'Primary', 0, file, 'old tag');
    const { url, init } = call(fetchMock);
    expect(url).toBe('/api/titles/t1/images/Primary/0?base_tag=old%20tag');
    expect(init.method).toBe('PUT');
    expect(init.body).toBe(file);
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('image/png');
  });

  it('resolves an all-conflicts 409 with its EditResult body', async () => {
    const result = { batch_id: null, titles: [], conflicts: [{ title_id: 't1', fields: ['overview'], current: {} }] };
    failWith(409, result);
    await expect(saveMetadataEdits([{ title_id: 't1', changes: {} }])).resolves.toEqual(result);
  });

  it('rejects a 422 with the field and reason on error.body', async () => {
    failWith(422, { detail: 'invalid_field', title_id: 't1', field: 'year', reason: 'out_of_range' });
    const error = await saveMetadataEdits([{ title_id: 't1', changes: {} }]).catch((e) => e);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error.status).toBe(422);
    expect(error.body).toMatchObject({ field: 'year', reason: 'out_of_range' });
  });

  it('rejects a 409 that is not a conflict list', async () => {
    failWith(409, { detail: 'title_locked' });
    await expect(saveMetadataEdits([])).rejects.toMatchObject({ status: 409 });
  });

  it.each([[413, 'too_large'], [415, 'unsupported_image'], [422, 'image_dimensions']])('upload %i surfaces %s', async (status, detail) => {
    failWith(status, { detail });
    const error = await uploadTitleImage('t1', 'Primary', 0, new Blob(['x'], { type: 'image/png' }), null).catch((e) => e);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error.status).toBe(status);
    expect(error.message).toBe(detail);
    expect(error.body).toEqual({ detail });
  });
});
