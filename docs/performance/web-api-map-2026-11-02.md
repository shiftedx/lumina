# Web UI → backend API performance map (2026-11-02)

## Scope and method

This is a read-only source audit of the React/Vite web application.  It maps the
application transport (`frontend/src/api.ts`, 1,152 lines), specialised media
transports, request lifetimes, and known background traffic.  It deliberately
does **not** assert production latency, throughput, cache-hit rate, or failure
rate: no browser trace or production telemetry was collected for this report.
Those are marked *measurement required* below.

The accompanying [machine-readable inventory](web-api-inventory-2026-11-02.json)
uses the installed TypeScript compiler API to identify **323 direct transport
call sites** in exported `api.ts` declarations: 310 ordinary wrappers, four
transport helpers, six symbolic static paths, and three path builders. It also
records 13 direct browser-network sites, three explicit URL builders, five
unresolved dynamic-path authorities, and zero `api.ts` re-exports. Separately,
208 frontend files import `api.ts`, nine production files contain a true direct
network primitive after manually excluding `prefetch*` identifier false
positives, and 24 non-test files contain an interval or `usePolledSnapshot`.

Call-site count is an upper bound on distinct routes: conditional wrappers can
have multiple entries, multiple wrappers can reach one route, and template
values/query values/server redirects cannot be fully expanded statically. The
inventory excludes type-only exports and non-network helpers, and deliberately
does not enumerate browser-managed image/video/audio/HLS/DASH/artwork
subrequests. This map covers browser-side calls under `frontend/src`; it
excludes native clients and server-to-server calls.

The inventory was generated from source revision
`a5e9329f32b9c6f1fb91f7a42b023dcd035a9be4`. Its frozen extractor is
[`web-api-inventory-extract-2026-11-02.mjs`](measurements/web-api-inventory-extract-2026-11-02.mjs): run it from repository root and redirect stdout to the inventory
path. The extractor records `requestJson` as the dynamic-method transport helper
at its actual `fetch` (`api.ts:189`); its recursive CSRF retry is deliberately
not counted as a second endpoint declaration.

Relevant constraints are ADR 0008 (guarded remote HLS), ADR 0020 (ordinary
authenticated HTTP counts as application activity, except the passive library
activity read), and ADR 0024 (admission/delivery budgets). In particular,
unnecessary authenticated polling is not just request overhead: it can defer
optional media backfill work under ADR 0020.

## Transport contract shared by most JSON calls

`requestJson` is the default transport (`frontend/src/api.ts:176-254`). It:

- uses cookies (`credentials: 'include'`), a per-request `AbortController`, and
  a 20-second default deadline;
- forwards a caller abort signal, adds JSON content type except for `FormData`,
  and sends the in-memory CSRF token for mutations;
- parses JSON errors, preserves `Retry-After` on 429, and retries **once** only
  when a mutation is rejected for CSRF after refreshing `/api/session/me`;
- has no generic retry, exponential backoff, request coalescing, priority
  scheduling, or offline queue; and does not itself send conditional-validation
  headers or maintain an HTTP response cache (this does not establish what any
  particular server route supports); and
- gates explicitly marked Watch side reads through `backgroundReady()` so they
  do not contend with the first playback frame (`api.ts:150-180`).

This is an intentionally conservative mutation model. A future read policy
must not indiscriminately retry writes or make stale authorization decisions.

## Endpoint inventory by workflow

Paths use `{id}` for URI-encoded dynamic values. `GET` is inferred from
`requestJson` without a method; mutations name their explicit method. The
source reference is the wrapper/caller rather than a backend implementation.

| Family / caller | Method and endpoint patterns | Trigger, fan-out, lifecycle |
| --- | --- | --- |
| Bootstrap and authentication | `GET /api/health`, `/runtime-health`, `/bootstrap/status`, `/session/me`, `/session/device-members`, `/session/oauth/providers`; `POST /bootstrap/admin`, `/session/login`, `/session/oauth/{provider}/start`, `/session/two-factor`, `/session/logout`, `/session/switch`, `/session/forget`; `PUT /session/me` | Start/sign-in/member switch. Session reads refresh the CSRF token and session user identity (`api.ts:267-291`). No polling in wrapper. Public showcase has an 8s deadline (`:287`). |
| Personal account, access, connections | `GET /me/access`, `/me/export`, import statuses, `/connected-apps`, `/mobile-connect`, `/mobile-connect/{id}`, `/me/two-factor`; associated import, token, connection, mobile-connect and 2FA mutations | Settings/account and enrolment only. Import preview/run calls carry longer explicit timeouts (`api.ts:295-312`, `:1030-1055`). Mobile-connect UI separately polls its code while open (`features/mobileConnect/MobileConnectDialog.tsx:78`). |
| Discovery / Streaming | `POST /youtube-search`, `/source-search`, `/channels/resolve`, `/discovery/channels/search`, `/onboarding/complete`, `/onboarding/skip`; `GET /discovery/popular`, `/discovery/live`, paged `/discovery/{popular,live}/{category}?limit=40&cursor=`, `/channels/youtube/{id}`, `/discovery/interests`, `/discovery/home`, `/discovery/channels?keys=`, suppressions; interest/suppression mutations | Streaming surface polls popular and live snapshots every 30s while visible; subscriptions surface also polls live. Channel page allows 20s for cold provider resolution. Walls page at 40 and cancel via signal (`api.ts:446-492`; `LuminaApp.tsx:638-649`; `features/channels/ChannelPage.tsx:68`). |
| Home recommendations and feedback | `GET /discovery/home`; `POST /discovery/up-next`, `/discovery/suppressions`, `/reco/events`; `DELETE /reco/history`, suppression | Home polling stops after a settled recommendation snapshot; `/up-next` repeats its POST every 5s for up to 90s while server reports refreshing (`api.ts:468-488`). Impressions/opens batch at 30s and `pagehide` via beacon (`features/reco/recoEvents.ts:1-94`). |
| Search and command palette | `GET /search?q=&limit=&scope=`, `/search/history`; search history mutations | User input only, with caller-owned debouncing/cancellation. `recordSearchHistory` is deliberately fire-and-forget (`api.ts:503-508`). |
| Library, collections and downloads | `GET /library` (cursor/filter page), `/library/{id}`, `/collections`, `/collections/{id}`, `/jobs`, `/acquisition-batches?limit=8&include_entries=true`; collection/library/job/acquisition mutations | Gallery wall starts at 60 records then 120, serialises one page request and aborts when unobserved; retains five query stores for back navigation (`features/gallery/wallPages.ts:13-188`). Downloads and jobs have surface-specific polling. |
| Titles, gallery and home media | `GET /titles/{id}`, paged `/titles`, `/titles/{id}/episodes`, `/similar`, `/facets`, `/episode-summaries`, `/key-scenes`, `/home/new-in-library`, `/home/title-rows`, `/library/sections`, `/library/channels`; watched/favourite/next-up/dismiss mutations | Title details are coalesced in a 30s/50-entry promise cache. Desktop hover/focus warms detail after 150ms and prefetches a backdrop (`features/gallery/titleCache.ts:1-51`, `PosterCard.tsx:43-60`). Library section counts use per-member localStorage immediately, then refetch on mount and each foreground transition (`LibraryTabs.tsx:35-75`). |
| Video playback, Watch, remote/live | `GET /library/{id}/playback-options`, `/playback`, `/playback/remote/{source}`, `/chat-replay/{source}`, `/live-chat`, `/live-recordings`; `POST /library/{id}/playback-sessions`, remote/progress/chat/recording mutations; `DELETE /playback-sessions/{id}` | Playback options/session allow 30s. The browser keeps a 60s, 50-entry option promise cache and may start one speculative conversion, guarded cross-tab (`playbackPrefetch.ts:1-120`). Watch panels defer notes/tags/transcripts/segments/mutes as background reads. Live viewer and chat/recording status use dedicated polling. |
| Books | JSON book metadata/chapter/progress endpoints in `features/books/booksApi.ts`; binary `GET /api/books/{id}/pages/{index}` | Reader fetches a bounded current/next page window at high/low fetch priority with `cache: 'no-store'`, AbortController cancellation and response byte/type checks (`booksApi.ts:19-29`, `BookPages.tsx:1-75`). |
| Music | `/music/queue`, tracks, lyrics, videos, enrichment, editions/discography, playlists/mixes, casting/Sonos/writeback endpoints | UI-specific metadata calls; native `<audio>` owns range streaming. WebAudio fallback fetches complete audio only when required. Music cast status polls while casting (`features/music/MusicPlayer.tsx:139`). |
| Photos | `/photos`, `/photo-albums`, `/photo-people`, `/photo-faces`, `/admin/photo-sources` CRUD/query routes; raw `PUT /photos/import?filename=` | Photo content is normal image/media URL loading. Upload intentionally uses XHR for upload progress, with cookies/CSRF and 120s timeout (`photoApi.ts:153-193`). Active source scans poll at 3.5s only while visible (`PhotoSources.tsx:60-69`). |
| Requests, imports, automations and admin | `/requests`, `/admin/imports`, `/automations`, `/follows`, `/admin/activity`, `/admin/backups`, `/admin/media-server`, `/admin/library/automation`, `/admin/metadata`, `/admin/tasks`, resource/settings/diagnostic endpoints | Admin/settings pages mount these on demand. Request badge polls pending count every 30s for admins (`LuminaApp.tsx:1041-1045`). Long external tests/imports use explicit 30–300s deadlines; finite household transfers use a bounded, parsed SSE-like fetch stream (`onboardingTransferApi.ts:49-110`). |
| Metadata editing and artwork | `/metadata/*`, `/titles/{id}/metadata*`, `/titles/{id}/images/*`, `/admin/titles/{id}/identify`, `/admin/metadata/*` | Editor only. File/image raw uploads use 60s request deadlines; metadata conflict handling exposes the 409 body (`api.ts:1060-1120`). |
| Server navigation and event invalidation | `GET /api/navigation` with `cache: 'no-store'`; `EventSource /api/events` | Navigation runs on auth/member change, every 30s while visible and on foreground (`app/useServerNavigation.ts:16-41`). SSE receives job/library/settings/model events and delegates refresh state to its consumer (`api.ts:824-851`). Browser reconnect semantics are EventSource defaults; this wrapper adds no explicit backoff or last-event-id policy. |

### Special data-plane routes outside `requestJson`

| Source | Method/path | Lifetime and resilience |
| --- | --- | --- |
| Shared transport | `navigator.sendBeacon` to `/api/reco/events` and `/api/metrics/client` | Best-effort pagehide loss is accepted; token is embedded in beacon body because headers cannot be set (`api.ts:496-499`, `:1001-1004`). Both buffers are capped at 500 and flush 200 items/30s. |
| Backup download | `POST /api/admin/backups/{name}/download` | Direct `fetch`, buffers entire response as a Blob (`api.ts:766-784`). Comment explicitly calls out a future disk-streaming threshold. |
| Local video error probe | `GET /api/library/{id}/media` with `Range: bytes=0-0` | Error-only access diagnosis after a direct video error; fire-and-forget and no timeout (`localPlayer.tsx:436-440`). |
| Book pages | `GET /api/books/{id}/pages/{index}` | Bounded reader prefetch as above; explicitly bypasses browser cache. |
| Music stream/audio/offline | `HEAD` then media-element range requests, or `GET` as a WebAudio/offline fallback | Every direct audio fetch is cancellable; native audio avoids reading the whole track into JS (`musicStream.ts:37-76`, `musicEngine.ts:262-265`, `musicOffline.ts:171-188`). |
| Household transfer | `POST /api/admin/household-transfer/{provider}/import` accepting event stream | One fetch with 120s call deadline, byte/event bounds and one CSRF refresh retry; caller cancellation propagated (`onboardingTransferApi.ts:49-110`). |
| Photo import | `PUT /api/photos/import?filename=` via XHR | Upload-progress required; timeout/cancel/errors are handled but no CSRF refresh retry (`photoApi.ts:153-193`). |
| Artwork and media element URLs | `<img>`/`<video>`/`<audio>` URLs generated by `Artwork.tsx`, `api.ts:562`, music/book modules | Browser controls connection, range and HTTP cache policy. These are not observable through the JSON wrapper; response/cache headers must be measured at the network edge. |

## Recurring traffic and cancellation

`usePolledSnapshot` is the standard: immediate load, 30s interval by default,
skip while hidden, one catch-up on visibility, and a `settled` opt-out
(`app/usePolledSnapshot.ts:1-33`). It does **not** abort an in-flight load on
unmount and does not serialize overlapping loads; `isCurrent` prevents stale
state writes but not duplicated backend work.

Known pollers: navigation (30s), streaming popular/live (30s), home
recommendations while warming (30s until settled), admin pending requests (30s),
recommendation metrics/events (30s), admin activity/download/request lists
(their local configured cadence), live viewers/chat, music cast status (5s),
downloads/acquisition (3s), photo source scanning (3.5s), and active photo
surface refresh (4s). Most explicitly check document visibility; metrics,
recommendation batching and local conversion heartbeat are background timers,
not polling reads.

There are valuable existing coalescing/caching controls: title detail promises
coalesce for 30s; playback decisions coalesce for 60s; wall pages are serialized
and aborted without subscribers; book pages cancel on reader/visibility change;
gallery title hover waits 150ms; and several page loaders use AbortController.
They are local to their workflows—there is no cross-feature request registry.

## Bounded browser request census

No new Playwright test was added for this audit, so this is a **static
lower-bound census**, corroborated by the existing mocked Home workflow. It is
not a timing benchmark and does not include browser-controlled image/media
subresources. The existing mock test proves that only near Home shelves load,
that `new-in-library` is fetched once, and that Recommended and Because-you-
watched share one `title-rows` request (`frontend/e2e/home.spec.ts`, "only near
shelves are requested until the member scrolls").

| Workflow / bounded trigger | Immediate request lower bound | Subsequent bounded requests | Notes |
| --- | ---: | --- | --- |
| Authenticated page bootstrap | 2 (`runtime-health`, `session/me`) then 5 in parallel (`jobs`, `library`, `settings/me`, `automations`, `playback/continue`), plus one SSE connection | Navigation, interests, suppressions and search history mount from `LuminaApp` as applicable | `workspace.ts:1096-1121`, `:1294-1335`; the app should instrument the exact surface-dependent total rather than treating this as a steady-state count. |
| Home, first viewport with ordinary shelves | 0–2 additional reads for visible `new-in-library` and shared `title-rows` | one per visible special shelf: Next Up; anime title page; album title page; collections list plus up to four collection detail reads | Home creates a visit-level shared promise for arrivals and title rows (`shelfSources.ts:40-45`); intersection observation defers far shelves. |
| Library wall | 1 title page initially (60), then 1 at a time (120) only for the viewed gap | Revalidation may enqueue page one plus loaded pages overlapping current viewport | `wallPages.ts:13-188`; client aborts its sole pending request when no subscribers remain. |
| Open local Watch item | 2 parallel reads at route preparation when item is not already complete (`library/{id}` and `/playback`); then one playback-options decision when player mounts | Optional panel calls: segments, recap, mute ranges, subtitle tracks, notes/tags, transcript pages, queue/up-next; media element requests are browser controlled | `LuminaApp.tsx:969-1020`, `localPlayer.tsx:184-190`, `WatchSurface.tsx:469-523`. These are intentionally conditional, not an all-at-once count. |
| Visible 30s steady state | 1 navigation refresh; plus only mounted `usePolledSnapshot` users (Streaming popular/live, warming Home recommendations, admin request badge, individual surfaces) | Hidden tab skips `usePolledSnapshot` ticks and performs one catch-up on visibility | `usePolledSnapshot.ts:12-33`; navigation uses a separate 30s visible timer. |
| Hidden → visible | 1 navigation refresh unconditionally for signed member | one catch-up for each mounted poller that missed ticks; Library sections also refreshes on every foreground transition | This is a potential short burst, particularly after a long hidden interval; requests are not coordinated across features. |

The next measurement step should be a reusable Playwright `page.on('request')`
census fixture layered on the existing mocks. It should capture method/path,
surface, trigger (mount, viewport, explicit action, interval, foreground), and
whether prior requests were in flight. It must exclude artwork/media or count
them in a separate resource bucket, and it must not report mock completion time
as backend latency.

## Ranked opportunities

Priority ranks expected leverage from source structure, **not measured savings**.
Each should be instrumented and benchmarked before adoption.

1. **P1 — Make polling single-flight and abortable after measuring the debt.**
   `usePolledSnapshot` calls `run()` regardless of an earlier unresolved request
   (`app/usePolledSnapshot.ts:20-28`), while `isCurrent` only drops an obsolete
   UI result. The ordinary 20-second `requestJson` timeout is shorter than the
   default 30-second polling cadence, so that cadence alone does **not** prove
   overlap. Re-entry from visibility/key changes, manual refreshes, and callers
   with longer/custom deadlines can still overlap or leave work running after
   unmount. Measure that first; if material, add one controller/in-flight guard
   per key, cancel on cleanup/key change, and collapse an overdue tick to one
   trailing refresh. Do not retry mutations through this mechanism.

2. **P1 — Evaluate event-driven invalidation for duplicated control-plane
   polling.** The app opens `/api/events` (`api.ts:824-851`) yet maintains
   fixed-interval reads, especially `/api/navigation` every 30s
   (`useServerNavigation.ts:34-41`) and admin/download/status views. Measure
   event coverage and request volume before extending the event contract with
   monotonic revisions or scoped invalidations, then refetch affected snapshots
   once. Retain low-frequency visibility/reconnect reconciliation because SSE
   can disconnect. If justified, this also reduces ADR-0020 activity-gate
   interference.

3. **P2 — Evaluate cache validation and bounded freshness on read-mostly snapshots.**
   `requestJson` always fetches JSON afresh; navigation deliberately uses
   `no-store` (`api.ts:1152`), library sections refetch on every foreground
   (`LibraryTabs.tsx:55-75`), and discovery popular/live has no shared cache.
   Measure payload bytes, invalidation frequency, and server support before
   adding ETag/revision + `If-None-Match` (or a revision cursor) to navigation,
   sections, discovery snapshots, and title rows. Keep auth/access, playback
   decisions, mutations, and active job status fresh. A 304 must not reset UI
   state or discard the last known good snapshot.

4. **P1 — Create a shared query/cache registry for duplicate mount-time reads.**
   The per-feature title and playback caches demonstrate safe promise
   coalescing (`titleCache.ts:15-31`, `playbackPrefetch.ts:84-107`), but most of
   the other 300 wrapper declarations do not share in-flight reads. A tiny
   member-scoped query registry keyed by method/path/body, with caller reference
   counts and abort only after the final subscriber leaves, would eliminate
   duplicate `get*` requests during route transitions/overlapping panels.
   Do not apply it to mutation, SSE, media, or privacy-sensitive member changes.

5. **P1 — Adapt high-frequency job polling to state and `Retry-After`.** Photo
   sources use 3.5s (`PhotoSources.tsx:60-69`) and playlist acquisition 3s;
   others have their own loops. Use SSE job events first, exponential/jittered
   fallback when disconnected, and a server-provided retry hint. Preserve an
   immediate refresh after an explicit user action. This lowers herd behaviour
   across household tabs and makes background failures less noisy.

6. **P1 — Avoid overfetching and make payload contracts explicit.** Gallery
   pagination is good (60/120 serial pages), but discovery, title detail and
   home snapshot contracts have no documented field/profile projection in the
   web caller. Capture response bytes, row/card count and JSON parse time per
   route; then introduce list-card projections, limits and cursors where payload
   evidence justifies it. Never use this to remove fields required for member
   visibility or playback action decisions.

7. **P2 — Give non-JSON fetches the same deadline/cancellation observability.**
   The diagnostic range fetch in `localPlayer.tsx:439` has neither deadline nor
   caller abort. Backup export buffers a full Blob (`api.ts:766-784`). Add a
   small direct-fetch helper for deadlines, error normalization, correlation
   metrics and cancellation, while retaining streaming bodies. For backup,
   prefer a streamed save API when supported. Measure backup-size distribution
   before prioritising the latter.

8. **P2 — Make best-effort telemetry delivery more loss-aware.** Metrics and
   recommendation events drop a chunk on failed POST (`perfMetrics.ts:55-57`,
   `recoEvents.ts:77-79`); beacons can also be rejected. That is a valid privacy
   and UX choice, but it biases performance/recommendation evidence during poor
   connectivity. Report enqueue/drop/beacon-refusal counters locally, and
   consider a small session-only retryable buffer with strict age/size limits.
   Do not persist user behaviour across logout without an explicit privacy
   decision.

9. **P2 — Use priority/concurrency budgets for speculative work.** Hover title
   prefetched detail/art and speculative playback each have bounded policies,
   but there is no coordinator across artwork, title cache, book lookahead and
   playlist/reader prefetch. A unified foreground-vs-speculative budget could
   cancel queued speculative fetches when a user clicks, improving first-action
   latency on constrained devices. Validate using first-screen/first-frame
   metrics already recorded in `perfMetrics.ts`.

## Existing evidence and proposed measurement plan

Existing source contains browser performance instrumentation: 500-sample,
30-second client metric batching (`frontend/src/perfMetrics.ts:1-57`), visual
wall timing assertions in `frontend/e2e/realstack/perf.spec.ts` and
`home-images.perf.spec.ts`, and backend performance contracts in
`backend/tests/test_library_watch_performance.py` and
`backend/tests/test_jellyfin_performance.py`. These are useful regression
signals, not production RUM results and not a complete API budget.

Committed historical measurements provide context but not a substitute for this
API census: [direct startup warm](measurements/direct-startup-warm-2026-10-10.json),
[remote recovery](measurements/remote-recovery-2026-10-11.json), and
[HLS absolute seek](measurements/hls-absolute-seek-2026-10-10.json). Their
workloads predate this audit and should not be used to infer current endpoint
latency or polling overlap.

Before changing behaviour, add a privacy-safe per-route dashboard or sampled
test trace that records: request count by visible surface; in-flight max;
queue/wait, TTFB and total duration; status/abort/timeout/retry category;
response bytes; 304/cache/coalesced outcome; and server activity-gate impact.
Exercise cold start, route change, member switch, hidden/foreground, server
restart/SSE reconnect, slow network, and playback concurrently with admin jobs.
Set budgets from that baseline, then add focused regression tests for the
chosen strategy.
