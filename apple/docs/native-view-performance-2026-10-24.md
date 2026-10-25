# Native view performance — 2026-10-24

## Result: partial clean iOS view comparison

This report compares baseline `9e22118` with optimized source `9cf8b60`. It includes clean before/after iOS Release captures for Navigation and Catalogs, plus clean baseline-only Playback and five-return-cycle captures. The owner explicitly ended profiling before optimized Playback and Cycles to prioritize dogfood fixes; those after values are **not captured**, not failed or inferred.

All earlier per-view `ios-baseline` and `ios-optimized` captures are excluded because they overlapped system PosterBoard crashes. A fresh iOS 27.1 Duo control ran for 319 seconds without Lumina installed and had no exact-simulator PosterBoard/SpringBoard incident. The final clean-boot diagnostic was also empty. Every accepted report had `capture.valid=true`, no system crashes, and a successful Time Profiler exit.

CPU is percent of one host core. Memory is end-of-settled-window charged physical footprint in MiB. Each view has one capture, with a two-second settle then eight seconds of sampling (45 seconds for the final return cycle). These are workflow observations, not a controlled statistical study. Builds use `LUMINA_UI_TESTING` for credential/preference isolation with 120 synthetic catalog titles and 1920×1080 media. Detailed samples, test logs and traces remain under `/tmp/lumina-perf-20261024`.

## Clean navigation

| View | CPU before | CPU after | MiB before | MiB after |
| --- | ---: | ---: | ---: | ---: |
| Login form | 0.495% | 0.604% | 29.7 | 29.8 |
| Home | 4.077% | 4.972% | 53.0 | 52.1 |
| Home feature detail | 0.040% | 0.050% | 54.6 | 53.6 |
| Library | 0.031% | 0.038% | 66.6 | 65.6 |
| Movie detail | 0.036% | 0.030% | 70.4 | 72.2 |
| Show detail | 0.047% | 0.033% | 72.3 | 71.6 |
| Show season | 0.041% | 0.035% | 73.2 | 72.1 |

Home increased from 4.077% to 4.972%. One capture cannot establish whether that increase is repeatable; hero motion remains enabled. The static views remain near idle, and these readings do not establish an app-wide improvement.

## Clean catalogs

| View | CPU before | CPU after | MiB before | MiB after |
| --- | ---: | ---: | ---: | ---: |
| Login form | 0.600% | 0.681% | 29.8 | 29.7 |
| Home | 4.592% | 4.940% | 52.8 | 52.3 |
| Requests | 0.057% | 0.091% | 57.2 | 56.5 |
| Requests detail | 0.071% | 0.059% | 59.6 | 59.0 |
| Requests history | 0.041% | 0.046% | 62.6 | 62.2 |
| Requests search | 0.021% | 0.022% | 63.5 | 62.8 |
| Account | 0.024% | 0.019% | 66.5 | 66.7 |
| Account Home editor | 0.018% | 0.025% | 68.8 | 69.2 |
| Books catalog | 0.038% | 0.037% | 83.2 | 83.3 |
| Books reader | 0.099% | 0.100% | 87.5 | 87.5 |
| Books comic catalog | 0.019% | 0.020% | 85.3 | 85.2 |
| Books comic reader | 0.022% | 0.165% | 85.8 | 85.8 |

## Baseline-only playback and return cycles

| View | CPU baseline | MiB baseline | Optimized |
| --- | ---: | ---: | --- |
| Video playing | 12.760% | 113.0 | Not captured |
| Video paused | 0.556% | 113.0 | Not captured |
| Video settings video | 0.458% | 117.8 | Not captured |
| Video settings audio | 0.434% | 116.9 | Not captured |
| Video settings subtitles | 0.430% | 117.1 | Not captured |
| MKV client demux playing | 12.084% | 142.7 | Not captured |
| MKV client demux settings | 0.481% | 162.5 | Not captured |
| MKV post-close | 0.443% | 78.3 | Not captured |
| Audio catalog | 1.809% | 76.2 | Not captured |
| Audio album | 0.475% | 77.2 | Not captured |
| Audio track detail | 0.471% | 77.2 | Not captured |
| Audio playing | 2.842% | 80.0 | Not captured |
| Audio queue | 2.982% | 80.9 | Not captured |
| Audio playlists | 0.475% | 80.4 | Not captured |
| Audio playlist detail | 0.480% | 81.2 | Not captured |
| Audio downloads | 0.471% | 80.7 | Not captured |

The five clean return-cycle end footprints were 77.9, 77.9, 77.7, 77.7, and 77.7 MiB. That flat baseline does not demonstrate a reproduced leak fix; optimized cycles were not captured.

## Deterministic cancellation and cache checks

The standalone artwork stress is separate from the simulator view captures: three alternating fresh-host-process pairs model 29 abandoned unique cards, one visible card, and two consumers sharing one card with delayed 150 ms responses. It is accepted only for this fixture.

| Metric | Before | After |
| --- | ---: | ---: |
| Requests started | 31 | 6 |
| Images decoded | 31 | 2 |
| Process CPU, median | 48.777 ms | 9.545 ms |
| Survivor wall time, median | 167.020 ms | 190.584 ms |

The queue trades 23.6 ms of median survivor wall time in this saturated fixture for less abandoned network/decode work. Both retained images were 400×600 and the shared URL fetched once. This is not a general navigation-latency claim.

Deterministic service checks retain a full 640-track resolved queue while bounding lookup history to 512 entries, reduce five ready Requests framing reads to one request, and cancel one obsolete 300-ms library query instead of zero.

## Validation and limits

85 focused native contracts, nine sampler checks, the optimized artwork `--check`, the iOS Release build, and six clean UI cohorts passed. The full repository gate was intentionally skipped under the owner's dogfood instruction. No version bump, archive, upload, deployment, release, or tag was created.

This partial result makes no app-wide CPU, memory, FPS, hitch, or zero-leak claim. Simulator captures omit physical-device thermal behavior, GPU/decoder-service cost, HDR, and display pacing; host load varies. The slow-server rapid-track-change playback-report backlog and foreground-independent Sonos polling remain separate measurement targets, with no redesign in this pass. The complete compact measurements are in [the results JSON](../Benchmarks/results/2026-10-24-ios-views.json).
