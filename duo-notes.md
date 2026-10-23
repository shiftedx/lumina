# iPhone Duo reader handoff
UI polish scope (user, 2026-10-22): whole native iOS app; Netflix-inspired media presentation with Lumina branding.
Current stage: Home, Library, Requests and Account use four managed system tabs. Library owns Movies, TV, Anime, Music and Books. Requests is a poster-led catalog with a compact feature, rails, filtered poster search, production request details and member history.
Trailer behavior: Requests retains catalog trailer metadata only. A validated YouTube watch URL opens through the system handler, preferring the YouTube app and otherwise the system browser. Trailer handoff remains outside Lumina's media pipeline.
Playback: library media keeps Fit as the default source-preserving mode and offers explicit Fill. Existing library playback behavior remains separate from Requests trailer handoff.
Verification: fresh native build, Core contract run, standard-iPhone and Duo screenshots, request workflow and external trailer handoff are pending the parent’s serialized results. Do not treat earlier media-player evidence as proof of the current Requests trailer behavior. The reader evidence below belongs to the earlier Books handoff.

Journey: Books library → native reader → contents/appearance → resume.
Stack: SwiftUI + UIKit; iOS 18 deployment; Xcode 27.1 SDK.
Stage: Functional reader and visual library; UI redesign is a separate follow-up.

## Preserve during redesign
- Native Connected-app credentials use the same member visibility as the web Books service.
- The selected outer-screen page/content stays physically right when unfolded, including RTL.
- LTR companion precedes the selected page; RTL companion follows it. Horizontal fold puts selected content below.
- EPUB stores a UTF-16 content anchor through reflow; artwork stores its selected page.
- Standard iPhone defaults to Fit entire page; Duo defaults to Fill display. Explicit Appearance choice overrides.
- Layout follows safe areas, actual size classes and reserved fold/camera regions, without device-name/angle branches.
- EPUB/Fit retain safe areas; immersive artwork fills the viewport. Native pan/zoom exposes cropped pixels.
- Progress writes are ordered/coalesced and failures offer retry. Each file retains its own position.
- Covers, shelf memberships and manga-series stacks use real service data and bounded shared thumbnails.
- Only visible pages are decoded, off the main actor; encoded lookahead is bounded and abandoned work cancelled.

## Evidence and limits
- Full make check passes; shared native Core 78 tests pass with no skips/failures.
- Four focused Books Core contracts pass, including failed-prefetch recovery.
- Full-to-bounded decoded bitmap storage: 96,000,000 → 25,165,824 bytes (73.8% smaller).
- Preparation/materialization median: 65.341 → 116.671 ms; no latency gain claimed.
- Actual Device Hub pose checks covered outer/inner, both rotations, both fold directions, keyboard, accessibility and both Split View sides.
- Final native workflows pass: iPhone library + four-format Fit/zoom/actual rotation/restore; Duo library + four-format RTL/right/Fill/zoom/restore.
- Final-build outer Page 3 → unfolded Page 3 physically right verified in Device Hub; Page 4 is the left companion. Other pose checks preceded library refinements.
- XCTest orientation does not resize Duo here; actual automated resizing uses standard iPhone.
- External Safari PiP was not exposed in this simulator and remains unverified.
- No tvOS reader was added; the existing tvOS app builds against shared Core changes.
- Details: apple/docs/native-book-reader-2026-10-21.md; local artifacts: output/native-book-reader/.

## Native Home shelf editing — 2026-10-22
- Home options → Edit Home provides native drag handles and visibility switches for the three existing native rails: Continue watching, Next up, and New in your library.
- Done persists to the same member preference as the website; Cancel discards the draft, and Reset restores defaults. Website-only shelf entries and other UI preferences are preserved.
- The hero stays outside shelf ordering. This change does not add the website's other eleven data feeds to native Home.
- Focused Core and backend contracts passed; the Duo workflow verifies drag order, hide, Cancel, relaunch persistence and reset. See apple/docs/native-requests-duo-2026-10-22.md for evidence and limits.


## Comprehensive native Account settings — 2026-10-22
- Account now exposes the website's personal settings through one searchable native Form. Controls are direct; sensitive actions keep password/second-factor checks. Server and household management are excluded even for owners.
- Profile/security, requests email, appearance, playback/captions, interests/hidden picks, providers, downloads, privacy/history/export, connected apps, music exports, private YouTube session, personal watch-history imports, and read-only allowances/own job cancellation use explicit member-scoped adapters.
- Settings share website preferences and preserve sibling keys. Native playback uses quality/loudness/track defaults; browser-only autoplay/caption styling are labeled.
- Focused checks pass: Settings Core 1; backend Settings/security/discovery 8; standard-iPhone workflows 2; final Closed Duo workflow 1. Light/dark and security/search screenshots were inspected. Full gate stays deferred until before merge.
- Evidence and limits: apple/docs/native-requests-duo-2026-10-22.md.

## Hero footer and actions — 2026-10-22
- Carousel indicators now sit in the hero footer below actions, including Closed Duo.
- Compact gold Play + subtle Details on Home; Play + real Watchlist on movie/show details. Play retains resume behavior. Favorites remain in overflow.
- Native Watchlist shares the website WatchQueue with explicit member-scoped GET/PUT adapters.
- iOS build, Watchlist Core 3 and backend adjacent contracts 13 passed; real Blender Closed Duo Home/detail/watchlist workflows passed in light and dark. Screenshot evidence and limits are in apple/docs/native-requests-duo-2026-10-22.md. Full gate remains deferred until before merge.
- The focused Closed Duo hero/playback workflow also passed: rotation, artwork swipe, footer geometry, Details/back, saved-position resume and persisted playback progress (`/tmp/lumina-hero-resume-duo-3.xcresult`).

## Title artwork before text — 2026-10-22
- Home/detail billboards prefer authenticated Logo artwork; Requests feature/detail headings prefer catalog logo_url. Text remains the missing/loading/failed-image fallback.
- Logos preserve aspect, alpha and original colors, stay within heading bounds, and expose the media name to VoiceOver.
- Targeted Core artwork check, native build, and Closed Duo logo/fallback workflow passed. Evidence: apple/docs/native-requests-duo-2026-10-22.md.

## Home overflow placement — 2026-10-22
- Removed Home's hero-overlay menu; Home options is now native topBarTrailing, placing it below the time/service indicator in the Duo right rail.
- Native build and actual Closed Duo screenshot verified. Revised Edit Home/Cancel regression awaits rerun: another workspace held the shared runner, so the queued rerun was cancelled. See apple/docs/native-requests-duo-2026-10-22.md.

## Home footer cleanup — 2026-10-22
- Removed the listening-room promotion and Explore all video button. Home ends after its media shelves; destination switching belongs to primary navigation.
- Removed HomeLibraryView's unused destination/category bindings. Swift syntax and diff checks passed. Build could not acquire the occupied performance lock and exited without running; no runtime build is claimed for this small removal.

## Music overflow cleanup — 2026-10-22
- iOS Music moves Playlists, Mixes, Downloads and Queue into the existing Music options toolbar menu, above the browse picker. Removed the in-content shortcuts row. Apple TV keeps its existing links.
- Updated the existing native playlist/download workflow to open overflow first on iOS. Swift syntax and diff checks passed. Build/runtime validation remains pending because another workspace still holds the shared performance lock; the build attempt exited without running.

## Native external-streaming opt-in — 2026-10-22
- External streaming settings/search, remote cache/download defaults and personal YouTube controls default hidden. Server admin opt-in: Settings → Server → Connections → Media apps → External streaming in the native app; member restrictions still apply. Local playback settings remain.
- Missing or failed capability responses fail closed. Native YouTube credential endpoints enforce the same server/member gate; no native admin toggle was added.
- Backend 9, final Core 3, Closed Duo Account and Home-overflow workflows 2 passed. Final iOS build passed, covering the previously build-blocked Home-footer/Music-overflow edits too. See apple/docs/native-requests-duo-2026-10-22.md. Full suite remains deferred until before merge.

## Native streaming is an admin server setting — 2026-10-22
- Replaced the environment opt-in with the saved External streaming in the native app switch under website Settings → Server → Connections → Media apps.
- Admin-only, defaults off, schema migration 34; no restart or native admin UI. Existing capability/member restrictions remain authoritative.
- Backend focused checks 59 passed, frontend settings tests 39 passed, TypeScript checks passed. See apple/docs/native-requests-duo-2026-10-22.md for details. Full suite remains deferred.
- The focused mocked browser media-admin save flow passed too, verifying the independent toggle payload preserves the Infuse setting.
