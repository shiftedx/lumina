# Lumina web UI: implementation plan

Approved reference synchronized from `5d4881a`; settings prototype `f5486e4482d81de13b549eb3bc0e5415d1f8295c`. Integration results, ownership and focused verification are recorded in [the integration status](web-native-integration-status.md), [settings registry coverage](web-settings-capability-matrix.md) and [the design review](web-native-design-review.md). This settings extension is local and undeployed; the prior media review deployment remains separate. The approved reference instructions follow.

Status: approved visual direction, production integration pending. Updated 2026-11-01.

## Scope and source of truth

Implement the approved native-inspired web app across Home, Movies, Shows, Music, Books, Requests, Streaming, My settings and Manage server. Keep the existing server-backed capabilities and authorization boundaries. This work concerns the authenticated web app; the marketing website and native app releases are separate.

Design reference: `codex/web-native-mockups`, prototype snapshot `f5486e4482d81de13b549eb3bc0e5415d1f8295c`, based on `ae2d56e7`. The prototype lives in `frontend/src/prototypes/native-web/`, behind the development-only `?mockup=native` entry. Direction A is the selected design; B/C are historical comparisons. Read the A implementations and this plan when old review notes disagree.

Personal and admin settings were approved on 2026-11-01. Read [the accepted settings design](web-settings-design-proposal.md) for the category map, typography, form/search behavior and production preservation gates; it is part of this implementation scope.

The prototype establishes composition and interaction intent. Production retains real routes, hooks, APIs, models and players. Its static media, fixed page counts, synthetic history, DOM events, local state and demo preference key are review scaffolding.

## Accepted design contract

| Surface | Required behavior |
| --- | --- |
| Shell | Desktop menu is closed initially, opens explicitly beside its trigger, overlays content without changing its width, closes after destination selection/outside click/Escape. Mouse leave dismisses after about 700ms; keyboard focus inside protects it; touch has explicit dismissal. Menu trigger remains reachable after scrolling. |
| Destinations | Home, Movies, Shows, Music, Books, Streaming, Requests are directly discoverable through the menu. Preserve other current sections, administration and personal settings with existing permissions. Surface availability follows real capabilities and member access. Existing sections absent from the mockup, including Photos and Saved/collection workflows, remain reachable; adapt their shell without inventing a new approved bespoke layout. |
| Phone | Keep the complete destination set reachable through Browse. The mockup uses Home, current medium, Streaming, Requests and Browse in its compact bar. Adapt existing navigation semantics and safe areas rather than squeezing every destination into a row. |
| Typography | One semantic media-title role: 3rem desktop, 2.125rem phone, approximately 48/34px at default scale. Keep section headings, body, controls and metadata distinct through shared tokens. Display serif belongs to media headings; controls remain sans-serif. |
| Artwork | Home, Movies, Shows, title details and Requests Discover use broad artwork with readable scrims and a fade into browsing. Music preserves square artwork; Books preserves cover proportions and reading context. Use real responsive artwork and existing focal framing. |
| Copy/search | Minimal, factual, actionable copy. Navigation supplies route context; retain accessible h1s without oversized duplicate route names. Each view has one visible search entry; shortcut, label, scope, results and permissions agree. |
| Home/video | Home brings media together. Movies browses posters; Shows browses series, then details, seasons and corresponding episodes. Back restores origin, filters, focus and scroll. Episode selection starts/resumes that exact episode through the existing playback preparation path. |
| Music | Albums is the first-use default. Lead album browsing with Recently played and Recently added album shelves; keep Artists visible above content and retain the complete catalog. Remember the member's last Albums/Artists choice. Artist catalog → artist/discography → album/tracks; artist names link to artists; contextual Back returns to origin. |
| Books | Catalog → title details (author, format, synopsis, cover, reading progress) → reader. Manga/comics add real volume/chapter navigation before reading. Merely browsing volumes leaves saved state unchanged. Continue resumes the actual format-specific saved location. Back retains catalog filters. |
| Requests | Discover → title/request actions; My requests and Manage remain compact, actionable workflows. Hero artwork is prominent in Discover. Single contextual search; retain quota and fulfillment states. |
| Streaming | One or two larger relevant thumbnails precede the remaining grid; avoid repeating those picks below. Prioritize followed/live content only when supported by actual data. Keep provider-qualified browse, live, followed channels, watch layout, theater and capability-gated read-only chat/recording. |
| My settings | A separate member-scoped space for profile/security, playback, display, discovery, downloads and privacy/apps. Compact settings typography and a searchable rail/phone picker; inline caption preview, visible autosave/manual-save semantics and dirty-only Save/Discard. |
| Manage server | An owner-only space led by health, actionable issues and activity, then household, library/storage/optimization, connections, AI and maintenance. State household-wide impact, retain write-only credentials and real connection tests, and disclose advanced tuning progressively. |

Use `apple/docs/brand-assets.md` for the canonical mark, colors, adaptive controls and icon family. Use Impeccable's product register with `PRODUCT.md` for the design pass. Existing theme, high-contrast, reduced-motion, accessibility and localization behavior remain production requirements even though the review mockup is dark-only.

## Production seams to reuse

Full paths are repository-relative. Abbreviated `features/`, `app/`, `styles/` and `ui/` paths start at `frontend/src/`. Verify them against the implementation base before assigning workers.

| Responsibility | Existing entry points |
| --- | --- |
| Composition/history | `frontend/src/LuminaApp.tsx`: `openRoute`, `openTitleStable`, lazy surfaces and title-back restoration. `app/routes.ts`: `AppRoute`, `parseRoute`, `routePath`, `routeFocusKey`, `libraryBackRoute`. |
| Shell | `frontend/src/app/AppShell.tsx`, `Navigation.tsx` (`navGroups`, `MobileTabs`, `NavigationPopover`), `TopBar.tsx`. |
| Search | `frontend/src/features/palette/CommandPalette.tsx`, `paletteModel.ts`; retain existing command actions and local/server result grouping while exposing one route-appropriate entry. |
| Shared appearance | `frontend/src/styles/tokens.css`, `ui/ui.css`, `Artwork.tsx` (`Artwork`, `resolveArtworkUrl`), `features/gallery/HeroFrame.tsx` (cached focal framing). |
| Home/video | `features/home/HomeSurface.tsx`, `HomeHero.tsx`, `HomeShelf.tsx`; `features/library/LibraryBrowser.tsx`; `features/gallery/AllLanding.tsx`, `LibraryTabs.tsx`, `GalleryWall.tsx`, `GalleryTitlePage.tsx` (`Seasons`, `EpisodeRow`). |
| Music | `features/music/AudioSurface.tsx`, `MusicPlayer.tsx`, `musicSession.ts`, `MusicPlaylists.tsx`, `AudioSoundstage.tsx`; `features/gallery/MusicTab.tsx` (`parseMusicState`, `serializeMusicState`). |
| Member preferences | `workspace.ts`: `WorkspacePreferences`, `preferences/patch`, `workspaceSettingsChanges`; `updateMySettings` → `PUT /api/settings/me`, merging `ui_prefs`. Extend typed validation/read/write paths; reuse this endpoint. |
| Books | `features/books/BooksSurface.tsx`, `BookDetail.tsx`, `BookCards.tsx`, `BookReader.tsx`, `BookPages.tsx`, `booksApi.ts`, `routes.ts`, `types.ts`. Backend: `routers/books.py`, `services/books.py`, `book_native.py`, `book_archives.py`. |
| Requests | `features/requests/RequestsSurface.tsx`, `Discover.tsx`, `Browse.tsx`, `TitlePage.tsx`, `RequestSheet.tsx`, `RequestLists.tsx`, `requestsApi.ts`, `requestsModel.ts`. |
| Streaming/watch | `features/streaming/StreamingSurface.tsx`, `features/live/LiveSurface.tsx`, `features/watch/WatchSurface.tsx`, `WatchQueue.tsx`, `WatchChatSeam.tsx`, `LiveChatPanel.tsx`; `liveRecordingControls.tsx`, `chatReplayRail.tsx`. |
| Settings | `features/settings/SettingsSurface.tsx`, `SettingsSearch.tsx`, `registry.ts`, `navigation.ts`, `settingsHost.tsx`, `SettingRow.tsx`, `SectionForm.tsx`, `unsavedChanges.ts`, `sections/` and their current control providers. Keep these real bindings and section forms instead of the mockup’s shared draft. |

Preserve public deep links and route normalization: `/video/movies`, `/video/shows`, `/video/title/:id?season=N`, `/audio`, `/audio/artists`, `/audio/title/:id`, `/books`, `/books/manga`, `/books/series/:id`, `/books/title/:id`, `/books/read/:id`, the existing Requests/Streaming subroutes, and `/settings`/`manage` section/page/member deep links and legacy admin redirects. Movies and Shows already exist as video lenses; expose them through navigation and existing detail routing. The prototype's `screen=` query is not a new production router.

## Capability gates to settle first

1. **Music recency and preference.** Recently added already exists: `GET /api/titles?type=album&sort=created`, through `listTitles`/`GalleryWall`. The title service sorts by arrival/first-seen (`coalesce(added_at, created_at)`), not release date. Recently played albums has no public endpoint yet: per-member `PlaybackProgress` has `last_watched_at`, `plays` and completions, but the Saved audio `sort=recent` path means acquisition recency and `getMusicHighlights(title_id)` ranks only that selected album/artist’s tracks by the member’s play count; it is not a global activity feed. Add a bounded member-scoped grouped-album read seam to `backend/app/routers/music.py` and the existing music service layer; a proposed route is `/api/music/albums/recently-played`, not an existing contract. Group real eligible track activity by album, sort by latest activity, deduplicate, and serialize existing visible `TitleSummary` data. Verify missing-item/visibility rules, tie-breaking, pagination/limit, empty history and what counts as a play against `services/playback.py`; use the current activity semantics instead of inventing recommendation recency.

   Introduce a typed member preference, proposed `ui_prefs.music_browse.view`, through `workspace.ts` decode/encode and its autosave path. The existing server accepts and key-merges a dictionary; this preference needs no new settings endpoint or database column. Parse only known values and preserve sibling preferences. On an explicit route/filter, honor that route; on generic Music entry, use the saved choice; otherwise use Albums. Opening an album or search result must not overwrite the browsing preference. Account changes must not inherit another member's choice. Preserve existing Saved/playlists views.
2. **Manga/comic chapters require real modeling.** Current production supports series, ordered volumes, pages/spreads/RTL and per-asset checkpoints. Its chapter API is EPUB-specific: CBZ/CBR chapters are not an existing feature. Inspect manifests for reliable boundaries; if missing, define an additive chapter contract and source of boundaries (validated metadata or explicit owner-authored boundaries) before implementing chapter UI. Record the decision in an ADR and focused contract tests. Pages retain their own label when no chapter data exists. The accepted chapter flow is complete only with genuine chapter data, not renamed page buttons or mock entries.
3. **Player and authorization continuity.** Inventory current downloads, offline paths, casting, captions, metadata edits, alternate editions/files, playlists, filters, household tools and provider gates. Map each to its retained action before replacing a surface.
4. **Settings registry and save contracts.** Map every existing registry row to its retained destination in the approved personal/admin structure, including controls omitted from representative mockups. Members retain personal settings and receive the current owner-only explanation for server URLs; owners have both distinct spaces. Preserve current immediate/autosaved actions, per-section explicit saves, validation failures, restart-required notices and write-only credential payloads. Unsaved edits remain guarded across category/search/member/global-route changes. The shared mock draft and section-keyword-only search do not replace real section providers or full registry/deep-link search.

Done: the implementation inventory lists every current route/workflow, its owner, retained component/API and regression test; music recency/preference and illustrated chapter decisions are recorded, and settings row/save/permission coverage is mapped. Ask a narrow product question if an actual chapter-source decision is needed while independent UI work continues.

## Execution order and team ownership

Use one implementation worktree/branch from the current intended application base. Keep the prototype branch as a pinned reference. Follow the current `AGENTS.md` model-routing policy and available runtime models. Run at most three workers beside the orchestrator. Reuse idle workers across waves.

The orchestrator owns shared files: `LuminaApp.tsx`, `app/routes.ts`, shell/navigation/TopBar, CommandPalette, global tokens/UI CSS, shared API/types, preference plumbing and integration records. Shared gallery ownership stays with the orchestrator: `GalleryWall.tsx`, `LibraryTabs.tsx`, `gallery.css`, `HeroFrame.tsx`/`heroFrame.css`, `Artwork.tsx`, `TitleActions.tsx` and their tests. Backend shared schemas/models, `db.py` migrations and `docker/README.md` rollback changes also stay with the orchestrator. Workers propose shared changes to that owner; they do not edit the same files concurrently.

### 1. Baseline and inventory — orchestrator

Read `AGENTS.md`, `PRODUCT.md`, the brand contract, the current owner continuation/testing instructions, this plan and the relevant ADRs below. Record target base, worktree status and prototype snapshot. Capture authenticated fixture-based baseline views and enumerate capabilities and public URLs. Resolve the capability gates above, including any justified backend packet.

Done: `docs/design/web-native-integration-status.md` contains the route/capability matrix, agreed file ownership, chapter/recency decisions, the complete settings row-to-destination/save-mode map and baseline artifact paths. Existing user edits are preserved.

### 2. Shared shell and one real vertical slice — orchestrator

Implement shared semantic typography, artwork composition and the on-demand menu through production components. Expose Movies/Shows through existing routes. Consolidate search triggers while retaining real search/command functionality. Carry one Movies → title → playback → Back flow through the real APIs and player before copying the composition to other surfaces.

Done: the slice works on real fixture data with permissions, Back/focus restoration and one search entry; focused shell/router/palette/title tests pass. The orchestrator’s preference packet extends `workspace.test.ts`, `workspaceAutosave.test.tsx` and the existing `api.test.ts` settings boundary as applicable: decode only valid values, patch only the changed music preference, retain sibling `ui_prefs`, honor explicit routes, and isolate account changes. Publish the shared component/token contracts to workers.

### 3. Media integration — three independent workers

| Worker | Owned responsibility | Completion evidence |
| --- | --- | --- |
| Video/Home | All files under `features/home/` and `features/library/`; `features/gallery/AllLanding.tsx`, `GalleryTitlePage.tsx`, `titlePage.css` and their corresponding tests. Shared gallery/shell files remain orchestrator-owned; propose their patches instead of editing them. | Home mixed-media handoffs; full movie/show catalog → correct details → correct season/episode → existing playback/Back. Real artwork, loaded/empty/error states; named test results and screenshots. |
| Music | All `features/music/` files; gallery `MusicTab.tsx`, `ArtistPage.tsx`, `AlbumPage.tsx`, `AlbumCard.tsx`, `albumQueue.ts` and corresponding tests; `backend/app/routers/music.py`, `backend/app/services/music_library.py` and `backend/tests/test_music_api.py`. Coordinate shared API client/types and workspace preference changes through orchestrator. | Recently played/added album data, complete album/artist/Saved access, artist↔album navigation, per-member preference, queue/session/casting/offline contracts retained. |
| Books | All `features/books/` files and corresponding tests. The Books worker also owns the dependent chapter backend packet after the source/contract decision: `backend/app/routers/books.py`, `backend/app/services/books.py`, `book_archives.py`/`book_native.py` only if the chosen boundary source requires them, and existing `test_books_api.py`, `test_book_access_views.py`, `test_book_archives.py`/`test_book_native.py` as affected. Shared schemas/models/migrations/rollback documentation go through the orchestrator. | Catalog → details → actual volume/chapter/page reader; EPUB and page-format positions remain distinct; Continue, format switching, sharing and permissions retain existing semantics. |

Done: each worker returns files/diff, retained capabilities, commands/results, desktop/phone captures and unresolved issues. The orchestrator integrates and checks cross-surface consistency before starting the next wave.

### 4. Requests, Streaming and Settings — three workers

| Worker | Owned responsibility | Completion evidence |
| --- | --- | --- |
| Requests | `features/requests/` and owned tests. | Real Discover hero, contextual search, request/quota/history/manage workflows and Sonarr/Radarr status behavior. Preserve Anime, title availability, retry/cancel and permission gates. |
| Streaming/watch | All `features/streaming/` files; `features/live/LiveSurface.tsx`; watch `WatchSurface.tsx`, `WatchQueue.tsx`, `WatchChatSeam.tsx`, `LiveChatPanel.tsx`, `WatchRelated.tsx`, `WatchInfoColumn.tsx`, `WatchTools.tsx`, `watch.css`, `player.css`, `tools.css` and their tests. The orchestrator owns shared `liveRecordingControls.tsx`, `chatReplayRail.tsx`, `resumeChoice.css`, lower-level player engines and API contracts; requests for those files are coordinated patches. | Larger relevant picks without duplicate cards; provider-appropriate search; Browse/Live/Channels, existing remote/local playback, read-only chat and recording actions retained where supported. |
| Settings | All `features/settings/` files and their tests, plus the applicable `frontend/src/v1_settings_*.test.ts[x]` contracts. Coordinate app-shell/routes, global tokens/UI controls, workspace preferences, shared API/types and backend changes through the orchestrator; these remain orchestrator-owned. | Both spaces match the approved settings design, every registry control remains reachable and correctly scoped, real fields/actions preserve their existing save/secret/permission contracts, and category/search/member/global navigation preserves dirty-state protection and focused deep links. Desktop rail/phone picker, caption preview, owner impact copy, advanced disclosures and dirty-only Save/Discard have real workflow evidence. |

Done: all three packets return their owned diffs and focused evidence, including the full settings registry coverage map. Shared shell edits stay with the orchestrator. Start an independent read-only reviewer only after an implementation worker frees a slot; keep maximum concurrency at orchestrator plus three workers.

### 5. Independent review, integration and delivery — orchestrator

Assign a completed worker a read-only cross-surface capability, keyboard, responsive, artwork/performance and design review. Require concrete findings tied to public flows, including personal versus owner settings and unsaved changes. Resolve findings before closing delivery. Validate the matrix below using the existing tests and real fixture workflows. Review the implementation against the pinned A reference and preserve current production capabilities absent from the mockup. Update the status document with final files, route behavior, test evidence, captures, backend/schema changes and remaining limits. Finish with the integrated code and a concrete review package; keep deployment and native/website releases outside this handoff's authorization.

Done: all required flows have evidence, no unresolved feature gap is described as finished, targeted checks pass, production bundles exclude review scaffolding, and the final report identifies the implementation revision and any remaining gate. Follow the current repository/owner rules before opening or merging a PR.

## Verification matrix

Use existing suites at the public workflow/service boundary. Extend them for changed behavior; avoid tests of CSS class names or one test per helper. Run commands with `mise exec --`; package scripts are authoritative.

| Contract | Focused regression anchors |
| --- | --- |
| Navigation/search/history | `frontend/src/app/Navigation.test.tsx`, `routes.test.ts`, `features/palette/CommandPalette.test.tsx`, `workspaceAutosave.test.tsx`. Test mouse auto-hide, keyboard protection/Escape, touch dismissal, history/deep links, one correctly scoped search and account separation. |
| Home/video | `features/home/HomeHero.test.tsx`, `features/gallery/GalleryTitlePage.test.tsx`; identify the current related video browser workflows in `frontend/e2e/`. Preserve exact-episode activation, resume/start-over, autoplay boundaries and scroll/focus return. |
| Music | `features/gallery/MusicTab.test.tsx`, `features/music/MusicPlayer.test.tsx`, `MusicPlaylists.test.tsx`, `MusicCuration.test.tsx`, `LuminaApp.music.test.ts`; `frontend/e2e/music-navigation.spec.ts`, `music-continuity.spec.ts`, `music-offline.spec.ts`. Add default/explicit route/member-persisted choice and real recency ordering coverage. |
| Books | `features/books/BooksSurface.test.tsx`, `BookDetail.test.tsx`, `BookReader.test.tsx`; `frontend/e2e/books.spec.ts`, `books-browsers.spec.ts`, `realstack/books.spec.ts`. Cover volume browsing without checkpoint mutation, real chapter mapping, unread start, last-location resume, format-specific state, authorization and Back/filter restoration. |
| Requests | `features/requests/RequestsSurface.test.tsx`, `requestsModel.test.ts`; `frontend/e2e/requests_quality.spec.ts`. |
| Watch/streaming | Existing `WatchSurface*.test.tsx`, `WatchChatSeam.test.tsx`, `LiveChatPanel.test.tsx`, `liveRecordingControls.test.tsx`, `chatReplayRail.test.tsx`; relevant `frontend/e2e/watch-start.spec.ts`, `watch-web-video.spec.ts`, `v1_chat.spec.ts`, `v1_recording.spec.ts`, `v1_watch_queue.spec.ts`. |
| Personal/admin settings | `features/settings/navigation.test.ts`, `unsavedChanges.test.ts`, `requestsSettings.test.tsx`, `HouseholdResources.test.tsx`; root `v1_settings_registry.test.ts`, `v1_settings_you.test.tsx`, `v1_settings_server.test.tsx`, `v1_settings_search.test.tsx`, `v1_settings_dpad.test.tsx` and affected credential/import/AI contracts. Verify every registry row remains reachable; personal/member/owner URL gates; autosave versus manual-save semantics; Save/Discard and failure retention; dirty guards across category/search/member/global-route navigation; focused deep-link search; secret replacement/removal without echo; distinct connection tests; and keyboard/mobile navigation. Extend existing tests rather than replacing their useful behavior with prototype-style checks. |
| Changed backend seams only | Music: `backend/tests/test_music_api.py`, `test_music_listening_session.py`. Books: `test_books_api.py`, `test_book_access_views.py`, relevant archive/native contracts. Extend existing settings contracts for a typed `ui_prefs` field. The new recently-played album read contract requires member-isolation/visibility/order/empty-history tests. Other backend tests are conditional on actual boundary changes. |

Examples: `mise exec -- npm --prefix frontend run typecheck`; `mise exec -- npm --prefix frontend test -- src/app/routes.test.ts`; from `frontend/`, `mise exec -- npx playwright test e2e/music-navigation.spec.ts` with that spec's required fixture/server setup. Build through the existing frontend build script. Read each workflow's configuration before running it; unavailable prerequisites are a reported limit, not a pass.

The current owner continuation document requires focused testing and reserves full repository/Core/native UI suites for a direct owner request. Apply that instruction alongside current `AGENTS.md`; record any unrun release/PR gate explicitly rather than silently running a prohibited broad suite or claiming it passed.

Capture all primary surfaces plus show/artist/album/book/volume details and both settings spaces (including playback preview, connections and dirty/error states) at desktop and phone widths, including 320px and a tablet breakpoint. Check 200% text zoom, short viewports, focus visibility/order, modal close/return, reduced motion, missing art, long metadata, empty/error/loading states, light/dark/high-contrast and permission-limited members. Meaningful real-stack playback/reader checks complement mock-API tests; captures alone do not establish streaming or device capability.

## Boundary references

Read on the branch that changes each boundary:

- Titles/ownership/visibility: ADRs 0009 and 0019.
- Artwork framing/cache: ADRs 0030 and 0034, plus `Artwork`'s current authenticated resource rules.
- Music edits/sessions/listening: ADRs 0023, 0026 and 0028. Preserve per-member queue revisions and conflict handling.
- Books: ADR 0022. Retain root/alternate-format assets, private/shared visibility, structured EPUB/raster delivery and archive bounds. Browser-local fake page counts never become production checkpoints.
- Requests: ADR 0018. Fulfillment uses configured Sonarr/Radarr and current policy; availability requires a visible imported library match.
- Streaming/chat: ADRs 0008 and 0021, and `0029-personal-youtube-sessions-are-playback-only.md`. Preserve existing provider configuration, refresh/auth failures, opaque emote artwork, recording retention and read-only chat. YouTube search does not imply Twitch/Kick global search; optional Kick stays available where configured.
- Schema work, if required: current `MIGRATIONS` contract and `docker/README.md` rollback documentation. Deployment, when separately authorized, follows its retained-image procedure.
