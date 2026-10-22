# Native UI test-history evidence

Scope: `apple/Tests/UITests/NativeProofTests.swift`, audited 2026-10-21.

The source has 16 XCTest methods: 16 are discovered by the iOS target; tvOS
compiles 12 (the four Books methods are iOS-only) and intentionally skips the
Duo and compatible-HLS methods. Therefore the existing effective baseline is
16 iOS journeys and 10 executable tvOS journeys (12 discovered, 2 skipped).

`Regression evidence` below has the narrow meaning used by the pruning audit:
an existing test failed and that failure caused a product-code fix. Creating a
test together with a feature, changing a selector/gesture, or repairing fixture
timing is not evidence.

| Test | Regression evidence | History finding | Suggested retained coverage / rationale |
| --- | --- | --- | --- |
| `testNativeBooksLibraryBrowsesShelvesAndMangaStacks` | No | Added in [PR #140](https://github.com/shiftedx/lumina/pull/140); later change `91439e8` repaired the back-element lookup, a test/UI-navigation drift repair rather than a demonstrated product regression. | iOS journey: native Books destination → shelf → reader → stack membership. |
| `testNativeBooksReadTurnAndRestoreAllFormats` | No | Added in [PR #140](https://github.com/shiftedx/lumina/pull/140). The PR's failed-prefetch finding is covered by `LuminaBooksTests`, not this UI method. | iOS journey: real reader preparation, page turn, and saved restoration across EPUB/PDF/CBZ/CBR. |
| `testNativeBooksReadTurnResizeAndRestoreAllFormats` | No | Added in [PR #140](https://github.com/shiftedx/lumina/pull/140); no later `-S`/`-L` product-fix link. | iOS rotation/reflow journey, the only UI layer that can prove the reader stays usable after a viewport change. |
| `testNativeBooksKeepPhysicalRightInRTLLocale` | No | Added in [PR #140](https://github.com/shiftedx/lumina/pull/140); no historical failure-to-product-fix link. | iOS RTL/Duo physical page-side journey. |
| `testFeaturedTitlePreservesDetailsAndResumesPlayback` | No | Added by [PR #115](https://github.com/shiftedx/lumina/pull/115); subsequent `-S` hits are feature/performance updates, not a test failure followed by an app fix. | iOS/tvOS Home → details → resume journey. |
| `testAccountTabAppearancePreservesHomeSelectionAndCompactHeader` | No | Added by [PR #115](https://github.com/shiftedx/lumina/pull/115); no later app fix attributable to it. | UI navigation/appearance state journey. |
| `testLibraryPullRevealsSearchAndRefreshesProgress` | No | Added by [PR #115](https://github.com/shiftedx/lumina/pull/115); no later app fix attributable to it. | iOS-only touch pull/search/refresh journey. |
| `testAudioAlbumTrackPlaybackReportsProgress` | No | Added by [PR #115](https://github.com/shiftedx/lumina/pull/115); no later app fix attributable to it. | iOS/tvOS audio playback and server-progress journey. |
| `testMusicPlaylistsSmartSelectionAndDownloadedPlayback` | No | Added by [PR #115](https://github.com/shiftedx/lumina/pull/115); no later app fix attributable to it. | iOS/tvOS playlist and downloaded-file workflow. |
| `testMusicAdvancedControlsPausedRestoreAndQueueConflict` | **Yes** | [PR #139](https://github.com/shiftedx/lumina/pull/139) records that the extended TV workflow exposed `List` rows swallowing Select. Commit [`156f33b`](https://github.com/shiftedx/lumina/commit/156f33b9248c6cb2c7915a26c2e63c67b09b6162) fixes `PlaybackScreen.swift` to make queue actions independently focusable and simplifies this method to activate the now-real button. This is an existing-test failure followed by app-code repair. | Must keep: tvOS queue remote-focus/control journey. |
| `testShowSeasonEpisodeBrowsingAndPlayback` | No | Added by [PR #115](https://github.com/shiftedx/lumina/pull/115); no later app fix attributable to it. | iOS/tvOS hierarchy-to-playback journey. |
| `testVideoWatchedFavoriteAndActivityFilters` | No | Added by [PR #115](https://github.com/shiftedx/lumina/pull/115); no later app fix attributable to it. | UI mutation/filter summary and server-state journey. |
| `testNativePlaybackReportsResumeAndConversionProgress` | **Yes** | The original method in [PR #69](https://github.com/shiftedx/lumina/pull/69) includes the TV Home/return pause assertion. Its PR body records that a later Home/return run found player disposal; [PR #75](https://github.com/shiftedx/lumina/pull/75), commit [`0bab243`](https://github.com/shiftedx/lumina/commit/0bab2432f41cae8a9bce4667c9659473239f7a1f), repairs `PlaybackScreen.swift` lifecycle/background behavior. The original test was the sole method with that TV Home/return check before the fix. | Must keep: real AVKit direct + conversion + persisted progress/resume journey. |
| `testCurrentDuoLayoutKeepsControlsAndPlaybackReachable` | No | Created with the Duo repair in [PR #75](https://github.com/shiftedx/lumina/pull/75); no pre-existing test failure led to that fix. | iOS/Duo layout, background pause, completion, replay, Done journey. |
| `testOriginalMKVReportsPeriodicProgressAndResumes` | No | Added by [PR #137](https://github.com/shiftedx/lumina/pull/137) with a cadence optimization; its PR reports passing validation, not a failure that led to product repair. | iOS/tvOS original-MKV playback/progress/resume journey. |
| `testCompatibleHLSReplayRequestsFreshSession` | No | Created alongside replay lifecycle work in [PR #75](https://github.com/shiftedx/lumina/pull/75); creation is not evidence under the audit rule. | iOS compatible-HLS completion/replay/new-session journey. |

## Sources and search record

Primary sources were the repository history and GitHub's first-party PR/issue
records: [issue #64](https://github.com/shiftedx/lumina/issues/64), [PR #69](https://github.com/shiftedx/lumina/pull/69), [PR #75](https://github.com/shiftedx/lumina/pull/75), [PR #115](https://github.com/shiftedx/lumina/pull/115), [PR #137](https://github.com/shiftedx/lumina/pull/137), [PR #139](https://github.com/shiftedx/lumina/pull/139), and [PR #140](https://github.com/shiftedx/lumina/pull/140).

Commands run (all read-only):

```sh
git log --format='%H %s' -- apple/Tests/UITests/NativeProofTests.swift
git log -S '<each test method>' --all --format='%H %s'
git log -L <method-range>:apple/Tests/UITests/NativeProofTests.swift
git show --unified=12 <introduced-or-repaired-commit> -- apple/Tests/UITests/NativeProofTests.swift
gh pr view <69|75|115|137|139|140> --json number,title,body,url,commits,closingIssuesReferences
gh issue view <64|69|75|115|137|139|140> --json number,title,body,url,state,comments
```

Limit: GitHub has no issue/PR explicitly naming most individual XCTest symbols.
For those rows, full file history, method `-S` history, and PR bodies found no
qualifying failure-to-fix evidence; that is recorded as `No`, not as a claim
that no unrecorded regression ever occurred.
