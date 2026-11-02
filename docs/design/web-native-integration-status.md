# Web native integration

Implementation branch: `codex/web-native-integration`, application base `e5573ea1` (current origin/main at kickoff, November 1, 2026). Worktree: `/Users/kmcdowell/.codex/worktrees/web-native-integration/lumina`. Original main checkout clean; all other worktrees and design branch preserved. Approved reference: `codex/web-native-mockups`, pinned prototype `e8720a92fd6c9b03c76d08e7c8351d0b6b32ca32`. Reference source is not merged into production.

Completed extension: approved My settings and Manage server integration, from `eb7e402`. Settings approval `5d4881a`; prototype `f5486e4482d81de13b549eb3bc0e5415d1f8295c`. [Settings ownership/validation](#approved-settings-extension--active-integration) supersedes the earlier settings exclusion. This extension remains undeployed.

## Ownership

Orchestrator: global shell, routes, typography, shared gallery, API client/types, authenticated preference plumbing, integration and browser evidence. Video/Home worker: home/library, AllLanding, GalleryTitlePage/titlePage.css. Music worker: music, album/artist/MusicTab and music.css, music router/service/API tests. Books worker: books frontend, books router/service/archive readers and focused tests; ADR 0036. Shared models/schema/migrations/docker rollback stay orchestrator owned. Wave two reuses workers for Requests, Streaming/watch, and read-only review. Workers share the implementation worktree and preserve each other's changes.

## Production contracts

- Recently played albums: bounded authenticated `/api/music/albums/recently-played?limit=1..50`, eligible visible tracks with member PlaybackProgress.plays > 0, grouped by album, max last_watched_at descending and album id tie-break. Existing depth-qualified play semantics; no recommendation/acquisition proxy. Normal TitleSummary serialization and access filtering. No migration.
- Music browsing: validated `ui_prefs.music_browse.view` albums/artists through existing settings merge/autosave; sibling preferences retained, explicit route/filter wins, account switch defaults independently. Album/search navigation does not write browsing preference.
- Illustrated chapters: explicit ComicInfo.xml Pages/Page Bookmark labels mapped through the real archive image order, validated bounded indices/labels. Page checkpoints remain page checkpoints; archives without authored boundaries remain page-only. Existing EPUB spine chapters remain distinct. Additive reader manifest, no migration; ADR 0036 records details.

## Capability and route matrix

| Surface/routes | Retained production boundary/actions | Regression anchors | Status |
| --- | --- | --- | --- |
| Shell / all destinations | Menu overlay; Photos, Saved/collections, downloads, settings/admin; access gates; focus/history | Navigation, routes, workspaceAutosave, CommandPalette | Implemented; shell/history verified |
| Home `/` | Mixed media shelves, personalization, continue/hide, remote/library/title entry | HomeHero, HomeSurface, home browser workflows | Implemented; focused workflows verified |
| Movies `/video/movies`, Shows `/video/shows` | Existing lens filters/search, collections, details/edit, versions, download | GalleryWall, gallery browser specs | Implemented; focused workflows verified |
| `/video/title/:id?season=N`, `/watch` | Real episode/season, prepare/resume/start-over, editions/files/captions/cast/offline; title Back/focus | GalleryTitlePage, watch-start, watch-web-video | Implemented; focused workflows verified |
| `/audio`, `/audio/artists`, `/audio/title/:id` | Albums/artists/Saved, playlists/manage/offline, tracks/session/queue/casting/lyrics | MusicTab, music player/playlists/curation, music navigation/continuity/offline | Implemented; focused workflows verified |
| `/books`, `/books/manga`, `/books/series/:id`, `/books/title/:id`, `/books/read/:id` | EPUB/page readers, RTL, format checkpoints, volume browse, sharing/import/download/delete, filters/Back | BooksSurface/BookDetail/BookReader; books/books-browsers/realstack | Implemented; focused workflows verified |
| `/requests` and subroutes | Discover/catalog/anime/search, title availability, request/quota, mine/cancel/retry, admin approval/fulfillment | RequestsSurface, requestsModel, requests_quality | Implemented; focused workflows verified |
| `/streaming` and provider routes, channels/watch | YouTube search, provider-qualified browse/live/followed, recording/read-only chat capability gates, queue/theater | WatchSurface/WatchChatSeam/LiveChatPanel, chat/recording/queue specs | Implemented; focused workflows verified |

| My settings `/settings` and section/page deep links | All personal registry providers, preferences, profile/security, imports/apps, autosave/manual forms, secret/error/dirty contracts | Settings registry/navigation/you/search/D-pad; focused browser and realstack settings | Implemented; registry, browser and realstack verified |
| Manage server `/manage`, member and legacy admin deep links | Owner-only health/activity, household/resources, storage/optimization, connections, AI and maintenance; all real providers | Settings server/requests/household/AI/import contracts; focused browser/realstack settings | Implemented; registry, browser and realstack verified |

## Verification policy

Focused checks only per current explicit owner instruction in dogfood-next-handoff.md. Full `make check`, Core/native UI suites and release gates are not run without direct request. No deployment, merge, marketing-site or native release changes authorized. Exact focused results, screenshot paths and remaining limitations will be appended as verification completes.


## Integrated behavior and review fixes

The production shell uses existing routes with direct Movies and Shows destinations and a compact phone bar whose Browse menu retains the complete destination set. Navigation overlays the page; mouse leave waits 700 ms, keyboard focus protects it, Escape/outside selection dismiss it, and touch has a Close menu action. A real input-mode regression found by the browser run was fixed. Movies, Shows and All video use distinct approved `movies`, `series` and `library` sprite symbols from the existing 104-design Lumina pack; no asset geometry or native release changed.

Media names share the 48/34 px semantic role; route context and the Home greeting use compact headings. Movies/Shows use the first real unfiltered catalog title's responsive backdrop and the existing focal-framing component, followed by the poster wall. Filtering/selection retains the compact wall. Hero Details retains a distinct origin in WallStore; both the wall and app focus authority restore that action on Back. The browser caught and verified this correction.

Music leads with actual recently played/added album shelves, keeps Albums/Artists visible, provides a URL-backed All albums disclosure and scoped server search, and retains Saved audio, playlists/mixes, curation, listening room, queue, offline and cast seams. The authenticated member preference uses the existing settings autosave; explicit views/filters take precedence. Member changes remount AudioSurface as well as clearing shared caches.

Requests retains real Discover/request/quota/Arr/admin flows, broad artwork and compact route context. Its title route now has the same real contextual search form rather than losing search when the duplicate shell trigger disappears. Streaming Browse features up to two actual followed uploads/popular entries and removes them from the following rails; Live retains its real followed/live hero. Provider and read-only chat/recording boundaries remain unchanged.

Books has one contextual search form on every non-reader route; generic overview/detail/series searches use the real mixed ebook/manga catalog, while scoped views retain their filters. Illustrated-book details expose genuine authored boundaries and a page deep link. Reader locations remain format-specific. The archive contract sorts bounded valid bookmarks by page so arbitrary XML ordering cannot invert chapter ranges. EPUB spine chapters and unmarked page archives retain their existing semantics.

## Focused validation evidence

All commands use the pinned `mise exec --` toolchain. Counts below are per run and overlap; they are not an aggregate test count.

| Run | Command / scope | Result |
| --- | --- | --- |
| Shared shell, routing, preference autosave, API, palette, gallery | `npm --prefix frontend test -- src/app/Navigation.test.tsx src/app/routes.test.ts src/LuminaApp.test.tsx src/LuminaRouteIcons.test.tsx src/workspace.test.ts src/workspaceAutosave.test.tsx src/api.test.ts src/features/gallery/GalleryWall.test.tsx src/features/gallery/WallGrid.test.tsx src/features/palette/CommandPalette.test.tsx` | 10 files, 279 passed |
| Final changed UI check | Navigation, route icons, GalleryWall/WallGrid, MusicTab, RequestsSurface | 6 files, 80 passed |
| Home/title workflows | HomeHero + GalleryTitlePage | 43 passed |
| Music player/catalog/manage | MusicTab, MusicPlayer, MusicPlaylists, MusicCuration, LuminaApp.music | 5 files, 43 passed |
| Books routes/details/readers | app routes, BookDetail, BookReader, BooksSurface | 88 passed for the initial packet; final contextual-search addition: BooksSurface 15 passed |
| Requests | RequestsSurface + requestsModel | 30 passed |
| Streaming Browse/Live | exploreModel, ExploreSurface, StreamingSurface, liveModel | 51 passed |
| Watch/chat continuity | liveModel, StreamingSurface, WatchChatSeam, LiveChatPanel | 33 passed |
| Backend music/search/access | `backend/.venv/bin/python -m pytest -q backend/tests/test_music_api.py backend/tests/test_music_listening_session.py backend/tests/test_gallery_search_scope.py backend/tests/test_search_scope_anime.py backend/tests/test_book_access_views.py` | 36 passed |
| Books archive/API/native | test_book_archives, test_books_api, test_book_native | 57 passed; final archive/API order-invariant rerun: 45 passed |
| Cross-surface browser | `npm --prefix frontend run test:browser -- e2e/web-native-integration.spec.ts --workers=1` | 9 passed; 1440/768/390/320 px, contextual search, URL/member preference, keyboard/mouse/touch dismissal, hero Back, 200% text and forced colors |
| Home browser | resume/newest details→Back→play; filtered title→Back | 3 passed; final Home light/dark captures at phone/tablet/desktop/TV widths: 8 passed |
| Music browser | music-navigation + music-continuity | 5 passed; updated desktop/phone album/artist detail capture journey: 1 passed |
| Books browser | `e2e/books.spec.ts` | Final 11 passed, including contextual detail→mixed catalog search, 320/390 px, readers, details and rich catalog captures |
| Requests browser | `e2e/requests_quality.spec.ts` | Final 4 passed, desktop/phone × light/dark; 53 captures cover catalogs/title/dialogs/mine/manage/empty/failure |
| Streaming browser | `e2e/explore-gallery.spec.ts` | 8 passed, desktop/phone/light/dark, accessibility and no overflow |
| Production offline browser | `MUSIC_OFFLINE_PRODUCTION_URL=http://127.0.0.1:4192 ... e2e/music-offline.spec.ts` against `vite preview` | 2 passed, including cached built modules and server-disconnected playback/member isolation |
| Real backend | from frontend: `npx playwright test e2e/realstack/series.spec.ts e2e/realstack/books.spec.ts --config=playwright.web-native.config.ts --project=journeys --workers=1` | Stable bundle: setup + real EPUB/CBZ/chapter/resume + episode start/resume/next = 3 passed |
| Build | `npm --prefix frontend run build` | Passed TypeScript, Vite production bundle, licenses and style/bundle contracts; CSS 76.6 KB gzip, core 7.1 KB; revised aggregate allowance 77 KB, core/JS ceilings retained |
| Icon package | `python3 scripts/lumina_icons.py check` | 104 designs, 40 solid alternates, 144 canonical/native SVGs and both targets valid |
| Working diff | `git diff --check` | Passed |

An initial real-stack attempt exposed stale test assumptions about a volume's accessible label, the new Resume confirmation, and multiple Continue-reading rows. These workflow assertions were corrected. A concurrent rebuild also invalidated served assets during one attempt; the recorded successful real-stack run used a completed bundle without rebuilding during the run. No product failure was hidden by relaxing playback, reader, authorization or checkpoint assertions.

## Captures and limits

The local [capture gallery](../../output/web-native/index.html) includes desktop/phone views for all seven primary surfaces, navigation, and album/artist/book details. Individual captures and exact logs are retained under `output/web-native/`; source suites additionally retain Home/Books/Requests and real-stack screenshots in their established output directories. Capture data is synthetic; missing/one-pixel fixture artwork is deliberately present in some catalog checks. No private household media was used. The clean-base capability inventory was recorded, but a portable before-capture set was not retained; the screenshots are integrated-production evidence rather than a before/after benchmark.

External provider accounts/network playback, physical casting devices, and physical CBR/PDF reader qualification were not exercised. Provider/live/chat/recording gates retain existing production contracts and focused fixture tests; real-backend browser playback/reading used synthetic episodes, EPUB and CBZ. PDF/unmarked archives remain page-only by design; ComicInfo bookmarks are required for illustrated chapters. No feature is described as having inferred chapters.

Final additional music preference/search run: 3 passed at 1440/390/320 px, with populated Recently played artwork and a request assertion for the real `scope=music` boundary. Final frontend typecheck passed after all changes. The final stable production-bundle real-stack rerun passed all 3 journeys in 1.3 minutes.

No full `make check`, full repository/Core/native UI suite, deployment, merge, push, marketing-site edit or native release was performed. The full PR/release gate remains unrun under the owner's focused-testing instruction.


## Production review deployment — November 1, 2026

At the owner's explicit follow-up request, application revision `44a00edbdd87a18cc2edd9052eb022384094d096` was deployed for production review as `lumina:web-native-44a00edb`, image `sha256:db9da908e3bfd0b3cd1bf8a395e2b5a1bcf7685d71083bb66ace6449443ed681`. This does not merge the implementation branch or release any native/marketing artifacts.

The image is an immutable application layer on the previously verified runtime image `sha256:fb586240bb81b3c501760907c33785f541bd109eb44b69d7498cbf6614896966` (source `c0b9f6a59762e5fd80a75eac97bee684fe260311`). Dockerfile, runtime dependency locks, entrypoint and runtime package inputs are unchanged between that source and the reviewed revision. All tracked backend application files and the previously verified production frontend bundle replace the old application trees; no review prototype is included. Source/base provenance is recorded in image labels.

Before switching, a consistent 8.6 GB SQLite snapshot passed full integrity checking and SHA-256 recording. Schema remained 42. The signing key, exact Compose inputs and protected environment configuration were backed up. The prior running image is retained by `lumina-web-native-44a00edb-rollback`, stopped with health checks/restart/networking disabled and covered by the existing cleanup exclusion. All 18 retained rollback pins remain stopped.

The effective Compose configuration changed only the selected image. Post-recreation comparisons confirm equivalent environment values, host configuration, device/media/data mounts, networks, entrypoint, command, user, health check and proxy labels. Container health is healthy; running database and code schema are both 42. Every one of 308 backend source files and 91 frontend artifacts matches the reviewed inputs in the actual running container. Public HTTPS returns 200 for the app, health, bootstrap status and public showcase; all nine entry-asset hashes match the reviewed bundle. Session and recently-played album endpoints correctly return 401 when unauthenticated. Social metadata injection explains why served HTML bytes differ from the static index. These signed-out smoke checks do not claim a new authenticated household playback qualification.

The owner additionally authorized old build/backup cleanup. Docker reported 5.568 GB of obsolete build cache reclaimed. Eleven old redundant/unmounted database snapshots reclaimed 74.26 GiB; incomplete backup retry files were also removed. Cleanup retained the fresh verified snapshot, the two newest previous snapshots, the newest snapshot for every older schema version, protected keys/configuration, all rollback pins/images and every container-mounted data directory. Root free space increased from approximately 1.4 GB to 5.2 GB. No media files or active canary/review data were deleted.

Sanitized local deployment evidence is retained in ignored `output/web-native-deployment-44a00edb/`; protected backup/configuration and cleanup manifests remain on the production host. The full repository gate remains unrun per the owner's focused-testing instruction.


## Direct A correction after production review

The owner correctly reported that `44a00edb` still resembled the old UI. Its data contracts and incremental UI changes did not establish fidelity to the approved mockup. The implementation was reopened and the actual A compositions integrated directly through production components. See [the Impeccable assessments, feedback and corrections](web-native-design-review.md).

This revision retains the original capability matrix above and adds the following reviewed destinations. Settings layouts remain outside the rework; the shared brand/theme/control system continues to apply across the app.

| Additional view group | Retained capabilities and review evidence | Result |
| --- | --- | --- |
| Photos: timeline, albums, people, trash, viewer/import | Real paged/virtual timeline, model opt-in, search, sort/camera/date, albums, favorites, private/shared metadata, view/restore; PhotosSurface/PhotoViewer and browser workflow | Reviewed and integrated; phone sorting and contextual shortcut verified |
| Downloads | Jobs, acquisition/recording activity, cancellation/retry/clear, completed-item watch route, failure/offline/stale recovery | Compact heading and quiet list; completed-download route verified |
| Collections/Saved and metadata tools | Lists, reorder/history, smart/list builders, title/file edits and permission gates, Music Saved/playlists/manage | Preserved in real routes/disclosures; collection routing/reorder and focused music/editor contracts retained |
| Readers and players | EPUB/page/RTL positions, canonical media headings, exact video/track activation, local/web theater/chat/recording capability seams, mini player/listening room | Retained production components; real-stack EPUB/CBZ and episode playback/resume/next journeys verified |

### Focused correction validation

Counts are per run and overlap; they are not an aggregate test count. The owner's focused-testing instruction remains in force.

| Scope | Exact command / artifact | Result |
| --- | --- | --- |
| Changed UI workflow/brand contracts | `npm --prefix frontend test --` with Navigation, LuminaApp, GalleryWall, WallGrid, GalleryTitlePage, AllLanding, MusicTab, HomeHero, BooksSurface, BookDetail, BookReader, AudioSoundstage, MusicPlayer, MusicPlaylists, PhotosSurface, RequestsSurface, requestsModel, ExploreSurface, StreamingSurface, WatchChatSeam, tokens tests | 21 files, 305 passed; exact invocation/output in `output/web-native-tests-final.log` |
| Final utility/header/catalog changes | `npm --prefix frontend test -- src/features/gallery/GalleryWall.test.tsx src/features/music/MusicCuration.test.tsx src/app/TopBar.test.tsx` | 3 files, 47 passed |
| Caption/virtual grid/music regression | `npm --prefix frontend test -- src/features/gallery/WallGrid.test.tsx src/features/gallery/GalleryWall.test.tsx src/features/gallery/MusicTab.test.tsx` | 3 files, 46 passed |
| Books all fixture workflows | `npm --prefix frontend run test:browser -- e2e/books.spec.ts --workers=1 --output=../output/playwright/native-books-verified` | 11 passed; sparse/rich, 320/390, detail/series/reader, light appearance, search, files, missing covers |
| Final shell/music captions and history | `npm --prefix frontend run test:browser -- e2e/music-navigation.spec.ts e2e/web-native-integration.spec.ts --workers=1 --output=../output/playwright/native-labels-verified` | 13 passed; captions visible, member choice/routes/search, keyboard/touch/mouse, 320/390/768/1440, 200% text/forced colors, focus above phone navigation |
| Home/title visual workflows | Home light/dark 1440/390 capture cases and title canonical-name unit checks | 4 final Home captures; 44 focused HomeHero/title tests passed |
| Requests/Streaming/watch/Photos/Downloads/collections | Existing Requests quality, Explore gallery, Watch quality, Photos, completed-download and collection routing/reorder specs | 24 passed in the cross-view review run; includes light/dark phone/desktop, actionable states/dialogs, sorting, opt-in, player/theater containment and permission semantics |
| Music browse/artist/album/queue/listening room | `MUSIC_VISUAL_DIR=../output/web-native-final/music ... e2e/music-navigation.spec.ts` | 4 passed; refreshed desktop/phone detail/browse captures |
| Build and types | `npm --prefix frontend run build`; `npm --prefix frontend run typecheck` | Passed; production bundle, licenses, style/bundle contracts and E2E typecheck |
| Working diff | `git diff --check` | Passed |

Initial runs caught real defects: moving search between two DOM positions lost focus, phone filter geometry clipped the sort control, the Books hero lost direct Continue, sparse Books templates displaced the hero, light on-art actions lost contrast, and a legacy caption-height token hid phone labels. These were corrected at their owning boundaries and checked through their actual workflows. Tests tied to the removed italic headline/boxed-feature decoration now retain their useful normal-font, missing-art, selected-brand and keyboard/history contracts in the approved layout.

The review package at `output/web-native-final/index.html` compares all seven approved primary mockups with integrated desktop/phone captures and links additional Photos, Downloads, movie/album/artist/book details and Live views. `captures.json` records artifact provenance/hashes. These are synthetic browser captures, not private household media or shipping fixture behavior.

Physical casting, external provider/account playback and physical CBR/PDF qualification remain the limits recorded above. The full repository/Core/native UI suite, merge, push, marketing-site edits and native releases remain unperformed. The separately authorized production review deployment is recorded below after verification.

### Corrected production review build

Application revision `e57290a23ccfb4df77d3fe889b60c8129279052a` is deployed as `lumina:direct-a-e57290a2`, image `sha256:68083c7796c6cf3c6e1f995f70619705af09f8917e4c193ed1599d11f20caba0`. This immutable frontend layer replaces the complete frontend artifact directory on the preceding verified application image. Backend/runtime/dependencies remain unchanged. The prototype branch and other worktrees remain separate.

Final checks against that committed application bundle:

| Check | Evidence | Result |
| --- | --- | --- |
| Production build | `output/web-native-build-committed.log` | Passed; aggregate CSS 81.1 KB gzip within 82 KB allowance; shared core 7.3 KB within 7.4 KB allowance; lazy JS, licenses and style contracts passed |
| Typecheck | `output/web-native-typecheck-committed.log` | Application and E2E passed |
| Gallery workflow regression | `output/web-native-gallery-final.log`; `e2e/gallery-wall.spec.ts` | 19 passed; phone captions are intentionally visible, and the retained expectation now checks that behavior |
| Real backend journeys | `output/web-native-realstack-e57290a2.log`; `e2e/realstack/books.spec.ts` and `e2e/realstack/series.spec.ts` with the web-native realstack config | 3 passed in 1.2 minutes: owner setup, actual EPUB/CBZ chapter/checkpoint/resume, episode start/resume/Home/next |
| Actual running source | `output/web-native-deployment-e57290a2/verify.py` | All 91 frontend artifacts and 308 backend source files match their SHA-256 manifests |
| Public HTTPS smoke | `output/web-native-deployment-e57290a2/public-verification.json` | App, health, bootstrap status and public showcase: 200; unauthenticated session and recently played albums: 401; all 9 entry assets match by SHA-256 |
| Runtime preservation | `output/web-native-deployment-e57290a2/verification.json` | Healthy; Compose only changes image; environment, host configuration, mounts, networks, startup and proxy labels unchanged |
| Database / rollback | Read-only schema check and deployment verification | Code/database schema 42; 19 rollback pins stopped; preceding image pinned by `lumina-direct-a-e57290a2-rollback` and protected by cleanup exclusion |

The gallery at `output/web-native-final/index.html` contains 35 captures and seven approved-A comparisons, each with integrated desktop and phone views. Its `captures.json` supplies provenance and hashes. The test-only gallery caption assertion and this deployment record were committed after the application revision; no shipping application code changed after the verified build.

Production verification is signed-out HTTP and artifact/runtime verification. Authenticated playback and reading were exercised against the real test backend with synthetic media; household provider credentials and physical casting/CBR/PDF qualification remain untested. Full `make check` remains unrun under the owner's focused-testing instruction. No merge, push, marketing change or native release occurred.

## Approved settings extension — active integration

The owner extended scope after the direct-A production review. Continue from implementation `eb7e402` (shipping app `e57290a2`), retaining all media work. Settings approval/team-plan reference is `5d4881a` on `codex/web-native-mockups`; settings prototype reference is `f5486e4482d81de13b549eb3bc0e5415d1f8295c`. The approved handoff, implementation plan and `web-settings-design-proposal.md` in the preserved prototype worktree govern this extension. Do not merge that branch. Deployment is outside this extension's scope; production remains at the separately reviewed revision above.

| Owner | Exclusive files / responsibility | Status |
| --- | --- | --- |
| Orchestrator | Shared shell/routes/tokens/UI/search, workspace/API contracts; E2E settings workflows/captures except the assigned realstack file; integration record/final review | Complete: shared contracts, focused verification and integration |
| Settings worker (reused Music slot) | `frontend/src/features/settings/**` and applicable root `v1_settings_*.test.ts[x]` | Complete: approved spaces, registry map and real forms/providers |
| Video/Home worker | `frontend/e2e/realstack/settings.spec.ts`; AdminOverview and metrics tests; bounded AdminBackups schedule, AdminMembers addresses, RecordingRetention and AdminStorage rules with existing tests | Complete: health-first layout, owner draft guards and focused realstack journeys |
| Books worker | Media packet preserved; read-only Settings source/capture review | Complete: feedback reworked and final representative captures accepted, no concrete P0/P1 |

Acceptance extends to both distinct spaces: member-scoped My settings and owner-only Manage server; compact typography and real caption preview; persistent desktop rail/phone picker; full registry search and focused deep links; explicit scope and autosave/manual-save semantics; dirty-only Save/Discard; real connection tests and write-only secrets; progressive advanced tuning. Preserve every registry row, provider, URL and permission boundary.

Verification uses existing focused settings registry/navigation/unsaved/requests/household and personal/server/search/D-pad contracts, plus affected secret/import/AI tests. Browser checks cover all row destinations, personal and owner URL gates, search/shortcut/focused target, real autosave/manual-save payload behavior, validation failure retention, separate connection testing, secret replacement/removal, category/search/member/global navigation and browser-history dirty guards. Capture personal Playback/Profile and owner Overview/Connections/Library plus dirty/error states at desktop/390px, with 320px/tablet/200% text/forced-colors/reduced-motion checks. Run frontend typecheck/build and shared shell/route checks; do not run the full repository/native suite. Record exact results, coverage map, design findings/fixes and limits before closing this extension.

### Settings extension completed

Both approved spaces are integrated through the production registry/providers. My settings defaults to Profile and includes all personal preferences and account/app workflows. Manage server defaults to health/activity, with actionable snapshot issues before neutral metrics. The rail and phone picker contain only the current space; the global menu/Account connects the two spaces, and owner search still covers the full registry. Existing public/legacy section, page and member URLs retain their controls and permissions.

The [complete settings coverage map](web-settings-capability-matrix.md) records all **105 registry rows across 52 subpages**, with destination and save/action behavior. The pre-existing Still-watching policy row was missing from navigation; it is now reachable through Connections → Media apps. All 27 member-detail rows remain covered by the browser registry walk. Playback/conversion and download limits are grouped under Library & storage while their stable URLs/providers remain unchanged.

Manual forms identify their save mode before editing and display Save/Discard only while dirty or saving. Profile, interests, custom mute words, backup schedule, public/local addresses and recording-retention drafts now share the existing unsaved authority; storage rules retain their guarded conflict/validation behavior with dirty-only actions. Existing manual provider forms, immediate/autosaved controls and staged authentication/test/import/export/token/destructive workflows retain their real semantics. No global prototype draft or new API is introduced. Sign out and account-link redemption preflight the guard before teardown; actual session expiration still clears invalidated state. Category, full-registry search, member details, Account/global media routes, browser Back and unload retain their guards.

Search shortcuts remain scoped even while Settings loads lazily. Row deep links wait for a provider's real field to become enabled, retain an accessible row heading while loading, and respect subsequent user focus. The app's generic heading focus no longer steals an already-focused field. Continuing music/video players retain their DOM/session and settings controls clear their actual overlays; the hidden settings media tab bar does not leave a phone inset. The caption row has one artwork-backed preview using the real caption preference model; the player keeps its existing preview.

#### Exact final validation

Counts are per run, overlap with earlier runs, and are not added together. The exact selected files, invocation and output are retained in each named local artifact. All toolchain commands use `mise exec --`.

| Final run | Scope / command | Result |
| --- | --- | --- |
| `output/settings-unit-verified.log` | `npm --prefix frontend test --` with 30 affected Settings/provider/import/registry/owner-form/shared-shell/router/music/caption files, enumerated in the log | **30 files, 301 passed** |
| `output/settings-shared-verified.log` | AppShell, TopBar, routes, LuminaApp and MusicPlayer | **5 files, 104 passed**; overlaps the affected Settings run |
| `output/settings-browser-verified.log` | Seven focused browser specs: settings shell/visual views/registry/polish/alignment, music navigation and watch start; exact executable invocation in `output/settings-extension/validation-commands.txt` | **45 passed in 1.6 minutes**. All twelve categories at 320/390/768/1440; full registry and member rows; light 320/1440 alignment; owner/member guards; scoped search/shortcut and asynchronous field focus; manual/autosave writes, failure retention and dirty history/category/search/global/logout paths; write-only replacement and separate connection testing; modal keyboard return; 200% text/forced colors/reduced motion; continuing music/video controls |
| `output/settings-shared-history.log` | Settings shell plus existing web-native media integration workflows after scoping field-focus protection to Settings | **25 passed in 59 seconds**; contextual search/navigation at all four widths, member Music preference/deep links and exact movie hero Back/focus restoration, plus Settings guards/focus. Overlaps the 45-check run. |
| `output/settings-realstack-final-bundle.log` | `scripts/with-lock.sh /tmp/lumina-perf.lock 1800 sh -c 'cd frontend && npx playwright test e2e/realstack/settings.spec.ts --config=playwright.web-native.config.ts --project=journeys --workers=1 --output=../output/playwright/settings-realstack-final-bundle'` against the completed production bundle and fresh test root | **6 passed in 1.0 minute**: owner setup, server-persisted theme across cleared local hints/reload, real profile draft/discard/save/reload/restore, real owner-limit draft/discard/save/reload/restore, member JSON export with secret exclusion, real backup create/verify/password-proved download |
| `output/settings-typecheck-verified.log` | `npm --prefix frontend run typecheck` | Application and E2E passed |
| `output/settings-build-verified.log` | `npm --prefix frontend run build` | TypeScript/Vite/licenses/style/bundle gates passed; aggregate CSS **81.7 KB gzip / 82 KB**, shared core **7.3 / 7.4 KB**; lazy JS ceilings retained |
| `output/settings-design-detector-verified.json` | Impeccable detector over Settings and changed shell/health/activity presentation | `[]`; complements independent source/capture review, not a conformance claim |
| Final working tree | `git diff --check` | Passed |

The earlier browser attempts exposed displaced desktop navigation, mixed-space rails, narrow phone columns, duplicate caption previews, missing durable-draft guards, player occlusion, lazy shortcut scope and provider-focus races. The final source fixes address those causes. The music fixture now reuses its existing 30-second WAV rather than overriding it with a 1.5-second clip that ended during the longer settings journey. Useful authorization, persistence, history and exact playback assertions remain in place.

#### Review package and limits

`output/web-native-final/settings/index.html` contains all twelve approved/current category comparisons, desktop/phone and 320/tablet links, light captures, and dirty/rejected-save states. Its SHA-256/provenance manifest records **81 browser captures plus 12 approved reference images**. The full web gallery at `output/web-native-final/index.html` retains the earlier media evidence and now includes the two settings spaces and their key sections. Captures use synthetic fixtures, not household secrets or private media. The reference proposal, handoff and plan are synchronized from approval `5d4881a`; the prototype branch is preserved and was not merged.

No backend/schema, dependency, marketing-site or native-release change was needed. Real external provider credentials, live household account mutations and physical casting/CBR/PDF qualification were not exercised. Connection tests/secret/error paths use controlled browser/provider fixtures; real persistence/export/backup checks use the isolated production backend. These checks are not a physical-device accessibility or provider-compatibility certification. Full `make check`/repository/Core/native suites remain unrun under the owner's focused-testing instruction.

**This extension is not deployed, merged or pushed.** The existing production media review build remains unchanged.

## Follow-up: pinned navigation and library titles

The owner reported that scrolling left a vacant band above the library filters and that a library label collided with the logo/menu instead of replacing the generic Video context. The deterministic Movies/Shows browser reproduction failed in all four original desktop/phone cases: the global header was at y=-1000 after a 1000px scroll, while the filter toolbar retained its header offset.

The complete header now remains pinned: sticky for ordinary pages and fixed over artwork-led pages, retaining the artwork stage at y=0. The menu, mark and active library context share the same layout row and adaptive opaque surface; the menu is no longer a separately fixed control. Canonical lens names populate the header (including Anime, YouTube, Recordings, Deleted and All video), and Movies/Shows retain accessible H1s without a competing visible heading in filtered views. Filters meet the pinned header rather than reserving space beneath a header that has scrolled away.

Settings rail/anchor offsets also clear the pinned header. Group navigation scrolls to its heading, and the existing player-clearance helper only adjusts actual controls when a player obstructs them; a large focused settings group no longer scrolls its own heading out of view.

Focused final evidence: `output/nav-scroll-browser-final.log` — **31 passed**, including Movies/Shows at 320/390/768/1440, unfiltered and filtered title placement, scrolling, contextual search/menu behavior, exact hero Back/focus, Settings bookmarks, music preference/queue continuity and video mini-player continuity. `output/nav-scroll-unit-final.log` — **6 files, 84 passed**. Frontend application/E2E typecheck and production build passed (`output/nav-scroll-typecheck-final.log`, `output/nav-scroll-build-final.log`); CSS 81.6 KB gzip within 82 KB, shared core 7.3 within 7.4 KB. `git diff --check` passed. Before/after desktop/phone screenshots and hashes are in `output/nav-scroll/index.html` and `captures.json`. No deployment, merge, push, backend/native/marketing change or full repository suite was performed.


## Follow-up: All video hero and navigation

The owner’s All video screenshot exposed a full-stage gradient applied to a bottom-only card caption: its upper edge split the artwork horizontally. The hero now uses continuous, full-height shading, with a dark caption scrim in both themes. Library tabs and totals share one responsive context block beneath the pinned global header. The accessible H1 remains, while the visible header supplies “All video” without a duplicate Video label. The mobile tab container can shrink and scroll without widening the page. The first catalog chapter follows the stage with 40px padding instead of a stacked 121px margin/divider gap. Existing real data providers, title actions, permissions and routes are unchanged.

Focused evidence: the initial geometry reproduction failed at all four widths. `output/all-video-complete.log` reports **25 passed** integration checks; the final populated-category fixture pass in `output/all-video-full-nav.log` reports **8 passed**, covering 320/390/1440/1920px in dark/light, continuous artwork, header clearance, catalog spacing, no horizontal page overflow, hero-to-detail Back/focus and See all Back/focus. `output/all-video-lazy.log` reports **1 passed** for lazy chapters/reduced motion. `output/all-video-unit.log` reports **15 passed** AllLanding checks. Application/E2E typecheck and production build passed (`output/all-video-typecheck.log`, `output/all-video-build.log`); CSS is 81.7 KB gzip within 82 KB, shared core 7.3 within 7.4 KB. `git diff --check` passed. The new detail journey waits for the visible detail page before returning; existing focus restoration required no product change.

Reviewed desktop/phone before-and-after captures are in `output/all-video-fix/index.html`, with SHA-256 records in `captures.json`. These use synthetic fixtures and do not claim live production verification. No deployment, merge, push, backend/native/marketing change or full repository suite was performed. The capability matrix remains complete; this correction preserves its existing browse/detail/history contracts.


## Production review deployment — owner authorized

The owner explicitly requested production deployment after the All video correction. Revision **45f36fc5dc7df2fc06ff2496341767d14a3a7f97** is deployed as an immutable verified frontend layer over the previously running media image. This includes the settings extension and pinned-navigation/All video follow-ups. The backend remains byte-identical to the prior production image. The prototype branch, main checkout, marketing site and native releases were not changed; no merge or push occurred.

Deployment evidence is retained under ignored `output/web-native-deployment-45f36fc5/`: exact Dockerfile, frontend archive, frontend/backend SHA-256 manifests, build log, normalized configuration verification and public verification. The running service is healthy. All **91 frontend files and 308 backend files** match their recorded hashes; **9 public entry assets** match the build. Public Home, All video, Movies, personal settings and server settings routes return 200, health/bootstrap/showcase return 200, and unauthenticated session/recently-played requests retain 401 guards. The browser reaches the production sign-in screen; authenticated production layout and playback were not exercised in this deployment session. The focused synthetic browser evidence above remains the layout/interaction qualification.

The Compose configuration differs only in the selected image. Running environment values match the unchanged Compose settings and image defaults. The prior image is retained by `lumina-ui-45f36fc5-rollback`, stopped with health checks/restarts disabled; all **20** retained rollback pins are stopped. The persistent `lumina-.*rollback` cleanup exclusion remains present. No application data or backups were removed. Production source revision is 45f36fc5; this deployment-note commit is documentation only.


## Follow-up: artwork-first opening navigation

The owner requested mostly translucent navigation before scrolling so opening hero images dominate. Artwork-led routes now use a 12% dark header tint with on-art control text and readable activity rings. After 8px of scrolling, the pinned header restores its opaque adaptive surface; returning to the top restores translucency. Ordinary pages, including Settings, retain opaque navigation. Existing hero route detection is reused across Home, video catalogs/All/detail, Books and Requests. No layout offset, route, permission or provider changed. The background transition is 180ms and the existing global reduced-motion rule disables it; forced-colors mode retains system Canvas/CanvasText.

The opening-opacity regression failed before the fix. Focused final checks: **37 browser checks passed**, including Home/Movies/All in light/dark at desktop/phone, scroll-and-return, opaque Settings, existing navigation/search/Back contracts; **26 TopBar/AppShell unit checks passed**. Application/E2E typecheck and production build passed. CSS is **81.9 KB gzip within 82 KB**, shared core 7.3 within 7.4 KB. Evidence: `output/translucent-nav-final.log`, `output/translucent-nav-unit.log`, `output/translucent-nav-typecheck.log`, `output/translucent-nav-build.log`; reviewed top/scrolled captures and hashes in `output/translucent-nav/index.html` and `captures.json`. No full repository/native suite was run.


The production review build was updated to **324593c0b853ab23ed213be2da727a0f485058fc** using the existing verified frontend-layer procedure. Service health, normalized environment/host configuration, mounts, startup, networks and proxy labels passed unchanged checks; only the Compose image selection changed. All 91 frontend and 308 backend files match their manifests, and 9 public entry assets match the build. Home/video/Movies/settings/manage, health/bootstrap/showcase and unauthenticated guards pass the same public smoke checks recorded above. The prior image remains pinned by `lumina-ui-324593c-rollback`, with all 21 rollback pins stopped and the persistent cleanup exclusion preserved. Exact image/build/public-verification evidence is in ignored `output/web-native-deployment-324593c/`. Authenticated production layout review remains with the owner; the captured Home/Movies/All scroll journeys use synthetic fixtures. No merge, push, native/marketing change or application-data cleanup occurred.


## Follow-up: clean drawer selection underline

The selected web drawer item now uses a straight brand-gold underline without the filled rounded card. Strong selected label, full hit target, hover and keyboard focus behavior remain. The worker's eight focused browser checks passed across desktop/mobile, with light/dark screenshots in `output/nav-underline/`. The production build/style budgets passed in `output/nav-underline/build.log`. This is a presentation-only change; routes and capabilities are unchanged.


The drawer underline is live at production source `c3f4a5acfdd126620f779198a49cb32751c852e7`. Service/configuration and public route/asset checks passed; all 399 recorded application files and 9 entry assets match the build. The previous review image remains protected by a stopped rollback pin; all 22 retained pins are stopped. Exact evidence is in ignored `output/web-native-deployment-c3f4a5ac/`. Native Home/header/focus follow-up work is isolated on `codex/native-home-navigation` at `370a9c4`; it is simulator-verified source, not a native distribution release.


## Follow-up: detail navigation without duplicate context

The owner reported the detail Back control overlapping the menu/mark and an incorrect All video header, then chose the cleaner design with no redundant category label beside the logo. Media detail routes now omit that global context label; their existing Back control remains the navigation context and retains its real destination. Its hero position clears the fixed header and safe area. Browse views retain their normal header labels, and returning to Movies restores its header and exact card focus. No title metadata, route, API or history model changed.

The regression reproduced both the overlap and header pointer interception before the fix. Final focused evidence: `output/detail-navigation/test-minimal.log` — **7 passed**, covering movie/show/anime direct links at 320/390/1440px in light/dark, Back clearance, related-title navigation and exact card focus. `output/detail-navigation/unit-minimal.log` — **58 passed** across GalleryTitlePage/AppShell/TopBar. Application/E2E typecheck and production build passed (`typecheck-minimal.log`, `build-minimal.log` in the same directory); CSS is 81.9 KB gzip within 82 KB and shared core 7.3 within 7.4 KB. `git diff --check` passed. Reviewed artwork-backed desktop/phone captures and hashes are in `output/detail-navigation/index.html` and `captures.json`; they use synthetic fixtures. No full repository/native suite, merge, push, marketing change or native release was performed.


This detail-navigation correction is deployed at source **a6ba01372f99fa14af9d1395196cd1ca4ba3af99**. Production is healthy; configuration, environment, mounts, startup, networks and proxy labels remain unchanged apart from the selected image. All 399 recorded application files and 9 public entry assets match the build, and the retained public route/auth-guard smoke checks pass. The prior production image is pinned by `lumina-ui-a6ba0137-rollback`; all 23 rollback pins are stopped. Deployment evidence is retained under ignored `output/web-native-deployment-a6ba0137/`. Authenticated production layout review remains with the owner; the browser layout/focus qualifications above use synthetic fixtures.
