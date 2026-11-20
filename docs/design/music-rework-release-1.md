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

After the Browse focus correction in `939ba44`, `testMusicBrowsePlayAndNextTiming` passed on the same tvOS 27.0 simulator and #343 fixture. **22,481 ms** Browse, **12,573 ms** First Audio and **8,412 ms** Next were within 15% of their respective baselines. This is one qualifying simulator sample. Physical-device and beta p95 cohorts remain open. [Updated Apple TV timing markers](evidence/music-home/tvos-release-1-focus-fix.json).

On iPhone 17 / iOS 26.5, `testMusicBrowsePlayAndNextTiming` at `f7ff414` passed in 113.104 s, including Stop. The timing harness tapped the visible Play and Next buttons by coordinate when XCTest reported them unhittable. Browse was **23,249 ms**, First Audio **5,441 ms** and Next **4,724 ms** under concurrent host workloads. Relative to the recorded #343 samples of 13,242 / 4,599 / 4,334 ms, Next was within 15%, while Browse and First Audio exceeded their limits. [iPhone timing markers](evidence/music-home/ios-release-1.json).

## Staging gates

1. Complete [Finamp mobile verification](finamp-compatibility.md), [physical-device qualification](../../apple/docs/device-validation.md) and the full repository check on the release revision. Record iPhone/iPad VoiceOver and Dynamic Type, phone background/lock, AirPlay and Cast receiver checks, Apple TV Siri Remote focus and Play/Pause, and disconnected retained and purged downloads.
2. Start the candidate web/server rollout with `LUMINA_MUSIC_REWORK_ENABLED=false` and a pinned [rollback image](../../docker/README.md#retaining-a-rollback-image). Record the image digest, server version and release source revision. Confirm normal music browse, controls and queue checkpoints.
3. Enable for dogfood. Verify Home, preview/append/replace, playlist save, album download, offline resume and reconnect conflict. Repeat disable and re-enable with the queue active and check the existing layout, queue, playlists and local downloads.
4. Stage the matching iOS and tvOS TestFlight builds using [the distribution runbook](../../apple/docs/testflight.md). Record build numbers and tester availability separately. Complete the retained-image rollback verification and attach the evidence to #355.

Local packaging preflight passed for iOS and tvOS Release device archives from `939ba44` and independently passed again from `f7ff414`. The unsigned archives used validation build `0.1.0 (355)`. The archive validator checked identity, device platform, artwork, privacy manifest, symbols, and exclusion of test fixtures and bundles. Export signing and TestFlight staging remain open.

## Physical-device evidence

| Hardware | Record |
| --- | --- |
| iPhone and iPad | VoiceOver speech and traversal through Home and mix actions, including shelf reordering. Verify large Dynamic Type and the active queue and mini-player through size changes. |
| iPhone | With playback active, exercise device lock, foreground return and lock-screen controls. Cold-launch in airplane mode, confirm the saved queue stays paused, explicitly play the download, then reconnect after a competing edit. |
| AirPlay and Chromecast receivers | On iPhone, confirm acknowledged playback transfer and return. Verify continuation does not append while the receiver owns playback. |
| Apple TV and Siri Remote | Use directions, Select and Back to edit Home, resume the paused queue and verify originating focus. Exercise Home/foreground and hardware Play/Pause. Cold-launch disconnected with retained audio, then record recovery after a cache purge. |

Record the tested revision, app build, device and OS, output route, network state, observed queue and saved position. Keep receiver and server addresses, account identifiers and credentials out of issue evidence.

## Disposable container rehearsal

On November 20, `ae3714a` built from the repository Dockerfile as Linux arm64, with source label `ae3714a86a3963a7d0c4f77f6aa5512d74df1dc3`, version `0.7.1` and local image ID `sha256:fd772ab101b2c8e3368c9f8e5274609b1436ca604de700db6527c4f20cac07aa`. The packaged model smoke downloaded and verified its pinned fixtures, returned 384-dimensional embeddings and transcribed the synthetic audio sample.

1. Booted the published `0.7.1` predecessor against a fresh #343 volume. Retained its exact image in a stopped rollback pin with health checks, restart, networking and mounts disabled.
2. Recreated the isolated Compose service with the candidate and `LUMINA_MUSIC_REWORK_ENABLED=false`. Chrome signed in, resolved the Home deep link to legacy browse and retained the checkpoint.
3. Enabled Music Home, customized shelves, resumed and paused the queue, and downloaded both fixture album tracks. The saved checkpoint retained duplicate occurrences and the Wait entry.
4. Disabled the feature again. The stored queue revision, entries, cursor, position and shared playlist survived. Chrome's standalone offline page still played the saved download. Enabling again restored the member layout and exact checkpoint.
5. Restored the pinned predecessor through the normal entrypoint with the same fixture volume. The service became healthy and preserved the queue and shared playlist. Chrome opened the album catalog.

This was a loopback Compose rehearsal on disposable data. The production image, dogfood setting and TestFlight builds are tracked in #355.

Repeated on the release candidate `939ba44`, Linux arm64 image `sha256:c966a60d3f1f6c96a1af9bdbded7a4442286e6773ef1a6893755347cdd347d3e`. The disabled-first, feature toggle, download retention and rollback sequence all passed on a fresh #343 volume.

## Functional evidence and remaining checks

- The #348 enabled, disabled and actual older-server journeys exercised iPhone 17 and Apple TV 4K. Disabled and older builds retained browse, playback and queue checkpoints on both platforms.
- iPhone Home shelf editing and explicit saved-position Resume passed. On iPad A16 at 820 × 1180 points, `testHomeLayoutAndBrowse` passed hide, reorder, reset and album browse. At the Accessibility Large Dynamic Type setting, the iPad editor displayed all five shelf labels and actions. AXe reordered Downloads, verified the new order and reset the shelves. VoiceOver speech and active playback through regular/compact size changes remain pending. [iPad editor evidence](evidence/music-home/ipad-accessibility-large-shelves.png).
- iPhone `testContextualMix` passed, including save with unchanged session revision and preview controls after foreground return. `testOfflineHomeResume` confirmed paused launch, explicit Resume and “Downloaded audio” after Music Home Resume began validating the same cached member checkpoint as its offline card.
- On iPhone, `f7ff414` repaired a race where compatibility Home loading invalidated the concurrent Library navigation response. A fresh #343 run reached Music Home after switching offline → online and relaunching. The focused Core race test and 52 adjacent navigation, transport and music tests passed. Queue conflict resolution still requires a complete reconnect proof.
- Apple TV passed shelf editing, explicit Resume, focus return from the player and Track Radio dismissal. For Audio-grid album focus, `939ba44` retains the grid while presenting details and keeps the standard Now Playing bar inside the presentation. On fresh #343 fixtures with tvOS 27.0, `testBrowseShortcutReturnsFocus` passed in 57.957 s, `testHomeAndLegacyBrowsePlayback` passed in 117.280 s and `testContextualMixReturnsFocus` passed in 66.676 s. Physical Siri Remote checks remain pending.
- On the same TV runtime, `testOfflineMusicHomeRestoresDownloadedQueue` passed in 140.798 s on a fresh #343 fixture. The test downloaded both tracks, cold-launched with forced offline mode, confirmed the restored queue stayed paused, explicitly resumed local audio, purged the selected file and verified the unavailable message and absence of Resume. Physical disconnect and cache lifecycle checks remain pending.
- On the same TV runtime, `testContinuationOptInSurvivesHomeAndNaturalEnd` passed in 130.403 s against a fresh #343 server. The simulated remote paused the seeded track before editing Listening Options, enabled continuation, resumed and returned from system Home. The server confirmed advancement to an appended contextual recommendation after natural completion. Regression coverage is in `93f250a`.
- Chrome fixture journeys passed Home, contextual mix actions, a real two-browser continuation revision conflict, complete album download and offline cold restart with reconnect conflict resolution. The disabled Home deep link preserved browse and the existing queue.
- Finamp 1.0.2 passed Track InstantMix, playback reporting, original download and offline cold playback on iOS simulator and Android 15 emulator. Album and artist InstantMix also passed on the Android emulator. Both clients showed a disabled Lyrics control against the standard route's 404. [Compatibility matrix](finamp-compatibility.md).

## Repository checks

- Flutter-built Finamp and the Lumina Apple simulator runs are separate from the repository gate. The final tvOS source compiled for the generic simulator destination.
- The full gate started at `04dc5fa`. The first backend run had 5,969 passes and zero failures, but three generated M2TS corpus skips were rejected. With the corpus configured, four workers reported 5,970 passes, 20 expected skips and failures in child-process timeout and caption bulk admission. Two workers reported 5,971 passes, 20 expected skips and one Jellyfin original-art fallback failure. All three cases passed their isolated retries.
- The November 20 backend full rerun passed 5,972 tests and 12 subtests with 20 expected skips, using the generated M2TS corpus and two workers. The local archive-signing regression also passed.
- Frontend typecheck, production build, CSS budget and 2,877 unit tests passed. The broad Playwright run reported 775 passes, zero test failures and two rejected skips due to missing Firefox. After installing Firefox, the books journey passed with its setup. The existing Firefox transcode case failed twice at 0.1 seconds of playback against the greater-than-one-second assertion. The full repository gate remains open.
- The November 20 Linux ARM64 run used the official Playwright 1.55.1 image. The unchanged Firefox and WebKit media cases passed. A separate Firefox check with identical packaged Jellyfin FFmpeg 8 output still stalled at 0.1 seconds on macOS, while the Linux runtime decoded it. The initial Linux full tier selected 777 cases and reported 734 passes, eight failures, one executed skip and 34 blocked cases. Three cross-platform UI failures passed unchanged on Linux and macOS after `42484a9`. The other failures were in the Linux books and photo fixture environment.
- The second Linux full tier used the official browser runtime with compatible archive and sandbox tools. Books, Photos and media decoding passed. Of 777 selected cases, 740 passed, three failed and 34 did not run. All three failures passed unchanged in serial focused reruns. The palette test passed its original 50 ms warm-focus budget on the quieter host. Bootstrap (18 tests) and browser runner (three tests) checks passed.
