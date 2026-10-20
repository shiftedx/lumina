# Apple client performance review — 2026-10-20

Scope: native iPhone, iPhone Duo, iPad and tvOS clients, shared AVFoundation music/video playback, and the server playback decision boundary. This is a source review with synthetic execution evidence and focused improvements. It is not a completed physical-device CPU, energy, thermal, HDR or HDMI qualification.

## Results implemented

| Work | Before | After | Evidence |
| --- | --- | --- | --- |
| Music pause/resume over ten cycles | 11 downloads and full PCM conversions; 33,837,056 media bytes | 1 initial download/conversion; 3,076,096 media bytes | Real loopback HTTP workload; retained native player nodes/buffers, 90.9% less delivered media |
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

### 1. Music pause/resume — fixed in the follow-up

Ordinary pause now uses `AVAudioPlayerNode.pause()` / `AVAudioEngine.pause()` or `AVQueuePlayer.pause()` and cancels the app's playback clock and unfinished lookahead. It retains only this transport's existing bounded decoded slots or native streaming queue. Resume starts the same nodes/player, shifting decoded host-clock boundaries by the paused interval so a scheduled successor and an in-progress fade stay aligned. It does not resolve the source, download, convert PCM, slice/copy the buffer, recreate the player, or publish another track/start event.

Stop, track selection/seek, owner replacement/sign-out, completion, terminal stream failure and explicit authorization failure still discard playback resources. Repeat/crossfade edits while paused invalidate the old scheduled successor. A source or seek finishing after Pause cannot restart playback. There is no new file cache, shared member cache, retained temporary input or new dependency. Pausing retains the existing PCM memory until teardown; this does not reduce the active-session memory ceiling. Streaming may still fetch bytes needed for ordinary buffering; reusing its item does not promise zero future network reads or system decoder work.

The host workload uses an eight-second synthetic stereo 48 kHz WAV (3,076,096 file bytes), an uncached loopback HTTP response with a fixed 100 ms delay, ten 40 ms pauses and ten resumes. Both versions use the same 80 ms decoded start lead. Resume latency spans Resume through `confirmPlaying()`; the revised confirmation requires a render timestamp after the resumed start, so a stale pre-pause render cannot acknowledge playback. This is engine render confirmation, not acoustic/output latency. The median is the upper middle observation of ten cycles, with a 50 ms confirmation polling interval.

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

### 2. Whole-track decoding still delays startup and has a substantial memory footprint

Source: `Core/Sources/LuminaCore/NativeMusicTransport.swift:101` and `:414`.

For admitted short tracks, `start()` awaits the entire file download and PCM conversion before beginning playback; it also prepares a successor. At 48 kHz, two-channel Float32 PCM uses 384,000 bytes per second: one 120-second converted buffer alone is 43.95 MiB. The 128 MiB guard covers source + converted buffers during one preparation, not total process RSS; the current buffer, successor conversion, retiring buffer during overlap and a seek slice can coexist.

The admission correction prevents a known download-then-reject path. Remaining work: measure peak live allocations across current/preload/crossfade/seek, then consider incremental scheduling with AVAudioFile/AVAudioConverter or native streaming when buffered DSP is unnecessary. Preserve gapless timing, manual gain and clipping safety. Do not silently remove normalization/crossfade to report a lower memory number. Normalization/crossfade remain unavailable on the streaming path; the corrected admission check selects that existing fallback before the unnecessary download.

### 3. Polling and background publication still have room to improve

Source: `Core/Sources/LuminaCore/NativeMusicTransport.swift:282`, `App/Shared/PlaybackScreen.swift:852`.

`NativeMusicTransport.startClock()` still wakes ten times per second in both modes. Throttling notifications removes most downstream SwiftUI, checkpoint checks and Now Playing writes, but does not eliminate those timer wakeups. `PlaybackModel.receiveAudioState()` rebuilds system Now Playing metadata on each delivered state, and lyrics/queue presentation can depend on the same playback model.

Next change: use AVPlayer item/transport/end observations and a native progress observer for streaming; use a coarser decoded clock outside a fade/boundary and the existing fine clock only where envelopes need it. Keep audio progress reporting and sleep deadlines correct in background. Measure callbacks, timer wakeups and SwiftUI body updates while the music screen is visible, while browsing another destination and while backgrounded. Avoid a global timer or broad state migration without a trace showing the benefit.

### 4. Artwork decode and resize churn can block rendering

Source: `App/Shared/MediaPresentation.swift:7`, `:122` and `:321`.

`BillboardArtwork` loads up to 12 MiB of encoded data and constructs `UIImage(data:)` on the view task. The encoded-byte limit does not bound decoded dimensions. There is no explicit thumbnail decode/preparation off the main actor. Hero artwork requests up to 1920 pixels; a portrait source can then incur a large blurred background. Carousel prefetch warms URLSession bytes but does not establish a prepared image-cache hit.

`MediaBillboard` and `FeaturedShelf` publish exact layout widths through `@State` for every geometry change although their layout decisions mostly need a compact/wide threshold. Duo opening and iPad live resizing can therefore repeatedly invalidate artwork/copy subtrees; branch changes can recreate the artwork task.

Next change: trace image decoding and body invalidation during resize/scroll. Downsample to an explicit pixel budget off the main actor; reuse prepared images with bounded cost and owner-aware cleanup. Publish a layout category where raw dimensions are unnecessary. Measure decoded bytes, image GET counts, long main-thread tasks and hitches; do not add another image library before checking native APIs and the existing system cache.

### 5. Home latency is unnecessarily sequential

Source: `Core/Sources/LuminaCore/LuminaStore.swift:190` and `:195`.

`LuminaStore.loadHome()` awaits recently added items, then resume items, then next-up items. `refresh()` loads the current video catalog before Home. These independent reads can add their network round trips to the visible refresh path. Generations reject stale results but do not themselves cancel an obsolete in-flight request.

Next change: run independent Home reads concurrently, committing only the current generation, and avoid refreshing invisible catalogs after every playback stop. Preserve partial-error behavior and member isolation. On a controlled 100 ms RTT fixture, record request overlap, time to first usable shelf, total refresh latency and canceled requests. No latency savings are claimed from source inspection alone.

### 6. Queue presentation and persistence still perform work proportional to queue size

Source: `App/Shared/PlaybackScreen.swift:481`, `Core/Sources/LuminaCore/LuminaMusicLibrary.swift:164`.

`PlaybackModel.queueRows` groups/maps the full queue whenever requested. Lyrics and playback progress share observable state with queue controls. `LuminaMusicLibrary.persistCheckpoint()` sanitizes/encodes the full queue and writes it atomically on the main actor; active playback checkpoints can repeat roughly every five seconds. `LuminaOfflineLibrary.downloadedItems` resolves local-file existence while deriving display data.

Next change: trace a 1,000-entry queue and large offline library. Cache queue rows when queue membership changes; separate frequently changing position from stable queue metadata; move bounded encoding/file work off the main actor with ordered owner/generation checks. Measure p95 main-thread duration, bytes written per minute and queue-panel body updates. Retain duplicate-occurrence identities and offline/member correctness.

### 7. Format capability and playback cost reporting need to become more precise

Source: `Core/Sources/LuminaCore/LuminaClient.swift:116` and the shared `backend/app/services/playback_decision.py` / `jellyfin_playback.py` boundary.

`LuminaClient.playbackInfo()` shares one static profile across all supported Apple devices. It still lacks hardware/OS/output-specific qualification, detailed Dolby Vision profiles, chroma restrictions and runtime fallback from a failed direct asset to one compatible attempt. Device profile conditions that the backend does not model cannot fix those gaps simply by adding JSON fields. Audio-only `audioRequest()` uses a container allowlist, which likewise does not prove that every codec inside an allowed container is playable; test asset failure and one bounded compatibility retry, excluding authorization failures.

The UI labels every compatible HLS request `HLS` and marks it `isTranscoding`, including server remuxes. This prevents the user-facing label from proving that video was copied. Expose the existing server decision mode/reasons in playback diagnostics, along with first-frame time, stalls and AVPlayer access/error-log information. Keep stream URLs/tokens/private media names out of exported evidence.

## Playback matrix after this change

This table describes negotiation and server work, not blanket physical-device certification.

| Source | Intended route | Cost / qualification |
| --- | --- | --- |
| MP4/M4V/MOV, H.264 8-bit or HEVC up to 10-bit, AAC/AC-3/E-AC-3 | Direct file | Existing AVKit route; actual device, HDR and sound output still need qualification |
| Same containers/video, MP3 or ALAC audio | Direct file | Newly advertised; synthetic H.264+MP3 MP4 and H.264+ALAC MOV advanced in macOS AVPlayer |
| MKV, supported H.264/HEVC + AAC/AC-3/E-AC-3/MP3 | HLS remux | Server copies video/audio; container work remains, no video encoder |
| MKV with supported video and DTS/TrueHD | HLS audio conversion | Copy video, encode compatible audio; no claim of lossless/Atmos preservation |
| H.264 High 10 / HEVC 12-bit | Compatible video conversion | Newly constrained to prevent false direct capability |
| Unsupported video codec or selected unsupported image subtitle | Compatible video conversion/burn-in | Genuine video encode; qualification and server hardware matter |
| MP3/AAC/M4A/FLAC/WAV/AIFF/CAF and other audio containers already on the native list | Direct audio URL | Short admitted sources may still be locally decoded/resampled; “direct” does not mean zero client DSP |
| Unsupported music container | Cached compatible FLAC endpoint | Existing server conversion path; not an HLS session |

Next format candidates: FLAC in supported video/HLS containers, PCM in MOV, and hardware-gated AV1. Test each real container/codec/profile/channel combination before advertising it, including seek, resume, track selection and fallback. Adding `mkv` or `dts` to AVKit’s static profile would make a capability claim the app does not currently establish. Apple’s [HLS authoring specification](https://developer.apple.com/streaming/hls-authoring-specification-for-apple-devices.html) documents lossless audio carriage and its restrictions; [AVPlayer](https://developer.apple.com/documentation/avfoundation/avplayer) supports local/remote file media and HLS. Published carriage support does not replace output testing.

## Route to broader direct playback

Direct file playback, stream-copy remux and decoding are different costs. Direct file playback avoids server media processing, but the device still demuxes, decodes and renders. A server HLS remux changes packaging while preserving compressed video/audio; it is substantially different from running a video encoder. Playback diagnostics should show those modes separately before using a direct-play percentage as the success metric.

| Approach | Format reach | Performance implications |
| --- | --- | --- |
| Existing AVKit plus truthful capability profile and server stream-copy remux | Native file formats, plus containers the server can repackage into supported HLS | Retains the native decode/render path; uses server demux/mux and segment I/O where required |
| Add client container demux feeding native hardware decode | Could permit original MKV delivery for supported elementary streams | New seek/timing/subtitle/buffering work; requires a playback architecture decision and format/output qualification |
| Add a general media engine with hardware decode and software fallback | Potentially wider container/audio/video reach | Dependency/license/binary-size tradeoffs; fallback CPU, HDR, audio output and background behavior need per-device measurement |

Near-term recommendation: maximize direct files and stream copies through the existing engine, then qualify FLAC/PCM and hardware-specific codecs. If original MKV delivery or unsupported lossless audio is a firm requirement, prototype a client demux/engine path against the same format corpus and compare server CPU, device energy, first-frame/seek latency and output fidelity. A codec-name allowlist alone cannot provide that support. No new media-engine dependency or architecture migration was introduced by this review.

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
- Initial review native script: 38 Core tests passed with no skips. The pause/resume follow-up script passed 44 with no skips, including all three real-server cases; iOS Simulator and tvOS Simulator builds both succeeded.
- Initial review repository `make check`: bootstrap, frontend typecheck/build and backend suite passed (5,233 passed, 11 skipped). The frontend unit phase stopped with one playback-metric label failure: `localPlayerStart.test.tsx` expected `direct` and received `direct_resume` for a start clamped to zero. A focused unchanged-source rerun passed 14/14. The unchanged full frontend rerun passed 2,622/2,622 tests across 299 files. The separately run real-stack journey/performance gate passed 52 cases with 5 skips and unchanged budgets. The mocked-browser gate passed 657 cases with 2 skips (23.4 minutes). This is not one green `make check` invocation.
- Pause/resume follow-up: one green full `mise exec -- make check` invocation, with 5,239 backend, 2,622 frontend, 52 real-stack and 657 mocked-browser cases passed (11, 0, 5 and 2 skips respectively). The final native script separately passed 44 Core tests with no skips and both simulator builds.
- Focused server playback decisions: 59 passed, including new native direct/remux/audio-only/bit-depth rows.
- Synthetic H.264+ALAC MOV advanced 0.981 seconds and H.264+MP3 MP4 advanced 1.066 seconds after approximately one second of macOS AVPlayer playback. This establishes synthetic decode on macOS only.

Local evidence logs: `/tmp/lumina-apple-performance-gain6.log`, `/tmp/lumina-apple-performance-before.log`, `/tmp/lumina-apple-performance-final-core.log`, `/tmp/lumina-apple-performance-final-native.log`, `/tmp/lumina-apple-performance-decisions.log`, and `/tmp/lumina-apple-performance-make-check.log`, and `/tmp/lumina-apple-performance-remaining-checks.log`. These are local, temporary artifacts; the durable result is this report plus the retained regression tests. No signed archive, upload or deployment was requested or performed.
