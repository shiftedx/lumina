# Finamp compatibility trial

[#354](https://github.com/shiftedx/lumina/issues/354) remains open: the mobile client trial and playback gates have not been completed. This is a protocol and source assessment, not a supported mobile capability claim.

## Source and environment

The official [Finamp 1.0.2 release](https://github.com/finamp-app/finamp/releases/tag/1.0.2), source commit `e27c7b328f7937a479a1a3ba041e41df378d6e2b`, was inspected on November 19, 2026. Its [API definition](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/jellyfin_api.dart) calls `GET /Items/{id}/InstantMix` with `userId`, `limit`, `enableImages`, and `enableImageTypes`; its [helper](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/jellyfin_api_helper.dart) reads the query result's `Items`. The [player](https://github.com/finamp-app/finamp/blob/e27c7b328f7937a479a1a3ba041e41df378d6e2b/lib/services/audio_service_helper.dart) starts those items in server order. This is source evidence, not captured client traffic.

The official macOS 1.0.2 binary launched on the test host, but automation of its UI failed with macOS assistive-access error `-1719`. No app login or playback was observed. An iOS simulator was available, but Flutter was not on PATH and the release did not provide an iOS simulator build; Android's `adb` was not on PATH. No iOS or Android client was run.

| Capability | iOS | Android | Evidence |
| --- | --- | --- | --- |
| Login and browse | Unverified | Unverified | No mobile client run |
| Timed lyrics | Unverified | Unverified | No mobile client run |
| Offline downloads | Unverified | Unverified | No mobile client run |
| Playback and reporting | Unverified | Unverified | No mobile client run |
| InstantMix playback | Unverified | Unverified | Source-inspected endpoint and loopback protocol replay only |

## Server contract and replay

The route handles track, album and artist seeds using the existing visibility-scoped contextual mix, returning Jellyfin Audio DTOs and at most 100 entries. A track seed comes first, then related tracks; album results retain the contextual mix's disc ordering. Unsupported, missing and hidden seeds return an empty query result. Client `limit` can reduce the returned count. Genre and collection seeds remain empty and are not claimed supported.

Start the [disposable #343 fixture](music-home-baseline.md#run-the-shared-server), then run:

```sh
mise exec -- backend/.venv/bin/python scripts/e2e/verify_finamp_instant_mix.py "$LUMINA_NATIVE_FIXTURE"
```

On November 19, 2026 this passed against the loopback server: Finamp-shaped requests for track, album and artist seeds, Audio DTOs, seed ordering, limits, and private seed isolation between the owner and member. The private manifest and credentials stay local. Backend contract tests also cover unauthenticated and cross-member requests, missing and empty seeds, and the legacy user path. This replay does not test Finamp parsing or playback.

To complete the gate, install a released Finamp build on each platform, sign in to the disposable fixture, record app and OS versions, and exercise login, browse, timed lyrics, download followed by offline playback, and playback reports. Start InstantMix from a track, album and artist, capture sanitized request/response status and IDs, confirm the queue and advancing playback, and repeat with an empty result and a member who cannot see the private seed. Record failures with exact steps and keep tokens, fixture credentials and private media out of evidence. Genre and collection InstantMix need separate contract and behavior work before support is claimed.
