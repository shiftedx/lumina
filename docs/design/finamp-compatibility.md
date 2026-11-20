# Finamp compatibility trial

[#354](https://github.com/shiftedx/lumina/issues/354) remains open: the mobile client trial and playback gates have not been completed. The iOS simulator trial below confirms a browse blocker; it is not a supported mobile capability claim.

## Source and environment

The official [Finamp 1.0.2 release](https://github.com/finamp-app/finamp/releases/tag/1.0.2), source commit `e27c7b328f7937a479a1a3ba041e41df378d6e2b`, was inspected on November 19, 2026. Its [API definition](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/jellyfin_api.dart) calls `GET /Items/{id}/InstantMix` with `userId`, `limit`, `enableImages`, and `enableImageTypes`; its [helper](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/jellyfin_api_helper.dart) reads the query result's `Items`. The [player](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/audio_service_helper.dart) starts those items in server order. This is source evidence, not captured client traffic.

The official macOS 1.0.2 binary launched on the test host, but automation of its UI failed with macOS assistive-access error `-1719`. No macOS app login or playback was observed. The release did not provide an iOS simulator binary, so the iOS trial used a debug simulator build of the tagged source.

A subsequent isolated simulator-build attempt used the release's pinned Flutter 3.47.7, locked package dependencies, CocoaPods 1.16.2 and Xcode 27.1. The first build failed because the `flutter_discord_rpc` build requires `rustup`, which was absent. A separate Rust installation under `/tmp` resolved that prerequisite; the retry reached Xcode compilation but was stopped when the combined SDK, package caches, source, Rust toolchain and Xcode DerivedData reached approximately 5.32 GiB, above the 5 GB attempt limit. After the scratch limit was raised to approximately 8 GiB, the incremental debug simulator build succeeded. AXe 1.8.0 was already installed and drove a dedicated iPhone 17 simulator running iOS 26.5. Neither Android `adb` nor an Android emulator was found on the host; no SDK licenses were accepted or system permissions changed.

Finamp's [lyrics API declaration](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/jellyfin_api.dart) uses `GET /Audio/{itemId}/Lyrics`. An authenticated protocol replay for the shared fixture's audio track returned HTTP 404 on November 19, 2026. This is a reproducible compatibility gap for the Jellyfin route; it does not establish how the Finamp UI presents the failure. Reproduce against the disposable fixture with a connected-app token and the manifest's `audioTrackID`, without recording the token or response headers.

| Capability | iOS | Android | Evidence |
| --- | --- | --- | --- |
| Login | Passed on simulator | Unverified | Finamp 1.0.2 debug build authenticated to the disposable fixture |
| Browse | Blocked on simulator | Unverified | The app reports no music libraries after login |
| Timed lyrics | Unverified | Unverified | No mobile client run |
| Offline downloads | Unverified | Unverified | No mobile client run |
| Playback and reporting | Unverified | Unverified | No mobile client run |
| InstantMix playback | Unverified | Unverified | iOS blocked before entry; source-inspected endpoint and loopback protocol replay only |

## iOS simulator result

On November 19, 2026, the dedicated iPhone 17 simulator launched the 1.0.2 debug build, selected the disposable #343 loopback server, and authenticated with the fixture owner. Finamp then presented **Select Music Libraries** with **No Music Libraries** and no selectable library. [Sanitized simulator screenshot](evidence/finamp/ios-no-music-libraries.png). A separate authenticated `GET /UserViews` replay on the same fixture returned collection types `movies` and `tvshows`, with no `music` view. Finamp's [view selector](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/screens/view_selector.dart) requires a music view. This prevents the real client from reaching browsing, timed lyrics, offline downloads, playback reporting and InstantMix; those remain unverified, not passed or failed individually. Add a visible, member-scoped Jellyfin music view and repeat the client trial before claiming those capabilities.

## Server contract and replay

The route handles track, album and artist seeds using the existing visibility-scoped contextual mix, returning Jellyfin Audio DTOs and at most 100 entries. A track seed comes first, then related tracks; album results retain the contextual mix's disc ordering. Unsupported, missing and hidden seeds return an empty query result. Client `limit` can reduce the returned count. Genre and collection seeds remain empty and are not claimed supported.

Start the [disposable #343 fixture](music-home-baseline.md#run-the-shared-server), then run:

```sh
mise exec -- backend/.venv/bin/python scripts/e2e/verify_finamp_instant_mix.py "$LUMINA_NATIVE_FIXTURE"
```

On November 19, 2026 this passed against the loopback server: Finamp-shaped requests for track, album and artist seeds, Audio DTOs, seed ordering, limits, and private seed isolation between the owner and member. The private manifest and credentials stay local. Backend contract tests also cover unauthenticated and cross-member requests, missing and empty seeds, and the legacy user path. This replay does not test Finamp parsing or playback.

To complete the gate, install a released Finamp build on each platform, sign in to the disposable fixture, record app and OS versions, and exercise login, browse, timed lyrics, download followed by offline playback, and playback reports. Start InstantMix from a track, album and artist, capture sanitized request/response status and IDs, confirm the queue and advancing playback, and repeat with an empty result and a member who cannot see the private seed. Record failures with exact steps and keep tokens, fixture credentials and private media out of evidence. Genre and collection InstantMix need separate contract and behavior work before support is claimed.
