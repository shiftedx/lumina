# Arr connector contracts

- Date: 2026-10-17
- Scope: Phase 3 discovery for Lidarr, Bazarr, Prowlarr, and the selected
  qBittorrent/NZBGet download-client observers. This is a contract record, not
  an assertion that any of these services is interoperable with this Lumina
  host.
- Evidence level: source contracts were read from the owning projects. The
  household selected qBittorrent, NZBGet, and Chromecast; an authorized,
  read-only homelab inspection found declarative service entries for
  Sonarr/Radarr/Lidarr/Prowlarr/qBittorrent/NZBGet and an existing qBittorrent
  API consumer. No live service or credential was accessed, and
  versions/identity joins remain qualification gaps.

## Baseline and boundary

The current application supports only `sonarr` and `radarr`: the request API's
server kind is a two-value literal, `ArrServer` says it represents one of those
two services, and `ArrClient` uses `/api/v3`. There is no production Lidarr,
Bazarr, Prowlarr, qBittorrent, Transmission, SABnzbd, or NZBGet client or
configuration. The closest test seam is `backend/tests/arr_fake.py`, which is
also only a Sonarr/Radarr v3 fake.

This matters because a music request must not be squeezed into
`media_requests`, whose kinds are `movie`, `show`, and `anime`. Phase 3A needs
a music-specific request/correlation contract. ADR 0018 also keeps release
selection, importing, naming, upgrades, download-client operation, and file
lifecycle with the owning Arr service. Lumina may observe these systems and
submit a tightly permitted command, but it must not bypass the owning manager
to control an Arr-owned transfer.

No installed-service evidence exists in this repository: no relevant server
row, environment setting, Compose service, operator guide, or test fixture
records a real version. The supported-version field for every Phase 3 connector
is therefore **unqualified**. A setup probe must persist the exact value from
the connector's status endpoint, its API root/base path, and the result of the
minimal read contract before the connector is enabled. Do not turn the source
repository's moving `HEAD`, or the OpenAPI document's `1.0.0` metadata, into a
product version promise.

All adapters should preserve the existing Arr boundary: configured LAN base
URLs only; no redirect following or proxy environment; bounded response bytes
and time; `X-Api-Key` sent only as a header; write-only secrets; content-free
errors; and no raw paths, diagnostic bodies, or credentials in member
responses. The header form avoids putting a key in a URL even where an upstream
also accepts a query parameter.

## Provider matrix

| Connector | Official contract and authentication | Stable correlation identity | Paginated reads | Phase 3 capability contract | Qualification status |
| --- | --- | --- | --- | --- | --- |
| Lidarr | `GET /api/v1/system/status`; API key as `X-Api-Key` (the implementation also accepts `apikey` and bearer input, which Lumina should not use). [OpenAPI](https://github.com/Lidarr/Lidarr/blob/aec88028d036707619baacbbb840b30c2d716318/src/Lidarr.Api.V1/openapi.json), [auth handler](https://github.com/Lidarr/Lidarr/blob/aec88028d036707619baacbbb840b30c2d716318/src/Lidarr.Http/Authentication/ApiKeyAuthenticationHandler.cs). | Store the configured server ID plus Lidarr's numeric `artist.id` and `album.id`; use `artist.foreignArtistId` and `album.foreignAlbumId` as external music identities, never a display title. [artist](https://github.com/Lidarr/Lidarr/blob/aec88028d036707619baacbbb840b30c2d716318/src/Lidarr.Api.V1/Artist/ArtistResource.cs), [album](https://github.com/Lidarr/Lidarr/blob/aec88028d036707619baacbbb840b30c2d716318/src/Lidarr.Api.V1/Albums/AlbumResource.cs). | `queue`, `history`, `wanted/missing`, and `wanted/cutoff` take `page` and `pageSize` and return the Arr paging envelope. Continue until all `totalRecords` are read; defaults are 1/10 and are not a safe whole-library fetch. [queue controller](https://github.com/Lidarr/Lidarr/blob/aec88028d036707619baacbbb840b30c2d716318/src/Lidarr.Api.V1/Queue/QueueController.cs), [history controller](https://github.com/Lidarr/Lidarr/blob/aec88028d036707619baacbbb840b30c2d716318/src/Lidarr.Api.V1/History/HistoryController.cs). | Read status, artists, albums, roots, profiles, queue/history, and missing/cutoff. After qualification, an approved music request may add/monitor the artist/album and initiate the owning Lidarr search command. A requested album is still not playable until Lumina's normal scoped import finds member-visible tracks. | No live Lidarr version, API response, release/completeness behavior, roots, path mapping, or command result has been verified. |
| Bazarr | `GET /api/system/status`, with `X-API-KEY`. The API source declares the `/api` prefix and key scheme; status returns Bazarr and attached Sonarr/Radarr versions. [API registration](https://github.com/morpheus65535/bazarr/blob/bfe4593b04caa16eb4a09c2a233777b0bd7723b6/bazarr/api/__init__.py), [status](https://github.com/morpheus65535/bazarr/blob/bfe4593b04caa16eb4a09c2a233777b0bd7723b6/bazarr/api/system/status.py). | Bazarr reports the upstream numeric `radarrId`, `sonarrSeriesId`, and `sonarrEpisodeId`; correlate only through Lumina's already-correlated Radarr/Sonarr records, never a path or title. [movie wanted](https://github.com/morpheus65535/bazarr/blob/bfe4593b04caa16eb4a09c2a233777b0bd7723b6/bazarr/api/movies/wanted.py), [episode wanted](https://github.com/morpheus65535/bazarr/blob/bfe4593b04caa16eb4a09c2a233777b0bd7723b6/bazarr/api/episodes/wanted.py). | `GET /api/movies/wanted` and `GET /api/episodes/wanted` use zero-based `start` and `length`, return `{data, total}`, and deliberately disable paging for a supplied upstream-ID filter. Fetch windows until `start + data.length >= total`; do not apply Arr `page/pageSize` rules here. | Conditional read-only missing-language/status view first. Explicit per-movie or per-episode search/download is available through `PATCH /api/movies/subtitles` or `/api/episodes/subtitles` with upstream IDs, language, forced, and HI values. Expose it only after version/auth/identity qualification and permission/audit design; it writes a sidecar file. [movie command](https://github.com/morpheus65535/bazarr/blob/bfe4593b04caa16eb4a09c2a233777b0bd7723b6/bazarr/api/movies/movies_subtitles.py), [episode command](https://github.com/morpheus65535/bazarr/blob/bfe4593b04caa16eb4a09c2a233777b0bd7723b6/bazarr/api/episodes/episodes_subtitles.py). | No Bazarr instance, attached upstream IDs, API version, subtitle-provider behavior, or filesystem/path mapping is evidenced. Keep the connector absent, not merely disabled, until qualified. |
| Prowlarr | `GET /api/v1/system/status` with `X-Api-Key`; Prowlarr's published OpenAPI also lists `apikey`, but Lumina should use the header. [OpenAPI](https://github.com/Prowlarr/Prowlarr/blob/b8d04898207b16070c010c8d74b4cc218ff84b8f/src/Prowlarr.Api.V1/openapi.json). | Persist Prowlarr's numeric `indexer.id`; history links it with `indexerId` and may contain a `downloadId`. Treat those strings as correlation hints only until an owning Arr record proves the link. [history resource](https://github.com/Prowlarr/Prowlarr/blob/b8d04898207b16070c010c8d74b4cc218ff84b8f/src/Prowlarr.Api.V1/History/HistoryResource.cs). | `history` uses the standard `page`/`pageSize` envelope. `indexer`, `health`, `indexerstats`, and `indexerstatus` are unpaged in this source contract. Paginate history to `totalRecords`; accept unpaged resources only as one bounded response and mark an over-limit read stale/error rather than silently truncating it. [history controller](https://github.com/Prowlarr/Prowlarr/blob/b8d04898207b16070c010c8d74b4cc218ff84b8f/src/Prowlarr.Api.V1/History/HistoryController.cs). | Read only: system status, health, indexer identity/status/statistics, and history/diagnostic summaries, plus a link to Prowlarr administration. Prowlarr remains owner of indexer configuration and credentials. Do not expose Prowlarr search/grab even though its API provides it: its search grab sends a release directly to a download client and would violate the plan's Arr-owned control path. [search controller](https://github.com/Prowlarr/Prowlarr/blob/b8d04898207b16070c010c8d74b4cc218ff84b8f/src/Prowlarr.Api.V1/Search/SearchController.cs). | No Prowlarr instance, indexer configuration, version, or safe redaction mapping is evidenced. |
| qBittorrent | Selected by the household. Its owner-published current WebUI documentation defines `GET /api/v2/app/version`, `GET /api/v2/app/webapiVersion`, authentication, `GET /api/v2/torrents/info`, and `GET /api/v2/torrents/files?hash=…`. [API index](https://github.com/qbittorrent/qBittorrent/wiki), [current API reference](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29), [API-key option](https://github.com/qbittorrent/qBittorrent/wiki/API-Key-Authentication-%28%E2%89%A5v5.2.0%29). | qBittorrent's torrent hash is the native identity. Do not assume an Arr `downloadId` equals that hash; create the mapping only after a real owning-Arr queue/history record and client record prove it, recording Arr server/item, hash, observed category, and time. | `torrents/info` supports `limit` and `offset` but does not have an Arr-style total response. Prefer qBittorrent's documented incremental-sync mechanism; otherwise read stable offset windows and record drift rather than claiming a snapshot is complete. | Admin-only, read-only correlated transfer state/progress/error/details, selected files, and upstream link. Existing local homelab automation already authenticates to qBittorrent WebAPI and reads `torrents/info` and `torrents/files`; that is installation evidence for this surface, not a version/identity qualification. | No copied version, live version probe, Arr-to-hash join, category contract, or access from Lumina has been qualified. The existing local consumer uses cookie login; API-key auth is available only from qBittorrent 5.2/WebAPI 2.14.1, so choose the live-supported auth during setup rather than assuming it. |
| NZBGet | Selected by the household. The owner-published API contract documents JSON-RPC at `/jsonrpc`, `version`, `status`, `listgroups`, `listfiles`, and `history`; it applies to NZBGet 13.0+ and uses HTTP Basic authentication. [API overview](https://github.com/nzbgetcom/nzbget/blob/develop/docs/api/API.md), [listgroups](https://nzbget.com/documentation/api/listgroups/). | NZBGet's native group identity is numeric `NZBID`; use it only with a proven owning-Arr correlation. `NZBName` is display data, not a stable cross-service key. | `listgroups` returns an array rather than a paged envelope. Bound its response, persist a complete-or-truncated outcome, and fail/stale rather than silently discarding groups. Do the same for history/listfiles after reviewing the deployed version's exact response. | Admin-only, read-only status, active groups, group files, and correlated history/error details with an upstream link. No queue editing, pausing, removal, rate, retention, server configuration, or credentials editor. | The authorized deployment manifest declares an NZBGet service, but records neither version nor auth contract. Its API/base path and Arr-to-`NZBID` correlation must be tested before enabling it. |

## Implementation-ready contracts

### Shared observation result

Each connector probe returns a server-owned record with: `connector`,
`base_url` (admin-only), `version`, `capabilities`, `last_ok_at`,
`last_error` (content-free), `observed_at`, and `stale`. It must distinguish
an unconfigured connector from a configured-but-unreachable one. A successful
status call alone is not qualification: complete the smallest resource read and
verify its identity/pagination response before setting a capability true.

All observer records carry `source_server_id`, a native resource ID, observed
timestamp, and a correlation state of `matched`, `unmatched`, or `ambiguous`.
They are never a member-visible media record. Hide unmatched inventory and raw
paths/diagnostics from ordinary members; only a visible registered library file
transitions an album, movie, or episode to playable.

### Lidarr music request

Use a separate durable music-request row with a public music identity,
`lidarr_server_id`, nullable `lidarr_artist_id`/`lidarr_album_id`, desired
profile/root snapshot, request/operation state, and import-correlation state.
On an uncertain external write, store the operation before dispatch, then
reread by foreign artist/album ID, local ID, and history/queue correlation. Do
not retry a search/add blindly. Profiles and roots are configuration for new
acquisitions; moving an existing root remains out of scope.

Lidarr queue data may report `downloadId`, artist/album IDs, progress fields,
and file counts. It is an acquisition explanation, not the authority to serve
a track. The album reaches `playable` only after the normal library scanner
registers currently visible track files.

### Bazarr subtitle action

Keep Bazarr's input as one of the already-correlated upstream item identities
plus requested language, forced, and HI flags. The API writes sidecar files,
so it needs an explicit operational permission and durable action/audit row;
it cannot be modeled as a harmless refresh. Recheck the local library after
Bazarr reports success. A source-generated or translated artifact still follows
ADR 0013 provenance and validation policy.

### Prowlarr and download-client observation

Prowlarr health messages and history `data` can expose upstream details. Store
only a reduced, admin-only reason code/message and an upstream link; never
forward credentials, indexer configuration, paths, URLs, release names, or raw
diagnostic bodies into member APIs. The same policy applies to any selected
download client. Correlation failure leaves a job `unmatched`, not assigned to
the nearest title or made visible to a member.

## Smallest independent delivery slices

1. **Capability probe and persistence seam:** extend the existing bounded
   request pattern with a connector-specific configuration model, a write-only
   key, status probe, staleness, and one paginated-read harness. Do not widen
   the existing Sonarr/Radarr `ArrServer.kind` literal until a migration and
   contracts are owned by the PM.
2. **Lidarr observer and music-request contract:** add a dedicated Lidarr
   client/fake and focused tests for status, roots/profiles, artist/album
   identity, complete pagination, uncertain dispatch reconciliation, and the
   import-to-visible-track transition. This can ship without Bazarr, Prowlarr,
   or a downloader.
3. **Prowlarr observer:** status/health/indexer/indexer-status/stats/history
   reducer with redaction and upstream links. It is strictly read-only and
   needs no indexer or download-client write surface.
4. **Bazarr conditional adapter:** only after a real setup probe confirms
   version/auth and Sonarr/Radarr ID correlation. Start with wanted-state
   observation; add one audited explicit subtitle search/download only after
   the path/import lifecycle is qualified.
5. **qBittorrent and NZBGet observers:** their household selection is now
   known, but run the version/auth and identity-join qualification first. Freeze
   each deployed client contract and implement only its correlated read model.

## Selected Chromecast output: qualification boundary

Chromecast is the selected Phase 5 output, outside this Phase 3 connector
implementation. Google documents that a Web Sender needs HTTPS and that the
Default Media Receiver can load a media URL; load/control interactions have a
request ID and media state. [Web Sender setup](https://developers.google.com/cast/docs/web_sender),
[Default Media Receiver](https://developers.google.com/cast/docs/web_receiver),
[media messages](https://developers.google.com/cast/docs/media/messages).

The first adapter must mint a receiver-scoped, revocable, short-lived Lumina
media ticket instead of sending a browser cookie or raw permanent media URL to
the device. It must not advertise a capability until a real selected device has
demonstrated discovery/session start, load, pause, seek, resume, applicable
audio/subtitles, stop/revocation, and recovery back to the browser session. No
Chromecast deployment, receiver reachability, or device qualification evidence
was found during this connector discovery; no real device was contacted.

## Phase 1 API correction for the observer implementation

The official current source contracts confirm that both Sonarr v3 and Radarr
v3 expose the inventory, queue, history, health, calendar, missing, cutoff, and
status reads required by Phase 1. In particular, Radarr does expose
`/api/v3/wanted/missing` and `/api/v3/wanted/cutoff`; unlike Sonarr, its current
OpenAPI contract has no per-ID `wanted/*/{id}` path. Their queue/history/wanted
endpoints use `page` and `pageSize`, defaulting to 1 and 10, so an observer must
read through `totalRecords` rather than treating a first page as inventory.

- [Sonarr v3 OpenAPI at reviewed commit](https://github.com/Sonarr/Sonarr/blob/2b39049ee6e16e1b326792009ea4b985e29adf48/src/Sonarr.Api.V3/openapi.json)
- [Radarr v3 OpenAPI at reviewed commit](https://github.com/Radarr/Radarr/blob/7879a635dd5ae22e41bbe75dd39b6aadfe2e2358/src/Radarr.Api.V3/openapi.json)

## Verification performed

Read-only repository inspection and source-contract review only. The official
repositories were examined at Lidarr `aec8802`, Prowlarr `b8d0489`, Bazarr
`bfe4593`, Sonarr `2b39049`, and Radarr `7879a63`. No live API calls were made,
no credentials were inspected, and no implementation test is applicable to this
documentation-only slice.
