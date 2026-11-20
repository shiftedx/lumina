# Music Rework Release 1 qualification

[Music Rework rollout issue](https://github.com/shiftedx/lumina/issues/355) · [Shared workflow contract](music-home-and-queue-workflows.md) · [Performance baseline](music-home-baseline.md)

## Disposable fixture

Build the production frontend and launch a new #343 proof server per mutating browser or simulator journey. Run the shared server verifier before using its resulting queue for a frontend smoke. Use freshly provisioned queues for the offline and mix journeys. Fixture manifests and response credentials stay local.

| Surface | Verification |
| --- | --- |
| Server | `scripts/e2e/verify_music_server.py` for web and native contracts. `scripts/e2e/verify_finamp_instant_mix.py` for Finamp-shaped requests. |
| Web Chrome | `frontend/scripts/verify-music-home.mjs`, `verify-music-mix.mjs`, `verify-music-offline-resume.mjs` against separate fixtures. Include 1440×900 desktop and 390×844 phone viewports. |
| iOS | `NativePhoneMusicHomeTests` against enabled, disabled and older servers. Use iPhone and regular-width iPad. Run `NativeMusicBaselineTimingTests/testMusicBrowsePlayAndNextTiming` with the baseline runtime. |
| tvOS | `NativeTVMusicHomeTests` against enabled, disabled and older servers. Run the timing test from the same class as iOS. Record Siri Remote workflow on an Apple TV. |

Capability comparison servers use the same #343 fixture. The enabled and disabled servers use the candidate build with `LUMINA_MUSIC_REWORK_ENABLED=true` and `false`. The older server uses `a453323`, where connected-app Audio and checkpoints exist and the Home capability endpoint returns 404.

## Browser timing checkpoint

Chrome 157, Apple M4 Max, five loopback samples per viewport. Values are median milliseconds. The candidate measurement includes the Home capability probe and mix controls.

| Surface | Browse ready | First audio | Manual Next |
| --- | ---: | ---: | ---: |
| Desktop baseline → candidate | 877.9 → 823.3 | 169.3 → 125.3 | 96.3 → 87.1 |
| Phone baseline → candidate | 827.4 → 825.6 | 153.9 → 140.4 | 84.6 → 89.1 |

Both cohorts met the #343 median regression budgets. Release qualification still requires the native timing runs and beta p95 cohorts.

## Staging gates

1. Complete [Finamp mobile verification](finamp-compatibility.md), [physical-device qualification](../../apple/docs/device-validation.md) and the full repository check on the release revision. Record iPhone/iPad VoiceOver and Dynamic Type, phone background/lock, AirPlay and Cast receiver checks, Apple TV Siri Remote focus and Play/Pause, and disconnected retained and purged downloads.
2. Start the candidate web/server rollout with `LUMINA_MUSIC_REWORK_ENABLED=false` and a pinned [rollback image](../../docker/README.md#retaining-a-rollback-image). Record the image digest, server version and release source revision. Confirm normal music browse, controls and queue checkpoints.
3. Enable for dogfood. Verify Home, preview/append/replace, playlist save, album download, offline resume and reconnect conflict. Repeat disable and re-enable with the queue active and check the existing layout, queue, playlists and local downloads.
4. Stage the matching iOS and tvOS TestFlight builds using [the distribution runbook](../../apple/docs/testflight.md). Record build numbers and tester availability separately. Complete the retained-image rollback verification and attach the evidence to #355.
