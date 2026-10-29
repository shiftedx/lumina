# Apple client performance review — 2026-10-20

Scope: native iPhone, iPhone Duo, iPad and tvOS clients, shared AVFoundation music/video playback, and the server playback decision boundary. This is a source review with synthetic execution evidence and focused improvements. It is not a completed physical-device CPU, energy, thermal, HDR or HDMI qualification.

## Results implemented

| Work | Before | After | Evidence |
| --- | --- | --- | --- |
| Qualified MKV original delivery | Server HLS remux per playback | On-device stream copy; zero server FFmpeg jobs | 60-second synthetic cohorts: client peak RSS −3–10%, client CPU +22–30%; §2e |
| FLAC video in native MP4, 12-second synthetic fixtures | FFmpeg AAC encode, 130–470 ms process CPU | Direct original; no FFmpeg process | H.264/HEVC, 24-bit stereo/5.1/7.1; detailed qualification and client tradeoffs in §2d |
| HEVC + 7.1 FLAC in MKV, 12-second fixture | 466 ms FFmpeg CPU; 37.47 MiB child peak RSS | 26.4 ms CPU (−94%); 25.97 MiB RSS (−31%) | Audio/video stream copy; retains 8 channels rather than the previous stereo AAC fallback; §2d |
| Active progressive WAV, startup plus ten seconds playing | 46.27 MiB peak; 409.3 ms process CPU | 27.05 MiB peak (−42%); 158.8 ms CPU (−61%) | Three fresh processes/revision; all five formats and window limits in §2c |
| Paused progressive WAV, startup plus ten-second pause | 42.39 MiB peak; 358.5 ms process CPU | 22.49 MiB peak (−47%); 108.0 ms CPU (−70%) | Three fresh processes/revision, native host benchmark; all five formats in §2b |
| Progressive remote MP3, admitted gain-free queue | 8.21 s startup; complete input sent | 0.59 s startup; 4.5% sent | Five fresh processes/revision on an eight-second paced delivery; higher native-player memory/CPU costs detailed in §2a |
| File scheduling at `595d9a3`, local 120 s two-track queue | 195.4 ms startup; 139.53 MiB peak footprint; 173.1 ms process CPU | 133.7 ms startup; 8.84 MiB peak; 45.9 ms CPU | Median of five fresh processes; gain/crossfade enabled, two-second post-confirmation window |
| Music pause/resume follow-up, ten cycles | 11 downloads and full PCM conversions; 33,837,056 media bytes | 1 initial download/conversion; 3,076,096 media bytes | Real loopback HTTP workload; retained native player nodes/buffers, 90.9% less delivered media |
| Steady music progress notifications over two playback seconds | 20 | 3 | Same synthetic WAV regression against original and revised transport; 85% fewer callbacks in this sample |
| Requests for an unavailable streaming successor over 700 ms | 8 | 1 | Same failing source callback against original and revised transport; explicit queue refresh permits one new attempt |
| Normalization work during a decoded playback tick | Queue-wide album/order scans and gain calculation | Array lookup; gains refreshed on queue/normalization/shuffle changes | Policy regression preserves album dynamics, shuffle, manual overrides and disabled normalization |
| Unchanged audio parameter writes | Two EQ gain writes and one node-volume write per decoded tick | Writes only when the value changes | Shared transport implementation; crossfade engine contracts retained |
| A 240-second, 48 kHz stereo source at PCM admission | Admitted from a one-buffer estimate, then rejected after downloading when source + converted PCM exceed the decoder budget | Rejected before full-file download; selects streaming | Existing admission contract extended for combined buffers and high-rate input |
| Video direct-play audio profile | AAC, AC-3, E-AC-3 | Also MP3 and ALAC | Request serialization, server decision table and actual synthetic macOS AVPlayer playback |
| H.264 High 10 / HEVC 12-bit negotiation | No bit-depth limit; server interpreted profile as permitting 16-bit video | H.264 capped at 8-bit; HEVC at 10-bit | Request serialization and server decision table select compatible video encoding |
| Streaming item bookkeeping | Retained identifiers for departed queue items | Pruned on item transition and lookahead invalidation | Source review; current/upcoming item ownership remains bounded |
| Failed streaming runtime | Playback intent stopped but 100 ms task could remain alive | Cancels the clock on stream failure | Existing invalid-stream playback contract retained |

The callback and request counts are measured operation counts on the host Mac, not device CPU percentages. Do not translate an 85% callback reduction into an 85% energy or CPU reduction. Internal envelope timing remains 100 ms, and decoded successors still use AVAudioTime on the audio clock. Outgoing progress is published before a decoded track transition, and pause/finish/error changes remain immediate. The elapsed-time UI and timed-lyric highlighting now receive roughly one-second progress updates; subsecond lyric highlighting would need a narrowly scoped visible-lyrics clock rather than restoring ten updates per second to every consumer.

The optimized host microbenchmark (`scripts/benchmark_music_gain.swift`) applies gain 1,000 times, taking the median of five runs. The revised measurement includes one complete cache rebuild per run, so it does not hide initialization cost:

| Queue length | Previous queue scans + gain | Cached policy including rebuild |
| --- | --- | --- |
| 100 | 7.626 ms | 0.0122 ms |
| 1,000 | 80.110 ms | 0.0841 ms |
| 10,000 | 857.056 ms | 0.9399 ms |

This isolates gain-policy computation on the host; it is not an app-wide speedup or a device energy measurement. Run from `apple/` using the compile/run instructions at the top of the script. The checksum keeps results consumed; synthetic ordered album data exercises the former full-queue scan path. Timing depends on the host and queue shape.

An additional correctness issue surfaced during the failure regression: an unavailable upcoming track populated the shared warning and made `confirmPlaying` reject a currently playing output. Confirmation now relies on playback intent and actual engine/player rendering; successor warnings do not reject a valid handoff. Invalid current media still cannot acknowledge a handoff.

Changed implementation: `Core/Sources/LuminaCore/NativeMusicTransport.swift`, `MusicPlaybackPolicy.swift`, and `LuminaClient.swift`. Existing engine/policy/client and backend decision contracts were extended rather than adding dependencies or a second playback engine.

## iPhone Duo continuity requirement

User acceptance requirement: in the normal full-screen opening transition, preserve the view visible when closed as the right-hand content pane when opened. Reveal supplementary navigation on the left, while preserving selected destination, navigation history, scroll position and playback. The opening animation must not rebuild the player or send a new playback-start request. This requirement is also recorded in `duo-notes.md`.

The source uses a persistent `NavigationSplitView` with `.detail` as the preferred compact column and the existing `TabView`/destination stacks in the detail pane (`App/iOS/MobileRootView.swift`). In the left-to-right expanded layout this places the existing content to the right of the sidebar. There is no custom hinge animation or per-pose player construction.

Apple’s documented continuity clause is: “Preserve functionality, element state, hierarchy, and access across displays/poses.” The requested physical-right placement is recorded separately as the product acceptance criterion; the cited material should not be presented as proving every opening-animation detail. Apple recommends adaptive standard navigation and independently respected asymmetric safe areas. Sources: [Duo HIG](https://developer.apple.com/design/human-interface-guidelines/designing-for-iphone-duo), [Prepare your app for iPhone Duo](https://developer.apple.com/videos/play/tech-talks/111461/).

Status: manual Duo continuity review accepted by the user on 2026-10-20. A user-approved CoreDeviceService restart recovered discovery and the live Lumina Duo display with Closed/Book/Open controls. The native fixture server was restarted and the app reconnected to a populated library. After initially reporting “this looks correct to me”, the user confirmed “I tested and it looks good” and authorized merging. This records acceptance of the live simulator review; the broader physical-device/pose/playback matrix remains separate. RTL, split-screen placement on either side, inner portrait, and outer landscape still require individual assessment; logical trailing placement alone does not prove physical-right placement in all configurations.

One layout risk to inspect live: the regular-width Now Playing inset attaches to the entire split view, so its controls can span the sidebar and content instead of remaining wholly in the continuing detail pane. Move it into the detail container if a live fold test shows controls crossing the fold or breaking continuity. No speculative navigation rewrite was made in this review.

## Remaining findings, ordered by expected impact

### 1. Music pause/resume — fixed in the prior follow-up

Ordinary pause now uses `AVAudioPlayerNode.pause()` / `AVAudioEngine.pause()` or `AVQueuePlayer.pause()` and cancels the app's playback clock and unfinished lookahead. It retains only this transport's existing bounded engine slots or native streaming queue. Resume starts the same nodes/player, shifting decoded host-clock boundaries by the paused interval so a scheduled successor and an in-progress fade stay aligned. It does not resolve the source, download, perform another whole-track conversion/copy, recreate the player, or publish another track/start event.

Stop, track selection/seek, owner replacement/sign-out, completion, terminal stream failure and explicit authorization failure still discard playback resources. Repeat/crossfade edits while paused invalidate the old scheduled successor. A source or seek finishing after Pause cannot restart playback. There is no new file cache, shared member cache, retained temporary input or new dependency. At that revision, pausing retained existing PCM memory until teardown. The next section replaces that memory with file-backed preparation. Streaming may still fetch bytes needed for ordinary buffering; reusing its item does not promise zero future network reads or system decoder work.

The pause/resume figures below were captured at `ac5f5ee`, before the file-scheduling follow-up removed the remaining initial whole-track conversion (next section). The host workload uses an eight-second synthetic stereo 48 kHz WAV (3,076,096 file bytes), an uncached loopback HTTP response with a fixed 100 ms delay, ten 40 ms pauses and ten resumes. Both versions use the same 80 ms decoded start lead. Resume latency spans Resume through `confirmPlaying()`; the revised confirmation requires a render timestamp after the resumed start, so a stale pre-pause render cannot acknowledge playback. This is engine render confirmation, not acoustic/output latency. The median is the upper middle observation of ten cycles, with a 50 ms confirmation polling interval.

| Ten-cycle workload, including initial load | Before (`ff76696`) | After |
| --- | --- | --- |
| Source resolutions | 11 | 1 |
| Full HTTP media downloads | 11 | 1 |
| Full source PCM conversions | 11 | 1 |
| Delivered media bytes (HTTP headers excluded) | 33,837,056 | 3,076,096 |
| Resume-to-render confirmation median | 233.181 ms | 125.010 ms |
| Streaming source resolutions (separate local-file workload) | 11 | 1 |

This eliminates all ten repeat downloads/conversions in the decoded workload: 30,760,960 fewer media bytes, a 90.9% reduction including the first load, and about 46% lower measured median resume confirmation latency. The before regression failed as expected; the revised regression passed. Full PCM conversion counts were verified in isolated before/after package copies by logging successful conversion completion, before the test's ten-cycle marker; the subsequent Stop/restart converted once more in each version. This instrumentation is not in production source. The latency is a single host comparison on a shared Mac with an injected network delay, not a physical-device CPU, energy or latency guarantee.

Retained public transport checks cover ten decoded HTTP cycles, ten streaming cycles, Stop forcing a new preparation, stable paused position, one track event per occurrence, one immediate state publication on Resume, startup cancellation, pause beyond a queued successor's old start, pause during a fade, Repeat changes while paused, duplicate occurrences, restored paused seek, pending Wait boundaries and invalid-output handoff refusal. Run the measurable decoded workload with:

```sh
mise exec -- swift test --package-path apple/Core --scratch-path /tmp/lumina-pause-resume-core --filter testTenDecodedPauseResumeCyclesReuseTheDownloadedAudio
```

Local evidence: `/tmp/lumina-pause-resume-before.log`, `/tmp/lumina-pause-resume-final-transport.log`, `/tmp/lumina-pause-resume-measured-before.log`, `/tmp/lumina-pause-resume-measured-after.log`, `/tmp/lumina-pause-resume-native-check.log`. The full native script passed all 44 Core tests, including three live-server cases and authorization teardown in both transport modes, and both iOS/tvOS Simulator builds. The full `mise exec -- make check` invocation passed: bootstrap, frontend typecheck/build, 5,239 backend tests (11 skipped), 2,622 frontend tests across 299 files, 52 real-stack cases (5 skipped), and 657 mocked browser cases (2 skipped, 19.9 minutes). No budget was relaxed and no rerun was needed for this gate. Full-gate log: `/tmp/lumina-pause-resume-make-check.log`. Physical interruption/route changes and output timing still need a device pass. Native API references: [player-node pause](https://developer.apple.com/documentation/avfaudio/avaudioplayernode/pause()), [engine pause](https://developer.apple.com/documentation/avfaudio/avaudioengine/pause()), [scheduled resume](https://developer.apple.com/documentation/avfaudio/avaudioplayernode/play(at:)).

### 2. Whole-track decoding — replaced with native file scheduling

The engine now holds an `AVAudioFile` per prepared slot and uses `AVAudioPlayerNode.scheduleSegment`, priming 4,096 frames before playback. AVFoundation performs file decoding/read-ahead and file sample-rate/channel adaptation into the existing 48 kHz stereo engine graph. The app no longer allocates a whole source PCM buffer, converts a whole track into another buffer, or copies the remaining track into a seek slice. Album/manual gain, peak-based gain limiting, the two EQ stages, equal-power fades, shared audio-clock scheduling and pause/resume reuse remain in place. Native API: [file and segment scheduling](https://developer.apple.com/documentation/avfaudio/avaudioplayernode).

The priming request is bounded; it is not a hard cap on AVFoundation's internal buffering. The existing conservative `canBuffer` admission estimate, actual-file checks and 128 MiB input limit remain. Longer/unprobed sources keep the existing AVQueuePlayer route and its DSP limitations. Remote engine input still finishes its bounded download before scheduling, so this removes PCM preparation latency without removing full-download latency. Progressive remote DSP remains a separate playback architecture task.

Remote input is now retained as a private encoded temporary file while its prepared slot is alive, instead of being deleted immediately after a whole-track conversion. It remains mode 0600 with the existing iOS data protection. Retirement, completion, Stop, owner teardown, authorization failure and canceled preparation release it; original/offline local files are preserved. At most the current and scheduled/retiring inputs are retained. A suspended lookahead captures scheduling scalars, so a source ignoring cancellation cannot pin the stopped occurrence's file. The disk tradeoff is up to two bounded input files for the active session; this is not a shared/durable cache. See ADR 0026.

#### Measured results

Medians of five fresh processes per revision/scenario (60 processes), comparing `ac5f5ee` with the file-backed implementation on an Apple M4 Max, macOS 27.0.1. Both binaries use Swift 6 optimized compilation and the same harness. Synthetic stereo FLAC fixtures were generated outside the measured process with FFmpeg 8.0.1. Revision order alternates, filesystem caches are not flushed, and the shared performance lock serializes cooperating native/performance jobs. Other host work, including the backend gate during this final cohort, can still affect wall time.

| Workload | Transport start → rendered confirmation, ms | Peak process physical footprint, MiB | Process CPU time for workload, ms |
| --- | --- | --- | --- |
| Local 15 s, two-track queue | 141.4 → 130.9 | 24.03 → 8.83 | 57.8 → 45.7 |
| Local 120 s, two-track queue | 195.4 → 133.7 | 139.53 → 8.84 | 173.1 → 45.9 |
| Local 120 s, seek to 117 s; active overlap | 197.2 → 135.3 | 96.56 → 8.75 | 174.7 → 47.4 |
| Local 120 s, seek to 60 s; one track | 159.1 → 133.8 | 94.61 → 8.05 | 108.0 → 45.0 |
| Local 60 s at 96 kHz; two tracks | 199.8 → 127.5 | 95.84 → 9.16 | 182.6 → 48.6 |
| HTTP 120 s, two tracks; 100 ms response delay | 309.5 → 242.3 | 142.42 → 12.49 | 181.8 → 56.7 |

For the local 120-second queue this is **31.6% lower startup confirmation latency**, **93.7% lower peak process footprint**, and **73.5% less process CPU time** in this workload. The HTTP case improves startup by **21.7%** and peak footprint by **91.2%** while transferring the same complete inputs. The active-overlap sample verifies that the public cursor has advanced to the successor and remains inside its 1.5-second overlap; it does not measure only a distant, silent successor.

Normalization and a three-second crossfade are configured, album gain is −3 dB, and output is muted. Each process constructs the same two-node engine, starts playback, confirms rendered output, then continues for two seconds to include lookahead/overlap. Startup timing starts at `transport.start()`; footprint and CPU include engine construction. Confirmation polls every 50 ms and the first scheduled start uses the existing 80 ms lead, so small latency differences are quantized/noisy. CPU is process user + system time for construction/startup and the two-second post-confirmation window; it excludes the HTTP server and system audio daemon. Peak/active footprint comes from `proc_pid_rusage(RUSAGE_INFO_V4)` (`ri_lifetime_max_phys_footprint` / `ri_phys_footprint`) in bytes, converted here using 1 MiB = 1,048,576 bytes. Fixture creation is excluded, avoiding a false memory peak from generating test PCM in the player process.

These are host component-process results, not a 94% reduction in the entire SwiftUI app's memory or a device battery measurement. The native scheduler still decodes samples as needed. Physical-device CPU/energy, route changes, interruption recovery and output fidelity need their device pass. An additional native offline-render probe verified equal left/right energy for 44.1 kHz mono AAC → 48 kHz stereo at a nonzero seek offset with +6 dB manual gain; this supports the framework adaptation path, separately from the transport workflow regressions.

#### Reproduction

From the repository root; only original generated tones are used. The Python driver starts an ephemeral loopback-only HTTP fixture with a fixed 100 ms response delay and emits individual JSON rows plus `SUMMARY` medians. Each binary invocation is a fresh process. The overlap sample fails if it misses the active fade.

```sh
music_bench_root=$(mktemp -d /tmp/lumina-music-bench.XXXXXX)
mkdir -p "$music_bench_root/fixtures" "$music_bench_root/before"
mise exec -- ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=15' -ac 2 -c:a flac "$music_bench_root/fixtures/tone-15.flac"
mise exec -- ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=120' -ac 2 -c:a flac "$music_bench_root/fixtures/tone-120.flac"
mise exec -- ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'sine=frequency=440:sample_rate=96000:duration=60' -ac 2 -c:a flac "$music_bench_root/fixtures/tone-96k.flac"
git archive ac5f5ee apple/Core/Sources | tar -x -C "$music_bench_root/before"
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 swiftc -swift-version 6 -O -parse-as-library "$music_bench_root"/before/apple/Core/Sources/LuminaCore/*.swift apple/scripts/benchmark_music_startup.swift -o "$music_bench_root/before-player"
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 swiftc -swift-version 6 -O -parse-as-library apple/Core/Sources/LuminaCore/*.swift apple/scripts/benchmark_music_startup.swift -o "$music_bench_root/after-player"
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 python3 -u apple/scripts/benchmark_music_startup.py "$music_bench_root/before-player" "$music_bench_root/after-player" "$music_bench_root/fixtures" > "$music_bench_root/results.log"
```

The final native script passed **48 Core tests with no skips**, including three live-server cases, plus iOS/tvOS Simulator builds. Retained/extended transport contracts cover mixed-rate mono AAC/stereo 96 kHz WAV playback, crossfade and seek; pause/resume without a new input; private mode-0600 input reuse and Stop cleanup; bounded current/lookahead files; retirement/completion cleanup; authorization cleanup; cancellation during download; and Stop while a source ignores cancellation. Existing gain, invalid-stream handoff, duplicate-occurrence and pending-Wait contracts remain. Required coverage completed with passing unchanged-source rechecks; this was **not one green `make check` invocation**. `mise exec -- make check` passed bootstrap, frontend typecheck/build, 5,239 backend tests (11 skipped), and 2,622 frontend tests across 299 files. Real-stack checks finished with 49 passed, 5 skipped and three scroll-budget failures: movie-wall fling median 7 long tasks, Home scroll median 4, and Live fling median 5, each against a limit of 2. A fresh unchanged `mise exec -- make e2e-realstack e2e-mocked` invocation stopped at real-stack with 50 passed, 5 skipped and two failures: cold movie-wall sharp-image median 3,256 ms against 2,500 ms, and movie-wall fling median 5 long tasks against 2. Home and Live passed the second run. The web/backend sources, harness and budgets are unchanged from `ac5f5ee`; concurrent host workloads were observed, but contention has not been established as the cause. No budgets were relaxed. The separately run `mise exec -- make e2e-mocked` finished with 656 passed, 2 skipped and one phone-dark Requests evidence screenshot timeout (15 seconds). With the broad suites finished, a focused real-stack recheck passed setup and both remaining movie-wall cases (3/3): sharp-image median 678 ms against 2,500 ms; fling long-task samples 0, 2, 0 against a median limit of 2, at 60 fps. The unchanged phone-dark Requests evidence recheck then passed (1/1, 12.5-second test). Every failed case has a subsequent pass, with no source, timeout, budget or assertion changes. The variability remains a test-environment/performance investigation item; these rechecks do not establish its cause.

Local evidence: `/tmp/lumina-file-scheduling-final-measurements.log`, `/tmp/lumina-file-scheduling-native-check.log`, `/tmp/lumina-file-scheduling-transport.log`, `/tmp/lumina-file-scheduling-channels.log`, `/tmp/lumina-file-scheduling-isolated-make-check.log`, `/tmp/lumina-file-scheduling-browser-recheck.log`, `/tmp/lumina-file-scheduling-mocked-check.log`, `/tmp/lumina-file-scheduling-perf-focused-recheck.log`, and `/tmp/lumina-file-scheduling-requests-focused-recheck.log`. The durable artifacts are this table, the two benchmark scripts and the retained public-boundary regressions.

### 2a. Remote music startup — progressive source playback

Qualified, admitted queues with crossfade off and unity effective gains now use native AVQueuePlayer source playback for remote files. Queue eligibility is cached alongside gain policy; a queue requiring processing or an unqualified source format keeps engine preloading from the first occurrence. Local/offline successors can join a source-playback queue without a download. Enabling processing switches the current occurrence to the engine at its saved position without another track-start event. **Normalization that changes gain and crossfade still require complete bounded engine input in this slice.** Unknown/large inputs retain the prior streaming fallback.

`ProgressiveMusicAsset` forwards AVFoundation's byte-range requests incrementally through an ephemeral, cookie/credential/cache-free URLSession. Same-origin redirects are allowed; cross-origin redirects, invalid lengths and inputs over 128 MiB are rejected. There is no app-owned whole-input allocation or persistent progressive cache. The common complete network chunk is passed through without another Data copy. Native decoding/read-ahead stays in AVFoundation, with a two-second forward-buffer preference (not a hard memory bound). The engine and EQ units are created only when processing is needed.

Stop, retirement, queue invalidation, owner release and authorization loss cancel owned transfers. Authorization callbacks use active asset identity so they still apply after pause/resume and cannot act on a retired owner. Failed lookahead is dropped until the real boundary, including Repeat One's separate occurrence of the same index; it neither stops current audio nor retries on the 100 ms clock. Canceled lookahead checks its generation before resolving a URL. Progressive seek and pause/resume reuse the current asset.

#### Measured results

| Source | Startup confirmation, ms | Bytes sent at startup, before → after | Peak process footprint, MiB | Process CPU, ms |
| --- | --- | --- | --- | --- |
| WAV (PCM16) | 8242 → 1052 | 100% → 7.5% | 8.77 → 27.95 | 203.1 → 168.3 |
| FLAC | 8224 → 689 | 100% → 4.9% | 9.19 → 14.69 | 90.6 → 100.8 |
| MP3 | 8211 → 587 | 100% → 4.5% | 8.97 → 13.36 | 81.2 → 86.6 |
| AAC / M4A | 8235 → 3924 | 100% → 43.0% | 8.99 → 15.06 | 76.1 → 129.4 |
| ALAC / M4A | 8244 → 2048 | 100% → 20.5% | 9.11 → 16.02 | 93.7 → 122.7 |

MP3 startup confirmation is **92.9% lower** in this paced workload, beginning after **4.5%** of input bytes have been sent. Across the five formats, startup improves by roughly **52–93%**. Host: Apple M4 Max, macOS 27.0.1, FFmpeg 8.0.1.

The comparison uses five fresh native processes per revision/format (50 total), alternating revision order, against `595d9a3` (the previous file-scheduling implementation). Both binaries use the same Swift 6 optimized harness, a single 120-second stereo 48 kHz synthetic file, crossfade off, normalization off, and muted output. Each format's full-body delivery is paced to eight seconds with 100 ms per-response delay; simultaneous range requests share a per-run bandwidth budget. Thus before/after throughput is equal within a format, while byte rates differ between formats. All inputs are generated ahead of time with deterministic pink noise; M4A files use FFmpeg's ordinary trailing-metadata layout. OS caches are not flushed, and unrelated host load can still affect timing despite the shared performance lock.

Startup spans `transport.start()` through native confirmation, polled every 50 ms. Before uses engine render-time confirmation; after uses AVPlayer item-ready/playing confirmation. This excludes manifest/API lookup and transport construction latency and is not acoustic-output or physical-device timing. The server snapshots body bytes sent when the harness emits its confirmation marker; this can slightly overestimate client-consumed bytes. Every revised sample must confirm before the whole input is sent, and must subsequently seek to the midpoint, advance beyond it and pause/resume without another source resolution. These extra qualification operations occur after the measured window.

Peak process physical footprint and user + system CPU include construction, startup/transfer and two seconds after confirmation. They exclude fixture-server/system-audio-helper work and later seek/resume qualification. **The progressive player has higher host component memory and, for some codecs, CPU cost than the file-backed engine.** This is a startup-latency improvement with a runtime tradeoff, not another general memory reduction. These are single-process measurements through a short playback window, not whole-app percentages, a long-run memory plateau or device energy results.

#### Reproduction and focused validation

From the repository root:

```sh
remote_bench_root="$(mktemp -d)"
mkdir -p "$remote_bench_root/before" "$remote_bench_root/fixtures"
git archive 595d9a3 apple/Core/Sources | tar -x -C "$remote_bench_root/before"
mise exec -- swiftc -swift-version 6 -O -parse-as-library "$remote_bench_root"/before/apple/Core/Sources/LuminaCore/*.swift apple/scripts/benchmark_music_startup.swift -o "$remote_bench_root/before-player"
mise exec -- swiftc -swift-version 6 -O -parse-as-library apple/Core/Sources/LuminaCore/*.swift apple/scripts/benchmark_music_startup.swift -o "$remote_bench_root/after-player"
ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'anoisesrc=color=pink:amplitude=0.02:sample_rate=48000:duration=120:seed=123' -ac 2 -c:a pcm_s16le "$remote_bench_root/fixtures/noise-120.wav"
ffmpeg -hide_banner -loglevel error -y -i "$remote_bench_root/fixtures/noise-120.wav" -c:a flac "$remote_bench_root/fixtures/noise-120.flac"
ffmpeg -hide_banner -loglevel error -y -i "$remote_bench_root/fixtures/noise-120.wav" -c:a libmp3lame -b:a 192k "$remote_bench_root/fixtures/noise-120.mp3"
ffmpeg -hide_banner -loglevel error -y -i "$remote_bench_root/fixtures/noise-120.wav" -c:a aac -b:a 192k "$remote_bench_root/fixtures/noise-120.aac.m4a"
ffmpeg -hide_banner -loglevel error -y -i "$remote_bench_root/fixtures/noise-120.wav" -c:a alac "$remote_bench_root/fixtures/noise-120.alac.m4a"
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 python3 apple/scripts/benchmark_music_remote.py "$remote_bench_root/before-player" "$remote_bench_root/after-player" "$remote_bench_root/fixtures" --runs 5
```

The final focused gate passed **27 tests with no skips** (26 transport workflows and the new authenticated real-server progressive audio workflow), plus **iOS and tvOS Simulator builds**. Extended coverage includes startup before whole delivery, same-asset pause/seek, Stop and owner-release cancellation, same-origin redirect/Range-ignoring delivery, cookie isolation, 401/403 and cross-origin/oversized rejection, post-pause authorization loss, duplicate/mixed offline queues, Repeat One failed-lookahead isolation, processing-queue preloading, and active/paused processing upgrades without duplicate track events. The full repository suite is intentionally deferred until the release gate at the user's request. No backend, schema, server encoding or runtime dependency changes.

Local evidence: `/tmp/lumina-progressive-focused-final.log`, `/tmp/lumina-progressive-final-measurements.log`, `/tmp/lumina-progressive-queue-before-fix.log`, `/tmp/lumina-progressive-queue-after-fix.log`, and `/tmp/lumina-progressive-paused-upgrade-before.log`. The last focused run includes the final paused-processing event guard; the gain-free source-playback path measured in the 50-process cohort is unchanged by that guard. Reproduce focused coverage with `swift test --package-path apple/Core --filter 'NativeMusicTransportTests|NativeServerTests/testNativeProgressiveAudioPlaybackAgainstFixture'` under `mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800`, setting `LUMINA_NATIVE_FIXTURE` to the ignored, currently running fixture file. Then build both Simulator schemes using the existing `apple/scripts/check.sh` build commands; do not print or commit the fixture credentials.

Native API references: [resource loading delegate](https://developer.apple.com/documentation/avfoundation/avassetresourceloaderdelegate), [incremental data delivery](https://developer.apple.com/documentation/avfoundation/avassetresourceloadingdatarequest/respond(with:)), [resource length/type/range information](https://developer.apple.com/documentation/avfoundation/avassetresourceloadingcontentinformationrequest). Physical-device interruption, route, background-network and sustained-memory qualification remains separate.

### 2b. Paused progressive playback — suspend unused read-ahead

At `85a22f9` the native player paused audio, but its private resource-loader HTTP tasks kept receiving the remaining input. The regression reproduced another **1,572,864 bytes delivered in 500 ms while paused**. A process memory capture identified several MiB of CoreMedia HTTP cache during WAV startup. Smaller forward-buffer preferences and media-type lookup changes did not materially reduce that footprint; changing the pitch algorithm increased it. Those probes were discarded.

Pause now suspends the current and lookahead assets' existing URLSession tasks, and requests created while paused stay suspended. Resume reactivates them without another source resolution or whole-track restart. A paused seek temporarily permits reads on the current asset; overlapping seeks must all finish before suspension resumes. Cancellation retains direct task references instead of asynchronously enumerating every session task. Stop, replacement and owner release still cancel suspended transfers. Apple's [task suspension contract](https://developer.apple.com/documentation/foundation/urlsessiontask/suspend()) also excludes suspended tasks from timeouts.

#### Measured results

| Format | Peak process MiB, before → after | Process CPU ms, before → after | Delivered MiB, before → after | Startup ms, before → after |
| --- | ---: | ---: | ---: | ---: |
| WAV | 42.39 → 22.49 | 358.5 → 108.0 | 21.99 → 2.35 | 1056 → 1054 |
| FLAC | 18.16 → 13.16 | 170.5 → 90.5 | 5.45 → 0.81 | 687 → 683 |
| MP3 | 15.05 → 12.84 | 127.4 → 86.9 | 2.75 → 0.77 | 591 → 583 |
| AAC/M4A | 15.91 → 14.39 | 148.8 → 120.6 | 2.80 → 1.85 | 3924 → 3939 |
| ALAC/M4A | 18.53 → 14.66 | 166.3 → 108.6 | 5.48 → 1.81 | 2039 → 2051 |

These measurements use the same host, synthetic 120-second/48 kHz stereo fixtures and paced HTTP server as §2a. Both revisions are progressive: baseline **`85a22f9`**, compared with this slice, compiled with the same current Swift 6 `-O` harness. Three alternating fresh-process pairs per format, 30 processes total; eight-second full-input delivery budget and 100 ms response latency. Each workload confirms playback, immediately pauses for ten seconds, then samples process footprint/CPU. CPU includes construction and startup plus the pause, and excludes the fixture server/system audio helpers. Delivered bytes are cumulative at the end-of-window marker, including startup, metadata probes and TCP data already in flight; marker delivery can slightly overestimate bytes before resume. They are not unique file-byte counts.

Every sample must start before whole-input delivery, retain its paused position, resume, seek past the midpoint, and pause/resume again with **one source resolution**. Those qualification actions occur after the measured window. The savings concern **paused read-ahead**, especially work wasted when the member subsequently stops or changes tracks. Active playback still needs native buffers and will transfer more data as listening continues; this is not a new active-playback memory cap or a physical-device energy measurement.

#### Reproduction and focused validation

Reuse the fixture-generation commands in §2a, replacing that section's archived baseline with `85a22f9`. Compile both source revisions with the current `benchmark_music_startup.swift`, then run:

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 python3 apple/scripts/benchmark_music_remote.py "$remote_bench_root/before-player" "$remote_bench_root/after-player" "$remote_bench_root/fixtures" --before-mode progressive --pause-seconds 10 --runs 3
```

The focused gate passed **28 tests without skips** (27 transport workflows plus authenticated real-server progressive playback), and both **iOS/tvOS Simulator builds**. The extended regression failed on the baseline and passed after suspension. Coverage includes paused current/lookahead transfers, same-source resume, paused/overlapping seek without resuming playback, Stop and owner release while suspended, processing upgrades, queue boundaries, redirects, authorization and size/privacy controls. Production source was unchanged after that gate. The full repository suite remains at the release gate, as requested. No deployment, backend, schema, dependency or server format-policy changes.

Local evidence: `/tmp/lumina-overhead-paused-red.log`, `/tmp/lumina-overhead-paused-green.log`, `/tmp/lumina-overhead-focused-final.log`, `/tmp/lumina-overhead-paused-recheck.log`, and `/tmp/lumina-overhead-final-measurements.log`.

### 2c. Active progressive playback — control read-ahead and use the native clock

At baseline `6179de9`, a paced sixty-second synthetic WAV was completely delivered after about seven seconds of active playback despite the two-second forward-buffer preference. The new active workflow failed on that baseline behavior. The resource loader now suspends HTTP transfers when the contiguous loaded range reaches eight seconds ahead, and resumes below four seconds. The same policy applies to lookahead at position zero. Waiting playback and app-initiated seeks permit reads; lookahead is released two seconds before the current item's actual native duration ends so AVFoundation can preroll the successor. This is a reserve policy in media time, not a hard byte or memory ceiling: native caching, TCP data in flight and transition preroll can exceed the target.

The initial flow-control implementation exposed a second problem while lookahead transfers were suspended: synchronous `AVQueuePlayer.currentTime()` blocked the main actor for **6.86 seconds** at a queue handoff. Position now comes from AVFoundation's periodic time observer; its callback keeps the same 100 ms progress/control cadence without that synchronous query. The app's source-playback fallback check runs once per second for failure/completion; decoded crossfade retains its 100 ms timer. The observer is removed on runtime replacement and owner destruction. A dedicated twelve-second-to-sixty-second queue test verifies that the limited successor remains prepared and advances at the boundary. The ordinary automatic stall-wait policy remains enabled.

#### Measured results

| Format | Peak process MiB, before → after | Process CPU ms, before → after | Delivered MiB, before → after | Startup ms, before → after |
| --- | ---: | ---: | ---: | ---: |
| WAV | 46.27 → 27.05 | 409.3 → 158.8 | 21.99 → 3.60 | 1050 → 1051 |
| FLAC | 18.44 → 15.94 | 215.0 → 138.8 | 5.45 → 1.53 | 683 → 690 |
| MP3 | 15.31 → 15.13 | 181.3 → 142.1 | 2.75 → 1.14 | 585 → 585 |
| AAC/M4A | 16.08 → 14.28 | 193.2 → 155.5 | 2.80 → 1.85 | 3922 → 3951 |
| ALAC/M4A | 18.80 → 14.52 | 226.8 → 151.9 | 5.48 → 1.81 | 2036 → 2042 |

WAV peak footprint fell 42% and CPU 61%; FLAC, AAC and ALAC also reduced both. MP3 CPU fell 22%, while its 0.19 MiB peak-memory difference is essentially unchanged at this sample size. All startup medians remained comparable.

Baseline **`6179de9`** and the revised sources are compiled with the same current Swift 6 `-O` harness. The method, host and five synthetic 120-second/48 kHz stereo files match §2a; the server budgets eight seconds for full-input delivery with 100 ms response latency. Three alternating fresh-process pairs per format, 30 processes. The window includes transport construction, startup and **ten seconds of active playback, with no pause**. Every sample must advance the playhead by at least 9.5 seconds during that ten-second window. After measurement it must seek past the midpoint and pause/resume with one source resolution. CPU excludes the fixture server and system audio helpers; bytes are cumulative server body bytes at the end-of-window marker, not unique input bytes.

The improvement avoids premature transfer/copy/cache work during active playback. Reading the rest of a track is deferred until it is needed, and may be avoided entirely if playback stops or selects another track. These short-window host measurements do not establish whole-track CPU/energy savings, an indefinite memory plateau, or physical iPhone/iPad/tvOS results. Startup keeps the existing native buffer preference and automatic waiting policy.

#### Reproduction and focused validation

Reuse §2a's fixture generation, archive baseline `6179de9`, and compile both source revisions with the current Swift harness. Then run:

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 python3 apple/scripts/benchmark_music_remote.py "$remote_bench_root/before-player" "$remote_bench_root/after-player" "$remote_bench_root/fixtures" --before-mode progressive --play-seconds 10 --runs 3
```

The final gate passed **30 tests without skips** (29 native transport workflows plus authenticated real-server progressive playback), and both **iOS/tvOS Simulator builds**. The active read-ahead contract failed on the merged baseline and passes with the final implementation; it verifies consumption-driven refill through eighteen seconds without a reported interruption. The queued-track contract checks that a limited sixty-second successor advances after the twelve-second outgoing item. Existing gain/crossfade, pause/seek, authorization, redirect, privacy, bounded input and teardown workflows remain passing. Production source is the same as the measured and platform-built source. The full repository suite remains deferred to the release gate at the user's request. No backend/schema, codec-policy, dependency or deployment changes.

Local evidence: `/tmp/lumina-active-red.log`, `/tmp/lumina-active-green.log`, `/tmp/lumina-active-transition-timing.log`, `/tmp/lumina-active-transition-preroll.log`, `/tmp/lumina-active-focused-final.log`, and `/tmp/lumina-active-final-measurements.log`. APIs: [loaded time ranges](https://developer.apple.com/documentation/avfoundation/avplayeritem/loadedtimeranges), [native periodic clock](https://developer.apple.com/documentation/avfoundation/avplayer/addperiodictimeobserver(forinterval:queue:using:)), [forward-buffer preference](https://developer.apple.com/documentation/avfoundation/avplayeritem/preferredforwardbufferduration).

### 2d. Video: retain FLAC instead of unnecessarily encoding AAC

The native request now advertises FLAC alongside its existing video audio codecs, with a codec-specific `AudioBitDepth <= 24` condition. The server honors that condition before choosing either direct delivery or an HLS stream copy. Missing depth and 32-bit FLAC retain audio-only conversion; compatible video is still copied. The existing eight-channel ceiling remains. The conservative 32-bit fallback is intentional: an actual synthetic 32-bit FLAC MP4 failed AVAssetReader decoding with `AVFoundationErrorDomain -11855` / `!dat`, whereas the qualified 16/24-bit files decoded. Request serialization and server decision regressions cover this distinction; no new runtime dependency or player engine was added.

The previous client profile was the cause of avoidable audio encoding. The feedback loop first failed both direct-MP4 and remux-MKV expectations, then failed the 32-bit/unknown-depth safety cases before the constraint implementation. The native qualification requires actual pixel buffers, an enabled audio track, advancing time and an accurate seek to six seconds followed by further playback. Direct-file counterparts also decode audio to nonempty PCM with AVAssetReader. HLS compressed audio/channel counts are checked with ffprobe. These checks do not establish acoustic fidelity, speaker mapping, HDMI or Atmos output.

#### Measured results

Three fresh processes per route/revision, alternating revision order, on the same M4 Max/macOS 27.0.1 host, Swift 6 optimized build and FFmpeg 8.0.1. Synthetic clips are 12 seconds, 1280×720 at 24 fps, with 24-bit FLAC: H.264 8-bit at stereo/5.1 48 kHz, and HEVC 10-bit with 7.1 96 kHz. The baseline removes FLAC from the native capability contract; both versions use the actual probed facts, production decision and production FFmpeg command builder. Thus these are route comparisons, not binaries with different AVPlayer implementations. The old default audio fallback is stereo AAC; copied FLAC preserves the original channels. CPU amounts below cover preparing the complete short clip; they are not percentages of one CPU core or whole-film forecasts.

| Source | FFmpeg CPU ms, before → after | FFmpeg child peak RSS MiB, before → after | Client first frame ms, before → after |
| --- | ---: | ---: | ---: |
| H.264 + stereo FLAC, MP4 | 129.6 → 0 | 33.97 → 0 | 167 → 195 |
| H.264 + stereo FLAC, MKV | 133.2 → 33.2 (−75%) | 34.25 → 37.48 | 171 → 162 |
| H.264 + 5.1 FLAC, MP4 | 241.2 → 0 | 46.48 → 0 | 162 → 192 |
| H.264 + 5.1 FLAC, MKV | 246.0 → 34.6 (−86%) | 45.64 → 38.64 | 168 → 168 |
| HEVC + 7.1 FLAC, MP4 | 469.7 → 0 | 37.72 → 0 | 166 → 199 |
| HEVC + 7.1 FLAC, MKV | 466.0 → 26.4 (−94%) | 37.47 → 25.97 | 161 → 171 |

Zero means no FFmpeg child is needed for direct original delivery, not zero server HTTP or device decode cost. Stereo MKV RSS rose slightly despite lower CPU; memory does not uniformly improve.

Client timing uses a loopback range server and **already prepared** HLS, excluding server packaging, API negotiation and network latency. It cannot establish an end-to-end startup improvement. Seek plus 200 ms of advancing playback remained 320–336 ms across these host route medians. The BGRA pixel-output/polling measurement harness adds rendering work and is not the app UI's own CPU profile.

| Source | Client CPU ms, startup plus one playback second, before → after | Client process peak RSS MiB, before → after |
| --- | ---: | ---: |
| H.264 stereo, MP4 | 86 → 277 | 56.6 → 85.2 |
| H.264 stereo, MKV/HLS | 87 → 86 | 54.8 → 56.4 |
| H.264 5.1, MP4 | 88 → 333 | 57.3 → 93.5 |
| H.264 5.1, MKV/HLS | 89 → 95 | 58.6 → 62.3 |
| HEVC 7.1, MP4 | 88 → 378 | 49.5 → 98.9 |
| HEVC 7.1, MKV/HLS | 86 → 110 | 49.2 → 61.8 |

Preserving lossless audio and additional channels carries a client cost, especially the host's direct-file path. This slice removes unnecessary server encoding and fidelity loss; it does **not** establish lower combined client/server CPU or memory. Treat the direct-file CPU/RSS difference as the next profiling target. Device energy, thermals, sustained large files, older OS runtimes and actual receiver output remain unqualified.

#### Reproduction and focused validation

The output directory must be new. The Python runner generates only synthetic media, serves loopback byte ranges, validates real encoded/remuxed stream facts, and emits per-run JSON plus medians. Its baseline is the previous native audio contract with FLAC omitted. It does not compare Infuse's runtime performance.

```sh
mise exec -- swiftc -swift-version 6 -O -parse-as-library \
  apple/scripts/benchmark_video_playback.swift -o /tmp/lumina-video-playback
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  backend/.venv/bin/python apple/scripts/benchmark_video_formats.py \
  /tmp/lumina-video-playback /tmp/lumina-video-formats-new --runs 3
```

For simulator qualification, compile the Swift harness with `xcrun --sdk iphonesimulator swiftc -target arm64-apple-ios18.0-simulator` (or `appletvsimulator` / `arm64-apple-tvos18.0-simulator`) and the same Swift flags. Pass the resulting executable and `--simulator <booted-UDID> --runs 1` to the Python runner, using another new output directory. Simulator execution qualifies decoder/seek behavior; its resource numbers are not physical-device estimates.

Host evidence: `/tmp/lumina-video-flac-host-final.log` (36 remote playback runs, plus three direct-file audio qualifications). Both iOS 26.5 and tvOS 27.0 simulators passed all six HTTP routes with frames, enabled audio, seek and subsequent advancement (one before/after process per case, plus local audio decode). iPhone Duo on iOS 27.1 and iPad on iPadOS 27.0 additionally passed all three direct-file decode/frame/seek smoke cases. These command-line simulator probes exercise AVFoundation, not the app's entire UI or hardware output. The Duo continuity layout is unchanged.

Focused validation passed: 152 backend cases across playback decisions, Jellyfin integration, seekable HLS and playback startup; three native client contract cases (profile serialization, stale-detail refresh, cross-origin refusal); one real fixture-server direct/HLS playback/progress integration; and both generic iOS/tvOS Simulator app builds. No simulator app was replaced. Evidence: `/tmp/lumina-video-flac-ios-final.log`, `/tmp/lumina-video-flac-tvos-final.log`, `/tmp/lumina-video-device-smoke.log`, `/tmp/lumina-video-focused-backend.log`, `/tmp/lumina-video-focused-swift.log`, `/tmp/lumina-video-unsafe-url.log`, `/tmp/lumina-video-live-server.log`, `/tmp/lumina-video-build-iOS.log`, `/tmp/lumina-video-build-tvOS.log`. The full suite remains reserved for release, as requested.

### 2e. Video: original MKV delivery with on-device stream copy

The native client now advertises MKV only when it can feed the original into the new `NativeVideoSource` adapter. Exactly pinned PrismCore 3.8.1 plus Lumina's final-segment statistics and separate-track Dolby Vision packaging patches demuxes and packages compatible streams on demand; the existing AVPlayer/AVKit UI still decodes, renders, seeks and reports progress. The public fork source and the shipping immutable revision are recorded in the bundled notices and Swift Package Manager lockfiles. This is **direct original delivery with local remuxing**, not native AVFoundation Matroska demuxing and not zero client work. No server remux process is started for qualified MKV. The initial native-only reproduction failed with AVFoundation `-11828`, unsupported format.

The adapter uses loopback-only delivery, coordinated range reads, one selected stream-copyable audio track (which can be a separate HLS rendition), a 64 MiB segment retention budget and a four-second forward-buffer preference. Stop/cancellation/replay invalidate session ownership. The workflow regression requires real frames, enabled audio, advancing playback, seek, listener shutdown, canceled startup and a fresh replay URL. The real backend fixture test additionally negotiates MKV direct delivery and saves playback progress, then checks the explicit forced-HLS fallback. See [ADR 0031](../../docs/adr/0031-client-side-matroska-packaging.md).

#### Measured results

Three fresh processes per route/case, alternating order on the same M4 Max/macOS 27.0.1 host with optimized Swift 6 compilation. Three synthetic **60-second** 720p/24 fps fixtures use H.264+24-bit FLAC stereo/5.1 at 48 kHz and HEVC 10-bit+24-bit FLAC 7.1 at 96 kHz. Both paths use the current client binary, probed facts and production decision/FFmpeg builder; the baseline removes MKV from direct profiles. Its HLS is prepared before client timing, while the new first-frame/CPU window includes on-device preparation. First-frame timing therefore does not compare complete server startup/API/network latency. CPU is process CPU time, not a percentage of one core; client CPU covers preparation through one playback second, server CPU covers packaging the entire short fixture.

| MKV source | Server FFmpeg CPU ms, before → after | Server child peak RSS MiB, before → after | Client CPU ms, before → after | Client peak RSS MiB, before → after |
| --- | ---: | ---: | ---: | ---: |
| H.264 stereo FLAC | 55.7 → 0 | 77.97 → 0 | 142.1 → 184.5 | 91.56 → 86.72 |
| H.264 5.1 FLAC | 61.0 → 0 | 90.05 → 0 | 155.9 → 190.6 | 105.39 → 101.89 |
| HEVC 7.1 FLAC | 45.8 → 0 | 28.00 → 0 | 161.2 → 196.2 | 94.09 → 84.38 |

All three avoid the server FFmpeg job. Client peak RSS falls about 3–10%; client CPU rises about 22–30% as packaging moves onto the device. Child and client peaks are separate process high-water marks and must not be summed into a simultaneous-memory claim. The initial multi-rendition local prototype consumed substantially more CPU/RSS; using a single muxed stream avoids duplicating demux/packaging work for this first default-track slice.

Prepared-HLS versus original-MKV first-frame medians were 203→214, 200→224 and 193→217 ms. Seek plus 200 ms of subsequent advancing playback was 336→338, 328→337 and 330→341 ms. This does not establish an end-to-end latency win or an Infuse performance comparison. The native pixel-output/polling harness adds work beyond the app UI. Framework/cache warm-up, filesystem caches and other host work remain limits of this small sample.

The optimized host harness grows from 105,424 bytes without LuminaCore/PrismCore to 38,885,552 bytes with the complete Core/media dependency graph. This is a standalone binary comparison, not an IPA/download-size delta; shipping app thinning/stripping differs. The dependency is explicit and its notices are bundled and accessible from Account. The library's native archives support arm64 tvOS Simulator; the project explicitly excludes unsupported x86_64 tvOS Simulator builds.

#### Reproduction

```sh
mise exec -- swift build --package-path apple/Benchmarks \
  --scratch-path /tmp/lumina-mkv-benchmark -c release
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  backend/.venv/bin/python apple/scripts/benchmark_video_formats.py \
  /tmp/lumina-mkv-benchmark/release/VideoBenchmark /tmp/lumina-mkv-new \
  --client-mkv --seconds 60 --runs 3
```

The output directory must be new. The runner's `--bytes-per-second 2097152 --runs 1` mode checks paced HTTP startup rather than claiming a slow-link speed from an unpaced loopback result. JSON records actual bytes sent at the first decoded frame and at the end of the playback/seek workload; transferred bytes may include repeated ranges. Simulator probes use the same package-built executable with `--simulator <UDID>`. The original standalone AVFoundation-only harness remains available for baseline format rejection. Raw host measurements: `/tmp/lumina-mkv-measured-muxed.log`.

At 2 MiB/s per-response pacing (one process per route/case), original-MKV first frames arrived in 2.26/4.14/2.21 seconds, with 7.8%/12.0%/8.5% of original bytes sent. Playback therefore did not require a complete download. Prepared-server-HLS first frames in the same paced cases were 1.97/2.05/0.94 seconds: the new path is slower in this sample. These are deliberately disclosed startup costs, not evidence of a latency win. Seeking plus 200 ms of playback took 1.88/2.38/1.95 seconds on the local path. Pacing is per response and does not model a shared Wi-Fi bandwidth budget or real network RTT. Evidence: `/tmp/lumina-mkv-paced.log`.

Focused checks passed: 153 backend playback/decision/Jellyfin/startup cases; five native request and decoded-MKV lifecycle cases; and the extended real-backend direct-MP4/direct-MKV/forced-HLS progress workflow. Both generic iOS and arm64 tvOS Simulator app builds passed. Original-MKV frame/audio-track/advance/seek probes passed on iPhone (iOS 26.5), Duo (iOS 27.1), iPad (27.0) and tvOS (27.0): three H.264/HEVC+FLAC cases and four additional H.264+5.1 AAC/AC-3/E-AC-3/ALAC cases per device. This is 28 simulator playback probes, not hardware output or full app UI qualification; installed apps were not replaced. ALAC cases emitted native mux timestamp warnings but passed these frame/track/time/seek checks; long-duration audio/video synchronization remains unqualified.

Evidence: `/tmp/lumina-mkv-focused-backend.log`, `/tmp/lumina-mkv-focused-core-final.log`, `/tmp/lumina-mkv-live-server.log`, `/tmp/lumina-mkv-simulators.log`, `/tmp/lumina-mkv-audio-check.log`, `/tmp/lumina-mkv-build-iOS.log`, `/tmp/lumina-mkv-build-tvOS.log`.

MKV+MP3 keeps the existing server remux. Unknown/32-bit FLAC, excessive channels, DTS/TrueHD and unsupported video retain server conversion as dictated by the existing constraints. This slice carries the default audio track, without claiming native image-subtitle rendering, arbitrary codecs, Dolby Vision/HDMI output, AirPlay/PiP or Infuse parity. Full-suite testing remains reserved for release.

### 3. Polling and background publication still have room to improve

Source: `Core/Sources/LuminaCore/NativeMusicTransport.swift:282`, `App/Shared/PlaybackScreen.swift:852`.

`NativeMusicTransport.startClock()` retains its ten-times-per-second decoded crossfade clock. Source playback now receives native clock callbacks and uses a once-per-second fallback check (§2c). Throttling notifications removes most downstream SwiftUI, checkpoint checks and Now Playing writes, but does not eliminate those timer wakeups. `PlaybackModel.receiveAudioState()` rebuilds system Now Playing metadata on each delivered state, and lyrics/queue presentation can depend on the same playback model.

Next change: measure whether source item/transport/end observations can replace the fallback check; use a coarser decoded clock outside a fade/boundary and the existing fine clock only where envelopes need it. Keep audio progress reporting and sleep deadlines correct in background. Measure callbacks, timer wakeups and SwiftUI body updates while the music screen is visible, while browsing another destination and while backgrounded. Avoid a global timer or broad state migration without a trace showing the benefit.

### 4. Artwork decode and resize churn — bounded in the Home follow-up

Source: `App/Shared/MediaPresentation.swift:7`, `:122` and `:321`.

The 2026-10-21 follow-up replaces full-resolution image construction with off-main ImageIO thumbnail preparation, keeping the 12 MiB encoded limit and capping the longest decoded edge at 1,920 pixels. It applies orientation and retains cancellation checks before publication. There is no new persistent or cross-member image cache. Carousel prefetch still warms URLSession bytes rather than a prepared image cache.

`MediaBillboard` and `FeaturedShelf` now publish only compact/wide geometry changes. The synthetic 4,000 × 3,000 JPEG comparison measured decoded pixel storage of 48.0 → 11.1 MB and preparation/draw medians of 38.323 → 27.138 ms. See the [Home/artwork report](home-artwork-performance-2026-10-21.md) for reproducible commands, input details and limitations.

Remaining qualification: trace real-device body invalidation, image requests, main-thread work and hitches during resize/scroll. The host microbenchmark does not establish those device results or total process memory.

#### Large-library artwork retention (2026-10-24)

Native artwork now uses one session-private cache across grid cards, album heroes,
playback artwork, track labels and cast portraits. The existing decoded tier targets
64 MiB / 80 images. A second compressed tier targets 32 MiB / 2,000 renditions so
returning to an evicted decoded image can prepare it again without another request.
Both tiers retain complete signed rendition URL keys, clear on member/session changes,
and remain in process memory; this adds no persistent disk cache. Image preparation
stays off the main actor and retains the 12 MiB encoded / 1,920-pixel input limits.
The shared transport rejects oversized declared responses before receiving the body
and cancels unknown-length responses as soon as streamed bytes exceed the limit.

Video and music grids nominate only the next 16 artwork renditions. One utility
worker shares a four-load speculative budget across successive windows; new windows
replace queued stale work. Visible and prefetched requests coalesce, and a remounted
view can read a decoded hit synchronously without flashing its placeholder.

`BillboardArtworkPreparationTests` reproduces the evicted-rendition re-fetch before
the change and retains compressed-budget, member clearing, signed-tag separation,
in-flight cancellation, lookahead readiness and overlapping-window limits.

The updated `scripts/benchmark_artwork_preparation.swift` compares compressed retention
with retention disabled, forcing the decoded tier to evict the same 400 × 600 JPEG.
Across five alternating runs with a synthetic 100 ms URLProtocol delay, median return
latency was **108.981 ms without compressed retention versus 0.657 ms with it**;
additional requests were **5 versus 0**. This isolates cache-return work on the host;
it does not establish device scrolling FPS, cold-image latency or behavior under real
memory pressure. Run from `apple/`:

```sh
mise exec -- swiftc -swift-version 6 -O Core/Sources/LuminaCore/BillboardArtworkPreparation.swift Core/Sources/LuminaCore/ArtworkImageCache.swift Core/Sources/LuminaCore/ArtworkDiskCache.swift scripts/benchmark_artwork_preparation.swift -o /tmp/lumina-artwork-benchmark
mise exec -- ../scripts/with-lock.sh /tmp/lumina-perf.lock 1800 /tmp/lumina-artwork-benchmark
mise exec -- swift test --package-path Core --filter BillboardArtworkPreparationTests
```

### 5. Home loading — concurrent in the Home follow-up

Source: `Core/Sources/LuminaCore/LuminaStore.swift:190` and `:195`.

The 2026-10-21 follow-up starts Recently Added, Resume and Next Up concurrently and publishes each shelf independently. Refresh overlaps Home with the current video catalog, preserving the catalog query and clearing the shared error once. Superseded loads, caller cancellation and sign-out cancel Home requests; generation and session checks also reject obsolete results.

The same asynchronous fixture with a 100 ms response delay per shelf measured original-store completion at 311 ms versus 105 ms concurrently (five-run medians), with first-response medians of 104 ms for both. See the [Home/artwork report](home-artwork-performance-2026-10-21.md) for source provenance and retained regression checks. This is API completion on a synthetic host fixture, not rendered Home or physical-network latency. Invisible-catalog suppression remains separate work because current refresh callers expect the catalog to stay current.

### 6. Queue presentation and persistence — bounded in the queue follow-up

The 2026-10-21 queue follow-up moves recurring checkpoint sanitization, JSON encoding and atomic file replacement to a private serial utility writer, retaining one active snapshot and the newest pending replacement. In-memory state remains immediate. Account changes drain accepted writes before reload/sign-out; backgrounding awaits a flush under a background execution task. The existing owner isolation, URL stripping and 8 MiB encoded ceiling remain. Account transitions can still wait for bounded disk work on the main actor.

Playback queue rows now rebuild on membership/order/metadata changes and retain their occurrence UUIDs. Rows carry their positions, removing per-row linear occurrence lookup. Cursor/repeat-only changes retain the cached rows. See the [queue measurement and regression report](music-queue-performance-2026-10-21.md) for methods, before/after results and limitations.

The later offline-library follow-up below already removed filesystem validation from repeated `downloadedItems` presentation reads. Initial/explicit offline reconciliation still performs filesystem work; physical-device UI traces remain separate qualification.

### 7. Format capability and playback cost reporting need to become more precise

Source: `Core/Sources/LuminaCore/LuminaClient.swift:116` and the shared `backend/app/services/playback_decision.py` / `jellyfin_playback.py` boundary.

`LuminaClient.playbackInfo()` shares one static profile across all supported Apple devices. It still lacks hardware/OS/output-specific qualification, detailed Dolby Vision profiles, chroma restrictions and runtime fallback from a failed direct asset to one compatible attempt. Device profile conditions that the backend does not model cannot fix those gaps simply by adding JSON fields. Audio-only `audioRequest()` uses a container allowlist, which likewise does not prove that every codec inside an allowed container is playable; test asset failure and one bounded compatibility retry, excluding authorization failures.

The UI labels every compatible HLS request `HLS` and marks it `isTranscoding`, including server remuxes. This prevents the user-facing label from proving that video was copied. Expose the existing server decision mode/reasons in playback diagnostics, along with first-frame time, stalls and AVPlayer access/error-log information. Keep stream URLs/tokens/private media names out of exported evidence.

## Playback matrix after this change

This table describes negotiation and server work, not blanket physical-device certification.

| Source | Intended route | Cost / qualification |
| --- | --- | --- |
| MP4/M4V/MOV, H.264 8-bit or HEVC up to 10-bit, AAC/AC-3/E-AC-3 | Direct file | Existing AVKit route; actual device, HDR and sound output still need qualification |
| MP4 H.264/HEVC with FLAC ≤24-bit, up to 8 channels | Direct file | Actual stereo/5.1/7.1 video/audio/frame/seek qualification; unknown/32-bit depth falls back to audio encode (§2d) |
| Same containers/video, MP3 or ALAC audio | Direct file | Newly advertised; synthetic H.264+MP3 MP4 and H.264+ALAC MOV advanced in macOS AVPlayer |
| MKV, H.264/HEVC + AAC/AC-3/E-AC-3/ALAC/FLAC ≤24-bit | Direct original, on-device remux | New PrismCore adapter; AVPlayer hardware decoding/controls retained; §2e |
| MKV + MP3 with supported video | HLS remux | Retains server copy rather than enabling a local audio bridge |
| MKV with supported video and DTS/TrueHD | HLS audio conversion | Copy video, encode compatible audio; no claim of lossless/Atmos preservation |
| H.264 High 10 / HEVC 12-bit | Compatible video conversion | Newly constrained to prevent false direct capability |
| Unsupported video codec or selected unsupported image subtitle | Compatible video conversion/burn-in | Genuine video encode; qualification and server hardware matter |
| MP3/AAC/M4A/FLAC/WAV/AIFF/CAF and other audio containers already on the native list | Direct audio URL | Short admitted sources may still be locally decoded/resampled; “direct” does not mean zero client DSP |
| Unsupported music container | Cached compatible FLAC endpoint | Existing server conversion path; not an HLS session |

FLAC up to 24-bit is now qualified in MP4 and stream-copied fMP4 HLS (§2d); unknown/32-bit depth keeps audio conversion. Next format candidates are PCM in MOV and hardware-gated AV1. Test each real container/codec/profile/channel combination before advertising it, including seek, resume, track selection and fallback. Adding `mkv` or `dts` to AVKit’s static profile would make a capability claim the app does not currently establish. Apple’s [HLS authoring specification](https://developer.apple.com/streaming/hls-authoring-specification-for-apple-devices.html) documents lossless audio carriage and its restrictions; [AVPlayer](https://developer.apple.com/documentation/avfoundation/avplayer) supports local/remote file media and HLS. Published carriage support does not replace output testing.

## Route to broader direct playback

Direct file playback, stream-copy remux and decoding are different costs. Direct file playback avoids server media processing, but the device still demuxes, decodes and renders. A server HLS remux changes packaging while preserving compressed video/audio; it is substantially different from running a video encoder. Playback diagnostics should show those modes separately before using a direct-play percentage as the success metric.

| Approach | Format reach | Performance implications |
| --- | --- | --- |
| Existing AVKit plus truthful capability profile and server stream-copy remux | Native file formats, plus containers the server can repackage into supported HLS | Retains the native decode/render path; uses server demux/mux and segment I/O where required |
| Add client container demux feeding native hardware decode | Could permit original MKV delivery for supported elementary streams | New seek/timing/subtitle/buffering work; requires a playback architecture decision and format/output qualification |
| Add a general media engine with hardware decode and software fallback | Potentially wider container/audio/video reach | Dependency/license/binary-size tradeoffs; fallback CPU, HDR, audio output and background behavior need per-device measurement |

Near-term recommendation: maximize direct files and stream copies through the existing engine, then qualify PCM and hardware-specific codecs. FLAC qualification and its bit-depth fallback are now covered in §2d. If original MKV delivery or unsupported lossless audio is a firm requirement, prototype a client demux/engine path against the same format corpus and compare server CPU, device energy, first-frame/seek latency and output fidelity. A codec-name allowlist alone cannot provide that support. No new media-engine dependency or architecture migration was introduced by this review.

## Video format target: Infuse

Infuse is a **feature target**, not a measured performance baseline in this review. Firecore documents broad container support including MKV, AVI, transport streams and disc structures; DTS-family/FLAC audio; and image/styled subtitle formats. Its [format list](https://support.firecore.com/hc/en-us/articles/360045051934-What-is-Infuse) is materially broader than our current AVKit route. Its [audio capabilities](https://support.firecore.com/hc/en-us/articles/217735707-Audio-Options-Capabilities) also distinguish lossless PCM decoding from immersive passthrough: TrueHD Atmos and DTS:X should not be assumed to retain their object metadata. Match output behavior, not codec names alone.

| Priority | Remaining gap | Next measurable acceptance criterion |
| --- | --- | --- |
| 1 | Initial MKV direct-delivery path implemented in §2e; larger/unindexed files and output fidelity remain | Qualify multi-hour/irregular-Cues sources, slow/disconnecting networks, alternate tracks, resume and physical HDR/audio output; retain zero server media jobs for supported originals |
| 2 | DTS/TrueHD currently require server AAC conversion | Qualified client decode retains channel/sample data on synthetic assets; zero server audio encoder; measure device decode cost and verify actual multichannel output on hardware |
| 3 | Image subtitles burn into video; ASS styling is not equivalent to original rendering | PGS/DVD/ASS samples render with correct timing/position and seek while video stays copied; measure overlay CPU and memory |
| 4 | AV1 and legacy video codec support are not established by the static native profile | Gate hardware capability by device/OS, qualify actual frames and seek; measure software fallback before enabling it |
| 5 | PCM container/sample-entry variants need precise qualification | Distinguish MOV LPCM from ISO MP4 PCM and older runtimes before advertising; prevent container aliases from creating false support |
| 6 | All HLS is still labeled as transcoding; actual first-frame/stall metrics are absent from app diagnostics | Distinguish direct/remux/audio/video conversion using server decisions, without logging source URLs or credentials; record decoder failures, first frame and stalls |

Keep compatible compressed video copied throughout these slices. HDR/Dolby Vision variants, subtitle selection, alternate audio, resume, and output fidelity must remain separate qualification dimensions. The current change closes one unnecessary-conversion gap; it does not reach Infuse's full format coverage or demonstrate faster playback than Infuse.

## Device measurement and acceptance matrix

These are proposed product budgets for the next hardware pass, not Apple requirements or achieved results. Use a Release build, fixed synthetic media/library/network conditions, repeat cold/warm runs, and retain median/p95 values and raw traces. Record device model, OS/build, output route, thermal state and display refresh rate. Compare the same physical device before/after; simulator CPU and the shared busy host are not battery or hardware-decoder evidence.

| Platform | Required journeys | Measurements / acceptance |
| --- | --- | --- |
| iPhone | Cold Home, 1,000-item scroll/search, direct/HLS start and seek, background music, ten pause/resume cycles | No repeated source download on ordinary pause/resume; no normalization queue scans in steady playback; p95 UI main-thread work below the display frame budget |
| iPhone Duo | Closed → Book → Open → Closed on Home, deep detail and active music/video; outer landscape; inner portrait; Split View on either side | Same continuing view on the right in requested full-screen opening; no new player/session, no lost navigation/selection/scroll; no duplicated asset fetch caused only by pose; record frame hitches during transition |
| iPad | Full width and narrow windows, live resize, destination switches during music, large queue/offline list | Preserve player identity; no layout-triggered media restart; bound image decode memory and main-thread checkpoint work; inspect exact-width invalidations |
| tvOS | Rapid focus movement, catalog scroll, direct/remux/audio-only-convert play, remote seek/pause, Match Content | Stable remote focus and native controls; record dropped frames/stalls and first-frame p50/p95; confirm HDR/refresh/audio on real HDMI equipment |

Suggested gates: no app-owned recurring playback clock after Stop or a terminal stream failure; zero failed-preload retry loop without a new user/queue action; no video encoder for a compatible video stream solely because its container/audio differs; sustained-memory plateau after warm-up, with no growth over 100 streaming queue advances; p95 frame work below 16.7 ms at 60 Hz or 8.3 ms at 120 Hz. These frame budgets are arithmetic targets, not universal device refresh-rate claims. Keep crossfade sound and progress/stop ordering correct while reducing work.

Instrumentation: physical-device SwiftUI/Time Profiler, Allocations, energy/CPU and system networking, plus AVPlayer access/error logs and backend decision/ffmpeg process data. Instruments discovery in this session exposed the host Mac and simulators, with no paired physical iPhone/iPad/Apple TV. No device CPU percentage, energy reduction, first-frame improvement or zero-stall guarantee is claimed. The live Duo simulator review has user acceptance; physical-device qualification remains open.

## Validation evidence

- Baseline Core: 35 discovered, 3 live-server cases skipped, 0 failures.
- Original transport with the new workload regressions: failed as expected (20 progress callbacks; 8 failed-successor requests).
- Initial review native script: 38 Core tests passed with no skips. The pause/resume follow-up script passed 44 with no skips. The file-scheduling follow-up script passed 48 with no skips, including all three real-server cases; iOS Simulator and tvOS Simulator builds both succeeded.
- Initial review repository `make check`: bootstrap, frontend typecheck/build and backend suite passed (5,233 passed, 11 skipped). The frontend unit phase stopped with one playback-metric label failure: `localPlayerStart.test.tsx` expected `direct` and received `direct_resume` for a start clamped to zero. A focused unchanged-source rerun passed 14/14. The unchanged full frontend rerun passed 2,622/2,622 tests across 299 files. The separately run real-stack journey/performance gate passed 52 cases with 5 skips and unchanged budgets. The mocked-browser gate passed 657 cases with 2 skips (23.4 minutes). This is not one green `make check` invocation.
- Pause/resume follow-up: one green full `mise exec -- make check` invocation, with 5,239 backend, 2,622 frontend, 52 real-stack and 657 mocked-browser cases passed (11, 0, 5 and 2 skips respectively). The final native script separately passed 44 Core tests with no skips and both simulator builds.
- Focused server playback decisions: 59 passed, including new native direct/remux/audio-only/bit-depth rows.
- Synthetic H.264+ALAC MOV advanced 0.981 seconds and H.264+MP3 MP4 advanced 1.066 seconds after approximately one second of macOS AVPlayer playback. This establishes synthetic decode on macOS only.

Local evidence logs: `/tmp/lumina-apple-performance-gain6.log`, `/tmp/lumina-apple-performance-before.log`, `/tmp/lumina-apple-performance-final-core.log`, `/tmp/lumina-apple-performance-final-native.log`, `/tmp/lumina-apple-performance-decisions.log`, and `/tmp/lumina-apple-performance-make-check.log`, and `/tmp/lumina-apple-performance-remaining-checks.log`. These are local, temporary artifacts; the durable result is this report plus the retained regression tests. No signed archive, upload or deployment was requested or performed.

## Video readiness latency, 2026-10-20

The native video screen previously checked `AVPlayerItem.status` every 100 ms before starting playback. It now awaits KVO readiness/failure directly, with a cancellable 30-second deadline. Replacing/removing the item ends the wait; an already-ready item completes immediately. Resume seek and playback reporting still happen after readiness. No decoding, buffer or format policy changed.

Three optimized macOS host runs per synthetic local 60-second source, using the same benchmark, frame-output settings and production preparation. `LUMINA_VIDEO_WAIT_MODE=poll` retains the former screen's readiness loop as a comparison; `event` calls the production readiness helper. First frame includes preparation, readiness and actual decoded-frame delivery.

| Source | Median first frame, polling → event | Change | Readiness wait, polling → event |
| --- | ---: | ---: | ---: |
| H.264 + stereo FLAC MP4 | 239.32 → 224.11 ms | −6.4% | 203.93 → 170.66 ms |
| H.264 + stereo FLAC MKV | 259.18 → 183.49 ms | −29.2% | 204.64 → 123.79 ms |
| HEVC + 7.1 FLAC MKV | 252.51 → 186.14 ms | −26.3% | 202.00 → 129.38 ms |

The old loop made three readiness checks in each run; the new path makes zero timer-driven readiness checks. This is a startup improvement, not evidence of sustained CPU or memory reduction. Startup-plus-one-second CPU medians were 191.31 → 188.42 ms, 132.68 → 129.58 ms and 134.98 → 143.28 ms respectively; peak RSS was effectively unchanged. Do not interpret those small/noisy changes as CPU wins.

`LUMINA_VIDEO_STEADY_SECONDS=12` additionally checks frame delivery and at least 90% playhead advancement, reports process CPU across that interval and a two-second pause, then verifies seeking. An initial H.264 MKV run used 330.19 ms CPU over 12.01 seconds (2.75% of one core), 288 sampled frames, and 8.61 ms CPU during the pause. The probe polls/copies BGRA frames; it is not the app's rendering/energy measurement and is not physical-device evidence. The current offline download product supports music only: local-file video probes do not establish a video-download workflow.

Focused validation: native MKV decoded-frame/audio-track/advance/seek/teardown/replay and readiness failure/cancellation checks; generic iOS and arm64 tvOS Simulator app builds. Full suite remains deferred to release per the requested workflow. Real-device video energy, large/unindexed sources, network stalls and saved-video product support remain open.

## Offline library presentation cost, 2026-10-20

`downloadedItems` previously walked the filesystem to validate every audio file on every read. The native Downloads screen reads this property several times during presentation. It now maps reconciled entry metadata without touching disk. Configure/refresh still validate all files; `localURL(for:)` and artwork lookup still validate the actual file before use. The Downloads screen refreshes on entry and foreground activation. A failed manifest/configuration clears presentation metadata rather than exposing entries that never completed reconciliation. A purge can leave a displayed row until refresh/open, but cannot authorize a missing or replaced file for playback.

An optimized macOS host benchmark creates 1,000 synthetic 32-byte files and a valid owner-scoped manifest, configures the real library, then takes 21 repeated list-read samples with an identical consumed checksum. Setup, initial reconciliation and post-measurement purge/owner checks are outside the timing window. This measures metadata access, not decoding or cold storage.

| Median per 1,000-entry list read | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Main-thread process CPU | 38.859 ms | 0.201 ms | 99.48% |
| Wall time | 38.919 ms | 0.201 ms | 99.48% |

No additional metadata cache or playback buffer is allocated; filesystem work moves out of repeated presentation reads, not out of validation. Initial/explicit reconciliation still scans storage and is not made asynchronous in this slice. These are existing **offline music** improvements; saved video downloads remain an unimplemented product path.

Reproduce: `mise exec -- swift run --package-path apple/Benchmarks -c release OfflineLibraryBenchmark 1000` (the same scratch/build configuration should be used across revisions). The benchmark verifies purge detection on playback lookup and owner hiding after measurement. Six focused offline boundary cases pass, including a retained purge workflow and new truncated/same-size-symlink replacement checks; the two focused video checks also pass. Both generic Apple client builds pass. Full-suite validation remains reserved for release.

## Sustained MKV subtitle-history retention, 2026-10-21

The MKV session inherited PrismCore's `.complete` host cue replay history. Lumina registers no host subtitle overlay: AVKit consumes packaged media, and subtitle WebVTT files are produced independently of this replay cache. Every encountered text cue and its deduplication key nevertheless stayed in memory for the session's lifetime. Configure the existing `.bounded(maxCues: 1, maxBytes: 1)` live-only policy. This removes lifetime host-history retention while keeping packaged subtitle production, video/audio stream copy and current buffering policies. The one-byte limit bounds this cache, not process RSS, decoder buffers or segment storage.

The focused decoded-MKV workflow was extended with actual embedded text captions. Before the configuration change, `swift test --package-path apple/Core --scratch-path /tmp/lumina-mkv-core --filter testOriginalMKVDecodesSeeksAndReleasesItsLoopbackSession` failed because 114 bytes remained in the unused host replay history. Afterward, retained history is zero, and a fetched loopback WebVTT segment still contains the opening caption. Audio track, decoded frames, advancement, seek, cancellation, listener teardown and replay checks pass.

Both comparison binaries use the same optimized host harness and new one-second RSS sampler. Inputs are original MKVs over range HTTP; there are no server FFmpeg jobs in either variant. Three pairs alternate order before/after. The stress fixture contains four text subtitle tracks with 24 synthetic cues/second/track, around 1 KiB/cue. This deliberately exercises dense caption retention; it is not representative of ordinary dialogue. The subtitle-free control is HEVC with 7.1 FLAC. Raw samples and medians are retained in `apple/Benchmarks/results/2026-10-21-mkv-subtitle-history.json`.

| Median | Caption stress: before → after | Subtitle-free control: before → after |
| --- | ---: | ---: |
| Sampled peak resident memory during playback (decimal MB) | 142.23 → 130.58 (−8.19%; 11.65 MB saved) | 103.12 → 101.70 (−1.38%) |
| Process CPU over steady window | 1,204.34 → 1,232.91 ms over 30 s (+2.37%) | 573.48 → 563.90 ms over 12 s (−1.67%) |
| First decoded frame | 242.93 → 240.65 ms | 208.23 → 206.04 ms |
| Seek plus 200 ms playback | 345.49 → 347.05 ms | 347.57 → 348.41 ms |

All twelve HTTP runs advanced through at least 90% of their steady window and delivered frames, then paused and sought successfully. Source byte counts match between variants in each case. CPU changes are small and mixed; **no sustained CPU improvement is established**. This slice lands the verified memory-retention benefit. The BGRA frame-copy probe adds overhead and is not AVKit UI rendering, hardware energy, or Apple TV thermal qualification. Other buffers still contribute to RSS growth; this does not establish a global memory plateau or a hard process budget.

Focused native tests and generic iOS/arm64 tvOS Simulator builds pass. The full suite remains a release gate, as requested. Future host-drawn subtitles must explicitly choose a replay window. Large/unindexed sources, producer read-ahead and device CPU/energy remain separate follow-up measurements.

## Sustained MKV CPU investigation and progress cadence, 2026-10-21

The video screen polled playback state every second, although it sends watch progress every five seconds and AVKit owns the visible video timeline. Video now uses a five-second media-time observer and reports each changed position tick. The fallback audio observer retains its one-second cadence. Pause/control-state, close/stop, completion, and resume-seek paths remain in place. Reporting each video tick avoids an extra five-second gap when a pause/seek or floating-point rounding resets the old delta threshold. Audio controls and listening progress are unchanged.

Three alternating before/after pairs use identical optimized host probes with a real periodic AVPlayer observer, an original 60-second HEVC/7.1 FLAC MKV over range HTTP, and a 30-second steady window. The observer uses the production interval; its body records media time rather than executing the full app reporting path. Each run verifies decoded frames, enabled audio, playhead advancement, pause, and seek. Raw samples are in `apple/Benchmarks/results/2026-10-21-mkv-progress-cadence.json`.

| Median over 30 seconds | Before | After |
| --- | ---: | ---: |
| App progress observer callbacks | 30 | 6 (80% fewer) |
| Whole-process CPU, including frame-copy probe | 1414.83 ms | 1438.04 ms |
| Sampled peak RSS, decimal MB | 121.39 | 121.42 |
| First decoded frame | 205.32 ms | 210.15 ms |

**This establishes fewer callbacks, not a reliable reduction in total playback CPU or memory.** All six runs delivered the original source without server FFmpeg work. The cadence applies to native video generally; it does not alter format support or decode quality.

An Instruments host Time Profiler capture (2–30 s, 1,370 ms sampled CPU weight) attributes substantial cost to native FLAC reconstruction, residual decoding, AudioToolbox conversion/mixing and memory copies. A separate steady sample found the packaging producer waiting for demand. The signed Release tvOS Simulator app also completed a 45-second playback probe and a 15-second process sample; the main thread was predominantly waiting, with native media/AVKit work visible. These are diagnostic profiles, not a physical-device energy result or proof that the full app shares the host probe's exact cost distribution.

Rejected probes: varispeed/spectral audio time-pitch settings, 10-second packaging segments, and a 256 MiB cache produced no convincing sustained CPU win. The larger cache returned the same source byte count and higher RSS. An isolated upstream prototype wrapping segment emission in an autorelease pool did not remove RSS growth. Reducing producer lead from 30 to 8 seconds reduced startup/source delivery work in one high-bitrate probe but increased measured steady CPU and did not reduce RSS; no fork or producer policy change ships. The harness adds `--video-bitrate` for reproducing H.264 cache/read-ahead stress and cumulative one-second CPU samples alongside RSS samples.

Reproduce the callback comparison with two optimized benchmark binaries built with one-second and five-second `NativeVideoSource.progressObservationInterval` respectively: `LUMINA_VIDEO_PROGRESS_PROBE=1 backend/.venv/bin/python apple/scripts/benchmark_video_formats.py AFTER_BINARY OUTPUT_DIR --client-mkv --baseline-binary BEFORE_BINARY --case hevc-71 --seconds 60 --steady-seconds 30 --runs 3`. Hold the repository's performance lock and keep other workloads consistent.

Focused validation: generic iOS and tvOS Simulator Release app builds pass; two native MKV/readiness tests pass; the signed Release tvOS Simulator workflow passes with no skips, checking periodic server progress before pause/close, pause stability, close, saved-position seek, and continued playback after reopening. Four earlier CLI decoder probes (H.264/stereo FLAC and HEVC/7.1 FLAC on tvOS Simulator and iPhone Duo Simulator) delivered frames/audio, advanced and sought; their baseline CPU values are retained in the result JSON and are not comparative app CPU measurements.

Physical Apple TV CPU/energy/thermal validation remains pending because the device is unavailable. Full-suite testing remains reserved for release, as requested.

### Native Library returns and persistent artwork (2026-10-27)

A repeated Library appearance previously requested page one again and discarded
already loaded pages. The retained pagination workflow reproduced the failure
(two loaded items became one, with an additional `/Items` request) before the fix.
The same warm return now retains the complete wall and paging cursor without a
new catalog request. Up to eight query snapshots retain category/filter paging;
explicit refresh still reaches the server and session changes discard snapshots.

The native artwork cache adds a 2 GiB compressed disk tier, a 128 MiB / 256-image
decoded tier, and a settings clear action on iOS and tvOS. Disk I/O and decoding
stay off the main actor; signed URL hashes isolate renditions and credentials.
A queue-owned LRU index avoids an O(n²) directory scan while filling a large
library. See ADR 0034 for persistence and cleanup boundaries. The Library keeps
its navigation root and visible image state during a pushed detail, and prepares
the first viewport before publishing a fresh poster wall.

An optimized host probe with a synthetic 400 × 600 JPEG and 100 ms network delay
measured a 107.979 ms median cold load versus 2.787 ms from disk across five fresh
cache instances with memory retention disabled. The persisted returns made no
additional network requests. These measurements isolate cache reuse; they do not
measure physical-device frame rate or end-to-end Library transition latency.

Qualification and before/after captures for this change are recorded in ignored
`output/library-cache-release/README.md`. Core request-count and persistence
checks are distinct from physical-device transition latency; a passing XCTest
journey alone does not establish that a system glass animation never hitches.
