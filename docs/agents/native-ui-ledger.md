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
The repaired original iOS smoke measured 397.97 seconds serially; full baseline
and complete concurrent-tier timings remain pending. Historical case times are
not a full-tier measurement.

| Test | Verdict | Evidence | Coverage after | Reason |
| --- | --- | --- | --- | --- |
| `testNativeBooksLibraryBrowsesShelvesAndMangaStacks` | keep (pending runtime) | No; #140, navigation selector self-fix only | Same method, independently scheduled `NativeNativeBooksLibraryBrowsesShelvesAndMangaStacksTests`; Core/API contracts retained | Shelf → reader → back → manga stack; real navigation and rendering |
| `testNativeBooksReadTurnAndRestoreAllFormats` | delete (pending original-runtime check) | No; #140 creation only; no current failure recorded | `testNativeBooksReadTurnResizeAndRestoreAllFormats` retains English all-format open/prepare/turn/immersive/restore plus resize; `testNativeBooksKeepPhysicalRightInRTLLocale` retains the same helper including physical side and legacy Duo Fill/panning | Duplicate journey; same helper, English flow retained by resize and default Fill branch retained by RTL. No assertions removed from the shared helper. |
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


Host policy update: the user explicitly removed Xcode exclusivity on 2026-10-21.
The user subsequently removed the load1 ceiling too. Concurrent jobs are
allowed; peak load is recorded. Owned simulators and owned-PID cleanup still apply. Initial qualification waits were cancelled without
launching Xcode; runtime qualification then started under this revised policy.


Runtime evidence so far:

- iOS `build-for-testing` succeeded; peak recorded load1 13.50.
- Standalone Swift 6 UI-source typechecks passed for iOS and tvOS after moving
  teardown onto XCTest's async lifecycle (no actor-isolation warnings).
- Two native clone-based parallel launches were cancelled before the first case
  at load1 24.77 and 27.37. Their resulting Mach -308 launch errors followed the
  runner's recorded load cancellation; they are invalid host runs, not an app
  regression or a passing kept-suite run. Owned simulators were removed.
- Core: 74 passed, 5 optional live-server skips, 99.892 seconds. Broker/runner:
  six passing safety checks. Two real backend fixtures concurrently proved
  separate origins, member credentials and state roots.
- Repository gate so far: backend 5,248 passed / 11 skipped (618.17 seconds),
  frontend 2,624 passed across 299 files (42.97 seconds). Browser gates: real-stack 52 passed / 5 skipped (10.4 minutes); mocked browser
  657 passed / 2 skipped (20.2 minutes). `make check` completed successfully.

Current failure under investigation: the first tvOS resume/conversion case reached
playback and Home return, then its geometric remote helper cycled Play ↔ Resume
instead of moving right toward featured Pause. The captured bounds were Pause
(1784, 699.5, 52, 52) and Play (76, 631, 220, 93): the helper treated a 1.5-point
center/bottom mismatch as a lower row despite vertical overlap. Restricting the
center-based vertical shortcut to horizontally overlapping controls preserves the
wide-control rule while allowing the real remote to move right. This test-only
repair is pending a complete rerun; no product regression claim is established.
The concurrent Audio case passed (208.287 seconds). Qualification also exposed
Xcode's `TEST EXECUTE SUCCEEDED` marker for test-without-building; the runner now
accepts that marker while still requiring exact passing xcresult counts.

The original TV focus failure reproduced twice (144.516 / 144.756 seconds),
while Audio passed twice. The separate original-revision iPad baseline also
failed both smoke methods after sign-in: its requested visible sidebar row was
replaced by an inactive same-identifier tab during `waitForHittable`. The helper
now retains the original row as a fallback and selects a candidate only when
visible, enabled and hittable. Product code is unchanged by either drift repair.

The direction repair passed the original Home detour. A subsequent detour timed
out while progressing normally through the eight-title row under extreme host
load; featured-control routes now allow 45 seconds for real remote traversal.
The video journey's repeated Home Resume-label detour is duplicate coverage:
`testFeaturedTitlePreservesDetailsAndResumesPlayback` retains the actual Home
Resume action. `NativeServerTests` retains saved-position API coverage, and the
video journey still asserts persisted API progress plus the reopened player's
actual resumed clock, direct/conversion advancement and Home/background lifecycle.
Only the duplicate label detour was removed; neither regression-catching journey
was deleted. Intermediate owned runs were stopped to rebuild the repaired tests.

First repaired parallel iPad smoke pass: Audio 191.894 seconds; native direct /
conversion / resume video 186.792 seconds, both passed. The second pass and
rebuilt tvOS smoke/full runs remain pending. These case times are not yet a
complete tier wall-time or budget claim.


Both repaired iPad smoke repeats passed (Audio 191.894 / 166.495 seconds;
video 186.792 / 159.840 seconds). Rebuilt tvOS smoke first repeat passed
(Audio 219.009, video 188.777 seconds); the full Home journey passed in
184.751 seconds. Original failures remain recorded, rather than being hidden
by these reruns.

With exclusivity removed, the runner now schedules iOS and tvOS concurrently,
with separate fixture brokers and cohort barriers. Monotonic phase timestamps
measure cross-platform wall spans, including cold setup on the first repeat;
build times remain separate. Eight harness checks pass, including staggered
starts, incomplete-result rejection and aborted-barrier release.


Repaired tvOS smoke second repeat also passed (Audio 224.034 seconds, video
192.808 seconds). All four platform smoke cases have two passing parallel
platform runs; the new unified concurrent cohort still needs its own measured
wall-time qualification. The original iOS serial smoke passed 2/2 in 397.97
seconds after the sidebar-query drift repair; its raw helper failure (0/2 in
114.48 seconds) is preserved separately and excluded from the passing baseline.

The first unified full run found a current-viewport resume-helper failure on
regular iPadOS 26.5. Background pause assertions passed; after tapping the player,
the helper's sequential four-second clock poll and later Play lookup timed out.
The captured hierarchy/screenshot show actual playback at 25 seconds, with no
Play control because playback was already running. The repair observes a freshly
queried clock and an available native Play control in one bounded loop, retaining
the actual clock-advancement assertion. Focused rerun pending; this is not yet
claimed as an app regression or a complete passing full cohort.

Original-revision Books diagnosis also identified visible iPad sidebar cells
whose *children* carry the tab identifiers. Destination lookup now selects the
containing hittable cell before falling back to labels. The baseline agent is
rerunning the original Books duplicate before its deletion verdict is finalized.

The repaired current-viewport journey passed its first focused run (157.483
seconds xcodebuild wall), including background pause/resume, completion and
Replay. A second focused run is pending. The full tvOS Advanced journey failed
inside XCTest while reading a focus-bound cell that disappeared during a menu
transition (`No matches found for Identity Binding`), rather than at an app
assertion. Focus metadata now uses immutable public accessibility snapshots,
including fresh target geometry as navigation scrolls. Identifiers, wrapper
checks and real remote actions remain unchanged; runtime verification pending.

The original Books duplicate reached the reader after sidebar repair and proved
the regular-iPad precondition drift: actual `Fit entire page`, expected legacy
Duo `Fill display`. The raw case is preserved (87.099 seconds test; 376.78
seconds command wall, including launch scheduling delay). The baseline is being
repaired to the actual generic-iPad precondition before its passing measurement.
