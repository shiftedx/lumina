# Native iOS cinematic UI polish — 2026-10-21

The iOS client follows the supplied Netflix and Apple TV references: artwork leads, controls remain understated, and the same library hierarchy adapts between the outer display, unfolded display and standard iPhones. Lumina retains its sunrise, warm accent and editorial title face. No backend, credential or media-service boundary changes are introduced.

## Presentation

- A system-managed TabView replaces the duplicated tab/sidebar shell. Each destination retains its NavigationStack through size changes.
- Home and title/collection details use full-width cinematic artwork and readable gradients. Artwork extends beneath the system rail, while copy and controls respect its inset. Title details expose Play/Resume and Favorite; Mark Watched and conversion live in the navigation overflow menu. Poster rails and catalog grids are denser; episode shelves use landscape still cards. Large text switches episode cards to a flowing vertical layout.
- Video retains one AVPlayer and one structurally stable AVPlayerViewController through fold/rotation. Fit preserves the source aspect ratio by default on every device and pose; explicit Fill crops to the current display and persists. Legacy Automatic preferences resolve to Fit. Horizontal reserved fold regions place video above and transport/scrubbing controls below.
- Audio keeps its real queue, playlists, downloads and listening controls. Books keeps the reader handoff, bounded cover worker and physical-right selected-page invariant. Native management lists and adaptive action rows remain accessible.
- Dark surfaces are neutral theater black, with adaptive light and increased-contrast variants. Sign-in, Account and filter sheets use the same hierarchy.
- Home has no separate black header overlay. Its masthead reads “Lumina”; the sunrise horizon aligns with the wordmark baseline. Featured titles are 25% smaller and reserve scaled space below the masthead. Home has no category buttons on any display; catalog type and filter options live in Video’s overflow. Artwork continues directly beneath the trailing rail without a reflected or blurred extension.
- The five native tabs use bespoke image-generated Lumina artwork, exported as 28-point template assets with the original transparent sources and prompt direction retained in `apple/Assets/NavigationSources/`.
- Video browsing removes the editorial masthead and visible type/sort pills. A compact native overflow contains Type, Sort and Filters. The catalog keeps ordered 2:3 covers with an occasional two-column lead poster beside smaller covers; narrow phones and accessibility sizes adapt the same content. Captions are hidden in this wall while complete title/metadata labels remain accessible.

## Regression coverage

The existing Home/Account workflow now retains the useful full-viewport/no-duplicate-navigation contract while replacing the old decorative Welcome-heading measurement with the branded native masthead. It still checks appearance changes, selected-feature retention and detail/back navigation. Home now asserts that category controls are absent, while Video retains its options menu. The existing playback workflow now checks advancing time through an actual standard-iPhone rotation and back, plus persistence of an explicit Fit selection. Existing server progress, direct/compatible playback, filters, episode, Audio, playlist/download and four-format Books workflows remain.

## Verification

Verification is in progress. Local logs, result exports and screenshots are under `output/ios-ui-polish/`. Only checks recorded as complete in the final revision qualify the delivered source.

| Check | Result |
| --- | --- |
| Full repository gate | Stopped at user request during browser checks; full suite deferred until before merge |
| Shared native Core and iOS/tvOS builds | 79 Core tests passed, zero failures/skips; both generic simulator builds passed; signed iOS test build passed |
| Focused Duo playback workflow | Passed after the paused-player resume control fix: advancing playback, background/resume and reachable detail/player controls; latest icon change pending |
| Standard-iPhone native workflows | Passed: watched/favorite/activity filters (227 s), and final Home/Account appearance, selection retention, favorite save/restore and Movies/Shows routing (117 s; superseded by removing Home categories) |
| Actual Device Hub Duo poses and continuity | Open landscape → closed portrait → open landscape continued real 4:3 playback; explicit Fill crops and restoring Fit preserves the complete source. Other pose evidence predates final refinements |
| Real-media visual review | Open 16:9 Fit, open/closed 4:3 Fit/Fill, mixed-size Duo catalog and compact Video overflow screenshots captured in `output/ios-ui-polish/screenshots/`; closed-display category removal verified in actual Device Hub Closed/Open poses; final all-display removal built successfully and verified in both Closed and Open poses |

## Visual-review media

The visual catalog uses real files imported by the production Lumina service, with original film frames as local artwork. It is separate from the deterministic synthetic regression catalog. Neither catalog is bundled into the app. Download/source records are retained locally in `output/ios-ui-polish/open-media/sources.json`; disposable credentials stay in ignored, restricted fixture files.

- [Big Buck Bunny](https://peach.blender.org/download/), [Sintel](https://durian.blender.org/download/), Caminandes: Gran Dillama, Elephants Dream and Tears of Steel: Blender Foundation open films/trailers from [Blender's official download archive](https://download.blender.org/demo/movies/). Film/frame attribution: Blender Foundation / Blender Institute, Creative Commons Attribution.
- Tears of Steel original soundtrack, “40 Years Later” and “End Credits”: © Joram Letwory / tearsofsteel.org. Original audio is retained; only catalog metadata is supplied. [Soundtrack license: Creative Commons Attribution–NoDerivs 3.0](https://download.blender.org/demo/movies/ToS/Tears-Of-Steel-OST/copyright.txt).
- True 4:3 playback: [The Penwiper (1926)](https://commons.wikimedia.org/wiki/File:The_penwiper_(1926).ogv), Prelinger Archives public-domain/CC0 release. Original 400×300 video was converted to H.264/AAC without cropping, stretching or changing aspect ratio.
- Public-domain books: [Alice's Adventures in Wonderland](https://www.gutenberg.org/ebooks/11), [Pride and Prejudice](https://www.gutenberg.org/ebooks/1342), and [The Adventures of Sherlock Holmes](https://www.gutenberg.org/ebooks/1661), from Project Gutenberg.

Simulator evidence does not establish physical hinge timing, display/HDR quality, battery use or hardware audio output. No archive, upload, deployment or commit is part of this pass.


## Dogfood Home follow-up — 2026-10-22

This follow-up supersedes the earlier playback sizing and Home presentation details where applicable. Normal video now uses AVKit controls without persistent duplicate Close or Fit/Fill overlays; custom Close remains for terminal/error states and the separate Duo tabletop controls remain.

Home portrait cards resolve the parent series poster for episodes and seasons, while landscape cards retain still artwork. Mobile now loads shared Home rows and exposes all 14 shared shelf choices in Edit Home, with saved order and visibility. Member-filtered external metadata uses source-app/site handoff. This adds feed choices without adding native remote playback. Default TV feed requests preserve compatibility.

The focused backend cohort passed 70 tests, final Home regression file passed 5, Core cohort passed 24, Home feed/layout suite passed 2, and the iPhone editing workflow passed in 110.239 seconds. Simulator build and unsigned device archive validation passed. Full repository checks remain deferred until before merge. The Home adapter was deployed with authenticated health/data checks and a stopped rollback pin. iOS 0.1.0 (9) remains archived but not uploaded: Xcode account visibility differs between the UI and command-line exporter after account refresh.
