# Performance audit remediation

Follow-up to the [comprehensive baseline audit](comprehensive-audit-2026-10-17.md), authorized without another review. Measurements use disposable synthetic fixtures on native macOS and the shared performance lock. External workloads are allowed and recorded; results do not establish quiet-host tail latency. Baseline measurements remain unchanged in the original report.

Final production source fingerprint: `bf13957a50dbe702922863970b3ff92095f3443eea4aef786efd96a68b086745`, based on the existing dirty integration checkout at `a4e01dad62fadc6186ca27ea0fbda8e3ea0d3674`. `output/performance-fixes/final-source.json` records this and the preceding freezes. Each earlier benchmark retains its own source identity. The final integrated playback repeat includes the title-alias and decoded-height stall-recovery corrections; the only subsequent production-fingerprint change is the phone tab-row CSS wrapping correction. Virtual substitution of the preceding CSS reproduces the measured `562f86…` fingerprint exactly. Earlier playback/readers runs retain their own revisions. The full gate started at the preceding `aca562…` snapshot; during its backend phase the frontend-only stall correction received focused tests plus a fresh typecheck/production build before frontend unit/browser verification. Backend source stayed fixed. No clean-commit or single-revision claim is made for the entire remediation sequence.

## Search and Next Up

Transcript search now keeps the bounded FTS candidate set in charge of the visibility join. The predicate preserves owner, shared, admin and restricted-member access semantics. Existing service and query-plan regression contracts cover those boundaries and deterministic ties: 87 focused tests passed.

The paired same-database SQL experiment alternates old/current order for 20 samples per variant and compares exact ordered rows on every execution. All 18 user/query combinations matched. Representative viewer medians were 67.9 → 21.5 ms for `river`, 78.9 → 31.7 ms for `river forest`, and 21.9 → 0.15 ms for a nonmatch. These are execute/fetch microbenchmarks excluding HTTP, hydration and ranking. Current instrumented direct-route medians were 60.3 / 77.8 / 4.0 ms respectively; profiling overhead remains included. Evidence: `output/performance-fixes/search/paired-native-final/` and `profile-native-rerun/`, both contended.

Next Up resolves the latest progress-row ID once per episode and joins that row for its timestamp, completion and position, replacing three equivalent correlated lookups. The public API regression exercises the latest checkpoint across multiple file versions; 38 focused title/Next Up tests passed. A same-fixture alternating 20-sample service comparison preserved ordered output and measured 65.1 → 60.1 ms wall median (63.3 → 57.2 ms process CPU median). This modest improvement is separate from the HTTP budget check. The paired wrapper records contention, including a brief focused-test overlap; final acceptance uses serialized runs. Evidence: `output/performance-fixes/titles/paired{,-run}.json` and retained driver.

The final Next Up follow-up reuses its season/episode SQL aliases across three query builders, avoiding repeated column-adapter construction without caching member predicates or results. A separate 20-alternating-sample exact-JSON comparison measured 80.6 → 54.7 ms wall median and 70.8 → 53.4 ms CPU median for a 20-pick page. Unlimited results also remained identical. Evidence: `output/performance-fixes/next-up-alias/`; this is a different measurement window, so its medians must not be composed with the preceding experiment into a single speedup. The final focused title, progress, query-plan and Jellyfin suite passed **142 tests**, including a public regression alternating members with different checkpoints.

A paired resolution-filter prototype measured 241.8 → 239.7 ms and was not adopted. Its production query plan already seeks title/parent/artifact indexes; no speculative index or cache was added.

Two additional Next Up prototypes were also rejected after exact-output paired checks: bounded series batches did not reduce CPU at the actual 20-pick page size (59.31 → 59.43 ms), and a SQL window for dismissed anchors barely changed it (61.98 → 61.15 ms). They remain temporary experiment sources under `output/performance-fixes/next-up-{batch,window}/`. The isolated route profile recorded generation-2 GC pauses of roughly 69–77 ms; it does not justify changing process-wide GC policy or hiding collections from the budget test. Earlier host/lifespan profiling attempts are retained separately and excluded from the isolated-route conclusion.

The Similar profile attributed 11 ms of an 81 ms direct handler call to full overlap ordering, 32 ms to visible title-base construction and 18 ms to ranking. Replacing the full overlap sort with an exact bounded heap preserved all 12 ordered results in 12 alternating same-fixture comparisons, but median CPU barely improved (51.39 → 50.23 ms) and p95 worsened (94.39 → 96.84 ms). The experiment was fully reverted; seven focused candidate/Similar/privacy tests passed. Evidence: `output/performance-fixes/similar/{profile-native,paired}/`. No recommendation result cache, global GC setting or ranking change was introduced.

Queue hydration and file-range delivery retain their baseline implementations. The audit's 1,979-distinct-track queue observation was 324 ms median across three GET+PUT samples without an established queue SLO; no queue speedup is claimed. Existing range chunking already beat the legacy comparison substantially. Neither observation justified adding a cache or changing delivery semantics during this remediation.

The serialized offline public-API title-budget repeat retained all original thresholds and provider/rate-limit controls: **4 passed / 4 failed** under contention, with load 21.3 on 16 cores. Series + 4K passed at 325.5/400 ms p95. Next Up remained 203.5/150, Similar 139.2/120 and recommendation diagnostics 105.9/80; the 10k Similar and title-row tests also failed their existing assertions. Next Up's 20 measured calls had a 77.1 ms wall / 75.0 ms CPU median and intermittent high-CPU tails. These misses remain recorded, not waived as passes or claimed resolved by a small service optimization. Evidence: `output/performance-fixes/title-budgets/`.

The final backend-source repeat (`output/performance-fixes/title-budgets-final/`) completed **5 passed / 3 failed** in 96.9 seconds. Load rose from 843 before execution to 942/968 at the test observations on 16 cores, and the harness explicitly warned that this is not a valid reference-budget verdict. The 10k title-row case passed at 205/250 ms p95, but series + 4K was 509.9/400, Next Up 211.0/150, Similar 204.8/120 and diagnostics 134.7/80; the 10k Similar case was 202/120. Next Up's median was 98.6 ms wall / 86.8 ms process CPU. These public-API tails do not verify a quiet-host SLO gain from alias reuse; the paired service comparison is the isolated evidence for that change. No budget was relaxed and no failed result was replaced by a favorable repeat.

The 60-second real-HTTP household repeats retained the original seed, endpoint mix and 0.02-second mean think time:

| Members | Baseline search median / p95 ms | Current search median / p95 ms | Current search samples | Current successful HTTP requests |
| --- | --- | --- | --- | --- |
| 1 | 146 / 210 | 70.9 / 120.7 | 53 | 1,668 |
| 6 | 415 / 1,136 | 173.8 / 346.6 | 237 | 6,204 |

All current HTTP requests were successful; JSON nearest-rank values above can differ slightly from the console's interpolated percentile. Evidence: `output/performance-fixes/household/c{1,6}.json`. The workload wrapper exited 1 because its subsequent browser workflow timed out at the transcript tab with no page errors; that browser result is not a successful household UI check. The completed HTTP samples remain independently usable. Frontend bandwidth source changes continued during this intermediate run, while the search SQL stayed fixed. Both baseline and repeat were contended; only the alternating SQL experiment isolates the query change from differences in host load.

## Bandwidth-aware playback

The web player snapshots a finite, constrained Network Information downlink estimate for its initial automatic decision and sends 80% of it as a transient bitrate hint. Options, speculative starts and actual sessions use the same estimate/cache identity. Saved quality and explicit quality choices override the advisory hint; household/server resource ceilings still apply. Switching from capped Auto to the reachable Original choice refreshes the decision without the hint, retains position and avoids an unnecessary conversion request.

Missing, invalid and saturated estimates preserve the previous server decision. Chromium deliberately [caps its reported estimate at 10 Mbps](https://issues.chromium.org/issues/40662901), so values at or above 10 are treated as unknown rather than an 8 Mbps LAN ceiling. This is an advisory initial choice, not measurement of the private media path or a continuously adapting quality ladder. A browser without this API receives the prior behavior. The shaped benchmark supplies a deterministic hint separately from its actual CDP transport limit.

Focused playback checks passed: 48 frontend tests, two focused backend API/session contracts and source/e2e typechecking. These cover constrained and saturated estimates, prefetched decision identity, saved/manual precedence, a capped-to-Original transition and retained menu selection. The operator guide documents the scope and fallback.

Final review also corrected the existing stall-recovery prompt to use the actual stream dimensions, scoped to its session. An automatically capped 480p stream now offers 360p rather than 720p from the original source; before dimensions arrive, a bitrate-capped stream offers no guessed higher rung. The public component regression covers both states and the resulting quality request. The final local-player/prefetch set passed 41 tests, followed by refreshed source/e2e typechecking and the production build. The whole-project frontend phases use those refreshed assets.

The final integrated-source nine-trial repeat passed all scenario correctness checks after the stall-recovery correction. Each case used three fresh contexts and a 20-second post-first-frame observation on the original 1080p H.264/AAC MKV fixture:

| Current scenario | First decoded frame, three trials ms | Median ms | Stalled time, three trials ms |
| --- | --- | ---: | --- |
| Unshaped, saturated 10 Mbps estimate ignored, 1080p remux | 495 / 327 / 212 | 327 | 0 / 0 / 0 |
| Shaped 2.5 Mbps / 50 ms, automatic 2 Mbps hint, 480p transcode | 1,763 / 1,742 / 1,763 | 1,763 | 0 / 0 / 0 |
| Same shaped link, saved 480p choice | 1,730 / 1,730 / 2,028 | 1,730 | 0 / 0 / 0 |

All nine windows advanced approximately 20 seconds. Automatic shaped startup improved from the original 16,589 ms median / 13,106 ms median stalled time to 1,763 ms / zero stalls. The unchanged baseline manual-480p comparator was 1,974 ms / zero stalls; this demonstrates automatic selection reaching the already-known useful rendition, not an encoder-speed improvement. The current unshaped reference stayed at 1080p with no transient budget. N=3, synthetic loopback shaping and injected browser hints do not qualify Wi-Fi, loss recovery, sustained adaptation or physical receivers. Evidence: `output/performance-fixes/playback/integrated-final/{run.json,playwright.json,raw/}`, matching the pre-wrap source `562f86…` and recorded as contended. The earlier accepted `attempt-2/` also passed, with automatic median 1,961 ms and zero stalls; the separate executions are not pooled. Attempt 1 was intentionally interrupted for the saturation/manual correction and is excluded.

## Watch UI

The Chromecast disclosure uses the existing 36/44 px control target token. Watch tabs wrap within a constrained row at 320/390 px, preserving visibility of every tab without page overflow. An initial horizontally scrolling row conflicted with the existing episode-phone contract; the full mocked suite caught it, and the correction retained that contract. Eleven focused browser checks then passed. Non-web-video WatchTools stays in one stable sibling position when late metadata changes the layout, preserving selection, the typed Notes draft and its timestamp. WebVideo retains its InfoColumn placement. The final component regression covers the draft and timestamp, rather than just masking the tab reset with parent selection state. Focused component and browser checks passed. The desktop-removal browser assertion still forbids standalone Open/Reveal and explicit file-manager actions while allowing ordinary in-app “Open queue” and “Open listening room” controls; its removed-API trap remains intact.

The frozen-build strict Home/Live/warm-All repeat passed all three cases plus setup, with external contention recorded in `output/performance-fixes/ui/run-home-live-warm-all.json`. Warm-All readiness was 303 / 279 / 296 ms, chapters 713 / 679 / 652 ms and four initial requests; Home scroll long-task counts were 3 / 0 / 0, and Live fling 0 / 0 / 0. The existing median contracts passed without threshold edits; the individual Home value of three remains visible. This repeat preceded the final stable-tools-only correction; final focused unit/browser checks and its subsequent production build passed. No Home/Live production optimization is inferred from workload variability.

The corrected current household UI-only workflow passed all routes, full library scroll and the transcript path (93 ms, 100 cues), in `output/performance-fixes/household/ui-only-final/`. Its fixture now requires a shared, available video/movie/episode rather than an arbitrary shared transcript that could belong to audio or unavailable media. The original failed run remains retained. The intermittent baseline overlay-CLS miss has no focused reproducible cause yet; no arbitrary layout change or relaxed CLS limit was applied.

## Long audio

Missing duration, sample rate or channel count now selects native streaming before Web Audio fetch/decode, both online and for offline saved tracks. The engine also refuses an unknown decoded-size estimate before fetching, covering queued/preloaded paths. Known short tracks retain bounded buffered playback. Existing engine/session/stream/offline lifecycle contracts passed: 55 tests.

Known-file estimates remain metadata-based; browser resampling, native decoder overhead and an absolute pre-allocation peak are not qualified by these checks.

The real-Chrome unprobed FLAC check returned null for all three probe fields from the actual track API, with isolated background probe warming disabled and no synchronous metadata setup. Primary Play reached native `playing` in 251 ms, made zero `decodeAudioData` calls and started zero AudioBufferSourceNodes; a genuine native `seeked` event landed at 60 seconds. Setup plus the long-form case passed. Evidence: `output/performance-fixes/music-unprobed-built/`. This N=1 event proxy establishes routing and seeking, not acoustic onset, a native-allocation peak or a before/after latency gain. The prior 224 ms baseline used populated metadata and is not a comparable cold/unprobed sample.

The first follow-up attempt was interrupted after detecting stale production assets and is excluded. The accepted case above ran after a completed production rebuild.

The later frozen-build repeat also passed (`output/performance-fixes/music-unprobed-final/`): 307 ms to native `playing`, zero decodes/buffer starts and native seek 60.003 seconds, still N=1 per execution. Both samples are retained separately rather than pooled into a tail percentile.

The final strengthened check waits for native `playing` **before** opening the listening room, so no second interaction can supply an autoplay gesture. Real track API facts remained null, with no synchronous probe setup:

| Engine / version | Primary Play → native `playing` ms | Native seek s | Web Audio decodes / buffer starts |
| --- | ---: | ---: | --- |
| Chrome / 155.0.8059.84 | 311 | 60.000 | 0 / 0 |
| WebKit / 26.0 | 1,503 | 60.014 | 0 / 0 |

Each row is N=1, not a tail percentile or physical output/decoder-allocation qualification. Evidence: `output/performance-fixes/music-{chrome,webkit}-primary/`. The WebKit variant rebuilt production assets before its isolated fixture server. Two preceding WebKit attempts stopped at an ambiguous “Open listening room” selector during the transient loading state; their artifacts remain retained and contribute no accepted timing. The refined workflow selects the track's room control after playback has begun. Its retained temporary WebKit configuration lives with the output artifacts; the normal reader config remains the Chrome default.

All EPUB/PDF/CBZ/CBR browser journeys passed in `output/performance-fixes/readers-final/`, including three contexts and bounded navigation soaks per format. That wrapper exited 1 because the separate short-music case still expected a Web Audio start before probe facts existed. The buffered-path repeat now explicitly qualifies all four short tracks through ordinary `playback-options` probes before its timings and records before/after API facts. This is setup for a known-short decode benchmark, not a claim that cold scanning synchronously completes metadata; the unprobed long case does not use it.

The corrected known-short music repeat passed (`output/performance-fixes/music-known-final/`, setup plus music case). The preflight API confirmed null duration/rate/channels on all four tracks before real probing. FLAC, MP3, AAC-in-M4A and Opus playback, scheduled gapless/crossfade transitions, induced WAV recovery, explicit native recovery and offline buffered playback all passed. Three-context buffered scheduling median was 90 ms, induced WAV fallback 151 ms, explicit native `playing` 63 ms and offline source scheduling 53 ms. These remain scheduling/event proxies; the setup and induced failures prevent treating them as equivalent cold-import or user-network latency measurements.

Reader startup medians for EPUB / PDF / CBZ / CBR were 448 / 822 / 451 / 997 ms, with warm page-turn medians 130 / 126 / 131 / 142.5 ms. No reader production change was needed; these are regression observations under contention rather than causal speedup claims.

## Infrastructure limits

The read-only Docker readiness retry again returned HTTP 503 before any container creation (`output/performance-fixes/docker-health.json`). Linux performance remains unqualified. No existing containers or unrelated jobs were stopped.

## Whole-project verification

The first `mise exec -- make check` execution passed bootstrap, source/e2e typechecking, the production build and stylesheet budgets, then completed the full backend suite with **5,058 passed / 11 skipped / 1 failed** in 730.3 seconds. The failure was the existing 5,000-directory automation writer-duration contract: observed maximum 589.0 ms against its unchanged 500 ms limit. No automation writer production code changed in this remediation. This contended timing failure is retained in `output/performance-fixes/check/{check.log,run.json}`; it is not a clean full-gate pass.

The frontend-only stall-recovery correction occurred during that backend phase and passed its own refreshed typecheck/build before frontend execution (`check/final-frontend-refresh.log`). The unchanged backend failure received an isolated repeat. The remaining Make gate was continued with `make check -o backend-test`, preserving the original backend result instead of silently replacing it or rerunning its other 5,058 successful cases. Both full-gate drivers hold the shared outer performance lock; the real-stack target uses a separate inner lock to avoid recursive-lock deadlock. Concurrency/threshold settings remain unchanged.

Focused frontend validation and its refresh build also ran during the first backend phase; their CPU appears outside that driver's process tree alongside unrelated activity. The writer timing failure is not attributed to any particular competing process. Subsequent timed/browser repeats are serialized under the shared lock.

Separate actual-decode WebKit checks passed **4 tests** in 1.4 minutes, including the real conversion path and a seek beyond the converted range (`output/performance-fixes/webkit/`).

The isolated automation writer repeat passed at the unchanged 500 ms contract (`check/writer-repeat.{json,log}`, one test in 2.39 seconds). Its successful observation does not erase the full-suite 589 ms miss or establish a quiet-host writer-tail guarantee.

The final-source Make continuation passed bootstrap checks, source/e2e typechecking, the production build/styles and **2,531 frontend tests across 293 files**. Raw continuation output is `output/performance-fixes/check/continuation.log`; the browser phases ran in that same driver.

Its complete real-stack browser stage passed **47 tests / 4 existing reference-only skips** in 7.9 minutes, with no failed tests. The separate strict Home/Live/warm-All repeat above and WebKit checks supplement that default gate configuration.

The first complete mocked stage finished **600 passed / 1 failed / 2 skipped** in seven minutes. Its failure exposed the phone tab-row regression described above (404 px scroll content inside a 334 px row). The row was corrected to wrap within its maximum width, preserving the original no-horizontal-tab-scroll assertion. The initial run, trace, screenshot and video are retained under `check/continuation.log` and `check/mock-initial-artifacts/`; a full mocked repeat followed the 11 passing focused geometry/playback checks.

The final complete mocked repeat passed **601 tests / 2 existing skips** in 9.5 minutes with no failures (`check/mock-final.log`, `check/mock-final-run.json`). This includes all four visual catalogue journeys and the original phone tab-row assertion. The final source differs from the integrated video measurement only in that CSS wrapping rule; `final-source.json` records the exact reconstruction check. The raw run inventory is `output/performance-fixes/artifact-index.json`; it includes unsuccessful and superseded runs, so acceptance and exclusions follow this report rather than mere presence in the inventory.

Final source/e2e TypeScript checks passed after the last audit-harness changes (`output/performance-fixes/final-static-latest.log`); Python helper compilation, JavaScript syntax and `git diff --check` also passed. These checks and the completed suite stages do not turn the first `make check` invocation into a clean pass. Remaining reference-budget misses and Docker/Linux qualification are the limits recorded above.
