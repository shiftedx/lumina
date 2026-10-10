# Search and direct-play follow-up, 2026-10-10

This follow-up starts at shipped revision `df3c108f3e0a40699d878c3f284acb03c48e748d`. The frozen application candidate is `e338e01d9b6189f7476ded35d3da345d736c1064`. Measurements use complete Linux arm64 production images on the local M4 Max; they do not measure the Intel/QSV deployment host. Repeated default Movie search beats Jellyfin at all three tested concurrencies. Direct playback beats it under the specified metadata load, while idle and varied-media playback still have no clear lead. The machine-readable authority is [the sanitized measurement summary](measurements/search-direct-2026-10-10.json).

## Changes and costs

Search now builds each explicit member-snapshot visibility expression once, projects visible title candidates in one SQL query, and reuses pure lexical field preparation. SQLite connection-local caches reuse immutable ranked refs and default Movie search cards when data and caller scope are unchanged. Limits are 128 visibility expressions, 1,024 lexical preparations, 32 ranked-ref entries and four encoded pages of at most 128 KiB each per connection. Page decoding and ordinary FastAPI serialization still run for each response. The two item-read admission slots remain unchanged.

Every request still authenticates and loads current member limits. Cache validity includes the literal caller scope, SQLite `data_version`, and the connection's `total_changes`; active transactions and pending ORM mutations bypass caching. Writes, including rolled-back writes, conservatively invalidate reuse. Model-backed/custom-encoder search, requested fields, and sorted page projections retain live rendering. This is bounded warm-query reuse, not an elimination of cold-query work. The ADR 0019 amendment documents the boundary.

Explicit selected-version playback validates the visible leaf title and selected file in one live SQL query without assembling an unused title page. Primary/default selection retains the established preference path. Connected-app token authentication loads the token and member in one live outer join, retaining inactive, orphaned, idle and touch behavior.

Single-range playback starts with a 64 KiB read and then uses the existing 1 MiB bulk chunks. It watches receive-side disconnects because the production Uvicorn can silently ignore sends after a player disconnects. Cancellation closes the file under a shield; concurrent response instances keep separate receivers. HEAD, invalid ranges, multipart and conditional-range parsing remain in the framework.

## Protocol

The original control uses the stopped, initialized 302-movie seed database and identical read-only media. That corpus has 302 paths but only two distinct byte streams. Four final blocks run candidate/control/control/candidate, reversing the first API/browser target between blocks. Each API scenario has ten warmups and 300 timed requests at concurrency 1, 8 and 32. Direct uses 60 fresh Chrome contexts per server per block, alternating trial order, and requires a real decoded frame. HLS uses 30 per server per block.

Direct retains prepared-source assignment-to-frame timing, which excludes authentication and MediaSources preparation. A separate sign-in-to-frame workload includes both. HLS includes PlaybackInfo and has a different timing scope. p99 is descriptive at these sample sizes; initial Chrome/GPU outliers and errors remain in raw samples.

A separate varied corpus contains 98 movies and 26 distinct byte streams: H.264/AAC at 360p–1080p, 24/25/30 fps, multiple GOP lengths, metadata sizes and faststart/end-moov MP4 layouts. Linux page-cache advice is a separate workload and does not evict the macOS host cache. Neither workload replaces the original control.

## Final measurements

The four original blocks ran candidate/control/control/candidate. Figures below are the two repeats, in order, in milliseconds. Each cell is p50 / p95 / requests per second.

| Search concurrency | Shipped control repeats | Candidate repeats | Jellyfin paired with candidate |
| --- | --- | --- | --- |
| 1 | 17.95 / 21.25 / 52.03; 17.93 / 20.63 / 52.57 | 4.07 / 5.21 / 241.53; 4.33 / 5.56 / 221.43 | 20.42 / 23.21 / 47.87; 22.89 / 25.98 / 42.80 |
| 8 | 126.09 / 186.56 / 59.81; 127.92 / 175.75 / 58.27 | 15.26 / 17.11 / 514.39; 16.20 / 19.02 / 479.90 | 42.05 / 60.13 / 177.87; 46.53 / 63.50 / 163.99 |
| 32 | 529.36 / 566.88 / 59.77; 568.97 / 607.15 / 56.11 | 66.25 / 105.92 / 444.20; 67.85 / 119.96 / 425.52 | 240.10 / 345.68 / 127.44; 296.11 / 384.46 / 111.20† |

All 14,400 original Lumina API requests returned 200. Every successful timed page/search body matches its warm decoded hash; canonical JSON, keys, IDs, order and counts match across all four Lumina blocks. The search remains a practical 60-card workload: Lumina reports 200 candidates and Jellyfin 180, with different relevance semantics. At concurrency 32, search batch CPU fell from 5.32–6.40 seconds to 0.74–0.82 seconds, approximately 85–88%. Peak process RSS in those batches was 181–186 MB candidate versus 190–196 MB control. Docker MemUsage ranged 172–254 MB candidate and 173–244 MB control; raw cgroup memory includes file cache and varied more. These are distinct metrics, retained separately in the summary. Catalog medians, throughput and HLS results show no consistent material regression across blocks.

† The second paired Jellyfin search batch recorded one ReadError, and its wall rate is not used as a comparative throughput claim. Two Jellyfin page batches also recorded one ReadTimeout each. All failures remain in raw results; their cause is unproven. Raw API percentile fields include attempted requests. Failed-batch rates are never substituted with success-only throughput.

### Direct playback under metadata traffic

Four additional blocks ran control/candidate/candidate/control, 60 fresh-context decoded-frame trials per server per block. Both servers started under two CPU and 512 MiB limits, so runtime thread/GC sizing saw the limits. Authentication, MediaSources selection and a one-byte authenticated range had to succeed before timing. Each server received equal offered traffic of 64 requests/s, alternating the original 60-card catalog with MediaSources and default `Benchmark` search. Eight requests maximum were in flight per server; skipped offers remain visible rather than building an unlimited client queue.

| First decoded frame under load | Lumina p50 / p95 | Paired Jellyfin p50 / p95 |
| --- | ---: | ---: |
| Control, block 1 | 41.0 / 79.3 | 52.4 / 171.5 |
| Candidate, block 2 | 26.2 / 55.8 | 54.9 / 192.0 |
| Candidate, block 3 | 28.4 / 43.8 | 70.4 / 172.9 |
| Control, block 4 | 40.0 / 58.2 | 41.6 / 133.7 |

Every frame and every started background request succeeded. Candidate groups skipped only 12–15 offers versus 311–322 in control groups; their Jellyfin peers skipped 463–466. Skips measure the fixed client's inability to start additional work at the cap, not failed server requests. Pooled Lumina p95 fell from 77.3 to 46.2 ms (40.2%); candidate repeats were 70.9% and 74.7% below their paired Jellyfin p95. Pooling is descriptive: block-to-block drift remains visible in the table. This is the reproducible direct-play win established by this change.

Idle original playback remains close: pooled candidate p50 / p95 is 26.8 / 35.3 ms versus paired Jellyfin 26.1 / 34.5 ms. Control Lumina was 28.7 / 36.3 ms. The older 76.9 ms idle regression did not reproduce consistently, and this work does not establish an idle-tail win. All 480 original direct trials decoded a real 1280×720 frame; initial Chrome/GPU outliers remain, including the 796.3 ms Jellyfin maximum. p99 and maxima are included in the machine-readable summary without treating 120 samples per image as a stable population tail.

HLS retained its advantage: pooled candidate p50 / p95 was 196.3 / 253.0 ms versus control 197.9 / 253.7 and its paired Jellyfin 324.0 / 362.0. All 240 original HLS trials decoded. Timing scopes and output-width differences remain those of the original protocol; nonfatal buffer-stall events remain in raw samples.

### Bytes, abandonment and additional workloads

First/middle/suffix and multipart ranges, HEAD and 416 passed exact contract checks. Eight sequential and eight concurrent full streams per server reproduced the 62,516,911-byte source SHA-256 `b99a007e848f7ae6dc0665b51fe8f0091ceecefb2fd656bd99f2c89529896e51`. Media remained uncompressed. Throughput and range headers are retained in the summary.

After 20 client-aborted whole-file single ranges, application `rchar` rose 1,250,368,522 bytes on control versus 44,407,310 on candidate: 96.4% less reading. This counter includes small HTTP/SQL reads; it is not a direct disk-I/O counter. Both released every media descriptor. The read-only observer used temporary privileged Docker exec to inspect the non-dumpable process; the measured application UID remained 1000. The instant-disconnect unit regression separately bounds in-flight reading to 64 KiB plus one bulk chunk.

The varied corpus decoded all 480 trials at four resolutions. Candidate p95 was 41.3 / 41.4 ms versus control 47.1 / 46.8; paired Jellyfin was 32.6 / 39.0. The improvement is modest and still trails Jellyfin. The 32-term query-churn candidate improved c8 p95 from 144.1 to 85.2 ms, but Jellyfin measured 45.5 ms. Its typical responses contain fewer cards because phrase/name and Lumina FTS metadata matching differ. This does not establish a novel-query search win over Jellyfin.

All 120 Linux page-advice trials decoded. Both media mounts had the same Linux device/inode, verified before the observer evicted it. `mincore` residency is recorded per trial; the macOS host cache remained warm. Candidate p95 was 36.8 ms versus Jellyfin 33.8, so no cold-cache lead is claimed. A separate 30-trial sign-in-to-frame probe measured median 135.8 ms Lumina versus 104.2 ms Jellyfin; p95 was 222.2 versus 263.3. That scope includes password work and broader MediaSources search preparation and does not replace prepared-source timing. Instrumented CDP request/header/data and media/decode events are diagnostic evidence, with their instrumentation costs, not a substitute for the original frame trials.

## Qualification

The pinned `make check` completed bootstrap, frontend types/build, then reported 4,461 backend passes, 11 skips and two native FFmpeg seek-copy failures. Both failures reproduce on pristine `origin/main`; neither assertion was weakened. The production dependency focused suite passed 269 cases, including both seek-copy cases and the new visibility/cache/range/auth regressions.

The frontend unit suite passed 2,256 tests. Real-stack journeys/performance passed 46 tests with four skips. Chrome passed all 34 playback performance tests and WebKit all four real-decode tests. The mocked suite reported 483 passed, one skipped and two failures. A single-worker focused rerun passed 21 cases; the remaining missing Shorts heading failure reproduces on pristine `origin/main`. The initial menu-layout failure passed the focused rerun. Failures and artifacts were retained.

The full final-production-runtime Linux suite reported 4,462 passed, eight skipped and four failed: Chromaprint shared-intro matching, encoder timeout cleanup, transcode throttle/resume timing and HDR10 software tone mapping. All four reproduce on pristine `origin/main` in the same runtime (and on the focused current-source rerun). No assertion was weakened. The native gate and Linux gate are therefore qualified with known baseline failures, not clean gates.

## Preserved attempts

The early concurrency sweep found that increasing item-read slots increased CPU and worsened tails. An intermediate ranked-ref candidate improved search but remained slower than Jellyfin at concurrency eight; the default-card cache addresses its measured rendering cost. An interrupted benchmark exposed a rollback-cache edge, which now has a failing-before/fixed-after regression. Earlier connection-pool rotation had hidden that edge; the regression pins the same connection.

An explicit-version join prototype hit SQL auto-correlation errors and was replaced by a visible-parent subquery. A receive-disconnect regression demonstrated that silent sends read the entire abandoned 16 MiB test file; the fixed response stops after its in-flight reads and closes the handle. The initial full-Linux harness failed collection because its UID 1000 process could not create `/data`; rerunning uses an isolated writable temporary data directory. The network-restricted Linux attempt reported 4,376 passes and 90 failures, chiefly blocked DNS policy checks; the corrected full production-runtime result above uses normal network with the suite’s provider-connection guard intact. One supplemental observer lacked permission to read process descriptors, and a guest-cache helper initially expected Python in the official Jellyfin image. Both harness errors were corrected and preserved. One early limited-load group recorded eight frame failures and 272 startup 503s after public-only readiness. It is excluded from comparisons; all four qualified groups require authenticated media readiness. All attempts and logs are preserved locally; exploratory, failed and interrupted runs do not contribute to final claims.

The search, initial direct-play changes and their qualification report have been merged in PRs #19–#22. Production still runs the original shipped control while the idle and varied-media follow-up is qualified.

The follow-up combines initial open/seek/read in one worker handoff, projects selected file and root together, and moves the enabled-setting/authentication check into the endpoint's existing worker. Four balanced idle/varied blocks compare each frozen variant with its control and paired Jellyfin; each experiment retains all 960 decoded-frame attempts. A four-chunk 64 KiB startup window and a single 256 KiB startup burst did not produce a repeatable idle-and-varied win and were reverted. The burst reduced varied p95 against the shipped control by 16.2%, but still trailed paired Jellyfin (38.9 versus 36.3 ms); idle was also behind (32.4 versus 32.2 ms). These are rejected experiments, not acceptance evidence.

## Reproduction and artifacts

The local `search-direct/final-qualification` bundle contains per-request API rows, all browser samples, phase manifests, initialized-database hashes, frozen application/test hashes, complete harness source and SHA-256 maps. Target credential JSON stays in private working roots. The checked-in summary lists every raw artifact hash; it contains no credentials or private media. Original image IDs, request counts and canonical equivalence are bound in `balanced-manifest.json` and `final-summary.json`; loaded qualification is bound independently in `loaded-qualified-manifest.json`.

Use the preserved harness from the repository root under the pinned toolchain:

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  <locked-python> <harness>/balanced.py
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  <locked-python> <harness>/loaded_qualification.py
```

The drivers explicitly select the full control/candidate images and clone stopped initialized databases; original and varied media stay separate. Edit working/output paths in the preserved local harness for a new workspace. Do not run correctness suites or other timing phases concurrently with benchmarks. Source manifests describe the frozen application commit; this documentation commit does not change the measured application.
