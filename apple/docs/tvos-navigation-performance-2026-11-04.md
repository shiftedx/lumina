# tvOS navigation performance — November 4, 2026

This pass covers the work Lumina does during remote navigation. It does not establish frame pacing or input latency on a physical Apple TV 4K.

- Opening a catalog, switching categories, sorting, and applying filters no longer wait on the 300 ms search debounce. Editing a nonempty search still does.
- The video wall updates artwork lookahead in six-item steps instead of restarting it for each focus change or visible card. It keeps the 36-poster window, 480-pixel rendition, and four shared speculative loads. Video and music only scan for a focus target when restoring a saved selection.
- Warm Home returns refresh the feed without requesting its layout and navigation configuration again. Initial load, foreground refresh, and explicit retry still fetch configuration. Home renders the feed directly without running the unused category and genre refinement.
- Requests retains its catalog for the current authorized session. Opening it again shows the retained content while refreshing the catalog and quotas. A session change or lost access clears the retained catalog.

The Release simulator comparison used baseline `e4359d02`, a tvOS 27 Apple TV 4K simulator, and the disposable proof server with 123 movies. The remote journey was `NativeTVHomeNavigationPreservesSelectionTests/testTVHomeNavigationPreservesSelection`. The proof server was started with `LUMINA_NATIVE_PERF_CATALOGUE_COUNT=120` and `LUMINA_NATIVE_PERF_REQUEST_LOG`; the build used `LUMINA_UI_TESTING` and the generated fixture. The route log shows that the first warm Home return requested rows in both builds, and requested layout only on baseline. Logs, XCResults and Instruments captures are retained locally under `output/tvos-focus/performance/`.

The updated build passed the main remote journey. The baseline recorded both entry markers before the test's focus helper failed to reach the filter sheet's Cancel button. The proof now uses remote Back to close that sheet. Single-run times from the start of XCTest navigation to the Movies wall were 5.90 s baseline and 6.01 s updated. Requests screen entry was 9.29 s and 9.01 s. These times include several remote presses and XCTest accessibility queries; they do not measure a single press or demonstrate an end-to-end speedup. XCTest automation was present in most baseline and updated main-thread samples, so the traces cannot establish a change in app-only hangs or frame pacing.

For hardware qualification, repeat the same cold and warm routes on the reported Apple TV 4K with its household library. Include continuous poster scrolling, Home → detail → Play → Back, category changes, and warm Requests reentry. Record Animation Hitches and compare remote input to the first visible response.
