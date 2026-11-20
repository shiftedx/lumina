# Music Rework Release 1 qualification

[Music Rework rollout issue](https://github.com/shiftedx/lumina/issues/355) · [Shared workflow contract](music-home-and-queue-workflows.md) · [Performance baseline](music-home-baseline.md)

## Disposable fixture

Build the production frontend and launch a new #343 proof server per mutating browser or simulator journey. Run the shared server verifier before using its resulting queue for a frontend smoke. Use freshly provisioned queues for the offline and mix journeys. Fixture manifests and response credentials stay local.

| Surface | Verification |
| --- | --- |
| Server | `scripts/e2e/verify_music_server.py` for web and native contracts. `scripts/e2e/verify_finamp_instant_mix.py` for Finamp-shaped requests. |
| Web Chrome | `frontend/scripts/verify-music-home.mjs`, `verify-music-mix.mjs`, `verify-music-continuation.mjs`, `verify-music-offline-resume.mjs` against separate fixtures. Include 1440×900 desktop and 390×844 phone viewports. |
| iOS | `NativePhoneMusicHomeTests` against enabled, disabled and older servers. Use iPhone and regular-width iPad. Run `NativeMusicBaselineTimingTests/testMusicBrowsePlayAndNextTiming` with the baseline runtime. |
| tvOS | `NativeTVMusicHomeTests` against enabled, disabled and older servers. Run the timing test from the same class as iOS. Record Siri Remote workflow on an Apple TV. |

Native offline and purge proofs require the test app built with `LUMINA_UI_TESTING`. Pass `LUMINA_APPLE_PROOF_FIXTURE` to the selected scheme and set `SWIFT_ACTIVE_COMPILATION_CONDITIONS` to `$(inherited) LUMINA_UI_TESTING`. Confirm the offline screen before accepting downloaded playback as evidence.

Capability comparison servers use the same #343 fixture. The enabled and disabled servers use the candidate build with `LUMINA_MUSIC_REWORK_ENABLED=true` and `false`. The older server uses `a453323`, where connected-app Audio and checkpoints exist and the Home capability endpoint returns 404.

## Browser timing checkpoint

Chrome 157, Apple M4 Max, five loopback samples per viewport. Values are median milliseconds. The candidate measurement includes Home, mix controls, continuation and offline resume. [Raw samples](evidence/music-home/web-release-1.json).

| Surface | Browse ready | First audio | Manual Next |
| --- | ---: | ---: | ---: |
| Desktop baseline → candidate | 877.9 → 823.8 | 169.3 → 124.6 | 96.3 → 85.4 |
| Phone baseline → candidate | 827.4 → 822.2 | 153.9 → 135.1 | 84.6 → 85.2 |

Both web cohorts met the #343 median regression budgets. Beta p95 cohorts remain open.

## Native timing checkpoint

The first passing Apple TV simulator run with the Browse Music shortcut measured **25,971 ms** Browse, **14,394 ms** First Audio and **9,150 ms** Next. The corresponding #343 samples were 21,169 ms, 11,884 ms and 8,168 ms. Browse and First Audio exceeded the 15% threshold on these single samples. Next was within the threshold. Collect matched repeats on a quiet host before qualifying the budget. [Apple TV timing markers](evidence/music-home/tvos-release-1.json).

The iPhone 17 simulator emitted 23,525 ms Browse, 46,146 ms First Audio and 15,841 ms Next under concurrent qualification work. XCTest stalled after Stop and the case was interrupted. Keep those markers diagnostic. A passing iPhone timing run with a successful Stop is still required.

In a later iPhone 17 diagnostic run using coordinates where XCTest reported visible controls as non-hittable, Browse took 23,119 ms and First Audio took 4,301 ms. XCTest could not activate Next and the diagnostic test changes were removed. This run does not qualify the iPhone timing budget.

## Staging gates

1. Complete [Finamp mobile verification](finamp-compatibility.md), [physical-device qualification](../../apple/docs/device-validation.md) and the full repository check on the release revision. Record iPhone/iPad VoiceOver and Dynamic Type, phone background/lock, AirPlay and Cast receiver checks, Apple TV Siri Remote focus and Play/Pause, and disconnected retained and purged downloads.
2. Start the candidate web/server rollout with `LUMINA_MUSIC_REWORK_ENABLED=false` and a pinned [rollback image](../../docker/README.md#retaining-a-rollback-image). Record the image digest, server version and release source revision. Confirm normal music browse, controls and queue checkpoints.
3. Enable for dogfood. Verify Home, preview/append/replace, playlist save, album download, offline resume and reconnect conflict. Repeat disable and re-enable with the queue active and check the existing layout, queue, playlists and local downloads.
4. Stage the matching iOS and tvOS TestFlight builds using [the distribution runbook](../../apple/docs/testflight.md). Record build numbers and tester availability separately. Complete the retained-image rollback verification and attach the evidence to #355.

## Physical-device evidence

| Hardware | Record |
| --- | --- |
| iPhone and iPad | VoiceOver speech and traversal through Home and mix actions, including shelf reordering. Verify large Dynamic Type and the active queue and mini-player through size changes. |
| iPhone | With playback active, exercise device lock, foreground return and lock-screen controls. Cold-launch in airplane mode, confirm the saved queue stays paused, explicitly play the download, then reconnect after a competing edit. |
| AirPlay and Chromecast receivers | On iPhone, confirm acknowledged playback transfer and return. Verify continuation does not append while the receiver owns playback. |
| Apple TV and Siri Remote | Use directions, Select and Back to edit Home, resume the paused queue and verify originating focus. Exercise Home/foreground and hardware Play/Pause. Cold-launch disconnected with retained audio, then record recovery after a cache purge. |

Record the tested revision, app build, device and OS, output route, network state, observed queue and saved position. Keep receiver and server addresses, account identifiers and credentials out of issue evidence.

## Functional evidence and remaining checks

- The #348 enabled, disabled and actual older-server journeys exercised iPhone 17 and Apple TV 4K. Disabled and older builds retained browse, playback and queue checkpoints on both platforms.
- iPhone Home shelf editing and explicit saved-position Resume passed. On iPad A16 at 820 × 1180 points, `testHomeLayoutAndBrowse` passed hide, reorder, reset and album browse. At the Accessibility Large Dynamic Type setting, the iPad editor displayed all five shelf labels and actions. AXe reordered Downloads, verified the new order and reset the shelves. VoiceOver speech and active playback through regular/compact size changes remain pending. [iPad editor evidence](evidence/music-home/ipad-accessibility-large-shelves.png).
- iPhone `testContextualMix` passed, including save with unchanged session revision and preview controls after foreground return. `testOfflineHomeResume` passed after Music Home Resume began validating the same cached member checkpoint as its offline card. The test confirmed paused launch, explicit Resume and “Downloaded audio”. Native reconnect conflict remains pending.
- Apple TV passed shelf editing, explicit Resume, focus return from the player, Track Radio dismissal and forced-offline cold resume plus simulated file purge. The exact `testBrowseShortcutReturnsFocus` run fails on tvOS 27.0. Menu leaves album details and returns to Audio, but focus does not return to the album. Two experimental corrections mounted the album and exposed Home's Resume as the focused element after Back. Those production changes were reverted. The test retains a hierarchy and focus diagnostic for the next attempt. Physical Siri Remote checks remain pending.
- Chrome fixture journeys passed Home, contextual mix actions, a real two-browser continuation revision conflict, complete album download and offline cold restart with reconnect conflict resolution. The disabled Home deep link preserved browse and the existing queue.
- Finamp 1.0.2 passed Track InstantMix, playback reporting, original download and offline cold playback on iOS simulator and Android 15 emulator. Album and artist InstantMix also passed on the Android emulator. Both clients showed a disabled Lyrics control against the standard route's 404. [Compatibility matrix](finamp-compatibility.md).

## Repository checks

- Flutter-built Finamp and the Lumina Apple simulator runs are separate from the repository gate. The final tvOS source compiled for the generic simulator destination.
- Backend full check after the Music view fix passed 5,971 tests with 20 allowed skips. A subsequent full run reported 5,971 passes, 20 skips and one caption-enrichment scheduler failure. The scheduler passed two focused retries. The final Finamp library, original-file and feature-flag checks passed.
- Frontend typecheck, production build, CSS budget and 2,877 unit tests passed. The broad Playwright tier reported 739 passes, two failures, two skips and 34 cases that did not run. The metadata editor and real-stack household failures each passed in focused reruns. The full repository gate remains open until a complete run passes.
