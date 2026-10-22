# Native UI signal ledger

Scope: Lumina native Apple XCTest UI suite at `f912b52`, 2026-10-21.
Status: **in progress**. Verdicts below preserve existing contracts pending current-runtime
qualification. One duplicate Books journey is deleted; no test with regression evidence is removed. A current failure must be
explained and repaired before its verdict is finalized.

Evidence asks only: **Has this ever failed in a way that indicated an actual regression?**
Only an existing test failure leading to app-code repair counts. See
[native-ui-history.md](native-ui-history.md) for commit/PR links and search commands.
`No` means no qualifying recorded evidence found, not proof that no regression occurred.

Before: 16 iOS methods; tvOS 11 discovered (5 iOS-only methods omitted),
3 intentional skips, 8 executable journeys.
After: 15 iOS + 8 executable tvOS cases (23 total; before 24 executable).
No before wall-time baseline has been measured. Historical case times are not a full-tier measurement.

| Test | Verdict | Evidence | Coverage after | Reason |
| --- | --- | --- | --- | --- |
| `testNativeBooksLibraryBrowsesShelvesAndMangaStacks` | keep (pending runtime) | No; #140, navigation selector self-fix only | Same method, independently scheduled `NativeNativeBooksLibraryBrowsesShelvesAndMangaStacksTests`; Core/API contracts retained | Shelf → reader → back → manga stack; real navigation and rendering |
| `testNativeBooksReadTurnAndRestoreAllFormats` | delete | No; #140 creation only; no current failure recorded | `testNativeBooksReadTurnResizeAndRestoreAllFormats` retains English all-format open/prepare/turn/immersive/restore plus resize; `testNativeBooksKeepPhysicalRightInRTLLocale` retains the same helper including physical side and legacy Duo Fill/panning | Duplicate journey; same helper, English flow retained by resize and default Fill branch retained by RTL. No assertions removed from the shared helper. |
| `testNativeBooksReadTurnResizeAndRestoreAllFormats` | keep (pending runtime) | No; #140 creation only | Same method, independently scheduled `NativeNativeBooksReadTurnResizeAndRestoreAllFormatsTests`; Core/API contracts retained | Rotate/reflow the live reader and retain position in four formats |
| `testNativeBooksKeepPhysicalRightInRTLLocale` | keep (pending runtime) | No; #140 creation only | Same method, independently scheduled `NativeNativeBooksKeepPhysicalRightInRTLLocaleTests`; Core/API contracts retained | Real Arabic-locale rendering and physically right selected image, beyond layout arithmetic |
| `testFeaturedTitlePreservesDetailsAndResumesPlayback` | keep (pending runtime) | No; #115 creation only | Same method, independently scheduled `NativeFeaturedTitlePreservesDetailsAndResumesPlaybackTests`; Core/API contracts retained | Home carousel touch/remote focus → details → playback → saved progress |
| `testAccountTabAppearancePreservesHomeSelectionAndCompactHeader` | keep (pending runtime) | No; #115 creation only | Same method, independently scheduled `NativeAccountTabAppearancePreservesHomeSelectionAndCompactHeaderTests`; Core/API contracts retained | Native appearance menu and navigation preserve selected Home title |
| `testLibraryPullRevealsSearchAndRefreshesProgress` | keep (pending runtime) | No; #115 creation only | Same method, independently scheduled `NativeLibraryPullRevealsSearchAndRefreshesProgressTests`; Core/API contracts retained | Actual short/deep pull gestures select search versus refresh |
| `testAudioAlbumTrackPlaybackReportsProgress` | keep (pending runtime) | No; #115 creation only | Same method, independently scheduled `NativeAudioAlbumTrackPlaybackReportsProgressTests`; Core/API contracts retained | Native audio playback, background/remote transport, Browse/now-playing and queue selection |
| `testMusicPlaylistsSmartSelectionAndDownloadedPlayback` | keep (pending runtime) | No; #115 creation only | Same method, independently scheduled `NativeMusicPlaylistsSmartSelectionAndDownloadedPlaybackTests`; Core/API contracts retained | Create/edit playlist then download and actually play a local file |
| `testMusicAdvancedControlsPausedRestoreAndQueueConflict` | keep (pending runtime) | YES; #139 / 156f33b, existing TV queue Select failure → PlaybackScreen focusability fix | Same method, independently scheduled `NativeMusicAdvancedControlsPausedRestoreAndQueueConflictTests`; Core/API contracts retained | Must retain; real TV focus plus relaunch paused and resolve server/device queue conflict |
| `testShowSeasonEpisodeBrowsingAndPlayback` | keep (pending runtime) | No; #115 creation only | Same method, independently scheduled `NativeShowSeasonEpisodeBrowsingAndPlaybackTests`; Core/API contracts retained | Show → season → episode → native playback and saved progress |
| `testVideoWatchedFavoriteAndActivityFilters` | keep (pending runtime) | No; #115 creation only | Same method, independently scheduled `NativeVideoWatchedFavoriteAndActivityFiltersTests`; Core/API contracts retained | Native state controls → server mutation → filtered catalog membership |
| `testNativePlaybackReportsResumeAndConversionProgress` | keep (pending runtime) | YES; #69 → #75 / 0bab243, Home/return disposal failure → player lifecycle fix | Same method, independently scheduled `NativeNativePlaybackReportsResumeAndConversionProgressTests`; Core/API contracts retained | Must retain; AVKit direct/conversion, Home/return, persisted progress and resume |
| `testCurrentDuoLayoutKeepsControlsAndPlaybackReachable` | keep (pending runtime) | No; #75 creation with fix does not count | Same method, independently scheduled `NativeCurrentDuoLayoutKeepsControlsAndPlaybackReachableTests`; Core/API contracts retained | Current viewport layout, background pause, completion and Replay; automated on isolated iPad; additional physical Duo matrix requires Device Hub |
| `testOriginalMKVReportsPeriodicProgressAndResumes` | keep (pending runtime) | No; #137 created with cadence change | Same method, independently scheduled `NativeOriginalMKVReportsPeriodicProgressAndResumesTests`; Core/API contracts retained | Actual on-device MKV preparation → advancing clock → progress → resume |
| `testCompatibleHLSReplayRequestsFreshSession` | keep (pending runtime) | No; #75 creation with fix does not count | Same method, independently scheduled `NativeCompatibleHLSReplayRequestsFreshSessionTests`; Core/API contracts retained | Actual end-of-stream → Replay negotiation → reset/advancing time and fresh server progress |

## Isolation and serial exception

Each XCTest scheduling class owns one journey. The loopback fixture broker starts a
separate production backend with an OS-assigned reserved port, temporary database,
media and member credentials per class. Teardown stops only that backend. Each test
launches the app with a UUID namespace for its defaults suite and Keychain service;
the same namespace survives intentional relaunch inside that test. Downloads and
queue checkpoints remain member/origin scoped. There is no shared App Group entitlement
in this app; each Xcode worker has its own application sandbox. No production App Group
is reused by a test.

All retained automated journeys are scheduled in parallel classes. The current-viewport
Duo method also runs on iPad. A separate physical Duo six-pose matrix still requires
manual Device Hub selection; passing the automated suite does not claim that hardware
configuration matrix was repeated.

## Measurements

Pending: current failures, baseline timing, two parallel kept-suite passes and per-tier budgets.
Do not report this audit as complete until these rows contain actual result evidence.
