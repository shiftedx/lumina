# Native proof verification

Validation performed on 2026-10-17 using Xcode 27.1 (27A9269), Swift 6.4, and the checked-in project. Minimum supported deployment targets are iOS/iPadOS/tvOS 18; older OS versions have not yet been exercised.

## Completed checks

| Check | Result |
| --- | --- |
| Shared Swift package unit contracts | 8 passed; optional real-server test skips without its fixture |
| Real-server AVFoundation integration | Passed direct MP4 and compatible HLS playback, seeking to saved positions, and persisted progress |
| iPhone 18 Pro, iOS 27.0 simulator | Passed native sign-in, browse, direct playback with advancing time, persisted watch progress, resume, and compatible HLS playback |
| iPad Pro 11-inch (M5), iPadOS 27.0 simulator, landscape | Passed the same journey, including keyboard Go submission; captured direct and HLS video frames were visually inspected |
| iPhone Duo, iOS 27.1 simulator | Passed native sign-in, browse, direct playback, persisted progress, resume, and compatible HLS playback on the active display |
| Apple TV 4K (3rd generation), tvOS 27.0 simulator | Passed the same journey using Siri Remote focus/select/Menu, Play/Pause and resumed transport, including return to the fixed library header |
| Generic iOS and tvOS simulator builds | Passed |
| Generic iOS and tvOS device Release builds | Passed compilation without distribution signing |
| Clean tvOS UI-test build without disposable credentials | Passed `build-for-testing`; verified the test bundle contains no `Fixture.json` |
| `apple/scripts/check.sh` | Passed the shared package tests and both generic simulator builds |

The UI tests use the production Lumina backend, its opt-in Jellyfin-compatible endpoints, a temporary database, disposable member credentials, and generated H.264/AAC MP4 and MKV media. They use the app's actual sign-in fields and AVKit player. Each journey verifies both advancing playback time and the server's saved watch position. Direct playback is reopened to verify resume. The shared media integration additionally seeks and plays the HLS stream from a saved position.

Simulator runtime tests retain Xcode's local code signing so Keychain works. Disabling signing is suitable for compilation checks only. Test fixture files, generated media, credentials, build products, and result bundles are deliberately excluded from Git.

The successful UI journeys above precede the final player lifecycle and audio-category changes. An additional Apple TV Home/return check exposed disposal of the player on disappearance. The final implementation retains the player across backgrounding, pauses on `willResignActive`, suppresses delayed autoplay, and allows cancelled initialization to retry. Its final UI reruns stalled in XCTest accessibility requests before the Home scenario; a passing final Home/return regression could not be recorded. Both device Release configurations compile with these changes. Verify background pause, explicit resume, and audio on physical devices before relying on this build for everyday viewing.

## Duo layout audit, 2026-10-18

The Apple HIG audit repaired bounded poster/card layout, long-title wrapping, accessibility button bounds, accessibility-size detail stacking, explicit sign-in labels/content types, and prominent-button contrast. Custom colors now respond to Increase Contrast. iOS keeps the player open at completion and offers Done/Replay; Replay negotiates a fresh Direct/HLS session because stopping releases the previous conversion session. Queued playback reports retain their original request identity.

The converted-video completion/replay workflow passed on the closed Duo in landscape: resume near completion, retained player, fresh compatible session, reset/advancing time, exit, and persisted progress. Shared package contracts passed (9 tests), as did the real-server AVFoundation integration for authentication, direct/HLS playback, seeking, and persisted progress. The final shared-player source builds for iOS and tvOS simulators.

All six Device Hub layouts passed the native sign-in, library/detail bounds and reachability, playback, verified background pause, explicit resume, completion, Replay, and Done workflow. Upright and 90° rotation refer to physical device orientation; an unfolded window's aspect ratio differs from the outer display.

| Pose | Physical orientation | Main window (pt) | Result bundle under `/tmp/` |
| --- | --- | --- | --- |
| Closed | Upright | 466 × 678 | `lumina-duo-closed-portrait-final.xcresult` |
| Closed | 90° rotation | 678 × 466 | `lumina-duo-closed-landscape-final.xcresult` |
| Book | Upright | 951 × 669 | `lumina-duo-book-portrait-content.xcresult` |
| Book | 90° rotation | 669 × 951 | `lumina-duo-book-landscape-final.xcresult` |
| Open | Upright | 951 × 669 | `lumina-duo-open-portrait-final.xcresult` |
| Open | 90° rotation | 669 × 951 | `lumina-duo-open-landscape-final.xcresult` |

Closed upright additionally passed both workflows at the largest accessibility Dynamic Type size with Dark Mode and Increase Contrast enabled (`lumina-duo-ax5-dark-contrast.xcresult`). Player buttons and hittable detail actions were checked for full vertical visibility. HLS replay required fresh saved progress strictly above 2 and below 20 seconds, so the old seeded 84-second position could not satisfy the check. The simulator's normal text size, light appearance and standard contrast were restored afterward.

After GUI access was restored, a single direct-play session continued through Open → Closed → Book → Closed → Open, rotation, then Book → Closed. The synthetic video time advanced from 15.208 seconds through 31.208, 43.792, 48.125, 56.667 and beyond 67 seconds without restarting or dismissing playback. Done returned to the same media detail; restoring upright orientation retained the title and Resume action. This live check supplements the six fixed-layout workflow runs. Device Hub was returned to the production validation simulator, where the installed app retained its authenticated library session; keyboard capture was disabled.

Device Hub is required for pose/orientation selection on this runtime: XCTest reported a requested orientation while the physical Duo display stayed unchanged. Tests use the main window's coordinate space because `XCUIApplication.frame` can be transposed after a pose change. Scroll gestures anchor to the visible content column rather than the fold or sidebar. Some XCTest screenshot attachments contain inconsistent orientation/capture metadata; Device Hub observations are required for visual conclusions.

The complete `mise exec -- make check` invocation exited successfully: 5,011 backend tests (11 skipped), 2,478 frontend unit tests, 47 real-stack/performance tests (4 skipped), and 592 mocked browser tests (2 skipped), plus bootstrap, typecheck, build, size and style gates.

## PR merge validation, 2026-10-18

After rebasing the native fixes onto the newer main branch, `mise exec -- make check` passed bootstrap, typecheck, build/size/style and 5,119 backend tests (11 skipped), then exposed an invalid AppShell activity mock. A separate one-line test fix uses the valid loading state. The full frontend suite passed all 2,547 tests with two workers; two default-concurrency timeouts passed without changing their assertions or timeouts.

The real-stack/performance gate passed 46 cases (4 skipped) and exposed an existing Diagnostics bookmark regression after Updates became the section's first page. A minimal default-page fix retains Diagnostics bookmarks and explicit Updates navigation, covered by 46 focused navigation/route tests. The unchanged recommendation/Diagnostics journey then passed with fixture setup (2 tests). The rebuilt frontend passed size/style checks and all 605 mocked browser tests (2 skipped). This is combined coverage of all gate targets after the two small web repairs, rather than a second single successful `make check` invocation. The native app sources are unchanged from the six-layout and live-transition validation above.

## Repository regression checks

Running `make check` exposed two existing regressions, fixed separately in [worker shutdown #66](https://github.com/shiftedx/lumina/pull/66) and [watch-tool selection #68](https://github.com/shiftedx/lumina/pull/68). The full backend suite passed 5,011 tests with 11 skips. After the web fix, the remaining check targets passed bootstrap, typecheck, production build, 2,478 frontend tests, and 47 real-stack/performance tests with 4 skips.

The complete mocked-browser run passed 582 tests with 2 skips; six screenshot/artifact writes failed when the shared Mac ran out of disk space, and four catalogue sweeps hit development-server navigation timeouts. After cleanup, all ten affected cases passed across focused reruns: two library-channel cases, two accessibility route sweeps, two resource/activity cases, and all four catalogue sweeps against the same compiled production bundle. The catalogue assertions and timeouts were unchanged. That earlier run provided combined coverage; the 2026-10-18 audit above subsequently recorded one complete passing `make check` invocation.

## Cinematic native library, 2026-10-19

The iOS and tvOS library and detail surfaces now share an adaptive artwork stage, selected-poster strip, and artwork-derived ambient color. iOS removes the redundant large page header and single-section sidebar, uses native navigation/search, and fits three posters across the normal phone width. A short pull reveals search; a deeper pull refreshes. Both actions commit on release, keeping the artwork layout stable during the gesture. Sign-out is inside Account. tvOS keeps system safe areas, tightens the featured composition, uses native card focus, and moves refresh/sign-out inside Account. Poster and landscape sources stay bounded by their card frames. The library requests another page near the end of the visible catalog, retaining the manual retry action.

`apple/scripts/check.sh` passed 10 shared package contracts, skipped the optional real-server test without its fixture environment, and built both generic simulators. The contracts cover authenticated same-origin backdrop URLs, artwork cache tags, catalog pagination, and deduplicated featured titles. The real-server iPhone journey passed direct playback, persisted progress, resume, and compatible HLS playback (`lumina-native-gestures-ios-reveal.xcresult`, playback case). The featured journey passed first-screen catalog visibility, thumbnail selection, selection retained through portrait/landscape rotation, Details navigation, resumed video, and persisted progress (`lumina-native-cinema-ios-bounds.xcresult`). The final iOS discovery workflow passed hidden initial search, a short pull to reveal it, a real filtered query, Cancel, and a deeper pull that reloaded a changed watch position from the server (`lumina-native-gestures-ios-pulls.xcresult`). Account sign-out was exercised during test setup.

The final tvOS featured journey passed first-screen catalog visibility, Siri Remote thumbnail selection, Details, resumed playback with advancing time, persisted progress, and sign-out through the native Account dialog (`lumina-native-account-tv-dialog.xcresult`). The TV uses a system dialog for its account actions. XCTest can expose anonymous focus wrappers with subpoint differences from their labelled action bounds; the test helper recognizes that focused wrapper while still activating with the real remote.

Visual captures use the disposable synthetic catalog and Lumina-owned onboarding illustrations in its temporary artwork cache. No household media or credentials are included. Artwork reflection uses the selected image; this pass does not implement hinge/light sensor effects or video-frame reflections. The six Duo pose checks recorded above precede this library redesign; this pass verifies iPhone rotation rather than repeating that pose matrix.

The broader tvOS Home/return regression did not pass: playback advanced after reactivation without an explicit Play command. The player lifecycle implementation is unchanged in this UI pass; background pause on the current TV runtime remains unqualified (`lumina-native-cinema-tv-final.xcresult`).

`mise exec -- make check` passed the initial build/typecheck/style gates, then stopped with 8 backend failures, 5,210 passes and 11 skips. Failures concern episode still generation, the bundled hero analyzer, idle activity, the ArtworkProgress TypeScript contract, and recommendation reason grammar. Backend and web sources are unchanged in this pass; the remaining repository targets did not run after that failure.

## TestFlight packaging, 2026-10-19

iOS/iPadOS and tvOS share the registered `ai.cozylabs.lumina` identity and the **Lumina: Household Cinema** App Store Connect record. Version 0.1.0 (1) passed Release device archives, automatic distribution signing, and upload for both platforms. Apple processed both builds; both show **Testing** in the private Device Testing group with one owner tester invited. Platform icons and TV top shelf branding use the existing Lumina sunrise glyph.

The user additionally requested an external tester. Version 0.1.0 (2) passed unsigned Release device archives, signed IPA exports, and standard App Store Connect uploads for both platforms with the internal-only export flag disabled. Both candidates were submitted for Beta App Review and show **Waiting for Review** in the private External Device Testing group, which contains the requested email tester. Automatic tester notification is enabled after approval. Review contact details are saved; review notes use the owner's companion-server model: reviewers install their own server and create their own member account, with no hosted instance or developer-managed credentials supplied. The reviewer sign-in-required checkbox is disabled as requested.

`scripts/validate_archive.py` verified bundle ID/version/build, physical-device platform, icon/top shelf metadata, compiled artwork, privacy manifest, archive debug symbols, absence of disposable fixtures/test bundles, and code signatures. IPA checks additionally verified disabled debugging, the TestFlight entitlement, and matching signing identity. Final `scripts/testflight.sh export` completed successfully for both platforms and both build numbers. The script defaults to internal distribution and takes an explicit external-audience option. All 16 asset catalog JSON files referenced existing resources; plist lint, shell syntax, and whitespace checks passed. Shared package tests passed 10 contracts, with the optional server integration test skipped without a fixture.

The current `mise exec -- make check` rerun passed bootstrap, frontend typecheck/build/size/style gates, then stopped at the same 8 backend failures: 5,210 passed, 11 skipped, 49 warnings, 890.20 seconds. The failures concern episode stills (4), bundled hero analysis, idle activity, the ArtworkProgress TypeScript contract, and recommendation reason grammar. No backend or web source was modified in this packaging pass; later gate targets did not run.

Archives and signed IPAs remain in ignored `apple/Distribution/0.1.0-1/` and `apple/Distribution/0.1.0-2/`. Packaging/upload logs are retained locally under `/tmp/lumina-testflight-*.log`. This is distribution evidence, not a new physical-device playback qualification or an App Review approval.

## Limits of this evidence

The simulator does not establish HDR10 or Dolby Vision output, HDMI Match Content behavior, frame-rate switching, surround sound, Atmos, remote-network reliability, or App Store readiness. Use the [physical device checklist](device-validation.md) to record those results. The proof does not claim Infuse format parity.

The earlier workflow checks cover generated SDR H.264/AAC sources; the dated local codec checks below add HEVC/AV1 fragment delivery evidence. HDR display output, additional audio codecs, subtitle delivery, large libraries, oldest supported OS releases and iPad multitasking need separate qualification. The 2026-10-18 Duo audit above adds display-transition and selected accessibility-setting coverage; spoken VoiceOver, RTL and Reduce Transparency remain untested.

## Local HDR fMP4 HLS decode/seek qualification, 2026-10-29

Native PlaybackInfo now takes a fresh VideoToolbox/AVFoundation capability
snapshot for MP4 and fMP4 HLS, with HDR gated by the current output route.
Eligible AV1 Matroska uses server HLS remuxing; Prism's native MKV path remains limited
to qualified H.264/HEVC. An initial format failure permits one explicit
H.264/SDR, stereo-AAC request retaining the source, quality, selected tracks
and resume position. Network, authorization and cancellation failures retain
their normal error behavior.

The repository gate passed: 5,742 backend tests (20 expected skips), 2,754
frontend tests and 656 browser cases. The subsequent AV1/AAC eligibility
correction passed the extended playback-decision suite (103 tests). Both iOS
and tvOS generic simulator application builds succeeded. These checks do not
replace the device-output qualification below.

The final serial Core run passed all 247 tests with five fixture-dependent
skips. The local Linux arm64 image, labeled with source revision `4faeba27`,
passed four production HEVC/AV1 × HDR10/HLG HLS encodes with AAC. Every fragment
passed static HDR metadata verification, and each emitted master contained
the actual codec configuration, resolution, frame rate and PQ/HLG range.
The image check ran without network access as a non-root user with a read-only
root filesystem and disposable temporary storage.

PR #215 merged the native format-recovery path. The full iPhone recovery
journey, iPad Pro 11-inch (M5) recovery journey and the Apple TV recovery journey passed against fresh disposable
fixtures. Each begins with an intentionally undecodable direct response for
the verified public item ID, then makes exactly one H.264/SDR, stereo-AAC
compatibility request. The journeys verify the same media source, selected
quality/tracks and resume position, then observe actual advancing recovered
video; the TV case also records the HLS playback-info panel and dismisses it
through the normal remote path. The fixture boundary regression keeps the
fault disabled during startup verification and proves that it affects only the
verified public direct-stream route, not another item or the HLS master route.

The iPad run exposed a touch-routing defect: the full-screen gesture surface
consumed the visible Info button's tap. The surface now passes the visible top
control band through UIKit hit testing. The recovery journey passed with the
HLS Info panel, normal dismissal and a visually inspected recovered frame.
The iPad test explicitly disables animations in the `LUMINA_UI_TESTING` build
to avoid repeated XCTest animation-idle stalls; shipping builds exclude this
switch, and the iPhone runs retain normal animations.
The final iPhone recovery and surface-gesture journeys each passed one case
with zero failures, skips or expected failures. The latter retains the real
pause/resume clock, physical hide/reveal and paused-seek checks; surface taps
use the center region rather than XCTest's unspecified full-screen hit point.
The routing fixture suite passed 27 cases, and the
separate backend-virtualenv proof-server HTTP contract passed one case. These
are local simulator and loopback results only; they do not qualify physical
HDR display output, HDMI behavior, or hardware decode on an Apple device.

This is a finite host-only AVFoundation delivery check, not a display-output
claim. On the local arm64 macOS 27.0.1 host,
`AVPlayer.eligibleForHDRPlayback` was `false` even though VideoToolbox reported
H.264, HEVC and AV1 hardware decoder availability. That negative HDR-display
gate remains authoritative for native HDR advertisement on this host.

The packet generated four bounded 10-bit sources using the production
`hwaccel._hdr_source` helper: HEVC and AV1, each in HDR10 (PQ) and HLG. It
looped each source through the HDR probe's production software encoder and
CICP/fMP4 HLS arguments, with one-second independent segments and AAC audio.
Production `_verify_hdr_source` and `_verify_hdr_hls` verified the
source and every output fragment before playback: codec, 10-bit 4:2:0 pixel
format, limited range, BT.2020 primaries/matrix, PQ or HLG transfer, and the
MDCV/CLL static metadata. The existing frame/seek harness was then compiled
and pointed at a loopback-only server:

```sh
swiftc -swift-version 6 -O -parse-as-library apple/scripts/benchmark_video_playback.swift \
  -framework AVFoundation -framework CoreVideo -framework Foundation \
  -o /tmp/lumina-video-probe
/tmp/lumina-video-probe http://127.0.0.1:<port>/<variant>/index.m3u8
```

All four media playlists passed actual `AVPlayerItemVideoOutput` delivery,
one-second time advance and a decoded-frame seek past six seconds. First-frame
times were 217 ms (HEVC HDR10), 183 ms (HEVC HLG), 182 ms (AV1 HDR10), and
185 ms (AV1 HLG); seek-plus-200-ms times were 316, 312, 321 and 319 ms,
respectively. These measurements include the probe's BGRA frame readback and
are not renderer performance claims.

The same fixtures also exercised the current server master shape: one variant,
`BANDWIDTH` and `EXT-X-INDEPENDENT-SEGMENTS`, without HDR rendition
attributes. HLG HEVC and AV1 delivered frames, time advance and seek; HDR10
HEVC and AV1 failed before frame delivery (`CoreMedia -12927`). Adding only
`VIDEO-RANGE` made every variant fail URL parsing. A fully described test
master (average bandwidth, bandwidth, video range, codec string, resolution,
frame rate and no captions) retained HLG delivery but the PQ variants failed
with AVFoundation `-11868` / CoreMedia `-17223`, consistent with this host's
negative HDR-display eligibility. [Apple's HLS authoring
rules](https://developer.apple.com/documentation/http-live-streaming/hls-authoring-specification-for-apple-devices/)
require a complete HDR rendition description. The server now derives codec
configuration, resolution, frame rate and video range from the emitted first
fragment and caches them for the master. Masters emitted by this code passed
the same HLG frame/advance/seek checks for HEVC and AV1; PQ retained the expected
negative result on this host. HDR display output still needs qualification on
an HDR-eligible device; fragment decoding on this SDR host does not certify it.

Latest passing result bundles were retained locally outside the source tree under `/tmp/lumina-native-proof-validation`, with validation logs in `/tmp/lumina-native-*.log`; these are temporary evidence, not release artifacts. Reproduce the native checks using [the fixture-server instructions](../scripts/README.md).


## Dolby Vision original delivery and MKV metadata, 2026-10-29

Native direct profiles now declare Dolby Vision separately for the MP4/M4V/MOV
and locally packaged MKV routes. Profile 5/compatibility 0 and supported
Profile 8 compatibility IDs 1 (HDR10 base) and 4 (HLG base) require the exact
runtime codec, level and Dolby Vision route checks described in ADR 0031.
Profile 8.1 requires iOS/iPadOS 27 or the supported tvOS runtime. Generic HDR
eligibility alone grants no Dolby Vision capability. Server HLS encoding and
the one-shot H.264/SDR compatibility profile do not inherit the declaration.
The backend requires complete level/layer/RPU probe facts; probe cache version
6 automatically refreshes older facts. Clients without the extension retain
their prior behavior; a present empty declaration denies that direct route.

| Profile | Local fixture | Direct MP4 | MKV packaging |
| --- | --- | --- | --- |
| 5 / compatibility 0 | Two bounded fragments from Apple's public Advanced HLS example; video copied unchanged, synthetic AAC added for transport checks | Decoded frames, advancing time and seek passed | Normal and external-subtitle master metadata checks passed |
| 8 / compatibility 1 | Generated 8-second Main 10 PQ video, AAC and real generated RPU metadata | Decoded frames, advancing time and seek passed | Same checks passed; `hvc1` primary, Dolby supplemental codec and PQ retained |
| 8 / compatibility 4 | Generated 8-second Main 10 HLG video, AAC and real generated RPU metadata | Decoded frames, advancing time and seek passed | Same checks passed; `hvc1` primary, Dolby supplemental codec and HLG retained |

The MKV contracts inspect every segment: configuration fields equal the source,
each video packet retains an RPU, the complete ordered RPU hash sequence equals
the source, and each fragment can be independently demuxed as a seek target.
Both paths use `NativeVideoSource.prepare` with exactly pinned PrismCore 3.8.1
plus Lumina's minimal final-segment conversion-statistics patch. The patch
publishes the converter's counters after the EOF tail is flushed; its dedicated
tail-corruption regression prevents a final rejected RPU from inheriting the
previous clean segment's verdict. This establishes metadata-conversion
accounting, not full FEL reconstruction or physical Dolby Vision output. A
test-only internal initializer supplies a metadata-capable display description
so that this host can inspect full Dolby masters; production still samples the
actual route. No display capability is forced in shipping code.

The macOS 27.0.1 host reported HEVC/Dolby Vision hardware decoding and positive
exact MP4 queries for Profiles 5 and 8 at levels 1, 7 and 9, while
`AVPlayer.eligibleForHDRPlayback` was false. The successful frame/seek checks
therefore establish native decoding, not physical Dolby Vision presentation.

Fixtures remain outside Git. The generated Profile 8 metadata is a structural
retention test, not reference-graded Dolby Vision content. These local checks do
not certify physical Dolby Vision presentation, dynamic tone-mapping accuracy,
HDMI/Match Content, Atmos, Profile 7 enhancement layers or HDR10+ preservation.
See [reproduction commands](../scripts/README.md#dolby-vision-local-qualification).

The final complete LuminaCore suite executed 251 tests with five skips and zero
failures, with both opt-in Dolby fixture cases enabled. Generic iOS and tvOS
Simulator builds passed. The full backend suite passed 5,764 tests with 20
skips; frontend unit tests passed 2,754 cases. The final `mise exec -- make check`
passed, including all 656 browser cases in 1,066.935 seconds.

## Profile 7 client packaging conversion, 2026-10-29

PR #218 was merged after integrating the episode-controls changes from main and
passing an integrated iOS Simulator build. The continuation adds a separate
MKV-only P7/compatibility-6 to P8.1 packaging declaration. It requires the P8.1
runtime/output capability, a compiled Libdovi converter and complete source
level/RPU/EL/BL facts. Original-preservation profiles, MP4, WebM, server HLS and
the H.264 compatibility profile do not gain P7 support from it.

The pinned engine converts the dynamic metadata, removes enhancement-layer
NALs and copies the base pictures. Both structural MEL and FEL cases passed
native decoded-frame, advancing-playback and six-second seek probes through
`NativeVideoSource`. Metadata contracts inspect normal and subtitle masters,
P8.1 configuration/signaling, expected mode-2 RPU sequences, removed EL,
unchanged base VCL and independently readable seek fragments. A deliberately
malformed RPU before an ordinary segment cut is rejected, and zero-conversion
or zero-EL-removal evidence is treated as unsafe.

The native info row says Profile 7 is converted to 8.1 with the enhancement
layer omitted. Known failures pause playback and can request one compatible
retry after advancement while retaining selected options and position. Snapshot
and source/request identity checks prevent stale conversion evidence from
affecting a replacement source. Validation samples produced-segment counters,
including the EOF tail after the pinned patch; it is not an atomic preflight.

Fixtures use synthetic eight-second video/AAC and hash-pinned upstream RPU test
vectors, not reference-graded or private movie media. They establish structural
conversion and base playback only. Full FEL residual reconstruction, reference
color accuracy, physical Dolby Vision/HDMI/Match Content and separate-track EL
inputs remain unqualified. The Libdovi availability getter was independently
verified to return true in the compiled arm64/x86_64 iOS and tvOS Simulator
objects. See [fixture commands](../scripts/README.md#dolby-vision-local-qualification).

The final complete Core suite executed 254 tests with five skips and zero
failures, with all P5/P8/P7 fixture contracts enabled. Generic iOS/tvOS Simulator
builds passed, as did the full backend suite (5,783 passed, 20 skipped) and
2,754 frontend unit tests. The final `mise exec -- make check` passed, including
all 656 browser cases in 1,080.958 seconds. Final iOS/tvOS builds include the
known-failure guard on rate restoration as well as ordinary playback/rate updates.

### Final partial-segment validation, 2026-10-29

PR #220 was merged after integrating main and passing 256 Core tests (five
opt-in server skips) and both generic native Simulator builds. Its upstream
PrismCore 3.8.1 dependency still omitted conversion-statistics publication at
the final partial segment. The continuation pins the minimal public fork
revision `6cdfae6fc89e42c4758f23005cb6c8152d7499ec`, based on that exact release.
The producer now records the final snapshot after media and renditions land,
before notifying waiting consumers.

The generated negative corpus retains 191 valid RPUs followed by a malformed
last RPU in an eight-second source, plus a two-frame source with a valid first
RPU and malformed last RPU. The host regression failed on upstream with a
stale clean verdict for the long source and no verdict for the short source.
Both now trigger the typed invalid-conversion rejection. Two fork-level
session regressions also passed and require successful, nonempty HTTP delivery
of the terminal fragments. Existing MEL/FEL success cases now require conversion
counts for every base picture, including the final partial segment.

The patched full Core suite passed 257 tests, with five server-fixture skips
and all P5/P8/P7 metadata fixtures enabled. Generic iOS and tvOS Simulator
builds passed. These checks qualify terminal conversion accounting locally;
they do not establish physical Dolby Vision output or full FEL reconstruction.
The complete `mise exec -- make check` gate also passed: 5,783 backend tests
(20 skips), 2,754 frontend tests and all 656 browser cases in 1,084.037 seconds.

### Separate-track Profile 7 packaging, 2026-10-29

PR #222 was merged after integrating main, rerunning the three Profile 7
contracts and building both native Simulator targets. The continuation pins
the public PrismCore fork at `0a26ce14ff0b38b56ff20c2cbd1a23b953d0a62c`.
It recognizes only explicitly complementary P7/compatibility-6 HEVC Main 10
BL-only and EL/RPU-only tracks in legacy MKV. The native profile additionally
opts in with `LuminaDolbyVisionSeparateTrackPackaging: true`; older conversion
declarations do not inherit separate-track support.

Synthetic MEL/FEL pairs contain 192 base and enhancement access units.
Hash-pinned RPUs have scene-refresh changes so incorrect temporal association
is observable. A third fixture uses a B-frame base with a different decode
order from its ordered EL stream. Contracts compare every base picture byte
and converted RPU in BL decode order after matching by PTS, inspect normal
and subtitle masters plus all independent fragments, and verify the declared
AAC rendition retains monotonic decode timestamps. Audio can be in its own
HLS rendition; absence from the video fragment is not evidence of dropped audio.

Malformed final RPUs, missing EL access units, shifted timestamps and ambiguous
companions fail with the typed conversion error. Native error resolution
retains known conversion evidence through AVPlayer failure teardown and uses
it for startup and later recovery; explicit stop/replacement clears it.
Cancellation and direct network errors keep their existing behavior.

The reordered fixture exposed a forced-reanchor stall: EL and B-frame
presentation pre-roll could arrive after the anchor keyframe, after the old
drop condition had cleared. The fixed producer keeps a PTS floor for both
paired streams across the seek. Engine public-session contracts pass after
forced cache eviction, regenerating the head and then the tail, with unchanged
P8.1 initialization and clean conversion statistics. Reopened sources must
retain the same pair and codec configuration.

Local verification passed 18 focused Core checks with all P5/P8/P7 fixtures
enabled and no skips. These include native decoded frames, at least one second
of advancement, and forward/backward seeks for MEL, FEL and the reordered case.
Generic iOS and tvOS Simulator builds passed against the exact fork revision.
The final `mise exec -- make check` passed: 5,799 backend tests (20 skips),
2,754 frontend tests and all 656 browser cases in 1,062.351 seconds. Its initial
attempt ran out of host disk space; the retry passed after removing owned
disposable build/test caches.

This qualifies structural packaging and base playback locally. It does not
qualify unmarked/ambiguous second-video layouts, M2TS dependency-PID pairing,
physical Dolby Vision/HDMI/receiver output, reference color or FEL residual
reconstruction. P7-to-P8.1 still omits enhancement pictures.

### Explicit M2TS Profile 7 pairing, 2026-10-29

The M2TS route requires the separate top-level transport capability and exact
server packaging marker. It accepts 192-byte framing and a CRC-validated PMT
with an explicit DOVI enhancement-to-base PID dependency. No ordinary direct
M2TS row, generic TS/MTS permission or guessed HDMV PID association is added.
Probe cache version 7 rebuilds the new facts. An EL-first PMT uses the proven
base stream for both playback and compact catalog resolution metadata.

Generate the synthetic corpus with `dovi_tool` 2.3.4:

```sh
mise exec -- python3 apple/scripts/generate_dv_m2ts_fixtures.py \
  --dovi-tool /path/to/dovi_tool --output /tmp/lumina-dv7-m2ts-fixtures
```

The generated MEL/FEL and nonconventional EL-first cases use distinguishable
RPUs and a B-frame base stream. Backend qualification runs the real probe
service and decision boundary. Structural descriptor/dependency, CRC,
continuity, scrambling, ambiguity and framing failures have no pair marker;
missing access units, shifted PTS and a malformed final RPU retain signaling
evidence for the engine's packet-level validation.

Native fixture contracts use `LUMINA_DV7_M2TS_DIRECTORY` for generated corpus
paths. They inspect normal and sidecar masters, P8.1 initialization records,
every independent fragment, unchanged base VCL, the ordered converted RPU
sequence and the declared AAC rendition. Separate AVPlayer contracts require
decoded base frames, advancement and forward/backward seeks for all three
positive files, plus a range-served HTTP source. Required pairing refuses
unmarked, dangling-dependency and ambiguous sources before publishing an item.
MPEG-TS PCR seek anchors are not used as video cut points. A first run uses
an EVENT playlist and harvests actual BL packet keyframes; a separately
namespaced cached map enables planned replay and forced cache reanchor.
Cold first-play deep seeks beyond the produced range remain unsupported by
this route and need a later seek-planning improvement. Cached reanchor
correctness uses a head scan plus verified PTS floor; its cost grows with the
seek distance, so these short fixtures do not qualify long-title latency.
These are structural and base-playback checks, not reference Dolby color,
physical HDR/HDMI/receiver output or full FEL reconstruction.

The immutable engine revision is
`a2b6c460775f11201dc39ced66a207013ba47de0`
([source](https://github.com/shiftedx/PrismCore/commit/a2b6c460775f11201dc39ced66a207013ba47de0)).
Its focused checks passed six M2TS session/PMT/cache tests, three live AVIO
failure/state tests, fuzz smoke and existing separate-track MKV MEL/FEL plus
reordered-B-frame regressions. The final Lumina Core run passed 265 checks
with five unconfigured fixture-server skips and no failures; all P5/P8/P7
and M2TS media fixtures were enabled with no media-fixture skips. The full
`mise exec -- make check` passed 5,810 backend tests, 2,754 frontend tests and
all 656 browser cases (1,107.964 seconds for the browser tier). A final backend
rerun after the compact BL metadata correction passed 5,814 tests with 20 skips.
Generic iOS and tvOS Simulator builds also passed against the exact pinned
revision, with signing disabled and no deployment or upload.

### Cold M2TS Dolby Vision seeks, 2026-10-29

PR #225 merged as `0136bfa23fe97fc7d5bee2226ee5f8457dfe27e5` before this
continuation. Its 8-second fixtures could fit within the producer lead window,
so they did not establish cold deep seeking. The new synthetic source is
96.02 seconds / 49,053,696 bytes, with a 4 Mb/s constant transport rate,
nonadjacent EL-first PIDs, B-frame BL, AAC and distinguishable RPUs around the
late target. No source media is committed.

```sh
mise exec -- python3 apple/scripts/generate_dv_m2ts_fixtures.py \
  --dovi-tool /path/to/dovi_tool --output /tmp/lumina-m2ts-cold \
  --cold-seek-duration-seconds 96
# A second corpus uses a 2-second BL GOP; a 5-second segment target then
# places some inferred boundaries between actual IRAPs.
mise exec -- python3 apple/scripts/generate_dv_m2ts_fixtures.py \
  --dovi-tool /path/to/dovi_tool --output /tmp/lumina-m2ts-cold-gop48 \
  --cold-seek-duration-seconds 96 --cold-seek-gop-frames 48
```

The old engine failed the public session check with a nil planned timeline;
its native item had an indefinite duration and only a 20-second seekable
range. The fixed route scouts actual BL IRAPs into an ephemeral partial map,
then validates a nearby real IRAP and its exact-PTS EL/RPU after timestamp
seeking. It never promotes PCR anchors to picture keyframes or silently
forgives unmatched future EL data at EOF. Candidate and verified states are
separate: post-IRAP BL frames remain queued while the matching EL is arriving.

The native workflow uses a unique range-served URL and a paced synthetic
origin. Before playback it requires finite duration and the full seekable
range. It seeks to 75 seconds before normal prefix playback, decodes the
destination and repeats forward/backward scrubs at 88, 68 and 75 seconds.
In the focused local run the first seek rendered in 0.352 seconds with
592,675 compressed packet bytes (0.57 MiB); the source's late anchor is at
byte 38,255,424 (36.5 MiB). That counter excludes transport padding and scout
reads, so the original byte assertion did not establish origin I/O or rule
out whole-source startup preflight. The changing-GOP qualification below
replaces it with the paced origin's sent-byte counter. These remain local
synthetic measurements, not a network/device throughput guarantee.

The final engine source is pinned to
`37148992b891d9a5e587a6aaa0aa56b72cd98b8f`
([source](https://github.com/shiftedx/PrismCore/commit/37148992b891d9a5e587a6aaa0aa56b72cd98b8f),
[engine PR](https://github.com/shiftedx/PrismCore/pull/2)). Its focused source
checks passed 16 tests across seven suites, three fuzz checks, selected-AAC
forced reanchor, and two exact payload checks. The latter compare retained BL
VCL bytes and original-index P8.1 RPUs at 74.083/78.083-second IRAPs, filtering
only source-PTS pre-roll; they also verify no EL/layer leakage and stable init.
A final pin-resolution rerun passed all four native M2TS contracts without skips.

The complete Core run passed 266 checks with five unconfigured fixture-server
skips and no failures, with all P5/P8/P7/M2TS and cold media fixtures enabled.
The full `mise exec -- make check` gate also passed: 5,814 backend tests
(20 skips), 2,754 frontend tests and all 656 browser cases (1,124.184 seconds
for the browser tier).
Generic iOS and tvOS Simulator builds passed against the final immutable pin
after integrating main through `052c2070` (Apple UI fixes #226/#227). Signing
was disabled; no app deployment or upload was performed.
The unseen tail retains the existing partial-map GOP-cadence inference;
insufficient bounded prefix evidence does not establish whole-file seek
coverage. Physical Dolby Vision/color/HDMI qualification and full FEL
reconstruction remain separate.

### Changing-GOP M2TS Dolby Vision seeks, 2026-10-30

PR #228 merged as `adee5d58dea2e157c15063646953f5232ddb1164`. Its head-prefix
map still inferred unseen tail cadence. The strict M2TS route now publishes
VOD only from observed IRAP segment boundaries across the source, with EOF
proof for the final tail. Sparse timestamp seeks locate candidates; they are
not picture evidence. One two-second / 64-MiB AVIO budget covers the complete
scout and its head reset. Incomplete proof retains sequential produced-range
seeking. Partial cached maps cannot bypass this rule; cache namespace v3
isolates the new semantics. Reanchors require the exact advertised IRAP.

The immutable production pin is
`5e6a28a714dc711a811ff678501ff9b5b99378ee`
([source](https://github.com/shiftedx/PrismCore/commit/5e6a28a714dc711a811ff678501ff9b5b99378ee),
[engine PR](https://github.com/shiftedx/PrismCore/pull/3)). The test-only
head `9e4054ba82e5d4e362356c12fc2e419f0314a4ae` has identical `Sources`
and `Package.swift`; it binds the older exact-payload test to the observed
plan, updates strict-cache expectations and locks cancellation, insufficient
budget and source-I/O outcomes independently.

```sh
mise exec -- python3 apple/scripts/generate_dv_m2ts_fixtures.py \
  --dovi-tool /path/to/dovi_tool --output /tmp/lumina-m2ts-variable \
  --cold-seek-duration-seconds 96 --cold-seek-variable-gop-pattern 288,24,72
LUMINA_DV7_M2TS_VARIABLE_GOP_COLD_SEEK_FIXTURE=/tmp/lumina-m2ts-variable/p7-m2ts-variable-gop-cold-seek-mel.m2ts \
  mise exec -- swift test --package-path apple/Core \
    --filter NativeVideoSourceTests.testM2TSProfile7ColdSeekTracksVariableGOPCadence
```

This optional source has ten seconds of one-second GOPs, then 12/1/3-second
GOPs. At target 77 seconds its nearest source IRAPs are 74.083 and 86.083
seconds. Sparse planning legitimately selects a larger 70.083–86.083-second
segment; every advertised start is an actual IRAP and the requested picture
remains inside that segment. The old production revision fails the new
public-session regression with ten non-IRAP boundaries. The new revision
passes, including byte-exact retained VCL vectors and cache-evicted repeated
fetches. The full-vector oracle uses source decode order, excluding the next
anchor and filtering only pre-anchor presentation preroll: 383 retained
pictures match. A presentation-time upper bound would incorrectly include a
picture decoded after the next segment's opening IRAP.

The native workflow uses real AVPlayer output, verifies returned display time,
finite duration/full seek range, forward/backward/repeated seeks, late HTTP
ranges and clean conversion. Its origin counter includes transport framing,
probing, lookahead and socket read-ahead. It replaces the earlier compressed
packet counter, which did not measure those costs. The 64-MiB synthetic
startup-send assertion is a whole-workflow test budget, distinct from the
runtime scout's AVIO budget. First-seek sends must be less than replaying the
entire source prefix; no half-file startup or sublinear-I/O claim is made.

| Synthetic source | Startup | Startup origin sent | First seek + frame | First-seek origin sent |
| --- | ---: | ---: | ---: | ---: |
| 96-second, one-second GOP | 1.636 s | 37,741,360 B | 0.448 s | 9,764,864 B |
| 96-second, changing GOP | 1.882 s | 44,543,536 B | 0.799 s | 19,267,584 B |

These are single local observations with a paced 2-ms/64-KiB origin, not
client-consumed byte counts or remote-network throughput guarantees. Socket
slack and concurrent producer lookahead vary the sent-byte totals.

Focused engine runs passed the seven sparse-map/cold/variable-GOP checks,
two exact-payload checks, 40 association/cache/fuzz checks, and 12 enabled
M2TS/MKV corpus checks without media skips. At that engine test head, the three scout boundary checks
also verify cancellation, byte/time budgets, source I/O and head restoration.
A delayed seekable-input run starts from mid-file, expires the optional proof
after ten milliseconds, and verifies `insufficient` with no required reopen
and the real opening BL IRAP still readable. No production change was needed.

The complete Lumina Core run passed 267 checks with five unconfigured
`NativeServerTests` skips and no failures. All P5/P8/P7, separate-track,
M2TS and cold/variable-GOP media fixtures were enabled, with no media skips.
Unsigned generic iOS and tvOS Simulator builds passed against the immutable
production pin. A final two-case native rerun also passed after requiring
both player position and returned frame presentation time.

Repository validation passed 5,814 backend checks (20 skips) and 2,754 frontend
checks. The second full browser run passed 651 cases; the backend's low-disk
guard refused one conversion and its four dependent WebKit cases did not run.
After reclaiming this task's rebuildable caches, a fresh-fixture rerun passed
all 35 selected setup/performance/WebKit cases (8.4 minutes), including the
previously blocked conversion and all four WebKit workflows. All 656 unique
browser cases passed across these runs; the aggregate `make check` invocation
is recorded as failed, not as a clean full-gate pass. An earlier unchanged
navigation test timeout also passed three isolated repeats and the second
full functional run; no web application or test changes were needed.

Physical Dolby Vision/color/HDMI qualification, arbitrary large-source
cold-seek availability, and full FEL reconstruction remain separate; no
deployment or upload was performed.

## Home, Video, and Audio workflow coverage, 2026-10-20

The native clients open on Home, with separate Video and Audio destinations. Home uses a cinematic artwork hero with an eight-second idle rotation, manual previous/next and title selection, horizontal swiping on iOS, and an explicit Pause control. Automatic rotation stops for Reduce Motion, VoiceOver, an inactive scene, or TV focus interaction. Continue watching, Next up, and recent-video shelves use the authenticated server catalog. The recent shelf can contain movies or episodes; its first visible card is not a fixed fixture item. Authenticated artwork requests support inherited series backdrops while preserving own-art precedence.

Video's All catalog groups shows as Series alongside movies and other video rather than flattening every episode into the first screen. Shows and Anime have separate browse scopes; show → season → episode navigation leads to the video detail and player. Search, sorting, genre/year filters, Unwatched/Favorites filters, and watched/favorite detail actions use the production API and member state. The activity workflow verifies server user data, native switch state, visible filter summaries, and filtered catalog membership, then resets its disposable state. TV category/filter menus are readable native controls, and Account remains available while the catalog is busy.

Audio browses albums, artists, and tracks. A session-owned player retains music when Browse dismisses the full player: the now-playing bar remains available while navigating Video and Audio, and reopening it keeps the same playback position. Stop explicitly ends the session and removes the bar. iOS background audio, Siri Remote Play/Pause on TV, ordered album playback, queue inspection and track selection, Next/Previous, and repeat off/queue/track controls have passing selected workflow coverage below. Browse and Stop share an aligned action row; the TV queue modal is bounded at 1,000 × 650 points with an accessible Done action.

Video uses the player pause policy on backgrounding and stays paused after foregrounding until the user explicitly resumes. Its native AVKit transport owns TV remote handling; the custom Play/Pause handler applies only to audio. This prevents competing video transport actions while keeping music available during browsing and backgrounding.

Audio uses authenticated direct delivery when supported and a bounded, cached server conversion to FLAC for compatibility playback. The shared response MIME resolver serves AAC M4A audio as `audio/mp4`, correcting the `audio/mp4a-latm` response that prevented native asset playback. The fixture checks the exact M4A response type and authenticated stream bytes. Its generated media includes three movies, a show/season/two-episode hierarchy copied from the original synthetic video, and two numbered AAC tracks with album/artist artwork. Public fixture IDs use Jellyfin's 32-hex UUID form; credentials and generated media stay outside version control.

### Shared, server, and repository checks

| Check | Verified result |
| --- | --- |
| Shared Core unit contracts | 14 passed, including inherited series backdrop URLs and own-art precedence |
| Real-server AVFoundation integration | All 3 `NativeServerTests` passed: direct audio, compatible FLAC audio, and direct/HLS video with progress |
| Final generic simulator builds | iOS and tvOS succeeded; `/tmp/lumina-parity-ios-final-build.log` and `/tmp/lumina-parity-tv-final-build.log` |
| Full backend suite | 5,227 passed, 11 skipped |
| Full frontend unit suite | 2,622 passed |
| Final web build, types, and style gates | Passed; strict CSS size 70.5 KiB |
| Retained read-only checks | 20 passed |
| Real-stack checks | Original run: 50 passed, 5 skipped, 2 failed; repaired focused rerun: 4 passed |

These are separate verified runs and retained focused repairs, not one wholly green final `make check` invocation. In particular, the focused real-stack repair result does not turn the original full run into a passing full rerun.

**Final mocked browser checks:** all 659 collected cases across 100 files have retained passing or skipped outcomes across the file batches and focused repairs: 657 passed, 2 skipped. The first batch retained 333 cases (332 passed, 1 skipped). The remaining 58 files ran 326 cases (312 passed, 13 failed, 1 skipped); all 13 failures were covered by the final seven-file rerun, which passed all 120 cases in 10.7 minutes with two workers. The final log is `/tmp/lumina-parity-browser-final-repairs.log`. These are combined coverage results, not a single green full-suite invocation.

### Final UI workflow results

All 11 selected native UI workflows passed across six iOS cases and five TV cases. They use the real disposable server, native sign-in and transport controls, advancing playback clocks, and independent server-state/progress checks. The Home case includes the cinematic hero and shelves; iOS also proves idle rotation, Pause, and a real horizontal swipe. Audio covers persistent browsing/reopening, background or remote transport behavior, queue selection, repeat, and explicit Stop. The table records each retained passing case, since a result bundle may also contain older failures in other cases.

All result-bundle paths below are under `/tmp/`.

| Platform | Passing workflow | Seconds | Result bundle |
| --- | --- | --- | --- |
| iOS | Home/carousel, destinations, featured detail and resumed playback | 146.401 | `lumina-parity-ios-qualified.xcresult` |
| iOS | Direct/HLS video, resume and persisted progress | 138.191 | `lumina-parity-ios-qualified.xcresult` |
| iOS | Persistent audio, background playback, queue/repeat and Stop | 174.337 | `lumina-parity-ios-final-controls.xcresult` |
| iOS | Watched/favorite actions and activity filters | 156.728 | `lumina-parity-ios-final-controls.xcresult` |
| iOS | Show → season → episode and playback progress | 79.355 | `lumina-parity-ios-complete.xcresult` |
| iOS | Pull/search and refresh | 64.605 | `lumina-parity-ios-video-search-final.xcresult` |
| tvOS | Home, destinations, featured selection/detail and resumed playback | 167.715 | `lumina-parity-tv-qualified.xcresult` |
| tvOS | Direct/HLS video, remote transport, background pause and progress | 249.683 | `lumina-parity-tv-qualified.xcresult` |
| tvOS | Show → season → episode and playback progress | 95.916 | `lumina-parity-tv-qualified.xcresult` |
| tvOS | Persistent audio, Siri Remote pause/resume, queue/repeat and Stop | 217.234 | `lumina-parity-tv-final-controls.xcresult` |
| tvOS | Watched/favorite actions and activity filters | 259.249 | `lumina-parity-tv-final-controls.xcresult` |

The older failed video case in `lumina-parity-ios-video-search-final.xcresult` is superseded by the passing iOS qualified video case. Older Audio/Filters failures in `lumina-parity-tv-qualified.xcresult` are superseded by the fully passing TV final-controls bundle. Earlier diagnostic failures remain historical evidence, rather than being counted as additional passing cases. The historical chapters above retain their original scope; the selected results here qualify the Home/Video/Audio snapshot tested in those bundles, not the later extended-audio changes, every native feature or physical device. Reproduction commands and screenshot behavior are in [the fixture README](../scripts/README.md).

All test simulators were shut down after the runs. After confirming that no devices were booted and no native test runners remained, residual orphaned simulator-runtime helpers were also terminated to release host resources. The disposable native media fixture was stopped and its generated credentials file removed. This pass performed no distribution upload or deployment.

### Historical limits before the extended-audio implementation

The following paragraph records the earlier Home/Video/Audio qualification boundary. Its music-feature exclusions are superseded by the implementation update below; its hardware qualification limits still apply.

The native audio queue is in-memory. Navigation and backgrounding preserve it within the current app process, but process termination does not restore the queue, and the native client does not synchronize the member-owned listening queue across devices. Saved/smart playlists and mixes, lyrics, loudness normalization, crossfade, sleep timer, offline downloads, and Sonos output remain web-only. Queue playback does not establish gapless transitions. Direct and FLAC fixture results do not qualify every hardware codec, file type, HDR/HDMI mode, surround/Atmos output, or network condition. Physical-device playback and accessibility checks remain necessary; this is not a claim of complete web-feature parity.


### Extended native audio implementation, 2026-10-20

The implementation now includes saved/smart playlist CRUD, read-only household-shared playlists, and server mixes. Listening queues are member-owned, revision-checked server sessions with explicit conflict resolution and owner-scoped checkpoints; restoration is paused and never automatically begins audible playback. This is synchronization between one member's devices, not a multi-member listening party.

Offline storage downloads sequentially to disk with cancel/retry/remove/clear and explicit sign-out cleanup. Its default owner budget is 1 GiB; each audio file is capped at 128 MiB and artwork at 2 MiB. Metadata manifests exclude credentials and stream URLs. Saved native bytes receive a local playability probe, then WAV fallback if needed; failed authorization never triggers that fallback. Retained sessions can use local audio and cached artwork/lyrics without network access. Apple TV uses purgeable Caches; absence of a cached file is recoverable, not evidence that a download remains available.

Lyrics expose source attribution and timed-line seeking. Manual gain, sequential-album gain or track normalization use peak limiting on the buffered path. Bounded current/next-track 48 kHz stereo PCM schedules transitions on the native audio clock, with crossfade Off/3/6/12 seconds. Unsupported or oversized buffering falls back to source-loudness streaming and labels that limitation. The sleep deadline persists per owner; system interruption/headphone removal pauses playback. Sonos handoff requires acknowledgement, polls output status, supports transport and debounced volume, and requires acknowledged return to local output; local audio effects and sleep controls are disabled while Sonos owns playback.

The enriched lawful fixture adds timed lyrics, gain metadata and a read-only shared playlist. Two additional public UI workflows are enabled on both iOS and tvOS: `testMusicPlaylistsSmartSelectionAndDownloadedPlayback` and `testMusicAdvancedControlsPausedRestoreAndQueueConflict`. Earlier eleven-case results above remain evidence for their original snapshot, not qualification of these additions.

| Extended-audio gate | Current evidence |
| --- | --- |
| Backend full suite | 5,233 passed, 11 skipped |
| Focused native API contracts | 35 passed, then 6 passed in the subsequent focused run |
| Offline Core contracts | 5 passed: disk persistence, budgets, cancel/owner switch, credentials and redirects |
| Music library Core contracts | 5 passed |
| Final Core suite with live fixture | 35 passed, including all 3 live `NativeServerTests` and Episode/Season inherited series-artwork fallback; `/tmp/lumina-audio-parity-core-art-final.log` |
| Real native transport and playback policy | 5 engine + 3 policy cases passed (8 focused cases); `/tmp/lumina-native-music-engine-final.log` |
| Frontend unit suite | Final full run: 2,622 passed across 299 files; the earlier recording-control async fixture repair passed all 14 focused cases |
| Bootstrap, typecheck, web build/style gates | Passed in the final qualification invocation |
| Final real-stack cohort | 51 passed, 5 skipped, 1 Home artwork baseline failure; focused baseline repair: 2/2 passed |
| Final mocked-browser cohort | 656 passed, 2 skipped, 1 catalogue interaction failure; focused dark-desktop Undo repair: 1 passed |
| Final repository qualification | Full cohorts plus retained focused repairs, as described below; **not one green `make check` invocation** |
| Local signed iOS/tvOS test builds | Runtime test builds compiled and ran on both platforms; no distribution archive or upload |
| Current native UI qualification | All eight selected journeys have passing case-level evidence below |
| Responsive Newsreader/layout visual acceptance | Current iOS and TV captures reviewed and accepted; TV album uses the retained accepted capture |


The eight selected native journeys below distinguish current passes from retained case-level evidence. A passing case does not make every other case in its result bundle pass. Older failed or interrupted cases are superseded only by the corresponding later passing journey. Paths below are under `/tmp/`.

| Platform | Journey | Seconds | Qualification/result bundle |
| --- | --- | --- | --- |
| tvOS | Home/featured | 193.410 | Passed; `lumina-audio-parity-tv-final-design.xcresult` |
| tvOS | Audio/remote transport | 211.004 | Retained pass; `lumina-audio-parity-tv-art-ui.xcresult` |
| tvOS | Advanced listening/restore/conflict | 284.025 | Retained pass; `lumina-audio-parity-tv-extended-qualified.xcresult` |
| tvOS | Playlist/download | 229.279 | Passed; `lumina-audio-parity-tv-final-design.xcresult` |
| iOS | Home/featured | 142.445 | Passed; `lumina-audio-parity-ios-final-design.xcresult` |
| iOS | Audio/background playback | 183.975 | Retained pass; `lumina-audio-parity-ios-final-ui.xcresult` |
| iOS | Advanced listening/restore/conflict | 144.629 | Passed; `lumina-audio-parity-ios-advanced-final-design.xcresult` |
| iOS | Playlist/download | 147.760 | Passed; `lumina-audio-parity-ios-final-design.xcresult` |

The TV final-design bundle passed both selected cases. The iOS final-design bundle retained passing Home and Playlist cases; its Advanced menu lookup failed, then passed as the sole case in the focused advanced-final-design bundle after an XCTest-only exact-button lookup repair. No product change occurred for that focused rerun. The retained TV Audio and Advanced cases likewise qualify their individual journeys, not their entire containing bundles.

The latest source removes duplicate Home/Video/Audio root navigation titles on iOS, leaves generic Album/Artist navigation titles blank, and removes redundant TV Video/Audio body headings while retaining actual destination labels. An opaque pinned iOS audio toolbar prevents scrolling content from showing through controls. Crossfade has an explicit caption on both platforms. Playlist creation keeps its queue snapshot in an item-based sheet; track state initializes before metadata awaits, with native editor Save/Cancel toolbar actions. `NativeMusicList` retains inset-grouped lists on iOS and uses a paper/ink `ScrollView`/`LazyVStack` with native focusable controls on tvOS, where `scrollContentBackground` is unavailable. Signed local runtime compilation is confirmed on both platforms; this does not establish a distribution archive or upload.

Local artwork evidence is collected in [the review gallery](/Users/kmcdowell/.codex/visualizations/2026/10/20/01a15766-1f7c-7d21-b521-0604bea3306b/native-parity/extended-audio/review.md). The final iOS Home, Audio, Video, smart-playlist, downloaded-player and focused Advanced-options captures have been personally reviewed and accepted: duplicate root titles are absent, paper/ink surfaces are consistent, the opaque toolbar has no content overlap, and Crossfade is labeled with one active playback channel. Current TV Home, Audio, Video, smart-playlist and player captures are accepted; TV Album acceptance uses the retained earlier capture, not a fresh album image from the final-design bundle. Current iPhone Home/Audio gallery images are present. This closes the selected simulator workflow and visual acceptance scope; physical hardware, codec and Sonos limits below remain. No distribution archive, upload or release was requested or performed by this pass.


The final `make check` invocation in `/tmp/lumina-audio-parity-make-check-qualified.log` passed backend, frontend unit, bootstrap, typecheck and web build/style gates, then failed the Home image performance test's first-viewport baseline. The real-stack cohort retained 51 passing cases and 5 skips. The repaired fixture explicitly disables browser scroll restoration and returns to the top after real scroll/focus checks; it preserves every artwork budget. The focused rerun in `/tmp/lumina-audio-parity-home-images-qualified.log` passed setup and the performance case (2/2). All six cold/warm phases decoded 8 offscreen images against a budget of 10, with concurrency 4 and no image failures or duplicate concurrent/body transfers.

The subsequent full mocked-browser run in `/tmp/lumina-audio-parity-mocked.log` retained 656 passes and 2 skips, with one failure opening the feedback menu for the final dark-desktop catalogue Undo screenshot. A test-only interaction repair hovers the populated card and verifies its expanded, controlled menu before selecting feedback; the Undo assertion remains. The focused `VISUAL_ONLY=home-undo-row` dark-desktop case passed in `/tmp/lumina-audio-parity-catalogue-undo-qualified.log`. These are composed qualification results from full cohorts and focused repairs; neither failed full invocation is reported as a single wholly green command. The selected native UI and visual scope is qualified separately in the case table above.

No new distribution upload or deployment is established by this update. Buffered-engine checks and synthetic media do not establish all codecs, lossless/bit-perfect output, arbitrary gapless/crossfade cases, Sonos hardware behavior, HDR/HDMI/Atmos, accessibility on physical devices, or complete web-feature parity. Retain hardware acceptance checks and record actual output rather than inferring it from a source label.

### Native Account navigation and Home spacing, 2026-10-20

Account is a native navigation destination on iPhone and iPad. Web and Apple TV keep their existing top-right account control. Home no longer reserves an empty navigation toolbar above Welcome home, reclaiming its 44-point strip; its content spacing is also tighter. Account retains native appearance selection and explicit Sign out. Root Home and Account suppress redundant navigation titles; pushed media details restore the system Back bar.

A balanced `NavigationSplitView` supplies separate leading navigation and trailing content at expanded widths. The detail pane retains one `TabView` with stable destination stacks; compact layouts expose native tabs. Expanded panes hide duplicate tabs while the sidebar is present and restore them when the sidebar is hidden. Hero, collection spotlight and album layouts use their actual available content width, including Dynamic Type scaling, instead of assuming that a regular window always provides a wide detail pane. The shared audio coordinator remains outside destination selection.

The iPad audio regression caught during this change was caused by placing the persistent bar inside the detail navigation host: after Browse returned to a pushed track detail, the bar was absent. Expanded layouts now host the bar on the outer library shell; compact layouts attach it to the root and pushed screen content inside the native tabs so its hit area does not overlap destination controls. Only the visible destination exposes the compact bar, and every host shares the same session-owned player. The existing public audio journey verifies background progress, Browse, destination switching, reopening without resetting position, album queue selection/repeat, explicit Stop and independently persisted server progress. The two earlier failing iPad audio bundles are retained as diagnostic evidence and superseded by the passing focused case below.

| Platform | Current selected journey | Result / bundle under `/tmp/` |
| --- | --- | --- |
| iPhone | Account, native appearance values, selected feature, detail and Back; compact Home heading position | Passed, 102.733 s; `lumina-home-nav-iphone-final.xcresult` |
| iPhone | Home hero idle/manual/swipe, destinations, featured detail and resumed video | Passed, 136.025 s; `lumina-home-nav-iphone-final.xcresult` |
| Duo Closed | Account appearance/state and Home/detail/Back | Passed, 101.026 s; `lumina-home-nav-duo-final.xcresult` |
| iPad | Separate trailing content, no duplicate navigation tabs, Account appearance/state and detail/Back | Passed, 102.525 s; `lumina-home-account-ipad-qualified.xcresult` (Account case only) |
| iPad | Persistent audio/background/queue/repeat/Stop after the outer-shell bar fix | Passed, 182.918 s; `lumina-home-nav-ipad-audio-shell.xcresult` |
| iPhone | Persistent audio/background/queue/repeat/Stop with compact insets on root and pushed screens | Passed, 178.561 s; `lumina-home-nav-iphone-audio-screens.xcresult` |

The first three navigation bundles predate the final bar-host correction; the focused audio bundles qualify that correction separately. No full all-feature native rerun is claimed. Original runtime captures and their provenance are in the local [Home navigation gallery](/Users/kmcdowell/.codex/visualizations/2026/10/20/01a15766-1f7c-7d21-b521-0604bea3306b/native-parity/home-navigation/review.md).

Duo Closed is qualified only for its observed viewport. Genuine Closed → Book → Open → Closed motion remains unqualified: Device Hub did not discover the current review devices even after bounded safe UI attempts. No custom screen pose, hinge animation, placement coordinates, or simulated fold screenshot was substituted. Other apps' simulators and shared simulator services were left intact. Every Lumina runtime check used the machine-wide performance lock and at most one Lumina-owned simulator, shut down after its run.

This is a source merge. It does not bump the version/build number or create a TestFlight archive, upload or release. Small UI changes do not trigger distribution; a future distribution candidate requires an explicit request.

The latest required repository invocation, `VITEST_MAX_WORKERS=2 mise exec -- make check E2E_WORKERS=2`, is retained in `/tmp/lumina-home-account-make-check.log`. Bootstrap (13), application/E2E typechecks, production build/license/style gates, backend (5,233 passed, 11 skipped) and frontend units (2,622 tests across 299 files) passed. Real-stack checks finished with 51 passed, 5 skipped and one watch-queue fixture timing failure; all 30 executed performance cases passed. The four-second test video had completed while queue ordering was being inspected, consuming an entry before the reload assertion. The repaired test pauses through editing/reload, then retains its actual autoplay assertion. Its focused rerun passed both setup and queue cases (2 passed; `/tmp/lumina-home-account-watch-repair.log`), and the changed E2E TypeScript check passed.

This is composed qualification, **not a wholly green `make check` invocation**. Make stopped at the original real-stack failure, before the mocked cohort. The previously recorded full mocked cohort (656 passed, 2 skipped, with its one failure covered by the passing focused catalogue repair) remains retained evidence: subsequent product changes are native-only; the latest web change is the queue-test pause repair. No redundant mocked cohort was started for this stopping-point merge. The five current real-stack skips are one unavailable Firefox executable and four `PERF_STRICT=1` reference-host-only cases.

## Playback controls and navigation dogfood, 2026-10-22

The native player now owns one quiet custom transport on iOS and tvOS, with native AVFoundation rendering underneath. This supersedes the earlier AVKit-transport note above. TV Back first dismisses playback settings, then the transport, then playback; at destination roots, Back returns Video, Audio, and Requests to Home, while Home yields to the system. Opening settings focuses its initial category and closing it restores the trigger.

The TV scrim covers the full video surface independently of the control margins, removing the visible rectangular gradient boundary. The toolbar uses one balanced screen-edge inset rather than compounded safe-area padding; contextual Skip/Next actions sit above the timeline. The settings panel is centered and capped at 760 points, with darkened glass, a pinned heading, Done action, and category strip. Label/value rows align fit, quality, speed, and caption appearance choices. TV caption picker rows each define a focus section so the trailing native values remain reachable from leading disclosure/reset actions. iOS subtitle tracks use a horizontal scrolling row, and subtitle timing uses three equal-width actions.

The stream preparation probe uses a separate AVPlayerItem from the item installed in the visible player. This avoids AVFoundation's abort when moving one preflighted item between players, while retaining the requested stream, captions, saved time, and rollback behavior.

Confirmed simulator evidence for this pass:

- Shared package: 111 tests passed, including generated-media integration (`/tmp/lumina-polish-core-final.log`).
- TV navigation/artwork/categories workflow: passed, including destination-root Back to Home and focused Home header (`/tmp/lumina-dogfood-tv-1023-r2.xcresult`, selected navigation case).
- iOS paused MKV quality/subtitle timing: passed, 168.338 seconds (`/tmp/lumina-dogfood-ios-1023-r11.xcresult`).
- iOS paused MKV chapters/playback speed: passed, 153.957 seconds (same result bundle).
- iOS custom transport, audio stream switching, horizontal subtitle choices, appearance selection/reset, dismissal, resume, and Close: passed, 160.455 seconds (`/tmp/lumina-dogfood-ios-1023-r12.xcresult`). The fixture is paused during the long settings walkthrough so its natural ending cannot invalidate the resume check.
- TV balanced edge-toolbar bounds, remote Skip intro, paused exact ten-second seeks into credits, Skip credits, and next-episode playback: passed, 168.481 seconds (`/tmp/lumina-dogfood-tv-1023-r7.xcresult`, selected episode case). Ordinary video seeks now use zero tolerance so keyframe snapping cannot accumulate during repeated skips.

The selected native workflows use generated media and disposable local state. These results do not qualify physical HDMI/HDR/audio output. No archives or uploads are part of this pass.

## TV welcome showcase, 2026-10-22

The TV connection screen uses the same anonymous release showcase as web sign-in. The public feed adds optional tokenized portrait posters and a release section; it still contains no household library, member identity, request history, or request status. Native clients accept only the server's exact signed public-art route, make the discovery request without credentials, and bound the accepted gallery to 12 slides.

When multiple upcoming movie posters are available, they supply the slideshow. Backdrops and portrait images preload together, then dissolve at nine-second intervals. The layout keeps one compact sign-in form, a restrained wordmark, and aligned footer actions for Connection Help and artwork pause. Help owns the setup instructions and restores remote focus on dismissal. Reduce Motion, VoiceOver, and inactive scenes suspend rotation. Three original bundled landscapes appear immediately, before any server information or network access. A known server can replace them with the preloaded release gallery; unavailable remote art retains the bundled gallery. Successful connections remember only the normalized server origin for the next sign-in.

The proof server supplies three original synthetic upcoming releases through the production showcase and art-token/cache routes, with generated images at the network seam. No external metadata or image requests are needed for the simulator workflow.


## Post-build-10 native dogfood, 2026-10-22

The working tree is based on main `b2d7bb6`, preserving subject-aware artwork framing. Build 10 was archived and exported for iOS and tvOS, with signatures, TestFlight entitlements, symbols, release assets and fixture exclusion validated. Those retained packages predate the following dogfood fixes; no replacement distribution archive or upload was created.

Home now uses a full-width wordmark in the leading system header, with Edit Home under Account settings. YouTube session, provider, remote acquisition/cache and browser-only controls were removed from native settings. iOS artwork fills its entire fixed card instead of inheriting the small placeholder stack's bounds. Library posters use an even grid; Requests shelves use uniform dimensions and top alignment. A TV-only label-only button style prevents the full-screen hidden-controls reveal target from drawing the system focus plate over video.

Movie/episode and series/season details now separate the hero, plot, cast, and related titles. Native person credits decode optional identifiers, character roles and signed portrait tags. Related titles use the authenticated existing Similar endpoint, bounded to 12 unique non-anchor results without modifying the library's state. Missing cast/related metadata does not invent content. TV detail actions share the same 64-point button sizing and distinct focus treatment; cast names wrap and related focus enlargement can overflow the rail without clipping.

Retained current evidence:

- iOS Home masthead, uniform Library grid, Account Home editor: passed (`/tmp/lumina-layout-visual-ios-final.xcresult`, selected Home settings case).
- iOS Requests equal poster bounds, discovery, submission and history: passed, 211.552 seconds (same bundle, selected Requests case).
- iOS Account search/save/persistence and absent YouTube controls: passed, 149.496 seconds (`/tmp/lumina-layout-settings-ios-r3.xcresult`, selected Account case; this bundle also contains a superseded failed Home placement case).
- TV full playback settings, auto-hide, audio stream selection, subtitle appearance/reset and focus restoration: passed, 249.118 seconds (`/tmp/lumina-tv-video-wash-r4.xcresult`). The retained hidden-chrome capture shows normal unobstructed test-pattern colors.
- iOS detail action alignment, cast credit, related-title navigation and Back: passed, 60.997 seconds (`/tmp/lumina-details-ios-r4.xcresult`). Final shared-style source also compiled for the iOS simulator (`/tmp/lumina-details-ios-final-build.log`).
- TV final detail action alignment, cast, related-title navigation/Back, wrapped names and unclipped focus render: passed, 65.154 seconds (`/tmp/lumina-details-tv-final.xcresult`).
- Shared Core suite: 119 executed, five server-fixture tests skipped, zero failures (`/tmp/lumina-core-after-details-final.log`). Generated-media transport tests ran; the skipped tests require a separately configured static server fixture.
- Backend full tier: 5,301 passed, 11 skipped (`/tmp/lumina-layout-backend-final.log`). Frontend typechecks/build/style/license checks passed; frontend units passed 2,660 tests across 305 files with two workers (`/tmp/lumina-layout-frontend-serial-final.log`).

The broad browser tier initially recorded 616 passing cases, one production service-worker activation timeout and 33 unexecuted dependent cases. The exact failed offline contract passed unchanged in isolation. An exact follow-up of the 33 unexecuted cases plus required setup finished with 33 passed and one failed in 9.8 minutes: all three WebKit decode/seek cases passed, while the strict unthrottled movie-wall fling reported long-task samples 3, 0, 1 (median 1 against an expected 0), with 60 FPS throughout. That run held the shared performance lock but overlapped an unrelated Xcode/simulator workload; it remains a failed performance observation, not a green browser gate or wholly green `make check` invocation. The follow-up log and failure assertion are retained at `/tmp/lumina-browser-unrun-final-evidence/run.log`. The native workflows use original synthetic media and independent platform fixture brokers. They do not qualify physical HDMI/HDR/audio output. Signed build-10 archives remain intact; only their temporary compiler cache was removed.


## Native dogfood release packaging, 2026-10-23

PR #147 includes the native Home/Requests/Account adapters, media-led browsing,
playback settings, TV music detail surfaces, and compact-player auto-hide. The
final release review also closed the disabled-native-streaming Home bypass and
cancelled TV playback preparation when Back dismisses its detail or collection.
The member-effective Home opt-in contract passed five cases. A deterministic
three-second disposable-server delay qualified both TV Back paths (94.371 s,
`/private/tmp/lumina-tv-back-during-preparation.xcresult`); the delay is broker-keyed
to that test class and is never a production server setting. The broker suite
passed ten tests.

The current iOS Duo Open music workflow passed (111.119 s), and the tvOS music
workflow with paused-clock/resume assertions passed (192.446 s). Shared Core
passed 121 tests with five fixture-dependent skips and zero failures. TV general
video controls, speed/chapters, and the corrected supported-stream timing/quality
workflow have passing focused evidence in the native performance record. These
are workflow checks, not comprehensive performance qualification.

Device Release archives and distribution-signed exports for iOS and tvOS
**0.1.0 (11)** passed local identity, platform, privacy/artwork, fixture exclusion,
framework/signature and entitlement validation. Both signed Organizer archives
have matching application/dSYM UUIDs. The ignored package directory is
`apple/Distribution/0.1.0-11/`; its manifest records source commit
`46f96384a05426e428ff6c74092775e1c73b8f55`, source-input hashes, and IPA checksums.
No upload or deployment is established by packaging. Physical display/audio and
comprehensive device performance remain unqualified.

Final merge qualification ran `make check` with four backend/browser workers.
Backend passed 5,301 tests with 11 skips; frontend passed 2,660 tests across
305 files, plus typecheck, production build, style, license and bundle checks.
The browser run passed 615 of 650 selected cases; two layout fixtures raced the
1.5-second episode's autoplay, preventing 33 dependent cases from running.
The fixture now disables autoplay for those layout assertions while retaining
separate autoplay coverage. Both repaired cases passed, and the exact remaining
33 cases plus setup passed (34/34), including WebKit playback and both movie-wall
performance budgets. Identity-based composition accounts for all 650 contracts
without gaps. This is composed passing evidence, not a single green `make check`
invocation. Logs: `/private/tmp/lumina-merge-make-check-final-r2-20261023.log`,
`/private/tmp/lumina-watch-quality-focused-20261023.log`, and
`/private/tmp/lumina-merge-browser-remainder-20261023/`; composition record:
`/private/tmp/lumina-composed-browser-qualification.json`.

## Episode-led TV details and Home artwork retention, 2026-10-23

TV show details now start with the current or next unwatched episode, with an
inline season selector and equal landscape episode cards. Remote focus previews
an episode's title, synopsis and available source metadata; Play/Resume uses
that selected episode. Cast and related titles remain below the episode rail.
The primary action row sits at the bottom of the hero gradient above seasons.
Detail actions and seasons use text with underlines for focus/selection instead
of filled boxes. Poster focus uses a 1.04 scale instead of a white outline, without animated
movement under Reduce Motion.

The Home reload audit found two independent causes: image lifetimes discarded
decoded artwork, and returning Home recreated the feed/layout models. The tab
roots now own those models, retaining rows while fresh metadata loads. Member,
origin and token changes gate rendering and clear the previous session's state.
The shared decoded-image cache coalesces identical signed rendition requests;
its NSCache eviction targets are 64 MiB and 80 images, with accurate decoded
costs and no retention for a single image over the byte target. These are cache
eviction targets, not a whole-process memory ceiling. Pending work is discarded
on session changes. Ready subject framing is retained for at most 80 rendition
URLs; pending analyses remain retryable.

Retained Core evidence: full suite 123 tests, five fixture skips, zero failures
(`/private/tmp/lumina-episode-home-core-full-r3.log`), followed by focused cache,
framing and Home ownership contracts after the later changes. Repeated and
concurrent image consumers share one request/decode; a cached return adds
neither, changed tags/member URLs load independently, session purge rejects a
pending predecessor, and over-budget decoded images are not retained. Ready
framing is requested once; pending framing retries to ready. Four Home model
contracts cover refresh retention and same-token/member-change isolation.
Focused logs: `/private/tmp/lumina-artwork-cache-focused-r2.log`,
`/private/tmp/lumina-artwork-framing-focused-r3.log`, and
`/private/tmp/lumina-home-layout-ownership-focused.log`.

These deterministic work-count checks do not establish hardware CPU, memory,
frame-rate or latency budgets. The host was under extreme load during this pass.
The existing signed build-11 distribution packages predate these new edits.

The Home featured title now consumes remote Left/Right to browse adjacent
features without rebuilding its focus target; Select opens the displayed title.
Its selection persists at the TV tab root. Back records chronological tab visits,
including revisits, and restores Video/Audio browsing state and saved card focus.
Nested details continue to pop their navigation stack before root tab history.
Explicit episode selection also survives a detail return when its season is
fully watched.

The full repository gate was attempted with `mise exec -- make check
BACKEND_WORKERS=4 E2E_TIER_WORKERS=4`. Typecheck/build/style/license/budget checks,
5,301 backend tests (11 skips), and 2,660 frontend tests passed. The browser tier
reported 608 passes, nine failures and 33 skips out of 650 selected contracts;
failures included settings/shell reachability timeouts, overlay layout and a
Firefox books workflow. No frontend or backend production code is changed by
this native patch. This is not a passing full gate. Retained log:
`/private/tmp/lumina-episode-home-make-check-r2.log`.

Final Core rerun after the cache/session and hero-selection changes: 124 tests,
five skips, zero failures in 109 seconds. Log:
`/private/tmp/lumina-home-cache-core-final.log`. Native warm-return HTTP metrics
have not been collected; the active UI fixture broker was not metrics-enabled.

## Native library alphabet browsing, 2026-10-23

TV sorting now leads the video toolbar as a quiet `Title A–Z` menu with an
underline focus state, replacing the isolated gold capsule. Its right-edge
alphabet scrubber expands during rapid scrolling or remote focus. Up/Down
previews an anchor; Select requests its page; Left/Back returns to the grid.
iOS offers a narrow fast-scroll edge scrubber that commits on release and an
explicit alphabet menu for accessible navigation. Search and non-title sorting
do not expose an alphabet index.

The additive connected-app `/Lumina/Library/alphabet` endpoint returns no more
than 27 member-visible absolute anchors in the same filtered SortName/ID order
as `/Items`. A jump replaces the current window with one 50-item page at that
index; subsequent pagination continues from the correct absolute offset. It
never traverses earlier pages or downloads their artwork. The initial catalog
page renders before the optional index request finishes. An older server still
loads its ordinary library; the letter controls require the updated backend.

Retained focused evidence: three backend alphabet contracts, 56 existing
Jellyfin integration cases (`/tmp/lumina-alphabet-integration.log`), and three
Core alphabet contracts passed. Coverage includes filter/parent/category/member
parity, SortName ties, absolute pagination, latest rapid jump wins, unavailable
index fallback and catalog rendering while the index request remains pending.
Both native simulator test builds passed at r14; the selected-episode workflow
passed at r13, including its fresh-session resume phase. Later Home/alphabet
navigation qualification is tracked separately; these builds alone do not prove
remote interaction or device performance budgets.

Final combined Core suite after alphabet and cancellation repairs: 127 tests,
five expected fixture skips, zero failures in 103 seconds
(`/tmp/lumina-alphabet-core-full-r2-20261023.log`). Missing legacy page offsets
fall back to the requested offset, and canceled loads/jumps cannot publish a
late page or error. Home concurrency checks continue to measure only the Home
endpoints; optional Library index traffic is outside that retained contract.

The focused TV alphabet workflow passed on the final container fix at r22
(`/private/tmp/lumina-tv-library-alphabet-r22.xcresult`, 86.75 seconds). It checks
left-leading sort, the rail's physical right-edge placement, remote letter
selection, indexed page/card focus, Left back to the grid, and hiding/restoring
the rail with non-name/name sorting. The rail's identifier belongs to its button;
`libraryScreen` belongs only to the concrete scroll view, avoiding transparent
parent accessibility-identifier propagation. Temporary count-only diagnostics
were removed. Existing Jellyfin API regression suite also passed: 66 cases
(`/private/tmp/lumina-alphabet-api-regression.log`). Together with the 56
integration and three alphabet cases this supplies 125 focused backend passes.

Final r23 signed iOS and tvOS simulator test builds both passed
(`/private/tmp/lumina-episode-detail-ios-r23-build.log`,
`/private/tmp/lumina-episode-detail-tv-r23-build.log`). The TV alphabet workflow
passed again (85.1 seconds; `/private/tmp/lumina-tv-library-alphabet-r23.xcresult`).
The focused Back-history workflow passed (74.0 seconds;
`/private/tmp/lumina-tv-back-history-r23.xcresult`), covering a nested title pop
and Home → Video → Audio → Video → Back Audio → Back Video → Back Home.
Home title focus, Right/Left browsing, Select and Back selection preservation
passed within r17 before a later remote-play readiness failure in the broader
journey. That broad journey was not fully requalified. iOS alphabet gestures
have compile/API/Core coverage here, without a completed touch-simulator
workflow or physical-device timing/memory qualification. No archives or uploads
were created for these edits.

The welcome slideshow and shared featured carousel no longer expose pause/resume
buttons. Their unused manual-pause state was removed; Reduce Motion, VoiceOver,
scene activity and carousel interaction guards remain. Existing UI workflows now
check that those controls are absent and welcome rotation continues.

Category/section navigation now shares a plate-free underline style: TV video
categories, Audio browse tabs, TV Requests browse links, music navigation links,
and playback-settings tabs on iOS/tvOS. Video Filters follows YouTube on the
same toolbar; active criteria remain available in its accessibility value and
filter form. Down from a category or Filters enters the poster grid directly.
Both signed simulator test builds passed. The focused TV Back-history and
alphabet/toolbar workflows passed (2/2), including same-baseline Filters after
YouTube, left-leading sort and right-edge alphabet placement. Result:
`/private/tmp/lumina-underline-toolbar.xcresult`; visual capture:
`/private/tmp/lumina-underline-toolbar-captures/32E3C9B5-C569-4604-89D4-A1A2A3AEFDC0.png`.

### Home navigation and media-first browsing (2026-10-23)

Home now exposes native Search and conditional offline-music Downloads, with
customization remaining in Account. The underlined All/Movies/Shows/Anime/Watchlist
row and Genres menu refine loaded Home items without replacing Library pagination
or changing saved shelf order. Home-only server metadata supplies exact category
and inherited series genres; older responses remain decodable. The default order
now starts Continue watching, Next up, then Live across the shared layout contract;
explicit member orders remain unchanged.

The hero adapts to available height, keeps Play/Resume and Watchlist near the dark
edge, and uses one contextual line. Its title/artwork opens details. Successful
watchlist writes refresh the Home feed; rotation waits for those writes and pauses
for 30 seconds after manual carousel interaction. Offscreen visibility stops its
rotation task. Episode rails lead with series, then season/episode/title and subtle
progress; caption slots scale with text size. The Home browsing row scrolls away,
the iOS masthead compacts after a threshold, and the native Duo rail is retained.
TV Home refinements live at the navigation root, so Back restores them. Both Home
search implementations have bounded results and cancellation/stale-result guards.
Hero watchlist status still uses the existing per-item authenticated lookup; this
pass does not claim zero network requests on every carousel selection.

Validation:

- Final iOS and tvOS signed simulator test builds passed:
  `/private/tmp/lumina-home-design-ios-final.log` and
  `/private/tmp/lumina-home-design-tvos-r8.log`.
- Duo Closed Home workflow passed (filter changes, series/episode captions,
  watchlist add/remove verified through the API, native search and result):
  `/private/tmp/lumina-home-browse-ios-r8.xcresult`.
- TV Home workflow passed (filter changes, Video-to-Home Back retaining selection,
  concise resume captions, Home Search opening and Back dismissing its sheet):
  `/private/tmp/lumina-home-design-tv-r8.xcresult`.
- Duo Open reached and passed the filtering/watchlist steps, then XCTest stalled
  waiting for animation idle around its focused search field/keyboard. The owned
  run was stopped after repeated 60-second framework waits; it is not a passing
  complete Open workflow. Evidence:
  `/private/tmp/lumina-home-browse-ios-open-r8.xcresult` and
  `/private/tmp/lumina-home-browse-ios-open-r8-search-idle.png`.
- Focused Home Core contracts: 6 passed; Home backend contracts: 5 passed.
  Full backend: 5,304 passed, 11 skipped. Full frontend repeat: 2,660 passed.
  Home editing browser workflows: 13 passed, including reset to the new default
  and persistence of an existing member order.
- The full Core run executed 129 tests (5 fixture skips) with one live-audio timing
  failure; that unchanged audio case passed when repeated alone. `make check`
  stopped at a gallery observer race in the frontend suite; that file passed all
  11 tests alone and the subsequent complete frontend run passed. The complete
  browser release gate was not requalified in this pass.

No distribution archive, deployment, or upload was performed. These simulator
checks do not establish physical-device timing or a universal memory/CPU budget.

### 2026-10-23 — Device settings and original-quality defaults

This pass adds device-local video quality, audio/subtitle language, subtitle mode,
and the existing portable caption appearance controls to native Account settings.
The TV settings page uses an opaque, full-screen two-column layout with Lumina's
underline selection/focus treatment; its choice and caption pages use matching
rows. iOS retains native settings navigation. Native preferences share the app's
UserDefaults store, including its disposable UI-test namespace.

Original/Automatic sends no client bitrate or height ceiling. Playback selects
the highest-resolution direct-compatible returned source rather than the first
source. Administrator global/member limits and the aggregate household upload
budget remain authoritative. New resource policies start with unlimited remote
stream/member allowances; explicitly saved policies are preserved. This does not
change an already deployed server's saved configuration.

An initially selected external caption on progressive MP4 explicitly requests
compatible server HLS (ADR 0031), while Matroska retains local packaging. Manual
playback selections override device defaults. The video settings page shows the
AVPlayer presentation resolution when available.

Validation completed during implementation:

- iOS and tvOS arm64 simulator build-for-testing succeeded:
  `/private/tmp/lumina-device-settings-ios-redesign.log` and
  `/private/tmp/lumina-device-settings-tvos-redesign-r2.log`.
- Twelve focused Core checks passed across preference persistence/normalization,
  source selection, playback requests and real local/HLS subtitle packaging:
  `/private/tmp/lumina-device-settings-core-final.log` (11) and
  `/private/tmp/lumina-device-settings-best-source-final.log` (1).
- The source-selection regression failed against the original first-source
  behavior and passed with ranking restored:
  `/private/tmp/lumina-source-quality-red.log` and
  `/private/tmp/lumina-source-quality-green.log`.
- Focused bandwidth-policy/backend contracts: 29 passed. Full backend:
  5,305 passed, 11 skipped. Full frontend: 2,660 passed.
- The revised TV layout was visually reviewed from the simulator:
  `/private/tmp/lumina-tv-device-settings-redesign-live.png`.

Physical Apple TV 4K/HDR output and receiver output are not established by these
simulator and request-policy checks. No distribution archive, upload or production
deployment was performed in this pass.

The first TV interaction run caught a real nested-Back bug: Menu from Caption
Appearance dismissed the entire settings cover. Settings now owns one typed
navigation path and one Back handler (pop one route, then close at root), with the
underlying library Back handler disabled while covered. The corrected TV build
passed: `/private/tmp/lumina-device-settings-tvos-back.log`.

The full `make check` browser phase was not green: 607 passed, 10 failed, 33 did
not run out of 650 selected (`/tmp/lumina-playwright-tier.dQC2dq/full-report.json`).
Its new Household Resources compact-height regression was corrected by folding
help into existing copy; the latest-source focused desktop settings visual case
and three HouseholdResources tests passed. Other browser failures include a
watch-web focus-ring assertion, CLS/a11y/theme/keyboard workflows, fixture timeouts
and Playwright step-ID errors; they are not claimed fixed by this pass.

The final TV device-settings workflow passed 1/1 (333 seconds), verifying saved
quality/audio/subtitle/caption preferences across relaunch, caption-to-settings
Back, and settings-to-Account Back:
`/private/tmp/lumina-device-settings-tv-back.xcresult` and
`/private/tmp/lumina-device-settings-tv-ui-back.log`.
Both the initial Original-quality page and the persisted Subtitles page were
exported and visually reviewed from that result bundle.

The Duo device-settings journey was not qualified: after relaunch, XCTest stalled
on a stale existence lookup and a long hierarchy capture; the eventual hierarchy
contained the expected `Video quality, Up to 720p` control. The settings-only wait
now re-queries identifiers per poll, preserving the value assertions. Standard
phone validation is recorded separately below when available. An earlier full
playback-settings UI journey also stopped on a non-focusable popup-label target;
its menu helper now targets the actual native option cell, but that complete
playback journey has not been requalified in this pass.

Standard iPhone 17 / iOS 27.0 device-settings validation passed 1/1 (194 seconds),
including relaunch persistence and iOS Caption Appearance Back:
`/private/tmp/lumina-device-settings-iphone.xcresult` and
`/private/tmp/lumina-device-settings-iphone.log`. Its persisted-settings screenshot
was visually reviewed. The installed iOS 27.1 runtime offered Duo profiles only,
so the compatible standard-phone runtime was used; this does not convert the
incomplete Duo run into a pass.

Final review also caught a redundant PlaybackInfo request when explicit stream
choices were already present in the first request. The existing request workflow
regression failed with two calls, then passed with one after the fix. All twelve
focused Core checks passed together afterwards:
`/private/tmp/lumina-device-settings-repeat-request-red.log` and
`/private/tmp/lumina-device-settings-core-green.log`.

After removing the redundant request, both final arm64 simulator builds passed:
`/private/tmp/lumina-device-settings-ios-complete.log` and
`/private/tmp/lumina-device-settings-tvos-complete.log`. `git diff --check` passed.
The disposable fixture broker was stopped after all native checks completed.

### Build 12 merge and package qualification, 2026-10-23

The browser gate is now qualified by composed evidence, not a single green
`make check` invocation. The original full tier selected 650 identities: 607
passed, 10 failed, and 33 were blocked by project dependencies. All ten failures
passed final-source focused reruns, including the 12-route CLS run. The exact
blocked cohort returned 33 passed / 1 failed including setup; it caught an eager
Live discovery request introduced during the CLS repair. Restoring Live's near
gate fixed that regression, and setup plus the Home request-budget test passed
2/2. Thus all 650 original identities are accounted for across these runs.

Home now waits for its hero and visible Next Up loading frame to settle before
painting, keeps the unpainted subtree inert, and bypasses the Next Up wait when
that shelf is hidden. Frontend typecheck, the production build, and the three
HouseholdResources unit tests passed after the frontend repairs.

Evidence retained locally:
- `/private/tmp/lumina-release-browser-composed-qualification-20261023.json`
- `/private/tmp/lumina-release-original-browser-failures-focused-20261023.log`
- `/private/tmp/lumina-release-final-cls-20261023.log` (12/12)
- `/private/tmp/lumina-release-browser-remainder.JrtlKD/results.json`
- `/private/tmp/lumina-release-home-budget.L48K7t/run.log` (2/2)
- `/private/tmp/lumina-release-final-frontend-build-20261023.log`

Both iOS and tvOS **0.1.0 (12)** Release device archives passed local archive
validation. Xcode's existing account signed local internal TestFlight exports
using its cloud-managed distribution certificate after command-line export
reported “No Accounts.” Both signed IPAs and staged Organizer copies passed
signature, identity, privacy, artwork and fixture-exclusion checks; executable
and debug-symbol UUIDs match. The ignored release directory retains provenance,
141 native build-input hashes, IPA checksums and this composed browser evidence.
No build 12 upload or production deployment was performed. The Duo, complete
playback-settings and physical 4K/HDR/HDMI/audio limits above remain in force.


## Duo dogfood: artwork, controls, and receiver output, 2026-10-27

The pending dogfood batch adds native video and music AirPlay/Chromecast output,
server conversion for native video receivers, and scoped music receiver queues.
Receiver credentials are separate from native app credentials. A valid native
video ticket recreates an idle-reaped HLS session on the next receiver request,
including its selected audio and subtitle tracks. Cast hardware qualification
remains outstanding; simulator checks do not establish receiver compatibility.

iOS detail heroes now occupy the first viewport. The Seasons action scrolls to
the existing collection, and synopsis/supporting detail follows below. Artwork
uses localized bottom shading; pushed details omit the Lumina masthead while
Home, Library, and Requests retain their navigation branding. Video controls use
a shared inset, centered transport, and grouped output actions, with Lumina SVG
assets and plain 44-point targets. Playback remains owned above adaptive
presentation; the layout changes do not replace the retained player/session.

Focused checks during this pass: 14 Core Chromecast client/handoff tests, nine
backend Chromecast tests (including renewal after idle reaping), 68 music receiver,
audio, device-lifecycle and schema tests, and the shared 144-asset icon contract
passed. The iOS Release simulator build and these native workflows passed:

- Closed Duo, 466×678 points: full-viewport hero, Seasons reveal, episode playback,
  centered transport/output group, common utility baseline, and chrome dismissal.
- Open Duo, 951×669 points: the same artwork and player alignment workflow.
- Open Duo: music output controls, background pause/restore, and queue conflicts.
- Open Duo: retained direct and client-demux playback sessions across repeated
  presentation rebuilds, with advancing playback and no replacement session.

The final open layout run is `lumina-dogfood-open-grid-r6.xcresult`; closed layout
is `lumina-dogfood-artwork-grid-r5.xcresult`. Music and retained-session results are
in `lumina-dogfood-open-qualified.xcresult` (that bundle's earlier overlay assertion
failed before the footer inset correction; it is superseded by the r6 overlay run).
These are focused simulator checks, not a full repository gate or receiver hardware
qualification. No archive, signed export, or TestFlight upload was made.


## Mark/wordmark baseline audit, 2026-10-27

Horizontal web lockups now align the sunrise's painted bottom to the capital L,
accounting for transparent SVG padding rather than centering the image canvas
against the text line box. The tiny shared brand stylesheet serves login,
navigation, onboarding, reader headers, and the standalone offline entry; the
latter retains its original JavaScript imports and italic typography. Its L's
font-relative ink overshoot is included in the optical correction. The shared
CSS budgets include this stylesheet and grew by 0.1 KiB to retain a measured cap.

All four native paired lockups (iOS/tvOS connection and root navigation) use the
same painted-edge baseline modifier, including aspect-fit padding. The separate
email PNG uses table-cell baselines; a rendered synthetic email measured a zero
pixel difference between the mark and word's lower painted edges. No approved
image geometry, colors, launcher assets, or playback code changed.

Focused validation: WebKit and Chromium sign-in/offline lockups at 390 and 720
points passed rendered-ink alignment checks; 14 branding/auth/offline unit tests,
11 email tests, frontend typecheck/build/style budgets, iOS/tvOS Release simulator
builds, and the native welcome/help/manual sign-in workflow passed. Native login
was also visually inspected from `lumina-brand-native-welcome.xcresult`.

The existing standalone offline-reload browser workflow fails after switching
the browser context offline. The same Chromium failure was reproduced with all
three modified web entry/style sources served verbatim from unchanged HEAD using
a temporary Vite load overlay. This is retained as a separate baseline failure;
no service-worker or playback changes were made to hide it. Logs:
`/tmp/lumina-brand-offline-chromium-r3.log` and
`/tmp/lumina-brand-offline-baseline.log`. The temporary overlay configs were removed.
No distribution archive, signed export, or upload was made.


## Episode thumbnail playback and artwork alignment — October 29, 2026

Source after staged build 35 removes the duplicate selected-episode title and
Play/Resume block. Tapping an episode card directly starts/resumes that exact
episode. The artwork card's outer leading edge aligns with Episodes, the season
selector and About text; interior text padding remains unchanged. Leading
restoration and trailing scroll room keep the final card on that same gutter.

The focused `NativeEpisodeShowContextTests/testEpisodeControlsStayAlignedAndPlayWithoutReturningToHero`
workflow passed on iPad Pro 11-inch (M5) and iPhone 17 simulators with iOS 27.0.
Each opens a saved episode from Home, checks card-edge alignment against all
three text anchors and the absence of duplicate controls, resumes from eight
seconds, switches seasons, and taps an episode thumbnail. Decoded playback time
and server progress verify the requested episode. Captured layout screenshots
were inspected on both layouts. Results and source hashes are retained in
ignored `output/episode-thumbnail-fix/` (`ipad-r8.xcresult`, `phone-r8.xcresult`).
No full suite, new distribution package or upload was performed; signed build 35
remains unchanged and predates this correction.


## Home header scrolling and TV remote-only video controls — October 29, 2026

On tvOS and iPad, the Home header is now part of the scroll content: logo,
categories and actions leave the viewport with the top of the page. Library
headers remain pinned. tvOS adds a bounded top gradient for readable categories
over bright hero artwork and preserves its safe-area clearance. The regular
TV video overlay no longer exposes a close icon; remote Back retains dismissal,
exit and episode-focus restoration. The existing Duo navigation paths and iOS
video close controls remain in place.

Three focused Release simulator workflows passed:

- Apple TV 4K (2nd generation), tvOS 27.0:
  `NativeTVLibraryAlphabetTests/testHomeHeaderScrollsAwayAndLibraryCategoriesStayPinned`
  checks that Home's header disappears over shelves, Up restores it, category
  selection opens Library, and Library categories stay fixed during real poster
  scrolling. Result: `output/home-navigation-scroll/tvOS/proof-r3.xcresult`.
- The same TV's
  `NativeTVEpisodeRemoteNavigationTests/testEpisodeCardSelectStartsPlayback`
  verifies decoded playback beyond three seconds, pauses to keep the controls
  visible, asserts no close icon, and exits using remote Back. Server progress
  and restored episode focus pass. Result: `tvOS/proof-r5.xcresult` in that folder.
- iPad Pro 11-inch (M5), iOS 27.0:
  `NativeLibraryCatalogScrollingTests/testIPadHomeHeaderScrollsAwayAndLibraryCategoriesStayPinned`
  verifies Home's header scrolls away, category taps still open Library, and the
  Library row remains fixed while the original poster scrolls out of view.
  Result: `iOS/proof-r5.xcresult` in that folder. This layout workflow uses the
  existing test-only static-animation switch to avoid animated sign-in idle
  waits; shipping animations are unchanged.

Both builds passed. Home, scrolled Library and visible TV controls screenshots
were inspected, including the top gradient over bright synthetic artwork.
Source hashes and attachments are retained beside the results. No full suite,
physical-device qualification, new distribution package or upload was performed.
These source changes postdate the signed build-35 packages.


## Episode autoplay and server-owned presence policy — October 30, 2026

Web, iOS and tvOS continue a show's episodes using the existing next-episode
resolver. With the default policy, the manually started episode plus two
successors play before a Still watching confirmation blocks episode four.
Continue starts another batch; Go Home or an expired wall-clock deadline stops
playback and returns Home. Member autoplay-off remains authoritative. Admin
Settings → Media can disable the check, change the episode limit (1–100), or
change the response timeout (1 minute–24 hours). Players refresh this policy at
playback/episode boundaries. Schema 42 adds the durable settings JSON column;
the operator guide documents its additive rollback.

Focused qualification:

- Backend policy serialization, admin authorization and bounds, native/member
  parity, migration, and media contract: 259 passed.
- Web overlays, episode workflows and admin form: 51 passed; TypeScript passed.
- Selected Core session/settings tests: 3 passed, including custom limits,
  disabled checks, timeout and continued batches. Broker checks: 27 passed.
- Both Debug simulator test builds passed after incorporating main's newer
  playback dependency. The selected Apple TV 4K (2nd generation, 1080p/tvOS
  27.0) workflow passed: direct thumbnail playback, automatic episodes two and
  three with advancing decoded time/server progress, episode four untouched,
  confirmation, then remote Go Home. Screenshot review found and corrected an
  inherited white-tint contrast problem; the final focused Continue action is
  readable. Result: `output/episode-autoplay-presence/tvos-policy-final.xcresult`.
- The final selected iPad Pro 11-inch (M5/iOS 27.0) workflow also passed with
  server policy support and the same episode/progress/Home assertions:
  `output/episode-autoplay-presence/ipad-policy-final.xcresult`. A separate
  unattended default-policy observation captured the confirmation and Home
  320 seconds later with no GUI input between captures. This observation
  preceded configurable policy support; `idle-observation.json` records that
  distinction. Custom durations are covered by deterministic Core/web checks.

Evidence, logs and screenshots remain in ignored
`output/episode-autoplay-presence/`. The iPad journey uses the existing test-only
static-animation switch; shipping animations are unchanged. No full suite,
physical-device qualification, production deployment, packaging or TestFlight
upload was performed for this feature. The previously uploaded build 36 does
not contain it.


## tvOS Home carousel entry — October 30, 2026

`NativeTVHomeNavigationPreservesSelectionTests/testNewHomeCarouselsEnterAtLeadingCard`
reproduced the owner's focus report on a second-generation Apple TV 4K
1080p/tvOS 27.0 simulator. With fresh metadata and no prior row history, Down
from the hero's right-hand watchlist action focused Continue watching's second
card. The baseline failed its first-card assertion; its screenshot and
accessibility hierarchy confirm that the first card remained visible at the
left edge, ruling out an initially shifted carousel.

The shared Home rail now uses a user-initiated default focus: its first item on
initial entry, or its last visited item when that item still exists. The same
remote workflow then passed both entry cases (hero to first row, right-hand
card to the next new row), Left/Right movement, Up restoring the previous row's
third card, and detail Back restoring that same card. The focused screenshot
was inspected and the tvOS simulator test build passed. Evidence remains in
ignored `output/tvos-carousel-entry/` (`baseline.xcresult`, `fix.xcresult`, and
`final.xcresult`). No full suite, physical Apple TV qualification, new release
package or upload was performed. This source fix is not in uploaded build 36.


## Branded Resume / Start Over chooser — October 30, 2026

Explicit video playback with positive saved progress now offers Resume, Start
Over and Cancel before a player starts. The shared native presentation boundary
covers heroes, movie details, episode thumbnails and downloaded playback. Its
Lumina chooser uses the approved sunrise mark, Newsreader title, adaptive
paper/ink surfaces and existing neutral button styles. Resume receives default
remote focus; TV Back cancels. A scrollable panel accommodates shorter screens
and larger text. The player is presented only after the chooser dismisses.
Start Over requests a zero-position stream with the same source, quality,
audio/subtitle selection and compatibility mode. Cancelling leaves history
untouched; cancelled or superseded restart tasks cannot open another player.

Web explicit movie/episode entries use fresh progress, retain the themed Dialog
and add the approved mark, editorial title and saved-time context. Item/history
fetches remain parallel. Unplayed conversions can still adopt a warmed session;
Start Over waits for obsolete speculative work to stop. Unavailable history
leaves browsing intact with a retry message. Automatic episode transitions,
music and explicit web timestamps retain their existing behavior.

The new native workflow first failed on baseline because the saved episode
started without a choice. Both TV movie/episode workflows then passed through
the final branded chooser: Cancel preserves the seeded 24-second position,
Resume plays from that position with advancing time, Start Over advances from
zero and reports the restarted timeline, and fresh server progress bypasses the
chooser even when the detail's displayed metadata is older. Final results:
`output/playback-resume-choice/tvos-branded.xcresult`. Both simulator test builds
passed. The TV three-episode autoplay/presence workflow also passed during this
feature (`tvos-autoplay.xcresult`). Web routing and warm-up checks passed 120
cases across two focused files; affected resume workflows passed again after
visual polish, and TypeScript passed. Web desktop dark/phone light and native
TV chooser screenshots were inspected.

Evidence is retained in ignored `output/playback-resume-choice/`, plus the web
screenshots `output/resume-web-desktop.png` and `output/resume-web-phone.png`.
The iPad journey uses the existing test-only static-animation flag and real
coordinate taps for visible playback controls; this avoids AX hittability and
chrome-hide races while retaining public user input. No full suite, physical
hardware qualification, packaging, upload or deployment was performed. This
feature requires updated app/web clients; it adds no backend API or schema.
The previously uploaded build 36 does not include it.
