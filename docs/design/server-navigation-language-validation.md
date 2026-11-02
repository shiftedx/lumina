# Server navigation and language-specific requests

Implementation branch: `codex/server-driven-media-requests`. This integrates the approved web work and native Home/navigation changes onto the current application base without changing the prototype branches. The owner subsequently authorized the production server rollout recorded below. No native release was uploaded.

## Packet ownership and status

| Packet | Owned files / boundary | Status |
| --- | --- | --- |
| Primary integration | App wiring, shared hook lifecycle, settings preference integration, final review and delivery | Integrated and verified |
| Backend contracts | Navigation endpoints/service, request engine/catalog/sync, additive schema and rollback contracts | Integrated; focused regression checks pass |
| Web surfaces | Drawer/tabs, Home, requests, palette/providers, focused browser fixtures | Integrated; desktop/phone workflows pass |
| Native surfaces | Core response/DTO consumption, iOS/tvOS navigation and request views, isolated native proof fixtures | Builds and focused Core checks pass; UI evidence limitations below |

## Capability matrix

| Capability | Server contract | Web | iOS / tvOS |
| --- | --- | --- | --- |
| Movies, Shows, Anime | Enabled sources, usable catalog items and member visibility | Drawer, mobile, library tabs, Home, search and direct browse routes | Supported library categories filtered by shared response |
| Music | Usable member-visible audio | Dynamic destination, Home/search; browsing preference retained | Dynamic library category |
| Books | Available member-visible books | Dynamic destination, Home/search; active reader preserved | Dynamic supported library category |
| Saved YouTube and recordings | Independently populated saved catalog; no dependency on online Streaming switch | Separate library lenses and Home content | No unsupported destination introduced |
| Photos | Available visible managed photos or enabled external source/root | Dynamic destination/search | No unsupported destination introduced |
| Streaming | Explicit server opt-in intersected with member provider grants | Destination, live/remote Home shelves, provider context and search gated | Existing supported external destination/feed gated |
| Requests | Enabled server feature and existing member policy | Destination and direct route gated | Destination gated |
| Member/server changes | Authenticated no-store response; member isolation | Fail closed during initial load/member switch, refresh on navigation/foreground and visible 30-second interval | Fail closed, refresh on foreground/settings/account changes |
| Existing tracks | Stored current artifact probe plus recorded sidecars, per file | Detail shows audio/subtitles and unknown/partial/truncated evidence | Same-file evidence on request details |
| Alternate language | Exact normalized audio/subtitle tuple; regional tags retained | View existing title and request another language | Same alternate-language workflow |
| Approval | Manual review unless an explicit compatible anime profile exists | Owner amendments retain omitted fields; explicit null clears | Typed DTOs preserve legacy and exact fields |
| Availability | Matching actual file tracks and proven episode coverage | Reflects truthful request status | Same server-owned status |
| Playback, offline downloads, settings | Existing permissions and real providers | Existing player/reader ownership retained; manual URL flow remains separate | Offline entry and playback remain available |

No transient storage-root online status is used to flicker known catalog destinations. Disabled roots and missing artifacts do remove their contribution. A null subtitle preference means no subtitle requirement, not a requirement that subtitles be absent. Evidence is bounded to 500 file rows and does not run probes or read media during detail requests.

## Validation

Focused checks only, following the owner's dogfood testing instructions; no full gate was run.

- Backend final navigation/request contracts: 102 passed. Final language/catalog/schema and documented rollback/re-upgrade checks after metadata hardening: 53 passed. These runs overlap and must not be summed.
- Final integrated schema/language/navigation rerun: 34 passed (11 existing SQLAlchemy/dependency warnings).
- Web navigation, shell, library tabs, request UI, Home visibility, palette and provider context: 69 passed across 7 files.
- Retained Home, shell, accessibility and recommendation suppression contracts: 52 passed across 4 files.
- Desktop and phone Playwright workflows: 2 passed. Each hides unavailable navigation, shows Japanese audio with English subtitles, and submits a separate English-audio request.
- Frontend typecheck passed. Production build passed including licenses and CSS budgets (81.9 KB gzip / 82 KB budget); existing large-chunk warnings remain.
- Native focused Core navigation: 1 passed; Requests DTO/contracts: 5 passed.
- Generic iOS and tvOS simulator builds and signed build-for-testing targets passed. TV navigation workflow passed (1/1, 53.696 seconds): Requests off/on, populated Music, and removal of the selected Music library returning Home.

Browser captures are in `output/server-navigation-language/`: `navigation-desktop.png`, `navigation-phone.png`, `language-desktop.png`, `language-phone.png`. Native logs, xcresults and captures are in `output/server-media-native/`.

## Operational and evidence limits

Schema 43 adds nullable audio/subtitle preference columns. Upgrade revalidates old language-specific available/partial requests instead of trusting historic manager state; existing manager bindings are retained without redispatch. The operator guide records additive rollback/re-upgrade handling. Older rollback images do not understand exact language requests.

Unprobed or malformed metadata stays unknown. Automatic searches are conditional on the configured anime profile; a manager may replace an existing version and cannot promise parallel language editions. Other exact combinations use owner/manual fulfillment. Manual TV fulfillment remains approved until authoritative season/episode coverage can be observed; track evidence from one episode does not establish the whole series.

Native UI evidence limits: iOS XCTest did not start because CoreSimulator failed with Mach error -308. The TV language workflow reached every body assertion, displayed Japanese audio with English subtitles, and verified a newly persisted English-audio request, then stalled during app-termination teardown; it is not a formal XCTest pass. `tv-navigation-retry.xcresult` is the passing navigation result, `tv-retry.xcresult` is incomplete language evidence, and `tv-live.png` shows the same-file language row. The external-source fixture was unpopulated, so its absence does not independently prove hiding of an already-populated native external rail. Owned disposable simulators and broker processes were cleaned up. No physical-device or release claim is made.

## Shared header search follow-up

All existing contextual search controls now mount in the Home/global header search position: Movies, Shows, Anime, Music albums/artists, Requests, Streaming, Books, Photos and Settings. Their existing query state, service calls, history updates and settings draft guards remain owned by their surfaces. Desktop exposes the scoped field; phones expand it within the header and restore focus to the search trigger on close. Command-K / Control-K and slash use the current contextual control before the global palette. Views without a scoped field retain the global search trigger.

The owner's follow-up supersedes the earlier manual-link visibility exception: Add Link is hidden from the header, Browse menu and palette unless server Streaming is enabled, while retaining the member access check. The link-mode command is also guarded when disabled.

Focused follow-up checks:

- Header, shell, GalleryWall, Requests and palette: 95 passed across 5 files.
- Books, Photos and Streaming: 42 passed across 3 files.
- Settings search plus shell after the shared Settings slot update: 17 passed across 2 files. These overlap the other runs and are not additive.
- `header-search.spec.ts`: 2 desktop/phone workflows passed, covering scoped Shows and Requests queries, Settings lookup, shortcut targeting, phone close/focus restoration and server-off/on Add Link visibility.
- Production build/typecheck, licenses and CSS gate passed; total CSS is 82.0 KB gzip within the existing 82 KB budget. No budget increase. Existing large-chunk warnings remain.

Screenshots: `output/header-search/{shows,requests}-{desktop,phone}.png` and `settings-dark-{desktop,phone}.png`. Build log: `output/header-search-build.log`. This follow-up was initially source-only; its production deployment is recorded below.


## Production rollout and navigation recovery

Production application revision: `b90a5532731a30ec427e7ace6404ffebce79e14b`. The spotlight count now appears at the bottom-right of its artwork, outside the account/header area; normal poster badges retain their placement. The desktop/phone badge regression passed before and after scrolling (2 workflows).

The initial rollout exposed a navigation performance regression on the production-scale catalog: a correlated OR query timed out, causing optional destinations to fail closed and appear empty. The prior working application was restored without deleting data; the additive schema was stamped back to 42 while retaining its new columns. The corrected query follows indexed title/item links. A restricted/unrestricted 1,500-series regression fails against the former query and passes against the fix; focused native/web navigation contracts passed (5 tests). A separate 20,000-series synthetic call took 0.070 seconds locally. The corrected function returned the actual production navigation in 0.040 seconds before final deployment.

Final rollout evidence:

- Complete backend/frontend application source deployed over a verified unchanged runtime: dependency locks, entrypoint and runtime support programs matched the previous image. 398 application files hash-verified.
- Private database/key backup retained. A disk-backed isolated copy passed schema 42 → 43 and focused integrity checks on the migrated settings/request tables before the live upgrade. Full-catalog integrity scans were stopped in favor of those focused checks; no full-database integrity result is claimed.
- Compose configuration changed only the image. Environment values, mounts, startup configuration, host settings, networks and proxy labels verified unchanged. Environment order is insignificant and is compared as a mapping.
- Live container healthy, zero restarts. Public Home/video/Shows/Requests/settings/manage/health routes returned 200; unauthenticated navigation, Requests quota and session routes returned 401. Nine public entry assets matched the build hashes.
- Authenticated production browser proof confirmed the populated hero, actual library cards and all configured destinations, hidden Streaming/Add Link, and the featured-series badge below the header. Complete workflow: 1.376 seconds. Its temporary verification session was removed.
- All 25 retained rollback pins stopped. Owned disposable migration database copies removed; actual backups and rollback images retained. Existing canary/review containers, media mounts and native release packages were untouched.

Ignored proof files live under `output/production-rollout/`: `verification.json`, `public-verification.json`, `browser-verification.json`, `navigation-production-timing.json`, `production-all-video.png` and `production-libraries-restored.png`. These screenshots contain owner library content and are not committed. Local synthetic badge captures are under `output/badge-containment/`.

## Anime and saved YouTube visual parity

Anime now uses the same opening artwork stage as Movies/Shows, preserving mixed film/series results, markers, filters, sort, selection and detail navigation. Its grid no longer repeats the older featured-tile layout below the hero. Saved YouTube videos and Channels open with actual fetched artwork: Play uses the exact featured saved item, and Browse uses the existing channel route when Streaming is on or the local saved-channel filter when it is off. Empty/error states retain their existing recovery controls without a fabricated hero. Recordings/audio retain their existing layouts.

Phone Videos/Channels controls remain together, with sort/source controls in a separate row. The shared header search, permission checks, paging, queue/delete controls and dynamic feature availability remain intact.

Focused checks: 58 unit tests across GalleryWall, StillWall, ChannelsView and retained library UI; six existing Channels browser/axe checks; two final desktop/phone cross-view browser workflows, including Anime filters and saved-channel browsing with Streaming disabled. Typecheck passed. Final Vite production output, license generation and CSS gate passed within the existing 82 KB budget. No backend/schema changes.

Six inspected image captures are in `output/video-browse-overhaul/`: Anime, YouTube Videos and YouTube Channels at desktop and phone sizes. Tests use existing licensed open-film art; production uses member-visible stored library artwork. Production review rollout is recorded in ignored `output/video-browse-overhaul/deployment/` evidence.

The Anime/YouTube update is deployed to the production review server at `c222b7b102eaa3220fea3970fb9e6abe1ae7802b`. The backend was verified byte-identical to the previous working release, and schema 43 was retained without migration. The container is healthy; all 26 rollback pins remain stopped. Source hashes matched 397 application files, public routes/assets passed, and an authenticated production browser confirmed populated Anime, saved-video and saved-channel heroes, the complete configured library navigation, hidden Streaming/Add Link, and badge clearance. This cross-view check took 12.297 seconds; its temporary verification session was removed. Actual-content production captures are under `output/video-browse-overhaul/deployment/` and remain untracked.

## Music width, overlap and population repair

Reproduced production symptoms before edits: Albums capped at 1440px while Artists used the 1920px viewport; phone Music tools intersected Saved audio; recent endpoints returned more items than the four rendered; the complete catalog started collapsed. Read-only endpoint checks found no count-versus-list mismatch in the Music catalog.

Albums, Artists, Saved audio and music search now share full-width browse containers. Music tools uses the existing accessible menu in a wrapping header action group, retaining Music, playlists/mixes, editing-permission-gated management and offline listening. Recently played/added preserve all fetched albums in bounded horizontal rows, the complete virtualized catalog is visible, and loading/error/empty states include recovery. Recent and saved shelves refresh on foreground; shared section counts also refresh on foreground and after saved-file mutations. Music search uses the global header position. The optional artist sampler loads when its disclosure opens and remains explicitly a sampler rather than a complete catalog.

Focused validation: 47 tests across MusicTab, music session and retained library UI; 20 tests across MusicTab and shared library sections, including foreground count refresh (overlapping runs, not additive); two browser workflows for wide-mode width parity, complete recent rows, phone non-overlap/menu controls, lazy sampler, scoped search and error/retry recovery. Frontend typecheck and production build passed. CSS measures 81.8 KB gzip within the unchanged 82 KB budget. No backend/schema edits.

Red production captures and sanitized data-shape measurements are retained in ignored `output/music-repair/`; mocked layout/error captures are under `output/music-layout/`. The production rollout and actual-content verification are recorded in `output/music-repair/deployment/`.

Music repair deployed at `17992b4a22f5dc29249fdaa388587330e9a9abba`. Actual production browser checks passed at 1920px and 390px: Albums matched Artists in width, Music tools and Saved audio did not intersect, the complete catalog was mounted, and recent rows rendered every item returned by their endpoints. Relevant music data calls returned 200. The temporary verification session was removed. Runtime/schema stayed unchanged; 398 application files matched the candidate, public routes/assets passed, and all 28 rollback pins remained stopped. Actual-content captures are `output/music-repair/deployment/music-albums-{wide,phone}.png`; private inventory measurements remain in ignored evidence only.

## Home browse density follow-up

Home shelves use tighter responsive card sizing and spacing, with the approved opening hero unchanged. Wide landscape cards are 300px rather than 400px; wide posters are 180px rather than 220px; shelf spacing is 32px rather than 64px. Desktop/tablet/phone sizes scale consistently, matching image `sizes` declarations to avoid requesting oversized artwork. Shelf-heading padding was removed and the heading-to-row gap reduced. Typography, touch controls, card actions, row navigation and member layout preferences remain intact.

Focused checks: 36 Home shelf/surface/accessibility tests; a red/green desktop-and-phone browser geometry and navigation workflow, plus inspected 1440px/2048px/phone captures. Production build and style gate passed at 81.8 KB gzip within the 82 KB ceiling. No backend or schema changes. Local evidence is under ignored `output/home-density/`; rollout records are under its `deployment/` directory.

Home density is deployed at `1b5159be1f3e92a9721393b6b9875919c68592be`. Authenticated production checks, after Home readiness and loaded Continue/Next up cards, measured 300px landscape cards/32px shelf spacing at 2048px and 234px cards/24px spacing at 390px, with zero body overflow. Public routes and entry assets passed; 398 application files matched the build; backend and schema remained unchanged; all 29 rollback pins were stopped. The temporary verification session was removed. Actual-content captures remain untracked under `output/home-density/deployment/`.

## Hero-to-first-shelf spacing

The first shelf immediately following the Home hero now uses a 16px gap across desktop and phone. Later shelf spacing and the hero artwork, actions and controls retain their existing layout. The existing Home density workflow checks both a minimum breathing room and the 16px maximum on wide and phone viewports, alongside retained row navigation, readable captions and touch targets. Focused browser workflow and production build passed. Review rollout evidence is retained in ignored `output/home-hero-spacing/deployment/`.

Deployed review source `5922e66400072a6ce1db774e4f7afb9ac0990d4b`: healthy, 398 application hashes verified, public routes and nine entry assets passed. Authenticated production geometry measured exactly 16px from hero bottom to first shelf heading at 2048px and 390px, with no body overflow; both captures were inspected. Backend/schema and runtime configuration remained unchanged, all 30 rollback pins stayed stopped, and the temporary verification session was removed.

## Personal activity modal

The avatar now opens an accessible native dialog with the member greeting/date, a 14-day gold playback timeline, recorded playback duration, sessions and active days. Home retains a semantic Home heading and opens directly on artwork. Account switching, Home editor, mobile connect, My Settings, guarded Manage Server, all four themes and sign-out remain available. Escape/backdrop dismissal and focus return use the shared dialog. Stats load only on opening, with cancellation on close/member change and explicit error/retry/quiet-history states.

`GET /api/me/activity-summary` is authenticated and self-scoped; it aggregates bounded recorded playback history into 14 UTC-day buckets. It returns no other member data, media names or device details. Recorded playback is not a title-completion or attention metric. Ongoing sessions appear after ending; days use UTC, and the underlying history retains 90 days. No schema migration or new dependency. Shared account-menu workflows now target dialog buttons instead of menuitems.

Validation: 39 focused TopBar/dialog/Home/accessibility unit tests passed; three modal browser workflows passed at desktop light/dark and phone dark, including automated accessibility checks, retry recovery, settings navigation, sign-out reachability, Escape and focus return. Six retained Home density/editor/artwork-opening workflows passed. Frontend typecheck passed. Production build/style gate passed; the new modal adds less than 1 KB gzip CSS with aggregate allowance adjusted from 82 to 83 KB; core and JavaScript ceilings unchanged. Evidence stays in ignored `output/member-activity/`.

Backend summary API tests: 2 passed. Broader admin activity checks: 18 passed, one existing stale media-fixture failure (artifact missing before playback permission guard), verified unchanged in the baseline. A separate existing acquisition integration test reaches settings successfully but later expects the old global search combobox; contextual settings search now focuses its scoped input, and jsdom lacks scrollIntoView. It remains outside this modal packet. An initial wider keyboard run was stopped; no full-suite claim is made.

Deployed at `bdaa9b72b5b9e7afed4a49aaf99c2ad430b534eb`. Server health, public routes, authenticated activity summary and nine entry assets passed. All 398 application files matched the candidate; schema stayed at 43, runtime configuration/mounts remained unchanged, and all 31 rollback pins stayed stopped. Signed-in production checks passed at 2048px and 390px: real 14-day data rendered, dialog fit its viewport, Home had no greeting/date masthead, and Escape restored avatar focus. Both production captures were inspected; verification session removed. Evidence: ignored `output/member-activity/deployment/member-activity-{wide,phone}.png`.
