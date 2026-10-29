# Native iOS Books reader — 2026-10-21

Lumina now has a native iOS Books library and EPUB/PDF/CBZ/CBR reader. The library uses actual covers, Continue Reading/Recently Added, shelf stacks and manga-series stacks, with searchable paginated catalogs. Shelf membership, ordering, visibility and per-file reading state come from the existing Books service through the opt-in Connected-app credential boundary. Archives and HTML are not parsed on-device; EPUB uses safe structured nodes and native attributed text, selection and links.

## Reading behavior and handoff

The selected page remains physically right when the Duo unfolds, including RTL manga. Its companion is the previous page in LTR and the next in RTL. A horizontal fold places the selected content below the fold. Position is independent of the viewport: prose retains a UTF-16 content anchor through pagination changes, and artwork retains its selected page through folding, rotation and reopening. Contents, appearance, file selection and Single Page use native sheets/system controls.

Artwork defaults to Fit entire page on standard iPhones and Fill display on Duo, with an explicit session override, native pan and pinch/double-tap zoom. Guarded `UIHingeInteraction` supplies device capability; actual safe-area geometry and reserved fold/camera regions determine panes. Physical pane positioning uses LTR coordinates while each page retains its locale. EPUB and Fit honor safe areas; immersive Fill artwork extends through the viewport. EPUB's reveal-control action stays in a managed bar so it does not obscure text.

This is the functional stopping point for a subsequent Duo/tvOS UI redesign. No tvOS reader was added. Keep the credential boundary, progress ordering, physical-right invariant, fold exclusions and device defaults when reworking presentation. Browse All lives beside the catalog section; pushed shelf/series screens retain the system back action, including the SDK’s unlabeled `BackButton` accessibility identifier.

## Performance measurement

There was no previous native reader, so no native startup/page-turn improvement is claimed. A Release component benchmark compares full ImageIO preparation against production bounded preparation using an original 6,000 × 4,000 JPEG, 21 alternating pairs. Both paths draw into an owned RGBA CGContext and consume an alpha byte. Bitmap storage is `bytesPerRow × height`; it is not process RSS. Cooperating jobs use the shared performance lock; other host activity remains possible.

| Component | Full-resolution control | Bounded preparation |
| --- | ---: | ---: |
| Decoded bitmap bytes | 96,000,000 | 25,165,824 |
| Preparation/materialization median | 65.341 ms | 116.671 ms |

Bounded bitmap storage is **73.8% smaller**, with **slower preparation** in this sample. No latency, energy, FPS or RSS gain is claimed. Preparation runs off the main actor; only visible reader pages are decoded. The encoded window retains current/spread data and at most one lookahead; abandoned speculation is cancelled. Failed lookahead is discarded so Retry can fetch anew. EPUB pagination is reused until content, size or typography changes. Progress writes are ordered and coalesced, with explicit save-failure recovery.

The library shares a session-bound thumbnail worker across its grid, shelves and series. It coalesces requests, prepares 600-pixel thumbnails off the main actor, and uses a 32-entry/24 MiB NSCache cost limit. Visible views release their images on disappearance. HTTP 202 cover preparation receives bounded retries. Initial catalog, overview and series requests run concurrently and publish together.

Reproduce with:

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 swift run --package-path apple/Benchmarks -c release BookPageBenchmark
```

Raw samples and diagnostics remain locally in `output/native-book-reader/image-benchmark-final.json` and `image-benchmark-final.log`.

## Verification

- Repository gate `mise exec -- make check`: passed. Backend 5,248 passed / 11 skipped; frontend 299 files / 2,624 tests passed; real-stack browser 52 passed / 5 skipped; mocked browser 657 passed / 2 skipped. Final gate log: `output/native-book-reader/library-make-check.log`.
- Shared native Core suite with disposable live server: 78 tests, zero skips/failures, 117.125 seconds. Includes four-format downloads, cover decoding, shelf membership and checkpoint rewinds. Log: `core-qualified-final.log`.
- Native boundary regression: backend workflow covers member auth, disabled integration, private/shared visibility, ordered shelf membership, series, resource bounds and traversal rejection. Core contracts cover DTO/auth paths, image limits and fold/RTL geometry. Failed-prefetch recovery adds one further Core contract.
- Final standard-iPhone workflows passed: library shelves/series (65.194s) and all four formats with Fit default, zoom/reset, actual rotation and saved-position restore (215.726s). Log: `phone-browse-navigation.log`. Hiding controls increased its measured viewport from 270,144 to 312,756 pt² (+15.8%); this measures content area, not frame rate.
- Final Duo RTL workflow passed (216.541s): all four formats, selected page physically right, Fill default, pan, Fit override, zoom/reset and saved-position restore. Hiding controls increased its measured page viewport from 216,813 to 315,322 pt² (+45.4%). Log: `duo-column-gestures.log`. Final Duo shelf/reader/back/series membership workflow also passed (67.883s). Both Duo cases passed with zero skips/failures. Earlier expanded LTR four-format workflow passed before the library refinement (191.195s).
- Final tvOS generic simulator build passed with the shared-Core page-window change (`tvos-qualified-final.log`).
- Actual Device Hub checks: selected PDF page survived outer→inner, rotations, vertical/horizontal folds and refolding. EPUB's source anchor remained in the right pane after reflow. Outer-landscape contents keyboard, largest Dynamic Type, Increase Contrast and Reduce Transparency were exercised. Actual system Split View on both sides retained the page; Next and fold/unfold preserved continuity. Most pose checks preceded the final library/default/navigation refinements. A final-build Device Hub check additionally confirmed selected RTL manga Page 3 on the outer display → Page 3 physically right after unfolding, with Page 4 as the left companion; screenshots `final-outer-page3.png` and `final-unfolded-page3-right.png` are retained locally. Local captures: `output/native-book-reader/layouts/`.
- XCTest orientation changes do not resize the Duo inner display in this setup. Automated actual-resize assertions therefore run on standard iPhone; Duo fold/rotation evidence uses Device Hub. RTL testing signs in normally and relaunches the saved session with Arabic/RTL arguments; RTL keyboard sign-in is not claimed.
- External Safari PiP did not expose a control or floating window in this simulator and remains unverified. Physical-device timing/energy, previews and a tvOS reader are outside this delivered scope.

## Review

Standards review found one failed-prefetch retry defect; it was fixed and covered at the Core service boundary. Requirements review found no unresolved source gaps. Final native workflow gates passed after correcting SDK back-button/offscreen-control selectors and anchoring test gestures in the catalog column. No broader shell redesign was retained.

## Immersive artwork update — 2026-10-24

PDF, CBZ and CBR now use an interactive UIKit paper curl, with a center spine for
side-by-side spreads and direction-aware single-page turning. The user can lift a
page, complete the fold or let it settle back; only completed turns select a new
reading position. Artwork fills the reader's available canvas with system chrome
hidden, including Fit mode. Existing Fill/Fit, zoom, contents, appearance, progress
recovery and format-specific checkpoints remain available. Magnified artwork pans
without turning. EPUB text, Reduce Motion and horizontal fold panes retain the
direct-turn renderer.

The reader stages only neighboring curl leaves at a maximum decoded edge of 2,048
pixels; it cancels discarded work. ADR 0022 records the revised staging bounds.
The native workflow checks RTL manga and LTR PDF dragging, cancellation, zoom/pan,
and checkpoint restoration; the existing four-format workflow retains EPUB/PDF/
CBZ/CBR reading and reopen coverage.

Two-page layout uses stable consecutive pairs by default on expanded displays:
1–2, 3–4, 5–6. Manga reverses their physical placement, without mirroring artwork.
The reading position label displays both visible page numbers, and page turns move
between complete pairs. Opening either member of a saved pair restores that spread.


## Unobstructed artwork controls — 2026-10-29

Immersive PDF/CBZ/CBR pages no longer carry the floating ellipsis control.
Tap once in the center of either page to reveal the reading menus; tap again
to hide them. Double-tap and pinch retain artwork zoom, and edge swipes retain
page turning. The page's VoiceOver custom action can still show or hide controls.
EPUB retains its reveal action in the managed bottom bar, clear of the text.
This source change follows uploaded iOS build 33 and is not in that package.

The focused iPad Pro/iOS 27 comic and PDF workflow passes (201-second test
session), including center-tap reveal/hide without a page change, zoom/pan,
cancelled/completed RTL/LTR curls and saved-position restoration. Actual
immersive/menu screenshots were inspected. Local evidence:
`output/reader-center-tap/reader-cover-entry.xcresult`. Test entry now uses Home
readiness and visible public cover/category frames; earlier attempts stopped in
navigation setup before the reader. No full suite or physical-Duo check was run.
