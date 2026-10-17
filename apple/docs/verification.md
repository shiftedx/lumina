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

## Repository regression checks

Running `make check` exposed two existing regressions, fixed separately in [worker shutdown #66](https://github.com/shiftedx/lumina/pull/66) and [watch-tool selection #68](https://github.com/shiftedx/lumina/pull/68). The full backend suite passed 5,011 tests with 11 skips. After the web fix, the remaining check targets passed bootstrap, typecheck, production build, 2,478 frontend tests, and 47 real-stack/performance tests with 4 skips.

The complete mocked-browser run passed 582 tests with 2 skips; six screenshot/artifact writes failed when the shared Mac ran out of disk space, and four catalogue sweeps hit development-server navigation timeouts. After cleanup, all ten affected cases passed across focused reruns: two library-channel cases, two accessibility route sweeps, two resource/activity cases, and all four catalogue sweeps against the same compiled production bundle. The catalogue assertions and timeouts were unchanged. This is combined coverage, not a claim that one final `make check` invocation exited successfully.

## Limits of this evidence

The simulator does not establish HDR10 or Dolby Vision output, HDMI Match Content behavior, frame-rate switching, surround sound, Atmos, remote-network reliability, or App Store readiness. Use the [physical device checklist](device-validation.md) to record those results. The proof does not claim Infuse format parity.

The tests cover generated SDR H.264/AAC sources. HEVC/HDR, additional audio codecs, subtitle delivery, large libraries, accessibility settings, oldest supported OS releases, iPad multitasking, and Duo display transitions need separate qualification. The Mac was locked when GUI access to Device Hub was attempted, so display-transition testing could not be completed through those controls.

Latest passing result bundles were retained locally outside the source tree under `/tmp/lumina-native-proof-validation`, with validation logs in `/tmp/lumina-native-*.log`; these are temporary evidence, not release artifacts. Reproduce the native checks using [the fixture-server instructions](../scripts/README.md).
