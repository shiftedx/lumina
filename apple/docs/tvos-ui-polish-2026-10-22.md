# tvOS artwork and navigation pass — 2026-10-22

The main destinations are Home, Video, Audio, and Requests. Account remains in the profile control. Video has Movies, Shows, Anime, and YouTube pivots; YouTube lists saved provider videos, independently of permission to acquire new streaming media. Audio retains album/artist/track browsing and the existing playlist, mixes, downloads, and listening queue routes.

Home paints the featured artwork behind the top navigation, then presents horizontal media shelves without destination cards or a greeting block. The server's member-scoped `/Lumina/Home/rows` adapter uses the website's underlying sources and shared shelf order/visibility. It includes continue watching, next up, watchlist, new library titles, because-you-watched rows, recommendations, smart video collections, recently saved media, anime, music, and continue listening. Empty shelves are absent. Remote/live-only sources (`live`, `picked_for_you`, `from_follows`) and non-video smart collections are not represented by this adapter; this is not complete web Home feature parity.

Title details prefer original title-logo artwork and preserve its colors and aspect ratio, with readable text on missing/failed artwork. Film details show available genres, official and community ratings, cast, year, runtime, progress, and story. Primary controls are Play/Resume and Watchlist; watched/favorite/compatibility actions live in More. Series and seasons provide Play next/Resume next and retain season and episode navigation. No unavailable metadata or source-quality badges are invented.

Requests uses poster rails, an artwork-led feature, search and kind/genre browsing with pagination, status/history, quotas, and season/language request controls. It does not relay trailer media. A validated external YouTube link attempts to open the installed handler and reports failure when the Apple TV cannot open it. Quota controls stay disabled while loading or after a failed quota request.

## Verification

Focused backend Home contracts: 4 passed. Focused saved-channel contracts: 12 passed. Core `LuminaCoreTests`: 23 passed. Swift syntax and scoped whitespace checks passed.

The first TV target build exposed a dynamic String/LocalizedStringKey mismatch in a collection heading; this was corrected. The first runtime workflow exposed an actual top-navigation-to-hero focus gap; the fix explicitly transfers Down into the active section's first useful control, without starting playback. Subsequent native results and screenshots are recorded separately after the focused workflow completes. No full repository gate, release archive, merge, upload, or deployment is established by this pass.

Native category queries now match the website's Movies and Anime walls through the narrow `LuminaCategory` extension: Anime includes films and series, and Movies excludes anime films. Existing third-party Jellyfin views keep their original grouping. The focused category/channel cohort passed 27 cases; the additional native Core taxonomy contract passed once.

The top navigation uses a custom text button style with the system focus effect disabled. A thin underline marks selection, and restrained text emphasis/underline marks remote focus. There are no background capsules, outline rings, or tinted panels behind the four main destinations or the profile icon. The header gradient spans the full artwork canvas rather than ending at the TV's safe-area edges.

Native evidence: show → season → episode → direct playback passed in `/tmp/lumina-tv-navigation-4.xcresult` (93.530 s). Navigation/artwork passed in `/tmp/lumina-tv-navigation-5.xcresult`; corrected artwork bounds/typography passed in `/tmp/lumina-tv-navigation-6.xcresult`; the unobtrusive main navigation passed in `/tmp/lumina-tv-navigation-7.xcresult` (115.018 s). These are focused case-level results, not a claim that every earlier result bundle passed. The full-suite merge gate remains pending.

Final combined navigation and artwork capture passed in `/tmp/lumina-tv-navigation-8.xcresult` (119.871 s), including Home/film details, Movies/Shows/Anime/YouTube pivots, Requests discovery/detail, and Audio album artwork. Seven 1920×1080 captures were exported to `output/tvos-ui-polish/final/`; `review.md` links the inspected screens. This final capture includes the plain main-navigation style, full-canvas Requests imagery, and typography corrections. Public-film media/artwork are real; the disposable discovery fixture contains controlled provider metadata and ratings, so its metadata is not an authoritative film reference.

## Dogfood repair

Production was still serving an image without the native Home, Requests, and Settings adapters. The current application source was deployed using the verified existing runtime (Dockerfile and runtime dependency lock unchanged), with the original production Compose overrides and mounts. A consistent database backup and stopped rollback image pin were retained. Post-deploy authenticated checks returned 200 for Home layout/rows, Requests catalog/quota, and Settings; the public health and server-discovery checks also returned 200. A temporary validation credential was revoked after the checks. No reviewer-demo service was replaced.

TV poster captions now sit directly on the page rather than inside the system card plate. Focus outlines the artwork and preserves remote navigation. Featured title buttons use an underline for focus instead of the system plain-button background. Normal video playback uses AVKit controls without the duplicate custom Close, Fit/Fill, or Play overlays.

The focused native API cohort passed 78 tests. The TV Home, film details, Movies/Shows/Anime/YouTube, Requests discovery/detail, and Audio workflow passed in `/tmp/lumina-tv-dogfood.xcresult` (106.982 seconds); its screenshots were inspected. The earlier repository gate passed 5,300 backend tests and 2,645 frontend tests, then was interrupted during real-stack browser testing to prioritize the TV run. It is not a completed full gate. Physical-device installation remains separate from simulator evidence.

Internal tvOS 0.1.0 (8) passed archive, signed IPA, and Organizer validation and uploaded successfully on 2026-10-22. Apple reported processing. Source/resource hashes stayed unchanged during archiving. Device Testing assignment remains pending; no external beta or public Store submission was performed.
