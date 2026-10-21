# Native Home and artwork performance — 2026-10-21

## Scope

This change overlaps the native Home reads, publishes successful shelves independently, cancels superseded Home work, and prepares billboard images off the main actor with ImageIO. It uses the existing native networking and imaging frameworks, with no new dependencies or persistent image cache. The catalog and Home retain their existing contents and member checks.

The baseline is merged revision `ac5f5ee`. Measurements use synthetic inputs on an Apple M4 Max (16 cores, 64 GiB RAM, macOS 27.0.1, Apple Swift 6.4), with finite timing runs serialized through `/tmp/lumina-perf.lock`. Other applications and simulators remain running; this is not a quiet-host or physical-device qualification. Unrelated branding work already present in the checkout is outside this change.

## Home measurements

The same asynchronous URLProtocol fixture delays each shelf response by 100 ms. Five runs of the original `LuminaStore` from `ac5f5ee` in an isolated package copy are compared with five revised-store runs. The original source receives the new measurement fixture only; its failure of the optimized concurrency assertions is expected. A separate serial client-call comparator in the retained regression also measures the original request ordering without copying production store code.

Both variants restore an authenticated session before timing `loadHome()`. This measures a warm Home API refresh, not cold application launch. The separate refresh regression checks that Home publishes while the catalog is still pending.

| Measurement | Original store | Concurrent store |
| --- | ---: | ---: |
| All shelves complete, median | 311 ms | 105 ms |
| First shelf response, median | 104 ms | 104 ms |
| Maximum overlapping shelf requests | 1 | 3 |
| Completion samples | 310, 311, 313, 311, 309 ms | 105, 105, 105, 103, 105 ms |

Total Home completion is **66.2% lower** in this controlled workload. The first shelf is not faster when all three responses have equal delays; the later shelves become available without waiting for earlier network round trips. The serial client-call comparator measured a 310 ms median, consistent with the original-store measurement. These clocks measure API completion, not rendered pixels, server computation or physical network RTT. A regression separately delays Recently Added while allowing Resume to finish first, checking that Resume is published while Recent remains pending.

The initial blocking test-double experiment is excluded: its synchronous `startLoading` callback serialized the networking fixture itself. Both accepted source variants use the corrected asynchronous fixture.

## Artwork measurements

The retained `scripts/benchmark_artwork_preparation.swift` compiles together with the production preparation helper. Five alternating before/after samples use the same 4,000 × 3,000 JPEG (190,257 encoded bytes). The old-policy comparison performs a full-resolution ImageIO decode and draw; the revised path prepares the bounded thumbnail and draws it. This models the former full-resolution decode policy rather than timing the old SwiftUI view or `UIImage(data:)` construction, which can defer decoding.

| Measurement | Full-resolution policy | Bounded preparation |
| --- | ---: | ---: |
| Decoded dimensions | 4,000 × 3,000 | 1,920 × 1,440 |
| Decoded pixel storage (`bytesPerRow × height`) | 48,000,000 bytes | 11,059,200 bytes |
| Median preparation and draw | 38.323 ms | 27.138 ms |

The fixture uses **77.0% less decoded pixel storage** and **29.2% less preparation/draw time**. These are not total process RSS or frame-hitch measurements. The uniform-color JPEG is deliberately simple; high-entropy images and other formats may have different decoder costs. The encoded request size and network transfer are unchanged by client-side downsampling. Source decoding can require transient allocations beyond the returned thumbnail.

`BillboardArtwork` keeps the existing 12 MiB encoded-input limit, downsamples to a longest edge of at most 1,920 pixels with EXIF orientation applied, and checks task cancellation before assigning the result. Already-started detached preparation may finish after cancellation, but its result is discarded. Only the current view retains the prepared image. `MediaBillboard` and `FeaturedShelf` now publish compact/wide geometry changes instead of exact widths. This removes per-pixel state updates by construction; no measured SwiftUI body-update or scrolling-hitch reduction is claimed.

Regression coverage exercises landscape, portrait, rotated and small images, plus malformed and oversized inputs. Small artwork is not upscaled.

## Reproduce artwork measurements

From the repository root:

```sh
mise exec -- swiftc -swift-version 6 -O \
  apple/Core/Sources/LuminaCore/BillboardArtworkPreparation.swift \
  apple/scripts/benchmark_artwork_preparation.swift \
  -o /tmp/lumina-artwork-benchmark
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 /tmp/lumina-artwork-benchmark
mise exec -- swift test --package-path apple/Core \
  --filter BillboardArtworkPreparationTests
```

The local raw artwork samples are retained in ignored `output/native-home-performance/artwork.txt`.

## Reproduce Home measurements

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  swift test --package-path apple/Core \
  --filter testHomeLoadsShelvesConcurrentlyWithTheSameFirstShelfTime
```

The test prints serial and concurrent completion, first-response and overlap values. The original-store comparison additionally substitutes `git show ac5f5ee:apple/Core/Sources/LuminaCore/LuminaStore.swift` into an isolated copy of `apple/Core` while retaining the same test fixture; optimized assertions intentionally fail on that source. Do not replace the working checkout's store to reproduce the baseline.

Raw Home runs are retained locally in ignored `output/native-home-performance/lumina-home-{head,current}-runs.log`.

## Regression contracts

The existing Core store suite now covers concurrent request admission, Resume publication while Recent is pending, a failed Recent shelf with successful Resume/Next Up, Home publication while the video catalog is slow, and catalog-error retention during refresh. Lifecycle cases cover superseded requests, caller cancellation, busy-state cleanup, sign-out with pending shelves and reconnecting as another member. Each publication checks both its Home generation and the current session. Refresh keeps loading the existing video query; suppressing invisible catalogs is outside this change.

The image helper tests check output dimensions and orientation through the same helper used by the view. No assertion claims a process-memory ceiling, physical output latency, device energy reduction or measured SwiftUI frame-rate improvement.

## Repository validation

One full `mise exec -- make check` invocation passed with the existing budgets unchanged: 5,239 backend tests (11 skipped), 2,624 frontend tests across 299 files, 52 real-backend browser tests (5 skipped), and 657 mocked-browser tests (2 skipped). The real-backend and mocked stages took 10.3 and 19.0 minutes respectively. This run includes the unrelated branding work already in the checkout; the native changes do not alter web/backend inputs.

Log: ignored `output/native-home-performance/make-check.log`. Native source/test hashes are retained in `output/native-home-performance/native-source-manifest.json`.

The final `apple/scripts/check.sh` run passed all 52 Core tests with no skips, including all three real-server cases, and built both iOS and tvOS Simulator applications. Its final-source Home regression printed a 327 ms serial comparator versus 106 ms concurrent completion, with overlap 1 versus 3; this single confirmation is separate from the paired five-run medians above. Log: `output/native-home-performance/native-check.log`. The fixture setup follows [the native test guide](../scripts/README.md).

The existing `NativeProofTests/testFeaturedTitlePreservesDetailsAndResumesPlayback` workflow passed on fresh iPhone, iPad and Apple TV simulators. It covers Home, carousel navigation, details and resumed playback, with focus-paused rotation on tvOS. All three hero screenshot attachments were inspected: compact portrait artwork, wide iPad artwork and landscape TV artwork rendered with their controls present. Logs and exported screenshots are retained under `output/native-home-performance/{ios,ipad,tv}-{home.log,attachments/}`; result bundles are `/tmp/lumina-home-performance-{ios,ipad,tv}.xcresult`. Workflow durations were 124.080, 109.515 and 168.348 seconds respectively; these are automation durations, not app latency comparisons.

All 27 recorded native source/test hashes matched after these checks, and `git diff --check` passed. Physical-device frame hitches, energy use, total process-memory peaks and the full Duo pose/output matrix remain unmeasured by this follow-up.

## PR integration on current main

PR #138 applies the eight Home/artwork files to `72fdeea`, preserving the intervening progressive-music, MKV, offline-library and progress-cadence changes. The branding files in the original checkout match already-merged PR #126 byte-for-byte and are outside this PR's diff. Independent standards and specification reviews found no actionable issues.

Fresh native validation on this integrated base passed all 73 Core tests with no skips, including four live-server cases, and both iOS/tvOS Simulator builds. The Home regression printed a 310 ms serial comparator and 105 ms concurrent completion, with overlap 1 versus 3. This confirms the final integration separately from the original measurements and UI journeys above. Local log: `output/native-home-performance/pr-native-check.log`.
