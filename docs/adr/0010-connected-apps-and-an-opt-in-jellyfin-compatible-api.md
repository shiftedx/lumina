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
