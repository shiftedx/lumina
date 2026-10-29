# TV browsing artwork and device cache qualification — October 29, 2026

Source follows iOS build 34 and the episode-layout fix (#217). This record concerns source qualification, not a new TestFlight upload or physical Apple TV performance.

## Reproduction and changes

The shared TV tile route could feed an episode's landscape Primary still into a portrait Continue Watching/Next Up tile. A separate wide episode tile could instead inherit the series backdrop. The artwork contract now distinguishes portrait series/catalog posters, landscape episode stills, and hero backdrops without changing the playable item ID. Explicit catalog-artwork overrides retain priority. A public artwork-resolution contract failed on both incorrect source paths before the fix and now passes.

Repeated detail browsing also fetched full item metadata on every visit. TV now opts into private device snapshots for detail, parent-show, season/episode and related-title reads. Resolving a shelf episode's parent series populates the same detail cache, avoiding an immediate duplicate parent lookup. Movie controls stop waiting for optional related titles after essential detail is ready. Home seeds retained shelves from disk and refreshes current server state; foreground refresh includes watched state. ADR 0034 records the byte/count/lifetime limits and authorization boundaries.

TV shelf posters request 480-pixel renditions rather than 760. The focused shelf prefetches only eight upcoming renditions through the shared four-load artwork queue. Existing decoded, compressed-memory and 2 GiB private artwork-disk budgets remain in force. The complete lightweight catalog and its bounded in-process query snapshots remain separate from the small metadata disk cache.

## Focused service measurements

The repeat-detail reproduction uses a synthetic 50 ms item response and three sequential visits through `LuminaStore.loadItem`. Baseline: three item requests, 159.77 ms for the window. Final: one item request, 60.30 ms for the same window; the two warm reads together took 0.18 ms and made no network request. This measures the service boundary and injected network delay, not end-to-end remote input or household-server latency.

Eight focused contracts pass. They cover the correct artwork owner, catalog override, repeat detail/season reads, new-store disk reuse, expiry, forced fresh reads, Home restoration through a transient server failure, member/server/credential isolation, revocation, mutation behavior, artwork disk reuse/pruning, and cancellation/generation-safe cache clearing and four-load lookahead. No full suite was run under the owner's focused-testing instruction.

## Native navigation and profiling

Final native workflow/profile results are pending. Dedicated profile: second-generation Apple TV 4K at 1080p, tvOS 27 Simulator, optimized Release build, synthetic media, and authenticated disposable fixture. The focused journey uses real directional remote focus and Select, visits a series three times, checks each shelf's actual poster request path, traverses the shelf, and requires advancing decoded video afterward. Continue Watching and Next Up use independent progress states because an in-progress series anchor is intentionally excluded from Next Up.

Earlier attempts are retained as diagnostics: one omitted the profiler's required settled-login marker, another used a background/app-switcher transition that did not reliably foreground the app. They are not complete performance passes. Automation-inclusive detail-ready timings include XCTest focus/idle/snapshot waits and are not app response-time measurements. Physical-device frame pacing, HDMI/HDR/audio, household media and network conditions remain unverified.
