# Native UI signal ledger

Scope: all 16 native UI methods at `f912b52`. Updated after the user's
2026-10-21 direction to avoid investing in UI that is being replaced.

Before: **16 iOS + 8 executable tvOS = 24 platform cases** (tvOS also discovered
three intentional skips). After: **7 iOS + 5 tvOS = 12 cases**, with no
intentionally skipped selected cases. Smoke contains four playback cases.
The reduced full cohort passed twice in parallel; smoke timing is pending.

Evidence answers **“Has this ever failed in a way that indicated an actual
regression?”** Only an existing failure leading to an app-code fix counts.
Creation and test-only repairs do not. [History and primary sources](native-ui-history.md)
record the `git log -S/-L/--grep`, PR and issue searches. `No` means no qualifying
record found. Both methods with regression evidence remain.

| Test | Verdict | Evidence | Coverage after | Reason |
| --- | --- | --- | --- | --- |
| `testNativeBooksLibraryBrowsesShelvesAndMangaStacks` | delete | No; #140 locator self-fix only | Books catalog/reader API contract [B1]; native reading retained below | Shelf/grid/manga-stack screen walk is being replaced; data contracts remain |
| `testNativeBooksReadTurnAndRestoreAllFormats` | keep, trim | No; #140 creation only; generic-iPad Fit/Fill expectation drift explained below | Same original method: EPUB/PDF/CBZ/CBR prepare, turn, close/reopen and restore | Actual native renderer/checkpoint integration survives a new screen design; crop/copy/zoom/viewport assertions removed |
| `testNativeBooksReadTurnResizeAndRestoreAllFormats` | delete | No; #140 creation only | Basic native reading above; layout/fold policy [B2] | Duplicate format journey plus current viewport/rotation choreography |
| `testNativeBooksKeepPhysicalRightInRTLLocale` | delete | No; #140 creation only | Explicit physical-right/fold/RTL unit contract [B2]; native format journey above | Repeated format walk and layout already pinned below; new UI's physical rendering still needs its own review |
| `testFeaturedTitlePreservesDetailsAndResumesPlayback` | delete | No; #115 creation only | Home loading/stability [H1]; retained playback regression journey proves actual progress/resume | Carousel timer, captions and details navigation are current-screen choreography; no new-UI routing coverage claimed |
| `testAccountTabAppearancePreservesHomeSelectionAndCompactHeader` | delete | No; #115 creation only | Home state/load isolation [H1] | Appearance menu and compact-header capture/layout walk will be replaced; those UI affordances are intentionally retired |
| `testLibraryPullRevealsSearchAndRefreshesProgress` | delete | No; #115 creation only | Refresh/pagination/search [L1]; retained playback refreshes progress | Pull-to-reveal choreography is tied to the old UI; search/refresh semantics stay lower |
| `testAudioAlbumTrackPlaybackReportsProgress` | keep, trim | No; #115 creation only | Same method: real audio, Home/background, browse/reopen, advancing clock and server progress; queue/repeat logic [M1] | Native audio-session persistence is durable; repeated queue/caption walk removed |
| `testMusicPlaylistsSmartSelectionAndDownloadedPlayback` | keep, trim/rename | No; #115 creation only | `testDownloadedAudioPlaysFromLocalFiles`; playlist ownership/rules [M2] | Retain download → local-file playback → advancing native clock; remove playlist-form/smart-rule/copy checks from UI |
| `testMusicAdvancedControlsPausedRestoreAndQueueConflict` | keep | **YES**; #139 / `156f33b`, existing TV queue Select failure led to PlaybackScreen focusability fix | Same method; real paused restore, queue actions and conflict resolution | Regression catcher; native focus and device/server queue reconciliation survive screen redesign |
| `testShowSeasonEpisodeBrowsingAndPlayback` | delete | No; #115 creation only | Series/season/episode catalog contract [S1]; retained video journey proves playback engine/progress | Current hierarchy screen walk duplicates lower catalog plus playback contracts; native episode-routing wiring is intentionally not claimed |
| `testVideoWatchedFavoriteAndActivityFilters` | delete | No; #115 creation only | Mutation/cache/revocation and visibility contracts [V1] | Logic and filter results already pinned below; current switch/caption presentation is being replaced |
| `testNativePlaybackReportsResumeAndConversionProgress` | keep, trim | **YES**; #69 → #75 / `0bab243`, existing Home/return failure led to player lifecycle fix | Same method; direct/conversion, actual saved seek, progress and background return on both platforms | Regression catcher; absorbs iOS lifecycle proof from retired viewport walk, removes grid bounds and duplicate Home label detour |
| `testCurrentDuoLayoutKeepsControlsAndPlaybackReachable` | delete | No; #75 creation with fix does not count | iOS background pause/resume retained in playback regression journey; actual EOS/Replay below | Current viewport/control-frame capture walk is obsolete; durable player behaviors retained |
| `testOriginalMKVReportsPeriodicProgressAndResumes` | keep | No; #137 creation with cadence change does not count | Same method: on-device original-file preparation, native playback, periodic progress and resume | Real platform packaging/decoder integration is useful through a UI rewrite |
| `testCompatibleHLSReplayRequestsFreshSession` | keep (iOS) | No; #75 creation with fix does not count | Same method: actual end-of-stream → Replay → fresh session and advancing reset clock | Native AVKit lifecycle plus server negotiation cannot be established by a caption/unit assertion |

## Retained lower contracts

These contracts already existed; the deleted walks are not reported as newly
moved tests. No additional test is needed merely to mirror a helper or caption.

- **[B1]** `apple/Core/Tests/LuminaCoreTests/LuminaBooksTests.swift`:
  `testNativeCatalogReaderAndRewindUseMemberHeaderAndStructuredEndpoints`;
  `backend/tests/test_books_api.py`: `test_epub_reader_resources_listing_series_and_shelves`,
  `test_cbz_books_are_private_then_shareable_with_member_state_and_shelves`,
  `test_books_filters_checkpoint_reset_metadata_clears_and_shelf_order`.
- **[B2]** `LuminaBooksTests.testReadingLayoutRetainsPhysicalRightPageAcrossExpansionFoldsAndRTL`
  explicitly pins physical right, fold exclusions and LTR/RTL companion selection.
  It does not claim a screenshot or gesture result from the replacement UI.
- **[H1]** `LuminaCoreTests.swift`: `testHomeLoadsShelvesConcurrentlyWithTheSameFirstShelfTime`,
  `testHomePublishesAnAvailableShelfWhileAnotherShelfIsStillLoading`,
  `testHomeKeepsSuccessfulShelvesWhenRecentFails`,
  `testHomeRemainsStableWhileVideoAndMusicBrowseIndependently`.
- **[L1]** `LuminaCoreTests.testRefreshPublishesHomeWhileCatalogIsSlowAndKeepsCatalogError`,
  `testLibraryPaginationAppendsTheNextPageAndStopsAtTheCatalogTotal`;
  `backend/tests/test_library_refresh_route.py::test_refresh_advances_at_most_two_bounded_steps_and_preserves_sweep_progress`,
  `test_library_search_fts.py::test_indexed_item_matches_on_title`,
  `test_jellyfin_search_pages.py::test_search_filters_scope_before_paging_and_keep_rank_and_visibility`.
- **[M1]** `MusicPlaybackPolicyTests.testQueueTransitionsUsePositionsSoRepeatedTrackIDsDoNotSkipEntries`;
  `NativeMusicTransportTests.testTenDecodedPauseResumeCyclesReuseTheDownloadedAudio`.
- **[M2]** `backend/tests/test_music_playlists.py`:
  `test_own_and_shared_playlist_listing_and_optimistic_revisions`,
  `test_shared_manual_playlist_filters_entries_that_became_private_or_missing`,
  `test_smart_rules_use_effective_genres_favorites_and_member_play_state`.
- **[S1]** `LuminaCoreTests.testSeriesBrowsingKeepsViewFiltersPagingAndOrderedChildren`;
  `backend/tests/test_titles_api.py`: `test_series_detail_puts_specials_last_and_offers_first_episode`,
  `test_episodes_by_season`, `test_unnumbered_episode_sorts_after_numbered`.
- **[V1]** `LuminaCoreTests.testWatchedFavoriteMutationRefreshesCachesAndRevocationClearsSession`;
  `backend/tests/test_titles_api.py::test_favorites_toggle_and_respect_visibility`,
  `test_watching_a_movie_marks_only_its_preferred_version`;
  `test_gallery_listing.py::test_watched_state_chips_for_movies_and_shows`.

## Isolation and host policy

Each scheduling class owns a reserved loopback port, backend/database/media/member
fixture, UUID defaults suite and Keychain service. Intentional relaunch retains
that test's namespace. Downloads/checkpoints remain member/origin scoped. The app
has no App Group entitlement; each owned simulator has its own app sandbox.
There are no serial retained tests. Separate platform brokers prevent same-name
classes from sharing fixtures; two shards per platform run concurrently, with
cohort barriers and exact executed/passed-count checks.

The user explicitly removed both Xcode exclusivity and the load1 ceiling.
Peak load is recorded, not used as a gate. Cleanup stops only owned processes and
devices. Cancellation prevents later repeat launches and releases cohort barriers.
All exploratory/baseline owned simulators and fixture servers were removed when
the user redirected work toward contracts that survive the redesign.

## Current evidence and limits

- `make check` passed: backend 5,248 / 11 skipped; frontend 2,624; real-stack
  52 / 5 skipped; mocked browser 657 / 2 skipped. No product code changed after it
  except removing redundant explicit AppStorage stores in favor of the scene store.
- Core: 74 passed / 5 optional live-server skips. The 16 focused retained Core
  contracts above passed again (1.983 seconds). Nine broker/runner checks pass.
- Historical helpers had real current failures: TV geometric navigation chose a
  lower row for horizontally separated controls; iPad identifiers resolved static
  label children rather than their sidebar cells; AVKit replaced clock/control
  nodes after background activation; TV focus identity binding vanished during a
  menu transition. Repairs are test-only, not new regression evidence.
- The old viewport journey passed twice after the native resume repair
  (157.483 / 183.872 seconds command wall), proving the lifecycle block retained
  in the playback regression journey. Old four-case smoke components passed twice
  per platform, but they do not qualify the newly trimmed cohort.
- Original iOS smoke before pruning: 2/2 serial after helper repair, 397.97 seconds
  command wall. Raw 0/2 helper failure: 114.48 seconds. Original Books reached the
  reader and exposed Fit vs legacy Fill expectation drift (87.099 seconds test;
  376.78 seconds command wall including scheduling delay). Its layout/copy
  assertion is removed from the durable reading journey.
- The original full baseline was stopped under the user's redesign direction.
  Its partial TV command wall was 1,277.06 seconds, with 2 passed, 4 failed
  (including cancellation), 3 skipped. This is **not a full-suite baseline** and
  cannot support a before/after speedup claim. A complete obsolete-UI baseline
  will not be run merely to produce a number.

## Reduced-cohort qualification

The committed reduced full tier passed twice: **12/12 each repeat**, zero failures,
zero skips, all four explicit shards green (iOS 4+3; tvOS 3+2). Cross-platform
wall times were **733.433 seconds (12m13s)** and **576.970 seconds (9m37s)**,
both below 2,700 seconds. The first includes 243.001 seconds of actual concurrent
cold-simulator setup span. Builds were separately recorded: iOS 10.026 seconds,
tvOS 14.061 seconds. Total invocation including builds, both repeats and cleanup:
1,331.750 seconds. Peak observed load1 reached 1,004.95; no delay was subtracted.

Evidence: `/tmp/lumina-ui-signal-durable-full-1/full-report.json` and eight
`*-full-{1,2}-shard-{1,2}.xcresult` bundles. Both historical regression methods
and all-format reader restoration passed on each selected platform twice.
The four-case smoke tier's independent two-pass measurement remains pending.

