# Native API performance map — 2026-11-02

## Scope and method

This is a source-backed inventory of the iOS and tvOS clients in `apple/` as
of 2026-11-02. It covers requests made by `LuminaCore`, including requests
initiated by shared iOS/tvOS SwiftUI views, plus AVFoundation-initiated media
traffic whose URL Lumina creates. It does **not** include browser traffic
(documented separately), operating-system DNS/TLS work, or traffic generated
by a user leaving Lumina for an external provider.

This report is static analysis unless marked **measured**. Endpoint counts are
source-route families, not production request counts. We did not collect
credentials, real server URLs, packet captures, or production traces. That
means this document can identify duplicated calls, serial waterfalls, missing
budgets, and cancellation behavior, but cannot rank them by actual p50/p95
latency, bytes, cache-hit rate, or error rate yet.

Primary evidence is the shipping client source and matching FastAPI handlers.
Existing synthetic qualification is called out separately; it is not silently
treated as a device or household-network measurement.

The companion machine-readable inventory is
[`native-api-inventory-2026-11-02.json`](native-api-inventory-2026-11-02.json):
73 path/caller authority records across 15 source files, plus 19 manually
reconciled production network-execution/AVFoundation handoff sites. The former
groups constrained facade route families; neither number is a runtime
endpoint/request total. Dynamic playback and provider URLs remain explicitly
marked as such, and the inventory includes a reproducible static extractor
command with reviewed exclusions.

### Bounded loopback observations added during this audit

[The committed exact-query observations](measurements/api-native-exact-2026-11-02.json) contain
20 real-HTTP loopback samples at C1 and C6 against the default seeded catalog:
1,735 movies, 850 series, 37,500 episodes, 60 anime/720 anime episodes, and
30 albums/300 tracks. The measurement host was heavily contended (load average
recorded in the companion artifact was roughly 65/51/30 before the run), uses
one shared admin principal, does not flush filesystem caches, disables some
rate-limit handling, and has no network shaping. Treat values as a bounded
route observation, not a native UI time-to-first-frame, LAN/WAN, device, or
independent-user result.

| Route case | C1 p50 / p95 | C6 p50 / p95 | Median response / SQL | Fidelity to current native source |
| --- | ---: | ---: | ---: | --- |
| `/Lumina/Settings/navigation` | 11.4 / 14.4 ms | 44.2 / 53.5 ms | 94 B / 11 | Exact route, measured in companion `measurements.json`. |
| `/Lumina/Settings/capabilities` | 3.3 / 3.7 ms | 11.5 / 15.6 ms | 292 B / 3 | Exact route, measured in companion `measurements.json`. |
| Catalog first page: `/Items…Fields=LuminaCatalog…StartIndex=0&Limit=50` | 16.6 / 17.5 ms | 62.2 / 71.6 ms | 22,307 B / 6 | Exact Movies catalog shape. |
| Catalog continuation: `/Items…Fields=LuminaCatalog…StartIndex=50&Limit=500` | 40.9 / 98.9 ms | 282.0 / 293.8 ms | 219,801 B / 6 | Exact Movies continuation shape. |
| Recent: `/Items?IncludeItemTypes=Movie,Episode,Video…Fields=Overview,UserData,ImageTags,People…Limit=50` | 200.7 / 320.1 ms | 1,734.2 / 1,902.9 ms | 54,569 B / 7 | Exact compatibility-Home query; the largest observed C6 regression in this narrow set. |
| Resume: `/UserItems/Resume?…Fields=Overview,UserData,ImageTags&Limit=50` | 23.0 / 32.3 ms | 101.7 / 135.1 ms | 26,861 B / 10 | Exact compatibility-Home query. |
| Next up: `/Shows/NextUp?Limit=50` | 82.5 / 158.6 ms | 443.1 / 478.2 ms | 54,820 B / 10 | Exact compatibility-Home query. |
| `/Lumina/Home/rows` | 273.9 / 512.9 ms | 780.9 / 922.2 ms | 129,958 B / 72 | Exact base route from companion `measurements.json`; Streaming may add `include_external=true`. |

For contrast only, the companion `measurements.json` also retains earlier
**non-fidelity** `/Items` probes that omitted `Fields`: page 50 was 20.4 / 35.3
ms at C1 and 107.5 / 130.6 ms at C6 (46,767 B, six SQL statements), while a
500-record page was 116.1 / 286.5 ms at C1 and 795.0 / 1,247.7 ms at C6
(465,222 B, six SQL statements). They are not benchmarks for the current
native DTO: ordinary native list calls request `Overview,UserData,ImageTags,
People`, while catalog mode requests `LuminaCatalog`.

The measured 500-record page makes catalog drain cost concrete: current native
complete catalogs issue it **serially** after publishing page one until `total`
is reached. With 1,735 movies, that means four 500-record pages after the first
50 page (the final page is partial); do not multiply p50s into an end-user
latency claim because cancellation, concurrent work, cache state, and server
scheduling change the result. The data does establish a high-value next
investigation: why the exact recently-added query suffers far more at C6 than
the exact full-catalog query while returning fewer rows.

### Coverage

| Area | Static coverage | Important entry points |
| --- | --- | --- |
| Central authenticated JSON transport | Complete | `LuminaClient.swift:4-777` |
| Video library, Home, people/search, playback/progress | Complete | `LuminaStore.swift:471-1819`, `PlaybackScreen.swift:2591-4498` |
| Artwork, poster/backdrop URLs, metadata snapshots | Complete | `ArtworkImageCache.swift:1-469`, `MediaMetadataCache.swift:1-70` |
| Music, books, requests, Home settings, watchlist, metadata editing | Complete | `LuminaMusicLibrary.swift`, `LuminaBooks.swift`, `LuminaRequests.swift`, feature facades below |
| Offline media and progressive music | Complete | `LuminaOfflineLibrary.swift`, `ProgressiveMusicAsset.swift`, `BoundedMusicDownload.swift` |
| TV QR enrollment and iPhone approval | Complete | `LuminaClient.swift:52-92`, `TVDevicePairing.swift` |
| Remote streaming / saved external video | Complete | `NativeStreaming.swift:35-176`, platform streaming views |
| WebSocket/SSE client traffic | No native initiator found | `rg` found no `webSocketTask`, `URLSessionWebSocketTask`, or EventSource use under `apple/App` or `apple/Core/Sources` |

There are 14 central `LuminaClient` workflow families, 8 feature-facade
families, and 7 direct/binary transport families below. Dynamic settings
sub-actions are intentionally reported as a constrained family rather than
pretending every server settings route is used by the clients.

## Transport and compatibility boundary

`LuminaClient` owns ordinary native JSON calls. Its default session is
ephemeral, has a 30-second request/resource timeout, and is invalidated on
deallocation (`apple/Core/Sources/LuminaCore/LuminaClient.swift:4-23`). It sends
`MediaBrowser` authentication with a device ID, JSON accept header, and only
same-origin redirects are accepted (`LuminaClient.swift:669-706`, `844-862`).
Individual progress reports lower their timeout to five seconds
(`LuminaClient.swift:650-667`). The standard request helper does not configure
automatic retries, exponential backoff, conditional GETs, `URLCache`, request
IDs, or client metrics.

Native clients use the root Jellyfin-compatible surface. The backend middleware
maps root requests such as `/Items` to private FastAPI routes such as
`/jellyfin/items`, lowercases *query keys* but preserves query values and the
opaque native streaming tail (`backend/app/routers/jellyfin.py:106-188`). This
is a compatibility alias, not an extra network hop. `/Items` list reads are
also admitted through a per-event-loop capacity limiter before auth/ORM work
(`jellyfin.py:87-100`, `170-182`).

| Native route family | Server handler family / behavior | Notes |
| --- | --- | --- |
| `/System`, `/Users`, `/Items`, `/Shows`, `/UserViews`, `/Sessions`, `/Audio`, `/Videos`, `/MediaSegments` | Root alias to `backend/app/routers/jellyfin.py` and `jellyfin_integration.py` | Jellyfin protocol compatibility; source request paths retain their public spelling. |
| `/Lumina/Home`, `/Lumina/Library`, `/Lumina/Requests`, `/Lumina/Settings`, `/Lumina/Music`, `/Lumina/Books`, `/Lumina/Metadata`, `/Lumina/Watchlist`, `/Lumina/Streaming` | Root alias to the specialized `/jellyfin/lumina/*` routers registered at `jellyfin.py:1000-1014` | Same auth/authorization boundary; native-only paths do not need a separate base URL. |
| `/Lumina/Streaming/playback/{opaque-id}/…` | Streaming middleware preserves the opaque tail byte-for-byte (`jellyfin.py:119-123`) | Required: case folding a lease identifier would create false 404s. |
| `/api/mobile-connect/claim`, `/api/public/showcase` | Direct public/server API handlers, not Jellyfin aliases | Used only for initial enrollment and signed-out showcase. |

The backend contains an authenticated Jellyfin WebSocket endpoint with
keepalive/idle/lifetime bounds (`jellyfin.py:960-998`), but neither native app
opens it. Native freshness therefore comes from explicit reads/mutations and
playback reports, not server push.

## Workflow inventory

### Session, enrollment, pairing, connectivity

| Trigger / caller | Method and route | Request chain, lifecycle, reliability |
| --- | --- | --- |
| Manual sign-in | `GET /System/Info/Public`; `POST /Users/AuthenticateByName`; `GET /Users/Me` | Serial validation/auth/member identity chain in `LuminaStore.connect` (`LuminaStore.swift:348-365`); then `refresh()` starts Home and Library work. A failed auth is surfaced; no retry. |
| Mobile-connect enrollment | `POST /api/mobile-connect/claim` → public-info → authenticate → me | Four serial calls before session persistence (`LuminaStore.swift:207-227`). Correctly validates returned server origin before accepting credentials. |
| Restore / route recovered | `GET /Users/Me`, then Home+Library refresh | Coalesced by `isConnectivityRefreshInFlight`; concurrent network-path callbacks do not add another member check (`LuminaStore.swift:290-342`, `367-412`). Connection failures present downloads/offline content; 401/403 clear session. |
| Sign out | `DELETE` receiver/cast tickets; `POST /Sessions/Logout` | Session and queued work are cleared before revocation; individual revocation/logout errors are deliberately best-effort (`LuminaStore.swift:414-439`). |
| Apple TV QR bootstrapping | `POST /Lumina/DevicePairing/Start`; repeated `POST …/Status`; once approved `POST …/Claim` → public-info → authenticate → me | Claim has deliberately no automatic retry because it is single-use (`LuminaStore.swift:229-264`). The TV UI sleeps for server-provided poll cadence (`apple/App/tvOS/TVRootView.swift:643`). |
| Authenticated phone approval | `POST /Lumina/DevicePairing/Inspect`, then `…/Approve` or `…/Deny` | Short, explicit mutations (`LuminaClient.swift:71-92`). |
| Signed-out welcome artwork | `GET /api/public/showcase`, then artwork URLs | Has a 10-second timeout and caps accepted slides at 12 (`LuminaShowcase.swift:24-65`). |

**Static observation:** initial sign-in does a serial three-request trust
chain, appropriately. Post-auth Home and Library are parallelized in
`refresh()`; the opportunity is not to remove identity validation, but to
measure whether an authenticated bootstrap response can safely eliminate a
post-login duplicate read (see P2).

### Home, navigation, library, title, people, search

| Trigger / caller | Method and route(s) | Paging, fan-out, cache/cancellation |
| --- | --- | --- |
| Compatibility Home (used alongside rich Home rows) | `GET /Items` recently-added; `GET /UserItems/Resume`; `GET /Shows/NextUp`; `GET /Lumina/Settings/navigation` | Four parallel calls per Home load (`LuminaStore.swift:977-990`). Recent/resume/next-up show stale cached snapshots first when empty, then revalidate; stored lifetime is 24 h (`LuminaStore.swift:1025-1080`). New Home generations cancel predecessor tasks. |
| Rich Home / streaming external rows | `GET /Lumina/Home/rows[?include_external=true]` | One rows document per `LuminaHomeFeed.load`; no persistent cache. Existing successful rows survive transient refresh failure (`LuminaHomeFeed.swift:196-238`, `296-316`). On initial iOS/TV Home admission the layout GET, rich rows GET, and the four-call compatibility Home fan-out start together (six JSON requests total): `MobileRootView.swift:1494-1510`, `TVRootView.swift:1647-1664`. |
| Recommendation feedback | `POST /Lumina/Home/reco/events`; `POST /Lumina/Home/reco/suppressions`; `DELETE /Lumina/Settings/suppressions/{id}` | Impression identities are de-duplicated in memory; a failed impression is unmarked for later retry. Hide updates local rows only after success (`LuminaHomeFeed.swift:243-289`). |
| Video Library first page | `GET /Items` with kind/sort/filter/search, normally `Limit=50` | Query-owned task survives view disappearance; same in-flight query is shared, warm query snapshots reuse loaded items, and eight snapshots/10,000 aggregate items cap memory (`LuminaStore.swift:471-526`, `579-716`). |
| Complete Movies/Shows/Anime catalog | first `GET /Items?Fields=LuminaCatalog&Limit=50`, then serial `Limit=500` pages | Empty-search video walls opt in; background preloader stops on suspend/query change and retains loaded pages (`LuminaStore.swift:820-878`). This is intentionally a metadata, not artwork, backfill; ADR 0034 amendment. |
| Library alphabet | `GET /Lumina/Library/alphabet` | Starts independently after first page; canceled on new query. A loaded complete catalog jumps locally; paged mode requests one anchor page (`LuminaStore.swift:693-714`, `879-929`). |
| Shows / saved streaming root lookup | `GET /UserViews`, then `GET /Items?ParentId=…` | `shows`, `youtube`, and `streaming` library kinds first resolve views; no cache at this layer (`LuminaStore.swift:637-662`). |
| Collections | in parallel franchise `GET /Items?IncludeItemTypes=BoxSet` and household `GET /UserViews`, then `GET /Items?ParentId=playlists&Limit=500` pages | This is a potential high-cardinality serial fan-out for many playlists (`LuminaStore.swift:663-810`). |
| Collection filter picker | `GET /Items?BoxSet&Limit=500` pages in parallel with household collection chain | Independent of current video wall, but no result cache (`LuminaStore.swift:793-823`). |
| Person title filter | paged `GET /Items?PersonIds`/`Persons` | Fetches until `total`; cancellation checked between pages (`LuminaStore.swift:594-614`). |
| Global native search | `GET /Items?SearchTerm&Limit=50` | One request only; does not replace Library state (`LuminaStore.swift:616-634`). |
| Details / related / season children | `GET /Users/{user}/Items/{id}`; `GET /Items/{id}/Similar?Limit=12`; `GET /Shows/{id}/Seasons` or paged `GET /Items?ParentId=…` | iOS/tvOS may use 2-minute metadata snapshots for detail/related/season reads. Cache key contains session and metadata revision; detail cache also checks user state (`LuminaStore.swift:1087-1138`, `1312-1331`; ADR 0034). Child/music loops currently page to completion. |
| Watch state/favorite | `POST /Users/{user}/Items/{id}/UserData`, then detail reread | Mutation clears metadata cache and adds a follow-up GET to make local state authoritative (`LuminaStore.swift:1214-1232`). |
| Technical-video filter support | `GET /Items/Filters2?Fields=LuminaTechnicalFilters` | Feature detected only when UI needs it; 404 is treated as unsupported (`LuminaStore.swift:528-550`). |

### Image/artwork transport

The core creates signed same-origin rendition URLs for primary, logo,
thumbnail, and backdrop artwork (`LuminaStore.swift:1335-1467`), including
`maxWidth`, image tag, and current token. `ArtworkImageCache` is the only
normal artwork downloader, not `LuminaClient`.

| Route / traffic | Trigger | Cache, budgets, cancellation |
| --- | --- | --- |
| `GET /Items/{id}/Images/Primary`, `/Logo`, `/Backdrop/0` | SwiftUI `ArtworkView`, Home heroes, library grids | Decoded `NSCache`: 128 MiB / 256 images; encoded memory: 32 MiB; hashed member-private disk LRU: 2 GiB. URL includes rendition tag and signed scope (`ArtworkImageCache.swift:7-91`; ADR 0034). |
| Artwork fetch body | Cache miss | Dedicated ephemeral transport disables URLCache and rejects non-2xx/oversize bodies. At most **4 shared** active loads; same URL coalesces consumers; last consumer cancels (`ArtworkImageCache.swift:142-263`, `290-469`). |
| Lookahead/prefetch | Visible card changes | Latest window replaces earlier work, capped by caller (catalog policy: iOS 72/400 px, tvOS 36/480 px); it uses the same four-load pool rather than a second queue (`ArtworkImageCache.swift:265-287`; ADR 0034 amendment). |
| Hero framing | `GET …/Images/Backdrop/0/Framing?tag=…` | Best-effort 150 ms first-response hold, no retry; late server analysis can be reused later (`LuminaClient.swift:33-41`). |
| Request catalog framing | `GET /Lumina/Requests/catalog/framing?src=…` | Same 150 ms hold; ready results held in an 80-entry in-memory cache (`LuminaRequests.swift:501-521`). |

Memory warnings/backgrounding drop only disposable memory and speculative
work. Session/account/credential change invalidates resident and pending disk
artwork; explicit clearing is generation-safe. This is a material reliability
and privacy optimization to preserve, not a generic HTTP cache
(`ArtworkImageCache.swift:93-140`; ADR 0034).

### Video playback, reports, casting, and offline video

| Trigger / caller | Method and route(s) | Chain / frequency / safeguards |
| --- | --- | --- |
| Start video | `GET /Users/{user}/Items/{id}` → `POST /Items/{id}/PlaybackInfo` → AVPlayer `GET /Videos/{id}/stream` **or** server-selected HLS URL | `preparePlayback` always refreshes the item before negotiating. A changed preferred audio/subtitle or external-sidecar requirement causes one additional PlaybackInfo call (`LuminaStore.swift:1469-1552`). |
| Direct media | `GET /Videos/{id}/stream?MediaSourceId…&Static=true&ApiKey…` | URL handed to AVFoundation; response/segment transfer count is OS/player owned and not observable in app source (`LuminaClient.swift:537-623`). |
| HLS media fallback | `GET` server master playlist, then AVPlayer HLS request(s) | Native fetch is capped at 1 MiB, same-origin redirects only; it chooses a valid one-variant media URL before AVPlayer takes over (`LuminaClient.swift:745-777`). This is only on recovery, not ordinary HLS. |
| External subtitles | Subtitle delivery URL `GET` | Sanitizes Jellyfin query credential away in favor of auth header, accepts only VTT, bounds to 10 MiB, stores a private temporary file (`LuminaClient.swift:721-743`; `LuminaStore.swift:1754-1778`). |
| Actions after start | `GET /MediaSegments/{source}` and `GET /Shows/{series}/Episodes?StartItemId…&Limit=2`, in parallel | Optional action metadata is deliberately independent of playback preparation; unavailable actions do not block playback (`PlaybackScreen.swift:3306-3337`). |
| Progress | `POST /Sessions/Playing`, `/Progress`, `/Stopped` | The shared player samples at one second but only reports when elapsed delta reaches `NativeVideoSource.progressObservationInterval`; queue coalesces duplicate pending progress and removes stale progress before Stop (`PlaybackScreen.swift:3039-3057`, `4463-4498`). Five-second request timeout. |
| Next episode / quality / sidecar recovery | repeats PlaybackInfo only for explicit source/quality/selection/recovery action | One guarded compatible retry, and media-playlist recovery reuses the existing negotiated session; no unbounded fallback loop (`LuminaStore.swift:1553-1635`). |
| M2TS seek hints | optional repeated same-origin seek-index GET while `pending` | 500 ms poll, 30 s request timeout, 1 MiB response ceiling; any non-auth error is an optimization miss rather than playback failure (`M2TSSeekIndex.swift:14-81`). |
| Chromecast ticket | `POST /Lumina/Cast/tickets`; renew/revoke ticket | Explicit receiver capability instead of bearer token in media URL; app tracks issued tickets so sign-out cleans them (`LuminaClient.swift:137-173`, `LuminaStore.swift:1637-1667`). |
| Offline video | `GET /Items/{id}/Download`, then optional artwork GET | Queue serializes transfers; URLSession has no cache/cookie/credential store, rejects redirect/oversize, and keeps one active job (`LuminaOfflineLibrary.swift:1-160`, `478-570`). Offline play avoids the network negotiation path. |

### Music

| Trigger / caller | Method and route(s) | Cache/lifecycle/reliability |
| --- | --- | --- |
| Music library collections | `GET /Items` (albums/artists/audio) and paged children | Same `LuminaStore` paging as video (`LuminaStore.swift:1264-1310`). |
| Playlist / mix / resolve / track | `GET/POST/PUT/DELETE /Lumina/Music/playlists…`; `GET /mixes/{kind}`; `POST /queue`; `GET /tracks/{id}` | `LuminaMusicLibrary` keeps a 512-entry metadata lookup; playlist mutations refresh list after success (`LuminaMusicLibrary.swift:74-145`, `236-266`). |
| Lyrics | `GET /Lumina/Music/tracks/{id}/lyrics?retry=…` | Bounded 100-entry cache and member-private persisted snapshot; cached result can be used after a non-retry error (`LuminaMusicLibrary.swift:138-153`). |
| Queue/session | `GET`/`PUT /Lumina/Music/session` | Server revision conflict is decoded as `MusicAPIError.conflict`; checkpoint disk write coalesces newest snapshot off main actor (`LuminaMusicLibrary.swift:155-191`, `268-349`; `LuminaClient.swift:698-707`). |
| Speakers / Sonos | `GET /sonos`; `POST /cast`; `POST /cast/{id}/control`; `GET /cast/{id}/status` | On-demand user actions; no source-visible polling in the core facade. |
| Receiver casting | `POST /Lumina/Music/Receiver/tickets`, renew/revoke | Ticket lifecycle mirrors video cast (`LuminaClient.swift:175-207`). |
| Local audio source | AVFoundation `GET /Audio/{id}/stream?ApiKey…` | Direct audio URL is created locally after item refresh; compatible format requested only if needed (`LuminaClient.swift:362-369`). |
| Music temporary download | direct URLSession download from the audio URL | Private 120 s resource timeout, size ceiling, same-origin redirect and cancellation (`BoundedMusicDownload.swift:3-111`). |
| Progressive music | AVAsset resource-loader `Range` GETs against audio URL | Own ephemeral no-cache/no-cookie session, pauses read-ahead while paused/buffered, resumes for seeking, validates range/size/status, cancels all on tear-down (`ProgressiveMusicAsset.swift:1-218`). |

Existing **measured** music work is documented in
`apple/docs/performance-review.md`: a host synthetic pause/resume workload
reduced repeated media downloads from 11 to 1 and delivered media bytes by
90.9%, and progressive read-ahead caps materially reduced bytes during a
ten-second window. Those measurements are explicitly host component-process
measurements, not phone/TV energy or whole-app claims. Preserve the current
bounded progressive and pause/resume ownership before attempting new music
network changes.

### Books

| Trigger / caller | Method and route(s) | Paging/cache/retry |
| --- | --- | --- |
| Browse / overview / series | `GET /Lumina/Books`, `/overview`, `/series[?cursor]`, `/series/{id}` | Cursor supplied by caller; cache policy deliberately reloads local URLCache (`LuminaBooks.swift:146-178`, `200-218`). |
| Book detail / reader | `GET /{id}/reader`, `/chapters/{index}` | Small JSON reads with cancellation checked after decode. |
| Cover, pages, resources | `GET /{id}/cover`, `/pages/{index}`, `/resources?member=` | Download-to-file verifies image MIME and a 24 MiB ceiling before in-memory decode. Cover alone retries HTTP 202 with 1, 1.5, 2.5, 4, 6, 8, 10 second delays (up to eight attempts) (`LuminaBooks.swift:167-190`, `200-220`). |
| Reader progress | `PATCH /{id}/state` | Five-second timeout; no automatic retry (`LuminaBooks.swift:191-208`). |
| Reader viewport prefetch | page GET | `BookPageWindow` retains visible pages plus at most one lookahead; it cancels obsolete lookahead and caps encoded window at 24 MiB (`LuminaBooks.swift:245-319`). |

### Requests, settings, metadata, watchlist

| Trigger / caller | Method and route family | Behavior |
| --- | --- | --- |
| Requests Home/quota/member records | `GET /Lumina/Requests/catalog/home`, `/quota`, `/?page&status` | Every request reloads local URLCache and is generation-guarded (`LuminaRequests.swift:523-538`, `579-618`). |
| Requests browse / anime / title | `GET /catalog/search`, `/catalog/list/{kind}`, `/catalog/genres/{kind}`, `/catalog/title/{kind}/{id}` | Caller-driven page query. No cached anime-language scan is issued by native client; request detail is a single catalog read. |
| Submit request/language preference | `POST /Lumina/Requests` | One mutation after local `canRequest` validation (`LuminaRequests.swift:560-629`). |
| Account settings | `GET`/`PUT /Lumina/Settings`, then `GET /capabilities` | Settings load is serial (document then capabilities), generation guarded; dynamic `action()` permits only safe path segments and known HTTP methods (`LuminaUserSettings.swift:157-235`). |
| Home layout | `GET`/`PUT /Lumina/Home/layout` | A save supersedes an in-flight load, so stale load cleanup cannot clear state (`LuminaHomeLayout.swift:89-177`). |
| Watchlist | `GET`/`PUT /Lumina/Watchlist/{id}` | One item at a time; mutation state is server response, not optimistic local ownership (`LuminaWatchlist.swift:65-142`). |
| Metadata editor | `GET /Lumina/Metadata/capabilities`, `/titles/{id}`, `/matches`; `POST /edits`, `/titles/{id}/match` | Opening a title first checks permissions then reads the document; match mode performs a third search request (`LuminaMetadataEditor.swift:80-205`). |

### Streaming: saved library and remote provider playback

| Trigger / caller | Method and route(s) | Gates / traffic ownership |
| --- | --- | --- |
| Enter Streaming | parallel `GET /Lumina/Settings/capabilities` and saved-library chain: `GET /UserViews` → `GET /Items?ParentId=Channels&SourceProvider=youtube,twitch,kick` | Saved videos always load. Remote capability determines whether rich Home feed is then loaded. iOS code is explicit at `MobileRootView.swift:2070-2089`; TV has the same model (`TVRootView.swift:2145-2162`). |
| Recommendations/live | `GET /Lumina/Home/rows?include_external=true` | Only requested after member/server capability says remote is available; native does not fetch provider discovery itself. |
| Start remote playback | `POST /Lumina/Streaming/playback` | Server validates member policy/streaming readiness, resolves upstream, then returns a same-origin content/HLS/relay URL or unsupported response (`LuminaClient.swift:101-117`; `backend/app/routers/jellyfin_streaming.py:77-109`). |
| Remote media | AVPlayer GET/range/HLS traffic to `/Lumina/Streaming/playback/{id}/…` | Backend mediates source and enforces rate/screen-time policy (`jellyfin_streaming.py:145-202`). Native traffic is OS-owned once the URL is handed to AVPlayer. |
| End remote playback | `DELETE /Lumina/Streaming/playback/{id}` | Best effort; stale-session start immediately releases its new remote session (`LuminaStore.swift:569-592`). |

## Existing protections and optimizations to preserve

1. **Query ownership rather than view-lifetime ownership.** Home, Library,
   catalog preload, alphabet and session generations coalesce/reject stale
   results; navigation cannot strand an old category or republish after logout.
   Evidence: `LuminaStore.swift:471-526`, `820-929`, `952-1023`.
2. **Bounded private artwork and metadata caches.** The 2 GiB disk artwork
   cache, 128/32 MiB memory targets, four fetch slots, hashed member scope,
   metadata 32 MiB disk/8 MiB memory limits and explicit invalidation avoid
   both repeat fetches and cross-member leakage. Evidence: ADR 0034;
   `ArtworkImageCache.swift:45-140`, `MediaMetadataCache.swift:1-70`.
3. **Library catalog payload shaping.** `Fields=LuminaCatalog`, first-50
   publish, serial 500-record continuation and artwork-only viewport warming
   are deliberate. The TV qualification measured 43,771 versus 101,345 bytes
   for 123 catalog items (56.81% less JSON), not a generalized latency claim:
   `apple/docs/tvos-performance-2026-10-29.md`.
4. **Playback report backpressure.** One pending position replaces the prior
   pending position; Stop dominates stale progress; network report timeout is
   five seconds. Evidence: `PlaybackScreen.swift:4463-4498`,
   `LuminaClient.swift:650-667`.
5. **Media is bounded and interruptible.** Art, books, subtitles, seek index,
   offline files, progressive music, HLS master parsing, and receiver tickets
   all have explicit origin/size/redirect/cancellation controls. These also
   prevent a reliability fix from becoming an unbounded memory/download bug.
6. **Offline is a genuine fallback.** Retained member downloads become the
   local surface on connectivity failure rather than retrying every browse
   request against an unreachable server (`LuminaStore.swift:290-342`,
   `LuminaOfflineLibrary.swift:80-125`).

## Ranked opportunities

Rank reflects static impact/risk, **not measured production volume**.

| Rank | Opportunity | Evidence and expected gain | Concrete next step / acceptance metric |
| --- | --- | --- | --- |
| P1 | Add end-to-end request observability before changing endpoint shape | Neither central `LuminaClient` nor facade sessions attach correlation IDs, collect timing/bytes/cache state, or surface `Server-Timing`; source currently cannot distinguish server queue, DB, transfer, decode, or cancellation. | Add privacy-safe operation name + opaque correlation ID, server duration/DB timing/cache headers, and client URLSession task metrics sampled/aggregated locally. Dashboard per route: p50/p95, bytes, 2xx/4xx/5xx/timeout/cancel, cache hit. Do not log titles/tokens/URLs. |
| P1 | Reduce authenticated startup/Home duplication with a measured bootstrap plan | Restore validates `/Users/Me`, then `refresh()` launches 4 compatibility-Home calls plus Library page/alphabet. Initial native Home additionally starts Home layout and rich rows in parallel—six JSON requests before Library traffic. The bounded loopback rich-row sample alone is 72 SQL / 130 KB and C6 p50 780.9 ms; it must not be read as UI TTFF. | Capture exact native waterfalls first. If meaningful, version a compact authenticated bootstrap document (member/navigation/initial Home pointers/layout/capability revision) or seed Home from a validated response. Retain independent retry and stale-data semantics; target lower critical-path RTT count, not simply fewer calls. |
| P1 | Persist and validate navigation/capability state with a revision/TTL | `/Lumina/Settings/navigation` runs on every compatibility-Home load; Streaming separately calls `/Settings/capabilities` each entry/foreground. Both are small stable documents but have no native TTL/ETag use. | Add server `ETag`/revision and `Cache-Control: private`, plus member/session-scoped cache with short TTL and stale-while-revalidate. Invalidate after settings/onboarding/connected-app change. Measure avoided calls and ensure a capability revocation is observed within the agreed bound. |
| P1 | Make repeated full-list consumer paths budgeted or cursor-first | Collection picker fetches all BoxSets plus all playlists (500/page); person and album/children paths fetch until `total`. On a large server this becomes latency/memory proportional to library size. | Return lightweight/cursor-based picker pages or an endpoint returning just id/name/artwork/count; virtualize the picker. Cap eager loading and expose a continuation. Measure response bytes, peak decoded item count, and time-to-first-result on a large fixture. |
| P2 | Audit ambiguous mutation outcomes before adding replay | General transport has no retry. Network loss can leave ambiguous create/request/hide/watchlist/layout/session outcomes; blindly replaying existing POSTs would duplicate state. | First inventory existing CAS/outbox/idempotency semantics. Reconcile receipts or add an idempotency key only for proven gaps; retry a mutation only after its server replay contract exists. Bound any retry by deadline, jitter and cancellation. Keep pairing Claim and non-idempotent playback starts non-retryable unless their semantics change. |
| P2 | Remove avoidable detail-to-playback waterfall where a fresh item is not required | `preparePlayback` rereads the item, then playback-info; some detail paths have just loaded the same item. Correctness benefit is clear, but it costs one RTT at play. | Add an item revision/ETag or short validated detail freshness token to allow a playback-info request directly when user state/media sources are known current. Revalidate after mutation and retain current safety fallback. Compare tap-to-first-frame and stale-source failures. |
| P2 | Cache/coalesce per-item action metadata for the playback session | Each video start launches markers + next-episode; reopening/retrying repeats them. They are non-blocking, but can contend with first-frame work on high RTT. | Session-key a small TTL/in-flight cache for markers and next episode, keyed by media source/item/member; invalidate on playback decision or watched-state change. Measure duplicate action reads and first-frame contention. |
| P2 | Bound polling and make long preparation state explicit | Book covers retry 202 up to ~33 seconds; M2TS indexing polls every 500 ms until server state ends. Both are bounded by cancellation/size but lack `Retry-After`, deadline telemetry, or user-visible server preparation state. | Server return `Retry-After`/terminal state and client honor it with capped exponential schedule; expose “preparing” state and report timeout reason. Preserve cancellation. |
| P2 | Revisit settings/editor serial chains only after trace evidence | Settings does document then capabilities; metadata editor does capabilities then document then match search. These preserve permissions but add sequential RTTs. | If traces show meaningful delay, offer a scoped combined document+capability endpoint/revision rather than parallelizing authorization-sensitive reads blindly. Require unchanged authorization behavior tests. |
| P3 | Apply conditional response semantics to immutable-ish metadata/artwork where private caching is safe | Native intentionally bypasses URLCache for sensitive member data, which is defensible. It currently relies on its own cache but cannot cheap-revalidate with 304. | Evaluate private `ETag` endpoints for catalog/detail/Home/artwork, using auth-scoped validators and no shared intermediary caching. Retain cache keys that include credential/member/artwork tag. Do not re-enable a shared URLCache by default. |
| P3 | Introduce adaptive concurrency only with backend saturation telemetry | Client has four art loads and server limits `/Items` reads to two per event loop, but JSON fan-out/load-more/media can still collide under poor networks. | Measure queue wait/DB pool pressure and client network type; then enforce explicit client operation classes (critical/Home/background/preload) rather than a global arbitrary cap. Verify playback/auth never starve behind catalog work. |

## Reliability gaps to verify with focused tests/telemetry

- **Timeout taxonomy:** normal requests share 30 seconds, but no caller sees
  whether DNS, connection, TLS, first byte, or decode consumed it. Capture
  `URLSessionTaskMetrics` and server phase timing first.
- **Retry semantics:** current cancellation and generation guards are strong,
  while automatic retry is deliberately absent. Add a contract matrix before
  implementation: GET/idempotent PUT/retryable POST/non-retryable Claim and
  ticket mint endpoints, duplicate submission behavior, and logout mid-retry.
- **AVFoundation ownership:** direct/HLS/range request count, buffering,
  cache and retries are not visible in Swift source. Instrument the server
  streaming/media endpoints and validate physical iOS/tvOS sessions.
- **Offline reconciliation:** playback position is locally durable and sent
  whenever reachable, but current source does not show a durable outbound
  mutation queue for a failed online report. Decide whether best-effort is
  sufficient; if not, add a bounded member-scoped last-position outbox with
  conflict/order rules.
- **Capability revocation:** streaming is server-gated on every start/content
  request, which is safe. A cached presentation gate must never delay enforced
  server denial or expose remote recommendations for a member whose onboarding
  status has changed.

## Existing validation and limits

The repository already has focused native contracts for the central surfaces:
`LuminaCoreTests` includes artwork, books, casts, music, offline, requests,
settings, TV pairing/native server and video source tests (see
`apple/Core/Tests/LuminaCoreTests/`). The relevant prior qualifications are
`apple/docs/performance-review.md`,
`apple/docs/tvos-performance-2026-10-29.md`,
`apple/docs/tvos-cached-browsing-2026-10-29.md`, and ADR 0034. They establish
specific synthetic/source contracts—not production service-level objectives.

No source was changed for this audit and no full suite was run. Before
implementing any ranked change, add a focused public workflow or service
contract test and capture baseline/after measurements for the affected route
family. The instrumentation baseline is the prerequisite for confidently ordering
the other work on a real household server.
