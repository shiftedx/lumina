# Native Requests and unified Library

The native iOS shell has four system-managed tabs: Home, Library, Requests and Account. Library supplies Movies, TV, Anime, Music and Books through its category control. Each category keeps its own native overflow controls; Home stays focused on artwork and playback.

Requests is a poster-led catalog powered by the existing connected-app adapter at `/Lumina/Requests`. It retains the household's server authority for catalog access, quotas, seasons, anime language selection, request creation, availability, screen time and token revocation. Connected-app credentials never grant access to browser `/api` routes.

The Requests landing page has one compact featured title and horizontal poster rails. Global catalog search and category browsing use an adaptive poster wall; the option menu exposes the member's request history. Title details provide the same production request choices, quota state, submission status, recommendations and history as the server API.

Trailer metadata remains part of a catalog detail. Lumina validates the provider ID and constructs one `https://www.youtube.com/watch?v=…` URL. Choosing Trailer hands that URL to the system, which opens the YouTube app when available or the system browser. Trailer handoff remains outside Lumina's media pipeline.

The Library and Requests tab artwork was generated with the built-in image-generation tool and exported as template assets. Sources and prompt direction are retained in `apple/Assets/NavigationSources/`.

## Verification

The focused native Requests, Jellyfin API and music-boundary backend checks passed: 73 tests. The former trailer playback route also has a retained 404 regression. Swift syntax parsing and whitespace checks passed.

The external-YouTube Requests revision has the following targeted native verification status:

- Core Requests contracts passed: 3 tests covering authenticated catalog, paginated search, section/genre browsing, request creation, structured errors and safe YouTube URLs.
- iPhone Requests workflow passed: catalog browse, cancel without server submission, global search, season selection, English dub, server-confirmed creation and visible personal history. Result: `/tmp/lumina-native-requests-posters-phone-8.xcresult`.
- Fresh real-artwork Duo screenshots and manual external trailer handoff remain pending. Device Hub reported the Mac locked before that visual pass.
- Signed native test build passed. The full repository gate remains deferred until before merge, as requested.

The deterministic Requests workflow uses disposable TMDB/AniList upstream fixtures and the production server/API. Its preflight now verifies that the anime search result is present before publishing the fixture. No provider video, credentials or media files are bundled into the shipping app.

Requests discovery uses debounced provider search with additional result pages, category-specific browse collections, and provider genre filters in overflow. Results preserve poster titles, years and request availability. Replacing a query or refreshing a collection invalidates older page loads; failures retain the loaded results and offer retry.

## Editable native Home

Home options → Edit Home opens a native reorder list for Continue watching, Next up, and New in your library. Drag handles change their order; switches show or hide rows, including currently empty rows. Done saves, Cancel discards the draft, and Reset to default removes the account override. Native Move up/Move down accessibility actions are also available.

The connected-app `/Lumina/Home/layout` adapter reads and writes the website’s `ui_prefs.home_shelves` preference. Edits preserve the other website shelf entries and unrelated user preferences. Only the three current native media rails are rendered and editable; the other website feeds are not added by this change. The hero remains outside row ordering. Native preferences refresh on Home pull-to-refresh and foreground entry. An already-open website Home needs a refresh to read a native edit; this adapter does not emit the browser settings event.

Targeted Home verification passed:

- Core authenticated load/save/reset and failure retention contract: 1 test.
- Home layout and native Requests adapter backend checks: 5 tests, including member isolation, shared preference preservation, normalization, validation and reset.
- Closed Duo native workflow: drag reorder, visibility change, Cancel without saving, preservation of website-only entries, persistence after relaunch, and reset. Result: `/tmp/lumina-home-layout-duo-6.xcresult`.
- The workflow screenshot was exported and visually inspected at `output/ios-ui-polish/home-layout-duo-pass-6/D2E69454-11A3-4125-92D2-DEA2927A2AE8.png`.

The full repository gate stays deferred until before merge. This focused Home pass covers the Closed Duo editor; it does not repeat every pose or the standard iPhone matrix.

## Comprehensive native Account settings

Account replaces the former appearance-only card with a searchable native Form. Search results contain the controls themselves; there are no category → subsection → setting navigation chains. A toggle needs one interaction and a menu choice two once Account is open. Text fields save from the keyboard or an adjacent Save action. Identity checks, external authorization and destructive confirmations retain their required steps.

The surface covers the website's personal settings: profile name; password and two-step verification; request emails; four appearance choices; streaming quality, sound, caption and language defaults, automatic skips, word muting and stream cache; interests and hidden recommendations; streaming providers; download defaults; data export and search/recommendation history; connected apps and app passwords; Last.fm/ListenBrainz export and outcomes; private YouTube session; personal Jellyfin/Plex history preview/import; and read-only allowances with cancellation of the member's own AI jobs. Server management and household administration are absent for every role.

`/Lumina/Settings` uses the same durable member preference record as the website. Native edits patch the relevant preference key and preserve sibling fields. Dedicated member-scoped actions reuse the existing personal services; they do not turn the playback token into a browser `/api` credential. Sensitive mutations require an account-password/second-factor check and a five-minute, token-bound, one-use proof held only in memory. The security sheet covers its contents while inactive, allowing an authenticator handoff without exposing one-time secrets in the app switcher. Secret inputs clear on background and all sheet secrets clear on dismissal.

Quality limits, loudness and audio/subtitle track defaults apply to native playback through the existing playback adapter. Autoplay of related videos and custom caption styling remain browser preferences; the UI states this. Full-screen iPhone/iPad captions use the system style. Credentials for external providers and imports are never bundled into the app or saved in its preferences.

Focused verification: Settings Core contract passed (1 test); native settings/member/security and discovery/connection backend contracts passed (8 tests). The final Closed Duo search/edit/relaunch and in-app security-screen workflow passed at `/tmp/lumina-account-settings-duo-3.xcresult`; its screenshot is `output/ios-ui-polish/account-settings-duo-pass-3/4AC80081-F04E-4F36-939C-7280FF382443.png`. The complete disposable server also returned successful JSON responses for all 16 personal-settings read endpoints. Standard-iPhone settings and appearance workflows passed at `/tmp/lumina-account-settings-phone-3.xcresult` (2 tests): all four themes, Home selection/back navigation, settings search, sibling-preserving edits, relaunch persistence, and the in-app identity-verification sheet. Light/dark and security screenshots were exported to `output/ios-ui-polish/account-settings-phone-pass-3/` and visually inspected. The full repository gate remains deferred until before merge.

## Hero footer and minimal detail actions — 2026-10-22

Home carousel indicators now occupy a reserved footer below the title actions on compact and wide layouts. They no longer use a separate 16:9 overlay that put the controls halfway up Closed Duo artwork. Primary actions use a compact warm-gold Play button and a subtle rectangular secondary action, with 44-point minimum targets. Home uses Play + Details; movie/episode and series/season details use Play + Add to watchlist. Play preserves the saved resume position. Favorites and watched/conversion actions remain in the detail overflow.

Watchlist membership uses the existing website WatchQueue through explicit member-scoped `/Lumina/Watchlist/{item_id}` GET/PUT routes. Series resolve their current play-next episode and seasons their first visible episode; no separate native list is created.

Targeted service checks passed: Watchlist Core 3 tests; backend Watchlist plus adjacent Settings/Home/Requests contracts 13 tests. The iOS build passed. Closed Duo Home → Details → add/remove watchlist passed against actual Blender media in both light and dark appearances, including an assertion that the indicator footer sits below Details. Dark result: `/tmp/lumina-hero-actions-duo-dark-1.xcresult`. Visually inspected Home screenshot: `output/ios-ui-polish/hero-actions-duo-dark-pass-1/692DA0D0-870F-47B0-9887-DF9609C84942.png`. This focused iteration does not repeat the full device/pose matrix or repository gate.

The deterministic Closed Duo hero workflow also passed at `/tmp/lumina-hero-resume-duo-3.xcresult`: automatic rotation, artwork swipe, footer placement, Library navigation, rotation, Details/back, stable Play label, playback resuming at the seeded 12-second position, and subsequent persisted progress. The existing test now swipes artwork rather than its clickable title and uses the current lazy Home shelf caption.

## Title artwork before text — 2026-10-22

Library Home and movie/show billboards now prefer the server's `ImageTags.Logo` artwork through the authenticated Logo endpoint. Requests feature and detail headings similarly use `logo_url`. Images keep their supplied proportions, transparency and colors within bounded heading sizes; the existing text heading remains the fallback during loading, when absent, or after a failed load. The image exposes the media name as an accessible heading; Home keeps its named Details button.

Focused checks passed: existing Core connection/artwork URL contract (1 test), iOS build, and Closed Duo logo → Details → no-logo fallback workflow (`/tmp/lumina-title-logo-duo-2.xcresult`). The workflow uses an original transparent synthetic wordmark to exercise image replacement independently of provider availability. The authenticated endpoint was also verified to preserve alpha. Home and detail screenshots were exported under `output/ios-ui-polish/title-logo-duo-pass-2/`; Home screenshot `5DFE075D-7E75-43EA-A979-98CA3C00123D.png` was visually inspected. Full checks remain deferred until before merge.

## Home overflow placement — 2026-10-22

Home options now belongs to a native top-bar-trailing ToolbarItem, matching media details. Removed its custom overlay from HomeHeroChrome. Closed Duo places it in the right system rail below the time/service indicator rather than at the trailing edge of the hero content. Navigation background remains transparent; platform safe areas remain managed by the system.

The native build completed and actual Closed Duo screenshot was inspected: `output/ios-ui-polish/home-overflow-duo-1/6F8345FF-5775-414F-B5F8-99806EEEF30E.png`. The first interaction assertion incorrectly searched only NavigationBar; Duo exposes the system rail as Toolbar. The regression check now accepts either native container and exercises Edit Home/Cancel. Its rerun was cancelled while still queued behind another workspace's long-running performance tests; no passing interaction rerun is claimed for this iteration. Full checks remain deferred until before merge.

## External streaming settings are server opt-in — 2026-10-22

External-provider settings are absent from Account and its search results unless an admin enables **External streaming in the native app** in the website’s **Settings → Server → Connections → Media apps** page and saves. The persisted server setting defaults off. Hidden groups include provider preferences, recent remote-stream caching, browser related-video autoplay and remote acquisition/download-format defaults. The personal YouTube-session section is separately gated by the server flag and the member's YouTube access; it is not fetched while unavailable, and an open YouTube sheet closes when its capability disappears. Other personal connections remain available. Local library playback controls remain; Streaming quality is now labeled Video quality.

Authenticated `/Lumina/Settings/capabilities` is the authority. Missing, malformed, or failed capability responses hide the features, so older servers do not accidentally enable them. Native YouTube-session reads/writes/deletes also enforce the flag and member policy before reading status or consuming a security proof. The setting is server-owned, not a personal preference or an in-app admin control, and does not change browser Streaming behavior.

Focused verification passed: backend native settings/discovery 9 tests; final Core settings capability contracts 3 tests; Closed Duo Account search/edit/relaunch/security and Home native overflow/editor 2 workflows (`/tmp/lumina-streaming-hidden-duo-1.xcresult`). The UI fixture is an older server without the capability endpoint, verifying fail-closed behavior; explicit opt-in, admin and blocked-provider combinations are covered by backend/Core contracts. Final signed iOS simulator build succeeded (`output/ios-ui-polish/native-streaming-final-build.log`), also compiling the preceding Home-footer and Music-overflow changes. Current Account screenshot was inspected under `output/ios-ui-polish/streaming-hidden-duo-pass-1/`. No full repository gate was run.

## Persisted admin control replaces environment opt-in — 2026-10-22

The native streaming opt-in is now a saved **External streaming in the native app** switch in **Settings → Server → Connections → Media apps** on the website. Admin media-server GET/PUT reads and writes `AppSettings.native_streaming_enabled`; new and upgraded databases default to false. Schema migration 34 adds the Boolean column. Operator rollback instructions preserve the additive column and saved value. There is no environment override or native-app admin control. Member restrictions and the native capability contract are unchanged, and a newly saved value is read on the next capability request without restarting the server.

Focused backend verification passed: 59 tests covering admin defaults/persistence/permissions, native capabilities and YouTube guards, schema upgrade and documented rollback/re-upgrade. Frontend focused settings tests passed (4 files, 39 tests) and TypeScript checks passed. Full repository gate remains deferred until before merge.

The focused mocked browser media-admin save flow also passed (`e2e/v1_admin_media.spec.ts`, “TMDB key” journey): saving the native toggle sends only `native_streaming_enabled: true` after the Infuse setting was saved, preserving sibling configuration.
