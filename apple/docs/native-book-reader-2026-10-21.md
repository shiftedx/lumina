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
- tvOS generic simulator build passed (`library-tvos-final.log`). Native iOS compiled and earlier LTR/RTL four-format workflows passed; final selected-tab navigation fix and library/device-default workflows are being qualified before merge.
- Actual Device Hub checks: selected PDF page survived outer→inner, rotations, vertical/horizontal folds and refolding. EPUB's source anchor remained in the right pane after reflow. Outer-landscape contents keyboard, largest Dynamic Type, Increase Contrast and Reduce Transparency were exercised. Actual system Split View on both sides retained the page; Next and fold/unfold preserved continuity. These pose checks preceded the final library/default/toolbar refinements. Local captures: `output/native-book-reader/layouts/`.
- XCTest orientation changes do not resize the Duo inner display in this setup. Automated actual-resize assertions therefore run on standard iPhone; Duo fold/rotation evidence uses Device Hub. RTL testing signs in normally and relaunches the saved session with Arabic/RTL arguments; RTL keyboard sign-in is not claimed.
- External Safari PiP did not expose a control or floating window in this simulator and remains unverified. Physical-device timing/energy, previews and a tvOS reader are outside this delivered scope.
