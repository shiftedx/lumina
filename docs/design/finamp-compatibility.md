# Finamp compatibility trial

[#354](https://github.com/shiftedx/lumina/issues/354) covers the Finamp compatibility trial. Track InstantMix and offline playback passed on both platforms. Timed lyrics and additional seed results are listed below.

## Source and environment

The official [Finamp 1.0.2 release](https://github.com/finamp-app/finamp/releases/tag/1.0.2), source commit `e27c7b328f7937a479a1a3ba041e41df378d6e2b`, was inspected on November 19, 2026. Its [API definition](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/jellyfin_api.dart) calls `GET /Items/{id}/InstantMix` with `userId`, `limit`, `enableImages`, and `enableImageTypes`; its [helper](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/jellyfin_api_helper.dart) reads the query result's `Items`. The [player](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/audio_service_helper.dart) starts those items in server order. This is source evidence, not captured client traffic.

The official macOS 1.0.2 binary launched on the test host, but automation of its UI failed with macOS assistive-access error `-1719`. No macOS app login or playback was observed. The release did not provide an iOS simulator binary, so the iOS trial used a debug simulator build of the tagged source.

A subsequent isolated simulator-build attempt used the release's pinned Flutter 3.47.7, locked package dependencies, CocoaPods 1.16.2 and Xcode 27.1. The first build failed because the `flutter_discord_rpc` build requires `rustup`, which was absent. A separate Rust installation under `/tmp` resolved that prerequisite; the retry reached Xcode compilation but was stopped when the combined SDK, package caches, source, Rust toolchain and Xcode DerivedData reached approximately 5.32 GiB, above the 5 GB attempt limit. After the scratch limit was raised to approximately 8 GiB, the incremental debug simulator build succeeded. AXe 1.8.0 was already installed and drove a dedicated iPhone 17 simulator running iOS 26.5. Neither Android `adb` nor an Android emulator was found on the host; no SDK licenses were accepted or system permissions changed.

Finamp's [lyrics API declaration](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/jellyfin_api.dart) uses `GET /Audio/{itemId}/Lyrics`. An authenticated protocol replay for the shared fixture's audio track returned HTTP 404 on November 19, 2026. This is a reproducible compatibility gap for the Jellyfin route; it does not establish how the Finamp UI presents the failure. Reproduce against the disposable fixture with a connected-app token and the manifest's `audioTrackID`, without recording the token or response headers.

| Capability | iOS | Android | Evidence |
| --- | --- | --- | --- |
| Login and browse | Passed on simulator | Passed on emulator | Candidate Music view and fixture tracks visible |
| Timed lyrics | Blocked | Blocked | Online Lyrics control disabled on both. Standard Jellyfin lyrics route returns 404 |
| Offline download and playback | Passed on simulator | Passed on emulator | Original track, app cold restart with fixture stopped |
| Playback and reporting | Passed on simulator | Passed on emulator | Player clock advanced and server Start, Progress and Stopped were observed on both |
| Track InstantMix | Passed on simulator | Passed on emulator | Both clients constructed a two-track queue and played the first track |
| Album/artist InstantMix | Protocol replay | Passed on emulator | Both Android seeds played with two-item queues |

## iOS simulator result

On November 19, 2026, the dedicated iPhone 17 simulator launched the 1.0.2 debug build, selected the disposable #343 loopback server, and authenticated with the fixture owner. Finamp then presented **Select Music Libraries** with **No Music Libraries** and no selectable library. [Sanitized simulator screenshot](evidence/finamp/ios-no-music-libraries.png). A separate authenticated `GET /UserViews` replay on the same fixture returned collection types `movies` and `tvshows`, with no `music` view. Finamp's [view selector](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/screens/view_selector.dart) requires a music view. At this baseline, the client could not reach browsing, timed lyrics, offline downloads, playback reporting or InstantMix. The retest after adding a member-scoped music view is recorded below.

## iOS retest after music-view and original-audio changes

A fresh #343 fixture incorporated the visibility-scoped music view from `2059287` and the original-audio and artist mapping subsequently committed as `1f35ffc`. The same Finamp 1.0.2 debug simulator build authenticated, displayed the fixture albums and tracks, opened **Signals from a Quiet Observatory**, and played its first track with the player clock advancing. The server trace recorded `/Items/{id}/File` and Playing, Progress and Stopped reports. Artist metadata appeared correctly on the later candidate.

Finamp's track **Instant Mix** action produced a two-track queue, identified the first as the track-mix seed, and advanced its player clock. The server trace recorded `/Items/{id}/InstantMix`; this verifies the tested track seed, not album or artist InstantMix playback.

The client downloaded the first track using **Download original** and stored an AAC file in its application sandbox. With Finamp's Offline Mode enabled, the fixture server was stopped and its port verified closed. After terminating and relaunching Finamp, the downloaded track was selected and played, with its clock advancing beyond six seconds. [Sanitized offline playback screenshot](evidence/finamp/ios-offline-playback.png). This demonstrates a single simulator's cold app restart with the server unreachable, not a physical-device or Android result.

The fixture's first track has timed lyrics on Lumina's native extension, but Finamp's Lyrics control was disabled. The standard Jellyfin lyrics request returned 404 in the separate protocol replay above, so timed lyric display remains blocked. The earlier **No Music Libraries** screenshot records the pre-fix baseline, not the result after `2059287`.

## Android emulator result

The official Finamp 1.0.2 universal APK (`1.0.2+202`, SHA-256 `094f56265e159fc84f910402b2cc456f629e93fb82e6c843e8277ca9a5fa3aa6`) ran on Android 15 / API 35 ARM64 against Lumina `04dc5fa`. ADB reverse connected the emulator to the disposable loopback #343 fixture.

Finamp signed in, listed the Music library and started Track InstantMix from the first fixture track. Android MediaSession reported a two-track queue and active playback. Finamp downloaded the original track and artwork. After enabling Offline Mode, the fixture was stopped and its port confirmed closed. Finamp was force-stopped, reopened and played the downloaded AAC track from local storage. MediaSession reported active playback at 18,876 ms and Finamp displayed “Locally Playing”. [Sanitized Android screenshot](evidence/finamp/android-offline-playback.png).

On a fresh fixture, album and artist InstantMix each started a two-item queue and advanced the playback clock. Server records showed the initial progress row, an updated position at 59 seconds, and a completed playback history row after a normal track transition. While online and playing the first fixture track, the Lyrics control remained disabled and tapping it left the player unchanged.

## Server contract and replay

The route handles track, album and artist seeds using the existing visibility-scoped contextual mix, returning Jellyfin Audio DTOs and at most 100 entries. A track seed comes first, then related tracks; album results retain the contextual mix's disc ordering. Unsupported, missing and hidden seeds return an empty query result. Client `limit` can reduce the returned count. Genre and collection seeds remain empty and are not claimed supported.

Start the [disposable #343 fixture](music-home-baseline.md#run-the-shared-server), then run:

```sh
mise exec -- backend/.venv/bin/python scripts/e2e/verify_finamp_instant_mix.py "$LUMINA_NATIVE_FIXTURE"
```

On November 19, 2026 this passed against the loopback server: Finamp-shaped requests for track, album and artist seeds, Audio DTOs, seed ordering, limits, and private seed isolation between the owner and member. The private manifest and credentials stay local. Backend contract tests also cover unauthenticated and cross-member requests, missing and empty seeds, and the legacy user path. This replay does not test Finamp parsing or playback.

Remaining claims require real-client proof for timed lyrics and additional mix seeds where the client exposes the action. Reproduce status and any parsing or playback failure against the shared fixture. Keep tokens, fixture credentials and private media out of evidence. Genre and collection InstantMix need separate contract and behavior work before support is claimed.
