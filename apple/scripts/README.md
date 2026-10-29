# Native proof fixture server

For signed archives, IPAs, and uploads, see [TestFlight distribution](../docs/testflight.md). This disposable simulator fixture is never included in a release package.

From the repository root, start the production server and keep its terminal alive for the entire native run:

```sh
mise exec -- backend/.venv/bin/python -u apple/scripts/proof_server.py
```

For visual review with original artwork, optionally supply a directory containing `cartographer.png`, `lanterns.png`, and `observatory.png` (each a PNG no larger than 12 MiB):

```sh
LUMINA_NATIVE_ARTWORK_DIR="<artwork-directory>" \
  mise exec -- backend/.venv/bin/python -u apple/scripts/proof_server.py
```

The fixture copies those files unchanged: cartographer and lanterns become their corresponding movie posters; observatory becomes the third movie poster, album cover, and artist image. Show artwork follows the lanterns poster. With the variable unset, the fast synthetic bar artwork remains the default. Restart the disposable fixture only between native runs when changing artwork.

Wait for “ready” before building the UI tests: their test-only build phase copies the generated fixture into the test bundle. The server listens only at `http://127.0.0.1:18743`, creates fresh temporary data/media roots, and writes ignored `apple/Tests/UITests/Fixture.json` with mode `0600`. It contains disposable per-run member credentials, public item IDs, and the loopback URL. Ctrl-C stops the server and removes its fixture and generated state. Restart after fixture schema changes; do not reuse a test bundle containing older IDs. Never commit the JSON or copy its credentials into logs or documentation.

The catalog has three generated H.264/AAC movies, a show/season/two-episode hierarchy imported through ordinary NFO metadata, and two numbered AAC sine-tone music tracks with generated album/artist artwork, timed lyrics, gain metadata and a shared read-only playlist. Episode files reuse the original synthetic movie bytes. Startup verifies public info and sign-in, video catalog/posters and direct/compatible negotiation, saved progress, show/season/episode relationships, music browsing and album children, authenticated artwork, fresh Audio detail, and audio stream bytes. The M4A stream must have the native-compatible `audio/mp4` content type.

PR #215 adds the native video format-recovery fixture. With
`LUMINA_NATIVE_VIDEO_FORMAT_FAILURE=1`, the server completes its ordinary
startup verification first, then faults only the verified public item's direct
stream bytes. A recovery client must make one H.264/SDR, stereo-AAC HLS retry
while retaining the media source, selected options and resume position. It does
not fault another item's direct stream or the HLS master route. The scoped UI
selectors are
`NativeVideoFormatRecoveryTests/testInitialFormatFailureRetriesOneCompatibleVideo`
on iPad/Apple TV and
`NativeVideoFormatRecoveryTests/testStandardPhoneInitialFormatFailureRetriesOneCompatibleVideo`
on iPhone. Use a fresh fixture/broker and refresh the selected test bundle for
each targeted run; do not reuse a fixture generated for another test class.
The iPad recovery journey passes `--ui-testing-static-animations` to its
`LUMINA_UI_TESTING` app build to avoid XCTest animation-idle stalls. This
disables UIKit and SwiftUI animations only for that explicit test launch;
shipping builds exclude the switch and the iPhone lane uses normal animations.

The fixture's fast boundary checks are separate from UI execution:

```sh
mise exec -- python apple/scripts/test_ui_suite.py
mise exec -- backend/.venv/bin/python -m unittest apple.scripts.test_proof_server
```

The first command covers the 27 broker/routing cases. The second is the one
ASGI HTTP contract for the public-ID direct-stream fault and its untouched HLS
route. Neither command establishes physical HDR output or hardware-decoder
qualification.

For the opt-in Duo continuity workflow, start the fixture with `LUMINA_NATIVE_EPISODE_MKV=1` to exercise client-demuxed episodes; leave it unset for MP4 episodes. Both paths appear as Direct in the app. Run `NativeVideoDuoContinuityTests` with `TEST_RUNNER_LUMINA_DUO_TRANSITIONS=1` and `LUMINA_UI_TESTING` in the app's compilation conditions. Start with Device Hub in Closed posture, then switch to Open during the printed playing window and back to Closed during the paused window. The workflow resumes an episode at 12 seconds from the season Play button; `TEST_RUNNER_LUMINA_DUO_ENTRY=home` instead uses Home's Continue Watching card. It checks player identity, real-time progress, and explicit pause preservation. It is a transport check, not proof of uninterrupted rendered frames; inspect the visible simulator window as well.

For standalone fold diagnosis, launch the optimized `LUMINA_UI_TESTING` app directly with `SIMCTL_CHILD_LUMINA_PLAYBACK_TRACE=1 xcrun simctl launch --stderr=/tmp/lumina-playback-trace.log <UDID> ai.cozylabs.lumina --ui-testing`, then start playback and change poses in Device Hub. No XCTest runner is involved, so there are no scheduled pause or close actions. The trace is written to the requested stderr file and records player/item identity, actual time, saved position, buffering, seeks, lifecycle transitions, and audio interruptions, without URLs, titles, or credentials. Rendering events include view/layer/window identity, bounds, and render readiness. Per-instance interval summaries count view updates, layout callbacks, rate applications versus actual writes, and queued versus sent progress reports; detailed output is capped at 20 events per second per instance so a callback storm remains measurable without flooding the log. `NativeVideoDuoContinuityTests/testStablePlaybackDoesNotContinuouslyRewritePlayerRate` separately checks that steady playback does not enter a rate-observer feedback loop; it needs no manual pose changes.

The legacy single-Fixture.json commands below must run sequentially: they mutate the same synthetic positions and member state. For isolated parallel tiers, use `run_ui_tiers.py` as documented in [the native UI readiness gate](../docs/app-store-readiness.md#native-ui-signal-gate-2026-10-22). That runner owns its simulators and fixtures, runs both platforms concurrently, and has no Xcode-exclusivity or host-load gate under the user's audit authorization. Never stop another developer's devices or test processes.

From `apple/`, run the real-server client/media integration before UI workflows:

```sh
LUMINA_NATIVE_FIXTURE="$PWD/Tests/UITests/Fixture.json" \
  mise exec -- ../scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  swift test --package-path Core --filter NativeServerTests
```

The three AVFoundation cases cover direct audio, server-compatible FLAC audio, and direct/HLS video with saved progress. These macOS checks supplement platform UI evidence; they do not qualify hardware output.

Use `xcrun simctl list devices` to find a disposable simulator UDID. A focused current workflow run is:

```sh
mise exec -- ../scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  xcodebuild -project Lumina.xcodeproj -scheme Lumina-iOS \
  -destination 'platform=iOS Simulator,id=<your-disposable-simulator-UDID>' \
  -parallel-testing-enabled NO -collect-test-diagnostics never \
  -derivedDataPath /tmp/lumina-native-navigation-ios \
  -resultBundlePath /tmp/lumina-native-navigation-ios.xcresult \
  -only-testing:Lumina-iOS-UITests/NativeProofTests/testFeaturedTitlePreservesDetailsAndResumesPlayback \
  -only-testing:Lumina-iOS-UITests/NativeProofTests/testAudioAlbumTrackPlaybackReportsProgress \
  -only-testing:Lumina-iOS-UITests/NativeProofTests/testShowSeasonEpisodeBrowsingAndPlayback \
  -only-testing:Lumina-iOS-UITests/NativeProofTests/testVideoWatchedFavoriteAndActivityFilters test
```

For Apple TV, use `Lumina-tvOS`, `platform=tvOS Simulator`, and `Lumina-tvOS-UITests`, with separate DerivedData and a fresh result-bundle path. Remove the `-only-testing` arguments to run the complete platform UI suite. Keep iOS, tvOS, and the shared real-server integration sequential. Retain local signing for runtime tests because Keychain needs the generated entitlements; unsigned generic builds check compilation only. Keep DerivedData and result bundles outside `apple/` to avoid package file-watcher rebuilds.

The current UI contracts are:

- Home: actual sign-in, cinematic hero, recent-shelf caption and a visible known poster, destination navigation, title selection, detail, and resumed video. iOS additionally requires idle rotation within its bounded wait, Pause, and a real horizontal swipe to another title. Automatic rotation is intentionally disabled with Reduce Motion or VoiceOver; use normal motion and VoiceOver off for that specific idle-rotation check. TV focus intentionally pauses rotation.
- Audio: album → track → player with advancing time and server progress; iOS background playback and TV remote pause/resume; Browse retaining the now-playing bar while navigating Video/Audio; reopening without restarting; album Next, queue inspection/selection, and repeat values. Stop ends playback and removes the bar for test isolation. Browse is different from video Done, which closes the video session.
- Extended music (both platforms): `testMusicPlaylistsSmartSelectionAndDownloadedPlayback` checks shared read-only playback, saved/smart playlists and actual local-file playback; `testMusicAdvancedControlsPausedRestoreAndQueueConflict` checks listening options, lyrics, paused restore and revision-conflict resolution. Both feature workflows have passing case-level evidence on both platforms, and the selected current simulator renders have been accepted in the verification record.
- Shows: show → season → two episode entries → selected episode in its parent show → playback, then saved progress.
- Member activity: watched/favorite actions checked against server state; native switch values and visible filter summaries checked before catalog membership assertions; cleanup resets the fixture state.

Screenshot attachments cover the hero, automatic/manual carousel changes, recent shelf, Video/Audio destinations, album tracks, persistent audio, queue, and episode list. Failed audio journeys include the accessibility hierarchy. Inspect the result images as well as test status. Catalog-specific playback/layout/discovery checks explicitly select Video; sign-in itself lands on Home. These workflows replace the old requirement that the full catalog fit on Home's first screen.

`testLibraryPullRevealsSearchAndRefreshesProgress` retains iOS search/pull-refresh coverage. `testNativePlaybackReportsResumeAndConversionProgress` retains direct/HLS playback, server progress, and resume checks. `testCompatibleHLSReplayRequestsFreshSession` verifies converted-stream completion/replay and fresh persisted progress.

For the existing Duo layout workflow, select a disposable simulator in Device Hub and set Closed, Book, and Open in portrait and landscape. Run once per state with a distinct result path:

```sh
mise exec -- ../scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  xcodebuild -project Lumina.xcodeproj -scheme Lumina-iOS \
  -destination 'platform=iOS Simulator,id=<your-disposable-simulator-UDID>' \
  -parallel-testing-enabled NO -collect-test-diagnostics never \
  -derivedDataPath /tmp/lumina-duo-matrix \
  -resultBundlePath /tmp/lumina-duo-<pose>-<orientation>.xcresult \
  -only-testing:Lumina-iOS-UITests/NativeProofTests/testCurrentDuoLayoutKeepsControlsAndPlaybackReachable test
```

This case preserves the current Device Hub layout and records actual viewport dimensions while checking sign-in, Video/detail bounds, playback, verified video background pause, explicit resume, completion, Replay, and Done. XCTest orientation/Home requests do not reliably change that Duo runtime; choose the layout in Device Hub and inspect it there. The background check activates SpringBoard and verifies application state. Test setup signs out any existing member, so never use a simulator holding a production login. Historical Duo results predate the current destination and persistent-audio changes unless explicitly rerun.

Without a fixture, a clean checkout builds and the UI tests explicitly skip. The optional fixture-copy phase has script sandboxing disabled only for UI-test targets so its input may be absent; application scripts remain sandboxed. There is no alternate test login path in the app. The extended offline contracts cover bounded disk storage and owner isolation; engine contracts exercise native-clock transitions. Synthetic tests do not establish HDR/HDMI/Atmos output quality, every offline interruption/storage condition, every gapless/crossfade format, Sonos hardware behavior, or complete web-feature parity. See [verification status](../docs/verification.md) for qualification evidence and remaining hardware limits.


For the extended music UI pass, add both test selectors above to the platform-specific command, replacing the test target prefix for tvOS. Run iOS and tvOS sequentially with separate result bundles and retained local signing. Keep the fixture terminal alive for the whole run, including source rebuilds; do not start a second server on the same port. Use the performance lock for finite commands and keep at most one Lumina-owned simulator booted, even when tests target separate platforms. The temporary global simulator guard has been stopped and its exit confirmed; do not restart it or stop unrelated apps' simulators or Xcode tests. Focused Core filters are `LuminaOfflineLibraryTests`, `LuminaMusicLibraryTests`, `NativeMusicTransportTests` and `MusicPlaybackPolicyTests`; record each actual result instead of treating an earlier green run as proof of later source changes.


Current repository/Core qualification (2026-10-20): the final artwork Core run passed 35 cases including all three live `NativeServerTests` and Episode/Season inherited series-artwork fallback (`/tmp/lumina-audio-parity-core-art-final.log`); the focused engine/policy run passed 5 + 3 cases. The final repository invocation passed bootstrap, typecheck, web build/style gates, backend (5,233 passed, 11 skipped), and frontend unit tests (2,622 passed across 299 files). Its real-stack cohort had 51 passes, 5 skips and one first-viewport image-baseline failure; the unchanged-budget focused repair passed 2/2. The mocked-browser cohort had 656 passes, 2 skips and one catalogue menu-interaction failure; the focused dark-desktop Undo repair passed. This is full-cohort evidence plus focused repairs, not one green `make check` invocation. The selected eight native journeys and current visuals are qualified separately in [the verification record](../docs/verification.md); no release build, upload or deployment is established by these checks.


The current native evidence table tracks eight passing journeys: Home, Audio, Advanced listening and Playlist/download on each platform. TV Home/Audio/Advanced/Playlist times are 193.410/211.004/284.025/229.279 seconds; iOS times are 142.445/183.975/144.629/147.760 seconds. Each result is case-level evidence; a bundle containing another failed/interrupted case is not reported as wholly passing. The final iOS Advanced pass followed an XCTest-only exact-button menu lookup repair, with no product change. Current iOS and TV renders have been reviewed and accepted; TV Album retains its earlier accepted capture. See [verification](../docs/verification.md) for result paths and [the local visual gallery](/Users/kmcdowell/.codex/visualizations/2026/10/20/01a15766-1f7c-7d21-b521-0604bea3306b/native-parity/extended-audio/review.md) for current captures. Signed local runtime compilation is confirmed on both platforms; no distribution archive or upload is claimed. Hardware codec/output and Sonos acceptance remain separate from this simulator qualification.

## Music startup and memory benchmark

`benchmark_music_startup.swift` runs one fresh-process engine workload with album normalization, a configured three-second crossfade, lookahead and two seconds of playback. It reports start-to-render confirmation, peak/active process physical footprint and process CPU time. `benchmark_music_startup.py` compares two compiled revisions over generated FLAC fixtures, including seek, active overlap and a loopback HTTP response with 100 ms delay. It alternates revision order and reports medians from five fresh processes per revision/scenario.

Use the finite-command performance lock. Fixture generation happens outside the measured process, and the benchmark does not stop other apps or flush OS caches. See [measurement results and reproduction](../docs/performance-review.md#2-whole-track-decoding--replaced-with-native-file-scheduling) for exact commands, scope and device limitations.


## Dolby Vision local qualification

The MP4 and MKV direct profiles opt into Profile 5 and supported Profile 8
variants independently of server HLS encoding. Tests check the native request
body, source profile/level/layer restrictions, and preservation of actual Dolby
configuration/RPU data through the pinned MKV packaging engine. They do not
certify a physical display or receiver.

Create a bounded Profile 5 fixture from Apple's public Advanced HLS example
outside Git. Only the init section and two video fragments are downloaded;
video is stream-copied and a synthetic stereo AAC track is added:

```sh
mise exec -- python apple/scripts/qualify_dv5_fixture.py /tmp/lumina-dolby-fixtures/p5
```

Generate synthetic Profile 8.1/8.4 fixtures using an explicitly supplied
[dovi_tool](https://github.com/quietvoid/dovi_tool/releases/tag/2.3.4) executable
(tested with 2.3.4), plus FFmpeg/libx265. This helper downloads nothing and adds
no app dependency. It generates real RPUs, injects them into Main 10 video,
creates the matching Dolby configuration record and checks that MP4 and MKV
retain the exact RPU sequence. Its manifest records tool versions and hashes.
These fixtures test structural preservation, not reference color grading.

```sh
mise exec -- python apple/scripts/generate_dv_fixtures.py \
  --dovi-tool /path/to/dovi_tool --output /tmp/lumina-dolby-fixtures/p8

LUMINA_DV5_MKV_FIXTURE=/tmp/lumina-dolby-fixtures/p5/apple-dv5-two-fragment.mkv \
LUMINA_DV8_MKV_FIXTURES=/tmp/lumina-dolby-fixtures/p8/dv8-1.mkv:/tmp/lumina-dolby-fixtures/p8/dv8-4.mkv \
mise exec -- swift test --package-path apple/Core \
  --filter NativeVideoSourceTests.testPublicDV5MKVRetainsMasterInitAndRPUsThroughSubtitleMaster \
  --filter NativeVideoSourceTests.testSyntheticDV8MKVsRetainMasterInitAndRPUsThroughSubtitleMaster
```

The two opt-in cases cover all three profiles with normal and external-subtitle
masters, every segment's Dolby configuration, one RPU per video packet, the
complete ordered RPU sequence and independently readable seek fragments. They
skip when their fixture environment is absent. Keep fixtures outside the source
tree. Physical Dolby Vision, HDMI/Match Content and Atmos remain separate checks.

## Remote music startup benchmark

Pass an optional codec to `benchmark_music_startup.swift` for a crossfade-off, source-loudness workload. It emits a `STARTUP` marker at confirmation, records two seconds of playback, then qualifies progressive midpoint seek and pause/resume outside the measured window. `benchmark_music_remote.py` compares five source formats with a shared per-run delivery budget, records bytes sent at confirmation and rejects any revised sample that waits for the whole input or fails seek/resume. See [progressive startup results and reproduction](../docs/performance-review.md#2a-remote-music-startup--progressive-source-playback). Use focused native transport checks and both Simulator builds for this slice; the full repository suite is deferred to the release gate at the user's request.

Use `--before-mode progressive --pause-seconds 10` to compare progressive revisions during a ten-second pause immediately after confirmation. Compile both revisions with the current Swift harness. `MEASURED` marks the end of the CPU/footprint window; the driver also snapshots delivered bytes then, before resume/seek qualification. `--formats wav mp3` narrows an investigation; omit it to qualify all five formats. The default remains the two-second active playback workload.

Use `--before-mode progressive --play-seconds 10` for an active progressive comparison. The harness requires at least 9.5 seconds of playhead advancement over a ten-second window, then qualifies seek and pause/resume after recording CPU/memory. `--play-seconds` defaults to two and is ignored when `--pause-seconds` is positive. Compile both baseline and revised sources with the same current harness. See [active results](../docs/performance-review.md#2c-active-progressive-playback--control-read-ahead-and-use-the-native-clock).

Video format/performance qualification is reproducible with `benchmark_video_formats.py` and
`benchmark_video_playback.swift`. The runner generates synthetic H.264/HEVC + FLAC MP4/MKV
fixtures, exercises the production decision/FFmpeg command, and compares native HTTP
playback and seek. See [performance review §2d](../docs/performance-review.md#2d-video-retain-flac-instead-of-unnecessarily-encoding-aac)
for compilation, simulator execution, measurements and output limits.

For on-device MKV, build `apple/Benchmarks` and pass its `VideoBenchmark` executable to
`benchmark_video_formats.py --client-mkv --seconds 60`. The benchmark package links the
same `NativeVideoSource` used by the app; its Swift source is a symlink to the standalone
AVFoundation benchmark, so frame/seek measurement stays shared. Add
`--bytes-per-second 2097152 --runs 1` for paced HTTP qualification. Exact commands and
tradeoffs are in performance review §2e and ADR 0031.

TestFlight export clears inherited signatures in unsigned archives with `prepare_archive.py`;
Xcode then signs each embedded framework using its current bundle identity. The archive/IPA
validator checks individual framework designated requirements as well as the parent app seal.
Run `mise exec -- python3 apple/scripts/test_archive_signing.py` for the synthetic signing
regression. The helper refuses a signed application archive.

Video playback probes accept `LUMINA_VIDEO_WAIT_MODE=event` to exercise native screen readiness, or `poll` to compare the former 100 ms loop. `LUMINA_VIDEO_STEADY_SECONDS=12` adds sustained frame/advance validation, process CPU and pause CPU measurements before the existing seek check. Use identical inputs/frame settings for comparisons; this host probe's BGRA sampling is measurement overhead, not the app's AVKit rendering path.

For a finite local qualification of a new native HLS variant, give
`benchmark_video_playback.swift` the exact loopback HLS entry point served to
the client rather than a media file. The fixture needs audio, independently
seekable fMP4 segments and at least seven seconds of media: the probe requires
a decoded frame and one second of advancing playback, then seeks to six
seconds and requires another decoded frame. For example, compile the
checked-in probe and point it at a temporary loopback server:

```sh
swiftc -swift-version 6 -O -parse-as-library apple/scripts/benchmark_video_playback.swift \
  -framework AVFoundation -framework CoreVideo -framework Foundation \
  -o /tmp/lumina-video-probe
/tmp/lumina-video-probe http://127.0.0.1:<port>/<master>.m3u8
```

This establishes local AVPlayer frame delivery, advancing time and seek for
that exact host, OS, manifest and codec profile. It does not certify hardware
decode, HDR display output, HDMI output, every AV1/HDR profile, or any other
device. `VTIsHardwareDecodeSupported` and
`AVURLAsset.isPlayableExtendedMIMEType` are useful candidate gates, but the
former only reports decoder availability and the latter validates a specified
asset/container combination, not an HLS master. Keep direct MP4 and HLS
qualification separate. For HDR, test the actual server master as well as its
media playlist: Apple requires `VIDEO-RANGE`, `CODECS`, average bandwidth and
frame rate for HDR variants, while a display-ineligible host can still decode
the fragments without proving HDR output. Apple documents AV1 HLS as fMP4 and
requires hardware support; still retain an H.264 fallback and record actual
playback failures.

`mise exec -- swift run --package-path apple/Benchmarks -c release OfflineLibraryBenchmark 1000` creates a temporary synthetic owner-scoped offline library and reports 21-sample median CPU/wall cost for reading its metadata list. It checks purge handling and owner hiding afterward, removes its files on exit, and makes no network requests. File decoding, initial reconciliation and cold-storage costs are outside this metric.

For sustained MKV comparisons, build the same `VideoBenchmark` harness against the baseline and candidate Core configurations, keeping separate binaries, then run:

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
  backend/.venv/bin/python apple/scripts/benchmark_video_formats.py \
  <candidate-binary> <fresh-output-directory> --client-mkv \
  --baseline-binary <baseline-binary> --case avc-stereo \
  --caption-stress --seconds 60 --steady-seconds 30 --runs 3
```

Both variants use client-side MKV packaging; the order alternates across pairs. The optional caption fixture is a four-track, dense synthetic stress case, not ordinary dialogue. Omit `--caption-stress` and use `--case hevc-71 --steady-seconds 12` for the subtitle-free control. The native probe samples resident memory once per second and retains peak/start/end/samples alongside process CPU, actual frame/advance checks, pause and seek. Keep frame sampling identical across binaries; a lower-cost probe alone is not an app improvement.

## Native view resource profiling

Use a locally signed, optimized simulator test build with
`SWIFT_ACTIVE_COMPILATION_CONDITIONS=LUMINA_UI_TESTING`. This explicit test-only
flag enables the UUID-scoped preferences and Keychain isolation also used by
Debug tests; ordinary Release builds omit it. Keep `-configuration Release` so
profiling does not measure unoptimized Swift.

Run `NativePerformanceTourTests/testNativePerformanceTour` through
`profile_native_views.py --simulator <UDID> --executable <Lumina-iOS-or-tvOS>
--output <fresh-private-directory> --trace -- <finite-xcodebuild-command>`.
Use the suite broker and fixture-copy build flow above; do not edit a signed
test bundle. The optional fixture variables
`LUMINA_NATIVE_PERF_CATALOGUE_COUNT=120`,
`LUMINA_NATIVE_PERF_VIDEO_SIZE=1920x1080`, and
`LUMINA_NATIVE_PERF_REQUEST_LOG=<absolute-private-path>` enable the dense
synthetic catalogue, full-HD direct and client-demux MKV movies, and bounded redacted request log.
They apply only to the disposable proof fixture.

The sampler waits for the tour's settled login marker, scopes samples and
Time Profiler to the simulator's exact app executable, rejects process changes,
and writes phase-level CPU, wakeups, charged footprint and host-load results.
CPU is a percentage of one host core, not whole-device energy or GPU/decoder
usage. The tour does not establish a retention trend or prove a leak. Simulator
traces cannot qualify hardware frame pacing, HDR or HDMI output. See the dated
performance record for the actual completed coverage and limitations.
