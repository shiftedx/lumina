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

## Limits of this evidence

The simulator does not establish HDR10 or Dolby Vision output, HDMI Match Content behavior, frame-rate switching, surround sound, Atmos, remote-network reliability, or App Store readiness. Use the [physical device checklist](device-validation.md) to record those results. The proof does not claim Infuse format parity.

The tests cover generated SDR H.264/AAC sources. HEVC/HDR, additional audio codecs, subtitle delivery, large libraries, oldest supported OS releases and iPad multitasking need separate qualification. The 2026-10-18 Duo audit above adds display-transition and selected accessibility-setting coverage; spoken VoiceOver, RTL and Reduce Transparency remain untested.

Latest passing result bundles were retained locally outside the source tree under `/tmp/lumina-native-proof-validation`, with validation logs in `/tmp/lumina-native-*.log`; these are temporary evidence, not release artifacts. Reproduce the native checks using [the fixture-server instructions](../scripts/README.md).
