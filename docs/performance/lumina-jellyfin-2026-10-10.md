# Controlled Lumina and Jellyfin performance comparison, 2026-10-10

This report compares full production Lumina images at baseline `392719d58460be00e8080fed96a50e221537b94a` and final `d8112ea98ff311e11b0375497e2a447de1ce173a` with the official Jellyfin 12.0 arm64 image. It describes one controlled local workload on a noisy development host. It does not establish universal product superiority.

## Protocol 3: qualifying result

The machine-readable authority is `final-comparison-summary.json`. Every input is named and SHA-256 hashed there. Separate phase manifests bind API, startup, direct-frame, and HLS-frame artifacts to the environment captured immediately before each phase.

| Runtime | Exact image ID | Uncompressed image size |
| --- | --- | ---: |
| Lumina baseline | `sha256:431d162e931ff9fb6ab51fd42ec36999aead712b3807fa173a6ab8c11e3a1b5d` | 897,986,711 B |
| Lumina final | `sha256:40047afc3d1969d3ad1525f5e0141a8b4aca70c9792dd70dc9d0c220695ebe78` | 883,233,853 B |
| Jellyfin 12.0 | `sha256:48cd39885e47f52367f11456a57e9450fee197f706aa0781588bb1d01aa528c5` | 867,376,808 B |

The final Lumina image is 14,752,858 bytes (1.64%) smaller than baseline and 15,857,045 bytes (1.83%) larger than Jellyfin. Docker image size is the sum of uncompressed layers, not download size or unique disk use. The production image retains the ASR, LLM, embedding, FFmpeg, and Node runtimes. The benchmark database configured no LLM, ASR, embedding, local-search, or local-speech model, so ready/serving memory below does not cover active model inference.

The host was an Apple M4 Max with 16 logical CPUs and 64 GiB RAM. Docker Desktop 29.8.0 provided a Linux arm64 VM with 16 vCPUs, 8,318,709,760 bytes of memory, cgroup v2, and overlay2. Unrelated user workloads remained active. Raw files retain host-load snapshots and Docker cgroup counters.

The synthetic corpus has 302 movie paths derived from two media files: a 120-second, 1280x720 H.264/AAC direct-play fixture (62,516,911 bytes, SHA-256 `b99a007e...`) and a 30-second, 1280x720 HEVC Main 10/E-AC-3 transcode fixture (12,132,243 bytes, SHA-256 `55b43166...`). The seed used hard links; copied phase roots did not preserve inode sharing. This repeated-content corpus exercises indexing and serving mechanics, not a diverse metadata collection. Jellyfin internet metadata providers and LUFS scanning were disabled.

A fully warmed, stopped database was cloned byte-for-byte for baseline and final. Both Lumina clones have SHA-256 `97a90f8c...`; both Jellyfin clones have SHA-256 `47e33ead...`. The two phases mounted the same read-only media root. Each server returned 302 movies. Lumina baseline/final page and search response keys, IDs, names, cardinalities, and canonical JSON hashes match exactly. The two Jellyfin controls also match exactly.

### API latency and throughput

Each scenario used ten warm-ups and 300 requests at concurrency 1, 8, and 32. The table shows p50 / p95 / wall requests per second. Lumina returned 200 for every request. The first Jellyfin control recorded two `/Items` and one search `ReadTimeout` at concurrency 32; percentiles describe successful responses while wall throughput includes the 120-second timeouts. The second Jellyfin control returned 200 for every request. Those timeouts are preserved but are not attributed to Jellyfin itself because container, client, and host causes were not isolated.

| Scenario | C | Baseline | Final | Jellyfin before | Jellyfin after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Public system info | 1 | 1.56 / 2.58 / 524.55 | 1.50 / 2.33 / 631.81 | 3.20 / 3.91 / 307.90 | 2.42 / 3.31 / 385.99 |
| Public system info | 8 | 12.20 / 20.02 / 614.98 | 7.76 / 16.47 / 896.27 | 6.01 / 9.45 / 1,219.15 | 5.06 / 9.42 / 1,383.06 |
| Public system info | 32 | 74.21 / 130.98 / 412.46 | 43.98 / 74.67 / 657.92 | 30.96 / 60.13 / 919.57 | 31.33 / 85.21 / 782.54 |
| Current user | 1 | 3.55 / 4.60 / 276.70 | 3.17 / 4.02 / 316.18 | 8.06 / 9.43 / 122.92 | 7.09 / 7.82 / 141.28 |
| Current user | 8 | 18.04 / 28.27 / 430.06 | 13.31 / 21.37 / 574.66 | 14.97 / 19.70 / 516.07 | 14.01 / 18.62 / 552.85 |
| Current user | 32 | 105.49 / 184.19 / 279.19 | 74.54 / 147.39 / 384.03 | 97.70 / 218.18 / 289.05 | 88.72 / 145.15 / 325.30 |
| 60-item page | 1 | 15.09 / 16.89 / 60.35 | 13.60 / 15.16 / 69.31 | 32.53 / 36.32 / 30.41 | 27.61 / 30.27 / 35.82 |
| 60-item page | 8 | 281.30 / 434.73 / 26.12 | 83.98 / 155.65 / 82.10 | 109.01 / 138.01 / 72.21 | 97.40 / 125.05 / 80.51 |
| 60-item page | 32 | 1,701.21 / 2,263.15 / 18.01 | 417.19 / 442.75 / 77.88 | 628.33 / 972.60 / 2.46¹ | 547.90 / 819.03 / 55.59 |
| Search `Benchmark` | 1 | 36.78 / 106.69 / 23.11 | 21.64 / 25.94 / 43.83 | 19.10 / 20.67 / 52.09 | 18.17 / 20.16 / 54.71 |
| Search `Benchmark` | 8 | 1,828.07 / 2,018.76 / 4.38 | 154.82 / 229.00 / 48.25 | 39.89 / 58.84 / 185.37 | 41.71 / 58.01 / 181.39 |
| Search `Benchmark` | 32 | 8,201.90 / 11,407.52 / 3.70 | 664.07 / 696.98 / 47.67 | 254.62 / 392.88 / 2.49¹ | 290.04 / 375.34 / 111.69 |

¹ The timeout-depressed wall rate is not used for a comparative throughput claim.

At concurrency 32, final page p50 fell 75.5% from baseline and p95 fell 80.4%; throughput rose 4.32×. Search p50 fell 91.9%, p95 fell 93.9%, and throughput rose 12.88×. Cgroup CPU for the 300-request page batch fell from 29.84 to 4.21 seconds; search fell from 157.10 to 6.63 seconds. Maximum Docker-reported container memory in those batches changed from 199.3 to 175.6 MB for page and 263.5 to 181.6 MB for search. Docker's `MemUsage` excludes inactive file cache; these values are neither process RSS nor raw cgroup `memory.current`.

Cross-server search is a practical 60-card client workload, not identical semantics: Lumina reports 200 candidates and Jellyfin 180. The final Lumina page p50 was lower than the all-success Jellyfin control at concurrency 32 (417 versus 548 ms), while final Lumina search p50 was higher (664 versus 290 ms). These local outcomes do not support a blanket server ranking.

### Metadata compression

Controlled identity/gzip blocks made 50 requests per representation in identity, gzip, gzip, identity order. Baseline served both requests as a 209,721-byte identity representation. Final served identity at 209,721 bytes and gzip at 7,840 bytes, a 96.26% reduction in encoded HTTP body `Content-Length`, with identical decoded SHA-256. Total network traffic, including headers and transport overhead, was not measured. Final median latency was 13.15 ms for identity and 13.22 ms for gzip; measured cgroup CPU was 0.796 and 0.664 seconds respectively. This local sample shows a smaller encoded body without a measurable latency penalty; it is not a remote-network result.

### Direct streaming

Thirty first- and middle-megabyte range requests per server returned 206 with exact range sizes. First-megabyte TTFB p50 was 6.79 ms baseline, 6.48 ms final, and 6.11/5.96 ms in the Jellyfin controls. Every sustained stream reproduced the 62,516,911-byte source hash.

| Full-file direct stream | Baseline | Final | Jellyfin before | Jellyfin after |
| --- | ---: | ---: | ---: | ---: |
| Sequential aggregate MiB/s | 314.47 | 339.16 | 338.87 | 337.41 |
| Eight concurrent aggregate MiB/s | 343.46 | 372.84 | 363.72 | 374.03 |
| Eight-stream completion p50 | 1,374.34 ms | 1,264.77 ms | 1,287.73 ms | 1,253.46 ms |

The final direct result falls within the bracketing controls; it does not establish a broad direct-throughput advantage.

### Playback startup

Three HLS server-path trials requested H.264/AAC, at most 854x480, one-second segments, and 2 Mbps. Final Lumina intent-to-first-segment-byte p50 was 135.90 ms versus 139.43 ms baseline; Jellyfin controls measured 277.76 and 278.76 ms. Lumina encoded 854x480 and Jellyfin 852x480; audio encoder details and server defaults differ. Three-run p95/p99 equal the maximum, so this is endpoint-shape evidence, not equal-quality encoder throughput evidence.

The same local Chrome and hls.js client then ran 30 order-balanced fresh-context trials per server:

| Intent to first decoded frame | Lumina p50 / p95 | Paired Jellyfin p50 / p95 | Successes |
| --- | ---: | ---: | ---: |
| Direct, baseline phase | 24.5 / 63.5 ms | 25.0 / 59.2 ms | 30/30 each |
| Direct, final phase | 28.6 / 76.9 ms | 27.8 / 34.6 ms | 30/30 each |
| HLS, baseline phase | 223.5 / 344.6 ms | 320.2 / 364.4 ms | 30/30 each |
| HLS, final phase | 192.5 / 260.7 ms | 308.4 / 356.2 ms | 30/30 each |

Direct timing starts at prepared media-source assignment, after authentication and playback-source preparation. It is not complete user-play negotiation. HLS timing starts before PlaybackInfo and ends at `requestVideoFrameCallback`; direct and HLS absolute numbers therefore have different scopes. Direct decoded 1280x720. HLS decoded 854x480 on Lumina and 853x480 display width on Jellyfin. Nonfatal hls.js `bufferStalledError` events occurred in every group and are retained; no trial failed.

### Scan and restart readiness

A fresh, isolated full-production final scan used an in-container Linux SQLite read-only observer while the container owned the WAL. Host-side live SQLite observations from earlier attempts are preserved separately as exploratory evidence because cross-host WAL visibility was unreliable.

| Fresh 302-path scan | Metadata visible | All named video codecs and positive runtime ready | Additional readiness |
| --- | ---: | ---: | ---: |
| Lumina final | 0.7416 s | 6.3221 s | 302 numeric loudness results at 406.4435 s |
| Jellyfin 12.0 | 0.9954 s | 88.5660 s | library refresh task idle at 88.5660 s; LUFS disabled |

Post-scan validation found 302 parsed Lumina probe objects, 302 named video codecs with positive duration, zero parse/probe errors, and 302 numeric `{i,tp,lra}` loudness objects. The comparable shared gate is codec/runtime readiness. Lumina's later loudness completion is a separate workload that Jellyfin did not run. Metadata-visible times alone are not a complete-scan comparison. This is one fresh scan over repeated media content, so no population-level scan ratio is claimed.

The probe-first scheduler was also measured in a same-source native harness at revision `65eda84`, using legacy interleaving versus probe-first ordering. Codec readiness changed from 444.8667 to 8.2983 seconds (53.61×), while full loudness completion changed from 446.3740 to 447.3842 seconds (+0.226%). A separate artifact confirms all 302 stored parsed probe values are canonically identical. This is a scheduling A/B, not a comparison between pristine Git revisions or servers.

Five initialized restart trials used the same successful Jellyfin-compatible `/Users/AuthenticateByName` request and credentials for both servers. Median Docker-start-to-authenticated-ready time was 1.8003 seconds baseline, 1.6875 seconds final, and 4.8955/4.6977 seconds in the Jellyfin controls. Final Lumina ready-time Docker-reported container memory was 119.9–121.0 MiB; its Jellyfin control was 307.3–320.0 MiB. Docker's `MemUsage` excludes inactive file cache; these values are neither process RSS nor raw cgroup `memory.current`. They cover model-inactive benchmark settings.

## Qualification and limits

The final image passed the real tiny Llama and ASR model smoke and an image contract under locked production FastAPI 0.139.2 / Starlette 1.3.1. The final frontend tail passed bootstrap, types, build, 2,256 unit tests, 46 real-stack tests (4 skipped), and 485 mocked tests (1 skipped). Playback qualification passed 34 Chrome performance tests and 4 WebKit tests.

The final Linux full suite reported 4,440 passed, 8 skipped, and 3 failed, versus pristine baseline 4,404 passed, 8 skipped, and 2 failed. Two FFmpeg media failures were common. The additional final-suite category-planner failure is classified as a pre-existing, fresh-schema layout-dependent SQLite planner risk rather than evidence of a candidate query regression. A 34-case Linux matrix reproduced the bad plan on both sources: baseline failed 10 of 16 repeated exact cases and final failed 4 of 16. Across the exact cases and two prefix controls, all 15 failures created `ix_media_titles_category_added` first and selected the `type_added` plan; all 19 passes created another competing title index first and selected the `category_added` plan. Target SQL and parameters were identical, PRAGMAs matched, and no case had `sqlite_stat1`. The sampled outcomes were repeatable, but the underlying SQLite tie-break mechanism remains unproven. No assertion was changed or weakened.

Other accepted measurements include directory relist median 318.556 to 6.565 ms and subtree remove 575.628 to 0.578 ms, with +7.85 MB at 40,001 directories; typed semantic materialization 18.440 to 14.204 ms with identical response hashes; and pinned media delivery 75.55 to 26.06 ms with combined CPU down 52.2% and first byte 0.78 to 0.86 ms. The title-letter proposal was reverted: the representative roughly 2,585-row workload showed essentially no median gain and a worse p95, while the roughly 50,000-row synthetic workload improved by about 11%.

## Reproduction

Run from the repository root with the locked environment. `compare_stack.py up` requires an explicit Lumina image so a historical default cannot enter a timed phase.

```bash
python scripts/perf/compare_stack.py up work/final-protocol3-baseline \
  --lumina-image lumina-perf-production:baseline \
  --jellyfin-image jellyfin/jellyfin:12.0
python scripts/perf/compare_servers.py work/final-protocol3-baseline/target-lumina.json \
  --out outputs/final-baseline.json --api-count 300 --stream-runs 30 --transcode-runs 3
python scripts/perf/compare_servers.py work/final-protocol3-baseline/target-jellyfin.json \
  --out outputs/final-jellyfin-baseline-control.json --api-count 300 --stream-runs 30 --transcode-runs 3
python scripts/perf/compare_stack.py startup work/final-protocol3-baseline --runs 5
node scripts/perf/compare_direct_frame_pair.mjs \
  work/final-protocol3-baseline/target-lumina.json \
  work/final-protocol3-baseline/target-jellyfin.json outputs/direct-frame.json 30
node scripts/perf/compare_hls_frame.mjs \
  work/final-protocol3-baseline/target-lumina.json \
  work/final-protocol3-baseline/target-jellyfin.json outputs/hls-frame.json 30
```

Repeat with the final clone and `lumina-perf-production:final`, then run `summarize_final_compare.py` with all three expected image IDs and both expected source revisions. Target JSON files contain synthetic credentials and stay under `work/`; public deliverables contain no passwords or tokens.

## Historical evidence excluded from protocol 3

Protocol 2 used a 406,955,379-byte trimmed Lumina runtime and remains useful for harness development, but it cannot support production-image size, startup, memory, or final performance claims. The later `1cada1d` pass measured an intermediate full-production candidate before probe scheduling, compression, packaging, semantic, and playback work was frozen. The first attempted protocol-3 baseline accidentally reused the trimmed image; its raw artifacts are preserved with `invalid-trimmed` names and excluded by exact image-ID and phase-manifest validation. Earlier A search/HLS passes used mismatched response cardinality or negotiated dimensions and remain exploratory only.
