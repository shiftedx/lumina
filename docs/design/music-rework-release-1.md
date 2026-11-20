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

## Staging gates

1. Complete [Finamp mobile verification](finamp-compatibility.md), [physical-device qualification](../../apple/docs/device-validation.md) and the full repository check on the release revision. Record iPhone/iPad VoiceOver and Dynamic Type, phone background/lock, AirPlay and Cast receiver checks, Apple TV Siri Remote focus and Play/Pause, and disconnected retained and purged downloads.
2. Start the candidate web/server rollout with `LUMINA_MUSIC_REWORK_ENABLED=false` and a pinned [rollback image](../../docker/README.md#retaining-a-rollback-image). Record the image digest, server version and release source revision. Confirm normal music browse, controls and queue checkpoints.
3. Enable for dogfood. Verify Home, preview/append/replace, playlist save, album download, offline resume and reconnect conflict. Repeat disable and re-enable with the queue active and check the existing layout, queue, playlists and local downloads.
4. Stage the matching iOS and tvOS TestFlight builds using [the distribution runbook](../../apple/docs/testflight.md). Record build numbers and tester availability separately. Complete the retained-image rollback verification and attach the evidence to #355.
