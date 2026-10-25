# ADR 0010: Connected apps and an opt-in Jellyfin-compatible API

- Status: Accepted
- Date: 2026-09-25

## Context

The household watches equally in Lumina's web UI and in third-party players, Infuse first. Those players speak the Jellyfin API. Agents (scripts, a future MCP server) also need a non-browser credential. Browser sessions carry expiry and a cookie/CSRF contract that fits neither. Jellyfin itself has shipped unauthenticated stream, image and subtitle routes, cross-user reads and a path-traversal bug through a client-supplied format. Its source is GPL-licensed.

## Decision

- **One credential for every non-browser client**: a *Connected app* (`device_tokens`). It is per member and device, has kind `jellyfin` or `agent` and scope `read` or `write`, stores only a SHA-256 digest, and is revocable from account settings. Password change, reset and deactivation revoke it. Jellyfin tokens are minted by login and accepted only on the Jellyfin-compatible API, which is served at the server root (amended 2026-09-28; originally "only under `/jellyfin`"). Agent tokens are created in the UI, shown once, and accepted as `Authorization: Bearer` on `/api/*`. Read-scoped tokens are refused on state-changing methods, and CSRF does not apply to bearer requests. A playback session is scoped to the same (member, device) pair as its Connected app, and revoking that app kills its active streams immediately, not just future requests.
- **The Jellyfin-compatible API is opt-in** (`jellyfin_enabled`, off by default; every Jellyfin route 404s when off). It is advertised as Jellyfin, and Lumina never advertises Emby. (Amended 2026-09-28: served at the server root only; there are no `/jellyfin` or `/emby` path aliases.) It is written clean-room from Jellyfin's published naming docs, the OpenAPI spec and observed client traffic. No code or regexes are ported; only OpenAPI property *names* are committed as a test fixture.
- Every route is authorized and visibility-scoped. A legacy `/users/{uid}/…` route must name the caller. No filesystem path is built from client input. Images need a token or an item-scoped HMAC tag issued only to authorized callers.

## Consequences

- Remote access still goes only through the one TLS proxy (ADR 0001). Stream URLs carry `ApiKey` because Infuse requires it, so proxy access logs must stay off or strip query strings, and the token headers are redacted.
- Any future non-browser surface (MCP) reuses Connected apps instead of inventing a credential.
- The API serves the union of old and new Jellyfin routes and auth forms, which keeps clients working across Jellyfin API churn.

## Amendment (2026-09-28): served at the server root only

Infuse and other Jellyfin apps add a server by host and port, so the Jellyfin-compatible API is served at the server root, for example `http://<host>:8765/System/Info/Public`. It is served there only. Lumina is unreleased, so there are no `/jellyfin` or `/emby` aliases to keep. The routes stay mounted under a private `/jellyfin` prefix. Client requests to `/jellyfin/...` or `/emby/...`, in any case, are refused with a 404. `LocalAddress` and the address shown in Settings are the bare public URL. A root path is Jellyfin's only when its first segment is a first segment of a registered Jellyfin route (derived from the routes at startup, not a hand-kept list). A request carrying a Jellyfin credential (MediaBrowser/Emby `Authorization`, an `X-Emby-*`/`X-MediaBrowser-Token` header, or `api_key`) is Jellyfin's at any first segment except `/api`, so an endpoint Lumina does not serve (`/Genres`) answers a JSON 404 rather than the SPA shell. `/api`, the SPA's assets and every other path are untouched. Where a Jellyfin segment is also a top-level SPA path, a browser page load stays the SPA's (`Sec-Fetch-Mode: navigate`, or HTML first in `Accept` with no Jellyfin credential), and anything else goes to Jellyfin. The switch is unchanged: with it off, every root Jellyfin path 404s. The Decision lines above are amended in place to match.

## Amendment (2026-09-28): importing watch history from a Jellyfin server

Households move to Lumina from Jellyfin, and their watch state should come with them.

- **Who and how.** A member imports only their own history, from Settings → Privacy & data. They sign in to the household's Jellyfin server with their own Jellyfin username and password.
- **The address.** An admin sets the server's address once (`app_settings.jellyfin_import_url`, schema v5). Members never type an address, so the import cannot be pointed at an arbitrary URL. Every signed-in member can see the address. That is fine for a household, but it may reveal an internal IP.
- **Credentials.** The password is used for one request and is never stored or logged. The Jellyfin access token is signed out (`/Sessions/Logout`) when the read ends, however it ends.
- **Network limits.** Requests go only to the admin's address. They never follow redirects, ignore proxy settings, and are capped at 30 s a request, 8 MB a page and 20,000 items. Each member may make 10 attempts per 5 minutes.
- **What is read.** Lumina reads played, resumable and favorite Movies and Episodes, and favorite Series.
- **Matching.** Each entry is matched to a Library item the member can see, first by the longest shared trailing file path of at least two components, then by TMDB/IMDb/TVDB ids. Episodes match by their series' ids plus season and episode numbers, and only when Jellyfin gives both numbers. Anything the member cannot see is never matched or named.
- **Merging.** The merge never loses Lumina data:
  - Newer Lumina progress wins (Jellyfin `LastPlayedDate` against `last_watched_at`).
  - Completed stays completed.
  - An undated resume point is skipped (counted as up to date), so it cannot jump to the top of Continue watching.
  - A Continue watching dismiss is undone only when it is older than the imported play.
  - Favorites are only added.
  - A re-run changes nothing.
- **Preview and Import.** Preview shows the counts first. Import recomputes the same plan inside one write transaction.
- **Not imported.** Play counts and ratings are not imported.

## Amendment (2026-09-28): bringing a household over from Jellyfin

A vault owner can move every Jellyfin user and their watch history over in one step, from Settings → Members → Bring members over from Jellyfin.

- **Credentials.** The owner signs in with their own Jellyfin administrator account; Lumina refuses an account whose `Policy.IsAdministrator` is not true. The password is used for one request and never stored or logged. The one access token reads every chosen user's history and is signed out when the request ends. Members' own Jellyfin passwords are never needed.
- **Address and limits.** The same admin-set address, network limits and rate limit as the member import, plus at most 50 Jellyfin users. The 20,000-item cap applies per user.
- **Pairing.**
  - The signed-in Jellyfin administrator lands on the signed-in owner.
  - Everyone else lands on the Lumina member with the same username (case-insensitive), or a new member.
  - A name Lumina cannot use, or a second Jellyfin user for one Lumina member, is skipped with the reason.
  - Disabled Jellyfin users are listed but not chosen by default.
- **New members** join as household members, never vault owners, and have no password. They cannot sign in until they choose one through a single-use reset link (the existing 24-hour link). Links are shown once, in the result, and never logged. A lost link is replaced from the member's row.
- **Import.**
  - Preview shows each user's counts.
  - Import recomputes the pairing and every plan on the server, using the member import's rules and each member's own visibility.
  - Each member is written in their own write transaction, so one failure never undoes another. The result reports each user.
- **Synchronous.** Preview and Import are single long requests (the web app waits up to 10 minutes), not background jobs, because a job would have to keep the Jellyfin administrator's token alive after the request.

Amendment (2026-10-01, 2.0.0): items and their MediaSources carry `Path`, relative to the item's storage root (for example `/Movies/Arrival (2016)/Arrival (2016).mkv`). A live test showed Infuse cannot browse a library without it ("DataSourceError"). The server's mount path is never sent; the relative path names only folders the member's library already shows. Infuse's observed add sequence also needs `/UserViews/GroupingOptions` and `/Library/VirtualFolders` (the caller's own views, with empty `Locations`).

Amendment (2026-10-04, public exposure): a Jellyfin client's sign-out (`POST /Sessions/Logout`) revokes its own Connected app and ends its streams, as Jellyfin does. `LocalAddress` (in `/System/Info` and `/System/Info/Public`) is the public address for a request that arrived on the public address's host, so the internet never learns the LAN address; LAN callers still get the LAN address.

Amendment (2026-10-07, every API client): Jellyfin apps that talk the API (Roku, Android TV, Swiftfin, Streamyfin, Findroid, Kodi add-ons, Fladder, Infuse) are supported. Apps that only load a server-hosted jellyfin-web (the official phone app, Jellyfin Media Player, a browser) are not, and Lumina never serves jellyfin-web.

- **Errors have no body.** Some apps parse a response body whatever its status, so a JSON error object was read as data and crashed them. Every 4xx and 5xx on the Jellyfin surface keeps its status with an empty body, and validation errors are 400. `/api` is unchanged.
- **Lists never 404.** A list endpoint asked about a missing or hidden parent answers an empty, correctly shaped result. Hidden and missing stay indistinguishable.
- **Endpoints apps call unconditionally** (additional parts, intros, theme media, ancestors, counts, sessions, encoding options and similar) answer real or empty, correctly shaped results instead of 404. Nothing is invented: Lumina has no intros, theme media or extra parts, so those are empty.
- **Untagged art.** Roku's poster views cannot send a token and request some art without a signed tag. For 10 minutes after an authenticated Jellyfin request, an image request from the same client address with neither a token nor a tag is served as that token's member, re-checked on every request (revocation, expiry, visibility). The grant does not slide and covers only titles and library items, whose ids are random. People, views and channel folders still need a token or tag. A wrong tag is never rescued. The same-address stream grant from 2.11.0 (a played item, 4 hours) is the precedent. The residual risk is that someone behind the same address who already knows an item's id can fetch its art. Both grants key on the client address and, for a Cloudflare Tunnel visitor, on the host that actually connected to the proxy too, so a LAN host that forges the visitor header cannot borrow one.
- **`/socket`** accepts an authenticated Jellyfin client only, sends keep-alives and takes no commands.

Amendment (2026-10-10, direct ranges): the two direct-stream routes prepare settings, authorization and the registered file in one endpoint worker. An async, zero-I/O session-context factory dependency lets that worker enter the ordinary session scope itself, preserving its commit, rollback, after-commit drain and close before any response bytes stream while avoiding separate dependency entry and exit worker handoffs. Explicit credentials load the enabled flag, token and member in one live outer join anchored on settings, so a disabled API returns 404 even for invalid credentials. Token expiry, deletion and activity touches share the security validator; caller claims, image grants and access snapshots share the normal Jellyfin resolver. Credential-less playback still checks the enable gate before looking up its sliding, address/item-scoped grant. Every range rechecks authorization and registered-file visibility; no credential cache is introduced.

Amendment (2026-10-12, TV playback launch): file MediaSources include `VideoType: VideoFile`, which Roku 3.2.4 reads without a null check. Movie and episode detail responses identify their preferred source with the parent item's ID, as Android TV's external player requires; additional versions keep their own IDs. This primary alias uses the existing visibility-scoped preferred-version resolver. PlaybackInfo keeps concrete version IDs for subtitles and HLS sessions. `EnableDirectPlay=false` in the body or query removes direct-file profiles from the decision and from the pending HLS session, allowing the client to retry through conversion. A direct-file response does not advertise `SupportsDirectStream`: clients that choose that mode require a `TranscodingUrl`, which only the HLS decision supplies.


Amendment (2026-10-19, native music): the connected-app Jellyfin surface supports explicit `MusicArtist`, `MusicAlbum` and `Audio` item queries, album/artist children, item detail and signed artwork. Music metadata comes from the existing curated track manifests. `GET`/`HEAD /Audio/{id}/stream` serves only registered audio files with the existing member visibility, enable gate, token revocation, screen-time and playback resource checks, including byte ranges. Native clients authenticate on this surface; Jellyfin credentials still cannot access browser `/api/music` endpoints. Audio streams default to the registered format. The optional `format=flac` uses the existing bounded, cached audio conversion service; its file lease lasts through response completion or disconnect. Every converted range still revalidates the connected app, member visibility and registered file, applies screen-time/activity rules and budgets the delivered file through the audio resource policy. Native clients request FLAC compatibility for source containers their player cannot decode. Browser-only music routes remain inaccessible to Jellyfin tokens.

## Amendment (2026-10-20): native music extensions

The opt-in Jellyfin-authenticated surface exposes Lumina-specific music operations at `/Lumina/Music`: saved and smart playlists, mixes and resolved queues, the member listening session, track metadata and lyrics, downloads, and Sonos discovery and transport. These adapters reuse the browser music services and request schemas; they do not allow Jellyfin tokens on generic `/api` routes. Playlist owners and revisions, current track visibility, screen time, download permission and the existing bounded conversion/response lease remain authoritative. Native downloads use the same `download=true` delivery policy as the browser, rather than claiming a playback slot.

Manifest IDs remain database IDs; native requests accept either UUID spelling. Manifest audio and artwork URLs are credential-free same-origin root paths, replacing browser-only URLs. The native client adds its own token only when requesting media. Nested queue and conflict manifests use the same mapping. This namespace explicitly returns JSON revision-conflict snapshots for listening-session and output-transfer conflicts; ordinary Jellyfin endpoints retain empty error bodies.

The existing listening-session `active_output.kind = browser` names a local client for compatibility, including a native client; it does not prove remote playback. Native restoration remains paused and follows ADR 0026's revision/conflict rules. Sonos uses the existing acknowledged handoff, owner-bound expiring capabilities and revocation behavior; speakers never receive a member's Connected-app token.


## Amendment (2026-10-21): importing Plex accounts and video progress

Plex imports use the same visibility-scoped matching, non-destructive merge and per-member account/reset-link transactions as the Jellyfin transfer. The existing normalized history records and reconciliation code are reused; the Plex source adapter translates its own wire data into those records.

An owner configures `app_settings.plex_import_url` (additive schema 33). Members cannot supply another destination. Personal preview/import accepts a request-scoped Plex token and reads that token's movie/episode state. Household preview/import requires a Plex server owner token, pairs the owner with the signed-in Lumina owner and other users by username, and reads each user's state using their own server-sharing token. Owner state must never be used as another member's state. Users without an available token are not imported.

Plex cloud requests go only to fixed HTTPS `plex.tv` endpoints to establish the owner and server shares. Library reads go only to the configured server, never a URL from a source response. Tokens stay in headers, never query strings, storage or logs, and are not revoked because they are existing Plex credentials. Network calls refuse redirects and environment proxies, have 30-second/8-MiB bounds, and cap users at 50 and each user scan at 100 video libraries, 250 pages and 20,000 unique watched or resumable entries; attempts are limited to 10 per member per five minutes. Legacy Plex cloud XML rejects document types/entities. New accounts remain viewers without a password until their 24-hour single-use link is redeemed.

Movies and episodes carry watched state, dated resume positions and provider identities into the existing merge. Plex ratings, play counts, favorites and cloud watchlists are excluded. Preview remains read-only; import recomputes every plan inside its member's write transaction, and one member's read/save failure cannot undo another's account or link.

Protocol references: [Plex Media Server API](https://developer.plex.tv/pms/), [Python PlexAPI source for server-sharing tokens](https://python-plexapi.readthedocs.io/en/latest/_modules/plexapi/myplex.html), and [Plex authentication token instructions](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).

## Amendment (2026-10-21): native Requests extensions

The opt-in Jellyfin-authenticated surface exposes member Requests at `/Lumina/Requests`: catalog discovery under `/catalog`, the member's own request list, quota and create operation. These adapters reuse the browser catalog, request engine and schemas with the Connected-app caller as the member. They never grant a Jellyfin credential access to `/api/requests`, and there are no administrative request decisions on this surface. Request policy, quota, configured-request switch, member visibility and connected-app revocation remain authoritative. This Lumina-specific namespace may return structured JSON errors; ordinary Jellyfin endpoints retain empty error bodies.

## Amendment (2026-10-22): native Home layout

The opt-in Jellyfin-authenticated surface exposes the active member's Home layout at `/Lumina/Home/layout`. It reads and updates only `UserSettings.ui_prefs.home_shelves`, the same preference used by the website; an update preserves every other UI preference. A null layout resets to the website default, while reads sanitize malformed older data against the frozen shelf catalogue. This does not make a Connected-app credential usable on `/api/settings/me` or any other browser API.

## Amendment (2026-10-23): native library alphabet index

Native clients may request compact, member-visible name anchors from `/Lumina/Library/alphabet` for an empty-search, ascending `SortName` video wall. Each anchor is an absolute `/Items` offset under the same view, category and filter predicate, so clients can seek one ordinary page without enumerating the full library. This additive Connected-app endpoint requires a server release that contains it; clients on an older server continue ordinary paged browsing when it is unavailable.

## Amendment (2026-10-22): native member settings and reauthentication

The opt-in Jellyfin-authenticated surface exposes the caller's member settings at `/Lumina/Settings`, using the same settings service and durable preference record as the website. It has explicit, member-scoped paths for personal profile, notification, access, history, export, two-step verification and Connected-app actions; there is no generic proxy to browser `/api` routes and no server or household administration path, including for an owner.

A sensitive native mutation requires a fresh `POST /Lumina/Settings/reauth` proof. That endpoint checks the account's local password and, when enabled, the current second factor. It returns a random proof held only as a hash in process memory, bound to the authenticated Connected-app token, valid for five minutes and consumed once through `X-Lumina-Reauth`. The proof never becomes a browser session, has no authority outside the explicit Settings routes, and is lost on restart. Password and recovery credentials remain request-only and are never stored or logged; changing a password still revokes all of the member's Connected-app tokens as it does in the browser flow.

### Native detail watchlist actions

The Jellyfin-authenticated `/Lumina/Watchlist/{item_id}` adapter reads or sets the caller's membership in the existing website WatchQueue. It resolves movie/episode titles to a visible preferred library version, series to their current play-next episode, and seasons to their first visible episode. It stores the same concrete queue entry as the website rather than creating a separate native list or using Favorites. The adapter is member scoped and does not grant Connected-app credentials access to browser `/api` routes.


## Amendment (2026-10-22): first-owner household transfer

The first account's existing `vault_setup_pending` gate now includes an optional inline household transfer after media-folder setup and before personal welcome. Later accounts and existing installations do not enter this flow. Owners can skip it and use the existing Settings transfer later. The media scan remains durable; transient provider, credential and result state is never written to browser storage. Reload restores media setup and requires source credentials again. Copyable password links are shown with committed results and can be replaced from Members if lost.

The onboarding flow uses `POST /api/admin/household-transfer/{provider}/discover` to authenticate the source owner and pair accounts without reading anyone's history. The owner reviews the proposed account destinations and chooses people before any history scan. The source URL is saved through the existing owner-only, validated media-server settings endpoint; neither transfer body accepts a destination URL. Plex users without a scoped server token are visibly unavailable. The interface can discover people while a media scan runs, but waits for matching media to be indexed before starting the transfer; optional enrichment is not a prerequisite.

`POST /api/admin/household-transfer/{provider}/import` is a finite, request-owned event stream. It authenticates and enumerates the source once, reads each selected user's history once, and reuses the existing per-member transaction and live visibility-scoped merge. Progress names the member being read before that read begins, and each completed row and new password link is emitted only after that member's transaction commits. A later error preserves earlier results. There is no background job, token cache or preview-history cache, and importing does not send notifications to source users. Existing JSON preview/import endpoints remain available to Settings.

The stream owns its database scope and Jellyfin sign-in context, closing both on completion, failure or disconnect; an in-flight bounded source read may finish before cancellation is observed, but no later member is started after stream closure. Streams bypass compression/proxy buffering and use `Cache-Control: no-store`. Existing source byte/time/page/user limits and per-owner attempt limits apply. The browser consumes bounded, incrementally decoded events, requires an explicit terminal event, holds timeout/cancellation through body consumption, retains committed results on a transport failure, and never automatically retries an account-creating request. The owner can stop the transfer and keep received results, retry unfinished people, or discover the same source household again if membership changed. Continue Watching is refreshed after the first-owner setup finishes so imported resumes are immediately available on Home.

Plex reads use server-side watched and in-progress filters and deduplicate overlapping results by rating key. Episode matching fetches only referenced shows in bounded ID batches, sharing the page budget; unrelated movies, episodes and show metadata are not traversed. Referenced show metadata is separately capped at 20,000 records. This keeps transfer work proportional to activity rather than total library size.

### Native external-streaming settings opt-in

External-provider controls in the native app require the persisted admin-owned `AppSettings.native_streaming_enabled` setting, default false. `/Lumina/Settings/capabilities` returns member-effective `streaming_enabled` and `youtube_session_enabled` booleans; unrestricted members and admins do not bypass the server opt-in. This setting changes Connected-app settings visibility, not the browser Streaming surface or local-media playback. Native YouTube-session operations reject disabled or YouTube-blocked callers before reading credential status or consuming a reauthentication proof. Native clients hide these controls for absent, failed, or malformed capability responses, including older servers.

## Amendment (2026-10-24): native admin quick actions

The opt-in Jellyfin-authenticated Settings surface exposes only these narrow household-admin quick actions:

- `GET /Lumina/Settings/admin/library` returns the existing safe library-automation snapshot, and `POST /Lumina/Settings/admin/library/scan` accepts the existing optional `root_id` request and returns the existing scan result with `202`.
- `GET /Lumina/Settings/admin/invites` returns the existing modern email-invite list. `POST /Lumina/Settings/admin/invites` accepts the existing `InviteCreate` contract, keeps the service's viewer-only invitation role, and returns the existing creation result with `201`. `DELETE /Lumina/Settings/admin/invites/{invite_id}` reuses the existing invitation revoke service and returns `204`.

Every route reloads the member and Connected-app rows from the live database and requires an active `admin` role; the transient Jellyfin caller role snapshot is not authoritative. Library scans and invite mutations require a write-scoped Jellyfin Connected app and invite mutations additionally require a fresh, one-use, token-bound `X-Lumina-Reauth` proof. The existing Jellyfin opt-in and token revocation checks remain in force. These adapters do not accept native credentials on browser `/api` routes, mint agent tokens, proxy arbitrary browser endpoints, or add schema. All successful native admin responses use `Cache-Control: no-store`, including creation responses containing the one-time invitation URL.

### Server-driven native navigation

`GET /Lumina/Settings/navigation` returns ordered `categories` identifiers for the member's available Movies, TV, Anime, Music, and Books libraries. It uses the complete catalog and the existing title, track, and book visibility rules, excluding missing tracks and unavailable/deleted books. It returns identifiers only with `Cache-Control: no-store`; Home and Watchlist remain local destinations. Clients refresh this list with Home and clear it on session changes, rather than inferring capabilities from a paginated Home shelf or carrying categories between servers.

## Amendment (2026-10-24): mobile pairing

The web profile menu can hand a Lumina mobile app the address it is currently serving and a one-use pairing secret. The QR payload is compact JSON with exactly `type: "lumina-pair"`, `version: 1`, `server_url`, and an opaque token. It never includes an account password or a Connected-app token. Pairing records keep only the token digest, expire after five minutes for a member's own code or fifteen minutes for an owner-shared code, and are atomically consumed before an app password is minted. The resulting app signs in through the existing Jellyfin-compatible authentication flow and receives its own revocable Connected-app token.

Every browser-session member can create their own code. A vault owner capable under the household's required two-step policy can create a share code for an existing active member, optionally emailing a PNG QR and `lumina://pair#…` fragment deep link to an explicit recipient. SMTP failure leaves the downloadable code available and reports only that delivery failed. A code is permanently invalid after its target or issuer changes credential, role or active state; a shared code also requires its issuer to remain owner-capable at claim time. Replacing or closing a personal code revokes it. The address is a validated bare HTTP(S) origin supplied by the current browser request, so a LAN request pairs the LAN address; remote use still requires that address to be externally reachable or available through a VPN.
