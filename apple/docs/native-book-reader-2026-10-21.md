# Native iOS Books reader — 2026-10-21

The native app previously had no Books tab or reader. This change adds a searchable,
paginated catalog and a native full-screen EPUB/PDF/CBZ/CBR reading workflow using the
existing format-aware Books service. It does not parse archives or render their HTML on-device.

The library presents actual covers in an adaptive grid, a Continue Reading rail, shared/private shelf stacks, and manga-series stacks. Shelf and series navigation uses the same service memberships and visibility rules as the web library.

## Continuity and layout

The selected chapter/page and relative content anchor are independent of the viewport.
Expanded reading adds a companion on the left and keeps the outer-display content on the
physical right, including for RTL manga. In LTR, the left companion precedes the selected
page; in RTL it follows it. Closing the display retains the selected page. A single-page
option remains available. EPUB uses native attributed text, selection, links, inline images,
Dynamic Type and a text enlargement control. A cover occupies an otherwise empty bookend pane, while the selected page stays right. Format selection retains each file's own state.

Artwork defaults to Fit entire page on standard iPhones and Fill display on iPhone Duo (the user’s choice), with native pan and pinch/double-tap zoom to reach every pixel. The SDK’s guarded `UIHingeInteraction` reports hinge capability even when the reader uses a narrow viewport; it sets the initial preference without model-name checks or changing pane geometry. An explicit Appearance choice overrides the device default for the reader session. The reader opens with the full controls hidden; a small native glass button or a page tap reveals the system toolbars. EPUB keeps that reveal action in a managed system bar so it cannot obscure the text. Fit and prose layouts retain safe areas; Duo Fill extends artwork through the available viewport. Artwork has no decorative outer padding, and expanded pages have a narrow gutter. Prose retains readable margins.

System-managed bars and sheets provide reading controls. Layout uses the actual safe-area
viewport and size classes; it does not branch on device names or hinge angles. The installed
iOS 27.1 SDK confirms `GeometryProxy.reservedRegions(kind:options:layoutDirectionBehavior:)`;
active fold/camera frames and their asymmetric margins are excluded from the custom panes.
Physical coordinate queries remain fixed in RTL. The page-positioning container also uses a physical LTR coordinate space; each page restores the original locale, and system bars retain their normal locale behavior. A horizontal fold separates upper/lower
panes. The existing library hierarchy remains a system NavigationSplitView/TabView.

## Performance method and results

There is no previous native-reader startup or page-turn baseline. The component control
compares full-resolution ImageIO decode with the production bounded page preparer. It
uses an original 6,000 × 4,000 JPEG, 21 pairs with alternating order, Release compilation,
an owned RGBA CGContext draw and consumption of an alpha byte on both paths, and `bytesPerRow × height` for owned decoded bitmap storage.
The shared performance lock serializes cooperating jobs; unrelated host activity remains possible.

| Component | Full decode control | Native bounded decode |
| --- | ---: | ---: |
| Decoded bitmap bytes | 96,000,000 | 25,165,824 |
| Decode median | 65.341 ms | 116.671 ms |

The bounded bitmap uses 73.8% less decoded storage in this cohort. It is slower to prepare
in this host sample; no decode-latency, page-turn, energy, frame-rate or RSS improvement is
claimed. Preparation runs away from the main actor. The page window retains current/spread
bytes plus at most one encoded lookahead, and speculative work is cancelled on abandonment.
Only visible pages are decoded. The library shares one session-bound thumbnail worker across grid/shelf/series navigation. Requests for the same cover are coalesced; ImageIO prepares thumbnails off the main actor at the server’s 600-pixel longest edge. Its NSCache has a 32-entry / 24 MiB cost limit; visible views release their image on disappearance. Covers still being prepared receive bounded retries for HTTP 202. Initial catalog, overview and series requests run concurrently and publish together to avoid inserting rows above an already visible grid.

Only visible reader pages are decoded. In the Duo inner-display workflow, hiding native controls increased the selected manga page viewport from 216,813 to 315,322 pt² (45.4%); this measures content area, not frame rate. EPUB pagination is retained until chapter, size or typography
changes; moving between its prepared pages does not paginate again.

Reproduce with `mise exec -- swift run --package-path apple/Benchmarks -c release BookPageBenchmark`
under `scripts/with-lock.sh /tmp/lumina-perf.lock 1800`. Raw samples/build diagnostics are in
`output/native-book-reader/image-benchmark-final.json` and `image-benchmark-final.log` locally.

## Verification

- Final shared Core suite with the live fixture: 78 tests, no skips or failures (117.125 seconds).
- Focused Core contracts: 3 passed (native credential/request boundary, reader geometry/RTL/folds,
  and image pixel limits).
- Live-server native Books workflow: passed for EPUB/PDF/CBZ/CBR downloads and checkpoint rewinds (0.243 seconds in the final Core run).
- Focused backend contracts: 21 passed, including native auth/visibility and existing archive/native-format behavior.
- Final iOS Simulator app/UI-test compilation and tvOS generic build: passed.
- Initial Duo four-format workflow passed (155.672 seconds). Its rotation check was subsequently strengthened to require an actual viewport size change. XCTest reports orientation changes without resizing the active Duo inner display in this setup; that strengthened run correctly fails, so it is not claimed as rotation evidence. Duo rotation/folding is qualified through actual Device Hub controls; the automated resize workflow runs separately on the standard iPhone simulator. Final Duo LTR and RTL workflows both passed: 2 tests, no failures, 387.031 seconds (RTL 195.836 seconds; LTR 191.195 seconds). The standard iPhone resize workflow is running.
- Device Hub visual checks: PDF Page 2 stays right after unfolding, stays selected across both rotations
  and both fold directions, and returns to the outer display. Horizontal folding places it below the fold.
  The selected EPUB text anchor remains in the right pane after reflow. Outer-landscape contents with
  the numeric keyboard remains usable. Largest Dynamic Type, Increase Contrast and Reduce Transparency
  were exercised. Native screenshots are retained locally in `output/native-book-reader/layouts/`.
- RTL visual testing found that SwiftUI mirrored the positioning wrappers despite fixed geometry queries.
  The canvas now fixes only pane positioning to physical LTR and restores the page locale within each pane.
  A separate RTL workflow asserts that the selected image is in the physical right half. The fixed layout and native pan/Fit swipe checks passed before the Duo XCTest rotation limitation stopped that run. Final LTR/RTL rerun passed. The RTL reader workflow establishes a normal UI session, then relaunches it with Arabic RTL arguments; the initial simulator RTL keyboard sign-in attempt was rejected and is not claimed as validated.
- Fold regions intersecting the edge of a Split View viewport now produce one bounded page rather than
  a manufactured offscreen pane; navigation follows the resulting one/two-pane layout. Core coverage added.
- Actual system Split View checked on both sides before the immersive changes: Page 2 retained on the right; then retained on the left with the toolbar at the outer edge, and Next advanced to Page 3. Folding from the left arrangement retained Page 3 on the outer display; reopening placed Page 3 physically right and Page 2 left.
- External PiP was attempted with Apple’s official HLS sample in Safari. No PiP control or automatic floating player was exposed in this simulator; this check remains unverified.
- `make check`: passed. Backend 5,248 passed / 11 skipped; frontend 299 files / 2,624 tests passed; real-stack browser 52 passed / 5 skipped; mocked browser 657 passed / 2 skipped. Final immersive/RTL native workflows are running.
- SwiftUI previews were not requested or rendered; the actual native app is exercised in Device Hub. The optional Xcode App Resizability skill is not available among this session’s tools or installed skills. No physical-device timing or energy claim is made.

## Latest library/default refinement

The expanded native library boundary tests passed (1 backend workflow); the new Core DTO/request contracts passed (3 cases), and the live Books workflow passed with EPUB/PDF/CBZ/CBR cover decoding plus shelf membership (4.261 seconds). The visual library and device-default implementation compiled. Simulator startup stalled before its first UI case while other Xcode jobs were active; only this task’s stalled jobs were interrupted. A final repository gate and fresh native workflows are queued; these latest UI checks are not yet claimed as passed.
