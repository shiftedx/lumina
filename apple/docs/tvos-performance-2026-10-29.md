# tvOS visual and performance qualification — October 28–29, 2026

The focused development qualification is complete. These are optimized simulator measurements, not physical Apple TV energy, HDMI, or HDR results. Retained local evidence is under `output/tvos-visual-performance/`.

## Changes

Movie and series details reuse the shared artwork-first billboard. Movie artwork fills a responsive 620–900-point hero; the series hero retains episode selection, facts, synopsis, actions, and its season rail. Both show independent IMDb, Rotten Tomatoes, and Metacritic ratings. Community ratings remain explicitly labelled. The existing subtle copy shadow keeps text legible on bright artwork.

TV video walls opt into the complete lightweight catalog, retaining search/channel pagination and in-place alphabet jumps. Poster requests use the server's 480-pixel rendition, with a directional 36-poster lookahead and four shared loads. Catalog/artwork work suspends in the background. The decoder respects requested widths even when an original image is returned or reloaded from disk. Decoded/compressed memory cache targets remain 128/32 MiB; the private disk budget remains 2 GiB. Those cache targets do not describe the app's entire footprint. Mounted views, playback, and graphics resources also consume memory.

The initial hero implementation recomputed image layout every animation tick. A fixed-layout SwiftUI transform experiment reduced CPU use but increased wakeups. The current candidate animates the existing cached image's normalized `CALayer.contentsRect`, retaining the 18-second easing, subject-safe endpoints, Reduce Motion, visibility, and scene-phase guards. It tears down its animation and image reference on removal. Apple documents [bitmap-backed layer animation](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CoreAnimation_guide/CoreAnimationBasics/CoreAnimationBasics.html) and [`contentsRect` as an animatable property](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CoreAnimation_guide/AnimatableProperties/AnimatableProperties.html). The iPhone motion/layout workflow and completed TV destination capture validate this implementation; the larger catalog stress qualification also passes.

## Method and baseline

Baseline production source is `c64e2a49b27574f0cfdf0a84d0b0fc4adf229c68`. Each build uses Release optimization on a dedicated Apple TV 4K third-generation simulator with tvOS 27. A private fixture supplies 123 movie records, original approved Lumina artwork, and moving 1080p test video. CPU is percent of one host core. The profiler samples the exact app process, reports charged footprint and wakeups, and records Time Profiler data. Settled windows allow two seconds before eight seconds of sampling. The machine-wide performance lock serializes captures; other host services remain a source of noise.

The completed baseline destination tour (`baseline-other-views`) covers Home detail, movie cast/related titles, series/seasons, direct video and all settings tabs, client-demux MKV playback/settings/close, music/queue, Requests search/detail/history, and Account/device settings. It excludes the catalog sweep. It passed with one app PID, no timeout, and no recorded simulator crash. Settled movie/series details used 0.11–0.18% of one core. MKV settings ended at 136.9 MiB footprint; the post-close window ended at 99.0 MiB. Window host-load medians ranged from 8.1 to 15.5.

Two earlier baseline tours failed in the test's return from a long grid sweep: it first tried to focus an absent lazy-grid cell, then moved from the alphabet into Search. Their partial captures are not full performance passes. The corrected journey selects the public alphabet control directly.

## Measurements

| Matching settled Home window | CPU, one core | Wakeups/second | Status |
| --- | ---: | ---: | --- |
| Baseline layout animation | 11.35% | 98.4 | Completed baseline capture |
| Fixed-layout SwiftUI transforms | 4.58% | 140.4 | Completed intermediate capture; replaced |
| Compositor crop animation | 1.89% | 20.3 | Completed full capture |

The completed baseline Home trace contains 859 samples; 520 inclusive stacks (60.54%) contain `AnimatableFrameAttribute.updateValue`. The intermediate experiment has 21 of 361 such stacks (5.82%); the compositor has 2 of 160 (1.25%). Inclusive stacks overlap and must not be added as separate CPU percentages. The intermediate full destination tour passed with no recorded hang. Its three catalog returns ended at 90.737, 91.972, and 93.753 MiB. This is a short observation, not a long-term leak result.

The compositor destination tour (`compositor-tour`) passed with a valid single-PID capture, no timeout or recorded simulator crash, and zero recorded hangs. Its matching Home window used 83.35% less app CPU time and 79.35% fewer wakeups than the baseline window. This is an observed simulator comparison, not a device-energy claim. Six ordinary catalog returns ended at 82.331, 83.612, 84.690, 85.331, 85.378, and 86.112 MiB. The traversals revisit a limited set of posters; the larger workload below checks a broader image working set.

The same ordered 123-item catalog uses 43,771 bytes with `Fields=LuminaCatalog`, compared with 101,345 bytes for full metadata: 56.81% less JSON. This is response size, not a latency or energy claim.

## Focused validation

- Nineteen artwork/catalog Core checks pass, including a decoder regression that failed before the fix. Portrait, landscape, rotated originals, disk reload, and no repeated network fetch are covered.
- Seven existing hero-framing checks and 27 fixture-runner checks pass.
- The compositor builds for iOS and tvOS. The iPhone hero workflow keeps ambient motion enabled while freezing only automatic title rotation. It verifies changed artwork pixels over three seconds with the same selected title, stable wide layout during swipes, restored source scores, and portrait layout. Actual before/after photo frames were inspected.
- Three TV workflows pass with the compositor: full 123-title catalog/sort/alphabet behavior, movie source ratings and Remote Play, and episode-focused series ratings/selection/button playback/saved Remote Resume. Scores include valid Rotten Tomatoes 0% and Metacritic 39.9.
- Direct video, compatible HLS, and original client-demux MKV pass progress/resume workflows on the baseline and intermediate candidate. Direct/HLS/MKV captured frames show decoded moving-video content. The final compositor build also passes both existing direct/HLS and original-MKV progress/resume workflows (213.628 and 126.573 seconds), with no test failures.

A stale series-test assertion expected the old placeholder synopsis; it now waits for the fixture's actual episode synopsis. XCTest became stuck reporting that failed assertion, and that owned run was deliberately interrupted after preserving a process sample. It is not a product crash or a passed capture.

## Larger catalog and memory

The separate `catalog-stress` run loads all 503 records before scrolling, then performs six long remote traversals with 80 Down presses per sweep and returns through the public alphabet. The first sweep recorded 84 Down events including focus setup, confirming the configured workload ran. It passed with a valid single-PID capture, no timeout, no recorded simulator crash, and zero recorded hangs.

Settled footprint after each traversal was 96.55, 97.85, 98.21, 98.47, 98.89, and 99.22 MiB; the entire sampled run peaked at 105.53 MiB. Cache headers and modification dates show 287 distinct 480×270 poster renditions touched during this run. The production decoder reports 32 bits/pixel and 1920 bytes/row for a representative rendition: 518,400 bytes per decoded image, or 141.89 MiB cumulatively for those posters, beyond the 128 MiB decoded-cache target. This does not mean all those images were simultaneously resident. The fixture reuses approved synthetic artwork across distinct URLs, so these observations are not a universal bound for arbitrary household art or GPU allocations.

The performance tour accepts TV-only `LUMINA_PERF_TV_SCROLL_STEPS` (1–80) and optional `LUMINA_PERF_EXPECTED_CATALOG_COUNT` inputs. This run uses 80 and 503, respectively. It changes workload, not production behavior. Local `poster-working-set.json`, `disk-observation.json`, process samples, traces, and XCResults retain the evidence without recording raw signed URLs or credentials.

## Other destinations and limits

Playing-media samples varied rather than showing a general speedup. The video engine was not changed by this work. The table includes these observations to distinguish the demonstrated Home improvement from ordinary playback cost and host variation. Baseline excludes catalog sweeps; the final tour includes six, so later footprints have different prior cache histories.

| Settled phase | Baseline CPU, one core | Final CPU, one core |
| --- | ---: | ---: |
| Movie detail | 0.15% | 0.15% |
| Series detail | 0.13% | 0.19% |
| Direct video playing | 12.72% | 13.26% |
| Direct video paused | 0.57% | 0.55% |
| MKV playing | 5.96% | 7.13% |
| MKV after close | 0.49% | 0.53% |
| Music playing | 2.67% | 3.03% |
| Requests | 0.52% | 0.49% |
| Account/device settings | 0.47% | 0.52% |

The Time Profiler captures here do not contain animation-hitch or SwiftUI update lanes. A lack of recorded hangs is not proof of hitch-free rendering. Physical Apple TV, household-server playback, HDMI/audio/HDR output, WAN behavior, and device energy remain unqualified. The older failed TV build's hardware/version and observed failure are awaiting owner clarification.

Release archives and upload records remain separate from simulator evidence. iOS build 32 is frozen at the baseline source above and has not been uploaded; it does not contain these later shared performance changes. The named-score fix also requires the matching server adapter update.
