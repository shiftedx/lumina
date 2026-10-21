# Native music queue performance — 2026-10-21

Baseline: `8a04f0a` (current main after the Home/artwork follow-up). This slice changes native queue checkpoint persistence and presentation only; it does not change server session revisions, playback routing, normalization or crossfade.

## Implementation

Regular checkpoints update `cachedSession` immediately, then hand a snapshot and its captured member directory to a private serial utility writer. Credential stripping, JSON encoding, backup/file-protection setup and atomic file replacement run there. The writer retains one active snapshot and only the newest pending replacement. The existing 8 MiB encoded-file limit and URL stripping apply to both queue tracks and nested logical-entry tracks.

Every actual configured-account transition drains accepted writes before clearing/reloading state, including sign-out. This rare transition can wait for disk work on the main actor; the recurring playback path does not. iOS/tvOS backgrounding persists the current snapshot and awaits the writer under a background execution task. This does not promise survival of arbitrary process termination before an asynchronous write completes. Server revisions and explicit conflict resolution remain authoritative.

Queue rows are retained until membership, order or item metadata changes. Each row keeps its existing occurrence UUID plus its position, eliminating repeated grouping/mapping on reads and linear occurrence lookup during row presentation. Moves/removals preserve surviving occurrence IDs and rebuild row positions; cursor/repeat-only changes retain the rows. The cache adds one row array proportional to queue size; it is scoped to the existing playback model.

The extended remote workflow also exposed tvOS List rows capturing Select for their primary track button, preventing independent operation of the trailing menu. The TV queue now uses the existing music-screen pattern of a `ScrollView` and `LazyVStack` with native bordered buttons and a focus section. iOS retains its native List. Both platforms share the same UUID-identified row content. The component figures below do not measure rendering or the TV layout change.

## Measurement method

The persistence driver compiles the original and revised production Core packages in Release mode, then alternates fresh before/after processes for 100, 1,000 and 10,000 synthetic tracks. Each process measures a first checkpoint and a subsequent overwrite after the first completes. Main-actor admission ends when `persistCheckpoint` returns; completion ends when the original synchronous call or revised writer flush finishes. Setup, snapshot generation, saved-state decoding and validation are outside timing. Completion means the atomic-write API finished, not an explicit filesystem durability barrier. The driver retains raw samples and checks saved state and credential stripping.

The presentation driver compiles the production row builder with `-O -whole-module-optimization`. Its baseline is the former `queueRows` body with only the local synthetic item type substituted. Twenty-one samples alternate before/after execution order, with 100 reads per sample and consumed matching content. It reports row construction/read cost and the revised single-rebuild cost separately. It excludes the old additional per-visible-row occurrence lookup, SwiftUI rendering, body invalidations and device frame hitches.

The shared performance lock serializes these workloads with cooperating Lumina jobs. The host can still have unrelated activity; these are component measurements, not physical-device energy, cold-app launch, total-process memory or tail-latency guarantees. Checkpoint cadence is unchanged, so isolated steady writes are expected to retain the same encoded byte volume; only overlapping pending snapshots coalesce.

## Results and validation

Host: Apple M4 Max, macOS 27.0.1. Twenty-one before/after samples per queue size; p95 is the nearest-rank observation in this small host cohort, not a production tail guarantee.

| Tracks | Recurring checkpoint main-actor median, ms | Main-actor p95, ms | Completion median, ms | Encoded bytes, unchanged |
| --- | ---: | ---: | ---: | ---: |
| 100 | 0.707 → 0.014 | 0.810 → 0.019 | 0.708 → 0.718 | 22,580 |
| 1,000 | 3.052 → 0.024 | 3.267 → 0.032 | 3.052 → 3.094 | 225,081 |
| 10,000 | 25.818 → 0.034 | 27.228 → 0.041 | 25.819 → 26.047 | 2,259,082 |

At 10,000 entries this removes about 25.8 ms of synchronous main-actor work per recurring checkpoint (99.87% lower admission median), while performing substantially the same total write work. Completion p95 was 27.229 → 35.139 ms for that cohort; no disk throughput or total completion-time improvement is claimed. Five-second checkpoint cadence is unchanged. A 10,000-track synthetic file written twelve times per minute would still produce about 27.1 MB/min of JSON payload before filesystem overhead; this arithmetic is not an observed production write rate or drive-wear measurement.

Cold first-checkpoint completion medians were 1.317 → 1.316 ms (100 tracks), 3.728 → 3.822 ms (1,000), and 27.435 → 27.625 ms (10,000). Those include directory preparation rather than only replacing an existing file.

| Rows | Former rebuild/read median, ms | Cached iteration/read median, ms | Revised single rebuild median, ms |
| --- | ---: | ---: | ---: |
| 100 | 0.0070 | 0.0001 | 0.0103 |
| 1,000 | 0.0711 | 0.0013 | 0.0987 |
| 10,000 | 0.7594 | 0.0130 | 0.8922 |

The 10,000-row repeated-read model workload is about 58× faster. A membership/order/metadata change still pays the single rebuild cost, which is slightly higher than the former materialization in this synthetic benchmark. This is a reuse tradeoff, not removal of all queue-size-proportional work. No measured reduction in SwiftUI body updates or physical-device hitches is claimed.

Raw local evidence: `output/native-queue-performance/queue-checkpoint-benchmark.json` (individual cold/warm samples and summaries), `queue-checkpoint-benchmark.log` (build diagnostics), and `queue-presentation-current.log`. The original Apple tree was archived from `8a04f0a` under `output/native-queue-performance/baseline/` before editing. Account-transition draining is outside the measured persist/flush path.

The focused music-library service suite passes 6 cases, including the retained credential/member contract and the extended burst/latest-write, deliberate-rewind, duplicate queue and sign-out restoration regression. Independent agent standards and specification reviews pass after fixing the sign-out drain. Native verification passes 74 Core tests with zero failures or skips, including four live-server cases, plus iOS/tvOS Simulator builds. Both extended iPhone workflows pass on the final shared row implementation (177.192 s playback/progress, 176.211 s advanced queue controls). TV playback/progress passes (224.334 s); the advanced workflow passes after the final focus-query repair (299.013 s). These workflows exercise progress-stable queue contents, background/foreground and terminated-app paused restoration, deliberate server conflict selection, duplicate occurrence reorder/removal, and Stop followed by new playback.

The expanded checks caught the TV List focus issue described above and a Home request test that could mistake a concurrent Resume request for Recently Added; the latter now selects the intended endpoint as well as the shared media-type filter. TV automation was adjusted for the native menu hierarchy and to bind focused element identity before reading its frame during menu animation. Earlier failed run logs remain alongside the passing logs rather than being counted as passes. Final platform evidence is in `ios-ui-shared.log`, `tv-ui-focus.log` (playback case), `tv-ui-complete.log` (advanced case), and the corresponding exported screenshot directories. The disposable fixture server and the two simulators created for this work were removed after verification.

The final `mise exec -- make check` gate passed (exit 0), serialized under the shared performance lock: bootstrap checks, TypeScript checking, production build and asset budgets, 5,247 backend tests passed / 11 skipped, 2,624 frontend unit tests across 299 files passed, 52 real-server browser tests passed / 5 skipped, and 657 mocked browser tests passed / 2 skipped. Evidence: `output/native-queue-performance/make-check.log`. An earlier gate was deliberately interrupted before completion while the native workflow fixes were still being verified; its separate `make-check-interrupted.log` is not counted as a passing run. All measured/runtime source hashes remained unchanged through the final gate.

## Reproduce

Create an original Apple source tree from `git archive 8a04f0a apple`, then run from the repository root:

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 python3 apple/scripts/benchmark_music_queue_checkpoint.py --before-core BEFORE_TREE/apple/Core --after-core apple/Core --samples 21
mise exec -- swiftc -swift-version 6 -parse-as-library -O -whole-module-optimization apple/App/Shared/QueuePresentation.swift apple/scripts/benchmark_queue_presentation.swift -o /tmp/lumina-queue-presentation-benchmark
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 /tmp/lumina-queue-presentation-benchmark
```

The persistence driver creates fresh synthetic owner directories and deletes only those directories; no household server or real media is used. See the [native fixture guide](../scripts/README.md) for the real-server integration and platform UI workflows.
