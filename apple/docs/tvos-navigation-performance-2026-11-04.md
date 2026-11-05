# tvOS navigation performance — November 4, 2026

This pass covers the work Lumina does during remote navigation. It does not establish frame pacing or input latency on a physical Apple TV 4K.

- Opening a catalog, switching categories, sorting, and applying filters no longer wait on the 300 ms search debounce. Editing a nonempty search still does.
- The video wall updates artwork lookahead in six-item steps instead of restarting it for each focus change or visible card. It keeps the 36-poster window, 480-pixel rendition, and four shared speculative loads. Video and music only scan for a focus target when restoring a saved selection.
- Warm Home returns refresh the feed without requesting its layout and navigation configuration again. Initial load, foreground refresh, and explicit retry still fetch configuration. Home renders the feed directly without running the unused category and genre refinement.
- Requests retains its catalog for the current authorized session. Opening it again shows the retained content while refreshing the catalog and quotas. A session change or lost access clears the retained catalog.

The Release simulator comparison used baseline `e4359d02`, a tvOS 27 Apple TV 4K simulator, and the disposable proof server with 123 movies. The remote journey was `NativeTVHomeNavigationPreservesSelectionTests/testTVHomeNavigationPreservesSelection`. The proof server was started with `LUMINA_NATIVE_PERF_CATALOGUE_COUNT=120` and `LUMINA_NATIVE_PERF_REQUEST_LOG`; the build used `LUMINA_UI_TESTING` and the generated fixture. The route log shows that the first warm Home return requested rows in both builds, and requested layout only on baseline. Logs, XCResults and Instruments captures are retained locally under `output/tvos-focus/performance/`.

The updated build passed the main remote journey. The baseline recorded both entry markers before the test's focus helper failed to reach the filter sheet's Cancel button. The proof now uses remote Back to close that sheet. Single-run times from the start of XCTest navigation to the Movies wall were 5.90 s baseline and 6.01 s updated. Requests screen entry was 9.29 s and 9.01 s. These times include several remote presses and XCTest accessibility queries; they do not measure a single press or demonstrate an end-to-end speedup. XCTest automation was present in most baseline and updated main-thread samples, so the traces cannot establish a change in app-only hangs or frame pacing.

For hardware qualification, repeat the same cold and warm routes on the reported Apple TV 4K with its household library. Include continuous poster scrolling, Home → detail → Play → Back, category changes, and warm Requests reentry. Record Animation Hitches and compare remote input to the first visible response.

## Axiom catalog scale run

The Release simulator loaded exactly 1,000 movies and 2,000 series in separate Library walls. Each wall passed eight Down and eight Up remote presses and returned focus to the same poster.

| Wall | Automated route to complete catalog | Remote sweep with focus assertions | Slowest catalog page on the fixture server |
| --- | ---: | ---: | ---: |
| Movies | 12.24 s | 7.13 s | 84 ms |
| TV Shows | 9.06 s | 6.22 s | 79 ms |

The route timings include XCTest navigation and accessibility queries. The fixture reuses synthetic artwork and adds one episode per generated series. During the TV Shows downward sweep, Instruments flagged eight main-thread microhangs of 265–281 ms. CPU samples in that interval concentrated in UIKit focus-region occlusion evaluation. The separate 1.37 s hang aligned with XCTest accessibility snapshots during the category switch. These are simulator measurements. Animation Hitches was unavailable, and a physical Apple TV 4K was unavailable for remote latency and frame pacing measurements.

Use the scale recipe in the proof server README to reproduce the run. The Axiom trace, exported hang events, and redacted server timings are under `output/tvos-focus/axiom-scale/final/`. The XCTest log and result bundle are under `output/tvos-focus/library-watchlist/` with the prefix `axiom-scale-final`.

## Focus window experiment

A SwiftUI prototype exposed up to 54 poster buttons around the focused item and used noninteractive placeholders to preserve wall geometry. A fresh control run and two prototype runs used the same 1,000-movie and 2,000-series fixture on the Release simulator.

| Run | Movies sweep | Series Down presses | Series scroll stalls |
| --- | ---: | --- | --- |
| Control | 5.62 s | Focus moved and returned to the starting poster | 8, 263–278 ms |
| 54-card window, run 1 | 5.59 s | Focus movement assertion failed | 8, 263–273 ms |
| 54-card window, run 2 | 5.71 s | Focus movement assertion failed | 8, 259–284 ms |

CPU stacks in the first prototype run still concentrated in UIKit's focus-region occlusion evaluator during the series stalls. Bounding the number of poster buttons within the existing grid did not reduce the measured stall count or duration. The prototype was removed. Traces and the experimental patch are retained locally under `output/tvos-focus/axiom-window/`.

Removing the two nested focus sections that spanned the poster grid produced no hang events during either recorded series sweep. The matching control recorded eight stalls and took 6.20 s for the automated series sweep. The two runs without the grid-wide sections took 5.72 s and 5.74 s. Movies sweep time stayed near 5.6–5.7 s. The 123-movie alphabet workflow also passed its remote entry, jump, sort change, and return to name sort checks.

The final Release workflow passed with both full catalogs. It moved focus in both directions, restored the exact series poster after Back from details, and jumped from deep in the series wall to the first series through A–Z. The results establish simulator behavior with synthetic artwork. Physical Apple TV input latency remains unmeasured.

## Library entry and varied artwork

The Release simulator workflow starts its clock at remote Select with the destination control already focused. It records when XCTest sees the first poster, when the full catalog is observable, and when a separate remote sequence actually focuses the poster. A test-only app log records when a decoded artwork view mounts. Mount time is an app-side readiness signal. XCTest observation includes remote automation and accessibility work.

The varied fixture rotates 256 synthetic 480 × 720 PNGs through 1,000 movies and 2,000 series. On the paired baseline runs, the warm movie sweep took 5.60 s with shared artwork and 5.76 s with varied artwork. App footprint peaked at 88.1 MiB and 82.7 MiB, respectively. Neither trace recorded a stall during the scrolling sweep. Four stalls during the separate varied-fixture focus check contained XCTest accessibility snapshot and query stacks. These single-run figures do not establish a footprint difference between fixtures.

Before the category guard, switching to TV Shows mounted a Movies poster about 48 ms after Select on the varied fixture. The expected series artwork mounted at 149 ms. With the guard, the prior Movies poster did not mount and the expected series artwork mounted at 127 ms. The Movies entry likewise avoided mounting the seeded series poster. Warm Library routes used the existing catalog snapshot without a catalog request. The timings use a loopback server and synthetic artwork. Physical Apple TV latency and frame pacing remain unmeasured.

Local evidence is under `output/tvos-focus/axiom-entry/`.

The revised build passed both the entry benchmark and the full catalog remote workflow. The latter verified reverse scrolling, Back to the exact series poster, and an A–Z jump from deep in the series wall.
